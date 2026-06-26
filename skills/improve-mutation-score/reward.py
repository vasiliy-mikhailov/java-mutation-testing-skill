#!/usr/bin/env python3
"""reward.py - mergeability reward for a generated JUnit test file.

reward = 0.9 ** (penalty)   ->  1.0 means nothing broken.
penalty = the total number of LINES of bad test code, summed across every
quality rule. A rule's penalty is how many lines of added test code violate it
(an offending @Test method counts its full body; a per-line wart counts its
matching lines), so a one-line slip barely dents the reward while a 15-line
warty method (0.9^15 ~= 0.21) tanks it. The two build-outcome rules (green,
mutation-improving) are not line-countable, so they stay binary (1 penalty) and
act as prerequisites the PR gate requires anyway.

A green, mutation-improving test that a maintainer will not merge is worth
nothing, so "avoid <wart>" is only real if breaking it costs reward. Each rule
below is machine-checkable; every penalty line multiplies the reward by 0.9.

Pure stdlib - runs anywhere (opencode / kilocode / CI), no dependencies.

Static rules (from the file alone) -- penalty = lines of offending test code:
  1 api-only          no reflection into internals
  2 every-test-asserts no coverage-theater @Test
  3 no-vacuous-assert  no assertTrue(true) / assertEquals(x,x) / assertNotNull("lit")
  4 no-adnt-only       no @Test whose only check is assertDoesNotThrow / try-catch-fail
  5 deterministic      no sleep / unseeded Random / wall-clock / real network/IO
  6 no-disabled        no @Disabled / @Ignore added
  7 no-unused-code     unused added import / private field / private method
  8 additive-only      (needs --baseline) no existing line removed
 11 no-partial-assert  no @Test that validates a string via substring match (assert the full value)
 12 no-trivial-accessor-test  no pure getter/setter/equals/hashCode/toString test (keep real logic)
 13 no-inner-class     no nested / @Nested / helper class declared in the test (keep tests flat)
 14 no-comment-spam    standalone comment lines <= 1 per 4 code lines (no what-not-why narration)
Dynamic rules (need build inputs) -- binary build outcomes (penalty 0/1):
  9 green              (needs --green true|false) all tests compile and pass
 10 mutation-improving (needs --mut-before N --mut-after M) kills strictly increased

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

def _comment_spam(region):
    """Count FULL-LINE comments vs code lines in the added region, and the lines OVER the
    1-comment-per-4-code-lines budget. A trailing `assertX(...); // why` is a code line, not a
    comment line, so good why-comments on assertions are never penalized -- only standalone
    comment lines (//, /* */ blocks, javadoc * continuations) that out-pace the code count."""
    comment_lines = code_lines = 0
    in_block = False
    for raw in region.splitlines():
        s = raw.strip()
        if in_block:
            comment_lines += 1
            if "*/" in s:
                in_block = False
            continue
        if not s:
            continue
        if s.startswith("//") or s.startswith("*"):
            comment_lines += 1
        elif s.startswith("/*"):
            comment_lines += 1
            if "*/" not in s:
                in_block = True
        else:
            code_lines += 1
    penalty = max(0, comment_lines - code_lines // 4)  # budget: 1 comment line per 4 code lines
    return comment_lines, code_lines, penalty

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
    rules = []  # (id, name, status, detail, penalty)
    def add(i, name, penalty, detail):
        # penalty = LINES of bad test code for this rule (0 = clean). reward = 0.9 ** total penalty.
        rules.append((i, name, "fail" if penalty else "pass", detail if penalty else "", penalty))
    def na(i, name, hint):
        rules.append((i, name, "na", hint, 0))
    def mlines(names):  # total source lines of the flagged @Test methods
        return sum(b.count("\n") + 1 for n, b, a in methods if n in names)
    def plines(pat):    # region lines containing a match
        return sum(1 for line in region.splitlines() if pat.search(line))

    refl = sorted({m.group(0) for m in REFLECT.finditer(region)})
    add("1", "api-only", plines(REFLECT), f"reflection lines: {refl}")

    noassert = [n for n, b, a in methods if not re.search(ASSERT, b) and "expected" not in a]
    add("2", "every-test-asserts", mlines(noassert), f"no-assertion tests: {noassert}")

    vac = sorted({m.group(0) for m in VACUOUS.finditer(region)})
    add("3", "no-vacuous-assert", plines(VACUOUS), f"vacuous: {vac}")

    adnt = [n for n, b, a in methods if ADNT.search(b) and not non_adnt_assert(b)]
    add("4", "no-adnt-only", mlines(adnt), f"assertDoesNotThrow-only tests: {adnt}")

    flaky = sorted({m.group(0) for m in FLAKY.finditer(region)})
    add("5", "deterministic", plines(FLAKY), f"nondeterministic: {flaky}")

    add("6", "no-disabled", plines(DISABLED), "@Disabled/@Ignore lines")

    dead, dead_lines = unused_code(src, baseline)
    add("7", "no-unused-code", dead_lines, f"{dead_lines} unused line(s): {dead}")

    if baseline is None:
        na("8", "additive-only", "pass --baseline to check")
    else:
        removed = [l for l in baseline.splitlines() if l.strip() and l not in set(src.splitlines())]
        add("8", "additive-only", len(removed), f"{len(removed)} baseline line(s) removed")

    # green / mutation-improving are build OUTCOMES (not line-countable) -> binary 1, and the PR gate
    # requires them true anyway, so they act as prerequisites rather than line-weighted quality.
    if green is None:
        na("9", "green", "pass --green true|false")
    else:
        add("9", "green", 0 if green else 1, "tests do not compile/pass")

    if mut_before is None or mut_after is None:
        na("10", "mutation-improving", "pass --mut-before N --mut-after M")
    else:
        add("10", "mutation-improving", 0 if mut_after > mut_before else 1,
            f"kills {mut_before} -> {mut_after} (no gain)")

    def piecemeal(b):
        vs = (re.findall(r"assert(?:True|False)\s*\(\s*([\w.()]+)\.(?:contains|startsWith|endsWith|matches)\s*\(\s*\"", b)
              + re.findall(r"assertThat\s*\(\s*([\w.()]+)\s*,\s*containsString\s*\(\s*\"", b))
        return any(vs.count(v) >= 2 for v in set(vs))
    partial = [n for n, b, a in methods if piecemeal(b)]
    add("11", "no-partial-assert", mlines(partial), f"piecemeal substring validation: {partial}")

    LOGIC = re.compile(r"assertThrows|assertThatThrownBy|\.isInstanceOf|\bfail\s*\(|Exception|Throws|Validates|\bverify\s*\(|Mockito", re.I)
    def trivial(n, b):
        nl = n.lower()
        if any(k in nl for k in ("equals", "hashcode", "tostring")):
            return True
        if (nl.startswith(("testget", "testset")) or "getter" in nl or "setter" in nl) and not LOGIC.search(b):
            return True
        return False
    accessor = [n for n, b, a in methods if trivial(n, b)]
    add("12", "no-trivial-accessor-test", mlines(accessor),
        f"trivial getter/setter/equals/hashCode/toString tests: {accessor[:8]}")

    # no nested (inner) classes in the test -- keep tests flat; @Nested / helper classes add structure noise.
    inner_lines, inner_names = 0, []
    for m in re.finditer(r"(?m)^[ \t]+(?:(?:public|private|protected|static|final|abstract)\s+)*class\s+(\w+)", region):
        br = region.find("{", m.end())
        if br == -1:
            continue
        e = _brace_end(region, br)
        inner_lines += region.count("\n", m.start(), e) + 1
        inner_names.append(m.group(1))
    add("13", "no-inner-class", inner_lines, f"inner classes {inner_names}: {inner_lines} line(s)")

    # no comment-spam -- standalone comment lines may not out-pace code more than 1 per 4 code lines.
    cmt, code, over = _comment_spam(region)
    add("14", "no-comment-spam", over,
        f"{cmt} comment line(s) vs {code} code line(s) -> {over} over the 1:4 budget")

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
    print(f"\n  penalty = {penalty} lines of bad test code ({len(broken)}/{len(evaluated)} rules broken)"
          f"   ->   reward = 0.9 ^ {penalty} = {reward}\n")

if __name__ == "__main__":
    main()
