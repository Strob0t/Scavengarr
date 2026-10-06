"""Build Stremio stream objects from ranked and resolved streams.

Pure functions: formatting, hoster deduplication, direct-video detection,
behaviorHints, cache link construction and proxy URL building.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from typing import Any

from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    RankedStream,
    ResolvedStream,
    StreamQuality,
    StremioStream,
)

_ADDON_NAME = "Scavengarr"

# The proxy path of an HLS stream's playlist: the proxy serves the current
# playlist under it, whatever the CDN names it (a new resolution may change
# the name and its tokens)
HLS_MASTER = "scavengarr.m3u8"

_QUALITY_LABELS: dict[StreamQuality, str] = {
    StreamQuality.UHD_4K: "4K",
    StreamQuality.HD_1080P: "1080p",
    StreamQuality.HD_720P: "720p",
    StreamQuality.SD: "SD",
    StreamQuality.TS: "TS",
    StreamQuality.CAM: "CAM",
}


def format_stream(
    ranked: RankedStream,
    *,
    reference_title: str = "",
    year: int | None = None,
    season: int | None = None,
    episode: int | None = None,
) -> StremioStream:
    """Convert a scored RankedStream into Stremio protocol format.

    Stremio lists streams with ``name`` in a narrow column (addon and
    quality, like other addons) and ``description`` beside it, one short
    line per fact, since long lines are cut off: the site's own title
    (release name, else title, else the reference title with the year;
    titles get the episode for series), then language and size, then
    hoster and source site. The site's title shows a wrong match that the
    reference title would hide.

    ``behaviorHints.bingeGroup`` lets Stremio autoplay the next episode: it
    picks the first stream of the next episode with the same group, so the
    group is the language (a German dub continues in German, at the best
    quality and hoster the next episode has). ``filename`` gives subtitle
    addons the release name to match.
    """
    quality_label = _QUALITY_LABELS.get(ranked.quality)
    name = f"{_ADDON_NAME}\n{quality_label}" if quality_label else _ADDON_NAME

    title = ranked.release_name or ranked.title or reference_title
    if title and not ranked.release_name:
        if season is not None and episode is not None:
            # Sites title the series; the stream was filtered to this episode
            code = f"S{season:02d}E{episode:02d}"
            if code.lower() not in title.lower():
                title = f"{title} {code}"
        elif year and not ranked.title:
            title = f"{title} ({year})"
    details = [ranked.language.label] if ranked.language else []
    if ranked.size:
        details.append(ranked.size)
    source = [part for part in (ranked.hoster.upper(), ranked.source_plugin) if part]
    lines = [title, " · ".join(details), " · ".join(source)]

    language = ranked.language.code if ranked.language else "unknown"
    hints: dict[str, Any] = {"bingeGroup": f"scavengarr|{language}"}
    if ranked.release_name:
        hints["filename"] = ranked.release_name

    return StremioStream(
        name=name,
        description="\n".join(line for line in lines if line),
        url=ranked.url,
        behavior_hints=hints,
    )


_MIB = 2**20
_GIB = 2**30


def format_size(size_bytes: int) -> str:
    """A file size as the sites write it: ``1.4 GB``, ``700 MB`` (binary
    units, as ``parse_size_to_bytes`` reads them)."""
    if size_bytes >= _GIB:
        return f"{size_bytes / _GIB:.1f} GB"
    return f"{max(1, round(size_bytes / _MIB))} MB"


def apply_resolution(ranked: RankedStream, resolved: ResolvedStream) -> RankedStream:
    """*ranked* with what its resolution measured, *ranked* itself when that
    changes nothing.

    A measured quality replaces the site's badge: sites label a release once,
    a hoster may serve another copy. A measured size shows only where the
    site gave none.
    """
    quality = ranked.quality
    if resolved.quality is not StreamQuality.UNKNOWN:
        quality = resolved.quality
    size = ranked.size
    if not size and resolved.size_bytes:
        size = format_size(resolved.size_bytes)
    if quality is ranked.quality and size == ranked.size:
        return ranked
    return replace(ranked, quality=quality, size=size)


def hoster_key(stream: RankedStream) -> tuple[str, str] | None:
    """Dedup key: one stream per hoster and language.

    Dub and sub on the same hoster are different content (anime sites offer
    both). ``None`` for streams without hoster name (no dedup).
    """
    if not stream.hoster:
        return None
    return stream.hoster, stream.language.code if stream.language else ""


def deduplicate_by_hoster(streams: list[RankedStream]) -> list[RankedStream]:
    """Keep only the first (best-ranked) stream per hoster and language.

    The input must already be sorted by rank (best first). Streams without
    hoster name are always kept (no dedup key).
    """
    seen: set[tuple[str, str]] = set()
    result: list[RankedStream] = []
    for s in streams:
        key = hoster_key(s)
        if key is None:
            result.append(s)
        elif key not in seen:
            seen.add(key)
            result.append(s)
    return result


# Video file extensions that Stremio can play directly.
_VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mkv",
        ".m3u8",
        ".ts",
        ".webm",
        ".avi",
        ".flv",
        ".mov",
    }
)


# URL path fragments that indicate a direct video/HLS resource.
_VIDEO_PATH_HINTS = ("master.m3u8", "index.m3u8", "/hls/", "/get_video")


def is_direct_video_url(resolved: ResolvedStream, original_url: str) -> bool:
    """Check whether a resolved stream points to an actual video resource.

    Returns ``False`` when the resolver merely validated availability and
    echoed back the original embed/download page URL (which Stremio cannot
    play).  Returns ``True`` when the resolver extracted a genuine video
    URL (``is_hls``, video extension, or a different URL with playback
    headers).
    """
    if resolved.is_hls:
        return True

    video_url_lower = resolved.video_url.lower()

    # Check for video file extensions
    for ext in _VIDEO_EXTENSIONS:
        if ext in video_url_lower:
            return True

    # Check for known video path patterns (CDN paths, HLS paths)
    for hint in _VIDEO_PATH_HINTS:
        if hint in video_url_lower:
            return True

    # If the resolver returned a *different* URL AND set custom headers
    # (e.g. Referer), it likely performed actual extraction.
    if resolved.video_url != original_url and resolved.headers:
        return True

    return False


def build_behavior_hints(
    resolved: ResolvedStream,
    *,
    user_agent: str,
) -> dict[str, Any]:
    """Build Stremio ``behaviorHints`` from a resolved stream.

    Sets ``notWebReady: true`` so Stremio routes the stream through its
    local streaming server, which applies the ``proxyHeaders`` to every
    request (including Range requests for seeking).

    Headers always include a browser User-Agent. If the resolver provided
    additional headers (e.g. Referer), they are merged in.
    """
    request_headers: dict[str, str] = {"User-Agent": user_agent}
    if resolved.headers:
        request_headers.update(resolved.headers)

    return {
        "notWebReady": True,
        "proxyHeaders": {
            "request": request_headers,
        },
    }


def stream_link_id(hoster_url: str) -> str:
    """The stored link's id of a hoster URL: one link per stream, the same in
    every answer, so a stream object Stremio kept stays valid."""
    return hashlib.sha256(hoster_url.encode()).hexdigest()[:32]


def build_cache_link(
    stream_id: str,
    ranked: RankedStream,
    resolved: ResolvedStream | None,
) -> CachedStreamLink:
    """The link ``/play/`` and ``/proxy/`` look up.

    It keeps the hoster URL for a resolution later and the resolved video
    with the time of its resolution.
    """
    link = CachedStreamLink(
        stream_id=stream_id,
        hoster_url=ranked.url,
        title=ranked.title,
        hoster=ranked.hoster,
    )
    return link if resolved is None else with_resolution(link, resolved)


def with_resolution(
    link: CachedStreamLink, resolved: ResolvedStream
) -> CachedStreamLink:
    """*link* with the video of *resolved*, as ``/play/`` and ``/proxy/``
    read it (headers as JSON, the time of the resolution)."""
    return replace(
        link,
        video_url=resolved.video_url,
        video_headers=json.dumps(resolved.headers) if resolved.headers else "",
        is_hls=resolved.is_hls,
        # A resolution from the registry's cache can be an hour old
        resolved_at=resolved.resolved_at or time.time(),
    )


def build_stream_from_resolved(
    stream: StremioStream,
    resolved: ResolvedStream,
    original_url: str,
    sid: str,
    base_url: str,
    user_agent: str,
) -> StremioStream | None:
    """Build a StremioStream for a resolved result, or ``None`` to skip.

    Returns ``None`` when the resolver only echoed back the original URL
    (embed/download page — Stremio cannot play HTML pages). The playback
    hints are added to the stream's own hints (``bingeGroup``, ``filename``).

    Every stream points at Scavengarr, so it can resolve the hoster URL
    again when Stremio plays the kept stream object later (autoplay of the
    next episode, "Continue Watching"): HLS through the proxy (a redirect
    to a playlist fails on Android, stremio-bugs #1574; the proxy also
    sends the CDN's headers on every sub-request), a file through
    ``/play/`` (a redirect to the current video URL).
    """
    if not is_direct_video_url(resolved, original_url):
        return None

    playback: dict[str, Any]
    if resolved.is_hls:
        url = f"{base_url}/api/v1/stremio/proxy/{sid}/{HLS_MASTER}"
        playback = {"notWebReady": True}
    else:
        url = f"{base_url}/api/v1/stremio/play/{sid}"
        playback = build_behavior_hints(resolved, user_agent=user_agent)
    return replace(
        stream, url=url, behavior_hints={**(stream.behavior_hints or {}), **playback}
    )
