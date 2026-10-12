"""UPN Share and RPM Share hoster resolver (moflix's ``upns`` and ``rpmplay``
mirrors).

One player family (``moflix.upns.xyz``, ``moflix.rpmplay.xyz``): a vidstack
player with the video id in the URL fragment (``/#<id>``) that refuses
headless browsers ("Headless Browser is not allowed"), so the stream comes
from its API, the way the player itself asks for it (headful capture,
2026-10-10; CloudStream's ``VidStack`` extractor, Cyberdrop-DL's
``vidstack`` crawler and Stream4me's ``rpmshare`` server do the same):

1. ``GET /api/v1/video?id=<id>&w=1280&h=720&r=`` with the player host as the
   ``Referer`` (``/api/v1/info`` only carries the player's configuration,
   the same for every id, and needs no call).
2. The answer is hex ciphertext: AES-128-CBC with PKCS7 padding, the
   player's fixed key and IV (a second IV on other installations).
3. The plaintext is JSON whose ``source`` is the HLS master URL (``title``,
   ``poster`` and ``subtitle`` besides).

Offline: 404 ``{"message": "Video not found or deleted"}`` (the player shows
the same words). A burst of calls gets 429 ``Rate limit exceeded``, answered
as not resolved. One instance per hoster name: the registry dispatches by
the URL's second-level domain, and the two mirrors stay two hosters.
"""

from __future__ import annotations

import json
import re
from typing import Literal
from urllib.parse import urlparse

import httpx
import structlog
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain
from scavengarr.infrastructure.hoster_resolvers._verify import verify_video_url
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

log = structlog.get_logger(__name__)

Hoster = Literal["upns", "rpmplay"]

_DOMAINS = frozenset({"upns", "rpmplay"})

# The id is the fragment up to a "/" or "&" (the player reads it the same way)
_FILE_ID_RE = re.compile(r"^([A-Za-z0-9]{4,16})(?:[/&]|$)")

_KEY = b"kiemtienmua911ca"
_IVS = (b"1234567890oiuytr", b"0123456789abcdef")
_BLOCK = 16

_API_TIMEOUT_S = 15.0
_NOT_FOUND = 404
_RATE_LIMITED = 429


def _extract_file_id(url: str) -> str | None:
    """The video id from the fragment of a UPN Share or RPM Share URL."""
    try:
        if extract_domain(url) not in _DOMAINS:
            return None
        match = _FILE_ID_RE.match(urlparse(url).fragment)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


def _decrypt(hex_text: str) -> str | None:
    """The player's JSON text from its hex answer; ``None`` when no IV fits.

    A wrong IV garbles the first block only (CBC), so the padding at the end
    proves nothing about it: the text must open the JSON object too.
    """
    try:
        data = bytes.fromhex(hex_text.strip())
    except ValueError:
        return None
    if not data or len(data) % _BLOCK:
        return None
    for iv in _IVS:
        decryptor = Cipher(algorithms.AES(_KEY), modes.CBC(iv)).decryptor()
        plain = decryptor.update(data) + decryptor.finalize()
        pad = plain[-1]
        if not 1 <= pad <= _BLOCK or not plain.endswith(bytes([pad]) * pad):
            continue
        try:
            text = plain[:-pad].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if text.lstrip().startswith("{"):
            return text
    return None


def _source(text: str) -> str | None:
    """The ``source`` URL of the player's JSON (bytes after the object ignored)."""
    try:
        data = json.loads(text)
    except ValueError:
        end = text.rfind("}")
        if end < 0:
            return None
        try:
            data = json.loads(text[: end + 1])
        except ValueError:
            return None
    source = data.get("source") if isinstance(data, dict) else None
    return source if isinstance(source, str) and source.startswith("http") else None


class UpnsResolver:
    """Resolves a UPN Share or RPM Share player URL to its HLS master URL."""

    def __init__(self, http_client: httpx.AsyncClient, hoster: Hoster = "upns") -> None:
        self._http = http_client
        self._hoster: Hoster = hoster

    @property
    def name(self) -> str:
        return self._hoster

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Ask the player's video API for the stream of *url*."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning(f"{self.name}_invalid_url", url=url)
            return None

        parsed = urlparse(url)
        host = parsed.hostname or f"moflix.{self.name}.xyz"
        origin = f"{parsed.scheme or 'https'}://{host}"
        try:
            resp = await self._http.get(
                f"{origin}/api/v1/video",
                params={"id": file_id, "w": "1280", "h": "720", "r": ""},
                headers={"User-Agent": DEFAULT_USER_AGENT, "Referer": f"{origin}/"},
                timeout=_API_TIMEOUT_S,
            )
        except httpx.HTTPError:
            log.warning(f"{self.name}_request_failed", host=host, file_id=file_id)
            return None

        if resp.status_code == _NOT_FOUND:
            log.info(f"{self.name}_file_offline", file_id=file_id)
            return None
        if resp.status_code == _RATE_LIMITED:
            log.warning(f"{self.name}_rate_limited", host=host, file_id=file_id)
            return None
        if resp.status_code != 200:
            log.warning(f"{self.name}_http_error", status=resp.status_code, host=host)
            return None

        text = _decrypt(resp.text)
        if text is None:
            log.warning(f"{self.name}_undecodable_answer", host=host, file_id=file_id)
            return None
        source = _source(text)
        if not source:
            log.warning(f"{self.name}_no_source", host=host, file_id=file_id)
            return None

        headers = {"Origin": origin, "Referer": f"{origin}/"}
        if not await verify_video_url(self._http, source, headers, self.name):
            return None

        log.debug(f"{self.name}_resolved", file_id=file_id)
        return ResolvedStream(
            video_url=source,
            is_hls=not urlparse(source).path.lower().endswith(".mp4"),
            quality=StreamQuality.UNKNOWN,
            headers=headers,
        )
