"""SuperVideo hoster resolver — XFS-based video extraction.

SuperVideo uses XFileSharingPro framework which embeds video URLs
via JWPlayer sources or HTML5 video tags.
Based on JD2 SupervideoTv.java (XFileSharingProBasic).
"""

from __future__ import annotations

import re

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)


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
    """

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "supervideo"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Fetch SuperVideo embed page and extract video URL."""
        # Normalize to embed URL format
        embed_url = self._normalize_embed_url(url)

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
                    "supervideo_http_error",
                    status=resp.status_code,
                    url=embed_url,
                )
                return None

            html = resp.text
        except httpx.HTTPError:
            log.warning("supervideo_request_failed", url=embed_url)
            return None

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
