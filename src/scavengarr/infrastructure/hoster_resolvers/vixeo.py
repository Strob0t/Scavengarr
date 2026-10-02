"""Vixeo (vixeo.io) hoster resolver — Vidsonic's current player.

The embed page ``/e/{id}`` is a JavaScript app that builds the signed HLS URL
(``*.vidsonic.net/secure/.../index.m3u8``) only while it runs; the page source
carries neither the URL nor Vidsonic's older hex blob. The stealth browser
runs the player and captures the stream request (``capture_stream``; live:
~4 s). Playback needs no headers.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream
from scavengarr.infrastructure.hoster_resolvers import extract_domain
from scavengarr.infrastructure.hoster_resolvers._browser import capture_stream

if TYPE_CHECKING:
    from scavengarr.infrastructure.browser.stealth_pool import StealthPool

log = structlog.get_logger(__name__)

_DOMAINS = frozenset({"vixeo"})
_FILE_ID_RE = re.compile(r"^/e/([A-Za-z0-9]{8,})(?:/|$)")


def _extract_file_id(url: str) -> str | None:
    """Extract the video ID from a vixeo embed URL (other paths are not videos)."""
    if extract_domain(url) not in _DOMAINS:
        return None
    match = _FILE_ID_RE.search(urlparse(url).path)
    return match.group(1) if match else None


class VixeoResolver:
    """Resolves vixeo.io embed pages by running the player in the browser."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        stealth_pool: StealthPool | None = None,
    ) -> None:
        self._http = http_client
        self._stealth_pool = stealth_pool

    @property
    def name(self) -> str:
        return "vixeo"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Capture the stream the embed page's player requests."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.info("vixeo_invalid_url", url=url)
            return None
        return await capture_stream(
            self._stealth_pool, f"https://vixeo.io/e/{file_id}", "vixeo"
        )
