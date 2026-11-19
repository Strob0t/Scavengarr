---
name: test
description: Run the Scavengarr test suite (or a subset) and report a compact result. Use when asked to run tests or to check that a change is green.
argument-hint: "[path | -k pattern | -m marker]"
---

Choose the command executor: `poetry run pytest` if `command -v poetry >/dev/null 2>&1`, otherwise `.venv/bin/pytest`.

- No argument: `<executor> -n auto` (full suite on parallel workers).
- Argument that is a path: `<executor> $ARGUMENTS` (runs without `-n` as a path argument runs faster without workers).
- Argument starting with `-` or name pattern:
  - If argument starts with `-`: `<executor> $ARGUMENTS`
  - Otherwise treat it as a name pattern: `<executor> -k "$ARGUMENTS"`

Warnings:
- Do not add extra `-q` (it hides the summary line with the counts that the report needs).
- Do not use `-o addopts=...` for the full suite (it drops `-m "not live"` and runs the live tests).

Output follows `addopts` (`-q --tb=short`). Live tests are excluded unless `-m live` is passed.

Report: passed/failed/errors counts. For each failure: test id and a one-line cause. If all pass, state the count only.
