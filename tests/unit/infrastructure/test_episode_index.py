"""The episode index of a site that numbers a series' episodes by seasons
of its own (aniworld, s.to): the rows of its season pages, cached, and the
row a request's ``EpisodeRef`` means."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from scavengarr.domain.entities.stremio import EpisodeRef
from scavengarr.infrastructure.plugins.episode_index import (
    INDEX_TTL_SECONDS,
    EpisodeRow,
    Located,
    RowSelectors,
    SeasonPageParser,
    episode_index,
    is_partial_series,
    locate,
    normalize_title,
)

# aniworld's season page rows
_SELECTORS = RowSelectors(
    row="table.seasonEpisodesList tbody tr[data-episode-id]",
    number="meta[itemprop=episodeNumber]",
    number_attr="content",
    german="td.seasonEpisodeTitle strong",
    english="td.seasonEpisodeTitle span",
)

_LUFFY_SITE = "I'm Luffy! The Man Who Will Become the Pirate King!"
_LUFFY_CATALOG = "I'm Luffy! The Man Who's Gonna Be King of the Pirates!"
_LABOON = "The First Line of Defense? The Giant Whale Laboon Appears!"
_PROMISE = "A Promise Between Men! Luffy and the Whale Vow to Meet Again!"
_SABO = "Entering a New Chapter! Luffy and Sabo's Paths!"


def _row(season: int, episode: int, english: str, absolute: int | None) -> EpisodeRow:
    return EpisodeRow(season, episode, f"Folge {episode}", english, absolute)


# One Piece as aniworld lists it: seasons of its own, the absolute number
# in every English title
_ONE_PIECE = [
    _row(1, 1, _LUFFY_SITE, 1),
    _row(1, 2, "The Great Swordsman Appears! Pirate Hunter, Roronoa Zoro", 2),
    _row(1, 3, "Morgan vs. Luffy! Who's This Beautiful Young Girl?", 3),
    _row(2, 1, _LABOON, 62),
    _row(2, 2, _PROMISE, 63),
    _row(2, 3, "An Angry Showdown! Cross the Red Line!", 64),
    _row(22, 1, "Egghead Approaches! The Island of the Future!", 1088),
    _row(22, 2, _SABO, 1089),
    _row(22, 3, "The Laboratory of a Genius! Vegapunk Appears!", 1090),
]

# Demon Slayer: titles only
_DEMON_SLAYER = [
    _row(1, 1, "Cruelty", None),
    _row(1, 2, "Trainer Sakonji Urokodaki", None),
    _row(3, 1, "Someone&#039;s Dream", None),
    _row(3, 2, "Yoriichi Type Zero", None),
]

# s.to's anime rows: the number, no English title
_STO_ANIME = [
    EpisodeRow(2, 1, "Ein Bad in Magensäure", "", 62),
    EpisodeRow(2, 2, "Das Versprechen", "", 63),
]


# Detective Conan as aniworld lists it: the dub's English titles and the
# Japanese count's absolute number, which the parts of a double-length
# episode share
_CONAN = [
    _row(1, 8, "The Art Museum Murder Case", 8),
    _row(1, 11, "The Moonlight Sonata Murder Case (1/2)", 11),
    _row(1, 12, "The Moonlight Sonata Murder Case (2/2)", 11),
    _row(1, 14, "Mystery Mastermind", 13),
    _row(6, 22, "Metropolitan Police Detective Love Story 2 (1)", 156),
    _row(6, 23, "Metropolitan Police Detective Love Story 2 (2)", 157),
]


class TestLocate:
    def test_located_by_number(self) -> None:
        ref = EpisodeRef(5, 2, _LABOON, "2001-03-21", absolute=62)

        assert locate(_ONE_PIECE, ref) == Located(_ONE_PIECE[3], "number")

    def test_the_numbers_row_wins_against_the_part_before(self) -> None:
        # The second part's title is a word subset of both parts' (1.00 for
        # each); the earlier row took the tie
        ref = EpisodeRef(
            7, 12, "Metropolitan Police Detective Love Story 2 (2)", absolute=157
        )

        assert locate(_CONAN, ref) == Located(_CONAN[5], "number")

    def test_the_numbers_row_wins_against_a_neighbour_with_a_better_title(
        self,
    ) -> None:
        # "The Art Museum Murder Case" matches 0.73, the number's row 0.38
        ref = EpisodeRef(1, 13, "The Strange Person Hunt Murder Case", absolute=13)

        assert locate(_CONAN, ref) == Located(_CONAN[3], "number")

    def test_the_numbers_row_wins_against_an_exact_title_elsewhere(self) -> None:
        # The number points at season 1, the title names the Laboon episode
        ref = EpisodeRef(5, 2, _LABOON, "2001-03-21", absolute=3)

        assert locate(_ONE_PIECE, ref) == Located(_ONE_PIECE[2], "number")

    def test_a_number_on_several_rows_places_the_first(self) -> None:
        # One double-length episode, its two parts on the site
        ref = EpisodeRef(1, 11, "Moonlight Sonata Murder Case", absolute=11)

        assert locate(_CONAN, ref) == Located(_CONAN[1], "number")

    def test_without_a_title_the_number_alone_counts(self) -> None:
        assert locate(_ONE_PIECE, EpisodeRef(5, 2, absolute=62)) == Located(
            _ONE_PIECE[3], "number"
        )
        # a neighbour is no evidence without a title
        assert locate(_ONE_PIECE, EpisodeRef(5, 1, absolute=61)) is None

    def test_rows_without_english_titles_count_by_the_number_alone(self) -> None:
        # s.to's anime rows show "Episode 062" where aniworld shows the title
        ref = EpisodeRef(5, 2, _LABOON, "2001-03-21", absolute=62)

        assert locate(_STO_ANIME, ref) == Located(_STO_ANIME[0], "number")
        assert locate(_STO_ANIME, EpisodeRef(5, 3, _PROMISE, absolute=61)) is None

    def test_the_title_confirms_the_neighbour_of_a_number_the_site_lacks(
        self,
    ) -> None:
        # The catalog's position is 1088, the site counts 1089 and shows no 1088
        index = [row for row in _ONE_PIECE if row.absolute != 1088]
        ref = EpisodeRef(22, 4, _SABO, "2024-01-07", absolute=1088)

        assert locate(index, ref) == Located(_ONE_PIECE[7], "neighbour")

    def test_a_variant_translation_confirms_the_neighbour(self) -> None:
        # The catalog counts 2, the site 1 and shows no 2; the translations
        # differ in wording (0.82)
        index = [row for row in _ONE_PIECE if row.absolute != 2]
        ref = EpisodeRef(1, 2, _LUFFY_CATALOG, "1999-10-20", absolute=2)

        assert locate(index, ref) == Located(_ONE_PIECE[0], "neighbour")

    def test_without_the_numbers_row_the_exact_title_places_it(self) -> None:
        # A number the site lacks, far from the episode's own
        ref = EpisodeRef(5, 2, _LABOON, "2001-03-21", absolute=500)

        assert locate(_ONE_PIECE, ref) == Located(_ONE_PIECE[3], "title")

    def test_without_a_number_the_title_places_it(self) -> None:
        ref = EpisodeRef(5, 2, _LABOON, "2001-03-21")

        assert locate(_ONE_PIECE, ref) == Located(_ONE_PIECE[3], "title")

    def test_located_by_the_exact_title_where_the_site_shows_no_numbers(self) -> None:
        ref = EpisodeRef(4, 1, "Someone's Dream", "2023-04-09", absolute=45)

        assert locate(_DEMON_SLAYER, ref) == Located(_DEMON_SLAYER[2], "title")

    def test_located_by_the_best_fuzzy_title(self) -> None:
        ref = EpisodeRef(4, 1, "Someone's Dreams", "2023-04-09", absolute=45)

        assert locate(_DEMON_SLAYER, ref) == Located(_DEMON_SLAYER[2], "fuzzy_title")

    def test_a_loose_title_match_is_no_location(self) -> None:
        ref = EpisodeRef(4, 2, "Yoriichi's Dream", "2023-04-16", absolute=46)

        assert locate(_DEMON_SLAYER, ref) is None

    def test_not_located(self) -> None:
        assert (
            locate(_ONE_PIECE, EpisodeRef(9, 9, "Nothing Like It", absolute=999))
            is None
        )
        assert locate(_ONE_PIECE, EpisodeRef(9, 9)) is None
        assert locate([], EpisodeRef(5, 2, _LABOON, absolute=62)) is None


# Bleach as the sites list it: the Thousand-Year Blood War is its season 17,
# numbered on from the series
_BLEACH = [
    _row(1, 1, "The Day I Became a Shinigami", 1),
    _row(1, 13, "Ichigo and Rukia, Thoughts in the Rain", 13),
    _row(17, 1, "The Blood Warfare", 367),
    _row(17, 13, "The Blade Is Me", 379),
]
_TYBW = ("Bleach: Thousand Year Blood War", "Bleach: Thousand-Year Blood War")


class TestPartialSeries:
    """A site's series named only by a shorter form of the request's titles
    (Bleach for the Thousand-Year Blood War) is partial: the reference's
    number counts the request's series, so the title alone places the
    episode there."""

    def test_the_parent_is_a_partial_series(self) -> None:
        ref = EpisodeRef(1, 13, "The Blade Is Me", absolute=13, titles=_TYBW)

        assert is_partial_series(["Bleach"], ref)
        assert not is_partial_series(["Bleach: Thousand-Year Blood War"], ref)

    def test_the_full_title_is_not_partial(self) -> None:
        ref = EpisodeRef(1, 6, absolute=6, titles=("Naruto: Shippūden",))

        assert not is_partial_series(["Naruto Shippuden"], ref)

    def test_without_the_requests_titles_no_series_is_partial(self) -> None:
        assert not is_partial_series(["Bleach"], EpisodeRef(1, 13, absolute=13))

    def test_a_sequel_kept_as_the_parents_season_is_located_by_title(self) -> None:
        ref = EpisodeRef(1, 13, "The Blade Is Me", absolute=13, titles=_TYBW)

        # never on Bleach's own row 13
        assert locate(_BLEACH, ref, partial=True) == Located(_BLEACH[3], "title")
        assert locate(_BLEACH, ref) == Located(_BLEACH[1], "number")

    def test_the_best_fuzzy_title_places_it_too(self) -> None:
        ref = EpisodeRef(1, 1, "The Blood Warfares", absolute=1, titles=_TYBW)

        assert locate(_BLEACH, ref, partial=True) == Located(_BLEACH[2], "fuzzy_title")

    def test_no_neighbour_on_a_partial_series(self) -> None:
        # The neighbour rule needs the number: the variant translation (0.82)
        # is no fuzzy title (0.85)
        index = [row for row in _ONE_PIECE if row.absolute != 2]
        ref = EpisodeRef(1, 2, _LUFFY_CATALOG, absolute=2)

        assert locate(index, ref) == Located(_ONE_PIECE[0], "neighbour")
        assert locate(index, ref, partial=True) is None

    def test_without_a_title_match_a_partial_series_locates_nothing(self) -> None:
        naruto = [_row(1, 6, "Dangerous Mission! Journey to the Land of Waves!", 6)]
        ref = EpisodeRef(1, 6, "Mission Cleared", absolute=6)

        assert locate(naruto, ref, partial=True) is None
        assert locate(naruto, EpisodeRef(1, 6, absolute=6), partial=True) is None


class TestNormalizeTitle:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Someone&#039;s Dream", "someone s dream"),
            ("Ein Bad in Magensäure", "ein bad in magensaure"),
            ("  Morgan vs. Luffy!  Who's This? ", "morgan vs luffy who s this"),
            ("", ""),
        ],
    )
    def test_entities_accents_and_punctuation_go(
        self, text: str, expected: str
    ) -> None:
        assert normalize_title(text) == expected


def _season_html(rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<tr data-episode-id="{n}" itemprop="episode">'
        f'<td class="season2EpisodeID"><meta itemprop="episodeNumber" content="{n}" />'
        f'<a href="/anime/stream/one-piece/staffel-2/episode-{n}">Folge {n}</a></td>'
        '<td class="seasonEpisodeTitle">'
        f'<a href="/anime/stream/one-piece/staffel-2/episode-{n}">{cells}</a></td>'
        "<td></td></tr>"
        for n, cells in enumerate(
            (
                f"<strong>{german}</strong> - <span>{english}</span>"
                for german, english in rows
            ),
            start=1,
        )
    )
    return (
        '<html><body><table class="seasonEpisodesList" data-season-id="2">'
        f"<thead><tr><th>Folge</th></tr></thead><tbody>{body}</tbody></table></body></html>"
    )


class TestSeasonPageParser:
    def test_rows_with_the_absolute_number_in_the_english_title(self) -> None:
        parser = SeasonPageParser(2, _SELECTORS)
        parser.feed(
            _season_html(
                [
                    ("Ein Bad in Magensäure", f"{_LABOON} [Episode 062]"),
                    ("Das Versprechen", f"{_PROMISE} [Episode 063]"),
                ]
            )
        )

        assert parser.rows == [
            EpisodeRow(2, 1, "Ein Bad in Magensäure", _LABOON, 62),
            EpisodeRow(2, 2, "Das Versprechen", _PROMISE, 63),
        ]

    def test_rows_without_numbers(self) -> None:
        parser = SeasonPageParser(3, _SELECTORS)
        parser.feed(_season_html([("Jemandes Traum", "Someone&#039;s Dream")]))

        assert parser.rows == [
            EpisodeRow(3, 1, "Jemandes Traum", "Someone's Dream", None)
        ]

    def test_a_number_alone_leaves_no_english_title(self) -> None:
        # s.to's anime rows
        parser = SeasonPageParser(2, _SELECTORS)
        parser.feed(_season_html([("Ein Bad in Magensäure", "Episode 062")]))

        assert parser.rows == [EpisodeRow(2, 1, "Ein Bad in Magensäure", "", 62)]

    def test_a_note_in_brackets_is_no_number(self) -> None:
        parser = SeasonPageParser(3, _SELECTORS)
        parser.feed(
            _season_html(
                [("Morgendämmerung", "Daybreak [Two Episodes air on May 4th]")]
            )
        )

        assert parser.rows[0].absolute is None
        assert parser.rows[0].english == "Daybreak [Two Episodes air on May 4th]"

    def test_a_row_without_a_number_cell_is_skipped(self) -> None:
        parser = SeasonPageParser(1, _SELECTORS)
        parser.feed(
            '<table class="seasonEpisodesList"><tbody><tr data-episode-id="1">'
            '<td class="seasonEpisodeTitle"><a><strong>Folge</strong></a></td></tr>'
            "</tbody></table>"
        )

        assert parser.rows == []

    def test_an_empty_page(self) -> None:
        parser = SeasonPageParser(1, _SELECTORS)
        parser.feed("<html><body></body></html>")

        assert parser.rows == []


def _fetcher(pages: dict[str, str | None]) -> tuple[Any, list[str]]:
    urls: list[str] = []

    async def fetch_html(url: str) -> str | None:
        urls.append(url)
        return pages[url]

    return fetch_html, urls


_PAGE_1 = _season_html([("Hier kommt Ruffy", f"{_LUFFY_SITE} [Episode 001]")])
_PAGE_2 = _season_html([("Ein Bad in Magensäure", f"{_LABOON} [Episode 062]")])
_ROWS = [
    EpisodeRow(1, 1, "Hier kommt Ruffy", _LUFFY_SITE, 1),
    EpisodeRow(2, 1, "Ein Bad in Magensäure", _LABOON, 62),
]
_STORED = [
    [1, 1, "Hier kommt Ruffy", _LUFFY_SITE, 1],
    [2, 1, "Ein Bad in Magensäure", _LABOON, 62],
]


def _url(season: int) -> str:
    return f"https://aniworld.to/anime/stream/one-piece/staffel-{season}"


class TestEpisodeIndex:
    async def _index(
        self,
        cache: AsyncMock | None,
        pages: dict[str, str | None],
        seasons: list[int] = [2, 1, 0],  # noqa: B006
    ) -> tuple[list[EpisodeRow], list[str]]:
        fetch_html, urls = _fetcher(pages)
        rows = await episode_index(
            cache=cache,
            key="aniworld:episodes:v1:one-piece",
            seasons=seasons,
            season_url=_url,
            fetch_html=fetch_html,
            selectors=_SELECTORS,
            semaphore=asyncio.Semaphore(2),
        )
        return rows, urls

    async def test_reads_the_season_pages_in_order_and_stores_the_rows(self) -> None:
        cache = AsyncMock()
        cache.get.return_value = None

        rows, urls = await self._index(cache, {_url(1): _PAGE_1, _url(2): _PAGE_2})

        assert rows == _ROWS
        # season 0 (specials) is no regular season
        assert urls == [_url(1), _url(2)]
        cache.set.assert_awaited_once_with(
            "aniworld:episodes:v1:one-piece", _STORED, ttl=INDEX_TTL_SECONDS
        )

    async def test_a_cached_index_reads_no_page(self) -> None:
        cache = AsyncMock()
        cache.get.return_value = _STORED

        rows, urls = await self._index(cache, {})

        assert rows == _ROWS
        assert urls == []
        cache.set.assert_not_awaited()

    @pytest.mark.parametrize(
        "stored", [[], "rows", [[1, 1, "x"]], [["1", 1, "x", "y", None]]]
    )
    async def test_a_corrupt_entry_is_rebuilt(self, stored: object) -> None:
        cache = AsyncMock()
        cache.get.return_value = stored

        rows, urls = await self._index(cache, {_url(1): _PAGE_1, _url(2): _PAGE_2})

        assert rows == _ROWS
        assert urls == [_url(1), _url(2)]
        cache.set.assert_awaited_once()

    async def test_a_page_the_site_did_not_answer_keeps_the_index_uncached(
        self,
    ) -> None:
        cache = AsyncMock()
        cache.get.return_value = None

        rows, _ = await self._index(cache, {_url(1): _PAGE_1, _url(2): None})

        # what was read serves this request; the next one reads again
        assert rows == _ROWS[:1]
        cache.set.assert_not_awaited()

    async def test_without_a_cache(self) -> None:
        rows, urls = await self._index(None, {_url(1): _PAGE_1, _url(2): _PAGE_2})

        assert rows == _ROWS
        assert urls == [_url(1), _url(2)]

    async def test_no_rows_are_not_stored(self) -> None:
        cache = AsyncMock()
        cache.get.return_value = None

        rows, _ = await self._index(cache, {_url(1): "<html></html>"}, seasons=[1])

        assert rows == []
        cache.set.assert_not_awaited()
