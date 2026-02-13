"""Tests for hoster embed URL liveness probe."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.probe import (
    _OFFLINE_MARKERS,
    _OFFLINE_STATUS_CODES,
    _is_error_redirect,
    probe_url,
    probe_urls,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_URL = "https://voe.sx/e/abc123"


def _mock_response(
    *,
    status_code: int = 200,
    text: str = "<html><body>Player</body></html>",
    url: str = _TEST_URL,
) -> httpx.Response:
    """Build a fake httpx.Response for probe testing.

    The ``url`` parameter simulates the final URL after redirects.
    httpx.Response.url reads from ``request.url``, so we set the
    request URL to the final redirect destination.
    """
    return httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("GET", url),
    )


def _mock_client(
    *,
    status_code: int = 200,
    text: str = "<html><body>Player</body></html>",
    url: str = _TEST_URL,
    side_effect: Exception | None = None,
) -> httpx.AsyncClient:
    """Build a mock httpx.AsyncClient that returns a fixed response."""
    client = AsyncMock(spec=httpx.AsyncClient)
    if side_effect is not None:
        client.get = AsyncMock(side_effect=side_effect)
    else:
        client.get = AsyncMock(
            return_value=_mock_response(
                status_code=status_code,
                text=text,
                url=url,
            )
        )
    return client


# ---------------------------------------------------------------------------
# _is_error_redirect
# ---------------------------------------------------------------------------


class TestIsErrorRedirect:
    def test_404_in_path(self) -> None:
        assert _is_error_redirect("https://example.com/404") is True

    def test_error_in_path(self) -> None:
        assert _is_error_redirect("https://example.com/error") is True

    def test_normal_url(self) -> None:
        assert _is_error_redirect("https://voe.sx/e/abc123") is False

    def test_404_remove(self) -> None:
        assert _is_error_redirect("https://katfile.com/404-remove") is True


# ---------------------------------------------------------------------------
# probe_url — HTTP status codes
# ---------------------------------------------------------------------------


class TestProbeUrlStatusCodes:
    async def test_404_returns_false(self) -> None:
        client = _mock_client(status_code=404)
        assert await probe_url(client, _TEST_URL) is False

    async def test_410_returns_false(self) -> None:
        client = _mock_client(status_code=410)
        assert await probe_url(client, _TEST_URL) is False

    async def test_500_returns_false(self) -> None:
        client = _mock_client(status_code=500)
        assert await probe_url(client, _TEST_URL) is False

    async def test_200_clean_page_returns_true(self) -> None:
        client = _mock_client(status_code=200)
        assert await probe_url(client, _TEST_URL) is True

    async def test_403_not_treated_as_offline(self) -> None:
        """403 is excluded because Cloudflare returns 403 for challenges."""
        assert 403 not in _OFFLINE_STATUS_CODES
        client = _mock_client(status_code=403)
        # 403 != 200, so it returns False (unexpected status), but NOT
        # because it's in _OFFLINE_STATUS_CODES
        assert await probe_url(client, _TEST_URL) is False

    async def test_301_redirect_followed_to_200(self) -> None:
        """Redirects are followed; probe checks final response."""
        client = _mock_client(status_code=200, url="https://voe.sx/e/final")
        assert await probe_url(client, _TEST_URL) is True


# ---------------------------------------------------------------------------
# probe_url — Offline markers
# ---------------------------------------------------------------------------


class TestProbeUrlOfflineMarkers:
    async def test_file_not_found(self) -> None:
        client = _mock_client(text="<h1>File Not Found</h1>")
        assert await probe_url(client, _TEST_URL) is False

    async def test_fake_signup_supervideo(self) -> None:
        client = _mock_client(text='<div class="fake-signup">Sign up</div>')
        assert await probe_url(client, _TEST_URL) is False

    async def test_empty_doodstream_iframe(self) -> None:
        client = _mock_client(text='<iframe src="/e/"/>')
        assert await probe_url(client, _TEST_URL) is False

    async def test_doodstream_oops_sorry(self) -> None:
        client = _mock_client(text="<h1>Oops! Sorry</h1>")
        assert await probe_url(client, _TEST_URL) is False

    async def test_video_not_found(self) -> None:
        client = _mock_client(text="<p>Video not found</p>")
        assert await probe_url(client, _TEST_URL) is False

    async def test_streamtape_video_not_found(self) -> None:
        client = _mock_client(text="<span>Video not found</span>")
        assert await probe_url(client, _TEST_URL) is False

    async def test_file_deleted(self) -> None:
        client = _mock_client(text="<p>Sorry, this file was deleted</p>")
        assert await probe_url(client, _TEST_URL) is False

    async def test_file_removed(self) -> None:
        client = _mock_client(text="<p>This file was removed</p>")
        assert await probe_url(client, _TEST_URL) is False

    async def test_copyright_ban(self) -> None:
        client = _mock_client(
            text="<p>This file was banned by copyright holder</p>"
        )
        assert await probe_url(client, _TEST_URL) is False

    async def test_maintenance_mode(self) -> None:
        client = _mock_client(
            text="<p>This server is in maintenance mode</p>"
        )
        assert await probe_url(client, _TEST_URL) is False

    async def test_file_expired(self) -> None:
        client = _mock_client(text="<p>The file expired</p>")
        assert await probe_url(client, _TEST_URL) is False

    async def test_deleted_by_owner(self) -> None:
        client = _mock_client(
            text="<p>The file was deleted by its owner</p>"
        )
        assert await probe_url(client, _TEST_URL) is False

    async def test_rapidgator_404(self) -> None:
        client = _mock_client(
            text="<div class='error'>404 File not found</div>"
        )
        assert await probe_url(client, _TEST_URL) is False

    async def test_file_unavailable(self) -> None:
        client = _mock_client(text="<p>File unavailable</p>")
        assert await probe_url(client, _TEST_URL) is False

    async def test_file_is_gone(self) -> None:
        client = _mock_client(text="<p>File is gone</p>")
        assert await probe_url(client, _TEST_URL) is False

    async def test_file_not_available(self) -> None:
        client = _mock_client(
            text="<p>This file is not available anymore</p>"
        )
        assert await probe_url(client, _TEST_URL) is False

    async def test_voe_server_overloaded(self) -> None:
        client = _mock_client(
            text="<p>Server overloaded, download temporary disabled</p>"
        )
        assert await probe_url(client, _TEST_URL) is False

    async def test_voe_access_restricted(self) -> None:
        client = _mock_client(
            text="<p>Access to this file has been temporarily restricted</p>"
        )
        assert await probe_url(client, _TEST_URL) is False

    async def test_404_remove_in_page(self) -> None:
        client = _mock_client(text='<a href="/404-remove">removed</a>')
        assert await probe_url(client, _TEST_URL) is False

    async def test_not_found_h1(self) -> None:
        client = _mock_client(text="<h1>Not Found</h1>")
        assert await probe_url(client, _TEST_URL) is False

    async def test_file_looking_for_not_found(self) -> None:
        client = _mock_client(
            text="<p>File you are looking for is not found</p>"
        )
        assert await probe_url(client, _TEST_URL) is False

    async def test_the_file_was_deleted(self) -> None:
        """DDownload-specific 'The file was deleted' marker."""
        client = _mock_client(text="<p>The file was deleted</p>")
        assert await probe_url(client, _TEST_URL) is False


# ---------------------------------------------------------------------------
# probe_url — Redirect detection
# ---------------------------------------------------------------------------


class TestProbeUrlRedirects:
    async def test_redirect_to_404_page(self) -> None:
        client = _mock_client(url="https://katfile.com/404")
        assert await probe_url(client, _TEST_URL) is False

    async def test_redirect_to_error_page(self) -> None:
        client = _mock_client(url="https://ddownload.com/error")
        assert await probe_url(client, _TEST_URL) is False

    async def test_redirect_to_valid_page(self) -> None:
        client = _mock_client(url="https://voe.sx/e/redirected")
        assert await probe_url(client, _TEST_URL) is True


# ---------------------------------------------------------------------------
# probe_url — Error handling
# ---------------------------------------------------------------------------


class TestProbeUrlErrors:
    async def test_http_connection_error(self) -> None:
        client = _mock_client(side_effect=httpx.ConnectError("refused"))
        assert await probe_url(client, _TEST_URL) is False

    async def test_http_timeout_error(self) -> None:
        client = _mock_client(
            side_effect=httpx.ReadTimeout("timeout")
        )
        assert await probe_url(client, _TEST_URL) is False

    async def test_generic_http_error(self) -> None:
        client = _mock_client(side_effect=httpx.HTTPError("generic"))
        assert await probe_url(client, _TEST_URL) is False


# ---------------------------------------------------------------------------
# probe_url — Marker coverage
# ---------------------------------------------------------------------------


class TestOfflineMarkerCoverage:
    """Verify each marker in _OFFLINE_MARKERS triggers a False result."""

    @pytest.mark.parametrize("marker", _OFFLINE_MARKERS)
    async def test_each_marker_triggers_offline(self, marker: str) -> None:
        html = f"<html><body>{marker}</body></html>"
        client = _mock_client(text=html)
        assert await probe_url(client, _TEST_URL) is False


# ---------------------------------------------------------------------------
# probe_urls — Parallel probing
# ---------------------------------------------------------------------------


class TestProbeUrls:
    async def test_parallel_probing_mixed_results(self) -> None:
        """5 URLs: indices 0,2,4 alive, 1,3 dead → {0,2,4} returned."""
        client = AsyncMock(spec=httpx.AsyncClient)

        responses = {
            "https://a.com/e/1": _mock_response(url="https://a.com/e/1"),
            "https://b.com/e/2": _mock_response(
                status_code=404, url="https://b.com/e/2"
            ),
            "https://c.com/e/3": _mock_response(url="https://c.com/e/3"),
            "https://d.com/e/4": _mock_response(
                text="File Not Found", url="https://d.com/e/4"
            ),
            "https://e.com/e/5": _mock_response(url="https://e.com/e/5"),
        }

        async def _fake_get(url: str, **_kw: object) -> httpx.Response:
            return responses[url]

        client.get = AsyncMock(side_effect=_fake_get)

        urls = [
            (0, "https://a.com/e/1"),
            (1, "https://b.com/e/2"),
            (2, "https://c.com/e/3"),
            (3, "https://d.com/e/4"),
            (4, "https://e.com/e/5"),
        ]
        alive = await probe_urls(client, urls, concurrency=5, timeout=5)
        assert alive == {0, 2, 4}

    async def test_empty_list_returns_empty_set(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        alive = await probe_urls(client, [])
        assert alive == set()

    async def test_all_dead_returns_empty_set(self) -> None:
        client = _mock_client(status_code=404)
        urls = [(0, _TEST_URL), (1, _TEST_URL)]
        alive = await probe_urls(client, urls)
        assert alive == set()

    async def test_all_alive_returns_all_indices(self) -> None:
        client = _mock_client(status_code=200)
        urls = [(0, _TEST_URL), (1, _TEST_URL), (2, _TEST_URL)]
        alive = await probe_urls(client, urls)
        assert alive == {0, 1, 2}

    async def test_concurrency_bounded(self) -> None:
        """Verify semaphore limits parallel probes."""
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def _slow_get(url: str, **_kw: object) -> httpx.Response:
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
            await asyncio.sleep(0.01)
            async with lock:
                current_concurrent -= 1
            return _mock_response()

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=_slow_get)

        urls = [(i, f"https://example.com/{i}") for i in range(10)]
        await probe_urls(client, urls, concurrency=3, timeout=5)

        assert max_concurrent <= 3
