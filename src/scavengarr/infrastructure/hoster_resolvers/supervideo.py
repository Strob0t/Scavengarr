"""SuperVideo hoster resolver — XFS-based video extraction.

SuperVideo uses XFileSharingPro framework which embeds video URLs
via JWPlayer sources or HTML5 video tags.
Based on JD2 SupervideoTv.java (XFileSharingProBasic).

Strategy: httpx-first (fast, stateless), Playwright-fallback on Cloudflare 403.
"""

from __future__ import annotations

import asyncio
import re

import httpx
import structlog
from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    async_playwright,
)

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5",
}


def _is_cloudflare_block(status_code: int, html: str) -> bool:
    """Detect Cloudflare block/challenge from HTTP response.

    Cloudflare uses several block types:
    - JS challenge: 503 + "Just a moment" / "challenge-platform"
    - WAF block:    403 + "Attention Required" / "cf-error-details"
    - Turnstile:    403 + "Just a moment" / "challenge-platform"
    """
    if status_code not in (403, 503):
        return False
    cf_markers = ("Just a moment", "challenge-platform", "cf-error-details")
    return any(marker in html for marker in cf_markers)


def _extract_jwplayer_source(html: str) -> str | None:
    """Extract video URL from JWPlayer sources config.

    Matches patterns like:
        sources: [{file:"https://cdn.example.com/video.mp4"}]
        sources:[{file:"https://..."}]
        {file: "https://...", label: "720p"}
    """
    # JWPlayer sources array
    match = re.search(
        r"""sources\s*:\s*\[\s*\{[^}]*file\s*:\s*["'](https?://[^"']+)""",
        html,
    )
    if match:
        return match.group(1)

    # Alternative: source/file property (via : or =)
    match = re.search(
        r"""(?:source|file)\s*[:=]\s*["'](https?://[^"']+\.(?:mp4|m3u8)[^"']*)""",
        html,
    )
    if match:
        return match.group(1)

    return None


def _extract_html5_video(html: str) -> str | None:
    """Extract video URL from HTML5 <video> or <source> tags."""
    match = re.search(
        r"""<source[^>]+src\s*=\s*["'](https?://[^"']+)""",
        html,
    )
    if match:
        return match.group(1)

    match = re.search(
        r"""<video[^>]+src\s*=\s*["'](https?://[^"']+)""",
        html,
    )
    if match:
        return match.group(1)

    return None


def _unpack_p_a_c_k(packed_js: str) -> str | None:
    """Decode eval(function(p,a,c,k,e,d){...}) packed JavaScript.

    XFS sites use Dean Edwards' packer. The encoded body uses base-N
    number tokens that map to a word list via split('|').
    """
    # Extract body template, base, count, and tokens
    match = re.search(
        r"\(\s*'((?:[^'\\]|\\.)*)',\s*(\d+),\s*(\d+),\s*'((?:[^'\\]|\\.)*)'\.split",
        packed_js,
        re.DOTALL,
    )
    if not match:
        return None

    body = match.group(1)
    base = int(match.group(2))
    tokens = match.group(4).split("|")

    if base < 2 or base > 36:
        return None

    import string

    chars = (string.digits + string.ascii_lowercase)[:base]
    pattern = r"\b[" + re.escape(chars) + r"]+\b"

    def replacer(m: re.Match[str]) -> str:
        word = m.group(0)
        try:
            idx = int(word, base)
        except ValueError:
            return word
        return tokens[idx] if idx < len(tokens) and tokens[idx] else word

    return re.sub(pattern, replacer, body)


def _extract_packed_eval(html: str) -> str | None:
    """Extract video URL from eval(function(p,a,c,k,e,d) packed JS.

    Some XFS sites pack their JWPlayer config in eval() blocks.
    We decode the packed JS and then search for video URLs.
    """
    # Find packed JS block
    match = re.search(
        r"eval\(function\(p,a,c,k,e,d\)\{.*?\.split\('\|'\)\)\)",
        html,
        re.DOTALL,
    )
    if not match:
        return None

    packed = match.group(0)

    # Try direct URL match first (some packed blocks contain literal URLs)
    url_match = re.search(
        r"(https?://[^\s\"'\\]+\.(?:mp4|m3u8)[^\s\"'\\]*)",
        packed,
    )
    if url_match:
        return url_match.group(1)

    # Decode the packed JS and search in the unpacked output
    unpacked = _unpack_p_a_c_k(packed)
    if not unpacked:
        return None

    # Look for file:"..." pattern (JWPlayer sources in unpacked JS)
    file_match = re.search(
        r"""file\s*:\s*["'](https?://[^"']+\.(?:mp4|m3u8)[^"']*)""",
        unpacked,
    )
    if file_match:
        return file_match.group(1)

    # Fallback: any video URL in unpacked content
    url_match = re.search(
        r"(https?://[^\s\"'<>]+\.(?:mp4|m3u8)[^\s\"'<>]*)",
        unpacked,
    )
    if url_match:
        return url_match.group(1)

    return None


class SuperVideoResolver:
    """Resolves SuperVideo embed pages to playable video URLs.

    Supports supervideo.cc, supervideo.tv.
    Uses httpx by default; falls back to Playwright when Cloudflare
    JS challenge is detected (403 + "Just a moment").
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        playwright_headless: bool = True,
        playwright_timeout_ms: int = 30_000,
    ) -> None:
        self._http = http_client
        self._headless = playwright_headless
        self._timeout_ms = playwright_timeout_ms
        # Lazy Playwright state
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._browser_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "supervideo"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Fetch SuperVideo embed page and extract video URL.

        Tries httpx first; falls back to Playwright on Cloudflare block.
        """
        embed_url = self._normalize_embed_url(url)

        html, cloudflare_blocked = await self._fetch_with_httpx(embed_url)

        if html is None and cloudflare_blocked:
            log.info("supervideo_playwright_fallback", url=embed_url)
            html = await self._fetch_with_playwright(embed_url)

        if html is None:
            return None

        return self._extract_video(html, url)

    # ------------------------------------------------------------------
    # Fetch strategies
    # ------------------------------------------------------------------

    async def _fetch_with_httpx(self, embed_url: str) -> tuple[str | None, bool]:
        """Fetch page via httpx. Returns (html, cloudflare_blocked)."""
        try:
            headers = {**_BROWSER_HEADERS, "Referer": embed_url}
            resp = await self._http.get(
                embed_url,
                follow_redirects=True,
                timeout=15,
                headers=headers,
            )

            if _is_cloudflare_block(resp.status_code, resp.text):
                log.info(
                    "supervideo_cloudflare_detected",
                    status=resp.status_code,
                    url=embed_url,
                )
                return None, True

            if resp.status_code != 200:
                log.warning(
                    "supervideo_http_error",
                    status=resp.status_code,
                    url=embed_url,
                )
                return None, False

            return resp.text, False
        except httpx.HTTPError:
            log.warning("supervideo_request_failed", url=embed_url)
            return None, False

    async def _fetch_with_playwright(self, embed_url: str) -> str | None:
        """Fetch page via Playwright (Cloudflare bypass)."""
        try:
            await self._ensure_browser()
            assert self._context is not None  # noqa: S101

            page = await self._context.new_page()
            try:
                await page.goto(embed_url, wait_until="domcontentloaded")
                await self._wait_for_cloudflare(page)

                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:  # noqa: BLE001
                    pass  # proceed — page may still be usable

                html = await page.content()
                log.debug(
                    "supervideo_playwright_page",
                    url=embed_url,
                    title=await page.title(),
                    html_len=len(html),
                    has_sources="sources" in html,
                    has_video="<video" in html,
                    has_jwplayer="jwplayer" in html,
                )
                return html
            finally:
                if not page.is_closed():
                    await page.close()
        except Exception:
            log.warning("supervideo_playwright_failed", url=embed_url, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Playwright lifecycle
    # ------------------------------------------------------------------

    async def _ensure_browser(self) -> None:
        """Launch Chromium if not already running (double-check lock)."""
        if self._browser is not None:
            return
        async with self._browser_lock:
            if self._browser is not None:
                return
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless,
            )
            self._context = await self._browser.new_context(
                user_agent=_BROWSER_HEADERS["User-Agent"],
            )

    async def _wait_for_cloudflare(self, page: object) -> None:
        """Wait for Cloudflare challenge/block to resolve."""
        try:
            await page.wait_for_function(  # type: ignore[union-attr]
                """() => {
                    const t = document.title;
                    return !t.includes('Just a moment')
                        && !t.includes('Attention Required');
                }""",
                timeout=15_000,
            )
        except Exception:  # noqa: BLE001
            pass  # proceed anyway — page may still be usable

    async def cleanup(self) -> None:
        """Close browser and Playwright resources."""
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _extract_video(self, html: str, url: str) -> ResolvedStream | None:
        """Run all extraction methods on HTML content."""
        # Check offline markers
        if 'class="fake-signup"' in html:
            log.info("supervideo_offline", url=url)
            return None

        # Method 1: JWPlayer sources
        video_url = _extract_jwplayer_source(html)
        if video_url:
            return self._build_result(video_url)

        # Method 2: HTML5 video/source tags
        video_url = _extract_html5_video(html)
        if video_url:
            return self._build_result(video_url)

        # Method 3: Packed eval() JS
        video_url = _extract_packed_eval(html)
        if video_url:
            return self._build_result(video_url)

        log.warning("supervideo_extraction_failed", url=url)
        return None

    def _normalize_embed_url(self, url: str) -> str:
        """Ensure URL uses the /e/ embed format."""
        # Already an embed URL
        if "/e/" in url:
            return url

        # Extract file ID and convert to embed URL
        match = re.search(
            r"(?:/(?:d|v|embed-)?)?([a-z0-9]{12})",
            url.split("//", 1)[-1].split("/", 1)[-1],
        )
        if match:
            fuid = match.group(1)
            # Determine domain from URL
            domain_match = re.search(r"https?://([^/]+)", url)
            domain = domain_match.group(1) if domain_match else "supervideo.cc"
            return f"https://{domain}/e/{fuid}"

        return url

    def _build_result(self, video_url: str) -> ResolvedStream:
        """Build ResolvedStream from extracted URL."""
        is_hls = ".m3u8" in video_url
        return ResolvedStream(
            video_url=video_url,
            is_hls=is_hls,
            quality=StreamQuality.UNKNOWN,
        )
