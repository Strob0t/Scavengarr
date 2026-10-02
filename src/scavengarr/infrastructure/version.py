"""The app's version: the installed package's, set in ``pyproject.toml``.

One source for every place that reports it: the FastAPI app, the Stremio
manifest (Stremio compares it to notice addon updates), Torznab caps and the
default User-Agent.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    APP_VERSION = version("scavengarr")
except PackageNotFoundError:  # a source tree that was never installed
    APP_VERSION = "0.0.0"

# Keep the contact URL: Wikidata answers 403 to a User-Agent without one
APP_USER_AGENT = f"Scavengarr/{APP_VERSION} (+https://github.com/Strob0t/Scavengarr)"
