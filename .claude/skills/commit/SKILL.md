---
name: commit
description: Commit and push the current changes following the Scavengarr workflow (pre-commit, pytest, Conventional Commits, push to staging). Use when asked to commit, or after finishing an isolated subtask.
argument-hint: "[commit message]"
---

Commit message: $ARGUMENTS (if empty, derive one from the diff).

1. Before running gates, `git add` any new files (pre-commit skips untracked files).
2. Run pre-commit: if `command -v poetry >/dev/null 2>&1`, use `poetry run pre-commit run --all-files`, otherwise `.venv/bin/pre-commit run --all-files`. If pre-commit fails to fetch its hook repo (exit 3 through the proxy), report the exit code and run `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .` and `.venv/bin/basedpyright` directly (note: `core.hooksPath` runs no hook here, so this skill is the only gate). Fix every error, re-run until clean.
3. Run tests: if `command -v poetry >/dev/null 2>&1`, use `poetry run pytest -n auto`, otherwise `.venv/bin/pytest -n auto`. All tests must pass. On failure fix and restart at step 1.
4. Docs ship with the code: if behavior, features, architecture or config changed, update `CHANGELOG.md` and the matching `docs/` pages (plus `AGENTS.md`/`README.md` if affected) in this commit.
5. Stage files belonging to this change, then commit with a Conventional Commits message: `<type>(<scope>): <subject>`, lower-case subject, no trailing period.
6. Push to staging: `git fetch origin && git rebase origin/staging`, then `git push origin HEAD:staging`.
7. Watch CI: get the run id via `gh run list --branch staging --limit 1 --json databaseId -q '.[0].databaseId'`, then `gh run watch <run-id> --exit-status`. A red run is fixed forward before the next change.

Never commit to or push `main`. One logical change per commit.
