"""Tests for HttpLinkValidator."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT
from scavengarr.infrastructure.validation import http_link_validator
from scavengarr.infrastructure.validation.http_link_validator import (
    HttpLinkValidator,
    _ValidationCacheEntry,
)


def _get_stream(
    status_code: int = 200, side_effect: Exception | None = None
) -> MagicMock:
    """``client.stream("GET", ...)`` stand-in: an async context manager."""
    cm = MagicMock()
    if side_effect is not None:
        cm.__aenter__.side_effect = side_effect
    else:
        cm.__aenter__.return_value = MagicMock(status_code=status_code)
    cm.__aexit__.return_value = False
    return MagicMock(return_value=cm)


def _mock_client(
    status_code: int = 200,
    side_effect: Exception | None = None,
    get_status_code: int | None = None,
    get_side_effect: Exception | None = None,
) -> AsyncMock:
    """Create mock httpx.AsyncClient with HEAD and GET responses.

    By default GET mirrors HEAD (same status/side_effect) unless
    get_status_code or get_side_effect is explicitly provided.
    """
    client = AsyncMock(spec=httpx.AsyncClient)

    # HEAD mock
    if side_effect:
        client.head = AsyncMock(side_effect=side_effect)
    else:
        response = MagicMock()
        response.status_code = status_code
        client.head = AsyncMock(return_value=response)

    # GET mock (streamed) — mirrors HEAD by default, explicit get_* overrides
    if get_side_effect is not None:
        client.stream = _get_stream(side_effect=get_side_effect)
    elif get_status_code is not None:
        client.stream = _get_stream(get_status_code)
    elif side_effect:
        client.stream = _get_stream(side_effect=side_effect)
    else:
        client.stream = _get_stream(status_code)

    return client


class TestValidate:
    async def test_200_is_valid(self) -> None:
        client = _mock_client(status_code=200)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is True

    async def test_301_redirect_is_valid(self) -> None:
        client = _mock_client(status_code=301)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is True

    async def test_399_is_valid(self) -> None:
        client = _mock_client(status_code=399)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is True

    async def test_404_is_invalid(self) -> None:
        client = _mock_client(status_code=404, get_status_code=404)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    async def test_500_is_invalid(self) -> None:
        client = _mock_client(status_code=500, get_status_code=500)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    async def test_timeout_is_invalid(self) -> None:
        client = _mock_client(
            side_effect=httpx.TimeoutException("timeout"),
            get_side_effect=httpx.TimeoutException("timeout"),
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    async def test_http_error_is_invalid(self) -> None:
        client = _mock_client(
            side_effect=httpx.HTTPError("connection refused"),
            get_side_effect=httpx.HTTPError("connection refused"),
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    async def test_unexpected_error_is_invalid(self) -> None:
        client = _mock_client(
            side_effect=OSError("DNS failure"),
            get_side_effect=OSError("DNS failure"),
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    # --- HEAD/GET fallback tests ---

    async def test_head_403_get_200_is_valid(self) -> None:
        """Hoster blocks HEAD but allows GET."""
        client = _mock_client(status_code=403, get_status_code=200)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://veev.to/dl/1") is True

    async def test_head_403_get_403_is_invalid(self) -> None:
        """Both HEAD and GET fail — genuinely dead link."""
        client = _mock_client(status_code=403, get_status_code=403)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://dead.com/dl/1") is False

    async def test_head_405_get_200_is_valid(self) -> None:
        """405 Method Not Allowed on HEAD, GET works."""
        client = _mock_client(status_code=405, get_status_code=200)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://savefiles.com/dl/1") is True

    async def test_head_200_no_get_fallback(self) -> None:
        """HEAD succeeds — GET should not be called."""
        client = _mock_client(status_code=200)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is True
        client.stream.assert_not_called()

    async def test_head_timeout_get_200_is_valid(self) -> None:
        """HEAD times out, GET works."""
        client = _mock_client(
            side_effect=httpx.TimeoutException("timeout"),
            get_status_code=200,
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://slow-head.com/dl") is True

    async def test_head_timeout_get_timeout_is_invalid(self) -> None:
        """Both HEAD and GET timeout."""
        client = _mock_client(
            side_effect=httpx.TimeoutException("timeout"),
            get_side_effect=httpx.TimeoutException("timeout"),
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://unreachable.com") is False

    async def test_head_error_get_error_is_invalid(self) -> None:
        """Both HEAD and GET raise HTTPError."""
        client = _mock_client(
            side_effect=httpx.HTTPError("refused"),
            get_side_effect=httpx.HTTPError("refused"),
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://broken.com") is False


class TestValidateCache:
    async def test_valid_result_cached(self) -> None:
        """Second call for same URL uses cache, not HTTP."""
        client = _mock_client(status_code=200)
        validator = HttpLinkValidator(client)
        url = "https://cached.example.com"

        result1 = await validator.validate(url)
        assert result1 is True

        # Reset mock to prove cache is used
        client.head.reset_mock()
        client.stream.reset_mock()

        result2 = await validator.validate(url)
        assert result2 is True
        client.head.assert_not_called()

    async def test_invalid_result_cached(self) -> None:
        """Failed validation is also cached."""
        client = _mock_client(status_code=404, get_status_code=404)
        validator = HttpLinkValidator(client)
        url = "https://dead-cached.example.com"

        result1 = await validator.validate(url)
        assert result1 is False

        client.head.reset_mock()
        client.stream.reset_mock()

        result2 = await validator.validate(url)
        assert result2 is False
        client.head.assert_not_called()


class TestValidateBatch:
    async def test_empty_list_returns_empty_dict(self) -> None:
        client = _mock_client()
        validator = HttpLinkValidator(client)
        result = await validator.validate_batch([])
        assert result == {}

    async def test_all_valid(self) -> None:
        client = _mock_client(status_code=200)
        validator = HttpLinkValidator(client)
        urls = ["https://a.com", "https://b.com"]
        result = await validator.validate_batch(urls)
        assert result == {
            "https://a.com": True,
            "https://b.com": True,
        }

    async def test_mixed_results(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        head_responses = [MagicMock(status_code=200), MagicMock(status_code=404)]
        client.head = AsyncMock(side_effect=head_responses)
        # GET fallback for the 404 HEAD — also fails
        client.stream = _get_stream(404)

        validator = HttpLinkValidator(client)
        urls = ["https://valid.com", "https://dead.com"]
        result = await validator.validate_batch(urls)
        assert result["https://valid.com"] is True
        assert result["https://dead.com"] is False

    async def test_returns_dict_with_all_urls(self) -> None:
        client = _mock_client(status_code=200)
        validator = HttpLinkValidator(client)
        urls = ["https://a.com", "https://b.com", "https://c.com"]
        result = await validator.validate_batch(urls)
        assert set(result.keys()) == set(urls)

    async def test_deduplicates_urls(self) -> None:
        """Duplicate URLs are validated once, result propagated to all."""
        client = _mock_client(status_code=200)
        validator = HttpLinkValidator(client)
        urls = ["https://a.com", "https://a.com", "https://b.com"]
        result = await validator.validate_batch(urls)

        assert result == {
            "https://a.com": True,
            "https://b.com": True,
        }
        # HEAD should have been called only 2 times (not 3)
        assert client.head.call_count == 2

    async def test_non_http_urls_marked_invalid(self) -> None:
        """Garbage strings like 'http-equiv=' are marked invalid without HTTP."""
        client = _mock_client(status_code=200)
        validator = HttpLinkValidator(client)
        urls = [
            "http-equiv=",
            "javascript:void(0)",
            "https://valid.com",
        ]
        result = await validator.validate_batch(urls)
        assert result["http-equiv="] is False
        assert result["javascript:void(0)"] is False
        assert result["https://valid.com"] is True
        # Only the valid URL should trigger HEAD
        assert client.head.call_count == 1


class TestUnreachableHosts:
    """Connection-level failures: remember the host, stop hammering it.

    Hundreds of links on a dead hoster (uploaded.net, ul.to, ...) used to
    mean hundreds of connection attempts per search, each followed by a GET
    retry. Home routers read such bursts as a port scan and block the host.
    """

    async def test_connect_error_skips_get_fallback(self) -> None:
        client = _mock_client(side_effect=httpx.ConnectError("refused"))
        validator = HttpLinkValidator(client)

        assert await validator.validate("https://dead-host.example/a") is False
        client.stream.assert_not_called()

    async def test_connect_timeout_skips_get_fallback(self) -> None:
        client = _mock_client(side_effect=httpx.ConnectTimeout("timeout"))
        validator = HttpLinkValidator(client)

        assert await validator.validate("https://dead-host.example/a") is False
        client.stream.assert_not_called()

    async def test_unreachable_host_not_contacted_again(self) -> None:
        client = _mock_client(side_effect=httpx.ConnectError("refused"))
        validator = HttpLinkValidator(client)

        assert await validator.validate("https://dead-host.example/a") is False
        assert await validator.validate("https://dead-host.example/b") is False
        assert await validator.validate("http://dead-host.example/c") is False

        assert client.head.await_count == 1

    async def test_other_hosts_unaffected(self) -> None:
        client = _mock_client(status_code=200)
        dead = httpx.ConnectError("refused")
        ok = MagicMock(status_code=200)

        async def _head(url: str, **kwargs: object) -> object:
            if "dead-host" in url:
                raise dead
            return ok

        client.head = AsyncMock(side_effect=_head)
        validator = HttpLinkValidator(client)

        assert await validator.validate("https://dead-host.example/a") is False
        assert await validator.validate("https://alive.example/a") is True

    async def test_unreachable_host_expires(self, monkeypatch) -> None:
        from scavengarr.infrastructure.validation import http_link_validator as mod

        now = [1000.0]
        monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
        client = _mock_client(side_effect=httpx.ConnectError("refused"))
        validator = HttpLinkValidator(client)

        await validator.validate("https://dead-host.example/a")
        now[0] += mod._UNREACHABLE_HOST_MIN_TTL + 1
        await validator.validate("https://dead-host.example/b")

        assert client.head.await_count == 2

    async def test_short_outage_does_not_block_same_url_long(self, monkeypatch) -> None:
        """A network blip must not mark a link invalid for 15 minutes."""
        from scavengarr.infrastructure.validation import http_link_validator as mod

        now = [1000.0]
        monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
        client = _mock_client(side_effect=httpx.ConnectError("network down"))
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://voe.example/e/1") is False

        ok = MagicMock(status_code=200)
        client.head = AsyncMock(return_value=ok)
        now[0] += mod._UNREACHABLE_HOST_MIN_TTL + 1

        assert await validator.validate("https://voe.example/e/1") is True

    async def test_repeated_unreachable_backs_off(self, monkeypatch) -> None:
        from scavengarr.infrastructure.validation import http_link_validator as mod

        now = [1000.0]
        monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
        client = _mock_client(side_effect=httpx.ConnectError("refused"))
        validator = HttpLinkValidator(client)
        step = mod._UNREACHABLE_HOST_MIN_TTL + 1

        await validator.validate("https://dead-host.example/a")
        now[0] += step
        await validator.validate("https://dead-host.example/b")  # 2nd failure
        now[0] += step
        await validator.validate("https://dead-host.example/c")  # still skipped
        assert client.head.await_count == 2

        now[0] += step
        await validator.validate("https://dead-host.example/d")
        assert client.head.await_count == 3

    async def test_backoff_capped(self, monkeypatch) -> None:
        from scavengarr.infrastructure.validation import http_link_validator as mod

        now = [1000.0]
        monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
        client = _mock_client(side_effect=httpx.ConnectError("refused"))
        validator = HttpLinkValidator(client)

        for i in range(12):
            await validator.validate(f"https://dead-host.example/{i}")
            now[0] += mod._UNREACHABLE_HOST_MAX_TTL + 1

        assert client.head.await_count == 12

    async def test_reachable_again_resets_backoff(self, monkeypatch) -> None:
        from scavengarr.infrastructure.validation import http_link_validator as mod

        now = [1000.0]
        monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
        dead = httpx.ConnectError("refused")
        ok = MagicMock(status_code=200)
        client = _mock_client(side_effect=dead)
        validator = HttpLinkValidator(client)
        step = mod._UNREACHABLE_HOST_MIN_TTL + 1

        await validator.validate("https://host.example/a")
        now[0] += step
        await validator.validate("https://host.example/b")  # backoff doubles
        now[0] += 2 * step
        client.head = AsyncMock(return_value=ok)
        assert await validator.validate("https://host.example/c") is True

        client.head = AsyncMock(side_effect=dead)
        await validator.validate("https://host.example/d")
        now[0] += step
        await validator.validate("https://host.example/e")
        # back to the short skip: /e was contacted again
        assert client.head.await_count == 2

    async def test_batch_contacts_dead_host_at_most_per_host_limit(self) -> None:
        from scavengarr.infrastructure.validation import http_link_validator as mod

        client = _mock_client(side_effect=httpx.ConnectError("refused"))
        validator = HttpLinkValidator(client, max_concurrent=20)
        urls = [f"https://uploaded.example/file/{i}" for i in range(200)]

        result = await validator.validate_batch(urls)

        assert not any(result.values())
        # only the first wave (per-host limit) reaches the network
        assert client.head.await_count <= mod._MAX_CONCURRENT_PER_HOST

    async def test_http_status_failure_does_not_mark_host(self) -> None:
        client = _mock_client(status_code=404, get_status_code=404)
        validator = HttpLinkValidator(client)

        assert await validator.validate("https://host.example/a") is False
        assert await validator.validate("https://host.example/b") is False

        assert client.head.await_count == 2


class _TrackingStream(httpx.AsyncByteStream):
    """Response body that records whether anyone read it."""

    def __init__(self) -> None:
        self.read = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.read = True
        yield b"\0" * 1024

    async def aclose(self) -> None:
        pass


class TestGetFallbackBody:
    async def test_body_is_not_downloaded(self) -> None:
        """Only the status matters; the body can be a multi-GB file."""
        body = _TrackingStream()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                return httpx.Response(405)
            return httpx.Response(200, stream=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            validator = HttpLinkValidator(client)
            assert await validator.validate("https://files.example/big.mkv") is True

        assert body.read is False


class TestCachePruning:
    async def test_expired_entries_are_pruned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(http_link_validator, "_PRUNE_INTERVAL", 1)
        validator = HttpLinkValidator(_mock_client(status_code=200))
        validator._cache["https://old.example/x"] = _ValidationCacheEntry(True, 0)
        validator._unreachable_until["gone.example"] = time.monotonic() - 1

        await validator.validate("https://new.example/y")

        assert "https://old.example/x" not in validator._cache
        assert "https://new.example/y" in validator._cache
        assert "gone.example" not in validator._unreachable_until


class TestUserAgent:
    """HEAD and the GET fallback send a browser's User-Agent: hosters behind
    Cloudflare answer the shared client's bot User-Agent with a challenge,
    which would mark a live link dead."""

    @pytest.mark.asyncio
    async def test_head_sends_the_browser_user_agent(self) -> None:
        client = _mock_client(200)
        await HttpLinkValidator(client).validate("https://example.com/file")
        headers = client.head.await_args.kwargs["headers"]
        assert headers["User-Agent"] == DEFAULT_USER_AGENT

    @pytest.mark.asyncio
    async def test_the_get_fallback_sends_it_too(self) -> None:
        client = _mock_client(405, get_status_code=200)
        await HttpLinkValidator(client).validate("https://example.com/file")
        headers = client.stream.call_args.kwargs["headers"]
        assert headers["User-Agent"] == DEFAULT_USER_AGENT
