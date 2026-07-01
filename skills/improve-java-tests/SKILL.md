---
name: improve-java-tests
description: Raise a Java (Maven or Gradle) project's PIT mutation score by adding tests that make the suite DETECT the mutations it currently misses: asserting real behaviour, append-only, never weakening an existing test. Use when asked to improve mutation coverage or mutation score, strengthen a Java test suite that passes but does not assert much, or turn line coverage into real verification.
---

# Improve a Java project's PIT mutation score

A green test suite proves nothing about whether it would **catch a regression**. PIT mutation testing
makes that measurable: it mutates the code (flips `<` to `<=`, replaces a return with `null`, removes
a void call) and reruns the tests. A mutant the tests still pass on **survived**: that line is
executed but **not verified**. The goal: **raise the mutation score** (detected / total) by adding
tests that make the suite *detect* the surviving mutants, **without weakening any existing test**.

Work on **one class at a time** (whole-repo mutation is far too slow). Standard tools only: the
build's PIT plugin and `git`. (PIT labels a detected mutant `KILLED` in its report; that's the
tool's vocabulary. The goal is to make the suite *catch* what it currently misses.)

**The reward you maximize: +1 for every mutant that no longer survives** once your tests are added: `survived_before − survived_after`, the drop in PIT's surviving-mutant count (e.g. 4000 survivors to 10 = reward 3990). Drive it with the loop in §5: each pass adds tests and re-scores; keep going while the reward stays positive.

## 0. Preconditions
- **Detect and use the right JDK FIRST**: follow the **`detect-java-version`** skill. A project's
  real build floor can exceed its declared target, and PIT's forked coverage minion crashes under
  the wrong JDK. Determine the JDK, then run EVERY command below under it (`JAVA_HOME` / the matching
  JDK container).
- **Detect the build tool:** root `pom.xml` → **Maven**; `build.gradle`/`.kts` (+ `gradlew`) and no
  `pom.xml` → **Gradle**. Use the project's wrapper when present (`./mvnw`, `./gradlew`).
- **Green baseline.** PIT refuses to run if any in-scope test already fails. Confirm the suite is
  green first; tests already red in the baseline (no DB/network/Docker) are **not** your concern:
  scope PIT away from them.
- **Detect the test framework and version FIRST**: follow the **`detect-unit-testing-framework`** skill
  (JUnit 4 / 5 / **6** / TestNG, resolving `${...}`/BOM version indirection). It decides how PIT is wired
  (§2) and how you write the new tests; the wrong wiring makes PIT find 0 tests or crash its minion.
- `git`: commit a baseline first so your additions are an isolated diff.

## 1. Pick one target class
- If the harness names a class, use exactly that one (it may already have a `FooTest`, or none yet).
- **Coverage first**: a class with **high line coverage but low mutation score** is the richest
  target: the tests run it but don't assert on it.
- An **untested** logic-dense class (branches, arithmetic, parsing, state) is just as good a target:
  it baselines at all-`NO_COVERAGE` (every mutant survives), and you raise the score by writing its
  first test from scratch (§4).
- Skip trivial getters/DTOs/enums: they have nothing to mutate, so PIT finds no survivors.

Let `C` = fully-qualified class, `T` = its test class or package glob.

## 2. Measure the baseline: run PIT scoped to that one class
**Mutate with the FULL operator set, `-Dmutators=ALL`** on every `mutationCoverage` invocation
(Gradle: `mutators = ['ALL']`). `ALL` surfaces mutation kinds `DEFAULTS` can't (removed conditionals,
member-variable writes, switches, extra return/boundary variants), and **each surviving kind is a distinct
test idea**. Expect *more* survivors at the start and *more* equivalents (§7), but a richer set of real
weaknesses to fix, so more mutants ultimately killed. (Pair with `-DwithHistory=true`, §5, to keep the
bigger mutant set fast to re-run.)
**Maven, JUnit 4**: invoke PIT as a one-off goal, no `pom.xml` change needed:
```bash
./mvnw -B -DskipTests test-compile
./mvnw -B org.pitest:pitest-maven:1.15.2:mutationCoverage \
  -DtargetClasses=C -DtargetTests=T -Dmutators=ALL -DoutputFormats=XML,HTML -DtimestampedReports=false
```
**Maven, JUnit 5**: add the `pitest-junit5-plugin` to the PIT plugin in `pom.xml` (a CLI `-D` can't
add a plugin dependency):
```xml
<plugin><groupId>org.pitest</groupId><artifactId>pitest-maven</artifactId><version>1.15.2</version>
  <dependencies><dependency><groupId>org.pitest</groupId>
    <artifactId>pitest-junit5-plugin</artifactId><version>1.2.1</version></dependency></dependencies>
</plugin>
```
**Maven, JUnit 6**: JUnit 6 unified its versioning, so `junit-platform-*` share the **jupiter version**
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
**TestNG**: add `pitest-testng-plugin` (`org.pitest:pitest-testng-plugin:1.0.0`) to the PIT plugin's `<dependencies>`; current PIT externalized TestNG (no longer built-in).

Inject the plugin into the project's **main `<build>`**, never a `<profile>` build: a plugin inside an
inactive profile is silently ignored and PIT runs with no engine (0 coverage).

**Gradle**: apply `info.solidsoft.pitest` (+ `junit5PluginVersion` for JUnit 5), scope it, `./gradlew pitest`.

Report lands at `target/pit-reports/mutations.xml` (Maven) or `build/reports/pitest/mutations.xml`.

## 3. Read the surviving mutants: compactly, in batches
The mutants to address are `status="SURVIVED"` and `status="NO_COVERAGE"`. **Do NOT `cat` the whole
`mutations.xml`**: on a mutant-dense class it is enormous, and reading it repeatedly floods your context;
that is exactly how a God-class run drowns and dies mid-task (tens of millions of tokens, a cut-off
response, a half-edited test left broken → BROKE_BUILD). Instead **extract only the survivors compactly**
(grep / `xmllint` / awk out `<lineNumber>`, `<mutator>`, `<mutatedMethod>`, `<description>` for the
`SURVIVED` / `NO_COVERAGE` rows), and on a class with **many survivors work in BATCHES**: pull ~10 to 20 at a
time, kill them, re-run (§5), then take the next batch. Bounded context per pass means you can go **as deep as
the class needs** without the report swamping the run. Mutation score = detected / total.

## 4. Strengthen the suite to catch each survivor: append-only
**Bank a verified win early, act, don't just plan.** Do *not* read and plan every survivor before
writing anything; take the **first** survivor, write its test now, run the §5 loop to confirm it lands,
then move to the next. Each verified test locks in real progress: a perfect plan you never execute
scores nothing. **You have unlimited iterations: use them to go as DEEP as possible**, one banked test
at a time, until no survivor can be killed. Never rush or stop early, there is no turn budget.

**Kill the cheap survivors first, `NO_COVERAGE` before `SURVIVED`.** Split the survivors into two piles:
`NO_COVERAGE` (the line never runs, no test even calls it) and `SURVIVED` (the line runs but nothing
asserts on it). Go after **`NO_COVERAGE` first**: it is usually the *larger* pile and the *easiest* drop.
Just call the method with representative inputs and assert the result, and a whole cluster of survivors on
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
| `(Null/Empty/Primitive/Boolean)ReturnVals` | return → `null`/`""`/`0`/`false` | assert the **actual returned value**, never just "doesn't throw" |
| `VoidMethodCall` | removes a `foo()` call | assert the **observable side effect** of that call |
| `EmptyObjectReturnVals` | return → empty | assert the returned object's **content** (length, a known element) |

**Hard rules: the score must rise from a *stronger* test, never a laxer one:**
- **Append-only.** Add new `@Test` methods; **never edit, delete, or relax an existing test**: this
  guarantees you can't weaken the suite.
- Your assertions must **pass against the real (unmutated) code**. A test asserting the *mutant's*
  wrong behaviour fails the green baseline: that's the build telling you the assertion is wrong.
- Match the existing test class's framework, imports, and style; put new methods in the matching `FooTest`.
  If the class has **no test yet**, CREATE `FooTest` following a sibling test in the same module (its
  assertion library, imports, naming, package layout), then add methods to it, the same append-only
  discipline starting from an empty test.

## 5. The Ralph loop: re-run yourself until the reward stops
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

**More than about 3 methods to cover? You are the TEST-MANAGER: delegate one method at a time, do NOT
author them all yourself.** Writing every method's tests in YOUR own context is what drowns the run: PIT
reports, file edits, and the class source pile into one conversation until it exceeds the model window and
the response is cut off mid-edit (a truncated tool call, a broken file, a left-broken build). The cure is
to give each method its own fresh context.

Delegate with **your environment's sub-agent / task tool** (e.g. an OpenHands `task`, a Claude Code Task).
Pick a sub-agent that can **edit files and run tests** -- NOT a read-only "explore / analyze" agent, which
will read the code and write nothing. **Your tool may warn "do not delegate file edits, use the editor
directly" -- that default does NOT apply here:** the per-method test-writing IS exactly what you delegate,
because a multi-method class does not fit in one context. This is the intended exception.

Workflow:
1. **List the methods** of the class (just read the source).
2. **For each method, ONE AT A TIME (sequential, NEVER parallel: the sub-agents share one test file and
   would collide), spawn a sub-agent and give it this brief:**
   > Raise the PIT mutation score of method `M` of class `C` (test class `T`, JDK `<jdk>`). Run the §5
   > loop scoped to `M` ONLY: keep `-DtargetClasses=C -DtargetTests=T` and add
   > `-DexcludedMethods="<every OTHER method of C, plus <init>,<clinit>>"` (keep it QUOTED so the shell
   > never treats `<init>` as a redirect). Read `M`'s surviving mutants, APPEND `@Test` methods named
   > `test<Method>_<case>` (never modify an existing test, never touch production code), run them, FIX any
   > compile/red breakage, and re-run the scoped PIT until survivors stop dropping. PIT is slow on a
   > mutant-dense method -- give every command a huge timeout and be patient; a slow command is not a hang.
   > Report back SHORT: the `@Test` names added, `M`'s score before->after, and "T compiles + all green" (or
   > exactly what is still broken). Never paste raw PIT / build output back -- distill it.
3. You do NOT touch the test file yourself between delegations. Move to the next method.
4. When every method is done, run ONE whole-class PIT (no `withHistory`) to confirm the overall score rose and
   ALL tests compile + are green. If the build is broken, delegate the fix to a sub-agent (hand it the javac
   errors).
5. Have the result scored by a separate sub-agent, not by your own opinion (§6). A self-score earns nothing
   here: the reward that counts comes from a fresh sub-agent that did not write these tests and shares none of
   your context. Give it the test class and the §6 rubric and let it score the added diff. If its reward is
   below `1.0`, delegate fixes for the offending lines to a writer sub-agent (without dropping a kill) and
   re-judge until the judge reaches `1.0`.
6. You are done when the whole-class build is green, the mutation score is up, and the judge's reward is `1.0`.
For a tiny class (1-3 methods) just write the tests yourself, then still hand them to a separate judge to
score (a self-score earns nothing); the delegation overhead only pays off past
one context's worth of work.

**Make the loop cheap so you can be exhaustive:** add **`-DwithHistory=true`** to your iterative PIT
re-runs. PIT caches results for unchanged production code + tests and only re-evaluates the mutants your
new tests could affect, often **10-50x faster** per pass, since you only ever *add* tests (the code never
changes). That speed is what lets you keep going until the survivor count really bottoms out instead of
quitting early. (`withHistory` is experimental: do your **final** confirming run *without* it for an
honest score.)

**Keep the additions only if** PIT runs clean (all tests green) and the mutation score rose. Each pass
re-reads the *fresh* report, so you always target the survivors that still remain. Stop at the plateau:
the first full pass that removes **zero** survivors (reward 0).

**Definition of done: compile, and if it doesn't compile, fix the tests.** After every edit, recompile
(the §5 PIT re-run does this, or run `test-compile` directly). **If it doesn't compile, read the compiler
error and fix the offending test** (a bad or duplicate import, a typo, the wrong type) or drop that one
test, then compile again. Loop until it compiles. You are *not* finished until the scoped PIT re-runs
**green** and the score **rose**; stopping on an unverified or non-compiling edit gets the whole run
discarded as BROKE_BUILD.

## 6. Mergeability reward: a green test a maintainer won't merge scores nothing
Mutation score makes a test **strong**; it does not make it **mergeable**. Maintainers reject tests that
reach into internals, assert nothing, or flake, so "avoid that wart" is empty advice unless breaking it
**costs reward**. **The agent that wrote the tests never scores them** -- grading your own work while you
are driven to finish is a conflict of interest that inflates the score. A **separate judge sub-agent**, one
that did not write the tests and shares none of your context, scores them instead. This
skill ships as a pure rubric: the environment may have no Python, no install step, nothing but a fresh model
and the file, so the judge is just another sub-agent applying the rules below. The judge reads the test diff
**the writer added** (compare against the upstream copy of the file so pre-existing code is never counted),
and for each rule below counts the **lines of added test code** that violate it. Its only job is an accurate
penalty; it gets nothing from the tests passing, so it has no reason to under-count.

**reward = 0.9 ^ (penalty)**: `1.0` means nothing broken; **penalty = the total number of LINES of bad
test code**, summed across every quality rule. A rule's penalty is how many lines of *added* test code
violate it (an offending `@Test` counts its full method body; a per-line wart counts its matching lines), so
a one-line slip barely dents the reward while a 15-line warty method (0.9^15 ≈ 0.21) tanks it. The two
build-outcome rules (**green (9)** and **mutation-improving (10)**) are not line-countable, so they stay
binary (1 penalty) and act as prerequisites the PR gate requires anyway. Count only the lines **you added**
against the upstream baseline, never pre-existing upstream code. The rules:

| # | rule | broken when (penalty = offending lines) |
|---|---|---|
| 1 | api-only | reaches internals via reflection (`setAccessible`, `getDeclaredField`, `ReflectionTestUtils`, `Whitebox`); drive the public API instead |
| 2 | every-test-asserts | a `@Test` exercises code but asserts nothing (coverage theater) |
| 3 | no-vacuous-assert | `assertTrue(true)`, `assertEquals(x, x)`, `assertNotNull("literal")` |
| 4 | no-adnt-only | a `@Test` whose only check is `assertDoesNotThrow` / try-catch-`fail`; assert the real result |
| 5 | deterministic | `Thread.sleep`, unseeded `new Random()`, wall-clock, real network / file IO |
| 6 | no-disabled | adds `@Disabled` / `@Ignore` |
| 7 | no-unused-code | leaves dead code: unused import / private field / private method |
| 8 | additive-only | removes or edits any existing line (see §4); penalty = removed lines |
| 9 | green | a test does not compile or fails: **binary** |
| 10 | mutation-improving | mutant kills did not strictly rise vs the baseline: **binary** |
| 11 | no-partial-assert | validates one string **piecemeal**: ≥2 substring checks (`assertTrue(url.contains("&p=v"))`) on the same variable; assert the full value with `assertEquals` instead |
| 12 | no-trivial-accessor-test | pure getter/setter/`equals`/`hashCode`/`toString` tests: maintainers see these as noise; keep only tests with real logic (validation, exceptions, behaviour) |
| 13 | no-inner-class | declares a nested / `@Nested` / helper class inside the test; keep tests flat; lift fixtures to the public API or a top-level test helper |
| 14 | no-comment-spam | standalone comment lines out-pace code more than **1 per 4 code lines**; comment *why*, not *what*; the assertions are the documentation. Trailing `// why` on a code line is fine (it's a code line, not a comment line); penalty = comment lines over the 1:4 budget |
| 15 | no-tooling-exhaust-comments | a comment names a PIT mutant operator (`InlineConstant`, `NonVoidMethodCall`, `NO_COVERAGE`, `EQUAL_ELSE`), says "kills the surviving mutant", or hardcodes a production line number (`at line 99-105`, `on line 58`); these document the tool and rot when the source shifts. State the behaviour under test instead (e.g. "charset comes from Content-Encoding when Content-Type is absent"); penalty = offending comment lines |
| 16 | no-thin-delegator | the class under test is a trivial wrapper whose real logic lives in another class (e.g. `PathNaturalOrderComparator` just delegates to `NaturalOrderComparator`); its tests belong on the delegate. Confirm the class has real branching of its own before testing it directly: **binary** |
| 17 | repo-idiomatic | the added tests do not match the target repo's own conventions. Before writing, read the repo's CONTRIBUTING and one existing test in the same module, then follow its assertion library, structure, naming, and any given/when/then. A green compile plus checkstyle and spotless is necessary but not sufficient: assertj #4310 built clean yet was closed for missing given/when/then; penalty = lines that diverge from the module idiom |

**This is part of the §5 loop, not a final gate.** Each pass, once PIT is green, re-judge the test against
the rubric and treat every FAILED rule as more work. Fix each broken rule **without losing a kill**:
rewrite the offending test so it *still* fails on the mutant but now goes through the public API / asserts
the real value / is deterministic. **Never delete a test or weaken an assertion just to clear a rule.** If a
rule genuinely cannot be satisfied without dropping a mutant kill, keep the kill and record the residual in
the PR. **Do not stop at "tests are green"; stop at reward `1.0`** (or a documented residual you cannot
remove without losing mutation coverage).

> **Contract: no runtime dependency.** This skill is just `SKILL.md`: there is no script to run and
> nothing to install. The reward above is computed by your own line-counted judgment against the rubric, so
> it works in any environment (opencode, kilocode, CI, a bare editor) with **no Python**. The rubric is the
> contract; you score against it directly.

## 7. Equivalent mutants are RARE: earn the right to skip one
A truly **equivalent** mutant (semantically identical behaviour, no test can detect it: a branch with no
observable effect, a redundant boundary on an unreachable value, reordered commutative ops) is the *rare*
exception, not the explanation for a stuck survivor. **Most survivors are killable**: they just need the
right input or to be reached at all. Before you write a survivor off as equivalent, actually try: feed an
input that exercises the exact mutated branch/value and assert the differing output; reach an unreached
method (`NO_COVERAGE`); mock a collaborator to observe a `VoidMethodCall` side effect. Only after a genuine
differentiating attempt fails do you set it aside, and say so. Do **not** end the §5 loop on the first
`reward == 0` pass if you have not yet tried a *fresh* approach on the survivors that remain; the goal is
the **lowest survivor count you can reach**, not the first plateau. 100% is rare, but get as close as the
non-equivalent survivors allow.

## 8. Open a PR
Open a PR only once the §6 mergeability **reward is 1.0** (or the only residual is a rule you documented as
unremovable without losing a kill). Branch, commit the **append-only** test additions (plus the PIT build
config if added for JUnit 5), and open a PR whose body reports the **mutation score AND line coverage
before → after**, the additional mutants now detected, and that the additions are append-only, green, and
clear the mergeability rules. Always end the body with a short **How this was produced** disclosure: the PR
was generated by an AI-assisted pipeline based on mutation testing (PIT surfaces edge cases the existing
tests miss, a focused test is written for each surviving mutant, and PIT is rerun to confirm it kills that
mutant, so every added test is verified). Disclose the AI methodology explicitly; never omit it.

---

## Gotchas
- **Project quality gates fight mutation testing; skip them.** A `jacoco:check` coverage threshold, or
  `checkstyle` / `enforcer` / `spotless` / `pmd` / `spotbugs`, will **fail the build** when you add tests to
  one class (they judge the whole project / style, not your scoped change). On every `mvn`/PIT command pass
  **`-Djacoco.skip=true -Dcheckstyle.skip=true -Denforcer.skip=true -Dspotless.check.skip=true -Dpmd.skip=true
  -Dspotbugs.skip=true`**. A green scoped PIT run is the only gate that matters; do not chase a project
  coverage threshold.
- **JUnit 5 + PIT** needs `pitest-junit5-plugin` (§2); without it PIT reports *0 tests* / all
  `NO_COVERAGE`. That symptom means wrong wiring, not bad coverage.
- **PIT's bundled platform too old for new JUnit**: if the project is on **JUnit 5.11+ / 6.x**
  (JUnit Platform ≥ 1.11) and baseline PIT shows **0 killed / all `NO_COVERAGE`** despite a green suite,
  PIT's launcher is too old to run the tests. **Bump `pitest-maven` + `pitest-junit5-plugin` to a current
  release** (or add a matching `junit-platform-launcher` to the PIT plugin classpath). **Never downgrade the
  project's own JUnit or dependencies to fit PIT**: that breaks the build for the rest of the module. If a
  build-config edit does not compile or run, revert it (compile-then-fix).
- **PIT coverage-minion crash** (`Minion exited abnormally`) = test-instrumentation too old for the
  JDK: raise **Mockito ≥ 5.18** / **ByteBuddy ≥ 1.14.12 (17/21), ≥ 1.17.6 (25)**, or pass the
  project's `--add-opens` to the test fork. EasyMock has no JDK-25 path.
- **`NO_COVERAGE`** survivors mean the line isn't executed by the scoped tests; add a test that
  **reaches** the code first, then **asserts** on it.
- **Flaky `TIMED_OUT`** mutants flip run-to-run; compare before/after in the **same** PIT config.
- **Give every PIT/maven command a HUGE command timeout** (~1 year, e.g. `timeout=31536000`). A
  mutant-dense method under `-Dmutators=ALL` can run many minutes; a short per-command timeout cuts the
  PIT pass mid-run and looks like a failure. A command that "timed out" is usually the timeout being too
  small, not a real hang; never short-time a PIT command.
- **Keep PIT scoped** to the one class (`targetClasses`): unscoped mutation of a whole module runs for hours.
