#!/usr/bin/env bash

set -euo pipefail

mkdir -p "$HOME/.docker"

# Wenn config.json existiert, credsStore entfernen
if [ -f "$HOME/.docker/config.json" ]; then
  python - <<'PY'
import json, pathlib
p = pathlib.Path.home() / ".docker" / "config.json"
data = json.loads(p.read_text() or "{}")
data.pop("credsStore", None)
p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
PY
else
  # Minimal config ohne credsStore
  cat > "$HOME/.docker/config.json" <<'JSON'
{
  "auths": {}
}
JSON
fi

echo "[devcontainer] Checking for npm..."

apt_update() {
  echo "[devcontainer] Running apt-get update..."
  if sudo apt-get update; then
    return 0
  fi

  echo "[devcontainer] apt-get update failed, trying to disable Yarn APT repo..."

  # Yarn-Repo mit kaputtem GPG-Key deaktivieren
  if [ -d /etc/apt/sources.list.d ]; then
    for f in /etc/apt/sources.list.d/*.list; do
      if [ -f "$f" ] && grep -q "dl.yarnpkg.com/debian" "$f"; then
        echo "[devcontainer] Disabling Yarn repo in $f"
        sudo sed -i 's|^deb |# deb |' "$f"
      fi
    done
  fi

  echo "[devcontainer] Retrying apt-get update without Yarn repo..."
  sudo apt-get update
}

if ! command -v npm >/dev/null 2>&1; then
  echo "[devcontainer] npm not found, installing nodejs + npm..."

  apt_update

  # In Debian Bookworm heißt das Paket nodejs, npm ist separat
  sudo apt-get install -y --no-install-recommends nodejs npm
fi

echo "[devcontainer] npm version: $(npm --version)"

echo "[devcontainer] Installing openspec globally via npm..."
sudo npm install -g @fission-ai/openspec@latest

if ! command -v openspec >/dev/null 2>&1; then
  echo "[devcontainer] ERROR: openspec CLI not found after npm install -g @fission-ai/openspec@latest"
  exit 1
fi

echo "[devcontainer] Installing Claude Code CLI globally via npm..."
npm install -g @anthropic-ai/claude-code@latest

if ! command -v claude >/dev/null 2>&1; then
  echo "[devcontainer] ERROR: claude CLI not found after npm install -g @anthropic-ai/claude-code@latest"
  exit 1
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

echo "[devcontainer] Starting MCP docker stack..."
docker compose --env-file ./.env.devcontainer up -d
docker compose ps

echo "[devcontainer] Setup complete."
