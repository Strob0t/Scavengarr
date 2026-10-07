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
