"""Build Stremio stream objects from ranked and resolved streams.

Pure functions: formatting, hoster deduplication, direct-video detection,
behaviorHints, cache link construction and proxy URL building.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    RankedStream,
    ResolvedStream,
    StreamQuality,
    StremioStream,
)


def format_stream(
    ranked: RankedStream,
    *,
    reference_title: str = "",
    year: int | None = None,
    season: int | None = None,
    episode: int | None = None,
) -> StremioStream:
    """Convert a scored RankedStream into Stremio protocol format.

    When *reference_title* (from TMDB) is available it is always used as
    the stream name, enriched with year (movies) or season/episode (series).
    Falls back to release_name → ranked.title → source_plugin + quality.

    The description always starts with the source plugin name so that
    users can see which site the stream came from.
    """
    quality_label = ranked.quality.name.replace("_", " ")
    show_quality = ranked.quality != StreamQuality.UNKNOWN

    # --- Build name (reference title has priority) ---
    title = reference_title or ranked.title
    if title:
        if season is not None and episode is not None:
            name = f"{title} S{season:02d}E{episode:02d}"
        elif year:
            name = f"{title} ({year})"
        else:
            name = title
        if show_quality:
            name = f"{name} {quality_label}"
    elif ranked.release_name:
        name = ranked.release_name
    else:
        name = (
            f"{ranked.source_plugin} {quality_label}"
            if ranked.source_plugin
            else quality_label
        )

    # --- Build description (source plugin always first) ---
    desc_parts: list[str] = []
    if ranked.source_plugin:
        desc_parts.append(ranked.source_plugin)
    if ranked.language:
        desc_parts.append(ranked.language.label)
    if ranked.hoster:
        desc_parts.append(ranked.hoster.upper())
    if ranked.size:
        desc_parts.append(ranked.size)

    return StremioStream(
        name=name,
        description=" | ".join(desc_parts) if desc_parts else "",
        url=ranked.url,
    )


def deduplicate_by_hoster(streams: list[RankedStream]) -> list[RankedStream]:
    """Keep only the first (best-ranked) stream per hoster.

    The input must already be sorted by rank (best first).  For each
    hoster name, only the first occurrence is kept.  Streams with an
    empty hoster string are always kept (no dedup key).
    """
    seen: set[str] = set()
    result: list[RankedStream] = []
    for s in streams:
        if not s.hoster:
            result.append(s)
            continue
        if s.hoster not in seen:
            seen.add(s.hoster)
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


def build_cache_links(
    stream_ids: list[str],
    ranked: list[RankedStream],
    resolved_map: dict[int, ResolvedStream],
) -> list[CachedStreamLink]:
    """Build ``CachedStreamLink`` objects, enriching HLS streams with proxy metadata."""
    links: list[CachedStreamLink] = []
    for i, (sid, ranked_s) in enumerate(zip(stream_ids, ranked)):
        resolved = resolved_map.get(i)
        extra_kwargs: dict[str, str | bool] = {}
        if resolved is not None and resolved.is_hls and resolved.headers:
            extra_kwargs["video_url"] = resolved.video_url
            extra_kwargs["video_headers"] = json.dumps(resolved.headers)
            extra_kwargs["is_hls"] = True
        links.append(
            CachedStreamLink(
                stream_id=sid,
                hoster_url=ranked_s.url,
                title=ranked_s.title,
                hoster=ranked_s.hoster,
                **extra_kwargs,
            )
        )
    return links


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
    (embed/download page — Stremio cannot play HTML pages).
    """
    if not is_direct_video_url(resolved, original_url):
        return None

    if resolved.is_hls and resolved.headers:
        # HLS stream requiring headers on sub-requests — route through
        # our proxy so manifests/segments get the correct Referer etc.
        # Preserve the original filename and CDN auth query params.
        parsed_video = urlparse(resolved.video_url)
        manifest_name = parsed_video.path.rsplit("/", 1)[-1] or "master.m3u8"
        qs = f"?{parsed_video.query}" if parsed_video.query else ""
        proxy_url = f"{base_url}/api/v1/stremio/proxy/{sid}/{manifest_name}{qs}"
        return StremioStream(
            name=stream.name,
            description=stream.description,
            url=proxy_url,
            behavior_hints={"notWebReady": True},
        )

    # Direct video URL (MP4 or HLS without special headers)
    hints = build_behavior_hints(resolved, user_agent=user_agent)
    return StremioStream(
        name=stream.name,
        description=stream.description,
        url=resolved.video_url,
        behavior_hints=hints,
    )
