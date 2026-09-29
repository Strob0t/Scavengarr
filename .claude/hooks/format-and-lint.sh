#!/bin/bash
# PostToolUse(Edit|Write) hook: ruff format + ruff check --fix on the edited Python file.
# Lint errors ruff cannot fix are reported back to Claude (exit 2 + stderr), so they
# get fixed right away instead of surfacing later in pre-commit.
# Keep this file LF-only: a CRLF shebang fails with exit 127 and the hook never runs.

FILE_PATH=$(jq -r '.tool_input.file_path // empty')
[[ $FILE_PATH == *.py && -f $FILE_PATH ]] || exit 0

cd "$CLAUDE_PROJECT_DIR" || exit 0
# Project venv; in a git worktree (no own .venv) the main checkout's venv; else PATH.
MAIN_VENV_RUFF="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)/../.venv/bin/ruff"
for RUFF in .venv/bin/ruff "$MAIN_VENV_RUFF" "$(command -v ruff)"; do
  [[ -x $RUFF ]] && break
done
[[ -x $RUFF ]] || exit 0 # no ruff available: pre-commit catches it later
unset FORCE_COLOR         # plain text for Claude: ANSI escapes only cost tokens
export NO_COLOR=1

"$RUFF" format --quiet "$FILE_PATH" >/dev/null 2>&1
if ! OUT=$("$RUFF" check --fix --quiet --output-format concise "$FILE_PATH" 2>&1); then
  echo "ruff found issues it could not fix in $FILE_PATH:" >&2
  echo "$OUT" >&2
  exit 2
fi
exit 0
