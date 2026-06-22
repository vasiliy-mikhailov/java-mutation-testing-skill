---
name: improve-mutation-score
description: Raise a Java (Maven or Gradle) project's PIT mutation score by adding tests that make the suite DETECT the mutations it currently misses — asserting real behaviour, append-only, never weakening an existing test. Use when asked to improve mutation coverage or mutation score, strengthen a Java test suite that passes but does not assert much, or turn line coverage into real verification.
---

# Improve a Java project's PIT mutation score

A green test suite proves nothing about whether it would **catch a regression**. PIT mutation testing
makes that measurable: it mutates the code (flips `<` to `<=`, replaces a return with `null`, removes
a void call) and reruns the tests. A mutant the tests still pass on **survived** — that line is
executed but **not verified**. The goal: **raise the mutation score** (detected / total) by adding
tests that make the suite *detect* the surviving mutants — **without weakening any existing test**.

Work on **one class at a time** (whole-repo mutation is far too slow). Standard tools only: the
build's PIT plugin and `git`. (PIT labels a detected mutant `KILLED` in its report — that's the
tool's vocabulary; our job is to make the suite *catch* what it currently misses.)

**The reward you maximize: +1 for every mutant that no longer survives** once your tests are added — `survived_before − survived_after`, the drop in PIT's surviving-mutant count (e.g. 4000 survivors → 10 = reward 3990). Drive it with the loop in §5 — each pass adds tests and re-scores; keep going while the reward stays positive.

## 0. Preconditions
- **Detect and use the right JDK FIRST** — follow the **`detect-java-version`** skill. A project's
  real build floor can exceed its declared target, and PIT's forked coverage minion crashes under
  the wrong JDK. Determine the JDK, then run EVERY command below under it (`JAVA_HOME` / the matching
  JDK container).
- **Detect the build tool:** root `pom.xml` → **Maven**; `build.gradle`/`.kts` (+ `gradlew`) and no
  `pom.xml` → **Gradle**. Use the project's wrapper when present (`./mvnw`, `./gradlew`).
- **Green baseline.** PIT refuses to run if any in-scope test already fails. Confirm the suite is
  green first; tests already red in the baseline (no DB/network/Docker) are **not** your concern —
  scope PIT away from them.
- **Detect the test framework and version FIRST** — follow the **`detect-unit-testing-framework`** skill
  (JUnit 4 / 5 / **6** / TestNG, resolving `${...}`/BOM version indirection). It decides how PIT is wired
  (§2) and how you write the new tests; the wrong wiring makes PIT find 0 tests or crash its minion.
- `git` — commit a baseline first so your additions are an isolated diff.

## 1. Pick one target class
- **Coverage first** — a class with **high line coverage but low mutation score** is the richest
  target: the tests run it but don't assert on it.
- Otherwise pick a **logic-dense** class (branches, arithmetic, parsing, state) that has an existing
  `FooTest`. Skip trivial getters/DTOs.

Let `C` = fully-qualified class, `T` = its test class or package glob.

## 2. Measure the baseline — run PIT scoped to that one class
**Mutate with the FULL operator set — `-Dmutators=ALL`** on every `mutationCoverage` invocation
(Gradle: `mutators = ['ALL']`). `ALL` surfaces mutation kinds `DEFAULTS` can't — removed conditionals,
member-variable writes, switches, extra return/boundary variants — and **each surviving kind is a distinct
test idea**. Expect *more* survivors at the start and *more* equivalents (§7), but a richer set of real
weaknesses to fix, so more mutants ultimately killed. (Pair with `-DwithHistory=true`, §5, to keep the
bigger mutant set fast to re-run.)
**Maven, JUnit 4** — invoke PIT as a one-off goal, no `pom.xml` change needed:
```bash
./mvnw -B -DskipTests test-compile
./mvnw -B org.pitest:pitest-maven:1.15.2:mutationCoverage \
  -DtargetClasses=C -DtargetTests=T -Dmutators=ALL -DoutputFormats=XML,HTML -DtimestampedReports=false
```
**Maven, JUnit 5** — add the `pitest-junit5-plugin` to the PIT plugin in `pom.xml` (a CLI `-D` can't
add a plugin dependency):
```xml
<plugin><groupId>org.pitest</groupId><artifactId>pitest-maven</artifactId><version>1.15.2</version>
  <dependencies><dependency><groupId>org.pitest</groupId>
    <artifactId>pitest-junit5-plugin</artifactId><version>1.2.1</version></dependency></dependencies>
</plugin>
```
**Maven, JUnit 6** — JUnit 6 unified its versioning, so `junit-platform-*` share the **jupiter version**
(e.g. `6.1.0`). PIT's older bundled launcher then mismatches the project's engine (`OutputDirectoryCreator
not available; unaligned junit-platform-engine/launcher`). Use a **current** PIT + `pitest-junit5-plugin`,
and pin a `junit-platform-launcher` to the project's platform version so engine == launcher:
```xml
<plugin><groupId>org.pitest</groupId><artifactId>pitest-maven</artifactId><version>1.25.4</version>
  <dependencies>
    <dependency><groupId>org.pitest</groupId><artifactId>pitest-junit5-plugin</artifactId><version>1.2.3</version></dependency>
    <dependency><groupId>org.junit.platform</groupId><artifactId>junit-platform-launcher</artifactId><version>6.1.0</version></dependency>
  </dependencies>
</plugin>
```
**TestNG** — add `pitest-testng-plugin` (`org.pitest:pitest-testng-plugin:1.0.0`) to the PIT plugin's `<dependencies>`; current PIT externalized TestNG (no longer built-in).

Inject the plugin into the project's **main `<build>`**, never a `<profile>` build — a plugin inside an
inactive profile is silently ignored and PIT runs with no engine (0 coverage).

**Gradle** — apply `info.solidsoft.pitest` (+ `junit5PluginVersion` for JUnit 5), scope it, `./gradlew pitest`.

Report lands at `target/pit-reports/mutations.xml` (Maven) or `build/reports/pitest/mutations.xml`.

## 3. Read the surviving mutants — compactly, in batches
The mutants to address are `status="SURVIVED"` and `status="NO_COVERAGE"`. **Do NOT `cat` the whole
`mutations.xml`** — on a mutant-dense class it is enormous, and reading it repeatedly floods your context;
that is exactly how a God-class run drowns and dies mid-task (tens of millions of tokens, a cut-off
response, a half-edited test left broken → BROKE_BUILD). Instead **extract only the survivors compactly**
(grep / `xmllint` / awk out `<lineNumber>`, `<mutator>`, `<mutatedMethod>`, `<description>` for the
`SURVIVED` / `NO_COVERAGE` rows), and on a class with **many survivors work in BATCHES**: pull ~10–20 at a
time, kill them, re-run (§5), then take the next batch. Bounded context per pass = you can go **as deep as
the class needs** without the report swamping the run. Mutation score = detected / total.

## 4. Strengthen the suite to catch each survivor — append-only
**Bank a verified win early — act, don't just plan.** Do *not* read and plan every survivor before
writing anything; take the **first** survivor, write its test now, run the §5 loop to confirm it lands,
then move to the next. Each verified test locks in real progress — a perfect plan you never execute
scores nothing. **You have unlimited iterations: use them to go as DEEP as possible**, one banked test
at a time, until no survivor can be killed — never rush or stop early, there is no turn budget.

**Kill the cheap survivors first — `NO_COVERAGE` before `SURVIVED`.** Split the survivors into two piles:
`NO_COVERAGE` (the line never runs — no test even calls it) and `SURVIVED` (the line runs but nothing
asserts on it). Go after **`NO_COVERAGE` first**: it is usually the *larger* pile and the *easiest* drop —
just call the method with representative inputs and assert the result, and a whole cluster of survivors on
that method falls at once. Only then spend effort on `SURVIVED`, where you need a sharper assertion.

For each survivor, open the source at its line, understand **what the mutation changed**, and add a
**new** test that **fails on the mutant but passes on the real code** (so the suite now detects it).
Map the mutator to the assertion it needs:

| Mutator | What it changes | The assertion that catches it |
|---|---|---|
| `ConditionalsBoundary` | `<`↔`<=`, `>`↔`>=` | exercise the value **exactly at the boundary**; assert the branch taken there |
| `NegateConditionals` | `==`↔`!=`, etc. | assert behaviour on **both** sides of the condition |
| `Math` | `+`↔`-`, `*`↔`/`, `%` | pick inputs where the operations **differ**; assert the exact numeric result |
| `Increments` | `i++`↔`i--` | assert the **final counted/accumulated** value |
| `(Null/Empty/Primitive/Boolean)ReturnVals` | return → `null`/`""`/`0`/`false` | assert the **actual returned value** — never just "doesn't throw" |
| `VoidMethodCall` | removes a `foo()` call | assert the **observable side effect** of that call |
| `EmptyObjectReturnVals` | return → empty | assert the returned object's **content** (length, a known element) |

**Hard rules — the score must rise from a *stronger* test, never a laxer one:**
- **Append-only.** Add new `@Test` methods; **never edit, delete, or relax an existing test** — this
  guarantees you can't weaken the suite.
- Your assertions must **pass against the real (unmutated) code**. A test asserting the *mutant's*
  wrong behaviour fails the green baseline — that's the build telling you the assertion is wrong.
- Match the existing test class's framework, imports, and style; put new methods in the matching `FooTest`.

## 5. The Ralph loop — re-run yourself until the reward stops
Treat §3→§4→§5 as one loop body and **repeat it on yourself**, Ralph-style, until the reward dries up:

```
loop:
  re-run the scoped PIT from §2          # produces a fresh mutations.xml
  reward = survived_prev - survived_now  # +1 for each survivor this pass removes
  if PIT is red (a new test failed)  -> fix or drop that test; never leave the suite red
  if reward == 0 on this pass        -> run the §6 mergeability checker; if its reward < 1.0, fix the
                                        broken rules (without losing a kill) and continue; else STOP
                                        (only equivalent/untestable survivors remain, §7)
  else                               -> read the still-SURVIVED mutants (§3), add tests (§4), continue
```

**Big class (logic-dense / 2000+ lines / many methods)? You are the TEST-MANAGER — orchestrate, do NOT
write tests yourself.** Authoring every method's tests in YOUR own context is what drowns the run (millions
of tokens, a cut-off response, a broken file -> BROKE_BUILD). Instead:
1. **List the methods** of the class (just read the source).
2. **For each method, ONE AT A TIME (sequentially — never in parallel: the sub-agents all edit the same test
   file and would collide), delegate to a fresh `mutation-tester` sub-agent.** Give it the method name, the
   class, the test class, the JDK, and the per-method PIT command. The mutation-tester scopes PIT to that one
   method (`-DexcludedMethods=` every OTHER method plus `<init>,<clinit>`, quoted), reads that method's
   survivors, APPENDS tests that kill them, runs them, FIXES any breakage, and reports the `@Test` names it
   added. **Use `mutation-tester` — NOT `code-explorer` or `bash-runner`; those only analyze, they will not
   author or fix tests** (that mistake produces a long run that writes zero tests).
3. You do NOT touch the test file yourself — each mutation-tester already edited it. Move to the next method.
4. When every method is done, run ONE whole-class PIT (no `withHistory`) to confirm the overall score rose and
   ALL tests compile + are green. If the build is broken, delegate the fix to a `mutation-tester`.
5. The harness commits the result and opens the PR — you are done when the whole-class build is green and the
   mutation score is up.
For a small/medium class do NOT delegate — just write the tests yourself; delegation only pays off when a
single context cannot hold the whole class.

**Make the loop cheap so you can be exhaustive:** add **`-DwithHistory=true`** to your iterative PIT
re-runs. PIT caches results for unchanged production code + tests and only re-evaluates the mutants your
new tests could affect — often **10-50x faster** per pass, since you only ever *add* tests (the code never
changes). That speed is what lets you keep going until the survivor count really bottoms out instead of
quitting early. (`withHistory` is experimental — do your **final** confirming run *without* it for an
honest score.)

**Keep the additions only if** PIT runs clean (all tests green) and the mutation score rose. Each pass
re-reads the *fresh* report, so you always target the survivors that still remain. Stop at the plateau —
the first full pass that removes **zero** survivors (reward 0).

**Definition of done — compile, and if it doesn't compile, fix the tests.** After every edit, recompile
(the §5 PIT re-run does this, or run `test-compile` directly). **If it doesn't compile, read the compiler
error and fix the offending test** — a bad or duplicate import, a typo, the wrong type — or drop that one
test, then compile again. Loop until it compiles. You are *not* finished until the scoped PIT re-runs
**green** and the score **rose**; stopping on an unverified or non-compiling edit gets the whole run
discarded as BROKE_BUILD.

## 6. Mergeability reward — a green test a maintainer won't merge scores nothing
Mutation score makes a test **strong**; it does not make it **mergeable**. Maintainers reject tests that
reach into internals, assert nothing, or flake — so "avoid that wart" is empty advice unless breaking it
**costs reward**. Score every test file you touch with the bundled checker (pure stdlib — runs anywhere):

```
python skills/improve-mutation-score/reward.py <TestFile> \
    --baseline <upstream copy of the file> --green true --mut-before <N> --mut-after <M>
```

**reward = 0.9 ^ (penalty)** — `1.0` means nothing broken; each broken binary rule costs 1 penalty (a 0.9
factor), and **unused code (rule 7) costs 1 penalty per LINE** — a one-line dead import barely dents the
reward, a 15-line dead method (0.9^15 ≈ 0.21) tanks it. The rules:

| # | rule | broken when |
|---|---|---|
| 1 | api-only | reaches internals via reflection (`setAccessible`, `getDeclaredField`, `ReflectionTestUtils`, `Whitebox`) — drive the public API instead |
| 2 | every-test-asserts | a `@Test` exercises code but asserts nothing (coverage theater) |
| 3 | no-vacuous-assert | `assertTrue(true)`, `assertEquals(x, x)`, `assertNotNull("literal")` |
| 4 | no-adnt-only | a `@Test` whose only check is `assertDoesNotThrow` / try-catch-`fail` — assert the real result |
| 5 | deterministic | `Thread.sleep`, unseeded `new Random()`, wall-clock, real network / file IO |
| 6 | no-disabled | adds `@Disabled` / `@Ignore` |
| 7 | no-unused-code | leaves dead code — unused import / private field / private method; **penalty = number of unused lines** |
| 8 | additive-only | removes or edits any existing line (see §4) |
| 9 | green | a test does not compile or fails |
| 10 | mutation-improving | mutant kills did not strictly rise vs the baseline |

**This is part of the §5 loop, not a final gate.** Each pass, once PIT is green, run the checker and treat
every FAILED rule as more work. Fix each broken rule **without losing a kill** — rewrite the offending test
so it *still* fails on the mutant but now goes through the public API / asserts the real value / is
deterministic. **Never delete a test or weaken an assertion just to clear a rule.** If a rule genuinely
cannot be satisfied without dropping a mutant kill, keep the kill and record the residual in the PR. **Do
not stop at "tests are green" — stop at reward `1.0`** (or a documented residual you cannot remove without
losing mutation coverage).

## 7. Equivalent mutants are RARE — earn the right to skip one
A truly **equivalent** mutant (semantically identical behaviour — no test can detect it: a branch with no
observable effect, a redundant boundary on an unreachable value, reordered commutative ops) is the *rare*
exception, not the explanation for a stuck survivor. **Most survivors are killable** — they just need the
right input or to be reached at all. Before you write a survivor off as equivalent, actually try: feed an
input that exercises the exact mutated branch/value and assert the differing output; reach an unreached
method (`NO_COVERAGE`); mock a collaborator to observe a `VoidMethodCall` side effect. Only after a genuine
differentiating attempt fails do you set it aside — and say so. Do **not** end the §5 loop on the first
`reward == 0` pass if you have not yet tried a *fresh* approach on the survivors that remain; the goal is
the **lowest survivor count you can reach**, not the first plateau. 100% is rare, but get as close as the
non-equivalent survivors allow.

## 8. Open a PR
Open a PR only once the §6 mergeability **reward is 1.0** (or the only residual is a rule you documented as
unremovable without losing a kill). Branch, commit the **append-only** test additions (plus the PIT build
config if added for JUnit 5), and open a PR whose body reports the **mutation score AND line coverage
before → after**, the additional mutants now detected, and that the additions are append-only, green, and
clear the mergeability rules.

---

## Gotchas
- **Project quality gates fight mutation testing — skip them.** A `jacoco:check` coverage threshold, or
  `checkstyle` / `enforcer` / `spotless` / `pmd` / `spotbugs`, will **fail the build** when you add tests to
  one class (they judge the whole project / style, not your scoped change). On every `mvn`/PIT command pass
  **`-Djacoco.skip=true -Dcheckstyle.skip=true -Denforcer.skip=true -Dspotless.check.skip=true -Dpmd.skip=true
  -Dspotbugs.skip=true`**. A green scoped PIT run is the only gate that matters — do not chase a project
  coverage threshold.
- **JUnit 5 + PIT** needs `pitest-junit5-plugin` (§2); without it PIT reports *0 tests* / all
  `NO_COVERAGE`. That symptom means wrong wiring, not bad coverage.
- **PIT's bundled platform too old for new JUnit** — if the project is on **JUnit 5.11+ / 6.x**
  (JUnit Platform ≥ 1.11) and baseline PIT shows **0 killed / all `NO_COVERAGE`** despite a green suite,
  PIT's launcher is too old to run the tests. **Bump `pitest-maven` + `pitest-junit5-plugin` to a current
  release** (or add a matching `junit-platform-launcher` to the PIT plugin classpath). **Never downgrade the
  project's own JUnit or dependencies to fit PIT** — that breaks the build for the rest of the module. If a
  build-config edit does not compile or run, revert it (compile-then-fix).
- **PIT coverage-minion crash** (`Minion exited abnormally`) = test-instrumentation too old for the
  JDK: raise **Mockito ≥ 5.18** / **ByteBuddy ≥ 1.14.12 (17/21), ≥ 1.17.6 (25)**, or pass the
  project's `--add-opens` to the test fork. EasyMock has no JDK-25 path.
- **`NO_COVERAGE`** survivors mean the line isn't executed by the scoped tests — add a test that
  **reaches** the code first, then **asserts** on it.
- **Flaky `TIMED_OUT`** mutants flip run-to-run; compare before/after in the **same** PIT config.
- **Give every PIT/maven command a HUGE command timeout** (~1 year, e.g. `timeout=31536000`). A
  mutant-dense method under `-Dmutators=ALL` can run many minutes; a short per-command timeout cuts the
  PIT pass mid-run and looks like a failure. A command that "timed out" is usually the timeout being too
  small, not a real hang — never short-time a PIT command.
- **Keep PIT scoped** to the one class (`targetClasses`) — unscoped mutation of a whole module runs for hours.
