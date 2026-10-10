"""Tests for the shared CDN verification and the error-redirect rule."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
import structlog

from scavengarr.infrastructure.hoster_resolvers._verify import (
    is_error_redirect,
    verify_video_url,
)

_URL = "https://cdn.example.com/video.mp4"
_HEADERS = {"Referer": "https://example.com/"}
_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42"


async def _verify(url: str = _URL) -> bool:
    async with httpx.AsyncClient() as client:
        return await verify_video_url(client, url, _HEADERS, "test")


class TestVerifyVideoUrl:
    @respx.mock
    @pytest.mark.parametrize(
        ("status", "content_type"),
        [
            (200, "video/mp4"),
            (206, "application/octet-stream"),
            (200, "application/vnd.apple.mpegurl"),
            (200, ""),
        ],
    )
    async def test_a_media_answer_is_reachable(
        self, status: int, content_type: str
    ) -> None:
        headers = {"Content-Type": content_type} if content_type else {}
        respx.head(_URL).respond(status, headers=headers)
        assert await _verify()

    @respx.mock
    @pytest.mark.parametrize("status", [403, 404, 500, 503])
    async def test_an_error_status_is_dead(self, status: int) -> None:
        respx.head(_URL).respond(status)
        assert not await _verify()

    @respx.mock
    async def test_an_html_answer_is_dead(self) -> None:
        """A CDN serves a dead file as an error page with 200 (steps 40 and
        42, row 46): the page is no video."""
        respx.head(_URL).respond(
            200, headers={"Content-Type": "text/html; charset=utf-8"}
        )
        assert not await _verify()

    @respx.mock
    async def test_head_not_allowed_asks_for_the_first_bytes(self) -> None:
        respx.head(_URL).respond(405)
        get = respx.get(_URL).respond(
            206, content=_MP4_BYTES, headers={"Content-Type": "video/mp4"}
        )
        assert await _verify()
        sent = get.calls.last.request
        assert sent.headers["Range"].startswith("bytes=0-")
        assert sent.headers["Referer"] == "https://example.com/"

    @respx.mock
    @pytest.mark.parametrize(
        "answer",
        [
            {
                "status_code": 200,
                "content": b"<!DOCTYPE html><html>",
                "headers": {"Content-Type": "text/html"},
            },
            {"status_code": 200, "content": b"  <html><body>File not found"},
            {"status_code": 404},
        ],
        ids=["typed html", "untyped html", "error status"],
    )
    async def test_the_first_bytes_must_be_media(self, answer: dict[str, Any]) -> None:
        respx.head(_URL).respond(405)
        respx.get(_URL).respond(**answer)
        assert not await _verify()

    @respx.mock
    async def test_a_network_error_is_dead(self) -> None:
        respx.head(_URL).mock(side_effect=httpx.ConnectError("down"))
        assert not await _verify()

    @respx.mock
    async def test_sends_the_headers_and_follows_redirects(self) -> None:
        moved = "https://cdn.example.com/moved.mp4"
        respx.head(_URL).respond(302, headers={"Location": moved})
        final = respx.head(moved).respond(200, headers={"Content-Type": "video/mp4"})
        assert await _verify()
        sent = final.calls.last.request
        assert sent.headers["Referer"] == "https://example.com/"
        assert sent.extensions["timeout"]["read"] == 8.0

    @respx.mock
    async def test_a_failed_check_logs_the_cdn_not_its_url(self) -> None:
        """CDN URLs carry tokens and the client's address."""
        url = "https://cdn.example.com/secure/secret-token/v.mp4?i=1.2.3.4"
        respx.head(url).respond(200, headers={"Content-Type": "text/html"})

        with structlog.testing.capture_logs() as logs:
            await _verify(url)

        events = [e for e in logs if e["event"] == "test_video_not_media"]
        assert events and events[0]["cdn"] == "example"
        assert events[0]["content_type"] == "text/html"
        assert not any("secret-token" in str(v) for v in events[0].values())


class TestIsErrorRedirect:
    @pytest.mark.parametrize(
        "url",
        [
            "https://host.example/404",
            "https://host.example/404.html",
            "https://host.example/error",
            "https://host.example/error?code=1",
            "https://host.example/errors/not-found",
            "https://host.example/?error=file_removed",
        ],
    )
    def test_error_pages(self, url: str) -> None:
        assert is_error_redirect(url)

    @pytest.mark.parametrize(
        "url",
        [
            # "error" inside a title must not count ("The Terror")
            "https://katfile.com/abcdefghijkl/The.Terror.S01E01.mkv.html",
            "https://serienstream.to/serie/stream/the-terror",
            "https://host.example/e/abc404def",
            "https://host.example/v/Errors.and.Omissions.2019.mkv",
        ],
    )
    def test_file_pages(self, url: str) -> None:
        assert not is_error_redirect(url)
