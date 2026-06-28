---
name: detect-unit-testing-framework
description: Determine which unit-testing framework AND version a Maven or Gradle project uses (JUnit 4, JUnit 5, JUnit 6, or TestNG), resolving property/BOM indirection, then wire PIT (or any forked test runner) to match it. Use before mutation testing, coverage, or anything that must discover and run the project's tests.
---

# Detect the test framework a project uses, and wire PIT to match it

PIT must be told how the project's tests are wired; the **wrong** wiring makes PIT find **0 tests**
(every mutant `NO_COVERAGE`) or crashes its forked minion (`Minion exited abnormally / UNKNOWN_ERROR`,
`OutputDirectoryCreator not available`). The framework's **version** matters as much as its name:
JUnit 6 needs different wiring than JUnit 5.

## 1. Detect the build tool
root `pom.xml` means Maven; `build.gradle(.kts)` + `gradlew` and no pom means Gradle. Use the wrapper (`./mvnw` / `./gradlew`).

## 2. Detect the framework
- **JUnit 5 / 6**: `org.junit.jupiter` (`junit-jupiter`, `junit-jupiter-api`) on the test classpath.
- **JUnit 4**: `junit:junit` (4.x); tests use `org.junit.Test`.
- **TestNG**: `org.testng:testng`; tests use `org.testng.annotations.Test`.
- **Vintage / mixed**: `junit-vintage-engine` = JUnit 4 tests running on the JUnit Platform; wire as
  JUnit 5/6 (the platform is present), though the test code is JUnit-4 style.

## 3. Detect the VERSION: resolve indirection, do NOT trust a naive grep
The version is often **not** a literal next to the dependency:
- a property: `<junit.jupiter.version>`, **a plain `<junit.version>`**, or `<junit-bom.version>`, with
  deps referencing it as `${...}`;
- a **BOM**: `<artifactId>junit-bom</artifactId><version>X</version>` (import scope) pins every `junit-*` to X.
- **Most reliable, ask the build for the RESOLVED version:**
  - Maven: `./mvnw -q dependency:list | grep -E "junit-platform-commons|junit-jupiter-api"`
  - Gradle: `./gradlew -q dependencies --configuration testRuntimeClasspath | grep -E "junit-platform|junit-jupiter"`
  The **`junit-platform-commons`** version IS the platform version, the one PIT must align to.

*A real case that fools grep: a project with `<junit.version>6.1.0</junit.version>` whose deps use
`${junit.version}`: naive matching misses it, mis-wires JUnit 6 as JUnit 5, and crashes the minion.*

## 4. JUnit generations (how the version maps to the platform)
- **JUnit 5**: Jupiter `5.x`, JUnit **Platform `1.x`** (offset: Jupiter `5.11` ↔ Platform `1.11`).
- **JUnit 6**: versioning **unified**: `junit-jupiter`, `junit-platform-*`, `junit-vintage` all share the
  **same** version (e.g. `6.1.0` means the platform is also `6.1.0`, not `1.x`). JUnit 6 also has a **Java 17
  baseline**: the project needs JDK ≥ 17 (coordinate with `detect-java-version`).

## 5. Wire PIT to the framework (Maven)
- **JUnit 4** → bare goal, no plugin: `org.pitest:pitest-maven:<v>:mutationCoverage`.
- **JUnit 5** → add `pitest-junit5-plugin` to the PIT plugin's `<dependencies>` (plugin **1.2.0+** with
  `pitest-maven` auto-selects a compatible `junit-platform-launcher`).
- **JUnit 6** → a **current** `pitest-maven` (≥ 1.19.4, e.g. `1.25.4`) + `pitest-junit5-plugin` `1.2.3`
  **plus** an explicit `junit-platform-launcher` pinned to the **platform version** (== the Jupiter
  version, e.g. `6.1.0`) so engine == launcher. Auto-selection does **not** cover JUnit 6's new scheme.
- **TestNG** → add **`pitest-testng-plugin`** (`org.pitest:pitest-testng-plugin:1.0.0`, needs pitest ≥ 1.9.0)
  to the PIT plugin's `<dependencies>`: current PIT **externalized** TestNG, so it is no longer built-in.

Inject the plugin into the project's **main `<build>`**, never a `<profile>` build (a plugin in an
inactive profile is silently ignored, so PIT runs with no engine). *(Gradle: apply `info.solidsoft.pitest`,
set `junit5PluginVersion`.)*

## 6. Confirm the wiring works
Run PIT scoped to one class. **0 killed / all `NO_COVERAGE`** despite a green suite, or a **minion crash**
(`UNKNOWN_ERROR` / `OutputDirectoryCreator not available`), means **wrong wiring, not weak tests**:
re-check the framework + the **platform version** above. A correct baseline shows real killed/total counts.
