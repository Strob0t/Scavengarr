"""Pick the search hits worth scraping.

Site searches also list loose matches ("Breaking Bad" finds "Better Call
Saul"). Plugins that load detail pages per hit spent most of their time on
those, and bursts of link-outs made some sites gate them. The title matcher
of the Stremio use case drops them later anyway; this keeps them from being
scraped in the first place.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import TypeVar

from unidecode import unidecode

T = TypeVar("T")

_WORD_RE = re.compile(r"[a-z0-9]+")

# Hits scraped when none contains every query word: titles in another
# language ("Money Heist" is "Haus des Geldes" on German sites)
_FALLBACK_HITS = 3

# Hits scraped for a request of one title (a season or an episode): "Dark"
# also finds "Dark Matter", "Dark Winds", … and each costs pages and link-outs
SINGLE_TITLE_HITS = 3


def hit_title(hit: Mapping[str, object]) -> str:
    """The ``title`` of a parsed search hit (plugins parse hits into dicts)."""
    return str(hit.get("title") or "")


def query_words(text: str) -> set[str]:
    """Lower-case ASCII words of *text* ("Pokémon: Die" → {"pokemon", "die"})."""
    return set(_WORD_RE.findall(unidecode(text).lower()))


def relevant_hits(
    hits: list[T],
    query: str,
    title: Callable[[T], str],
    *,
    fallback: int = _FALLBACK_HITS,
    limit: int | None = None,
) -> list[T]:
    """The hits whose title contains every word of *query*, closest first.

    Closest: the fewest words besides the query's (an exact title first);
    ties keep the site's order. Falls back to the site's first *fallback*
    hits when none matches, keeps every hit for an empty query, and returns
    at most *limit* hits (e.g. ``SINGLE_TITLE_HITS`` for a season request).
    With a *limit* (a request for one title), an exact hit drops the longer
    ones: "Dark Matter" is another series than "Dark".
    """
    wanted = query_words(query)
    if not wanted:
        return hits[:limit]
    scored: list[tuple[int, T]] = []
    for hit in hits:
        words = query_words(title(hit))
        if wanted <= words:
            scored.append((len(words - wanted), hit))
    scored.sort(key=lambda pair: pair[0])
    if limit is not None and scored and scored[0][0] == 0:
        # A request for one title: next to an exact hit, longer titles are
        # other titles ("Dark" vs "Dark Matter") the title matcher drops later
        scored = [pair for pair in scored if pair[0] == 0]
    matching = [hit for _, hit in scored]
    return (matching or hits[:fallback])[:limit]
