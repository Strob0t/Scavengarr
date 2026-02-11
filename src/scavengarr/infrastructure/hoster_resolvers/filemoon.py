"""Filemoon hoster resolver — extracts HLS URLs from filemoon.sx embed pages.

Supports two architectures:
1. Byse SPA (new): Vite/React frontend that loads video sources via
   GET /api/videos/{id}/embed/details JSON API.
2. Legacy XFS: Packed JavaScript (Dean Edwards packer) containing JWPlayer config.

Filemoon domain variants: filemoon.sx, filemoon.to, filemoon.eu, etc.
"""

from __future__ import annotations

import re

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)


def _unpack_p_a_c_k(packed: str) -> str | None:
    """Unpack Dean Edwards packed JavaScript.

    Format: eval(function(p,a,c,k,e,d){...}('payload',base,count,'dict'.split('|')))

    The algorithm replaces base-N encoded tokens in the payload
    with words from the dictionary.
    """
    # Extract the parameters from the outer function call
    match = re.search(
        r"}\('(.*?)',\s*(\d+),\s*(\d+),\s*'([^']*)'\s*\.split\('\|'\)",
        packed,
        re.DOTALL,
    )
    if not match:
        return None

    payload = match.group(1)
    base = int(match.group(2))
    count = int(match.group(3))
    keywords = match.group(4).split("|")

    if len(keywords) < count:
        # Pad with empty strings if dictionary is short
        keywords.extend([""] * (count - len(keywords)))

    def _base_n(num: int, radix: int) -> str:
        """Convert integer to base-N string (supports up to base 36)."""
        if num < 0:
            return ""
        chars = "0123456789abcdefghijklmnopqrstuvwxyz"
        if num < radix:
            return chars[num]
        return _base_n(num // radix, radix) + chars[num % radix]

    def _replace_word(match: re.Match[str]) -> str:
        word = match.group(0)
        # Convert the base-N token back to an integer index
        try:
            index = int(word, base)
        except ValueError:
            return word
        if index < len(keywords) and keywords[index]:
            return keywords[index]
        return word

    # Replace all word-boundary tokens with dictionary entries
    result = re.sub(r"\b\w+\b", _replace_word, payload)
    return result


def _extract_hls_from_unpacked(js: str) -> str | None:
    """Extract HLS m3u8 URL from unpacked JWPlayer config.

    Handles both regular quotes and escaped quotes (\\' or \\")
    that appear in unpacked output.
    """
    # Normalize escaped quotes for easier matching
    normalized = js.replace("\\'", "'").replace('\\"', '"')

    # Pattern 1: sources:[{file:"https://...master.m3u8"}]
    match = re.search(
        r"""sources\s*:\s*\[\s*\{[^}]*file\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)""",
        normalized,
    )
    if match:
        return match.group(1)

    # Pattern 2: file:"https://...m3u8"
    match = re.search(
        r"""file\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)""",
        normalized,
    )
    if match:
        return match.group(1)

    # Pattern 3: source:"https://..." (any video URL)
    match = re.search(
        r"""(?:source|src)\s*:\s*["'](https?://[^"']+\.(?:m3u8|mp4)[^"']*)""",
        normalized,
    )
    if match:
        return match.group(1)

    return None


class FilemoonResolver:
    """Resolves Filemoon embed pages to playable HLS URLs.

    Supports filemoon.sx, filemoon.to and domain variants.
    Extracts HLS URL from packed JavaScript (Dean Edwards packer).
    """

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "filemoon"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Fetch Filemoon embed page and extract HLS URL."""
        embed_url = self._normalize_embed_url(url)

        # Method 0: Byse SPA API (new Filemoon architecture)
        result = await self._try_byse_api(embed_url)
        if result:
            return result

        # Fetch HTML for legacy extraction methods
        try:
            resp = await self._http.get(
                embed_url,
                follow_redirects=True,
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    ),
                },
            )
            if resp.status_code != 200:
                log.warning(
                    "filemoon_http_error",
                    status=resp.status_code,
                    url=embed_url,
                )
                return None

            html = resp.text
        except httpx.HTTPError:
            log.warning("filemoon_request_failed", url=embed_url)
            return None

        # Check offline markers
        if self._is_offline(html):
            log.info("filemoon_offline", url=url)
            return None

        # Method 1: Unpack packed JS blocks (legacy XFS)
        result = self._try_packed_js(html)
        if result:
            return result

        # Method 2: Direct HLS URL in page source (rare, but possible)
        result = self._try_direct_hls(html)
        if result:
            return result

        log.warning("filemoon_extraction_failed", url=url)
        return None

    async def _try_byse_api(self, url: str) -> ResolvedStream | None:
        """Extract video URL via Byse SPA API (new Filemoon architecture).

        Filemoon migrated to a Vite/React SPA ("Byse Frontend") that loads
        video sources from: GET /api/videos/{id}/embed/details
        """
        video_id = self._extract_video_id(url)
        if not video_id:
            return None

        base_match = re.match(r"(https?://[^/]+)", url)
        if not base_match:
            return None
        base_url = base_match.group(1)

        api_url = f"{base_url}/api/videos/{video_id}/embed/details"
        try:
            resp = await self._http.get(
                api_url,
                follow_redirects=True,
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    ),
                    "Referer": url,
                },
            )
            if resp.status_code != 200:
                log.debug(
                    "filemoon_byse_api_not_ok",
                    status=resp.status_code,
                    url=api_url,
                )
                return None

            data = resp.json()
        except Exception:  # noqa: BLE001
            log.debug("filemoon_byse_api_failed", url=api_url)
            return None

        return self._parse_byse_sources(data)

    def _extract_video_id(self, url: str) -> str:
        """Extract video ID from Filemoon URL (e.g., /e/abc123 -> abc123)."""
        match = re.search(r"/(?:e|d|download)/([a-z0-9]+)", url)
        return match.group(1) if match else ""

    def _parse_byse_sources(self, data: dict) -> ResolvedStream | None:  # type: ignore[type-arg]
        """Parse Byse API response for video sources."""
        sources = data.get("sources")
        if not isinstance(sources, list):
            # Try nested structure
            inner = data.get("data")
            if isinstance(inner, dict):
                sources = inner.get("sources")
            if not isinstance(sources, list):
                return None

        for source in sources:
            if not isinstance(source, dict):
                continue
            video_url = source.get("url") or source.get("file", "")
            if not video_url or not video_url.startswith("http"):
                continue
            mime = (source.get("mimeType") or source.get("type") or "").lower()
            is_hls = "mpegurl" in mime or ".m3u8" in video_url
            log.debug("filemoon_byse_api_success", url=video_url[:80])
            return ResolvedStream(
                video_url=video_url,
                is_hls=is_hls,
                quality=StreamQuality.UNKNOWN,
            )
        return None

    def _try_packed_js(self, html: str) -> ResolvedStream | None:
        """Extract HLS URL from packed JavaScript blocks.

        Uses a robust start-marker approach: find eval(function(p,a,c,k,e,d))
        start positions, extract a chunk, and let _unpack_p_a_c_k() handle
        parameter extraction. This avoids fragile full-block regex matching
        that breaks on real-world Filemoon page variations.
        """
        for m in re.finditer(
            r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*d\s*\)",
            html,
        ):
            # Extract a chunk large enough to contain the full packed block
            chunk = html[m.start() : m.start() + 65536]
            unpacked = _unpack_p_a_c_k(chunk)
            if not unpacked:
                continue

            hls_url = _extract_hls_from_unpacked(unpacked)
            if hls_url:
                log.debug("filemoon_packed_js_success", url=hls_url[:80])
                return ResolvedStream(
                    video_url=hls_url,
                    is_hls=True,
                    quality=StreamQuality.UNKNOWN,
                )

        return None

    def _try_direct_hls(self, html: str) -> ResolvedStream | None:
        """Look for HLS URL directly in page source."""
        match = re.search(
            r"""["'](https?://[^"']+\.m3u8[^"']*)["']""",
            html,
        )
        if match:
            url = match.group(1)
            if "thumbnail" not in url.lower() and "track" not in url.lower():
                log.debug("filemoon_direct_hls", url=url[:80])
                return ResolvedStream(
                    video_url=url,
                    is_hls=True,
                    quality=StreamQuality.UNKNOWN,
                )
        return None

    def _normalize_embed_url(self, url: str) -> str:
        """Ensure URL uses the /e/ embed format."""
        if "/e/" in url:
            return url
        # Convert /d/ or /download/ to /e/
        url = re.sub(r"/(?:d|download)/", "/e/", url)
        return url

    def _is_offline(self, html: str) -> bool:
        """Check for Filemoon offline/removed markers."""
        if "File Not Found" in html:
            return True
        if "file was deleted" in html.lower():
            return True
        if 'class="fake-signup"' in html:
            return True
        return False
