#!/bin/bash
# basedpyright's language server from the project venv; in a git worktree (no own
# .venv) the main checkout's venv, as in .claude/hooks/format-and-lint.sh.
# stdout carries the LSP protocol: print nothing else there.
# Keep this file LF-only and executable (100755), or the server never starts.

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 1
VENV=.venv
if [[ ! -x $VENV/bin/basedpyright-langserver ]]; then
  VENV="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)/../.venv"
fi
if [[ ! -x $VENV/bin/basedpyright-langserver ]]; then
  echo "basedpyright-langserver not found in the project venv; run: poetry install --with dev" >&2
  exit 1
fi
VENV=$(cd "$VENV" && pwd)
# Imports resolve against the venv's interpreter, as with `poetry run basedpyright`.
export VIRTUAL_ENV=$VENV PATH="$VENV/bin:$PATH"
exec "$VENV/bin/basedpyright-langserver" "$@"
