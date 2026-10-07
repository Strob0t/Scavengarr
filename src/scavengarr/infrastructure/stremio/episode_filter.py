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


def _numbers(r: SearchResult) -> tuple[set[int] | None, set[int] | None]:
    """The result's season and episode numbers: the metadata's (``season``,
    ``episode``, ints from plugins that know the episode from the page),
    else guessit's from the title (a set: a multi-episode release such as
    S01E01-E03 names several)."""
    info = guess_release(r.title)
    numbers: list[set[int] | None] = []
    for key in ("season", "episode"):
        value = r.metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            numbers.append({value})
        else:
            guessed = info.get(key)
            numbers.append(None if guessed is None else _ints(guessed))
    return numbers[0], numbers[1]


def _narrow_links(
    r: SearchResult, season: int | None, episode: int | None
) -> SearchResult | None:
    """Keep the links whose episode labels match. With an episode requested
    a result without a labelled link is dropped (``None``): a show page
    would pass with every episode otherwise; so is one whose labelled links
    all name another episode. Without one (a season request) a result
    without labelled links passes unchanged."""
    kept = filter_links_by_episode(r.download_links or [], season, episode)
    if kept is None:
        return None if episode is not None else r
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

    A result's season and episode come from its metadata (``season``,
    ``episode``), else from guessit on its title (``_numbers``). A result
    of another season or episode is dropped. A result without an episode
    number (a show page, a season page, a season pack) passes only through
    the links labelled with the requested episode (``1x5``, ``S01E05``);
    with an episode requested and no such link it is dropped, so a show
    page cannot leak other episodes. A result with an episode but no
    season passes for season 1 only. For a season request results without
    episode information pass unchanged.
    """
    if season is None and episode is None:
        return results

    filtered: list[SearchResult] = []
    for r in results:
        r_season, r_episode = _numbers(r)

        # Season/episode mismatch -> skip (multi-season/-episode releases
        # such as S01E01-E03 give lists and match any of their numbers)
        if season is not None and r_season is not None and season not in r_season:
            continue
        if episode is not None and r_episode is not None and episode not in r_episode:
            continue

        if r_episode is None:
            narrowed = _narrow_links(r, season, episode)
            if narrowed is not None:
                filtered.append(narrowed)
            continue

        if r_season is None and episode is not None and season not in (None, 1):
            # An episode number without a season names season 1
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
