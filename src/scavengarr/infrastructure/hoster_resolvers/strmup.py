"""StreamUp (strmup) hoster resolver — extracts HLS video URLs.

Resolution strategy (from JD2 StreamupWs.java):
1. GET page with file ID
2. Extract ``streaming_url`` from page HTML
3. Fallback: AJAX ``/ajax/stream?filecode={id}`` → JSON ``streaming_url``
4. Return HLS master URL

Offline detection: HTTP 404 or blank page (< 100 characters).

Vidara (vidara.so/.to, vidaraa.cc, and kinoger.pw, kinoger's white-label
Vidara player) runs the same streaming backend behind a JSON API instead
(JD2 VidaraTo.java): ``POST /api/stream`` with
``{"device": "web", "filecode": id}`` → ``streaming_url``; 404 when gone.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain

log = structlog.get_logger(__name__)

_DOMAINS = frozenset({"strmup", "streamup", "vidara", "vidaraa", "kinoger"})
# Hosts served through the JSON API (JD2 VidaraTo); kinoger.pw is
# kinoger's own Vidara player ("Vidara" in its credits, same API)
_API_DOMAINS = frozenset({"vidara", "vidaraa", "kinoger"})

_FILE_ID_RE = re.compile(r"^/(?:e/|v/)?([A-Za-z0-9]{12,})(?:/|$)")


def _extract_file_id(url: str) -> str | None:
    """Extract 13-char file ID from a StreamUp URL."""
    try:
        domain = extract_domain(url)
        if domain not in _DOMAINS:
            return None
        parsed = urlparse(url)
        match = _FILE_ID_RE.search(parsed.path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


class StrmupResolver:
    """Resolves StreamUp/strmup embed pages to HLS video URLs."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "strmup"

    @property
    def supported_domains(self) -> frozenset[str]:
        """Mirror domains dispatched to this resolver by the registry."""
        return frozenset(_DOMAINS)

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Fetch StreamUp page and extract HLS master URL."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("strmup_invalid_url", url=url)
            return None

        # Determine base host from URL
        try:
            parsed = urlparse(url)
            host = parsed.hostname or "strmup.to"
            scheme = parsed.scheme or "https"
        except Exception:  # noqa: BLE001
            host = "strmup.to"
            scheme = "https"

        if extract_domain(url) in _API_DOMAINS:
            hls_master = await self._api_stream(scheme, host, file_id)
            return self._stream(hls_master, scheme, host, file_id)

        page_url = f"{scheme}://{host}/{file_id}"
        referer = f"{scheme}://{host}/v/{file_id}"

        try:
            resp = await self._http.get(
                page_url,
                follow_redirects=True,
                timeout=15,
                headers={"Referer": referer},
            )
        except httpx.HTTPError:
            log.warning("strmup_request_failed", url=url)
            return None

        if resp.status_code == 404:
            log.info("strmup_file_not_found", file_id=file_id)
            return None

        if resp.status_code != 200:
            log.warning("strmup_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text

        # Blank page = invalid file ID
        if len(html) <= 100:
            log.info("strmup_blank_page", file_id=file_id)
            return None

        # Method 1: Extract streaming_url from page HTML
        hls_master = self._extract_streaming_url(html)

        # Method 2: AJAX fallback
        if not hls_master:
            hls_master = await self._ajax_fallback(host, scheme, file_id)

        return self._stream(hls_master, scheme, host, file_id)

    def _stream(
        self, hls_master: str | None, scheme: str, host: str, file_id: str
    ) -> ResolvedStream | None:
        if not hls_master:
            log.warning("strmup_no_hls_url", file_id=file_id)
            return None

        log.debug("strmup_resolved", file_id=file_id, hls_url=hls_master)
        return ResolvedStream(
            video_url=hls_master,
            is_hls=True,
            quality=StreamQuality.UNKNOWN,
            headers={
                "Origin": f"{scheme}://{host}",
                "Referer": f"{scheme}://{host}/",
            },
        )

    async def _api_stream(self, scheme: str, host: str, file_id: str) -> str | None:
        """Vidara JSON API: ``streaming_url`` for *file_id*, None when gone."""
        try:
            resp = await self._http.post(
                f"{scheme}://{host}/api/stream",
                json={"device": "web", "filecode": file_id},
                timeout=15,
            )
        except httpx.HTTPError:
            log.warning("strmup_api_request_failed", host=host, file_id=file_id)
            return None
        if resp.status_code == 404:
            log.info("strmup_file_not_found", file_id=file_id)
            return None
        try:
            data = resp.json()
        except ValueError:
            log.warning("strmup_api_bad_json", status=resp.status_code)
            return None
        url = data.get("streaming_url") if isinstance(data, dict) else None
        return url if isinstance(url, str) and url.startswith("http") else None

    def _extract_streaming_url(self, html: str) -> str | None:
        """Extract streaming_url from page HTML."""
        match = re.search(r'streaming_url:\s*"(https?://[^"]+)"', html)
        if match:
            return match.group(1)
        # Alternative pattern with single quotes
        match = re.search(r"streaming_url:\s*'(https?://[^']+)'", html)
        if match:
            return match.group(1)
        return None

    async def _ajax_fallback(self, host: str, scheme: str, file_id: str) -> str | None:
        """Try AJAX endpoint to get streaming URL."""
        ajax_url = f"{scheme}://{host}/ajax/stream?filecode={file_id}"
        try:
            resp = await self._http.get(
                ajax_url,
                follow_redirects=True,
                timeout=10,
                headers={"Referer": f"{scheme}://{host}/"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            url = data.get("streaming_url")
            if url and isinstance(url, str) and url.startswith("http"):
                return url
        except Exception:  # noqa: BLE001
            log.debug("strmup_ajax_failed", file_id=file_id)
        return None
