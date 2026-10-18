"""Filemoon hoster resolver — extracts HLS URLs from filemoon.sx embed pages.

Supports two page generations:
1. Byse player (current): a Vite/React app whose stream URL exists only once
   the player runs — "click play to verify you're a human" starts a
   fingerprint attestation plus a proof-of-work captcha, then an encrypted
   playback response. The stream request is captured from the stealth
   browser (``StealthPool.capture_media``) instead of replaying that flow.
   A video whose owner allows embedding only on its own sites plays on the
   hoster's watch page ``/d/<id>`` instead, in a player frame of a second
   host; the details API tells which page to open.
2. Legacy XFS: packed JavaScript (Dean Edwards packer) with a JWPlayer config,
   or a plain HLS URL in the page source.

Filemoon domain variants: filemoon.sx, filemoon.to, filemoon.eu, etc.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers._browser import capture_stream
from scavengarr.infrastructure.hoster_resolvers._video_extract import (
    extract_hls_from_unpacked,
    unpack_p_a_c_k,
)
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

if TYPE_CHECKING:
    from scavengarr.infrastructure.browser.stealth_pool import StealthPool

log = structlog.get_logger(__name__)

# The Byse player's own captcha: a cold browser context requests
# ``captcha`` and ``captcha/verify`` before the stream, a warm one neither
# (row 91). A capture cut after them ends as ``challenge``, not ``timeout``
_CAPTCHA_REQUESTS = re.compile(r"/captcha(?:/verify)?/?(?:[?#]|$)")


# Mirror domains (JD2 FilemoonSxCrawler.java): filemoon.*, the rotating Byse
# player domains and a few odd aliases
_DOMAINS = frozenset(
    {
        "filemoon",
        "filemooon",
        "byse",
        "bysejikuar",
        "bysedikamoum",
        "byseraguci",
        "bysezejataos",
        "bysekoze",
        "bysesayeveum",
        "bysesukior",
        "morgan0928-6v7c14vs",
        "bf0skv",
        "ghajini-emtftw1o",
        "f51rm",
    }
)

# Byse players under a second-level name another resolver claims: moflix hands
# out moflix-stream.link (Byse) next to moflix-stream.click (VidHide)
_HOSTS = frozenset({"moflix-stream.link"})

# The Byse watch page /d/<id> shows its player in a frame below its header:
# a click on the page centre lands in the frame above its play button, a
# click on the frame's centre starts it
_WATCH_PLAYER = ".video-page__player iframe"

# The pass the captcha gives, in the player's localStorage: a token and its
# expiry in epoch milliseconds, 30 min after the captcha. A capture with the
# pass leaves it alone, and once it has gone the player asks again (row 106:
# warm captures at 20 and 29 min, the captcha at 31)
_PASS_ITEM = "byse:captcha-clearance:embed"


def pass_expiry(state: Mapping[str, Any]) -> float | None:
    """When the Byse captcha pass in *state* (a browser context's storage
    state) ends, in epoch seconds: the latest of its player hosts' passes;
    ``None`` without one."""
    expiries: list[float] = []
    for origin in state.get("origins") or []:
        items = origin.get("localStorage") if isinstance(origin, dict) else None
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or item.get("name") != _PASS_ITEM:
                continue
            try:
                record = json.loads(str(item.get("value", "")))
            except ValueError:
                continue
            expires = record.get("expiresAt") if isinstance(record, dict) else None
            if isinstance(expires, int | float) and not isinstance(expires, bool):
                expiries.append(expires / 1000)
    return max(expiries, default=None)


def _unpack_p_a_c_k(packed: str) -> str | None:
    """Unpack Dean Edwards packed JavaScript (delegates to shared module)."""
    return unpack_p_a_c_k(packed)


def _extract_hls_from_unpacked(js: str) -> str | None:
    """Extract HLS URL from unpacked JWPlayer config (delegates to shared module)."""
    return extract_hls_from_unpacked(js)


class FilemoonResolver:
    """Resolves Filemoon embed pages to playable HLS URLs.

    Supports filemoon.sx, filemoon.to and domain variants. Legacy pages are
    parsed from the HTML; Byse player pages need *stealth_pool* (without it
    they cannot be resolved).
    """

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
        return "filemoon"

    @property
    def supported_domains(self) -> frozenset[str]:
        """Mirror domains dispatched to this resolver by the registry."""
        return _DOMAINS

    @property
    def supported_hosts(self) -> frozenset[str]:
        """Hosts dispatched here whose second-level name other hosts share."""
        return _HOSTS

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Parse a legacy embed page, else capture the Byse player's stream."""
        embed_url = self._normalize_embed_url(url)

        try:
            resp = await self._http.get(
                embed_url,
                follow_redirects=True,
                timeout=15,
                headers={"User-Agent": DEFAULT_USER_AGENT},
            )
        except httpx.HTTPError:
            log.warning("filemoon_request_failed", url=embed_url)
            return None

        if resp.status_code == 200:
            html = resp.text
            if self._is_offline(html):
                log.info("filemoon_offline", url=url)
                return None
            result = self._try_packed_js(html) or self._try_direct_hls(html)
            if result:
                return ResolvedStream(
                    video_url=result.video_url,
                    is_hls=result.is_hls,
                    quality=result.quality,
                    # Referer after redirects: the CDN checks it
                    headers={"Referer": str(resp.url)},
                )
        else:
            # Cloudflare / geo block: the browser may still get through
            log.info("filemoon_http_error", status=resp.status_code, url=embed_url)

        page = await self._byse_page(embed_url)
        if page is None:
            return None
        page_url, player = page
        # The player frame's ad tag turns every click into a popup
        return await capture_stream(
            self._stealth_pool,
            page_url,
            "filemoon",
            player=player,
            own_scripts_only=True,
            challenge_requests=_CAPTCHA_REQUESTS,
        )

    async def _byse_page(self, embed_url: str) -> tuple[str, str | None] | None:
        """The page the browser opens for a Byse video and the player element
        it clicks there, by the details API.

        A 404 means the video is gone (``None``, no browser). The API's own
        403 ``embedding`` answer means the owner allows embedding only on
        its own sites: the embed page shows "Embedding blocked on this
        site", the hoster's watch page ``/d/<id>`` plays the video in a
        player frame of its own (``_WATCH_PLAYER``). Anything else, a
        Cloudflare 403 or no answer included, leaves it to the embed page
        (one frame less; the page is the player, its centre is clicked).
        """
        match = re.match(r"(https?://[^/]+)/e/([A-Za-z0-9]+)", embed_url)
        if not match:
            return embed_url, None
        base, video_id = match.groups()
        api_url = f"{base}/api/videos/{video_id}/embed/details"
        try:
            resp = await self._http.get(
                api_url, timeout=10, headers={"User-Agent": DEFAULT_USER_AGENT}
            )
        except httpx.HTTPError:
            log.debug("filemoon_details_failed", url=api_url)
            return embed_url, None
        if resp.status_code == 404:
            log.info(
                "filemoon_unavailable",
                url=embed_url,
                status=resp.status_code,
                reason=resp.text[:120],
            )
            return None
        # A Cloudflare 403 is no verdict on the video: only the API's own error
        if resp.status_code == 403 and "embedding" in resp.text:
            log.info("filemoon_embedding_blocked", url=embed_url)
            return f"{base}/d/{video_id}", _WATCH_PLAYER
        return embed_url, None

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
