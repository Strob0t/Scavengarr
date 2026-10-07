#!/bin/bash
# In a git worktree without its own .venv, link .venv to the main checkout's
# venv (the lookup of .claude/plugins/basedpyright-lsp/scripts/langserver.sh),
# so that `poetry run`, pre-commit and pytest work as in the main checkout.
# Prints what it did; an existing .venv stays untouched. The link is
# gitignored with .venv itself.
# Keep this file LF-only and executable (100755).

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
if [[ -e .venv ]]; then
  echo ".venv exists ($(readlink -f .venv)); nothing to do"
  exit 0
fi
MAIN_VENV="$(git rev-parse --path-format=absolute --git-common-dir)/../.venv"
if [[ ! -x $MAIN_VENV/bin/python ]]; then
  echo "no venv in the main checkout ($MAIN_VENV); run there: poetry install --with dev" >&2
  exit 1
fi
MAIN_VENV=$(cd "$MAIN_VENV" && pwd)
ln -s "$MAIN_VENV" .venv
echo "linked .venv -> $MAIN_VENV"
