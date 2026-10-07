"""Tests for HttpxPluginBase shared base class."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.domain.plugins.base import (
    PluginUnreachableError,
    SearchResult,
)
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
    async def test_first_domain_reachable(self) -> None:
        plugin = _TestPlugin()
        head_resp = _head_response("example.com")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=head_resp)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "example.com" in plugin.base_url

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
        assert "fallback.com" in plugin.base_url

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
