"""VOE hoster resolver — extracts playable video URLs from voe.sx embed pages.

Extraction methods (tried in order, matching JD2 VoeSx.java + voe-dl):
1. MKGMa/application-json deobfuscation chain (ROT13 → token replace →
   base64 → char shift → reverse → base64 → JSON)
2. Direct mp4/hls regex in page source
3. Base64-encoded hls value
4. Base64-encoded variables (wc0, ey*, hex-named vars)
"""

from __future__ import annotations

import base64
import json
import re
from urllib.parse import urljoin

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Bait/ad URL patterns to filter out
_BAIT_PATTERNS = re.compile(
    r"(banner|track|metric|pixel|adserv|analytics)", re.IGNORECASE
)

# JS redirect pattern: voe.sx now returns a redirect page instead of the embed
_JS_REDIRECT_RE = re.compile(
    r"window\.location\.href\s*=\s*['\"]"
    r"(https?://(?!.*voe\.sx)[^'\"]+)['\"]"
)

# Token array pattern — 5-8 short separator tokens (e.g. ['@$','^^','~@',...])
_TOKEN_ARRAY_RE = re.compile(r"=\s*(\[\s*(?:['\"][^'\"]{2,}['\"]\s*,?){5,8}\])\s*,")


def _rot13(text: str) -> str:
    """Apply ROT13 transformation."""
    result: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0x41 <= code <= 0x5A:  # A-Z
            code = (code - 0x41 + 13) % 26 + 0x41
        elif 0x61 <= code <= 0x7A:  # a-z
            code = (code - 0x61 + 13) % 26 + 0x61
        result.append(chr(code))
    return "".join(result)


def _replace_tokens(text: str, tokens: list[str]) -> str:
    """Replace token strings with underscores (JD2 _0x2e9c5e)."""
    for token in tokens:
        escaped = re.escape(token)
        text = re.sub(escaped, "_", text)
    return text


def _char_shift(text: str, shift: int) -> str:
    """Shift each character code by -shift (JD2 _0x533e0a)."""
    return "".join(chr(ord(ch) - shift) for ch in text)


def _b64decode(data: str) -> str:
    """Decode base64 with padding fix."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.b64decode(data).decode("utf-8", errors="replace")


def _deobfuscate_mkgma(encoded: str, tokens: list[str]) -> dict | None:
    """Full MKGMa deobfuscation chain (JD2 _0x43551c).

    Chain: ROT13 → token replace → remove underscores → base64 →
           char shift(-3) → reverse → base64 → JSON parse.
    """
    try:
        step1 = _rot13(encoded)
        step2 = _replace_tokens(step1, tokens)
        step3 = step2.replace("_", "")
        step4 = _b64decode(step3)
        step5 = _char_shift(step4, 3)
        step6 = step5[::-1]
        step7 = _b64decode(step6)
        return json.loads(step7)
    except Exception:
        log.debug("voe_mkgma_deobfuscation_failed")
        return None


def _extract_tokens(text: str) -> list[str] | None:
    """Extract replacement token array from page HTML or script content."""
    match = _TOKEN_ARRAY_RE.search(text)
    if match:
        try:
            raw = match.group(1).replace("'", '"')
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _parse_video_json(data: dict) -> str | None:
    """Extract video URL from VOE JSON structure.

    Prefers HLS master, falls back to mp4 from fallbacks array.
    """
    # HLS master playlist
    hls = data.get("file") or data.get("source")
    if hls and isinstance(hls, str) and hls.startswith("http"):
        return hls

    # MP4 fallback
    fallbacks = data.get("fallbacks")
    if isinstance(fallbacks, list) and fallbacks:
        mp4 = fallbacks[0].get("file") if isinstance(fallbacks[0], dict) else None
        if mp4 and isinstance(mp4, str) and mp4.startswith("http"):
            return mp4

    return None


def _is_valid_video_url(url: str) -> bool:
    """Check if URL looks like a real video URL (not bait/ad)."""
    if not url.startswith("http"):
        return False
    return not _BAIT_PATTERNS.search(url)


class VoeResolver:
    """Resolves VOE embed pages to playable video URLs.

    Supports voe.sx and domain variants.
    """

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "voe"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Fetch VOE embed page and extract video URL."""
        html, embed_url = await self._fetch_embed_page(url)
        if html is None or embed_url is None:
            return None

        # Headers that the video CDN requires for playback
        playback_headers = {"Referer": embed_url}

        # Try extraction methods in priority order
        methods: list[tuple[str, ResolvedStream | None]] = [
            ("mkgma", await self._try_mkgma(html, embed_url)),
            ("direct_regex", self._try_direct_regex(html)),
            ("b64_hls", self._try_b64_hls(html)),
            ("b64_vars", self._try_b64_vars(html)),
        ]

        for method_name, result in methods:
            if result is None:
                continue
            stream = ResolvedStream(
                video_url=result.video_url,
                is_hls=result.is_hls,
                quality=result.quality,
                headers=playback_headers,
            )
            if not await self._verify_video_url(stream.video_url, playback_headers):
                log.warning(
                    "voe_video_unreachable",
                    method=method_name,
                    url=stream.video_url[:120],
                )
                return None
            return stream

        log.warning("voe_all_methods_failed", url=url)
        return None

    async def _verify_video_url(self, url: str, headers: dict[str, str]) -> bool:
        """HEAD-check the CDN URL to verify it is accessible."""
        try:
            resp = await self._http.head(
                url, headers=headers, follow_redirects=True, timeout=8.0
            )
            if resp.status_code in (200, 206):
                return True
            log.warning(
                "voe_video_head_failed",
                status=resp.status_code,
                url=url[:120],
            )
            return False
        except httpx.HTTPError:
            log.warning("voe_video_verify_error", url=url[:120])
            return False

    async def _fetch_embed_page(self, url: str) -> tuple[str | None, str | None]:
        """Fetch the actual embed page, following JS redirects if needed."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            ),
        }
        try:
            resp = await self._http.get(
                url, follow_redirects=True, timeout=15, headers=headers
            )
            if resp.status_code != 200:
                log.warning("voe_http_error", status=resp.status_code, url=url)
                return None, None

            html = resp.text
            embed_url = str(resp.url)
        except httpx.HTTPError:
            log.warning("voe_request_failed", url=url)
            return None, None

        # VOE now returns a JS redirect page (window.location.href = '...')
        # instead of the actual embed. Detect and follow it.
        js_match = _JS_REDIRECT_RE.search(html)
        if js_match and "<title>Redirecting" in html:
            redirect_url = js_match.group(1)
            log.debug("voe_js_redirect", target=redirect_url)
            try:
                resp = await self._http.get(
                    redirect_url,
                    follow_redirects=True,
                    timeout=15,
                    headers=headers,
                )
                if resp.status_code != 200:
                    log.warning(
                        "voe_redirect_error",
                        status=resp.status_code,
                        url=redirect_url,
                    )
                    return None, None
                html = resp.text
                embed_url = str(resp.url)
            except httpx.HTTPError:
                log.warning("voe_redirect_failed", url=redirect_url)
                return None, None

        return html, embed_url

    async def _try_mkgma(self, html: str, embed_url: str) -> ResolvedStream | None:
        """Method 1: MKGMa or application/json deobfuscation."""
        encoded = None

        # Try MKGMa variable
        match = re.search(r"MKGMa\s*=\s*[\"'](.*?)[\"']", html)
        if match:
            encoded = match.group(1)

        # Try application/json script tag
        if encoded is None:
            match = re.search(
                r'<script[^>]*type\s*=\s*"application/json\s*"[^>]*>\s*\[\s*"(.*?)"',
                html,
            )
            if match:
                encoded = match.group(1)

        if encoded is None:
            return None

        # Try extracting tokens from the HTML first
        tokens = _extract_tokens(html)

        # If not found, try the external loader script
        if tokens is None:
            tokens = await self._fetch_loader_tokens(html, embed_url)

        if tokens is None:
            log.debug("voe_mkgma_no_tokens")
            return None

        data = _deobfuscate_mkgma(encoded, tokens)
        if data is None:
            return None

        video_url = _parse_video_json(data)
        if video_url and _is_valid_video_url(video_url):
            is_hls = ".m3u8" in video_url or "/hls" in video_url
            log.debug("voe_mkgma_success", is_hls=is_hls)
            return ResolvedStream(
                video_url=video_url,
                is_hls=is_hls,
                quality=StreamQuality.UNKNOWN,
            )
        return None

    async def _fetch_loader_tokens(self, html: str, embed_url: str) -> list[str] | None:
        """Fetch the external loader.js script to extract token array.

        VOE moved the MKGMa replacement tokens from inline HTML to an
        external ``/js/loader.*.js`` script.
        """
        match = re.search(r'src="(/js/loader\.[^"]+)"', html)
        if not match:
            return None

        # Build absolute URL from embed page origin
        loader_url = urljoin(embed_url, match.group(1))
        log.debug("voe_fetching_loader", url=loader_url)

        try:
            resp = await self._http.get(
                loader_url,
                follow_redirects=True,
                timeout=10,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    ),
                    "Referer": embed_url,
                },
            )
            if resp.status_code != 200:
                return None
            return _extract_tokens(resp.text)
        except httpx.HTTPError:
            log.debug("voe_loader_fetch_failed", url=loader_url)
            return None

    def _try_direct_regex(self, html: str) -> ResolvedStream | None:
        """Method 2: Direct mp4/hls key in page source."""
        # Try mp4 key
        match = re.search(r"""['"]mp4['"]\s*:\s*['"]?(https?://[^"'\s]+)""", html)
        if match and _is_valid_video_url(match.group(1)):
            return ResolvedStream(video_url=match.group(1))

        # Try hls key
        match = re.search(r"""['"]hls['"]\s*:\s*['"]?(https?://[^"'\s]+)""", html)
        if match and _is_valid_video_url(match.group(1)):
            return ResolvedStream(video_url=match.group(1), is_hls=True)

        # Try /engine/hls URL
        match = re.search(r'"(https?://[^/]+/engine/hls[^"]+)"', html)
        if match and _is_valid_video_url(match.group(1)):
            return ResolvedStream(video_url=match.group(1), is_hls=True)

        return None

    def _try_b64_hls(self, html: str) -> ResolvedStream | None:
        """Method 3: Base64-encoded hls value."""
        match = re.search(r"'hls'\s*:\s*'(aHR0[^']+)", html)
        if match:
            try:
                decoded = _b64decode(match.group(1))
                if _is_valid_video_url(decoded):
                    return ResolvedStream(video_url=decoded, is_hls=True)
            except Exception:
                pass
        return None

    def _try_b64_vars(self, html: str) -> ResolvedStream | None:
        """Method 4: Base64-encoded variables (wc0, ey*, hex-named)."""
        patterns = [
            r"(?:var|let|const)\s*wc0\s*=\s*'([^']+)",
            r"(?:var|let|const)\s*[^=]+\s*=\s*'(ey[^']+)",
            r"(?:var|let|const)\s*[a-f0-9]+\s*=\s*'([^']+)",
            r"""['"]hls['"]\s*:\s*['"]([^"']+)""",
        ]

        for pattern in patterns:
            match = re.search(pattern, html)
            if not match:
                continue

            raw = match.group(1)
            try:
                decoded = _b64decode(raw)
            except Exception:
                continue

            # If starts with "}", reverse it first
            if decoded.startswith("}"):
                decoded = decoded[::-1]

            if decoded.startswith("http") and _is_valid_video_url(decoded):
                is_hls = ".m3u8" in decoded or "/hls" in decoded
                return ResolvedStream(video_url=decoded, is_hls=is_hls)

            # Try as JSON
            try:
                data = json.loads(decoded)
                if isinstance(data, dict):
                    video_url = _parse_video_json(data)
                    if video_url and _is_valid_video_url(video_url):
                        is_hls = ".m3u8" in video_url or "/hls" in video_url
                        return ResolvedStream(
                            video_url=video_url,
                            is_hls=is_hls,
                        )
            except (json.JSONDecodeError, ValueError):
                continue

        return None
