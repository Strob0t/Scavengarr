"""Domain entities for Stremio addon support.

Pure value objects — no framework dependencies, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

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
    """Reference title and year for filtering search results."""

    title: str
    year: int | None = None


@dataclass(frozen=True)
class CachedStreamLink:
    """A cached hoster URL for deferred stream resolution."""

    stream_id: str
    hoster_url: str
    title: str = ""
    hoster: str = ""


@dataclass(frozen=True)
class ResolvedStream:
    """Result of resolving a hoster embed URL to an actual video URL.

    Returned by HosterResolverPort implementations.
    """

    video_url: str  # Actual playable URL (.mp4, .m3u8, etc.)
    headers: dict[str, str] = field(default_factory=dict)  # Required request headers
    is_hls: bool = False  # True for .m3u8 playlists
    quality: StreamQuality = StreamQuality.UNKNOWN


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
