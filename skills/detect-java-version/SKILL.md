---
name: detect-java-version
description: Determine which Java/JDK version a Maven or Gradle project actually needs to BUILD (not merely its declared bytecode target), then select and use that JDK so builds, tests, and analysis tools run in a faithfully reproducing environment. Use before compiling, running tests, mutation testing, coverage, or any tooling that forks a JVM and must match the project's real Java version.
---

# Detect the JDK a project needs to build, and run under it

Tooling that forks a test JVM (Surefire, **PIT**, JaCoCo) crashes with cryptic errors when run under
the wrong JDK. The project's **declared** Java target is only a starting guess: the real build floor
can be higher. Verify by compiling, then run everything under the JDK that actually works.

## 1. Detect the build tool
- root **`pom.xml`** → **Maven**; **`build.gradle`/`.kts` + `gradlew`** and no `pom.xml` → **Gradle**.
- **Always use the repo's wrapper** when present: `./mvnw`, and for Gradle **always** the repo's
  `./gradlew` (never a system `gradle`).

## 2. Read the DECLARED target (a guess, not the floor)
- **Maven:** `<maven.compiler.release>`, `<maven.compiler.target>`/`<maven.compiler.source>`,
  `<release>` in `maven-compiler-plugin`, the `<java.version>` property (and any parent/BOM),
  `maven-toolchains-plugin`.
- **Gradle:** `sourceCompatibility`/`targetCompatibility`, `java { toolchain { languageVersion =
  JavaLanguageVersion.of(N) } }`, `JavaVersion.VERSION_N`.
- Normalize the legacy scheme: `1.8` → **8**. Take the **highest** declared across all modules.

## 3. The critical nuance: declared target ≠ build floor
A project whose toolchain says **8** can still need **JDK 11+ to build**: codegen and annotation
processors (ANTLR, Lombok, MapStruct, protoc) and newer build plugins often require a newer JDK than
the bytecode they emit. **Trust what actually compiles, not the declared number.**

## 4. Pick the JDK and VERIFY it
Start at the nearest LTS **≥** the declared target, one of **8, 11, 17, 21, 25**. Verify it compiles:
- Maven: `JAVA_HOME=<jdk> ./mvnw -q -ntp -DskipTests test-compile`
- Gradle: `JAVA_HOME=<jdk> ./gradlew testClasses`

If compilation fails because the toolchain/plugins demand newer, **step up to the next LTS and retry**.
The JDK that cleanly compiles **and** runs the baseline tests green is the one to use for everything
after: building, testing, and any forked-JVM tooling (PIT etc.).

## 5. Gradle wrapper floors (a too-old wrapper won't start on a newer JDK)
JDK **11**→Gradle ≥**5.0**, **17**→≥**7.3**, **21**→≥**8.5**, **25**→≥**9.0**. Signatures
`Could not determine java version from '<v>'` or `Unsupported class file major version` inside
`_BuildScript_` mean the wrapper is too old → `./gradlew wrapper --gradle-version <X>` (run under the
old JDK first if the current wrapper can't start on the new one).

## 6. Run under the detected JDK
Run **every** build/test/analysis command under the chosen JDK: via `JAVA_HOME` or by executing in
the matching JDK container/image. Report the detected version so downstream steps reproduce the same
environment.
