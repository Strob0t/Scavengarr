#!/bin/bash
# venv_tool.sh <tool> [args...]: a tool of the project venv. The venv is the
# active one (`poetry run pre-commit` activates Poetry's, which in CI is not
# .venv), else the project's .venv, else, in a git worktree without its own
# .venv, the main checkout's venv (the lookup of
# .claude/plugins/basedpyright-lsp/scripts/langserver.sh). pre-commit's ruff
# and basedpyright hooks run through it, so they run the versions
# pyproject.toml pins; `poetry run <tool>` would create an empty venv in a
# worktree and end with "Command not found".
# Keep this file LF-only and executable (100755).

set -euo pipefail

TOOL=${1:?usage: scripts/venv_tool.sh <tool> [args...]}
shift
VENV=${VIRTUAL_ENV:-.venv}
if [[ ! -x $VENV/bin/$TOOL ]]; then
  VENV=.venv
fi
if [[ ! -x $VENV/bin/$TOOL ]]; then
  VENV="$(git rev-parse --path-format=absolute --git-common-dir)/../.venv"
fi
if [[ ! -x $VENV/bin/$TOOL ]]; then
  echo "$TOOL not found in the project venv; run: poetry install --with dev" >&2
  exit 1
fi
VENV=$(cd "$VENV" && pwd)
# Imports resolve against the venv's interpreter, as with `poetry run <tool>`
export VIRTUAL_ENV=$VENV PATH="$VENV/bin:$PATH"
exec "$VENV/bin/$TOOL" "$@"
