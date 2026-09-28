#!/usr/bin/env bash

set -euo pipefail

# Git access independent of the DevPod credential tunnel (localhost:12049),
# which only lives while a DevPod/IDE connection forwards host credentials.
# GH_TOKEN and GIT_AUTHOR_NAME/GIT_AUTHOR_EMAIL come from .env.devcontainer.
echo "[devcontainer] Configuring git..."
if [ -n "${GIT_AUTHOR_NAME:-}" ] && [ -n "${GIT_AUTHOR_EMAIL:-}" ]; then
  git config --global user.name "$GIT_AUTHOR_NAME"
  git config --global user.email "$GIT_AUTHOR_EMAIL"
else
  echo "WARN: GIT_AUTHOR_NAME/GIT_AUTHOR_EMAIL not set, commits need a git identity"
fi
if [ -n "${GH_TOKEN:-}" ] && command -v gh >/dev/null 2>&1; then
  # Scoped to github.com: resets the DevPod helper there, other hosts keep it.
  gh auth setup-git --hostname github.com \
    || echo "WARN: gh auth setup-git failed, falling back to DevPod credentials"
else
  echo "WARN: GH_TOKEN or gh missing, git push relies on DevPod credential forwarding"
fi

# Node comes from the devcontainer feature `node` (version 22, via nvm, see
# devcontainer.json). Debian's apt nodejs (18) is too old: the `skills` CLI
# used for the caveman skills needs node:util.styleText (Node >= 20.12).
echo "[devcontainer] Checking Node.js..."
if ! command -v node >/dev/null 2>&1 \
  || ! node -e 'process.exit(require("node:util").styleText ? 0 : 1)'; then
  echo "[devcontainer] ERROR: Node.js >= 20.12 required, found: $(node --version 2>/dev/null || echo none)"
  echo "[devcontainer] Rebuild the container so the node feature from devcontainer.json is applied."
  exit 1
fi
echo "[devcontainer] node $(node --version), npm $(npm --version)"

# No sudo for npm -g: the global prefix is the nvm dir (group nvm, writable
# for vscode), and sudo's secure_path would not find the nvm node anyway.
echo "[devcontainer] Installing openspec globally via npm..."
npm install -g @fission-ai/openspec@latest

if ! command -v openspec >/dev/null 2>&1; then
  echo "[devcontainer] ERROR: openspec CLI not found after npm install -g @fission-ai/openspec@latest"
  exit 1
fi

# Caveman: CLI (`caveman claude`) + Claude Code skills from JuliusBrussee/caveman.
# Optional tooling, so failures only warn.
echo "[devcontainer] Installing caveman CLI + skills..."
npm install -g @caveman-ai/cli \
  || echo "WARN: caveman CLI install failed (optional)"
npx -y skills add JuliusBrussee/caveman -g -a claude-code -s '*' -y </dev/null \
  || echo "WARN: caveman skills install failed (optional)"

# Claude: native install bevorzugt
if ! command -v claude >/dev/null 2>&1; then
  echo "[devcontainer] Installing Claude Code (native)..."
  curl -sSfL https://claude.ai/install.sh | bash
  # shellcheck disable=SC2016  # literal $PATH for .bashrc
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
else
  echo "[devcontainer] Claude CLI already installed."
fi

echo "[devcontainer] Ensuring Poetry is installed..."
if ! command -v poetry >/dev/null 2>&1; then
  pipx install poetry
fi

export PATH="$HOME/.local/bin:$PATH"

echo "[devcontainer] Configuring Poetry..."
poetry config virtualenvs.in-project true

if [ -f pyproject.toml ]; then
  echo "[devcontainer] Installing Python dependencies via Poetry..."
  poetry install
fi

echo "[devcontainer] Activating virtualenv..."
# shellcheck disable=SC1091
. .venv/bin/activate

# Browser for the playwright-mcp server. Claude Code starts the server itself
# over stdio (see .mcp.json), so no container or daemon is needed here.
# Keep PLAYWRIGHT_MCP_VERSION in sync with the version pinned in .mcp.json.
# --with-deps installs the system libraries (apt via sudo) Chromium needs.
PLAYWRIGHT_MCP_VERSION="0.0.82"
echo "[devcontainer] Installing Chromium for @playwright/mcp@${PLAYWRIGHT_MCP_VERSION}..."
npx -y -p "@playwright/mcp@${PLAYWRIGHT_MCP_VERSION}" playwright install --with-deps chromium \
  || echo "WARN: Chromium install for playwright-mcp failed (optional)"

# Virtual display for headful browsers: interactive Cloudflare Turnstile
# rejects every headless browser, headful Patchright under Xvfb passes
# (see docs/plans/antibot-patchright.md). Run via `xvfb-run -a <cmd>`.
if ! command -v Xvfb >/dev/null 2>&1; then
  echo "[devcontainer] Installing Xvfb..."
  sudo apt-get update -qq \
    && sudo apt-get install -y -qq --no-install-recommends xvfb \
    || echo "WARN: Xvfb install failed (needed for headful browser tests)"
fi

echo "[devcontainer] Setup complete."
