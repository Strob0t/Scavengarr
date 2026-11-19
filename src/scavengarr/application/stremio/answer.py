"""The streams of a Stremio answer: ranked, measured, served by Scavengarr.

The search results convert into streams in a worker thread (CPU work) and
the sorter ranks them; what a resolution measured (quality, size) can
change a stream's rank. Streams Scavengarr serves point at ``/play/`` or
the HLS proxy, and their links are saved for those to look up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

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
    StremioStreamRequest,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.stream_link_repository import StreamLinkRepository

log = structlog.get_logger(__name__)

# One line per answered stream: which plugin and which link label gave it
# (a stream of the wrong season names its source)
STREAM_ITEM_EVENT = "stremio_stream_item"
IMDB_ID_FIELD = "imdb_id"
REQ_SEASON_FIELD = "req_season"
REQ_EPISODE_FIELD = "req_episode"
PLUGIN_FIELD = "plugin"
HOSTER_FIELD = "hoster"
LINK_LABEL_FIELD = "link_label"
LABEL_SEASON_FIELD = "label_season"
LABEL_EPISODE_FIELD = "label_episode"
STREAM_ID_FIELD = "stream_id"
# Labels are cut to this length in the log
LOG_LABEL_CHARS = 40

# Converts search results into streams: (results, plugin_languages=...)
ConvertFn = Callable[..., list[RankedStream]]

# Where an answer's search results came from (``title_search.SearchSource``),
# or ``none`` for an answer without a search (no title, no plugins)
AnswerSource = Literal["cache", "stale", "search", "joined", "none"]


@dataclass(frozen=True)
class StreamAnswer:
    """A request's streams, and the state of the search behind them.

    The answer is *complete* when no plugin's results are missing from it
    and no search (a continuing one, a refresh or a completion) runs for
    its cache key: a later request would find nothing more.
    """

    streams: list[StremioStream]
    source: AnswerSource
    complete: bool
    missing: tuple[str, ...]  # the plugins whose results the answer lacks


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
    request: StremioStreamRequest | None = None,
) -> list[StremioStream]:
    """Point the streams at Scavengarr and save the links it looks up.

    With a resolve callback (*has_resolver*), the streams in *resolved_map*
    (by index) go through ``/play/`` or, for HLS, the proxy
    (``build_stream_from_resolved``); the others are dropped. Without
    one, every stream goes through ``/play/``. Each answered stream
    gets its link saved; one save per ranked stream (dozens) delayed
    the answer by seconds. Each answered stream logs its source
    (``stremio_stream_item``) for *request*.
    """
    answer: list[tuple[StremioStream, CachedStreamLink]] = []
    sources: dict[str, RankedStream] = {}
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
        sources[sid] = ranked[i]

    unsaved = await save_links(stream_link_repo, [lnk for _, lnk in answer])
    proxied = [s for s, lnk in answer if lnk.stream_id not in unsaved]
    for _, lnk in answer:
        if lnk.stream_id not in unsaved:
            log_stream_item(request, sources[lnk.stream_id], lnk.stream_id)
    skipped_unsaved = len(answer) - len(proxied)
    if skipped_echo or skipped_unresolved or skipped_unsaved:
        log.info(
            "stremio_streams_skipped",
            skipped_echo=skipped_echo,
            skipped_unresolved=skipped_unresolved,
            skipped_unsaved=skipped_unsaved,
        )
    return proxied


def log_stream_item(
    request: StremioStreamRequest | None, ranked: RankedStream, stream_id: str
) -> None:
    """Log one answered stream: the request's numbers, the plugin and the
    link label that gave it (no URL)."""
    log.info(
        STREAM_ITEM_EVENT,
        **{
            IMDB_ID_FIELD: request.imdb_id if request else None,
            REQ_SEASON_FIELD: request.season if request else None,
            REQ_EPISODE_FIELD: request.episode if request else None,
            PLUGIN_FIELD: ranked.source_plugin or None,
            HOSTER_FIELD: ranked.hoster or None,
            LINK_LABEL_FIELD: ranked.link_label[:LOG_LABEL_CHARS] or None,
            LABEL_SEASON_FIELD: ranked.label_season,
            LABEL_EPISODE_FIELD: ranked.label_episode,
            STREAM_ID_FIELD: stream_id,
        },
    )


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
