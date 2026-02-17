"""Vidsonic hoster resolver — extracts HLS video URLs.

Resolution strategy (from page analysis):
1. GET embed page ``/e/{file_id}``
2. Extract hex-encoded pipe-delimited HLS URL from inline JS
3. Decode: remove pipes → hex-to-chars → reverse string → HLS master URL
4. Return HLS master URL

Offline detection: "Video Not Found" marker in page HTML.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain

log = structlog.get_logger(__name__)

_DOMAINS = frozenset({"vidsonic"})

_FILE_ID_RE = re.compile(r"/(?:e/|d/)?([a-z0-9]{12})(?:/|$)")

# Matches the hex pipe-delimited obfuscated URL string in page JS.
_HEX_BLOB_RE = re.compile(
    r"'((?:[0-9a-f]{2,}[|])+[0-9a-f]{2,})'",
)


def _extract_file_id(url: str) -> str | None:
    """Extract 12-char file ID from a Vidsonic URL."""
    try:
        domain = extract_domain(url)
        if domain not in _DOMAINS:
            return None
        parsed = urlparse(url)
        match = _FILE_ID_RE.search(parsed.path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


def _decode_hex_blob(blob: str) -> str | None:
    """Decode Vidsonic's obfuscated URL.

    The blob is pipe-delimited hex segments.  Decoding:
    1. Remove pipes → single hex string
    2. Convert each 2-hex-char pair to a character
    3. Reverse the entire result
    """
    try:
        hex_str = blob.replace("|", "")
        chars = [chr(int(hex_str[i : i + 2], 16)) for i in range(0, len(hex_str), 2)]
        decoded = "".join(chars)[::-1]
        if decoded.startswith("http"):
            return decoded
        return None
    except Exception:  # noqa: BLE001
        return None


class VidsonicResolver:
    """Resolves Vidsonic embed pages to HLS video URLs."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "vidsonic"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Fetch Vidsonic embed page and extract HLS master URL."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("vidsonic_invalid_url", url=url)
            return None

        try:
            parsed = urlparse(url)
            host = parsed.hostname or "vidsonic.net"
            scheme = parsed.scheme or "https"
        except Exception:  # noqa: BLE001
            host = "vidsonic.net"
            scheme = "https"

        embed_url = f"{scheme}://{host}/e/{file_id}"

        try:
            resp = await self._http.get(
                embed_url,
                follow_redirects=True,
                timeout=15,
            )
        except httpx.HTTPError:
            log.warning("vidsonic_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning(
                "vidsonic_http_error",
                status=resp.status_code,
                url=url,
            )
            return None

        html = resp.text

        if "Video Not Found" in html or "Video ID is required" in html:
            log.info("vidsonic_file_offline", file_id=file_id)
            return None

        # Extract hex-encoded blob from inline JS
        match = _HEX_BLOB_RE.search(html)
        if not match:
            log.warning("vidsonic_no_hex_blob", file_id=file_id)
            return None

        hls_url = _decode_hex_blob(match.group(1))
        if not hls_url:
            log.warning("vidsonic_decode_failed", file_id=file_id)
            return None

        log.debug("vidsonic_resolved", file_id=file_id, hls_url=hls_url)
        return ResolvedStream(
            video_url=hls_url,
            is_hls=True,
            quality=StreamQuality.UNKNOWN,
            headers={
                "Origin": f"{scheme}://{host}",
                "Referer": f"{scheme}://{host}/",
            },
        )
