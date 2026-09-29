"""Cloudflare challenge / block detection (thin view on the shared detector)."""

from __future__ import annotations

from scavengarr.infrastructure.captcha.detect import detect_challenge


def is_cloudflare_challenge(status_code: int, html: str) -> bool:
    """Return *True* when *status_code* + *html* indicate a CF challenge/block.

    Cloudflare uses several block types:
    - JS challenge: 503 + "Just a moment" / "challenge-platform"
    - WAF block:    403 + "Attention Required" / "cf-error-details"
    - Turnstile:    403/503 + "cf-turnstile"
    """
    return detect_challenge(status_code, html) == "cloudflare_page"
