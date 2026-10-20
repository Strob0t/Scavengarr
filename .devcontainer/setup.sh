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
# Optional tooling, so failures only warn. Only the locally useful skills: every
# installed skill description costs context tokens in every session. Left out:
# the Caveman Cloud skills (discover, evidence-review, learn, manage, optimize,
# setup) and caveman-commit, which collides with the project `commit` skill.
SKILLS_CLI="skills@1.7.2"
CAVEMAN_SKILLS=(caveman cavecrew caveman-explore caveman-review caveman-compress caveman-help caveman-stats)
echo "[devcontainer] Installing caveman CLI + skills..."
npm install -g @caveman-ai/cli \
  || echo "WARN: caveman CLI install failed (optional)"
npx -y "$SKILLS_CLI" add JuliusBrussee/caveman -g -a claude-code -s "${CAVEMAN_SKILLS[@]}" -y </dev/null \
  || echo "WARN: caveman skills install failed (optional)"

# The skill set of docs/agents/skills.md: one `skills add` per source repository,
# non-interactive, with the pinned CLI. Optional tooling, so failures only warn.
SKILL_SOURCES=(
  "obra/superpowers brainstorming writing-plans test-driven-development executing-plans subagent-driven-development dispatching-parallel-agents systematic-debugging verification-before-completion requesting-code-review receiving-code-review"
  "mattpocock/skills handoff writing-for-agents grill-me domain-modeling research codebase-design"
  "pwguler/skills recall reflect rubric"
  "DietrichGebert/ponytail ponytail ponytail-review ponytail-audit ponytail-debt"
  "blader/humanizer humanizer"
  "addyosmani/agent-skills spec-driven-development documentation-and-adrs api-and-interface-design incremental-implementation code-simplification observability-and-instrumentation performance-optimization ci-cd-and-automation"
  "pbakaus/impeccable impeccable"
  "cathrynlavery/diagram-design diagram-design"
  "cloudflare/security-audit-skill security-audit"
)
echo "[devcontainer] Installing the skill set of docs/agents/skills.md..."
for source in "${SKILL_SOURCES[@]}"; do
  read -r -a parts <<< "$source"
  npx -y "$SKILLS_CLI" add "${parts[0]}" -g -y -a claude-code -s "${parts[@]:1}" </dev/null \
    || echo "WARN: skills install from ${parts[0]} failed (optional)"
done
# Skills taken out of the set (docs/agents/skills.md, Removed): an attach removes
# the copy an earlier one installed, so its description stops costing context
RETIRED_SKILLS=(code-review)
installed_retired=()
for skill in "${RETIRED_SKILLS[@]}"; do
  [ -d "$HOME/.claude/skills/$skill" ] && installed_retired+=("$skill")
done
if [ "${#installed_retired[@]}" -gt 0 ]; then
  npx -y "$SKILLS_CLI" remove -g -y -a claude-code -s "${installed_retired[@]}" </dev/null \
    || echo "WARN: removing retired skills failed (optional)"
fi

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

# graphify (Graphify-Labs/graphify) ships its own installer: the `/graphify` skill plus
# a three-line user-level ~/.claude/CLAUDE.md (docs/agents/skills.md). User-wide: its
# --project option writes a CLAUDE.md into the repository root and a hook into
# .claude/settings.json. Optional, warns only.
echo "[devcontainer] Installing graphify..."
pipx install graphifyy >/dev/null 2>&1 \
  || echo "WARN: graphify install failed (optional)"
if command -v graphify >/dev/null 2>&1; then
  graphify install --platform claude </dev/null \
    || echo "WARN: graphify install failed (optional)"
fi

# Serena (oraios/serena), the code-intelligence MCP server of .mcp.json: uv runs
# it (uv tool install fetches the Python it asks for), and basedpyright's language
# server is fetched ahead of the first session. Pinned: bump SERENA_VERSION together
# with docs/agents/mcp-servers.md. Optional, warns only; safe to repeat.
SERENA_VERSION="2.0.0"
echo "[devcontainer] Installing Serena ${SERENA_VERSION}..."
pipx install uv >/dev/null 2>&1 || pipx upgrade uv >/dev/null 2>&1 \
  || echo "WARN: uv install failed (optional)"
if command -v uv >/dev/null 2>&1; then
  uv tool install -p 3.13 "serena-agent==${SERENA_VERSION}" </dev/null >/dev/null 2>&1 \
    && serena init </dev/null >/dev/null 2>&1 \
    && serena download-ls-dependencies python_basedpyright </dev/null >/dev/null 2>&1 \
    || echo "WARN: Serena install failed (optional)"
fi

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
# (see docs/current/browser-and-challenges.md). Run via `xvfb-run -a <cmd>`, which
# needs xauth (only a recommendation of the xvfb package).
if ! command -v Xvfb >/dev/null 2>&1 || ! command -v xauth >/dev/null 2>&1; then
  echo "[devcontainer] Installing Xvfb..."
  sudo apt-get update -qq \
    && sudo apt-get install -y -qq --no-install-recommends xvfb xauth \
    || echo "WARN: Xvfb install failed (needed for headful browser tests)"
fi

# Chromium for the app itself (Patchright, Python venv). Separate from the
# playwright-mcp browser above. --with-deps here as well: the app browser must
# not depend on the optional playwright-mcp step for its system libraries
# (libatk etc.); Patchright ships its own Node driver, so the system Node
# version does not matter.
echo "[devcontainer] Installing Patchright Chromium for the venv..."
python -m patchright install --with-deps chromium \
  || echo "WARN: Patchright Chromium install failed (Playwright plugins, live tests)"

echo "[devcontainer] Setup complete."
