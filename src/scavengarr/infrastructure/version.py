"""The app's version: the installed package's, set in ``pyproject.toml``.

One source for every place that reports it: the FastAPI app, the Stremio
manifest (Stremio compares it to notice addon updates), Torznab caps and the
default User-Agent. ``build_identity()`` adds the build: the commit and the
build time the image build recorded.
"""

from __future__ import annotations

import json
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

try:
    APP_VERSION = version("scavengarr")
except PackageNotFoundError:  # a source tree that was never installed
    APP_VERSION = "0.0.0"

# Keep the contact URL: Wikidata answers 403 to a User-Agent without one
APP_USER_AGENT = f"Scavengarr/{APP_VERSION} (+https://github.com/Strob0t/Scavengarr)"

# What docker/build_info.py wrote at image build time, next to the package's
# modules so it travels with them (an editable install, the image's /app/src)
BUILD_INFO_FILE = Path(__file__).resolve().parents[1] / "build_info.json"


def build_identity() -> dict[str, str]:
    """Version, commit and build time: which build runs.

    The environment (``SCAVENGARR_COMMIT``, ``SCAVENGARR_BUILT``) wins, then
    the file the image build wrote (``BUILD_INFO_FILE``); a source checkout
    has neither and reports ``unknown``. Reported by ``scavengarr_build_info``,
    the health routes, the Stremio manifest and the startup log.
    """
    recorded = _recorded_build()
    return {
        "version": APP_VERSION,
        "commit": os.environ.get("SCAVENGARR_COMMIT")
        or recorded.get("commit")
        or "unknown",
        "built": os.environ.get("SCAVENGARR_BUILT")
        or recorded.get("built")
        or "unknown",
    }


def _recorded_build() -> dict[str, str]:
    """The build ``docker/build_info.py`` recorded; ``{}`` without the file."""
    try:
        text = BUILD_INFO_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        log.warning("build_info_unreadable", path=str(BUILD_INFO_FILE), error=str(exc))
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        log.warning("build_info_unreadable", path=str(BUILD_INFO_FILE), error=str(exc))
        return {}
    if not isinstance(data, dict):
        log.warning(
            "build_info_unreadable", path=str(BUILD_INFO_FILE), error="not an object"
        )
        return {}
    return {key: str(value) for key, value in data.items() if value}
