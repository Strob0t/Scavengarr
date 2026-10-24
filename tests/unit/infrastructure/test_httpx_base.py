"""Tests for HttpxPluginBase shared base class."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import httpx
import pytest
import respx
import structlog

from scavengarr.domain.entities.stremio import EpisodeRef
from scavengarr.domain.plugins.base import (
    PluginUnreachableError,
    SearchResult,
)
from scavengarr.domain.ports.browser_fetcher import Redirect
from scavengarr.domain.ports.plugin_history import ChallengeFlag, challenge_flag
from scavengarr.infrastructure.common.rate_limiter import DomainRateLimiter
from scavengarr.infrastructure.common.retry_transport import RetryTransport
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Concrete test subclass
# ---------------------------------------------------------------------------


class _TestPlugin(HttpxPluginBase):
    name = "test-plugin"
    provides = "stream"
    _domains = ["example.com", "fallback.com"]


class _SingleDomainPlugin(HttpxPluginBase):
    name = "single"
    provides = "download"
    _domains = ["only.com"]


class _RecordingPlugin(HttpxPluginBase):
    """A plugin whose search takes the four arguments only."""

    name = "recording"
    provides = "stream"
    _domains = ["example.com"]

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        self.calls.append(
            {"query": query, "category": category, "season": season, "episode": episode}
        )
        return []


class _LocatingPlugin(_RecordingPlugin):
    """A plugin that locates episodes: its search takes the reference."""

    name = "locating"
    locates_episodes = True

    async def search(  # type: ignore[override]
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
        *,
        episode_ref: EpisodeRef | None = None,
    ) -> list[SearchResult]:
        self.calls.append(
            {
                "query": query,
                "category": category,
                "season": season,
                "episode": episode,
                "episode_ref": episode_ref,
            }
        )
        return []


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestInit:
    def test_base_url_set_from_first_domain(self) -> None:
        plugin = _TestPlugin()
        assert plugin.base_url == "https://example.com"

    def test_attributes_set(self) -> None:
        plugin = _TestPlugin()
        assert plugin.name == "test-plugin"
        assert plugin.provides == "stream"
        assert plugin.mode == "httpx"
        assert plugin.default_language == "de"


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------


class TestEnsureClient:
    @pytest.mark.asyncio
    async def test_creates_client(self) -> None:
        plugin = _TestPlugin()
        client = await plugin._ensure_client()
        assert client is not None
        assert plugin._client is client
        await plugin.cleanup()

    @pytest.mark.asyncio
    async def test_reuses_existing_client(self) -> None:
        plugin = _TestPlugin()
        c1 = await plugin._ensure_client()
        c2 = await plugin._ensure_client()
        assert c1 is c2
        await plugin.cleanup()


# ---------------------------------------------------------------------------
# Domain verification
# ---------------------------------------------------------------------------


def _head_response(
    domain: str, status: int = 200, headers: dict[str, str] | None = None
) -> MagicMock:
    """Build a mock HEAD response with a realistic ``.url`` attribute."""
    resp = MagicMock()
    resp.status_code = status
    resp.url = httpx.URL(f"https://{domain}/")
    resp.headers = httpx.Headers(headers or {})
    return resp


class TestVerifyDomain:
    @pytest.mark.asyncio
    async def test_the_check_sends_the_browser_user_agent(self) -> None:
        """A site behind Cloudflare answers the bot User-Agent of the shared
        client with a challenge or a block; the check sends what a browser
        sends, like the plugin's own requests."""
        plugin = _TestPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=_head_response("example.com"))
        plugin._client = mock_client

        await plugin._verify_domain()

        headers = mock_client.head.await_args.kwargs["headers"]
        assert headers["User-Agent"] == DEFAULT_USER_AGENT

    @pytest.mark.asyncio
    async def test_first_domain_reachable(self) -> None:
        plugin = _TestPlugin()
        head_resp = _head_response("example.com")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=head_resp)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert urlparse(plugin.base_url).hostname == "example.com"

    @pytest.mark.asyncio
    async def test_fallback_to_second_domain(self) -> None:
        plugin = _TestPlugin()
        fail_resp = _head_response("example.com", status=503)
        ok_resp = _head_response("fallback.com")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=[fail_resp, ok_resp])
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert urlparse(plugin.base_url).hostname == "fallback.com"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "headers"),
        [
            (403, {"cf-mitigated": "challenge"}),
            (503, {"cf-mitigated": "challenge"}),
            (503, {"cf-ray": "abc123"}),
        ],
        ids=["403-mitigated", "503-mitigated", "503-cf-ray"],
    )
    async def test_a_challenge_answer_counts_as_reachable(
        self, status: int, headers: dict[str, str]
    ) -> None:
        """kinoger answers 403 with a Cloudflare challenge: the site is up
        behind it, the plugin's browser fallback solves it. A 503 challenge
        counts too (the check read the body-less answer without its
        Cloudflare headers before, review of step 21). The domain is not
        pinned: the next window checks again."""
        plugin = _TestPlugin()
        challenge = _head_response("example.com", status=status, headers=headers)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=challenge)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is False
        assert plugin.base_url == "https://example.com"

    @pytest.mark.asyncio
    async def test_a_working_domain_outranks_an_answering_one(self) -> None:
        plugin = _TestPlugin()
        blocked = _head_response("example.com", status=403)
        ok_resp = _head_response("fallback.com")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=[blocked, ok_resp])
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin.base_url == "https://fallback.com"

    @pytest.mark.asyncio
    async def test_an_error_page_is_still_the_site(self) -> None:
        """No domain answers below 400: the first answering one is used,
        without pinning it."""
        plugin = _TestPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(
            side_effect=[
                _head_response("example.com", status=404),
                _head_response("fallback.com", status=502),
            ]
        )
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is False
        assert plugin.base_url == "https://example.com"

    @pytest.mark.asyncio
    async def test_a_transient_error_does_not_pin_the_domain(self) -> None:
        """A 429 at check time serves this search; the next window checks
        again and a 200 then pins the domain (before, a 4xx pinned it for
        the process lifetime; review of step 21)."""
        plugin = _TestPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(
            side_effect=[
                _head_response("example.com", status=429),
                _head_response("fallback.com", status=429),
                _head_response("example.com"),
            ]
        )
        plugin._client = mock_client

        await plugin._verify_domain()
        assert plugin._domain_verified is False
        assert plugin.base_url == "https://example.com"

        plugin._domain_recheck_at = 0.0  # the window is over
        await plugin._verify_domain()

        assert mock_client.head.await_count == 3
        assert plugin._domain_verified is True

    @pytest.mark.asyncio
    async def test_an_answering_domain_serves_its_window(self) -> None:
        """Within the window, no new HEAD checks."""
        plugin = _TestPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(
            return_value=_head_response("example.com", status=404)
        )
        plugin._client = mock_client

        await plugin._verify_domain()
        await plugin._verify_domain()

        assert mock_client.head.await_count == 2  # both domains, once
        assert plugin._domain_verified is False

    @pytest.mark.asyncio
    async def test_a_working_domain_is_pinned(self) -> None:
        plugin = _TestPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=_head_response("example.com"))
        plugin._client = mock_client

        await plugin._verify_domain()
        await plugin._verify_domain()

        assert mock_client.head.await_count == 1
        assert plugin._domain_verified is True

    @pytest.mark.asyncio
    async def test_all_domains_fail(self) -> None:
        plugin = _TestPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        plugin._client = mock_client

        with pytest.raises(PluginUnreachableError):
            await plugin._verify_domain()

        assert plugin._domain_verified is False

    @pytest.mark.asyncio
    async def test_skips_if_already_verified(self) -> None:
        plugin = _TestPlugin()
        plugin._domain_verified = True
        plugin.base_url = "https://custom.domain"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin._verify_domain()

        mock_client.head.assert_not_called()
        assert plugin.base_url == "https://custom.domain"

    @pytest.mark.asyncio
    async def test_single_domain_skips_verification(self) -> None:
        """Plugins with only one domain skip HEAD checks."""
        plugin = _SingleDomainPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin._verify_domain()

        mock_client.head.assert_not_called()
        assert plugin._domain_verified is True

    @pytest.mark.asyncio
    async def test_redirect_captures_final_url(self) -> None:
        """When a domain redirects (e.g. to www.), base_url uses the final host."""
        plugin = _TestPlugin()
        # Simulate: HEAD https://example.com/ → redirected to www.example.com
        redirected_resp = _head_response("www.example.com")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=redirected_resp)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert plugin.base_url == "https://www.example.com"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    @pytest.mark.asyncio
    async def test_closes_client(self) -> None:
        plugin = _TestPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client
        plugin._domain_verified = True

        await plugin.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert plugin._client is None
        assert plugin._domain_verified is False

    @pytest.mark.asyncio
    async def test_noop_when_no_client(self) -> None:
        plugin = _TestPlugin()
        await plugin.cleanup()  # Should not raise


# ---------------------------------------------------------------------------
# _safe_fetch
# ---------------------------------------------------------------------------


class TestSafeFetch:
    @pytest.mark.asyncio
    async def test_returns_response_on_success(self) -> None:
        plugin = _TestPlugin()
        resp = MagicMock(spec=httpx.Response, history=[])
        resp.status_code = 200
        resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=resp)
        plugin._client = mock_client

        result = await plugin._safe_fetch("https://example.com/api")

        assert result is resp

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self) -> None:
        plugin = _TestPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))
        plugin._client = mock_client

        result = await plugin._safe_fetch("https://example.com/api")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        plugin = _TestPlugin()
        resp = MagicMock(spec=httpx.Response, history=[])
        resp.status_code = 403
        resp.text = "<html>Forbidden</html>"
        resp.headers = httpx.Headers()
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "forbidden", request=MagicMock(), response=resp
            )
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=resp)
        plugin._client = mock_client

        result = await plugin._safe_fetch("https://example.com/api")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_connection_error(self) -> None:
        plugin = _TestPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        plugin._client = mock_client

        result = await plugin._safe_fetch("https://example.com/api")

        assert result is None


class TestSafeFetchChallenge:
    """A challenge answer to ``_safe_fetch`` is told apart from an error
    page: logged as ``<plugin>_challenge`` with the class and the host, the
    host goes into the browser memo (so ``_fetch_text`` of the same plugin
    goes straight to the browser), and the caller gets ``None``. The
    browser fallback itself stays with ``_fetch_text``."""

    _CHALLENGE = "<html><title>Just a moment...</title>challenge-platform</html>"

    @respx.mock
    async def test_a_challenge_is_logged_and_remembered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        respx.get("https://example.com/api").respond(
            403, headers={"cf-mitigated": "challenge"}, html=self._CHALLENGE
        )
        plugin = _TestPlugin()
        async with httpx.AsyncClient() as client:
            plugin._client = client
            with structlog.testing.capture_logs() as logs:
                result = await plugin._safe_fetch("https://example.com/api")

        assert result is None
        events = {log["event"]: log for log in logs}
        assert "test-plugin_challenge" in events
        assert events["test-plugin_challenge"]["challenge"] == "cloudflare_page"
        assert events["test-plugin_challenge"]["host"] == "example.com"
        assert "test-plugin_http_error" not in events
        assert HttpxPluginBase._cf_blocked_until.keys() == {"example.com"}

    @respx.mock
    async def test_the_memo_sends_fetch_text_to_the_browser(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        fetcher = AsyncMock()
        fetcher.fetch_text = AsyncMock(return_value="<html>solved</html>")
        monkeypatch.setattr(HttpxPluginBase, "_browser_fetcher", fetcher)
        respx.get("https://example.com/api").respond(503, html=self._CHALLENGE)
        plugin = _TestPlugin()
        async with httpx.AsyncClient() as client:
            plugin._client = client
            assert await plugin._safe_fetch("https://example.com/api") is None
            html = await plugin._fetch_text("https://example.com/page")

        assert html == "<html>solved</html>"
        fetcher.fetch_text.assert_awaited_once()

    @respx.mock
    async def test_an_error_page_is_an_http_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        respx.get("https://example.com/api").respond(403, html="<html>Forbidden</html>")
        plugin = _TestPlugin()
        async with httpx.AsyncClient() as client:
            plugin._client = client
            with structlog.testing.capture_logs() as logs:
                result = await plugin._safe_fetch("https://example.com/api")

        assert result is None
        events = [log["event"] for log in logs]
        assert events == ["test-plugin_http_error"]
        assert HttpxPluginBase._cf_blocked_until == {}


class TestChallengeFlag:
    """A challenge seen by ``_safe_fetch`` or ``_fetch_text`` marks the
    running search's flag, which the search runner counts once (step 50);
    an error page does not."""

    _CHALLENGE = "<html><title>Just a moment...</title>challenge-platform</html>"

    @respx.mock
    async def test_safe_fetch_marks_the_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        respx.get("https://example.com/api").respond(403, html=self._CHALLENGE)
        plugin = _TestPlugin()
        flag = ChallengeFlag()
        token = challenge_flag.set(flag)
        try:
            async with httpx.AsyncClient() as client:
                plugin._client = client
                await plugin._safe_fetch("https://example.com/api")
        finally:
            challenge_flag.reset(token)

        assert flag.seen is True
        # Nothing tried to get past it (row 70)
        assert flag.unsolved_search is True

    @respx.mock
    async def test_fetch_text_marks_the_flag_without_a_fetcher(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        monkeypatch.setattr(HttpxPluginBase, "_browser_fetcher", None)
        respx.get("https://example.com/page").respond(503, html=self._CHALLENGE)
        plugin = _TestPlugin()
        flag = ChallengeFlag()
        token = challenge_flag.set(flag)
        try:
            async with httpx.AsyncClient() as client:
                plugin._client = client
                assert await plugin._fetch_text("https://example.com/page") is None
        finally:
            challenge_flag.reset(token)

        assert flag.seen is True
        assert flag.unsolved_search is True

    @respx.mock
    async def test_fetch_text_marks_the_flag_on_the_browser_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        fetcher = AsyncMock()
        fetcher.fetch_text = AsyncMock(return_value="<html>solved</html>")
        fetcher.session = AsyncMock(return_value=None)
        monkeypatch.setattr(HttpxPluginBase, "_browser_fetcher", fetcher)
        respx.get("https://example.com/page").respond(503, html=self._CHALLENGE)
        plugin = _TestPlugin()
        flag = ChallengeFlag()
        token = challenge_flag.set(flag)
        try:
            async with httpx.AsyncClient() as client:
                plugin._client = client
                html = await plugin._fetch_text("https://example.com/page")
        finally:
            challenge_flag.reset(token)

        assert html == "<html>solved</html>"
        assert flag.seen is True
        # The browser got past it (row 70)
        assert flag.unsolved_search is False

    @respx.mock
    async def test_a_browser_fallback_without_the_page_is_unsolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        fetcher = AsyncMock()
        fetcher.fetch_text = AsyncMock(return_value=None)
        monkeypatch.setattr(HttpxPluginBase, "_browser_fetcher", fetcher)
        respx.get("https://example.com/page").respond(503, html=self._CHALLENGE)
        plugin = _TestPlugin()
        flag = ChallengeFlag()
        token = challenge_flag.set(flag)
        try:
            async with httpx.AsyncClient() as client:
                plugin._client = client
                assert await plugin._fetch_text("https://example.com/page") is None
        finally:
            challenge_flag.reset(token)

        assert flag.seen is True
        assert flag.unsolved_search is True

    async def _link_out(
        self, monkeypatch: pytest.MonkeyPatch, redirect: Redirect
    ) -> tuple[str | None, ChallengeFlag]:
        """A link-out answered with a challenge, which the browser resolves
        to *redirect*."""
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        fetcher = AsyncMock()
        fetcher.resolve_redirect = AsyncMock(return_value=redirect)
        monkeypatch.setattr(HttpxPluginBase, "_browser_fetcher", fetcher)
        respx.get("https://example.com/external/abc").respond(403, html=self._CHALLENGE)
        plugin = _TestPlugin()
        flag = ChallengeFlag()
        token = challenge_flag.set(flag)
        try:
            async with httpx.AsyncClient() as client:
                plugin._client = client
                found = await plugin._resolve_redirect(
                    "https://example.com/external/abc"
                )
        finally:
            challenge_flag.reset(token)
        fetcher.resolve_redirect.assert_awaited_once()
        return found, flag

    @respx.mock
    async def test_a_link_out_the_browser_resolves_is_solved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        found, flag = await self._link_out(
            monkeypatch, Redirect("https://hoster.example/e/1", passed=True)
        )

        assert found == "https://hoster.example/e/1"
        assert flag.seen is True
        # The browser got past it (row 88: it stayed open, so unsolved)
        assert flag.unsolved_search is False

    @respx.mock
    async def test_a_link_out_the_browser_does_not_resolve_is_unsolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        found, flag = await self._link_out(monkeypatch, Redirect(None, passed=False))

        assert found is None
        assert flag.seen is True
        assert flag.unsolved_search is True

    @respx.mock
    async def test_a_dead_link_out_behind_a_passed_challenge_is_solved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The review's case (code review 2026-10-12, finding 14): the browser
        gets past the challenge, and the link-out answers a same-host 404 (an
        expired hash). A dead link, not an unsolved challenge."""
        found, flag = await self._link_out(monkeypatch, Redirect(None, passed=True))

        assert found is None
        assert flag.seen is True
        assert flag.unsolved_search is False

    @respx.mock
    async def test_an_error_page_leaves_the_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        respx.get("https://example.com/api").respond(500, html="<html>Oops</html>")
        plugin = _TestPlugin()
        flag = ChallengeFlag()
        token = challenge_flag.set(flag)
        try:
            async with httpx.AsyncClient() as client:
                plugin._client = client
                await plugin._safe_fetch("https://example.com/api")
                await plugin._fetch_text("https://example.com/api")
        finally:
            challenge_flag.reset(token)

        assert flag.seen is False


# ---------------------------------------------------------------------------
# _safe_parse_json
# ---------------------------------------------------------------------------


class TestSafeParseJson:
    def test_parses_valid_json(self) -> None:
        plugin = _TestPlugin()
        resp = MagicMock(spec=httpx.Response, history=[])
        resp.json.return_value = {"data": [1, 2, 3]}
        resp.url = "https://example.com/api"

        result = plugin._safe_parse_json(resp)

        assert result == {"data": [1, 2, 3]}

    def test_returns_none_on_invalid_json(self) -> None:
        plugin = _TestPlugin()
        resp = MagicMock(spec=httpx.Response, history=[])
        resp.json.side_effect = ValueError("invalid")
        resp.url = "https://example.com/api"

        result = plugin._safe_parse_json(resp)

        assert result is None


# ---------------------------------------------------------------------------
# _new_semaphore
# ---------------------------------------------------------------------------


class TestCache:
    @pytest.fixture(autouse=True)
    def _reset_cache(self) -> None:
        HttpxPluginBase.set_cache(None)
        yield
        HttpxPluginBase.set_cache(None)

    def test_the_injected_cache_reaches_every_plugin(self) -> None:
        cache = AsyncMock()
        HttpxPluginBase.set_cache(cache)

        assert _TestPlugin()._cache is cache

    def test_without_a_cache(self) -> None:
        assert _TestPlugin()._cache is None


class TestIsolatedSearch:
    """The runner's entry: ``isolated_search`` hands the reference on."""

    async def test_the_reference_reaches_search(self) -> None:
        plugin = _LocatingPlugin()
        ref = EpisodeRef(5, 2, "Laboon", "2001-03-21", absolute=62)

        await plugin.isolated_search(
            "one piece", 5000, season=5, episode=2, episode_ref=ref
        )

        assert plugin.calls == [
            {
                "query": "one piece",
                "category": 5000,
                "season": 5,
                "episode": 2,
                "episode_ref": ref,
            }
        ]

    async def test_without_a_reference_search_is_called_as_before(self) -> None:
        # a plugin without the keyword: the call carries no episode_ref
        plugin = _RecordingPlugin()

        await plugin.isolated_search("dark", 5000, season=1, episode=1)

        assert plugin.calls == [
            {"query": "dark", "category": 5000, "season": 1, "episode": 1}
        ]


class TestNewSemaphore:
    def test_returns_semaphore_with_default_limit(self) -> None:
        plugin = _TestPlugin()
        sem = plugin._new_semaphore()
        assert isinstance(sem, asyncio.Semaphore)

    def test_custom_limit(self) -> None:
        plugin = _TestPlugin()
        plugin._max_concurrent = 5
        sem = plugin._new_semaphore()
        assert isinstance(sem, asyncio.Semaphore)


# ---------------------------------------------------------------------------
# search() abstract
# ---------------------------------------------------------------------------


class TestSharedHttpClient:
    @pytest.fixture(autouse=True)
    def _reset_shared(self) -> None:
        """Ensure shared client is cleared before/after each test."""
        HttpxPluginBase._shared_http_client = None
        yield
        HttpxPluginBase._shared_http_client = None

    @pytest.mark.asyncio
    async def test_uses_shared_client_when_set(self) -> None:
        shared = AsyncMock(spec=httpx.AsyncClient)
        HttpxPluginBase.set_shared_http_client(shared)
        plugin = _TestPlugin()

        client = await plugin._ensure_client()

        assert client is shared

    @pytest.mark.asyncio
    async def test_creates_own_client_when_no_shared(self) -> None:
        plugin = _TestPlugin()

        client = await plugin._ensure_client()

        assert client is not None
        assert isinstance(client, httpx.AsyncClient)
        await plugin.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_skips_shared_client(self) -> None:
        shared = AsyncMock(spec=httpx.AsyncClient)
        HttpxPluginBase.set_shared_http_client(shared)
        plugin = _TestPlugin()
        await plugin._ensure_client()

        await plugin.cleanup()

        shared.aclose.assert_not_awaited()
        assert plugin._client is None

    @pytest.mark.asyncio
    async def test_safe_fetch_adds_per_plugin_overrides(self) -> None:
        shared = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock(spec=httpx.Response, history=[])
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        shared.get = AsyncMock(return_value=resp)
        HttpxPluginBase.set_shared_http_client(shared)

        plugin = _TestPlugin()
        plugin._timeout = 42.0
        plugin._user_agent = "CustomAgent/1.0"
        await plugin._safe_fetch("https://example.com/test")

        call_kwargs = shared.get.call_args[1]
        assert call_kwargs["timeout"] == httpx.Timeout(42.0)
        assert call_kwargs["headers"] == {"User-Agent": "CustomAgent/1.0"}

    @pytest.mark.asyncio
    async def test_safe_fetch_extra_headers_keep_the_user_agent(self) -> None:
        """aniworld's AJAX search adds a header: its request went out with
        the app's own User-Agent instead of the plugin's."""
        shared = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock(spec=httpx.Response, history=[])
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        shared.get = AsyncMock(return_value=resp)
        HttpxPluginBase.set_shared_http_client(shared)

        plugin = _TestPlugin()
        plugin._user_agent = "CustomAgent/1.0"
        await plugin._safe_fetch(
            "https://example.com/ajax", headers={"X-Requested-With": "XMLHttpRequest"}
        )

        assert shared.get.call_args[1]["headers"] == {
            "User-Agent": "CustomAgent/1.0",
            "X-Requested-With": "XMLHttpRequest",
        }

    @pytest.mark.asyncio
    async def test_a_plugin_request_still_waits_out_a_429(self) -> None:
        """Link validation's requests go out once (``LINK_CHECK``); a
        plugin's own request through the same shared client still waits out
        a 429 and retries."""
        router = respx.MockRouter(assert_all_called=False)
        route = router.get("https://example.com/search").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "2"}),
                httpx.Response(200, text="<html>results</html>"),
            ]
        )
        transport = RetryTransport(
            wrapped=httpx.MockTransport(router.async_handler),
            rate_limiter=DomainRateLimiter(default_rps=0.0),
            max_retries=2,
        )
        shared = httpx.AsyncClient(transport=transport)
        HttpxPluginBase.set_shared_http_client(shared)
        try:
            with patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m:
                m.sleep = AsyncMock()
                resp = await _TestPlugin()._safe_fetch("https://example.com/search")
        finally:
            await shared.aclose()

        assert resp is not None
        assert resp.status_code == 200
        assert route.call_count == 2
        m.sleep.assert_awaited_once_with(2.0)

    @pytest.mark.asyncio
    async def test_safe_fetch_no_overrides_with_own_client(self) -> None:
        plugin = _TestPlugin()
        resp = MagicMock(spec=httpx.Response, history=[])
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=resp)
        plugin._client = mock_client

        await plugin._safe_fetch("https://example.com/test")

        call_kwargs = mock_client.get.call_args[1]
        assert "timeout" not in call_kwargs
        assert "headers" not in call_kwargs


class TestCategoryMatches:
    """Tests for _category_matches range-aware helper."""

    def test_none_matches_anything(self) -> None:
        assert _TestPlugin._category_matches(None, 5070) is True

    def test_exact_match(self) -> None:
        assert _TestPlugin._category_matches(5070, 5070) is True

    def test_parent_matches_child(self) -> None:
        """5000 (any TV) should match 5070 (anime)."""
        assert _TestPlugin._category_matches(5000, 5070) is True

    def test_parent_matches_itself(self) -> None:
        assert _TestPlugin._category_matches(5000, 5000) is True

    def test_parent_matches_all_children(self) -> None:
        for child in (5010, 5020, 5030, 5040, 5050, 5060, 5070, 5080):
            assert _TestPlugin._category_matches(5000, child) is True

    def test_movie_parent_matches_movie_children(self) -> None:
        assert _TestPlugin._category_matches(2000, 2040) is True

    def test_different_range_no_match(self) -> None:
        """Movie category should not match TV child."""
        assert _TestPlugin._category_matches(2000, 5070) is False

    def test_child_does_not_match_different_child(self) -> None:
        assert _TestPlugin._category_matches(5070, 5080) is False

    def test_child_does_not_match_parent_as_accepted(self) -> None:
        """5070 requested should not match 5000 accepted."""
        assert _TestPlugin._category_matches(5070, 5000) is False


class TestSearchAbstract:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self) -> None:
        plugin = _TestPlugin()
        with pytest.raises(NotImplementedError, match="search.*not implemented"):
            await plugin.search("test")


class TestOwnLinkResolutionBound:
    @pytest.mark.asyncio
    async def test_redirects_bounded_by_max_concurrent(self) -> None:
        """Up to 1000 results must not open hundreds of redirect requests
        to the plugin's own (usually Cloudflare-protected) host at once."""
        plugin = _TestPlugin()
        plugin._max_concurrent = 2
        in_flight = 0
        peak = 0

        async def _redirect(url: str, context: str = "") -> str:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return "https://hoster.example/" + url.rsplit("/", 1)[-1]

        plugin._resolve_redirect = _redirect  # type: ignore[method-assign]
        results = [
            SearchResult(
                title=f"r{i}", download_link=f"https://example.com/external/{i}"
            )
            for i in range(10)
        ]

        resolved = await plugin._resolve_result_links(results)

        assert len(resolved) == 10
        assert resolved[3].download_link == "https://hoster.example/3"
        assert peak == 2


# ---------------------------------------------------------------------------
# The site's official domain list (row 69)
# ---------------------------------------------------------------------------


class _ListedPlugin(HttpxPluginBase):
    name = "listed"
    provides = "stream"
    _domains = ["site.example"]
    domain_list_url = "https://list.example/"
    domain_marker = 'a[href*="list.example"]'


# The site's own page shows the link to its list; a parked domain does not
_SITE_PAGE = '<footer><a href="https://list.example/">Domains</a></footer>'
_PARKED_PAGE = "<h1>This domain is for sale</h1>"
_LIST_PAGE = (
    '<a href="https://site.example/">a</a>'
    '<a href="https://parked.example/">b</a>'
    '<a href="https://mirror.example/">c</a>'
)
_KEY = "listed:domain_list:v1"


@pytest.fixture
def list_cache(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    monkeypatch.setattr(HttpxPluginBase, "_cache", cache)
    return cache


class TestDomainList:
    @pytest.mark.asyncio
    @respx.mock
    async def test_a_listed_host_that_serves_the_site_is_used(
        self, list_cache: AsyncMock
    ) -> None:
        respx.head("https://site.example/").mock(side_effect=httpx.ConnectError("x"))
        respx.get("https://list.example/").respond(200, text=_LIST_PAGE)
        respx.get("https://parked.example/").respond(200, text=_PARKED_PAGE)
        respx.get("https://mirror.example/").respond(200, text=_SITE_PAGE)
        site_get = respx.get("https://site.example/")
        plugin = _ListedPlugin()
        plugin._client = httpx.AsyncClient()

        with structlog.testing.capture_logs() as logs:
            await plugin._verify_domain()

        assert plugin.base_url == "https://mirror.example"
        assert plugin._domain_verified is True
        # A host of _domains is not tried twice
        assert not site_get.called
        list_cache.set.assert_awaited_once_with(
            _KEY, ["site.example", "parked.example", "mirror.example"], ttl=86400
        )
        found = [e for e in logs if e["event"] == "plugin_domain_list_host"]
        assert found == [
            {
                "event": "plugin_domain_list_host",
                "plugin": "listed",
                "host": "mirror.example",
                "log_level": "info",
            }
        ]

    @pytest.mark.asyncio
    @respx.mock
    async def test_the_list_is_read_once_a_day(self, list_cache: AsyncMock) -> None:
        list_cache.get = AsyncMock(return_value=["mirror.example"])
        respx.head("https://site.example/").mock(side_effect=httpx.ConnectError("x"))
        list_route = respx.get("https://list.example/")
        respx.get("https://mirror.example/").respond(200, text=_SITE_PAGE)
        plugin = _ListedPlugin()
        plugin._client = httpx.AsyncClient()

        await plugin._verify_domain()

        list_cache.get.assert_awaited_once_with(_KEY)
        assert not list_route.called
        assert plugin.base_url == "https://mirror.example"

    @pytest.mark.asyncio
    @respx.mock
    async def test_a_host_without_the_marker_is_refused(
        self, list_cache: AsyncMock
    ) -> None:
        list_cache.get = AsyncMock(return_value=["parked.example"])
        respx.head("https://site.example/").mock(side_effect=httpx.ConnectError("x"))
        respx.get("https://parked.example/").respond(200, text=_PARKED_PAGE)
        plugin = _ListedPlugin()
        plugin._client = httpx.AsyncClient()

        with pytest.raises(PluginUnreachableError):
            await plugin._verify_domain()

        assert plugin.base_url == "https://site.example"

    @pytest.mark.asyncio
    @respx.mock
    async def test_a_list_that_fails_leaves_the_domains_for_an_hour(
        self, list_cache: AsyncMock
    ) -> None:
        respx.head("https://site.example/").mock(side_effect=httpx.ConnectError("x"))
        respx.get("https://list.example/").respond(503)
        plugin = _ListedPlugin()
        plugin._client = httpx.AsyncClient()

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(PluginUnreachableError):
                await plugin._verify_domain()

        list_cache.set.assert_awaited_once_with(_KEY, [], ttl=3600)
        warnings = [e for e in logs if e["event"] == "plugin_domain_list_failed"]
        assert len(warnings) == 1
        assert warnings[0]["plugin"] == "listed"

    @pytest.mark.asyncio
    @respx.mock
    async def test_a_working_domain_never_reads_the_list(
        self, list_cache: AsyncMock
    ) -> None:
        respx.head("https://site.example/").respond(200)
        list_route = respx.get("https://list.example/")
        plugin = _ListedPlugin()
        plugin._client = httpx.AsyncClient()

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert not list_route.called
        list_cache.get.assert_not_awaited()

    @pytest.mark.asyncio
    @respx.mock
    async def test_an_answering_domain_never_reads_the_list(
        self, list_cache: AsyncMock
    ) -> None:
        respx.head("https://site.example/").respond(
            403, headers={"cf-mitigated": "challenge"}
        )
        list_route = respx.get("https://list.example/")
        plugin = _ListedPlugin()
        plugin._client = httpx.AsyncClient()

        await plugin._verify_domain()

        assert plugin.base_url == "https://site.example"
        assert not list_route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_without_a_cache_the_list_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(HttpxPluginBase, "_cache", None)
        respx.head("https://site.example/").mock(side_effect=httpx.ConnectError("x"))
        list_route = respx.get("https://list.example/")
        plugin = _ListedPlugin()
        plugin._client = httpx.AsyncClient()

        with pytest.raises(PluginUnreachableError):
            await plugin._verify_domain()

        assert not list_route.called


def _route_fails(route: respx.Route, failure: httpx.Response | Exception) -> None:
    """Let *route* answer *failure*: an error answer or a raised exception."""
    if isinstance(failure, httpx.Response):
        route.mock(return_value=failure)
    else:
        route.mock(side_effect=failure)


class TestSearchPage:
    """The search page's raising mode (``_fetch_search_page``): a failure of
    the site stays one, so the plugin record counts a timeout or an
    unreachable site instead of a search without results (row 139). Every
    other page keeps the lenient mode, ``None`` with the log line."""

    @respx.mock
    async def test_an_answering_page_is_returned(self) -> None:
        respx.get("https://only.com/search", params={"q": "x"}).respond(
            200, text="<html>hits</html>"
        )
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        html = await plugin._fetch_search_page("/search", params={"q": "x"})

        assert html == "<html>hits</html>"

    @respx.mock
    async def test_a_timeout_stays_a_timeout(self) -> None:
        respx.get("https://only.com/search").mock(side_effect=httpx.ReadTimeout("x"))
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        with structlog.testing.capture_logs() as logs, pytest.raises(TimeoutError):
            await plugin._fetch_search_page("/search")

        assert [e["event"] for e in logs] == ["single_timeout"]

    @pytest.mark.parametrize(
        "failure",
        [
            httpx.Response(503, text="<html>Service Unavailable</html>"),
            httpx.ConnectError("refused"),
            httpx.ConnectTimeout("no connection"),
            httpx.RemoteProtocolError("closed"),
        ],
    )
    @respx.mock
    async def test_the_only_domain_without_an_answer_is_unreachable(
        self, failure: httpx.Response | Exception
    ) -> None:
        """A 5xx, a transport error and a connect timeout: the domain does
        not answer, as in the domain check."""
        _route_fails(respx.get("https://only.com/search"), failure)
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        with pytest.raises(PluginUnreachableError):
            await plugin._fetch_search_page("/search")

    @respx.mock
    async def test_a_domain_without_an_answer_hands_the_page_to_the_next(
        self,
    ) -> None:
        refused = httpx.ConnectError("refused")
        respx.get("https://example.com/search").mock(side_effect=refused)
        respx.head("https://example.com/").mock(side_effect=refused)
        respx.head("https://fallback.com/").respond(200)
        respx.get("https://fallback.com/search").respond(200, text="<html>hits</html>")
        plugin = _TestPlugin()
        plugin._client = httpx.AsyncClient()
        plugin._domain_verified = True

        html = await plugin._fetch_search_page("/search")

        assert html == "<html>hits</html>"
        assert plugin.base_url == "https://fallback.com"
        assert plugin._domain_verified is True

    @respx.mock
    async def test_no_domain_answering_is_unreachable(self) -> None:
        respx.get("https://example.com/search").respond(503)
        respx.head("https://example.com/").respond(503)
        respx.head("https://fallback.com/").mock(
            side_effect=httpx.ConnectError("refused")
        )
        plugin = _TestPlugin()
        plugin._client = httpx.AsyncClient()
        plugin._domain_verified = True

        with pytest.raises(PluginUnreachableError):
            await plugin._fetch_search_page("/search")

        # The next search checks the domains again
        assert plugin._domain_verified is False

    @respx.mock
    async def test_the_page_failing_on_the_answering_domain_too_is_unreachable(
        self,
    ) -> None:
        """The front page answers the check, the search page fails there
        again: no domain serves the search."""
        search = respx.get("https://example.com/search").respond(502)
        respx.head("https://example.com/").respond(200)
        plugin = _TestPlugin()
        plugin._client = httpx.AsyncClient()
        plugin._domain_verified = True

        with pytest.raises(PluginUnreachableError):
            await plugin._fetch_search_page("/search")

        assert search.call_count == 2

    @respx.mock
    async def test_a_client_error_stays_none(self) -> None:
        respx.get("https://only.com/search").respond(404)
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        assert await plugin._fetch_search_page("/search") is None

    @respx.mock
    async def test_a_challenge_stays_with_the_challenge_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 503 challenge page is the site behind a challenge, not a site
        that does not answer."""
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        monkeypatch.setattr(HttpxPluginBase, "_browser_fetcher", None)
        respx.get("https://only.com/search").respond(
            503, html=TestSafeFetchChallenge._CHALLENGE
        )
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        assert await plugin._fetch_search_page("/search") is None

    @pytest.mark.parametrize(
        "failure",
        [
            httpx.Response(503),
            httpx.ConnectError("refused"),
            httpx.ReadTimeout("slow"),
        ],
    )
    @respx.mock
    async def test_every_other_page_stays_lenient(
        self, failure: httpx.Response | Exception
    ) -> None:
        """``_fetch_text()`` keeps ``None``: a missing detail page is a
        partial result."""
        _route_fails(respx.get("https://only.com/detail"), failure)
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        assert await plugin._fetch_text("https://only.com/detail") is None


class TestSearchPagePost:
    """A search that is a POST below ``base_url`` (a form, an API) in the
    raising mode: ``_fetch_search_page(method="POST")`` sends it through
    ``_safe_fetch(raising=True)``, with the same mapping as a GET."""

    @respx.mock
    async def test_an_answering_post_is_returned(self) -> None:
        route = respx.post("https://only.com/search").respond(200, text="<p>hits</p>")
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        html = await plugin._fetch_search_page(
            "/search",
            method="POST",
            data={"q": "x"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        assert html == "<p>hits</p>"
        request = route.calls.last.request
        assert request.content == b"q=x"
        assert request.headers["X-Requested-With"] == "XMLHttpRequest"

    @respx.mock
    async def test_a_json_body_is_sent(self) -> None:
        route = respx.post("https://only.com/api").respond(200, json={"hits": []})
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        body = await plugin._fetch_search_page(
            "/api", method="POST", json_body={"query": "x"}
        )

        assert body == '{"hits":[]}'
        assert route.calls.last.request.content == b'{"query":"x"}'

    @respx.mock
    async def test_a_read_timeout_stays_a_timeout(self) -> None:
        respx.post("https://only.com/search").mock(side_effect=httpx.ReadTimeout("x"))
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        with structlog.testing.capture_logs() as logs, pytest.raises(TimeoutError):
            await plugin._fetch_search_page("/search", method="POST", data={})

        assert [e["event"] for e in logs] == ["single_timeout"]

    @pytest.mark.parametrize(
        "failure",
        [
            httpx.Response(503, text="<html>Service Unavailable</html>"),
            httpx.ConnectError("refused"),
            httpx.ConnectTimeout("no connection"),
            httpx.RemoteProtocolError("closed"),
        ],
    )
    @respx.mock
    async def test_the_only_domain_without_an_answer_is_unreachable(
        self, failure: httpx.Response | Exception
    ) -> None:
        _route_fails(respx.post("https://only.com/search"), failure)
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        with pytest.raises(PluginUnreachableError):
            await plugin._fetch_search_page("/search", method="POST", data={})

    @respx.mock
    async def test_a_client_error_stays_none(self) -> None:
        respx.post("https://only.com/search").respond(404)
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        assert (
            await plugin._fetch_search_page("/search", method="POST", data={}) is None
        )

    @respx.mock
    async def test_a_challenge_stays_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        respx.post("https://only.com/search").respond(
            503, html=TestSafeFetchChallenge._CHALLENGE
        )
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        with structlog.testing.capture_logs() as logs:
            html = await plugin._fetch_search_page("/search", method="POST", data={})

        assert html is None
        assert [e["event"] for e in logs] == ["single_challenge"]

    @respx.mock
    async def test_a_domain_without_an_answer_hands_the_post_to_the_next(
        self,
    ) -> None:
        refused = httpx.ConnectError("refused")
        respx.post("https://example.com/search").mock(side_effect=refused)
        respx.head("https://example.com/").mock(side_effect=refused)
        respx.head("https://fallback.com/").respond(200)
        moved = respx.post("https://fallback.com/search").respond(
            200, text="<p>hits</p>"
        )
        plugin = _TestPlugin()
        plugin._client = httpx.AsyncClient()
        plugin._domain_verified = True

        html = await plugin._fetch_search_page(
            "/search", method="POST", data={"q": "x"}
        )

        assert html == "<p>hits</p>"
        assert moved.calls.last.request.content == b"q=x"
        assert plugin.base_url == "https://fallback.com"


class TestSafeFetchRaising:
    """``_safe_fetch()``'s modes: lenient by default, ``raising=True`` for a
    search page."""

    @pytest.mark.parametrize(
        "failure",
        [httpx.Response(503), httpx.ConnectError("refused"), httpx.ReadTimeout("x")],
    )
    @respx.mock
    async def test_the_default_stays_lenient(
        self, failure: httpx.Response | Exception
    ) -> None:
        _route_fails(respx.get("https://only.com/page"), failure)
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        assert await plugin._safe_fetch("https://only.com/page") is None

    @respx.mock
    async def test_a_raising_read_timeout_is_a_timeout(self) -> None:
        respx.get("https://only.com/page").mock(side_effect=httpx.ReadTimeout("x"))
        plugin = _SingleDomainPlugin()
        plugin._client = httpx.AsyncClient()

        with pytest.raises(TimeoutError):
            await plugin._safe_fetch("https://only.com/page", raising=True)
