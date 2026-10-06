"""The app's version: the installed package's, set in ``pyproject.toml``.

One source for every place that reports it: the FastAPI app, the Stremio
manifest (Stremio compares it to notice addon updates), Torznab caps and the
default User-Agent. ``build_identity()`` adds the build: the commit and the
build time the image build passes in.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version

try:
    APP_VERSION = version("scavengarr")
except PackageNotFoundError:  # a source tree that was never installed
    APP_VERSION = "0.0.0"

# Keep the contact URL: Wikidata answers 403 to a User-Agent without one
APP_USER_AGENT = f"Scavengarr/{APP_VERSION} (+https://github.com/Strob0t/Scavengarr)"


def build_identity() -> dict[str, str]:
    """Version, commit and build time: which build runs.

    ``Dockerfile.prod`` turns the build arguments ``SCAVENGARR_COMMIT`` and
    ``SCAVENGARR_BUILT`` into environment variables; without them (a source
    checkout, a build that passed none) both are ``unknown``. Reported by
    ``scavengarr_build_info``, the health routes and the startup log.
    """
    return {
        "version": APP_VERSION,
        "commit": os.environ.get("SCAVENGARR_COMMIT") or "unknown",
        "built": os.environ.get("SCAVENGARR_BUILT") or "unknown",
    }
