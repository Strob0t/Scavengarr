"""Shared checks that hoster-resolved video URLs are reachable and playable."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream
from scavengarr.infrastructure.hoster_resolvers import _mp4
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
# The whole segment probe (up to two playlists and one segment)
_SEGMENT_PROBE_TIMEOUT_S = 4.0
# What a CDN answers for a signed link that has expired or was never valid
_SEGMENT_GONE_STATUSES = frozenset({403, 404, 410})
# A CDN that does not allow HEAD is asked for the first bytes instead
_HEAD_NOT_ALLOWED = 405

# A variant of a master playlist; its attributes (a cut last line names no
# resolution, because the pattern needs the whole line). An I-frame variant
# is not a playable variant and does not match.
_STREAM_INF_RE = re.compile(rb"^#EXT-X-STREAM-INF:(.*)$", re.MULTILINE)
_ATTRIBUTE_RE = re.compile(rb'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
_RESOLUTION_RE = re.compile(rb"(\d+)x(\d+)")
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
    start: int = 0,
) -> _Answer:
    """Fetch ``_SNIFF_BYTES`` of *url* from byte *start* with a Range GET
    (redirects followed); the body is read below status 400 only. A failed
    request (timeout, reset) raises ``httpx.HTTPError``."""
    end = start + _SNIFF_BYTES - 1
    headers = {**headers, "Range": f"bytes={start}-{end}"}
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
    the largest resolution of an HLS master playlist or an MP4 file's
    video, a file's size, and the bitrate of the variant or file."""

    playable: bool
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    bitrate_bps: int | None = None


@dataclass(frozen=True)
class _Variant:
    """A playable variant of an HLS master playlist."""

    width: int
    height: int
    bitrate_bps: int | None


def _variants(head: bytes) -> list[_Variant]:
    """The variants a master playlist's ``#EXT-X-STREAM-INF`` lines name
    with a complete ``RESOLUTION``; a line the sniff cut is ignored."""
    variants: list[_Variant] = []
    for match in _STREAM_INF_RE.finditer(head):
        # The sniff may end inside the last line: its attributes are cut
        if match.end() == len(head) and not head.endswith(b"\n"):
            continue
        # A CRLF playlist leaves the carriage return in the group
        line = match.group(1).rstrip(b"\r")
        attributes = {
            name: value.strip(b'"') for name, value in _ATTRIBUTE_RE.findall(line)
        }
        resolution = _RESOLUTION_RE.fullmatch(attributes.get(b"RESOLUTION", b""))
        if resolution is None:
            continue
        # AVERAGE-BANDWIDTH is the better figure when the playlist has it
        bitrate = attributes.get(b"AVERAGE-BANDWIDTH") or attributes.get(
            b"BANDWIDTH", b""
        )
        variants.append(
            _Variant(
                int(resolution[1]),
                int(resolution[2]),
                int(bitrate) if bitrate.isdigit() else None,
            )
        )
    return variants


def _best_variant(head: bytes) -> _Variant | None:
    """The variant with the most pixels, the higher bitrate among equal
    resolutions; ``None`` for a media playlist."""
    return max(
        _variants(head),
        key=lambda variant: (
            variant.width * variant.height,
            variant.bitrate_bps or 0,
        ),
        default=None,
    )


def _total_size(content_range: str | None) -> int | None:
    match = _CONTENT_RANGE_TOTAL_RE.fullmatch((content_range or "").strip())
    return int(match[1]) if match else None


@dataclass(frozen=True)
class _FileVideo:
    """What an MP4 file's header names: its video size and its duration."""

    width: int | None = None
    height: int | None = None
    seconds: float | None = None


async def _file_video_info(
    http_client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    answer: _Answer,
    deadline: float,
) -> _FileVideo:
    """The video size and duration an MP4 file's header names: from the
    checked bytes when its ``moov`` comes first, else from one more read of
    ``_SNIFF_BYTES`` where its ``moov`` starts, before the check's
    *deadline* (``time.monotonic()``). A failed read measures nothing.

    The checked bytes are left-stripped; a box under 144 MiB starts with a
    byte below 0x09, which ``lstrip()`` keeps.
    """
    size = _mp4.video_size(answer.head)
    seconds = _mp4.movie_duration(answer.head)
    if size is not None and seconds is not None:
        return _FileVideo(*size, seconds)
    total = _total_size(answer.content_range)
    if total is None:
        return _FileVideo(*(size or (None, None)), seconds)
    offset = _mp4.moov_offset(answer.head, total)
    left = deadline - time.monotonic()
    if offset is None or left <= 0:
        return _FileVideo(*(size or (None, None)), seconds)
    try:
        async with asyncio.timeout(left):
            moov = await _first_bytes(http_client, url, headers, left, start=offset)
    except (httpx.HTTPError, TimeoutError) as exc:
        log.info(
            "playback_check_moov_unread",
            cdn=extract_domain(url),
            error=type(exc).__name__,
        )
        return _FileVideo(*(size or (None, None)), seconds)
    # A CDN that ignores the range answers from the file's start
    if moov.status != 206 or not (moov.content_range or "").startswith(
        f"bytes {offset}-"
    ):
        return _FileVideo(*(size or (None, None)), seconds)
    width, height = _mp4.video_size(moov.head) or size or (None, None)
    return _FileVideo(width, height, _mp4.movie_duration(moov.head) or seconds)


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
    what the bytes tell: the largest variant of a master playlist and that
    variant's bitrate, the total size of a file's range answer, an MP4
    file's video size and duration (``_file_video_info``: one more read for
    a ``moov`` at the end, within the check's 6 s), and the file's bitrate
    from its size and duration.

    A failed request (timeout, reset) raises ``httpx.HTTPError``: it says
    nothing about the stream.
    """
    headers = {"User-Agent": DEFAULT_USER_AGENT, **stream.headers}
    deadline = time.monotonic() + _PLAYBACK_CHECK_TIMEOUT_S
    answer = await _first_bytes(
        http_client, stream.video_url, headers, _PLAYBACK_CHECK_TIMEOUT_S
    )
    # A body that starts like a playlist is one, whatever the resolver
    # guessed from the URL: its range answer sizes the playlist, not a video
    playlist = answer.head.startswith(b"#EXTM3U")
    if answer.status >= 400:
        reason = f"status {answer.status}"
    elif answer.is_html:
        reason = "html"
    elif stream.is_hls and not playlist:
        reason = "no playlist"
    elif playlist:
        variant = _best_variant(answer.head)
        # A playlist's range answer sizes the playlist, not the video
        return PlaybackCheck(
            True,
            width=variant.width if variant else None,
            height=variant.height if variant else None,
            bitrate_bps=variant.bitrate_bps if variant else None,
        )
    else:
        video = await _file_video_info(
            http_client, stream.video_url, headers, answer, deadline
        )
        total = _total_size(answer.content_range)
        return PlaybackCheck(
            True,
            width=video.width,
            height=video.height,
            size_bytes=total,
            bitrate_bps=(
                round(total * 8 / video.seconds) if total and video.seconds else None
            ),
        )
    log.info(
        "playback_check_failed",
        cdn=extract_domain(stream.video_url),
        reason=reason,
    )
    return PlaybackCheck(False)


def _first_uri(playlist: bytes) -> str | None:
    """The first URI line of an HLS playlist (a variant or a segment)."""
    for raw in playlist.decode("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            return line
    return None


async def _segment_url(
    http_client: httpx.AsyncClient, stream: ResolvedStream, headers: dict[str, str]
) -> str | None:
    """The first segment of an HLS stream: the first variant of a master
    playlist, then that media playlist's first segment. A segment URL
    without a query takes the stream URL's, as the HLS proxy sends it. None
    when a playlist is not one or lists no segment."""
    url = stream.video_url
    for _ in range(2):
        answer = await _first_bytes(http_client, url, headers, _SEGMENT_PROBE_TIMEOUT_S)
        if answer.status >= 400 or not answer.head.startswith(b"#EXTM3U"):
            return None
        uri = _first_uri(answer.head)
        if uri is None:
            return None
        target = urljoin(url, uri)
        if b"#EXT-X-STREAM-INF" not in answer.head:
            query = urlparse(stream.video_url).query
            if query and not urlparse(target).query:
                target = f"{target}?{query}"
            return target
        url = target
    return None


async def segment_is_gone(
    http_client: httpx.AsyncClient, stream: ResolvedStream
) -> bool:
    """Whether the first segment of an HLS *stream* answers 403, 404 or 410.

    Walks the playlists to one segment and asks for its first bytes (a
    ``Range`` GET with the playback headers and the player's User-Agent),
    all within ``_SEGMENT_PROBE_TIMEOUT_S``. A signed CDN link can expire
    or be refused while its playlists still answer, so the player would
    open a stream that fails at its first segment. Any other result (a
    timeout, a network error, a playlist that is gone or lists nothing,
    another status) is no verdict and returns ``False``: the stream stays.
    """
    if not stream.is_hls:
        return False
    headers = {"User-Agent": DEFAULT_USER_AGENT, **stream.headers}
    cdn = extract_domain(stream.video_url)
    try:
        async with asyncio.timeout(_SEGMENT_PROBE_TIMEOUT_S):
            segment = await _segment_url(http_client, stream, headers)
            if segment is None:
                return False
            answer = await _first_bytes(
                http_client, segment, headers, _SEGMENT_PROBE_TIMEOUT_S
            )
    except (httpx.HTTPError, TimeoutError) as exc:
        log.info("hls_segment_probe_unread", cdn=cdn, error=type(exc).__name__)
        return False
    if answer.status in _SEGMENT_GONE_STATUSES:
        log.info("hls_segment_probe_gone", cdn=cdn, status=answer.status)
        return True
    return False
