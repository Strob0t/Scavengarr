"""Season/episode filtering of search results for Stremio series requests.

Uses guessit to parse release names; falls back to episode labels in
``download_links`` (e.g. ``1x5`` from episode tabs) when the title has
no parseable season/episode info.
"""

from __future__ import annotations

import re
from dataclasses import replace

import structlog

from scavengarr.domain.plugins.base import SearchResult, link_url
from scavengarr.infrastructure.stremio.release_guess import guess_release

log = structlog.get_logger(__name__)


# Matches episode labels in download_links.
# Patterns: "1x5", "1x05", "2X10", "S01E05", "s1e5", "S02E10 Episode Title".
_EPISODE_LABEL_RE = re.compile(
    r"(?:^|\D)"
    r"(?:"
    r"(\d{1,2})\s*[xX]\s*(\d{1,4})"  # 1x5, 2X10
    r"|"
    r"[Ss](\d{1,2})\s*[Ee](\d{1,4})"  # S01E05, s1e5
    r")"
    r"(?:\D|$)"
)


def parse_episode_from_label(label: str) -> tuple[int | None, int | None]:
    """Extract (season, episode) from a download_link label.

    Recognises patterns like ``1x5``, ``1x05``, ``2x10``,
    ``S01E05``, ``s1e5``.
    Returns ``(None, None)`` when no pattern is found.
    """
    m = _EPISODE_LABEL_RE.search(label)
    if m:
        # Groups 1,2 for NxM pattern; groups 3,4 for SxxExx pattern
        season = m.group(1) if m.group(1) is not None else m.group(3)
        episode = m.group(2) if m.group(2) is not None else m.group(4)
        return int(season), int(episode)
    return None, None


def filter_links_by_episode(
    links: list[dict[str, str]],
    season: int | None,
    episode: int | None,
) -> list[dict[str, str]] | None:
    """Filter download_links by episode info in their labels.

    Returns:
        List of matching links when at least one link had episode info.
        ``None`` when no links contained parseable episode labels
        (meaning the filter cannot be applied).
    """
    matched: list[dict[str, str]] = []
    has_episode_info = False

    for link in links:
        label = link.get("label", "")
        l_season, l_episode = parse_episode_from_label(label)

        if l_season is None and l_episode is None:
            # No episode info in this link — skip (orphaned mirror)
            continue

        has_episode_info = True

        if season is not None and l_season is not None and l_season != season:
            continue
        if episode is not None and l_episode is not None and l_episode != episode:
            continue

        matched.append(link)

    if not has_episode_info:
        return None

    return matched


def _ints(value: object) -> set[int]:
    """guessit gives an int, or a list for multi-season/-episode releases."""
    items = value if isinstance(value, list) else [value]
    return {v for v in items if isinstance(v, int)}


def _narrow_links(
    r: SearchResult, season: int | None, episode: int | None
) -> SearchResult | None:
    """Keep the links whose episode labels match; ``r`` unchanged when no
    link has an episode label, ``None`` when every labelled link is wrong."""
    if not r.download_links:
        return r
    kept = filter_links_by_episode(r.download_links, season, episode)
    if kept is None:
        return r
    if not kept:
        return None
    first_url = link_url(kept[0]) or r.download_link
    return replace(r, download_link=first_url, download_links=kept)


def filter_by_episode(
    results: list[SearchResult],
    season: int | None,
    episode: int | None,
) -> list[SearchResult]:
    """Filter results to match the requested season/episode.

    Uses guessit to parse the titles. A title of another season or
    episode drops the result. A title without an episode (a show or
    season page, a season pack) falls back to filtering its
    download_links by their labels (e.g. ``1x5`` from episode tabs).

    Results whose title and links carry no episode info are kept -- they
    might be different hosters for a single content page.
    """
    if season is None and episode is None:
        return results

    filtered: list[SearchResult] = []
    for r in results:
        info = guess_release(r.title)
        r_season = info.get("season")
        r_episode = info.get("episode")

        # Season/episode mismatch -> skip (multi-season/-episode releases
        # such as S01E01-E03 give lists and match any of their numbers)
        if (
            season is not None
            and r_season is not None
            and season not in _ints(r_season)
        ):
            continue
        if (
            episode is not None
            and r_episode is not None
            and episode not in _ints(r_episode)
        ):
            continue

        if r_episode is None:
            narrowed = _narrow_links(r, season, episode)
            if narrowed is not None:
                filtered.append(narrowed)
            continue

        filtered.append(r)

    if len(filtered) < len(results):
        log.debug(
            "episode_filter_applied",
            season=season,
            episode=episode,
            before=len(results),
            after=len(filtered),
        )
    return filtered
