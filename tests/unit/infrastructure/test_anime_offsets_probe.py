"""Tests for the comparison and the report of scripts/probes/anime_offsets.py.

The captured pair is Yu☆Gi☆Oh! Duel Monsters (``kitsu:442``, ``tt0249327``)
as the Anime Kitsu addon and Cinemeta listed it on 2026-10-11, reduced to
the fields the comparison reads
(``tests/fixtures/json/kitsu/meta_series_442.json``,
``tests/fixtures/json/cinemeta/meta_series_tt0249327.json``).

The aniworld mode's placement, tally and report run on rows built here (Duel
Monsters' S4E1 and Naruto Shippuden's #372 as aniworld listed them on
2026-10-11), its index on the plugin with the captured One Piece pages of
``tests/fixtures/html/aniworld/``.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from scavengarr.domain.entities.stremio import (
    EpisodeMeta,
    EpisodeRef,
    SeriesMeta,
    TitleMatchInfo,
)
from scavengarr.infrastructure.anime import kitsu_addon
from scavengarr.infrastructure.anime.id_lists import LIST_URL
from scavengarr.infrastructure.anime.kitsu_addon import ADDON_URL, Placement
from scavengarr.infrastructure.plugins.episode_index import EpisodeRow, Located
from scavengarr.infrastructure.stremio import cinemeta
from scavengarr.infrastructure.stremio.cinemeta import CINEMETA_URL

_PROBES = Path(__file__).resolve().parents[3] / "scripts" / "probes"
_FIXTURES = Path(__file__).parents[2] / "fixtures" / "json"


def _load() -> ModuleType:
    # By path: scripts/probes/ is no package
    spec = importlib.util.spec_from_file_location(
        "anime_offsets_probe", _PROBES / "anime_offsets.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses resolve their annotations through sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load()


def _fixture(path: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / path).read_text(encoding="utf-8"))


_ADDON = _fixture("kitsu/meta_series_442.json")
_CINEMETA = _fixture("cinemeta/meta_series_tt0249327.json")


def _meta(*entries: tuple[int, int]) -> SeriesMeta:
    return SeriesMeta(
        name="Series",
        year=2000,
        genres=("Animation",),
        episodes=tuple(EpisodeMeta(s, e, name=f"S{s}E{e}") for s, e in entries),
    )


class TestCapturedPair:
    @pytest.fixture
    def offsets(self) -> Any:
        record = kitsu_addon._reduce(_ADDON["meta"])
        meta = cinemeta._reduce(_CINEMETA["meta"])
        return _mod.compare(
            "Yu☆Gi☆Oh! Duel Monsters", record.imdb_id, record.episodes, meta
        )

    def test_the_episode_0_entries_no_longer_count(self, offsets: Any) -> None:
        # Cinemeta lists S2E0 and S4E0 among the regular seasons; until row
        # 77 the position counted them (season 2 and 3 one high, season 4
        # and 5 two: 49 equal, 95 one off, 80 more), now every episode is
        # Kitsu's number
        assert (offsets.episodes, offsets.mapped) == (224, 224)
        assert (offsets.equal, offsets.plus_one, offsets.minus_one, offsets.more) == (
            224,
            0,
            0,
            0,
        )
        assert (offsets.not_listed, offsets.season_zero, offsets.unmapped) == (0, 0, 0)
        assert offsets.not_in_kitsu == 2

    def test_no_episode_is_off(self, offsets: Any) -> None:
        # Kitsu 50 (S2E1) was the first one off before row 77
        assert offsets.first_off is None

    def test_the_row_names_counts_and_the_first_off(self, offsets: Any) -> None:
        assert _mod.report_row("442", offsets) == (
            "| Yu☆Gi☆Oh! Duel Monsters | 442 | 224 | 224 | 0 | 224 | 0 | 0 | 0 | 0"
            " | 0 | 0 | 2 | – |"
        )


class TestCompare:
    def test_an_episode_imdb_lacks_puts_the_rest_one_low(self) -> None:
        # As One Piece's Kitsu 590: no IMDb episode, so from the next one
        # Cinemeta's position is one below the Kitsu number
        episodes = {
            1: Placement(1, 1, mapped=True),
            2: Placement(1, 2, mapped=True),
            3: Placement(1, 3, mapped=False),
            4: Placement(2, 1, mapped=True),
            5: Placement(2, 2, mapped=True),
            6: Placement(2, 3, mapped=True),
        }
        offsets = _mod.compare(
            "Series", "tt1", episodes, _meta((1, 1), (1, 2), (2, 1), (2, 2))
        )
        assert (offsets.equal, offsets.minus_one, offsets.unmapped) == (2, 2, 1)
        # Kitsu 6's IMDb episode is not listed yet
        assert offsets.not_listed == 1
        assert offsets.first_off == (4, 2, 1, -1)

    def test_the_kitsu_numbers_put_the_position_right(self) -> None:
        # The same series with the app's Kitsu numbers: the episode IMDb
        # lacks no longer shifts the rest
        episodes = {
            1: Placement(1, 1, mapped=True),
            2: Placement(1, 2, mapped=True),
            3: Placement(1, 3, mapped=False),
            4: Placement(2, 1, mapped=True),
            5: Placement(2, 2, mapped=True),
        }
        offsets = _mod.compare(
            "Series",
            "tt1",
            episodes,
            _meta((1, 1), (1, 2), (2, 1), (2, 2)),
            {(1, 1): 1, (1, 2): 2, (2, 1): 4, (2, 2): 5},
        )
        assert (offsets.numbered, offsets.equal, offsets.minus_one) == (4, 4, 0)
        assert offsets.first_off is None

    def test_an_episode_mapped_to_season_0_has_no_number(self) -> None:
        episodes = {1: Placement(1, 1, mapped=True), 2: Placement(0, 1, mapped=True)}
        offsets = _mod.compare("Series", "tt1", episodes, _meta((0, 1), (1, 1)))
        assert (offsets.equal, offsets.season_zero, offsets.not_in_kitsu) == (1, 1, 0)

    def test_without_a_cinemeta_list_every_mapped_episode_is_not_listed(self) -> None:
        offsets = _mod.compare("Series", "tt1", {1: Placement(1, 1, mapped=True)}, None)
        assert (offsets.mapped, offsets.not_listed, offsets.first_off) == (1, 1, None)


class TestSeriesOffsets:
    @respx.mock
    async def test_the_addon_and_cinemeta_are_read(self) -> None:
        respx.get(f"{ADDON_URL}/meta/series/kitsu:442.json").respond(json=_ADDON)
        respx.get(f"{CINEMETA_URL}/meta/series/tt0249327.json").respond(json=_CINEMETA)
        async with httpx.AsyncClient() as http:
            offsets, note = await _mod.series_offsets(http, "442")
        assert note == ""
        assert offsets is not None
        assert (offsets.name, offsets.equal, offsets.more) == (
            "Yu☆Gi☆Oh! Duel Monsters",
            224,
            0,
        )

    @respx.mock
    async def test_the_app_numbers_through_the_list_and_the_addon(self) -> None:
        # Fribb's list maps kitsu:442 to tt0247902; here to the addon's id
        respx.get(LIST_URL).respond(
            json=[{"kitsu_id": 442, "type": "TV", "imdb_id": ["tt0249327"]}]
        )
        respx.get(f"{ADDON_URL}/meta/series/kitsu:442.json").respond(json=_ADDON)
        respx.get(f"{CINEMETA_URL}/meta/series/tt0249327.json").respond(json=_CINEMETA)
        async with httpx.AsyncClient() as http:
            resolver = await _mod.numbering(http)
            assert resolver is not None
            offsets, _ = await _mod.series_offsets(http, "442", resolver)
        assert offsets is not None
        assert (offsets.numbered, offsets.equal) == (224, 224)

    @respx.mock
    async def test_without_the_list_there_is_no_numbering(self) -> None:
        respx.get(LIST_URL).respond(500)
        async with httpx.AsyncClient() as http:
            assert await _mod.numbering(http) is None

    @respx.mock
    async def test_a_kitsu_id_of_another_series_is_named(self) -> None:
        # The long-runner list expects One Piece under kitsu:12
        respx.get(f"{ADDON_URL}/meta/series/kitsu:12.json").respond(json=_ADDON)
        respx.get(f"{CINEMETA_URL}/meta/series/tt0249327.json").respond(json=_CINEMETA)
        async with httpx.AsyncClient() as http:
            _, note = await _mod.series_offsets(http, "12")
        assert note == "the addon names it 'Yu☆Gi☆Oh! Duel Monsters'"

    @respx.mock
    async def test_an_unknown_kitsu_id_gives_no_offsets(self) -> None:
        respx.get(f"{ADDON_URL}/meta/series/kitsu:1.json").respond(404)
        async with httpx.AsyncClient() as http:
            offsets, note = await _mod.series_offsets(http, "1")
        assert (offsets, note) == (None, "the addon knows no such series")


def _row(
    season: int, episode: int, english: str, absolute: int | None = None
) -> EpisodeRow:
    return EpisodeRow(season, episode, f"Deutsch {season}-{episode}", english, absolute)


def _ref(
    title: str | None, absolute: int | None, season: int = 1, episode: int = 1
) -> EpisodeRef:
    return EpisodeRef(season=season, episode=episode, title=title, absolute=absolute)


# Duel Monsters' S4E1 as aniworld listed it on 2026-10-11 (N26): the row with
# the episode's number carries the dub's title
_DUB_ROWS = [
    _row(4, 1, "Pulse of a New Darkness", 145),
    _row(4, 2, "Into the Fire", 146),
    _row(4, 3, "Showdown in the Shadows", 147),
]
_DUB_REF = _ref("A New Evil (Part 1 of 2)", 145, season=4, episode=1)

# Naruto Shippuden's #372 as aniworld listed it on 2026-10-11: the shorter
# title is a subset of the longer one, both score 1.00
_HOLE_ROWS = [
    _row(17, 10, "Hole", 371),
    _row(17, 11, "Something to Fill the Hole", 372),
]
_HOLE_REF = _ref("Something to Fill the Hole", 372)


class TestPlace:
    def test_a_title_confirms_the_exact_row(self) -> None:
        index = [
            _row(1, 1, "The Heart of the Cards", 1),
            _row(1, 2, "Into the Fire", 2),
        ]
        verdict = _mod.place(index, _ref("The Heart of the Cards", 1))
        assert (verdict.kind, verdict.row, verdict.confirmed) == (
            "exact",
            index[0],
            True,
        )
        assert verdict.offset == 0

    def test_without_a_title_the_number_alone_places_the_exact_row(self) -> None:
        index = [_row(1, 1, "The Heart of the Cards", 1)]
        verdict = _mod.place(index, _ref(None, 1))
        assert (verdict.kind, verdict.confirmed) == ("exact", False)

    def test_a_neighbour_the_title_confirms_has_its_offset(self) -> None:
        # No row shows 1
        index = [
            _row(1, 2, "The Heart of the Cards", 2),
            _row(1, 3, "Something Else Entirely", 3),
        ]
        verdict = _mod.place(index, _ref("The Heart of the Cards", 1))
        assert (verdict.kind, verdict.offset, verdict.confirmed) == (
            "neighbour",
            1,
            True,
        )
        assert verdict.exact is None

    def test_a_title_far_from_the_number_places_by_title(self) -> None:
        # No row shows 2, its neighbour's title is another (0.39)
        index = [
            _row(1, 1, "Something Else Entirely", 1),
            _row(2, 1, "The Heart of the Cards", 30),
        ]
        verdict = _mod.place(index, _ref("The Heart of the Cards", 2))
        assert (verdict.kind, verdict.row, verdict.offset) == ("title", index[1], 28)
        assert verdict.exact is None

    def test_rows_without_numbers_place_by_title_alone(self) -> None:
        index = [_row(1, 1, "Pikachu's Goodbye")]
        for title in ("Pikachu's Goodbye", "Pikachu's Goodbyes"):
            verdict = _mod.place(index, _ref(title, 39))
            assert (verdict.kind, verdict.exact, verdict.offset) == (
                "title",
                None,
                None,
            )

    def test_the_numbers_row_with_the_dubs_title_is_exact(self) -> None:
        # N26's case, placed by the number since row 87
        verdict = _mod.place(_DUB_ROWS, _DUB_REF)
        assert (verdict.kind, verdict.row, verdict.offset) == (
            "exact",
            _DUB_ROWS[0],
            0,
        )
        assert verdict.score == pytest.approx(0.53, abs=0.01)

    def test_an_exact_row_with_a_dub_title_is_n26s_case(self) -> None:
        # locate() before row 87 gave nothing here
        with patch.object(_mod, "locate", return_value=None):
            verdict = _mod.place(_DUB_ROWS, _DUB_REF)
        assert (verdict.kind, verdict.row, verdict.exact) == (
            "unconfirmed",
            None,
            _DUB_ROWS[0],
        )
        assert verdict.exact_score == pytest.approx(0.53, abs=0.01)
        # The best title match near the number is the exact row itself
        assert verdict.near is not None
        assert verdict.near[0] == _DUB_ROWS[0]

    def test_the_numbers_row_wins_a_tie(self) -> None:
        # Naruto Shippuden's #372 as aniworld listed it on 2026-10-11: the
        # shorter title is a subset of the longer one, both score 1.00
        verdict = _mod.place(_HOLE_ROWS, _HOLE_REF)
        assert (verdict.kind, verdict.row, verdict.offset) == (
            "exact",
            _HOLE_ROWS[1],
            0,
        )
        assert not verdict.tie

    def test_an_earlier_row_that_scores_as_high_is_a_tie(self) -> None:
        # locate() before row 87 kept the earlier row of equal scores
        with patch.object(
            _mod, "locate", return_value=Located(_HOLE_ROWS[0], "neighbour")
        ):
            verdict = _mod.place(_HOLE_ROWS, _HOLE_REF)
        assert (verdict.kind, verdict.row, verdict.offset) == (
            "neighbour",
            _HOLE_ROWS[0],
            -1,
        )
        assert verdict.score == verdict.exact_score == 1.0
        assert verdict.tie

    def test_without_an_exact_row_nothing_is_placed(self) -> None:
        verdict = _mod.place(
            [_row(1, 1, "Into the Fire", 1)], _ref("Duel Identity", 50)
        )
        assert (verdict.kind, verdict.exact, verdict.exact_score) == (
            "nothing",
            None,
            None,
        )


def _tallied() -> Any:
    """Three episodes placed as locate() before row 87 did: the exact row,
    nothing (N26) and a neighbour while the number's row is there."""
    meta = SeriesMeta(
        name="Series",
        year=2000,
        genres=("Animation",),
        episodes=(
            EpisodeMeta(0, 1, name="Special"),
            EpisodeMeta(1, 0, name="Recap"),
            EpisodeMeta(1, 1, name="The Heart of the Cards"),
            EpisodeMeta(1, 2, name="A New Evil (Part 1 of 2)"),
            EpisodeMeta(1, 3, name="Duel Identity"),
        ),
    )
    index = [
        _row(1, 1, "The Heart of the Cards", 1),
        _row(1, 2, "Pulse of a New Darkness", 2),
        _row(1, 3, "Something Else Entirely", 3),
        _row(1, 4, "Duel Identity", 4),
    ]
    placed = {
        1: Located(index[0], "number"),
        2: None,
        3: Located(index[3], "neighbour"),
    }
    with patch.object(
        _mod, "locate", side_effect=lambda _, ref, **_kw: placed[ref.episode]
    ):
        return _mod.tally(
            "Series", "series", "tt1", index, meta, {(1, 2): 2}, position_trusted=True
        )


def _offset_series() -> tuple[SeriesMeta, list[EpisodeRow], dict[Any, int]]:
    """A series whose site and Kitsu count an episode the catalog lacks
    (One Piece from #590 on): each position is one below the Kitsu
    number, and the third episode's title matches no row."""
    meta = SeriesMeta(
        name="Series",
        year=2000,
        genres=("Animation",),
        episodes=(
            EpisodeMeta(1, 1, name="Luffy and Sabo"),
            EpisodeMeta(1, 2, name="The Island of the Future"),
            EpisodeMeta(1, 3, name="Nothing Like It"),
        ),
    )
    index = [
        _row(1, 1, "The Episode the Catalog Lacks", 1),
        _row(1, 2, "Luffy and Sabo", 2),
        _row(1, 3, "The Island of the Future", 3),
        _row(1, 4, "A Genius Appears", 4),
    ]
    return meta, index, {(1, 1): 2, (1, 2): 3, (1, 3): 4}


class TestTally:
    def test_each_regular_episode_is_placed(self) -> None:
        counts = _tallied()
        # The special and the episode 0 entry have no position
        assert (counts.rows, counts.numbered_rows, counts.episodes) == (4, 4, 3)
        assert counts.kitsu == 1
        assert dict(counts.kinds) == {"exact": 1, "unconfirmed": 1, "neighbour": 1}
        assert dict(counts.offsets) == {1: 1}

    def test_the_title_confirmed_placements_count_those_at_another_row(
        self,
    ) -> None:
        counts = _tallied()
        assert counts.confirmed == 2
        assert [ref.episode for ref, _ in counts.elsewhere] == [3]

    def test_n26s_cases_keep_the_reference(self) -> None:
        counts = _tallied()
        assert [(ref.episode, ref.absolute) for ref, _ in counts.unconfirmed] == [
            (2, 2)
        ]

    def test_a_partial_series_is_placed_by_title_alone(self) -> None:
        """The site's "Bleach" is a shorter form of the request's title (the
        Thousand-Year Blood War, its season 17 there): the reference's
        number counts the request's series (row 92)."""
        meta = SeriesMeta(
            name="Bleach: Thousand-Year Blood War",
            year=2022,
            genres=("Animation",),
            episodes=(EpisodeMeta(1, 1, name="The Blood Warfare"),),
        )
        index = [
            _row(1, 1, "The Day I Became a Shinigami", 1),
            _row(17, 1, "The Blood Warfare", 367),
        ]
        titles = ("Bleach: Thousand-Year Blood War",)

        def tally(site_name: str) -> Any:
            return _mod.tally(
                "Bleach: Thousand-Year Blood War",
                "bleach",
                "tt14986406",
                index,
                meta,
                position_trusted=True,
                site_name=site_name,
                titles=titles,
            )

        partial = tally("Bleach")
        assert (partial.partial, dict(partial.kinds)) == (True, {"title": 1})
        assert "| bleach | yes |" in _mod.site_row("tt14986406", partial)
        whole = tally("Bleach: Thousand-Year Blood War")
        assert (whole.partial, dict(whole.kinds)) == (False, {"exact": 1})

    def test_the_position_of_a_series_a_kitsu_entry_counts_whole_is_unchecked(
        self,
    ) -> None:
        """Row 95: the episodes without a Kitsu number are placed as the
        app places an unchecked position, by the titles."""
        meta, index, _ = _offset_series()

        # The first episode's number came out, the others' did not
        counts = _mod.tally(
            "Series", "series", "tt1", index, meta, {(1, 1): 2}, position_trusted=False
        )

        assert (counts.kitsu, counts.unchecked) == (1, 2)
        # The first at its Kitsu number's row; the second's title takes the
        # row after its position's, the third's confirms no row
        assert dict(counts.kinds) == {"exact": 1, "neighbour": 1, "unconfirmed": 1}

    def test_right_after_a_start_every_position_is_unchecked(self) -> None:
        """``--start`` (row 95): no Kitsu number, every position unchecked,
        each placement judged by the Kitsu number; the same references as
        trusted positions (before row 95) take the rows one below."""
        meta, index, numbers = _offset_series()

        counts = _mod.tally(
            "Series",
            "series",
            "tt1",
            index,
            meta,
            numbers,
            position_trusted=True,
            start=True,
        )

        assert (counts.kitsu, counts.unchecked) == (3, 3)
        assert dict(counts.kinds) == {"neighbour": 2, "unconfirmed": 1}
        assert (counts.judged, counts.off) == (3, 3)
        assert (counts.right, counts.right_off) == (2, 2)
        assert (counts.trusted_right, counts.trusted_right_off) == (0, 0)
        assert _mod.start_row("tt1", counts) == (
            "| Series | tt1 | series | 3 | 3 | 3 | 2 (2) | 0 (0) | 0 | 2 | 0 | 1 | 0 |"
        )


class TestSiteReport:
    def test_the_row_names_counts_and_offsets(self) -> None:
        assert _mod.site_row("tt1", _tallied()) == (
            "| Series | tt1 | series | no | 4 | 4 | 3 | 1 | 0 | 1 | 1 (+1: 1) | 0"
            " | 1 (1) | 0 | 2 | 1 (0) |"
        )

    def test_the_share_sums_the_series(self) -> None:
        assert _mod.share_line([_tallied(), _tallied()]) == (
            "Title-confirmed placements with an exact-number row: 4,"
            " at another row: 2 (50.0 %), ties 0"
        )

    def test_a_case_names_the_reference_the_rows_and_the_scores(self) -> None:
        with patch.object(_mod, "locate", return_value=None):
            verdict = _mod.place(_DUB_ROWS, _DUB_REF)
        line = _mod.case_line(_DUB_REF, verdict)
        assert line == (
            "S4E1 #145 'A New Evil (Part 1 of 2)': exact row S4E1"
            " 'Pulse of a New Darkness' / 'Deutsch 4-1' 0.53;"
            " best near #145 0.53"
        )

    def test_a_placement_elsewhere_names_the_row_it_took(self) -> None:
        counts = _tallied()
        ref, verdict = counts.elsewhere[0]
        assert _mod.case_line(ref, verdict) == (
            "S1E3 #3 'Duel Identity': neighbour S1E4 #4 'Duel Identity'"
            " / 'Deutsch 1-4' 1.00;"
            " exact row S1E3 'Something Else Entirely' / 'Deutsch 1-3' 0.44"
        )

    def test_five_cases_spread_over_the_series(self) -> None:
        assert _mod.spread(list(range(12)), 5) == [0, 2, 4, 7, 9]
        assert _mod.spread([1, 2], 5) == [1, 2]


_PAGES = Path(__file__).parents[2] / "fixtures" / "html" / "aniworld"


def _page(name: str) -> str:
    return gzip.decompress((_PAGES / name).read_bytes()).decode()


def _aniworld() -> Any:
    spec = importlib.util.spec_from_file_location(
        "aniworld_probe_plugin", _PROBES.parents[1] / "plugins" / "aniworld.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AniworldPlugin()


class TestSiteIndex:
    async def test_the_first_relevant_hit_and_its_season_pages(self) -> None:
        plugin = _aniworld()
        plugin._ajax_search = AsyncMock(
            return_value=[
                {
                    "title": "One Piece Film: Red",
                    "link": "/anime/stream/one-piece-film",
                },
                {"title": "One Piece", "link": "/anime/stream/one-piece"},
            ]
        )
        plugin._safe_fetch = AsyncMock(
            return_value=MagicMock(text=_page("detail-one-piece.html.gz"))
        )
        season = _page("season-one-piece-staffel-2.html.gz")
        plugin._season_html = AsyncMock(
            side_effect=lambda url: (
                season if url.endswith("/one-piece/staffel-2") else None
            )
        )
        found = await _mod.site_index(plugin, "One Piece")
        assert found is not None
        slug, name, hits, rows = found
        # The exact hit, as the plugin takes it (the film is another title)
        assert (slug, name, hits) == ("one-piece", "One Piece", ["One Piece"])
        # Each of the 23 season pages once
        assert plugin._season_html.await_count == 23
        assert (len(rows), rows[0].absolute, rows[-1].absolute) == (16, 62, 77)

    async def test_a_slug_skips_the_search(self) -> None:
        plugin = _aniworld()
        plugin._ajax_search = AsyncMock()
        plugin._safe_fetch = AsyncMock(
            return_value=MagicMock(text=_page("detail-one-piece.html.gz"))
        )
        plugin._season_html = AsyncMock(return_value=None)
        found = await _mod.site_index(plugin, "One Piece", slug="one-piece")
        assert found is not None
        # The page's heading names the series
        assert found[:3] == ("one-piece", "One Piece", [])
        plugin._ajax_search.assert_not_awaited()
        assert plugin._safe_fetch.await_args.args[0] == (
            "https://aniworld.to/anime/stream/one-piece"
        )

    async def test_a_search_without_hits_gives_no_index(self) -> None:
        plugin = _aniworld()
        plugin._ajax_search = AsyncMock(return_value=[])
        assert await _mod.site_index(plugin, "Digimon Adventure") is None


class TestRequestTitles:
    async def test_each_languages_title_and_alternatives(self) -> None:
        infos = {
            "de": TitleMatchInfo(
                title="Bleach: Thousand Year Blood War",
                alt_titles=["Bleach: Sennen Kessen-hen"],
            ),
            "en": TitleMatchInfo(title="Bleach: Thousand-Year Blood War"),
        }
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            side_effect=lambda _imdb_id, language: infos[language]
        )

        titles = await _mod.request_titles(tmdb, "tt14986406", ["de", "en"])

        assert titles == (
            "Bleach: Thousand Year Blood War",
            "Bleach: Sennen Kessen-hen",
            "Bleach: Thousand-Year Blood War",
        )
