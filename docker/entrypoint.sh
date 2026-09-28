#!/bin/sh
# Container entrypoint: start a virtual display for the headful browser
# (Cloudflare Turnstile rejects headless Chromium, see
# docs/plans/antibot-patchright.md), then exec the app so it stays the
# signal recipient (graceful shutdown on SIGTERM). Not xvfb-run: that
# wrapper does not forward SIGTERM to the app.
set -e

if [ -z "${DISPLAY:-}" ] && command -v Xvfb >/dev/null 2>&1; then
  Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &
  export DISPLAY=:99
fi

exec python -m scavengarr.interfaces.cli "$@"
