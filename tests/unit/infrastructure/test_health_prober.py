"""Unit tests for HealthProber."""

from __future__ import annotations

import httpx
import respx

from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT
from scavengarr.infrastructure.scoring.health_prober import HealthProber

_URL = "https://example.com"


class TestProbe:
    @respx.mock
    async def test_reachable_returns_ok(self) -> None:
        respx.head(_URL).respond(200)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is True
        assert result.http_status == 200
        assert result.duration_ms > 0
        assert result.error_kind is None

    @respx.mock
    async def test_404_returns_not_ok(self) -> None:
        respx.head(_URL).respond(404)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is False
        assert result.http_status == 404

    @respx.mock
    async def test_500_returns_not_ok(self) -> None:
        respx.head(_URL).respond(500)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is False
        assert result.http_status == 500

    @respx.mock
    async def test_405_falls_back_to_get(self) -> None:
        respx.head(_URL).respond(405)
        respx.get(_URL).respond(200)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is True
        assert result.http_status == 200

    @respx.mock
    async def test_501_falls_back_to_get(self) -> None:
        respx.head(_URL).respond(501)
        respx.get(_URL).respond(200)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is True
        assert result.http_status == 200

    @respx.mock
    async def test_timeout_returns_error(self) -> None:
        respx.head(_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is False
        assert result.error_kind == "timeout"
        assert result.http_status is None

    @respx.mock
    async def test_network_error_returns_error(self) -> None:
        respx.head(_URL).mock(side_effect=httpx.ConnectError("refused"))
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is False
        assert result.error_kind == "http_error"

    @respx.mock
    async def test_started_at_is_set(self) -> None:
        respx.head(_URL).respond(200)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.started_at is not None
        assert result.started_at.tzinfo is not None


class TestCaptchaDetection:
    @respx.mock
    async def test_head_403_with_cf_ray_detects_captcha(self) -> None:
        respx.head(_URL).respond(403, headers={"cf-ray": "abc123"})
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.captcha_detected is True
        assert result.ok is False
        assert result.error_kind == "captcha"
        assert result.http_status == 403

    @respx.mock
    async def test_head_503_with_cf_ray_detects_captcha(self) -> None:
        respx.head(_URL).respond(503, headers={"cf-ray": "def456"})
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.captcha_detected is True
        assert result.ok is False
        assert result.error_kind == "captcha"

    @respx.mock
    async def test_head_403_without_cf_ray_is_not_captcha(self) -> None:
        respx.head(_URL).respond(403)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.captcha_detected is False
        assert result.ok is False
        assert result.error_kind is None

    @respx.mock
    async def test_get_fallback_with_cf_body_detects_captcha(self) -> None:
        cf_html = "<html><title>Just a moment</title></html>"
        respx.head(_URL).respond(405)
        respx.get(_URL).respond(503, text=cf_html)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.captcha_detected is True
        assert result.ok is False
        assert result.error_kind == "captcha"
        assert result.http_status == 503

    @respx.mock
    async def test_get_fallback_normal_page_not_captcha(self) -> None:
        respx.head(_URL).respond(501)
        respx.get(_URL).respond(200, text="<html>OK</html>")
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.captcha_detected is False
        assert result.ok is True
        assert result.error_kind is None

    @respx.mock
    async def test_head_200_with_cf_ray_is_not_captcha(self) -> None:
        """A 200 with cf-ray is fine — CF proxies all traffic."""
        respx.head(_URL).respond(200, headers={"cf-ray": "ok123"})
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.captcha_detected is False
        assert result.ok is True

    @respx.mock
    async def test_head_403_from_ddos_guard_detects_captcha(self) -> None:
        respx.head(_URL).respond(403, headers={"server": "ddos-guard"})
        async with httpx.AsyncClient() as client:
            result = await HealthProber(http_client=client).probe(_URL)

        assert result.captcha_detected is True
        assert result.error_kind == "captcha"

    @respx.mock
    async def test_get_fallback_with_ddos_guard_body_detects_captcha(self) -> None:
        html = '<title>DDoS-Guard</title><script src="https://check.ddos-guard.net/check.js">'
        respx.head(_URL).respond(405)
        respx.get(_URL).respond(403, text=html)
        async with httpx.AsyncClient() as client:
            result = await HealthProber(http_client=client).probe(_URL)

        assert result.captcha_detected is True

    @respx.mock
    async def test_login_widget_on_homepage_is_not_a_block(self) -> None:
        html = "<div class='g-recaptcha' data-sitekey='x'></div>"
        respx.head(_URL).respond(405)
        respx.get(_URL).respond(200, text=html)
        async with httpx.AsyncClient() as client:
            result = await HealthProber(http_client=client).probe(_URL)

        assert result.captcha_detected is False
        assert result.ok is True


class TestProbeAll:
    @respx.mock
    async def test_probes_multiple_plugins(self) -> None:
        respx.head("https://a.com").respond(200)
        respx.head("https://b.com").respond(200)
        respx.head("https://c.com").respond(500)
        plugins = {
            "plugin_a": "https://a.com",
            "plugin_b": "https://b.com",
            "plugin_c": "https://c.com",
        }
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            results = await prober.probe_all(plugins, concurrency=2)

        assert len(results) == 3
        assert results["plugin_a"].ok is True
        assert results["plugin_b"].ok is True
        assert results["plugin_c"].ok is False

    @respx.mock
    async def test_empty_plugins_dict(self) -> None:
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            results = await prober.probe_all({})

        assert results == {}

    @respx.mock
    async def test_respects_concurrency(self) -> None:
        # Just verify it doesn't crash with concurrency=1.
        respx.head("https://a.com").respond(200)
        respx.head("https://b.com").respond(200)
        plugins = {
            "a": "https://a.com",
            "b": "https://b.com",
        }
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            results = await prober.probe_all(plugins, concurrency=1)

        assert len(results) == 2


class TestUserAgent:
    """The probe sends a browser's User-Agent: a site behind Cloudflare
    challenges or blocks the bot User-Agent of the shared client, which
    would count the site as down."""

    @respx.mock
    async def test_head_sends_the_browser_user_agent(self) -> None:
        route = respx.head(_URL).respond(200)
        async with httpx.AsyncClient() as client:
            await HealthProber(http_client=client).probe(_URL)
        assert route.calls.last.request.headers["user-agent"] == DEFAULT_USER_AGENT

    @respx.mock
    async def test_the_get_fallback_sends_it_too(self) -> None:
        respx.head(_URL).respond(405)
        route = respx.get(_URL).respond(200, text="x")
        async with httpx.AsyncClient() as client:
            await HealthProber(http_client=client).probe(_URL)
        assert route.calls.last.request.headers["user-agent"] == DEFAULT_USER_AGENT
