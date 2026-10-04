"""Build plugin search queries and title references for Stremio requests.

Pure functions: query normalisation (Unicode transliteration, punctuation
stripping), base-title fallback queries, and per-language-group query and
reference-title construction from TMDB title info.
"""

from __future__ import annotations

import re
from dataclasses import replace

from unidecode import unidecode as _unidecode

from scavengarr.domain.entities.stremio import TitleMatchInfo


def build_search_query(title: str) -> str:
    """Build a search query string from title.

    Returns the plain title without SxxExx suffix — season and episode
    are passed as separate parameters to each plugin so they can
    navigate directly to the correct content.

    Uses ``unidecode`` for universal Unicode→ASCII transliteration
    (130+ scripts), then strips punctuation and normalizes whitespace.
    """
    # 1) Universal Unicode → ASCII transliteration
    text = _unidecode(title)
    # 2) Remove punctuation that breaks site searches (keep hyphens and apostrophes)
    cleaned = re.sub(r"[^\w\s\-']", " ", text)
    # 3) Collapse whitespace
    return " ".join(cleaned.split())


def build_search_queries(title: str) -> list[str]:
    """Build search query variants from a title.

    Returns a deduplicated list of queries in priority order:

    1. Full title (e.g. ``"Dune Part One"``)
    2. Base title before the first colon, if any (e.g. ``"Dune"``)

    Many German streaming sites list subtitled movies without the
    subtitle (``"Dune"`` instead of ``"Dune: Part One"``), so
    searching with only the full title misses results.  The title
    matcher filters out false positives from the shorter query.
    """
    full_query = build_search_query(title)
    queries = [full_query]

    if ":" in title:
        base = title.split(":", maxsplit=1)[0].strip()
        if base:
            base_query = build_search_query(base)
            if base_query and base_query != full_query:
                queries.append(base_query)

    return queries


def build_multi_lang_reference(
    title_infos: dict[str, TitleMatchInfo | None],
    languages: list[str],
) -> TitleMatchInfo | None:
    """Build a TitleMatchInfo combining titles from multiple languages.

    The first available language becomes the primary title; additional
    language titles are merged into ``alt_titles``.  This lets the title
    matcher accept results in any of the plugin's configured languages.
    """
    primary: TitleMatchInfo | None = None
    extra_titles: list[str] = []
    for lang in languages:
        info = title_infos.get(lang)
        if not info:
            continue
        if primary is None:
            primary = info
        else:
            if (
                info.title
                and info.title != primary.title
                and info.title not in extra_titles
            ):
                extra_titles.append(info.title)
            for alt in info.alt_titles:
                if alt and alt != primary.title and alt not in extra_titles:
                    extra_titles.append(alt)
    if primary is None:
        return None
    # Merge, filtering out any that already appear in primary.alt_titles.
    existing = set(primary.alt_titles)
    merged = list(primary.alt_titles) + [t for t in extra_titles if t not in existing]
    return replace(primary, alt_titles=merged)


def first_available_title(
    title_infos: dict[str, TitleMatchInfo | None],
    languages: list[str],
) -> TitleMatchInfo | None:
    """Return the first non-None TitleMatchInfo from the language list."""
    for lang in languages:
        info = title_infos.get(lang)
        if info is not None:
            return info
    return None


def build_lang_group_queries(
    title_infos: dict[str, TitleMatchInfo | None],
    languages: list[str],
) -> list[str]:
    """Search queries of a language group: the titles of its languages.

    Original titles (``alt_titles``) only serve the title matching. As
    queries they cost 23% of the search requests and added 2 of 58
    streams on a 12-title set (production, 2026-10-04).
    """
    queries: list[str] = []
    for lang in languages:
        info = title_infos.get(lang)
        if not info:
            continue
        for q in build_search_queries(info.title):
            if q not in queries:
                queries.append(q)
    return queries
