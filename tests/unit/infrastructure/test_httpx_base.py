"""Tests for HttpxPluginBase shared base class."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

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


class TestVerifyDomain:
    @pytest.mark.asyncio
    async def test_first_domain_reachable(self) -> None:
        plugin = _TestPlugin()
        head_resp = MagicMock()
        head_resp.status_code = 200

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=head_resp)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "example.com" in plugin.base_url

    @pytest.mark.asyncio
    async def test_fallback_to_second_domain(self) -> None:
        plugin = _TestPlugin()
        fail_resp = MagicMock()
        fail_resp.status_code = 503

        ok_resp = MagicMock()
        ok_resp.status_code = 200

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=[fail_resp, ok_resp])
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "fallback.com" in plugin.base_url

    @pytest.mark.asyncio
    async def test_all_domains_fail(self) -> None:
        plugin = _TestPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "example.com" in plugin.base_url  # falls back to primary

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
        resp = MagicMock(spec=httpx.Response)
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
        resp = MagicMock(spec=httpx.Response)
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
        resp = MagicMock(spec=httpx.Response)
        resp.json.return_value = {"data": [1, 2, 3]}
        resp.url = "https://example.com/api"

        result = plugin._safe_parse_json(resp)

        assert result == {"data": [1, 2, 3]}

    def test_returns_none_on_invalid_json(self) -> None:
        plugin = _TestPlugin()
        resp = MagicMock(spec=httpx.Response)
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
        resp = MagicMock(spec=httpx.Response)
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
    async def test_safe_fetch_no_overrides_with_own_client(self) -> None:
        plugin = _TestPlugin()
        resp = MagicMock(spec=httpx.Response)
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
