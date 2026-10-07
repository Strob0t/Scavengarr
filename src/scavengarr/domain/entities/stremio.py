"""Domain entities for Stremio addon support.

Pure value objects — no framework dependencies, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Literal

StremioContentType = Literal["movie", "series"]


class StreamQuality(IntEnum):
    """Ranked quality levels (higher value = better quality)."""

    UNKNOWN = 0
    CAM = 10
    TS = 20
    SD = 30
    HD_720P = 40
    HD_1080P = 50
    UHD_4K = 60


# The lowest width and height of each class, best first
_RESOLUTION_CLASSES = (
    (3840, 2160, StreamQuality.UHD_4K),
    (1920, 1080, StreamQuality.HD_1080P),
    (1280, 720, StreamQuality.HD_720P),
)


def quality_from_resolution(width: int | None, height: int | None) -> StreamQuality:
    """The quality class of a video resolution, ``UNKNOWN`` without one.

    By width or by height, whichever gives the higher class: a letterboxed
    1080p encode (1920x800) is 720p by its height alone.
    """
    if width is None or height is None:
        return StreamQuality.UNKNOWN
    for min_width, min_height, quality in _RESOLUTION_CLASSES:
        if width >= min_width or height >= min_height:
            return quality
    return StreamQuality.SD


@dataclass(frozen=True)
class StreamLanguage:
    """Language metadata for a stream link."""

    code: str  # "de", "en", "de-sub", "en-sub"
    label: str  # "German Dub", "English Sub", etc.
    is_dubbed: bool  # True for dubs, False for subs


@dataclass(frozen=True)
class RankedStream:
    """A single stream link with quality and language metadata for ranking."""

    url: str
    hoster: str
    quality: StreamQuality = StreamQuality.UNKNOWN
    language: StreamLanguage | None = None
    size: str | None = None
    release_name: str | None = None
    title: str = ""
    source_plugin: str = ""
    rank_score: int = 0


@dataclass(frozen=True)
class StremioStream:
    """Stremio protocol Stream object (JSON-serializable)."""

    name: str  # Bold title in Stremio UI, e.g. "HDFilme 1080p"
    description: str  # Below name, e.g. "German Dub | VOE | 1.2 GB"
    url: str  # Direct stream URL
    behavior_hints: dict[str, Any] | None = None  # Stremio behaviorHints


@dataclass(frozen=True)
class StremioMetaPreview:
    """Stremio catalog item (MetaPreview object)."""

    id: str  # IMDb ID, e.g. "tt1234567"
    type: StremioContentType
    name: str
    poster: str = ""
    description: str = ""
    release_info: str = ""  # Year, e.g. "2024"
    imdb_rating: str = ""
    genres: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TitleMatchInfo:
    """Reference title and year for filtering search results.

    *alt_titles* holds additional language variants (e.g. the original
    English title when the primary title is German).  The scorer picks
    the best match across all titles.

    *content_type* controls year tolerance: series span multiple years
    so a wider tolerance (±3) is used versus movies (±1).
    """

    title: str
    year: int | None = None
    alt_titles: list[str] = field(default_factory=list)
    content_type: StremioContentType | None = None


@dataclass(frozen=True)
class CachedStreamLink:
    """A stream of an answer behind ``/play`` or the HLS proxy.

    The hoster URL stays so the stream can be resolved again: ``/play``
    redirects to ``video_url`` and the HLS proxy fetches it (with
    ``video_headers``) while it is fresh (``resolved_at``), else they
    resolve the hoster URL again. An address-bound file (``address_bound``)
    is fetched by the proxy as well.
    """

    stream_id: str
    hoster_url: str
    title: str = ""
    hoster: str = ""
    video_url: str = ""  # resolved CDN URL
    video_headers: str = ""  # JSON-encoded headers dict for the CDN
    is_hls: bool = False  # whether the stream is HLS
    resolved_at: float = 0.0  # time.time() of video_url's resolution; 0: none
    address_bound: bool = False  # the file plays through /proxy/<id>/file


@dataclass(frozen=True)
class ResolvedStream:
    """Result of resolving a hoster embed URL to an actual video URL.

    Returned by HosterResolverPort implementations.
    """

    video_url: str  # Actual playable URL (.mp4, .m3u8, etc.)
    headers: dict[str, str] = field(default_factory=dict)  # Required request headers
    is_hls: bool = False  # True for .m3u8 playlists
    quality: StreamQuality = StreamQuality.UNKNOWN
    # time.time() of the resolution, stamped by the resolver registry (its
    # cache answers for an hour); 0.0: unknown
    resolved_at: float = 0.0
    # The file's total size, as the playback check read it (Content-Range)
    size_bytes: int | None = None
    # The CDN binds the URL to the address that resolved it (the resolver's
    # ``address_bound``): a player fetches the file through Scavengarr
    address_bound: bool = False


@dataclass(frozen=True)
class StremioStreamRequest:
    """Parsed Stremio stream request.

    Created from URL path: ``tt1234567`` (movie) or
    ``tt1234567:1:5`` (series, season 1, episode 5).
    """

    imdb_id: str
    content_type: StremioContentType
    season: int | None = None
    episode: int | None = None
