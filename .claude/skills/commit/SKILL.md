---
name: commit
description: Commit and push the current changes following the Scavengarr workflow (pre-commit, pytest, Conventional Commits, push to staging). Use when asked to commit, or after finishing an isolated subtask.
argument-hint: "[commit message]"
---

Commit message: $ARGUMENTS (if empty, derive one from the diff).

1. `poetry run pre-commit run --all-files`: fix every error, re-run until clean.
2. `poetry run pytest`: all tests must pass. On failure fix and restart at step 1.
3. Docs ship with the code: if behavior, features, architecture or config changed, update `CHANGELOG.md` and the matching `docs/` pages (plus `AGENTS.md`/`README.md` if affected) in this commit.
4. Stage only files belonging to this change, then commit with a Conventional Commits message: `<type>(<scope>): <subject>`, lower-case subject, no trailing period.
5. Push the current branch (`git push origin staging` on `staging`).

Never commit to or push `main`. One logical change per commit.
