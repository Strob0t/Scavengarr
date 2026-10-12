"""Mixdrop hoster resolver — the MP4 behind the embed player.

The embed page (``/e/{id}``; file pages ``/f/{id}`` and ``/emb/{id}`` are
read through it) packs its player setup with Dean Edwards' packer. Unpacked,
``MDCore.wurl`` holds the protocol-relative MP4 URL on the delivery CDN
(``//a-delivery48.mxcontent.net/v2/{id}.mp4?s=…&e=…``); the player of a
deleted file sets none. Playback needs no headers (checked 2026-10-01); the
embed page goes along as ``Referer`` like the player's own requests.

JDownloader's ``MixdropCo`` downloads through the captcha-protected
``?download`` form instead; the embed player needs no captcha.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain
from scavengarr.infrastructure.hoster_resolvers._video_extract import (
    unpacked_scripts,
)
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

log = structlog.get_logger(__name__)

# Second-level domains of JDownloader's MixdropCo, without its dead ones
_DOMAINS = frozenset(
    {
        "mixdrop",
        "mixdrop23",
        "mxdrop",
        "m1xdrop",
        "mixdrp",
        "miixdrop",
        "miiixdrop",
        "miiiixdrop",
        "mdy48tn97",
        "mdbekjwqa",
        "mdfx9dc8n",
        "mdzsmutpcvykb",
    }
)
_FILE_ID_RE = re.compile(r"^/(?:f|e|emb)/([a-z0-9]+)(?:/|$)")
_WURL_RE = re.compile(r'MDCore\.wurl\s*=\s*"([^"]+)"')


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from a mixdrop file or embed URL."""
    if extract_domain(url) not in _DOMAINS:
        return None
    match = _FILE_ID_RE.search(urlparse(url).path)
    return match.group(1) if match else None


class MixdropResolver:
    """Resolves mixdrop file and embed pages to the player's MP4.

    The MP4 URL plays only from the address that resolved it (finding 17:
    403 from another address), so it is proxied.
    """

    address_bound = True

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "mixdrop"

    @property
    def supported_domains(self) -> frozenset[str]:
        return _DOMAINS

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Read ``MDCore.wurl`` from the embed player's packed setup.

        Network errors propagate: the registry does not cache them as a
        dead link.
        """
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("mixdrop_invalid_url", url=url)
            return None

        resp = await self._http.get(
            f"https://{urlparse(url).netloc}/e/{file_id}",
            headers={"User-Agent": DEFAULT_USER_AGENT},
            follow_redirects=True,
            timeout=15,
        )
        if resp.status_code != 200:
            log.info("mixdrop_file_offline", file_id=file_id, status=resp.status_code)
            return None

        wurl = next(
            (
                found.group(1)
                for script in unpacked_scripts(resp.text)
                if (found := _WURL_RE.search(script))
            ),
            "",
        )
        if not wurl:
            log.info("mixdrop_file_offline", file_id=file_id)
            return None

        video_url = f"https:{wurl}" if wurl.startswith("//") else wurl
        log.debug("mixdrop_resolved", file_id=file_id, cdn=extract_domain(video_url))
        return ResolvedStream(
            video_url=video_url,
            headers={"Referer": str(resp.url)},
            quality=StreamQuality.UNKNOWN,
        )
