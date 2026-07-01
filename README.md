# improve-java-tests-skill

Portable [Agent Skills](https://agentskills.io/) that make a coding agent **raise a Java
(Maven or Gradle) project's PIT mutation score**: find the code its tests *run* but don't
*verify*, and add tests that make the suite detect the surviving mutants, **without weakening any existing test**,
and under **the JDK the project actually needs**. Standard tools only (the build's PIT plugin,
`git`); no project-specific scripts, since the skills are a hand manual your agent reads and follows.

A green test suite proves nothing about whether it would catch a regression. PIT mutation testing
makes that measurable (killed / total); a line that's executed but not asserted on is unverified.
These skills turn that signal into stronger tests.

## The three skills

- **`detect-java-version`**: determine which JDK the project actually needs to *build* (not just
  its declared bytecode target, since codegen/annotation processors can require a newer JDK), and run
  every command under it. A forked test JVM (Surefire, **PIT**, JaCoCo) crashes under the wrong JDK.
- **`detect-unit-testing-framework`**: determine which unit-testing framework the project uses
  (JUnit 4, JUnit 5, TestNG) and how it is wired into the build, so new tests are written in the
  right style and the PIT run is configured correctly. `improve-java-tests` depends on this.
- **`improve-java-tests`**: run PIT scoped to one logic-dense class, read the surviving mutants,
  and add **append-only** JUnit tests that make the suite detect them by asserting the *correct* behaviour,
  keeping the additions only if every previously-passing test still passes. Includes a mutator->assertion playbook and
  the JUnit5 / test-instrumentation (Mockito, ByteBuddy, `--add-opens`) gotchas.

## Install

**Claude Code**: add this repo as a plugin marketplace, then install:
```
/plugin marketplace add vasiliy-mikhailov/improve-java-tests-skill
/plugin install improve-java-tests
```

**OpenHands**: copy the skills where it reads them:
```
mkdir -p <your-project>/.openhands/skills
cp -r skills/detect-java-version skills/detect-unit-testing-framework skills/improve-java-tests <your-project>/.openhands/skills/
```

**opencode**: drop the skills in and reference them from your `AGENTS.md`.

**Kilo Code**: copy the skills in, or install from the Kilo Marketplace.

**Any agent**: point it at the files: *"follow `skills/detect-java-version/SKILL.md`, then
`skills/detect-unit-testing-framework/SKILL.md`, then `skills/improve-java-tests/SKILL.md` to
raise this project's mutation score."*

## How it works

1. **Detect** the JDK the project needs and run under it.
2. **Pick** one logic-dense, well-covered class (high line coverage + low mutation score = richest).
3. **Measure**: run PIT scoped to that class; read the `SURVIVED`/`NO_COVERAGE` mutants.
4. **Detect**: add new `@Test` methods that assert the behaviour each survivor breaks (append-only).
5. **Confirm**: re-run PIT; keep only if the mutation score rose and the suite stayed green.
6. **Open a PR** stating the mutation score before -> after.

## How it's built

The skills are written as a self-contained procedure, refined against a corpus of real GitHub
projects and validated by running them with different coding agents on the same model, so the
instructions (not one agent's quirks) are what carry the result. The procedure and its gotcha
catalogue are hardened as new failure modes surface (v0.9.0).

## License

MIT, see [LICENSE](LICENSE).
