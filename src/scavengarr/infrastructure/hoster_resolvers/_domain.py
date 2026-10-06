"""Second-level domain of a URL: the hoster's name, and a CDN's in logs."""

from __future__ import annotations

from urllib.parse import urlparse


def extract_domain(url: str) -> str:
    """Extract the second-level domain from a URL.

    Returns the second-to-last segment of the hostname (e.g.
    ``"voe"`` from ``"https://voe.sx/e/abc"``).  Handles ``www.``
    prefixes automatically since ``parts[-2]`` skips them.

    Returns ``""`` when the URL cannot be parsed or has fewer than
    two hostname segments.

    It is what logs may say about a CDN URL: path and query carry tokens
    and the client's address (``i=``).
    """
    try:
        hostname = urlparse(url).hostname or ""
        parts = hostname.split(".")
        return parts[-2] if len(parts) >= 2 else ""
    except Exception:  # noqa: BLE001
        return ""
