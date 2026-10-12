"""Streamtape hoster resolver — extracts video URLs from streamtape.com.

The embed page carries the ``get_video`` parameters (id, expires, ip,
token) in hidden elements whose HTML texts are decoys; the page's script
assigns the real value to the element the player reads (``botlink``) as
string pieces joined with ``substring`` chains, and the player appends
``&stream=1``. ``video_params`` evaluates that assignment; a page without
it falls back to the first literal with the token the script names (the
JD2 StreamtapeCom.java approach). Tokens are mixed case.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers._domain import extract_domain
from scavengarr.infrastructure.hoster_resolvers._verify import verify_video_url
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

log = structlog.get_logger(__name__)

# The element the player reads: $('#botlink').text() + '&stream=1'
_PLAYER_ELEMENT_RE = re.compile(r"\$\('#(\w+)'\)\.text\(\)")
# getElementById('botlink').innerHTML = '//host/get_video' + ('xyza?id=…').substring(4);
_ASSIGNMENT_RE = re.compile(r"getElementById\('(\w+)'\)\.innerHTML\s*=\s*([^;]+);")
_STRING_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_SUBSTRING_RE = re.compile(r"\.substring\((\d+)\)")
_QUERY_RE = re.compile(r"id=[^&\s\"'<]+&expires=\d+&ip=[^&\s\"'<]+&token=[^&\s\"'<]+")
# Legacy pages: the literal, and the token the script names
_PARAMS_RE = re.compile(
    r"(id=[^\"'&]*&expires=\d+&ip=[^\"'&]*&token=[^\"'&]*?)([\"'<])"
)
_SCRIPT_TOKEN_RE = re.compile(r"document\.getElementById[^<]*&token=([A-Za-z0-9\-_]+)")


def _js_string(expr: str) -> str:
    """The value of a script expression joining string literals with ``+``,
    each cut by its ``.substring(n)`` chain."""
    pieces = []
    for piece in expr.split("+"):
        match = _STRING_RE.search(piece)
        if match is None:
            continue
        value = match.group(1) if match.group(1) is not None else match.group(2)
        for start in _SUBSTRING_RE.findall(piece):
            value = value[int(start) :]
        pieces.append(value)
    return "".join(pieces)


def video_params(html: str) -> str | None:
    """The ``get_video`` query (``id=…&expires=…&ip=…&token=…``) of an embed
    page: what the script assigns to the element the player reads (the
    last assignment, as in the browser), else the first literal with its
    token corrected from the script."""
    player = _PLAYER_ELEMENT_RE.search(html)
    if player is not None:
        for match in reversed(list(_ASSIGNMENT_RE.finditer(html))):
            if match.group(1) != player.group(1):
                continue
            value = _js_string(match.group(2))
            query = _QUERY_RE.search(value.partition("?")[2])
            if query is not None:
                return query.group(0)
    legacy = _PARAMS_RE.search(html)
    if legacy is None:
        return None
    params = legacy.group(1)
    token = _SCRIPT_TOKEN_RE.search(html)
    if token is not None:
        params = re.sub(r"token=[^&]*", f"token={token.group(1)}", params)
    return params


# Mirror domains (JD2 StreamtapeCom.java + mirrors seen on plugin sites)
_DOMAINS = frozenset(
    {
        "streamtape",
        "streamta",  # streamta.pe / streamta.site
        "strtape",
        "strtpe",
        "strcloud",
        "shavetape",
        "streamadblocker",
        "streamtapeadblock",
        "streamtapeadblockuser",
        "tapeadvertisement",
        "tapeblocker",
        "gettapeads",
        "watchadsontape",
    }
)


class StreamtapeResolver:
    """Resolves Streamtape embed pages to direct video URLs."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "streamtape"

    @property
    def supported_domains(self) -> frozenset[str]:
        """Mirror domains dispatched to this resolver by the registry."""
        return frozenset(_DOMAINS)

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Fetch Streamtape page and extract video download URL."""
        try:
            resp = await self._http.get(
                url,
                follow_redirects=True,
                timeout=15,
                headers={"User-Agent": DEFAULT_USER_AGENT},
            )
            if resp.status_code != 200:
                log.warning(
                    "streamtape_http_error",
                    status=resp.status_code,
                    url=url,
                )
                return None

            html = resp.text
        except httpx.HTTPError:
            log.warning("streamtape_request_failed", url=url)
            return None

        # Check if video exists
        if ">Video not found" in html or resp.status_code in (404, 500):
            log.info("streamtape_video_not_found", url=url)
            return None

        params_str = video_params(html)
        if params_str is None:
            log.warning("streamtape_no_params", url=url)
            return None

        # Determine base domain from the response URL
        resp_host = urlparse(str(resp.url)).hostname or "streamtape.com"

        video_url = f"https://{resp_host}/get_video?{params_str}&stream=1"
        playback_headers = {"Referer": f"https://{resp_host}/"}

        if not await self._verify_video_url(video_url, playback_headers):
            log.warning("streamtape_video_unreachable", cdn=extract_domain(video_url))
            return None

        log.debug("streamtape_resolved", cdn=extract_domain(video_url))
        return ResolvedStream(
            video_url=video_url,
            quality=StreamQuality.UNKNOWN,
            headers=playback_headers,
        )

    async def _verify_video_url(self, url: str, headers: dict[str, str]) -> bool:
        """HEAD-check the video URL to verify it is accessible."""
        return await verify_video_url(self._http, url, headers, "streamtape")
