"""Stream capture from the stealth browser, shared by the resolvers.

Used where httpx cannot get at the stream: Cloudflare in front of the embed
page, or players that create the stream URL only while running (Filemoon's
Byse player). See ``StealthPool.capture_media``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers._domain import extract_domain

if TYPE_CHECKING:
    from scavengarr.infrastructure.browser.stealth_pool import StealthPool

log = structlog.get_logger(__name__)


async def capture_stream(
    stealth_pool: StealthPool | None,
    embed_url: str,
    hoster: str,
    *,
    player: str | None = None,
    own_scripts_only: bool = False,
) -> ResolvedStream | None:
    """Run the hoster's player in the stealth browser, take its stream request.

    Playback needs the Referer of the captured request (the player frame's
    origin); the embed URL stands in when the request had none. The pool's
    own timeout bounds navigation and the Cloudflare challenge
    (``stremio.probe_stealth_timeout_seconds``). *player* names the element
    the browser clicks to start the player, the page centre without it.
    *own_scripts_only* keeps the scripts of other sites than the page's and
    its frames' out (an ad tag that takes the clicks).
    """
    if stealth_pool is None:
        log.info(f"{hoster}_no_browser", url=embed_url)
        return None
    media = await stealth_pool.capture_media(
        embed_url, player=player, own_scripts_only=own_scripts_only
    )
    if media is None:
        log.info(f"{hoster}_browser_no_stream", url=embed_url)
        return None
    log.debug(f"{hoster}_browser_stream", cdn=extract_domain(media.url))
    return ResolvedStream(
        video_url=media.url,
        is_hls=".m3u8" in media.url,
        quality=StreamQuality.UNKNOWN,
        headers={"Referer": media.referer or embed_url},
    )
