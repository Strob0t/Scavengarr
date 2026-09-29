"""Classify bot challenges and captchas in an HTTP response.

One classification for every plugin, resolver and prober, so logs and
scoring speak the same language (docs/plans/captcha-solving.md, requirement 1).

Page blocks (the whole response is a challenge) win over embedded widgets
(a normal page that contains a captcha form).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

ChallengeKind = Literal[
    "cloudflare_page",
    "ddos_guard",
    "turnstile",
    "recaptcha",
    "hcaptcha",
    "altcha",
]

_CF_PAGE_STATUSES = (403, 503)
_CF_PAGE_MARKERS: tuple[str, ...] = (
    "Just a moment",
    "challenge-platform",
    "cf-error-details",
    "Attention Required",
    "cf-turnstile",
)
# JS challenge page ("Checking your browser") and the manual captcha that
# follows a rejected client (title "DDOS-GUARD", ``?check=1``).
_DDOS_GUARD_MARKERS: tuple[str, ...] = (
    "check.ddos-guard.net",
    "/.well-known/ddos-guard/",
    "<title>ddos-guard</title>",
    "data-ddg-origin",
)
_WIDGET_MARKERS: tuple[tuple[ChallengeKind, tuple[str, ...]], ...] = (
    ("turnstile", ("cf-turnstile", "challenges.cloudflare.com/turnstile")),
    ("hcaptcha", ("h-captcha", "hcaptcha.com/1/api.js")),
    ("recaptcha", ("g-recaptcha", "google.com/recaptcha", "recaptcha.net/recaptcha")),
    ("altcha", ("<altcha-widget",)),
)


def detect_challenge(
    status_code: int,
    html: str,
    headers: Mapping[str, str] | None = None,
) -> ChallengeKind | None:
    """Return the challenge/captcha kind in a response, ``None`` if there is none."""
    lowered = html.lower()
    server = (headers or {}).get("server", "").lower()

    if status_code in _CF_PAGE_STATUSES and any(m in html for m in _CF_PAGE_MARKERS):
        return "cloudflare_page"
    if any(m in lowered for m in _DDOS_GUARD_MARKERS) or (
        server == "ddos-guard" and status_code in _CF_PAGE_STATUSES
    ):
        return "ddos_guard"
    for kind, markers in _WIDGET_MARKERS:
        if any(m in lowered for m in markers):
            return kind
    return None
