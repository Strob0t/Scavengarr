"""Tests for the anime id translation port and its null object."""

from __future__ import annotations

import pytest

from scavengarr.domain.entities.stremio import StremioStreamRequest
from scavengarr.domain.ports.anime_ids import NO_ANIME_IDS, AnimeIdResolverPort


class TestNullObject:
    def test_satisfies_the_port(self) -> None:
        assert isinstance(NO_ANIME_IDS, AnimeIdResolverPort)

    @pytest.mark.asyncio
    async def test_translates_nothing(self) -> None:
        request = StremioStreamRequest(
            imdb_id="kitsu:7442", content_type="series", episode=3
        )

        assert await NO_ANIME_IDS.translate(request) is None

    @pytest.mark.asyncio
    async def test_numbers_nothing_and_closes_quietly(self) -> None:
        request = StremioStreamRequest(
            imdb_id="tt0388629", content_type="series", season=22, episode=4
        )

        assert await NO_ANIME_IDS.kitsu_episode(request) is None
        # Nothing counts a series whole: the position is all there is
        assert await NO_ANIME_IDS.has_whole_series_entry("tt0388629") is False
        await NO_ANIME_IDS.aclose()
