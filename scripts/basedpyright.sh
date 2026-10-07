#!/bin/bash
# basedpyright from the active venv (`poetry run pre-commit` activates Poetry's,
# which in CI is not .venv), else from the project's .venv, else, in a git
# worktree without its own .venv, from the main checkout's venv (the lookup of
# .claude/plugins/basedpyright-lsp/scripts/langserver.sh). pre-commit's
# basedpyright hook runs this instead of `poetry run basedpyright`, which in a
# worktree creates an empty venv and ends with "Command not found".
# Keep this file LF-only and executable (100755).

set -euo pipefail

VENV=${VIRTUAL_ENV:-.venv}
if [[ ! -x $VENV/bin/basedpyright ]]; then
  VENV=.venv
fi
if [[ ! -x $VENV/bin/basedpyright ]]; then
  VENV="$(git rev-parse --path-format=absolute --git-common-dir)/../.venv"
fi
if [[ ! -x $VENV/bin/basedpyright ]]; then
  echo "basedpyright not found in the project venv; run: poetry install --with dev" >&2
  exit 1
fi
VENV=$(cd "$VENV" && pwd)
# Imports resolve against the venv's interpreter, as with `poetry run basedpyright`
export VIRTUAL_ENV=$VENV PATH="$VENV/bin:$PATH"
exec "$VENV/bin/basedpyright" "$@"
