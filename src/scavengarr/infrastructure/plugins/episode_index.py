"""The episode index of a site that numbers a series' episodes by seasons
of its own (aniworld, s.to).

Such a site lists a long-runner's episodes in Staffeln that are not
IMDb's seasons (One Piece S5E2 is its ``staffel-2/episode-1``), so a
request's numbers do not place the episode there. The index holds every
row of the series' season pages, ``(season, episode, german, english,
absolute)``, cached per series for 7 days; ``locate`` finds the row a
request's ``EpisodeRef`` means: by the absolute number the row shows
(``[Episode 062]`` in the English title) confirmed by the title, else by
the title alone. A plugin passes its page selectors and its fetcher and
logs the outcome itself.
"""

from __future__ import annotations

import asyncio
import html
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz
from selectolax.lexbor import LexborHTMLParser, LexborNode
from unidecode import unidecode

from scavengarr.domain.entities.stremio import EpisodeRef
from scavengarr.domain.ports.cache import CachePort
from scavengarr.infrastructure.plugins.dom import parse_page

INDEX_TTL_SECONDS = 7 * 24 * 3600

# A row within this many numbers of the reference's absolute number is a
# candidate the title confirms (the catalog's position is an estimate: One
# Piece 1088 where the site counts 1089)
_NUMBER_SLACK = 5
# The title match a candidate row needs; the titles are translations of
# the Japanese ones and differ in wording ("The Man Who Will Become the
# Pirate King" against "The Man Who's Gonna Be King of the Pirates": 0.82)
_NEIGHBOUR_MIN = 0.6
# The title match a row needs without a number
_TITLE_MIN = 0.85

_ABSOLUTE_RE = re.compile(r"\[?\s*episode\s+(\d+)\s*\]?\s*$", re.IGNORECASE)
_DIGITS_RE = re.compile(r"\d+")
_PUNCT_RE = re.compile(r"[^\w\s]")

LocatedBy = Literal["number", "title"]


@dataclass(frozen=True)
class EpisodeRow:
    """One row of a season page: the site's season and episode, the titles
    and the absolute number when the row shows one."""

    season: int
    episode: int
    german: str
    english: str
    absolute: int | None = None


@dataclass(frozen=True)
class RowSelectors:
    """Where a season page keeps its rows (CSS, selectolax): the rows, and
    within a row the number cell (its *number_attr*, else its text), the
    German and the English title."""

    row: str
    number: str
    german: str
    english: str
    number_attr: str | None = None


@dataclass(frozen=True)
class Located:
    row: EpisodeRow
    by: LocatedBy


def normalize_title(text: str) -> str:
    """Lower-case ASCII words: entities decoded (the sites escape their
    titles twice), accents transliterated, punctuation dropped."""
    text = unidecode(html.unescape(text)).lower()
    return " ".join(_PUNCT_RE.sub(" ", text).split())


def _text(node: LexborNode | None) -> str:
    return node.text().strip() if node is not None else ""


class SeasonPageParser:
    """The rows of one season page."""

    def __init__(self, season: int, selectors: RowSelectors) -> None:
        self._season = season
        self._selectors = selectors
        self.rows: list[EpisodeRow] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for node in tree.css(self._selectors.row):
            number = self._number(node)
            if number is None:
                continue
            english = _text(node.css_first(self._selectors.english))
            absolute: int | None = None
            found = _ABSOLUTE_RE.search(english)
            if found:
                absolute = int(found.group(1))
                english = english[: found.start()].strip()
            self.rows.append(
                EpisodeRow(
                    self._season,
                    number,
                    _text(node.css_first(self._selectors.german)),
                    english,
                    absolute,
                )
            )

    def _number(self, row: LexborNode) -> int | None:
        cell = row.css_first(self._selectors.number)
        if cell is None:
            return None
        attr = self._selectors.number_attr
        raw = (cell.attributes.get(attr) or "") if attr else cell.text()
        found = _DIGITS_RE.search(raw)
        return int(found.group()) if found else None


def _cached_rows(value: object) -> list[EpisodeRow] | None:
    """The rows a cache entry holds; ``None`` for anything else."""
    if not isinstance(value, list) or not value:
        return None
    rows: list[EpisodeRow] = []
    for item in value:
        if not (
            isinstance(item, list)
            and len(item) == 5
            and isinstance(item[0], int)
            and isinstance(item[1], int)
            and isinstance(item[2], str)
            and isinstance(item[3], str)
            and (item[4] is None or isinstance(item[4], int))
        ):
            return None
        rows.append(EpisodeRow(item[0], item[1], item[2], item[3], item[4]))
    return rows


async def episode_index(
    *,
    cache: CachePort | None,
    key: str,
    seasons: Iterable[int],
    season_url: Callable[[int], str],
    fetch_html: Callable[[str], Awaitable[str | None]],
    selectors: RowSelectors,
    semaphore: asyncio.Semaphore,
) -> list[EpisodeRow]:
    """The rows of the series' regular season pages (season 1 and up), from
    the cache under *key* or read through *fetch_html* under *semaphore*.

    The rows are stored for ``INDEX_TTL_SECONDS`` as plain lists. An index
    missing a page the site did not answer serves the request but is not
    stored, so the next request reads the page again.
    """
    if cache is not None:
        cached = _cached_rows(await cache.get(key))
        if cached is not None:
            return cached

    async def _season(number: int) -> list[EpisodeRow] | None:
        async with semaphore:
            page = await fetch_html(season_url(number))
        if page is None:
            return None
        parser = await parse_page(SeasonPageParser(number, selectors), page)
        return parser.rows

    pages = await asyncio.gather(
        *(_season(n) for n in sorted({n for n in seasons if n >= 1}))
    )
    rows = [row for page in pages if page is not None for row in page]
    if cache is not None and rows and all(page is not None for page in pages):
        stored = [[r.season, r.episode, r.german, r.english, r.absolute] for r in rows]
        await cache.set(key, stored, ttl=INDEX_TTL_SECONDS)
    return rows


def _best(rows: Iterable[EpisodeRow], title: str) -> tuple[EpisodeRow, float] | None:
    """The row whose English title matches *title* (normalised) best."""
    best: tuple[EpisodeRow, float] | None = None
    for row in rows:
        if not row.english:
            continue
        score = (
            fuzz.token_set_ratio(title, normalize_title(row.english), processor=None)
            / 100
        )
        if best is None or score > best[1]:
            best = (row, score)
    return best


def locate(index: list[EpisodeRow], ref: EpisodeRef) -> Located | None:
    """The row *ref* means, or ``None``.

    1. With an absolute number: the rows numbered within ``_NUMBER_SLACK``
       of it, the best title match of at least ``_NEIGHBOUR_MIN`` among
       them; the row with the number itself when there is no title to
       compare (the reference has none, or the row shows the number in
       place of a title, as s.to's anime rows do).
    2. The row whose normalised English title equals the reference's.
    3. The best title match of at least ``_TITLE_MIN`` over the index.
    """
    title = normalize_title(ref.title) if ref.title else ""
    if ref.absolute is not None:
        near = [
            row
            for row in index
            if row.absolute is not None
            and abs(row.absolute - ref.absolute) <= _NUMBER_SLACK
        ]
        exact = next((row for row in near if row.absolute == ref.absolute), None)
        if exact is not None and (not title or not exact.english):
            return Located(exact, "number")
        if title:
            best = _best(near, title)
            if best is not None and best[1] >= _NEIGHBOUR_MIN:
                return Located(best[0], "number")
    if title:
        for row in index:
            if row.english and normalize_title(row.english) == title:
                return Located(row, "title")
        best = _best(index, title)
        if best is not None and best[1] >= _TITLE_MIN:
            return Located(best[0], "title")
    return None
