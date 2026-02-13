"""Shared Cloudflare challenge / block detection.

Centralises the markers and heuristic so that probe, supervideo,
and any future resolver can reuse the same logic.
"""

from __future__ import annotations

_CF_MARKERS: tuple[str, ...] = (
    "Just a moment",
    "challenge-platform",
    "cf-error-details",
    "Attention Required",
    "cf-turnstile",
)


def is_cloudflare_challenge(status_code: int, html: str) -> bool:
    """Return *True* when *status_code* + *html* indicate a CF challenge/block.

    Cloudflare uses several block types:
    - JS challenge: 503 + "Just a moment" / "challenge-platform"
    - WAF block:    403 + "Attention Required" / "cf-error-details"
    - Turnstile:    403/503 + "cf-turnstile"
    """
    if status_code not in (403, 503):
        return False
    return any(marker in html for marker in _CF_MARKERS)
