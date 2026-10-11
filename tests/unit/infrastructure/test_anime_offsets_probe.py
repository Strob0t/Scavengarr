"""Tests for the comparison and the report of scripts/probes/anime_offsets.py.

The captured pair is Yu☆Gi☆Oh! Duel Monsters (``kitsu:442``, ``tt0249327``)
as the Anime Kitsu addon and Cinemeta listed it on 2026-10-11, reduced to
the fields the comparison reads
(``tests/fixtures/json/kitsu/meta_series_442.json``,
``tests/fixtures/json/cinemeta/meta_series_tt0249327.json``).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

from scavengarr.domain.entities.stremio import EpisodeMeta, SeriesMeta
from scavengarr.infrastructure.anime import kitsu_addon
from scavengarr.infrastructure.anime.kitsu_addon import ADDON_URL, Placement
from scavengarr.infrastructure.stremio import cinemeta
from scavengarr.infrastructure.stremio.cinemeta import CINEMETA_URL

_PROBES = Path(__file__).resolve().parents[3] / "scripts" / "probes"
_FIXTURES = Path(__file__).parents[2] / "fixtures" / "json"


def _load() -> ModuleType:
    # By path: on sys.path the probes' redis.py would shadow the redis package
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
            "| Yu☆Gi☆Oh! Duel Monsters | 442 | 224 | 224 | 224 | 0 | 0 | 0 | 0 | 0"
            " | 0 | 2 | – |"
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
