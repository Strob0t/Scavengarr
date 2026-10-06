"""The streams of a Stremio answer: ranked, measured, served by Scavengarr.

The search results convert into streams in a worker thread (CPU work) and
the sorter ranks them; what a resolution measured (quality, size) can
change a stream's rank. Streams Scavengarr serves point at ``/play/`` or
the HLS proxy, and their links are saved for those to look up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace

import structlog

from scavengarr.application.stremio.stream_builder import (
    apply_resolution,
    build_cache_link,
    build_stream_from_resolved,
    stream_link_id,
)
from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    RankedStream,
    ResolvedStream,
    StremioStream,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.stream_link_repository import StreamLinkRepository

log = structlog.get_logger(__name__)

# Converts search results into streams: (results, plugin_languages=...)
ConvertFn = Callable[..., list[RankedStream]]


async def rank_streams(
    results: list[SearchResult],
    plugin_languages: dict[str, str],
    *,
    convert_fn: ConvertFn,
    sort: Callable[[list[RankedStream]], list[RankedStream]],
) -> list[RankedStream]:
    """The streams of *results*, best first (the conversion in a worker
    thread: CPU work)."""
    converted = await asyncio.to_thread(
        lambda: convert_fn(results, plugin_languages=plugin_languages)
    )
    return sort(converted)


def with_measurements(
    ranked: list[RankedStream],
    resolved: dict[int, ResolvedStream],
    *,
    rank_score: Callable[[RankedStream], int],
) -> tuple[list[RankedStream], dict[int, ResolvedStream]]:
    """The streams with what their resolutions measured (quality, size;
    ``apply_resolution``) and the resolutions by index.

    A changed quality changes the rank: the streams are sorted again
    (stable, like the sorter), the resolutions follow their streams.
    """
    merged = [
        apply_resolution(s, resolved[i]) if i in resolved else s
        for i, s in enumerate(ranked)
    ]
    if all(m.quality is s.quality for m, s in zip(merged, ranked, strict=True)):
        return merged, resolved
    scores = [rank_score(s) for s in merged]
    order = sorted(range(len(merged)), key=scores.__getitem__, reverse=True)
    return (
        [replace(merged[old], rank_score=scores[old]) for old in order],
        {new: resolved[old] for new, old in enumerate(order) if old in resolved},
    )


async def cache_and_proxy(
    streams: list[StremioStream],
    ranked: list[RankedStream],
    resolved_map: dict[int, ResolvedStream],
    base_url: str,
    *,
    stream_link_repo: StreamLinkRepository,
    has_resolver: bool,
    user_agent: str,
) -> list[StremioStream]:
    """Point the streams at Scavengarr and save the links it looks up.

    With a resolve callback (*has_resolver*), the streams in *resolved_map*
    (by index) go through ``/play/`` or, for HLS, the proxy
    (``build_stream_from_resolved``); the others are dropped. Without
    one, every stream goes through ``/play/``. Each answered stream
    gets its link saved; one save per ranked stream (dozens) delayed
    the answer by seconds.
    """
    answer: list[tuple[StremioStream, CachedStreamLink]] = []
    skipped_echo = 0
    skipped_unresolved = 0
    for i, stream in enumerate(streams):
        sid = stream_link_id(ranked[i].url)
        resolved = resolved_map.get(i)
        if resolved is not None:
            built = build_stream_from_resolved(
                stream, resolved, ranked[i].url, sid, base_url, user_agent
            )
            if built is None:
                skipped_echo += 1
                continue
        elif has_resolver:
            # Resolver is configured but returned None — skip this stream.
            # The /play/ proxy would also fail (502).
            skipped_unresolved += 1
            continue
        else:
            # No resolver configured — proxy through /play/ endpoint
            built = replace(stream, url=f"{base_url}/api/v1/stremio/play/{sid}")
        answer.append((built, build_cache_link(sid, ranked[i], resolved)))

    unsaved = await save_links(stream_link_repo, [lnk for _, lnk in answer])
    proxied = [s for s, lnk in answer if lnk.stream_id not in unsaved]
    skipped_unsaved = len(answer) - len(proxied)
    if skipped_echo or skipped_unresolved or skipped_unsaved:
        log.info(
            "stremio_streams_skipped",
            skipped_echo=skipped_echo,
            skipped_unresolved=skipped_unresolved,
            skipped_unsaved=skipped_unsaved,
        )
    return proxied


async def save_links(
    stream_link_repo: StreamLinkRepository, links: list[CachedStreamLink]
) -> set[str]:
    """Save the links in parallel; return the stream ids not saved."""
    outcomes = await asyncio.gather(
        *(stream_link_repo.save(lnk) for lnk in links),
        return_exceptions=True,
    )
    errors = [
        (lnk.stream_id, outcome)
        for lnk, outcome in zip(links, outcomes, strict=True)
        if isinstance(outcome, BaseException)
    ]
    if errors:
        log.warning(
            "stremio_stream_link_save_failed",
            count=len(errors),
            error=str(errors[0][1]),
        )
    return {sid for sid, _ in errors}
