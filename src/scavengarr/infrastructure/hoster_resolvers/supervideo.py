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
    """Detect Cloudflare JS challenge from HTTP response."""
    if status_code == 403 and "Just a moment" in html:
        return True
    if status_code == 503 and "challenge-platform" in html:
        return True
    return False


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


def _extract_packed_eval(html: str) -> str | None:
    """Extract video URL from eval(function(p,a,c,k,e,d) packed JS.

    Some XFS sites pack their JWPlayer config in eval() blocks.
    We look for http URLs ending in common video extensions.
    """
    # Find packed JS block
    match = re.search(
        r"eval\(function\(p,a,c,k,e,d\)\{.*?\.split\('\|'\)\)",
        html,
        re.DOTALL,
    )
    if not match:
        return None

    packed = match.group(0)
    # Look for base URL pattern in the packed data
    # The split('|') section contains tokens
    tokens_match = re.search(r"'([^']+)'\.split\('\|'\)", packed)
    if not tokens_match:
        return None

    # Try to find a direct URL in the packed content
    url_match = re.search(
        r"(https?://[^\s\"'\\]+\.(?:mp4|m3u8))",
        packed,
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

                return await page.content()
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
        """Wait for Cloudflare challenge to resolve."""
        try:
            await page.wait_for_function(  # type: ignore[union-attr]
                "() => !document.title.includes('Just a moment')",
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
