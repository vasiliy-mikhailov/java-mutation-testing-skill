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

**The reward you maximize: +1 for every mutant your new tests make the suite detect** (`killed_after − killed_before`). Drive it with the loop in §5 — each pass adds tests and re-scores; keep going while the reward stays positive.

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
- **Detect the test framework:** JUnit 5 (`org.junit.jupiter`) vs JUnit 4 vs TestNG — it changes how
  PIT is wired (§2) and how you write the new tests.
- `git` — commit a baseline first so your additions are an isolated diff.

## 1. Pick one target class
- **Coverage first** — a class with **high line coverage but low mutation score** is the richest
  target: the tests run it but don't assert on it.
- Otherwise pick a **logic-dense** class (branches, arithmetic, parsing, state) that has an existing
  `FooTest`. Skip trivial getters/DTOs.

Let `C` = fully-qualified class, `T` = its test class or package glob.

## 2. Measure the baseline — run PIT scoped to that one class
**Maven, JUnit 4** — invoke PIT as a one-off goal, no `pom.xml` change needed:
```bash
./mvnw -B -DskipTests test-compile
./mvnw -B org.pitest:pitest-maven:1.15.2:mutationCoverage \
  -DtargetClasses=C -DtargetTests=T -DoutputFormats=XML,HTML -DtimestampedReports=false
```
**Maven, JUnit 5** — add the `pitest-junit5-plugin` to the PIT plugin in `pom.xml` (a CLI `-D` can't
add a plugin dependency):
```xml
<plugin><groupId>org.pitest</groupId><artifactId>pitest-maven</artifactId><version>1.15.2</version>
  <dependencies><dependency><groupId>org.pitest</groupId>
    <artifactId>pitest-junit5-plugin</artifactId><version>1.2.1</version></dependency></dependencies>
</plugin>
```
**Gradle** — apply `info.solidsoft.pitest` (+ `junit5PluginVersion` for JUnit 5), scope it, `./gradlew pitest`.

Report lands at `target/pit-reports/mutations.xml` (Maven) or `build/reports/pitest/mutations.xml`.

## 3. Read the surviving mutants
In `mutations.xml`, the mutants to address are `status="SURVIVED"` and `status="NO_COVERAGE"`. For
each, read `<lineNumber>`, `<mutator>`, `<mutatedMethod>`, `<description>`. Mutation score =
detected / total.

## 4. Strengthen the suite to catch each survivor — append-only
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
  reward = killed_now - killed_prev      # +1 for each newly-detected mutant this pass
  if PIT is red (a new test failed)  -> fix or drop that test; never leave the suite red
  if reward == 0 on this pass        -> STOP (only equivalent / untestable survivors remain — see §6)
  else                               -> read the still-SURVIVED mutants (§3), add tests (§4), continue
```

**Keep the additions only if** PIT runs clean (all tests green) and the mutation score rose. Each pass
re-reads the *fresh* report, so you always target the survivors that still remain. Stop at the plateau —
the first full pass that adds **zero** new detections (reward 0).

## 6. Don't chase equivalent mutants
Some survivors are **equivalent** — the mutation produces semantically identical behaviour, so **no**
test can detect them (a mutated branch with no observable effect, a redundant boundary on an
unreachable value, reordered commutative ops). Recognize the pattern and **move on**. A class rarely
reaches 100%; stop when the remaining survivors are equivalent or genuinely untestable.

## 7. Open a PR
Branch, commit the **append-only** test additions (plus the PIT build config if added for JUnit 5),
and open a PR whose body reports the **mutation score before → after**, the additional mutants now
detected, and that the additions are append-only and green.

---

## Gotchas
- **JUnit 5 + PIT** needs `pitest-junit5-plugin` (§2); without it PIT reports *0 tests* / all
  `NO_COVERAGE`. That symptom means wrong wiring, not bad coverage.
- **PIT coverage-minion crash** (`Minion exited abnormally`) = test-instrumentation too old for the
  JDK: raise **Mockito ≥ 5.18** / **ByteBuddy ≥ 1.14.12 (17/21), ≥ 1.17.6 (25)**, or pass the
  project's `--add-opens` to the test fork. EasyMock has no JDK-25 path.
- **`NO_COVERAGE`** survivors mean the line isn't executed by the scoped tests — add a test that
  **reaches** the code first, then **asserts** on it.
- **Flaky `TIMED_OUT`** mutants flip run-to-run; compare before/after in the **same** PIT config.
- **Keep PIT scoped** to the one class (`targetClasses`) — unscoped mutation of a whole module runs for hours.
