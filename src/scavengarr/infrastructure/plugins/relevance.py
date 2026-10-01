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
) -> list[T]:
    """The hits whose title contains every word of *query*, in the site's order.

    Falls back to the site's first *fallback* hits when none does, and keeps
    every hit for an empty query.
    """
    wanted = query_words(query)
    if not wanted:
        return hits
    matching = [hit for hit in hits if wanted <= query_words(title(hit))]
    return matching or hits[:fallback]
