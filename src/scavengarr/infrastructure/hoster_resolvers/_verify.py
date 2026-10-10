"""Shared checks that hoster-resolved video URLs are reachable and playable."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream
from scavengarr.infrastructure.hoster_resolvers._domain import extract_domain
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

log = structlog.get_logger(__name__)


_ERROR_PAGE_NAMES = frozenset({"404", "error", "errors"})

# Bytes read from a resolved URL: enough for a playlist header or a
# container signature, and for a master playlist's variants, small enough
# to be cheap on servers ignoring Range.
_SNIFF_BYTES = 4096
_VERIFY_TIMEOUT_S = 8.0
_PLAYBACK_CHECK_TIMEOUT_S = 6.0
# A CDN that does not allow HEAD is asked for the first bytes instead
_HEAD_NOT_ALLOWED = 405

# A variant of a master playlist; its RESOLUTION complete (a comma or the
# line end after it: the sniff can cut the last line)
_VARIANT_RESOLUTION_RE = re.compile(
    rb"^#EXT-X-STREAM-INF:.*?RESOLUTION=(\d+)x(\d+)(?=[,\r\n])", re.MULTILINE
)
# The total of a range answer; "*" (unknown) does not match
_CONTENT_RANGE_TOTAL_RE = re.compile(r"bytes \d+-\d+/(\d+)")


def is_error_redirect(url: str) -> bool:
    """True when a hoster redirected to its error page (``/404``, ``/error``).

    Looks at whole path segments (without extension) and query keys only:
    a plain substring test flags files and titles such as
    "The.Terror.S01E01.mkv" as dead.
    """
    parsed = urlparse(url)
    segments = (s.rsplit(".", 1)[0].lower() for s in parsed.path.split("/") if s)
    if any(s in _ERROR_PAGE_NAMES for s in segments):
        return True
    return any(k.lower() in _ERROR_PAGE_NAMES for k in parse_qs(parsed.query))


@dataclass(frozen=True)
class _Answer:
    """What a CDN answered: the status, the lower-cased content type, the
    ``Content-Range`` header and the first bytes (none after a HEAD)."""

    status: int
    content_type: str
    content_range: str | None = None
    head: bytes = b""

    @property
    def is_html(self) -> bool:
        """An error page, typed as HTML or starting like it: what a CDN
        serves for a dead file, with 200 as often as not."""
        return "text/html" in self.content_type or self.head[:1] == b"<"


async def _first_bytes(
    http_client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> _Answer:
    """Fetch the first ``_SNIFF_BYTES`` of *url* with a Range GET (redirects
    followed); the body is read below status 400 only. A failed request
    (timeout, reset) raises ``httpx.HTTPError``."""
    headers = {**headers, "Range": f"bytes=0-{_SNIFF_BYTES - 1}"}
    async with http_client.stream(
        "GET", url, headers=headers, follow_redirects=True, timeout=timeout
    ) as resp:
        head = b""
        if resp.status_code < 400:
            async for chunk in resp.aiter_bytes():
                head += chunk
                if len(head) >= _SNIFF_BYTES:
                    break
        return _Answer(
            resp.status_code,
            resp.headers.get("content-type", "").lower(),
            resp.headers.get("content-range"),
            head[:_SNIFF_BYTES].lstrip(),
        )


async def verify_video_url(
    http_client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    hoster: str,
) -> bool:
    """Check that a CDN URL answers like media.

    A HEAD request (8 s timeout, redirects followed) with the playback
    headers: 200 or 206 with a content type other than HTML counts as
    reachable. A CDN that does not allow HEAD (405) is asked for the first
    bytes instead (a Range GET, as the playback check sends), which must
    not be HTML either. A CDN serves a dead file as an error page, with 200
    as often as not, so an HTML answer is dead like an error status; both
    and a network error log a warning naming the CDN's domain, never the
    URL (``<hoster>_video_head_failed``, ``<hoster>_video_not_media``,
    ``<hoster>_video_verify_error``), and return ``False``.

    Parameters
    ----------
    http_client:
        Shared async HTTP client.
    url:
        The video CDN URL to check.
    headers:
        Playback headers (e.g. ``Referer``) required by the CDN.
    hoster:
        Hoster name used as prefix in structured log events
        (e.g. ``"voe"`` → ``"voe_video_head_failed"``).
    """
    cdn = extract_domain(url)
    try:
        resp = await http_client.head(
            url,
            headers=headers,
            follow_redirects=True,
            timeout=_VERIFY_TIMEOUT_S,
        )
        answer = _Answer(resp.status_code, resp.headers.get("content-type", "").lower())
        if answer.status == _HEAD_NOT_ALLOWED:
            answer = await _first_bytes(http_client, url, headers, _VERIFY_TIMEOUT_S)
    except httpx.HTTPError:
        log.warning(f"{hoster}_video_verify_error", cdn=cdn)
        return False
    if answer.status not in (200, 206):
        log.warning(f"{hoster}_video_head_failed", status=answer.status, cdn=cdn)
        return False
    if answer.is_html:
        log.warning(
            f"{hoster}_video_not_media", content_type=answer.content_type[:40], cdn=cdn
        )
        return False
    return True


@dataclass(frozen=True)
class PlaybackCheck:
    """What the playback check saw: whether a player could play the stream,
    the largest resolution of an HLS master playlist and a file's size."""

    playable: bool
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None


def _largest_variant(head: bytes) -> tuple[int, int] | None:
    sizes = [(int(w), int(h)) for w, h in _VARIANT_RESOLUTION_RE.findall(head)]
    return max(sizes, key=lambda size: size[0] * size[1], default=None)


def _total_size(content_range: str | None) -> int | None:
    match = _CONTENT_RANGE_TOTAL_RE.fullmatch((content_range or "").strip())
    return int(match[1]) if match else None


async def check_playable(
    http_client: httpx.AsyncClient,
    stream: ResolvedStream,
) -> PlaybackCheck:
    """Check that a resolved stream answers like a player expects.

    Fetches the first bytes with the stream's playback headers and, like
    the player (Stremio's ``proxyHeaders``), a browser User-Agent: CDNs
    such as mixdrop's answer other agents with 403.  An error status, an
    HTML page, or (for HLS) a body that is no playlist means the player
    would fail, so the stream is not playable. A playable stream comes with
    what the bytes tell without another request: the largest variant of a
    master playlist and the total size of a file's range answer.

    A failed request (timeout, reset) raises ``httpx.HTTPError``: it says
    nothing about the stream.
    """
    headers = {"User-Agent": DEFAULT_USER_AGENT, **stream.headers}
    answer = await _first_bytes(
        http_client, stream.video_url, headers, _PLAYBACK_CHECK_TIMEOUT_S
    )
    if answer.status >= 400:
        reason = f"status {answer.status}"
    elif answer.is_html:
        reason = "html"
    elif stream.is_hls and not answer.head.startswith(b"#EXTM3U"):
        reason = "no playlist"
    else:
        variant = _largest_variant(answer.head) if stream.is_hls else None
        width, height = variant or (None, None)
        # A playlist's range answer sizes the playlist, not the video
        size = None if stream.is_hls else _total_size(answer.content_range)
        return PlaybackCheck(True, width, height, size)
    log.info(
        "playback_check_failed",
        cdn=extract_domain(stream.video_url),
        reason=reason,
    )
    return PlaybackCheck(False)
