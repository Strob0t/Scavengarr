---
name: test
description: Run the Scavengarr test suite (or a subset) and report a compact result. Use when asked to run tests or to check that a change is green.
argument-hint: "[path | -k pattern | -m marker]"
---

- No argument: `poetry run pytest -n auto` (full suite on parallel workers).
- Argument that is a path or starts with `-`: `poetry run pytest $ARGUMENTS`.
- Otherwise treat it as a name pattern: `poetry run pytest -k "$ARGUMENTS"`.

Output is already compact (`-q --tb=short` via `addopts`); don't add `-v`. Live tests are excluded unless `-m live` is passed.

Report: passed/failed/errors counts. For each failure: test id and a one-line cause. If all pass, state the count only.
