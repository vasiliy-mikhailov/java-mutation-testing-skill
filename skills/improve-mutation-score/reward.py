#!/usr/bin/env python3
"""reward.py - mergeability reward for a generated JUnit test file.

reward = 0.9 ** (penalty)   ->  1.0 means nothing broken.
Each broken binary rule costs 1 penalty; unused code (rule 7) costs 1 PER LINE,
so a one-line dead import barely dents the reward while a 15-line dead method
(0.9^15 ~= 0.21) tanks it.

A green, mutation-improving test that a maintainer will not merge is worth
nothing, so "avoid <wart>" is only real if breaking it costs reward. Each rule
below is machine-checkable; every penalty unit multiplies the reward by 0.9.

Pure stdlib - runs anywhere (opencode / kilocode / CI), no dependencies.

Static rules (from the file alone):
  1 api-only          no reflection into internals
  2 every-test-asserts no coverage-theater @Test
  3 no-vacuous-assert  no assertTrue(true) / assertEquals(x,x) / assertNotNull("lit")
  4 no-adnt-only       no @Test whose only check is assertDoesNotThrow / try-catch-fail
  5 deterministic      no sleep / unseeded Random / wall-clock / real network/IO
  6 no-disabled        no @Disabled / @Ignore added
  7 no-unused-code     unused added import / private field / private method (penalty = lines)
  8 additive-only      (needs --baseline) no existing line removed
Dynamic rules (need build inputs):
  9 green              (needs --green true|false) all tests compile and pass
 10 mutation-improving (needs --mut-before N --mut-after M) kills strictly increased
 11 no-partial-assert  no @Test that validates a string via substring match (assert the full value)
 12 no-trivial-accessor-test  no pure getter/setter/equals/hashCode/toString test (keep real logic)

Rules with missing inputs are reported n/a and excluded from the count.
"""
import argparse, difflib, json, re, sys

def read(p):
    try:
        return open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return None

def added_region(src, baseline):
    """The lines WE added, as CONTIGUOUS blocks (so methods stay parseable). A plain
    set-difference scrambles structure and, when it yields no parseable method, the old
    code fell back to judging the whole file — penalizing us for UPSTREAM warts. difflib
    keeps the inserted/replaced blocks intact and in order, so only our additions judge."""
    if baseline is None:
        return src
    a, b = baseline.splitlines(), src.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("insert", "replace"):
            out.extend(b[j1:j2])
    return "\n".join(out)

def test_methods(src):
    """[(name, body)] for each @Test method, via brace matching."""
    out = []
    for m in re.finditer(r'@Test\b', src):
        i = m.end()
        brace = src.find('{', i)
        semi = src.find(';', i)
        if brace == -1 or (semi != -1 and semi < brace):
            continue  # abstract / annotation-only
        head = src[i:brace]
        names = re.findall(r'(\w+)\s*\(', head)
        name = names[-1] if names else "?"
        depth, j, end = 0, brace, brace
        while j < len(src):
            c = src[j]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = j
                    break
            j += 1
        # annotation window just before @Test (catches @Test(expected=...))
        ann = src[max(0, m.start()-120):brace]
        out.append((name, src[brace:end+1], ann))
    return out

REFLECT = re.compile(r'\bsetAccessible\b|\bgetDeclaredField\b|\bgetDeclaredMethod\b|\bgetDeclaredConstructor\b'
                     r'|ReflectionTestUtils|\bWhitebox\b|FieldUtils\.(read|write)|\.getModifiers\(\)|Unsafe\b')
ASSERT = re.compile(r'\bassert[A-Z]\w*\s*\(|\bassertThat\b|\bexpectThrows\b|\bassertThrows\b|\bfail\s*\('
                    r'|\bverify\s*\(|\bverifyNoInteractions\b|Mockito\.verify|\bthen\(.*\)\.should')
ADNT = re.compile(r'\bassertDoesNotThrow\b')
VACUOUS = re.compile(r'assertTrue\s*\(\s*true\s*\)|assertFalse\s*\(\s*false\s*\)'
                     r'|assertNotNull\s*\(\s*"[^"]*"\s*\)|assertNull\s*\(\s*null\s*\)'
                     r'|assertEquals\s*\(\s*([\w.]+)\s*,\s*\1\s*\)')
FLAKY = re.compile(r'Thread\.sleep|new\s+Random\s*\(\s*\)|System\.currentTimeMillis|System\.nanoTime'
                   r'|new\s+Date\s*\(\s*\)|InetAddress|new\s+Socket|\.openConnection\s*\(|new\s+URL\s*\(')
DISABLED = re.compile(r'@Disabled\b|@Ignore\b')
# weak substring assertion on a STRING LITERAL (url.contains("..."), startsWith, Hamcrest containsString,
# indexOf>=0). A substring check kills fewer mutants than a full assertEquals on the whole value (a mutant
# that reorders/alters a DIFFERENT part survives), so it is flagged even when the test asserts other values.
# The string-literal arg keeps collection-membership tests (list.contains(var)) out.
PARTIAL = re.compile(r'assert(?:True|False)\s*\(\s*[\w.()]+\.(?:contains|startsWith|endsWith|matches)\s*\(\s*"'
                     r'|\bcontainsString\s*\(\s*"'
                     r'|assert(?:True|False)\s*\(\s*[\w.()]+\.indexOf\s*\(\s*"[^"]*"\s*\)\s*(?:>=\s*0|!=\s*-?\s*1|>\s*-\s*1)')

def non_adnt_assert(body):
    # an assertion that is not assertDoesNotThrow
    return bool(re.search(ASSERT, re.sub(r'assertDoesNotThrow', 'XXX', body)))

def _brace_end(src, brace):
    depth = 0
    for j in range(brace, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(src) - 1

def _line_at(src, pos):
    return src[src.rfind("\n", 0, pos) + 1: src.find("\n", pos) if src.find("\n", pos) != -1 else len(src)]

def unref(src, name):
    """name appears only at its own declaration (referenced nowhere else)."""
    return len(re.findall(r'\b' + re.escape(name) + r'\b', src)) <= 1

def unused_code(src, baseline):
    """Dead code we ADDED, returned as [(desc, line_count)] so the penalty scales with
    the NUMBER OF LINES of unused code, not just its presence. Catches unused imports
    (1 line each), unused private fields (declaration span), and unused private methods
    (whole-body span). Conservative: with a baseline, only lines we introduced are judged."""
    base = set(baseline.splitlines()) if baseline else set()
    body = re.sub(r'(?m)^\s*import\s.*;', '', src)  # strip imports before counting refs
    items = []  # (desc, line_count)
    for m in re.finditer(r'(?m)^[ \t]*import\s+(?!static\b)([\w.]+)\s*;', src):
        if m.group(0).strip() in base or m.group(1).endswith(".*"):
            continue
        simple = m.group(1).rsplit(".", 1)[-1]
        if not re.search(r'\b' + re.escape(simple) + r'\b', body):
            items.append(("import " + simple, 1))
    # private methods first (so a method's inner locals aren't mistaken for fields)
    method_spans = []
    for m in re.finditer(r'(?m)^[ \t]*private\s+(?:static\s+|final\s+)*[\w.$<>,\[\]]+\s+(\w+)\s*\([^;{]*\)\s*(?:throws[\w.,\s]+?)?\{', src):
        brace = src.index("{", m.start()); end = _brace_end(src, brace)
        method_spans.append((m.start(), end))
        if _line_at(src, m.start()).strip() in base:
            continue
        if unref(src, m.group(1)):
            items.append(("method " + m.group(1), src.count("\n", m.start(), end) + 1))
    in_method = lambda p: any(s <= p <= e for s, e in method_spans)
    for m in re.finditer(r'(?m)^[ \t]*private\s+(?:static\s+|final\s+)*[\w.$<>,\[\]\s]+?\b(\w+)\s*[=;]', src):
        if in_method(m.start()) or m.group(0).strip().splitlines()[0] in base:
            continue
        semi = src.find(";", m.start())
        if unref(src, m.group(1)):
            items.append(("field " + m.group(1), src.count("\n", m.start(), semi) + 1))
    return items, sum(n for _, n in items)

def evaluate(path, baseline_path, green, mut_before, mut_after):
    src = read(path)
    if src is None:
        print(f"cannot read {path}", file=sys.stderr); sys.exit(2)
    baseline = read(baseline_path) if baseline_path else None
    region = added_region(src, baseline)
    methods = test_methods(region)  # region == src when baseline is None; only OUR methods otherwise
    rules = []  # (id, name, status: pass/fail/na, detail, penalty)
    def add(i, name, fail, detail, penalty=None):
        # binary rules cost 1 when broken; a weighted rule passes its own penalty
        rules.append((i, name, "fail" if fail else "pass", detail if fail else "",
                      penalty if penalty is not None else (1 if fail else 0)))
    def na(i, name, hint):
        rules.append((i, name, "na", hint, 0))

    refl = [m.group(0) for m in REFLECT.finditer(region)]
    add("1", "api-only", bool(refl), f"reflection: {sorted(set(refl))}")

    noassert = [n for n, b, a in methods if not re.search(ASSERT, b) and 'expected' not in a]
    add("2", "every-test-asserts", bool(noassert), f"no-assertion tests: {noassert}")

    vac = [m.group(0) for m in VACUOUS.finditer(region)]
    add("3", "no-vacuous-assert", bool(vac), f"vacuous: {sorted(set(vac))}")

    adnt = [n for n, b, a in methods if ADNT.search(b) and not non_adnt_assert(b)]
    add("4", "no-adnt-only", bool(adnt), f"assertDoesNotThrow-only tests: {adnt}")

    flaky = [m.group(0) for m in FLAKY.finditer(region)]
    add("5", "deterministic", bool(flaky), f"nondeterministic: {sorted(set(flaky))}")

    dis = [m.group(0) for m in DISABLED.finditer(region)]
    add("6", "no-disabled", bool(dis), f"disabled: {sorted(set(dis))}")

    # WEIGHTED: penalty = number of lines of unused code (not a flat 1)
    dead, dead_lines = unused_code(src, baseline)
    add("7", "no-unused-code", bool(dead), f"{dead_lines} unused line(s): {dead}", dead_lines)

    if baseline is None:
        na("8", "additive-only", "pass --baseline to check")
    else:
        removed = [l for l in baseline.splitlines() if l.strip() and l not in set(src.splitlines())]
        add("8", "additive-only", bool(removed), f"{len(removed)} baseline line(s) removed")

    if green is None:
        na("9", "green", "pass --green true|false")
    else:
        add("9", "green", not green, "tests do not compile/pass")

    if mut_before is None or mut_after is None:
        na("10", "mutation-improving", "pass --mut-before N --mut-after M")
    else:
        add("10", "mutation-improving", not (mut_after > mut_before),
            f"kills {mut_before} -> {mut_after} (no gain)")

    # a @Test that validates ONE string PIECEMEAL: >=2 substring checks on the SAME variable
    # (e.g. 6x assertTrue(url.contains("&p=v")) -> should be one assertEquals on the full url).
    # A single presence check (assertThat(out, containsString("<?xml"))) is legit and NOT flagged.
    def piecemeal(b):
        vs = (re.findall(r'assert(?:True|False)\s*\(\s*([\w.()]+)\.(?:contains|startsWith|endsWith|matches)\s*\(\s*"', b)
              + re.findall(r'assertThat\s*\(\s*([\w.()]+)\s*,\s*containsString\s*\(\s*"', b))
        return any(vs.count(v) >= 2 for v in set(vs))
    partial = [n for n, b, a in methods if piecemeal(b)]
    add("11", "no-partial-assert", bool(partial), f"piecemeal substring validation (assert full value): {partial}")

    # trivial getter/setter/equals/hashCode/toString tests — maintainers see these as noise (the
    # mutation gate rewards killing accessor mutants cheaply). Keep only tests with real logic.
    # A getter/setter test is spared if its body has logic (exception/validation/collaboration).
    LOGIC = re.compile(r'assertThrows|assertThatThrownBy|\.isInstanceOf|\bfail\s*\(|Exception|Throws|Validates|\bverify\s*\(|Mockito', re.I)
    def trivial(n, b):
        nl = n.lower()
        if any(k in nl for k in ("equals", "hashcode", "tostring")):
            return True
        if (nl.startswith(("testget", "testset")) or "getter" in nl or "setter" in nl) and not LOGIC.search(b):
            return True
        return False
    accessor = [n for n, b, a in methods if trivial(n, b)]
    add("12", "no-trivial-accessor-test", bool(accessor),
        f"trivial getter/setter/equals/hashCode/toString tests ({len(accessor)}): {accessor[:8]}")

    broken = [r for r in rules if r[2] == "fail"]
    evaluated = [r for r in rules if r[2] != "na"]
    penalty = sum(r[4] for r in rules)
    reward = round(0.9 ** penalty, 4)
    return rules, broken, evaluated, reward, penalty

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("testfile")
    ap.add_argument("--baseline", help="upstream copy of the test file (for additive-only)")
    ap.add_argument("--green", choices=["true", "false"], help="did all tests compile and pass")
    ap.add_argument("--mut-before", type=int, dest="mb")
    ap.add_argument("--mut-after", type=int, dest="ma")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    green = {"true": True, "false": False}.get(a.green)
    rules, broken, evaluated, reward, penalty = evaluate(a.testfile, a.baseline, green, a.mb, a.ma)
    if a.json:
        print(json.dumps({"reward": reward, "penalty": penalty, "broken": [r[1] for r in broken],
                          "rules": [{"id": i, "rule": n, "status": s, "detail": d, "penalty": p}
                                    for i, n, s, d, p in rules]}, indent=2))
        return
    print(f"\n  mergeability reward for {a.testfile}\n")
    for i, n, s, d, p in rules:
        mark = {"pass": "PASS", "fail": "FAIL", "na": " -- "}[s]
        wt = f" (+{p})" if p > 1 else ""   # show the weight only when a rule costs more than 1
        print(f"   [{mark}] {i}. {n}{wt}" + (f"   {d}" if d else ""))
    print(f"\n  penalty = {penalty} ({len(broken)}/{len(evaluated)} rules broken; unused code weighted by line)"
          f"   ->   reward = 0.9 ^ {penalty} = {reward}\n")

if __name__ == "__main__":
    main()
