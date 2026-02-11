"""DoodStream hoster resolver — extracts video URLs from doodstream.com.

Extraction: GET embed page → extract /pass_md5/ URL + token →
GET pass_md5 endpoint → append token + expiry to get video URL.
Based on JD2 DoodstreamCom.java.

NOTE: May fail if captcha (reCaptchaV2/Turnstile) is required.
In that case, resolve() returns None gracefully.
"""

from __future__ import annotations

import re
import time

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known DoodStream domains
_DOMAINS = {
    "dood",
    "doods",
    "doodstream",
    "doodapi",
    "dooood",
    "ds2play",
    "ds2video",
    "d0o0d",
    "do0od",
    "d0000d",
    "d000d",
    "dooodster",
    "vidply",
    "do7go",
    "all3do",
    "doply",
    "vide0",
    "vvide0",
    "d-s",
    "dsvplay",
    "myvidplay",
}


class DoodStreamResolver:
    """Resolves DoodStream embed pages to playable video URLs.

    Supports dood.re, doodstream.com and many domain variants.
    Returns None if captcha is required (no automated captcha solving).
    """

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "doodstream"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Fetch DoodStream embed page and extract video URL."""
        # Normalize to embed URL
        embed_url = self._normalize_embed_url(url)

        try:
            resp = await self._http.get(
                embed_url,
                follow_redirects=True,
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                        " AppleWebKit/537.36"
                    ),
                },
            )
            if resp.status_code != 200:
                log.warning(
                    "doodstream_http_error",
                    status=resp.status_code,
                    url=embed_url,
                )
                return None

            html = resp.text
            base_url = str(resp.url)
        except httpx.HTTPError:
            log.warning("doodstream_request_failed", url=embed_url)
            return None

        # Check offline markers
        if self._is_offline(html):
            log.info("doodstream_offline", url=url)
            return None

        # Check for captcha requirement
        if self._has_captcha(html):
            log.warning("doodstream_captcha_required", url=url)
            return None

        # Extract /pass_md5/ URL
        pass_match = re.search(r"'(/pass_md5/[^<>\"']+)'", html)
        if not pass_match:
            log.warning("doodstream_no_pass_md5", url=url)
            return None

        pass_url = pass_match.group(1)

        # Extract token
        token_match = re.search(r"&token=([a-z0-9]+)", html)
        if not token_match:
            log.warning("doodstream_no_token", url=url)
            return None

        token = token_match.group(1)

        # Build full pass_md5 URL from the response domain
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        full_pass_url = f"{parsed.scheme}://{parsed.hostname}{pass_url}"

        # GET pass_md5 endpoint
        try:
            pass_resp = await self._http.get(
                full_pass_url,
                follow_redirects=True,
                timeout=10,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": base_url,
                },
            )
            if pass_resp.status_code != 200:
                log.warning(
                    "doodstream_pass_md5_error",
                    status=pass_resp.status_code,
                )
                return None

            video_base = pass_resp.text.strip()
        except httpx.HTTPError:
            log.warning("doodstream_pass_md5_failed", url=full_pass_url)
            return None

        if not video_base.startswith("http"):
            log.warning("doodstream_invalid_video_base", base=video_base[:50])
            return None

        # Append token and expiry
        expiry = int(time.time() * 1000)
        video_url = f"{video_base}?token={token}&expiry={expiry}"

        log.debug("doodstream_resolved", video_url=video_url[:80])
        return ResolvedStream(
            video_url=video_url,
            quality=StreamQuality.UNKNOWN,
            headers={"Referer": base_url},
        )

    def _normalize_embed_url(self, url: str) -> str:
        """Ensure URL uses the /e/ embed format."""
        if "/e/" in url:
            return url
        # Convert /d/ to /e/
        return re.sub(r"/d/", "/e/", url)

    def _is_offline(self, html: str) -> bool:
        """Check various DoodStream offline markers."""
        if '<iframe src="/e/"' in html:
            # Empty embed iframe with no real content
            if "minimalUserResponseInMiliseconds" not in html:
                return True
        if re.search(r"<h1>\s*Oops!\s*Sorry\s*</h1>", html):
            return True
        if re.search(
            r"<title>\s*Video not found\s*\|\s*DoodStream", html
        ):
            return True
        return False

    def _has_captcha(self, html: str) -> bool:
        """Check if captcha is required before extraction."""
        if "op=validate&gc_response=" in html:
            return True
        if "data-sitekey=" in html:
            return True
        if "cf-turnstile" in html.lower():
            return True
        return False
