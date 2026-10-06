"""veev.to hoster resolver — decodes the player API source.

Resolution strategy (port of JD2 ``VeevTo.getEmbedDllink``, originally from
https://github.com/skoruppa/docchi-players/blob/main/veev.py):

1. GET the embed page ``/e/{file_code}`` and read the encoded token from
   ``window._vvto[...] = "<token>"``.
2. LZW-decode the token (``ch``) and derive the unwrap steps from it.
3. GET ``/dl?op=player_api&cmd=gi&file_code=...&ch=...`` (XHR) → JSON with
   ``file.dv[0].s``.
4. LZW-decode ``s`` and unwrap it (hex pairs, optional reversal) → MP4 URL.

No captcha on this path (the official download path needs Turnstile).
Offline: ``<title>Watch video - Veev.to</title>`` or "File not found".
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

log = structlog.get_logger(__name__)

_DOMAINS = frozenset({"veev"})
# veevcdn binds the stream token to the resolving User-Agent and
# Accept-Language (another value, or one more header, gets 403; production,
# 2026-10-06), so resolve with a browser UA and hand it on for playback;
# /play resolves again with a player's own values
_USER_AGENT = DEFAULT_USER_AGENT
_BOUND_HEADERS = ("user-agent", "accept-language")
_FILE_ID_RE = re.compile(r"^/(?:e/|d/)?([A-Za-z0-9]{12,})(?:/|$|\.html)")
_TOKEN_RE = re.compile(r'window\._vvto\s*\[\s*\w+\s*\]\s*=\s*"([^"]+)"')
_OFFLINE_RE = re.compile(
    r"<title>Watch video - Veev\.to</title>|>\s*File not found", re.IGNORECASE
)
_HEX_PAIR_RE = re.compile(r"[0-9a-fA-F]+")
_PADDING = "dXRmOA=="  # base64("utf8"), stripped after each unwrap step


def _extract_file_id(url: str) -> str | None:
    """Extract the file code from a veev ``/e/`` or ``/d/`` URL."""
    try:
        if extract_domain(url) not in _DOMAINS:
            return None
        match = _FILE_ID_RE.search(urlparse(url).path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


def _lzw_decode(data: str) -> str:
    """LZW decompression as done by the veev player (codes >= 256)."""
    if not data:
        return ""
    current = previous = data[0]
    out = [current]
    table: dict[int, str] = {}
    next_code = 256
    for char in data[1:]:
        code = ord(char)
        if code < 256:
            entry = char
        elif code in table:
            entry = table[code]
        else:
            entry = previous + current
        out.append(entry)
        current = entry[0]
        table[next_code] = previous + current
        next_code += 1
        previous = entry
    return "".join(out)


def _numeric(char: str) -> int:
    """Java ``Character.getNumericValue``: 0-9, a-z → 10-35, otherwise -1."""
    return int(char, 36) if char.isascii() and char.isalnum() else -1


def _build_array(decoded: str) -> list[list[int]]:
    """Split the decoded token into unwrap step lists.

    Format: a count digit followed by that many digits (stored reversed),
    repeated until a count <= 0.
    """
    chars = list(decoded)
    groups: list[list[int]] = []
    count = _numeric(chars.pop(0)) if chars else 0
    while count > 0 and chars:
        group: list[int] = []
        for _ in range(count):
            if not chars:
                break
            group.insert(0, _numeric(chars.pop(0)))
        groups.append(group)
        count = _numeric(chars.pop(0)) if chars else 0
    return groups


def _hex_pairs(data: str) -> str:
    """JS ``decodeURIComponent`` over hex pairs, keeping unparsable pairs."""
    out: list[str] = []
    for i in range(0, len(data), 2):
        match = _HEX_PAIR_RE.match(data[i : i + 2])
        out.append(chr(int(match.group(0), 16)) if match else "%0NaN")
    return "".join(out)


def _unwrap(data: str, steps: list[int]) -> str:
    """Apply the unwrap steps: optional reversal, then hex-pair decoding."""
    for step in steps:
        if step == 1:
            data = data[::-1]
        data = _hex_pairs(data).replace(_PADDING, "")
    return data


class VeevResolver:
    """Resolves veev.to embeds to a direct MP4 URL via the player API."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "veev"

    @property
    def bound_headers(self) -> tuple[str, ...]:
        return _BOUND_HEADERS

    async def resolve(self, url: str) -> ResolvedStream | None:
        return await self._resolve(url, {"User-Agent": _USER_AGENT})

    async def resolve_for_client(
        self, url: str, headers: Mapping[str, str]
    ) -> ResolvedStream | None:
        """Resolve *url* with the player's User-Agent and Accept-Language."""
        client = {"User-Agent": headers.get("user-agent") or _USER_AGENT}
        if language := headers.get("accept-language"):
            client["Accept-Language"] = language
        return await self._resolve(url, client)

    async def _resolve(self, url: str, client: dict[str, str]) -> ResolvedStream | None:
        """Resolve *url*, every request with the *client* headers the CDN binds."""
        file_code = _extract_file_id(url)
        if not file_code:
            log.warning("veev_invalid_url", url=url)
            return None

        parsed = urlparse(url)
        origin = f"{parsed.scheme or 'https'}://{parsed.hostname or 'veev.to'}"
        embed_url = f"{origin}/e/{file_code}"

        try:
            page = await self._http.get(
                embed_url, follow_redirects=True, timeout=15, headers=client
            )
        except httpx.HTTPError:
            log.warning("veev_request_failed", url=url)
            return None
        if page.status_code != 200:
            log.info("veev_http_error", status=page.status_code, url=url)
            return None
        # veev redirects some links to another file code; the page's token
        # belongs to that code (the API answers the old one "malformed request")
        file_code = _extract_file_id(str(page.url)) or file_code
        embed_url = str(page.url)
        if _OFFLINE_RE.search(page.text):
            log.info("veev_offline", file_code=file_code)
            return None

        token = _TOKEN_RE.search(page.text)
        if not token:
            log.warning("veev_no_token", file_code=file_code)
            return None
        ch = _lzw_decode(token.group(1))
        steps = _build_array(ch)
        if not steps:
            log.warning("veev_bad_token", file_code=file_code)
            return None

        source = await self._player_source(origin, embed_url, file_code, ch, client)
        if not source:
            return None
        video_url = _unwrap(_lzw_decode(source), steps[0])
        if not video_url.startswith("http"):
            log.warning("veev_decode_failed", file_code=file_code)
            return None

        log.debug("veev_resolved", file_code=file_code)
        return ResolvedStream(
            video_url=video_url,
            quality=StreamQuality.UNKNOWN,
            headers={"Referer": f"{origin}/", **client},
        )

    async def _player_source(
        self,
        origin: str,
        embed_url: str,
        file_code: str,
        ch: str,
        client: dict[str, str],
    ) -> str | None:
        """Ask the player API for the encoded source (``file.dv[0].s``)."""
        try:
            resp = await self._http.get(
                f"{origin}/dl",
                params={
                    "op": "player_api",
                    "cmd": "gi",
                    "file_code": file_code,
                    "r": "",
                    "ch": ch,
                    "ie": "1",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": embed_url,
                    **client,
                },
                timeout=15,
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            log.warning("veev_api_failed", file_code=file_code)
            return None
        if not isinstance(data, dict) or data.get("status") != "success":
            log.info("veev_api_status", file_code=file_code, status=str(data)[:80])
            return None
        try:
            return str(data["file"]["dv"][0]["s"])
        except (KeyError, IndexError, TypeError):
            log.warning("veev_api_unexpected", file_code=file_code)
            return None
