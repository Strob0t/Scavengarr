#!/bin/sh
# Container entrypoint: start a virtual display for the headful browser
# (Cloudflare Turnstile rejects headless Chromium, see
# docs/plans/antibot-patchright.md), then exec the app so it stays the
# signal recipient (graceful shutdown on SIGTERM). Not xvfb-run: that
# wrapper does not forward SIGTERM to the app.
set -e

# Seed the config on the first start: a volume over /app/config hides the
# image's files, and without a config file the app runs on built-in defaults.
# The default ships beside this script; a config already there stays.
config="${SCAVENGARR_CONFIG:-}"
default="$(dirname "$0")/config.default.yaml"
if [ -n "$config" ] && [ ! -e "$config" ] && [ -f "$default" ]; then
  if mkdir -p "$(dirname "$config")" 2>/dev/null && cp "$default" "$config" 2>/dev/null; then
    echo "entrypoint: wrote the default config to $config" >&2
  else
    echo "entrypoint: cannot write $config (user $(id -u)); the app runs on its defaults" >&2
  fi
fi

if [ -z "${DISPLAY:-}" ] && command -v Xvfb >/dev/null 2>&1; then
  # A restarted container keeps /tmp: the lock of the Xvfb killed with the
  # previous app would make the new Xvfb refuse to start ("Server is
  # already active for display 99") and leave every browser without a display
  rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
  Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &
  # Wait up to 5 s for the display socket so the first browser launch finds it
  i=0
  while [ ! -S /tmp/.X11-unix/X99 ] && [ "$i" -lt 50 ]; do
    sleep 0.1
    i=$((i + 1))
  done
  export DISPLAY=:99
fi

exec python -m scavengarr.interfaces.cli "$@"
