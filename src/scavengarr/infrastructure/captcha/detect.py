"""Classify bot challenges and captchas in an HTTP response.

One classification for every plugin, resolver and prober, so logs and
scoring speak the same language (docs/plans/captcha-solving.md, requirement 1).

Page blocks (the whole response is a challenge) win over embedded widgets
(a normal page that contains a captcha form). In a browser, the frames a
captcha provider shows its challenge in name the kind (``challenge_frame``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal
from urllib.parse import parse_qs, urlsplit

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
# The hosts of the providers' challenge frames: Cloudflare's challenge
# platform (the Turnstile widget, the interstitial's check) and hCaptcha's;
# reCAPTCHA's frames live under /recaptcha/ on these hosts
_FRAME_HOSTS: tuple[tuple[ChallengeKind, str], ...] = (
    ("turnstile", "challenges.cloudflare.com"),
    ("hcaptcha", "hcaptcha.com"),
)
_RECAPTCHA_HOSTS = frozenset(
    {"google.com", "www.google.com", "recaptcha.net", "www.recaptcha.net"}
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


def challenge_frame(url: str) -> ChallengeKind | None:
    """The captcha provider whose challenge frame *url* loads, ``None`` for
    any other URL. An invisible reCAPTCHA's frame (v3's score) shows nothing
    to pass and counts as none."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    for kind, frame_host in _FRAME_HOSTS:
        if host == frame_host or host.endswith(f".{frame_host}"):
            return kind
    if host in _RECAPTCHA_HOSTS and parts.path.startswith("/recaptcha/"):
        if parse_qs(parts.query).get("size") == ["invisible"]:
            return None
        return "recaptcha"
    return None


def detect_challenge_headers(
    status_code: int, headers: Mapping[str, str]
) -> ChallengeKind | None:
    """The challenge kind of an answer without a body (a HEAD check):
    Cloudflare's ``cf-mitigated: challenge``, or a 403/503 from behind
    Cloudflare (``cf-ray``); else what the headers alone tell
    ``detect_challenge`` (DDoS-Guard's ``Server``). The health prober's
    and the httpx domain check's rule."""
    if headers.get("cf-mitigated", "").lower() == "challenge" or (
        status_code in _CF_PAGE_STATUSES and "cf-ray" in headers
    ):
        return "cloudflare_page"
    return detect_challenge(status_code, "", headers)
