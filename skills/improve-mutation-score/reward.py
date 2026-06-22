#!/usr/bin/env python3
"""reward.py - mergeability reward for a generated JUnit test file.

reward = 0.9 ** (number of broken rules)   ->  1.0 means nothing broken.

A green, mutation-improving test that a maintainer will not merge is worth
nothing, so "avoid <wart>" is only real if breaking it costs reward. Each rule
below is machine-checkable; every broken rule multiplies the reward by 0.9.

Pure stdlib - runs anywhere (opencode / kilocode / CI), no dependencies.

Static rules (from the file alone):
  1 api-only          no reflection into internals
  2 every-test-asserts no coverage-theater @Test
  3 no-vacuous-assert  no assertTrue(true) / assertEquals(x,x) / assertNotNull("lit")
  4 no-adnt-only       no @Test whose only check is assertDoesNotThrow / try-catch-fail
  5 deterministic      no sleep / unseeded Random / wall-clock / real network/IO
  6 no-disabled        no @Disabled / @Ignore added
  7 no-unused-code     no unused import / unused private field we added
  8 additive-only      (needs --baseline) no existing line removed
Dynamic rules (need build inputs):
  9 green              (needs --green true|false) all tests compile and pass
 10 mutation-improving (needs --mut-before N --mut-after M) kills strictly increased

Rules with missing inputs are reported n/a and excluded from the count.
"""
import argparse, json, re, sys

def read(p):
    try:
        return open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return None

def added_region(src, baseline):
    """Lines present in src but not in baseline (the contribution)."""
    if baseline is None:
        return src
    base = set(baseline.splitlines())
    return "\n".join(l for l in src.splitlines() if l not in base)

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

def non_adnt_assert(body):
    # an assertion that is not assertDoesNotThrow
    return bool(re.search(ASSERT, re.sub(r'assertDoesNotThrow', 'XXX', body)))

def unused_code(src, baseline):
    """Imports we ADDED whose type never appears in the body, and private fields we
    ADDED that are referenced only at their declaration. Conservative (no false
    positives): if baseline given, only lines we introduced are judged."""
    base = set(baseline.splitlines()) if baseline else set()
    body = re.sub(r'(?m)^\s*import\s.*;', '', src)  # strip imports before counting refs
    issues = []
    for m in re.finditer(r'(?m)^\s*import\s+(?!static\b)([\w.]+)\s*;', src):
        if m.group(0).rstrip() in base:            # pre-existing, not ours
            continue
        fqn = m.group(1)
        if fqn.endswith(".*"):
            continue
        simple = fqn.rsplit(".", 1)[-1]
        if not re.search(r'\b' + re.escape(simple) + r'\b', body):
            issues.append("import " + simple)
    for m in re.finditer(r'\bprivate\s+(?:static\s+)?(?:final\s+)?[\w.$<>,\[\]\s]+?\b(\w+)\s*[=;]', src):
        line = src[src.rfind("\n", 0, m.start()) + 1: src.find("\n", m.start())]
        if line in base:
            continue
        name = m.group(1)
        if len(re.findall(r'\b' + re.escape(name) + r'\b', src)) <= 1:
            issues.append("field " + name)
    return issues

def evaluate(path, baseline_path, green, mut_before, mut_after):
    src = read(path)
    if src is None:
        print(f"cannot read {path}", file=sys.stderr); sys.exit(2)
    baseline = read(baseline_path) if baseline_path else None
    region = added_region(src, baseline)
    methods = test_methods(region) or test_methods(src)
    rules = []  # (id, name, status: pass/fail/na, detail)

    refl = [m.group(0) for m in REFLECT.finditer(region)]
    rules.append(("1", "api-only", "fail" if refl else "pass",
                  f"reflection: {sorted(set(refl))}" if refl else ""))

    noassert = [n for n, b, a in methods if not re.search(ASSERT, b) and 'expected' not in a]
    rules.append(("2", "every-test-asserts", "fail" if noassert else "pass",
                  f"no-assertion tests: {noassert}" if noassert else ""))

    vac = [m.group(0) for m in VACUOUS.finditer(region)]
    rules.append(("3", "no-vacuous-assert", "fail" if vac else "pass",
                  f"vacuous: {sorted(set(vac))}" if vac else ""))

    adnt = [n for n, b, a in methods if ADNT.search(b) and not non_adnt_assert(b)]
    rules.append(("4", "no-adnt-only", "fail" if adnt else "pass",
                  f"assertDoesNotThrow-only tests: {adnt}" if adnt else ""))

    flaky = [m.group(0) for m in FLAKY.finditer(region)]
    rules.append(("5", "deterministic", "fail" if flaky else "pass",
                  f"nondeterministic: {sorted(set(flaky))}" if flaky else ""))

    dis = [m.group(0) for m in DISABLED.finditer(region)]
    rules.append(("6", "no-disabled", "fail" if dis else "pass",
                  f"disabled: {sorted(set(dis))}" if dis else ""))

    dead = unused_code(src, baseline)
    rules.append(("7", "no-unused-code", "fail" if dead else "pass",
                  f"unused: {dead}" if dead else ""))

    if baseline is None:
        rules.append(("8", "additive-only", "na", "pass --baseline to check"))
    else:
        removed = [l for l in baseline.splitlines() if l.strip() and l not in set(src.splitlines())]
        rules.append(("8", "additive-only", "fail" if removed else "pass",
                      f"{len(removed)} baseline line(s) removed" if removed else ""))

    if green is None:
        rules.append(("9", "green", "na", "pass --green true|false"))
    else:
        rules.append(("9", "green", "pass" if green else "fail",
                      "" if green else "tests do not compile/pass"))

    if mut_before is None or mut_after is None:
        rules.append(("10", "mutation-improving", "na", "pass --mut-before N --mut-after M"))
    else:
        ok = mut_after > mut_before
        rules.append(("10", "mutation-improving", "pass" if ok else "fail",
                      f"kills {mut_before} -> {mut_after}" + ("" if ok else " (no gain)")))

    broken = [r for r in rules if r[2] == "fail"]
    evaluated = [r for r in rules if r[2] != "na"]
    reward = round(0.9 ** len(broken), 4)
    return rules, broken, evaluated, reward

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
    rules, broken, evaluated, reward = evaluate(a.testfile, a.baseline, green, a.mb, a.ma)
    if a.json:
        print(json.dumps({"reward": reward, "broken": [r[1] for r in broken],
                          "rules": [{"id": i, "rule": n, "status": s, "detail": d} for i, n, s, d in rules]}, indent=2))
        return
    print(f"\n  mergeability reward for {a.testfile}\n")
    for i, n, s, d in rules:
        mark = {"pass": "PASS", "fail": "FAIL", "na": " -- "}[s]
        print(f"   [{mark}] {i}. {n}" + (f"   {d}" if d else ""))
    print(f"\n  broken rules: {len(broken)}/{len(evaluated)} evaluated"
          f"   ->   reward = 0.9 ^ {len(broken)} = {reward}\n")

if __name__ == "__main__":
    main()
