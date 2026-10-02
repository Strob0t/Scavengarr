"""Tests for the shared captcha/challenge detector."""

from __future__ import annotations

import pytest

from scavengarr.infrastructure.browser.cloudflare import is_cloudflare_challenge
from scavengarr.infrastructure.captcha.detect import detect_challenge

_CF_JS = (
    "<html><head><title>Just a moment...</title></head><body>"
    "<script src='/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1'>"
    "</script></body></html>"
)
_CF_WAF = "<title>Attention Required! | Cloudflare</title><div id='cf-error-details'>"
_DDOS_GUARD = (
    "<!doctype html><html><head><title>DDoS-Guard</title>"
    '<script src="https://check.ddos-guard.net/check.js"></script></head>'
    '<body data-ddg-origin="true"><h1 id="ddg-l10n-title">Checking your browser'
    "</h1></body></html>"
)
_DDOS_GUARD_MANUAL = (
    "<html><head><title>DDOS-GUARD</title></head><body>"
    "<form action='?check=1'><div class='ddg-captcha'></div></form></body></html>"
)
_TURNSTILE = (
    "<form><div class='cf-turnstile' data-sitekey='0x4AAA'></div></form>"
    "<script src='https://challenges.cloudflare.com/turnstile/v0/api.js'></script>"
)
_RECAPTCHA = (
    "<script src='https://www.google.com/recaptcha/api.js'></script>"
    "<div class='g-recaptcha' data-sitekey='6Le'></div>"
)
_HCAPTCHA = (
    "<script src='https://js.hcaptcha.com/1/api.js'></script>"
    "<div class='h-captcha' data-sitekey='abc'></div>"
)
_ALTCHA = '<altcha-widget challenge="/api/captcha/challenge"></altcha-widget>'


class TestDetectChallenge:
    @pytest.mark.parametrize(
        ("status", "html", "expected"),
        [
            (503, _CF_JS, "cloudflare_page"),
            (403, _CF_WAF, "cloudflare_page"),
            (403, _TURNSTILE, "cloudflare_page"),  # managed challenge page
            (403, _DDOS_GUARD, "ddos_guard"),
            (200, _DDOS_GUARD_MANUAL, "ddos_guard"),
            (200, _TURNSTILE, "turnstile"),
            (200, _RECAPTCHA, "recaptcha"),
            (200, _HCAPTCHA, "hcaptcha"),
            (200, _ALTCHA, "altcha"),
            (200, "<html><body>Iron Man</body></html>", None),
            (404, "<html>Not found</html>", None),
            (200, "", None),
        ],
    )
    def test_kinds(self, status: int, html: str, expected: str | None) -> None:
        assert detect_challenge(status, html) == expected

    def test_ddos_guard_server_header_without_body(self) -> None:
        assert detect_challenge(403, "", {"server": "ddos-guard"}) == "ddos_guard"

    def test_cloudflare_ok_page_is_not_a_challenge(self) -> None:
        # a normal page served through Cloudflare mentions cdn-cgi scripts
        html = "<script src='/cdn-cgi/scripts/rocket-loader.min.js'></script>"
        assert detect_challenge(200, html, {"server": "cloudflare"}) is None


class TestIsCloudflareChallenge:
    """The legacy helper keeps its semantics on top of the detector."""

    @pytest.mark.parametrize(
        ("status", "html", "expected"),
        [
            (503, _CF_JS, True),
            (403, _CF_WAF, True),
            (403, _TURNSTILE, True),
            (200, _CF_JS, False),
            (403, _DDOS_GUARD, False),
            (403, "Forbidden", False),
        ],
    )
    def test_semantics(self, status: int, html: str, expected: bool) -> None:
        assert is_cloudflare_challenge(status, html) is expected
