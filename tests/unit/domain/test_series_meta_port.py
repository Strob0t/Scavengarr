"""Tests for the series meta port and its null object."""

from __future__ import annotations

import pytest

from scavengarr.domain.ports.series_meta import NO_SERIES_META, SeriesMetaPort


class TestNullObject:
    def test_satisfies_the_port(self) -> None:
        assert isinstance(NO_SERIES_META, SeriesMetaPort)

    @pytest.mark.asyncio
    async def test_knows_no_title(self) -> None:
        assert await NO_SERIES_META.lookup("series", "tt0388629") == (
            None,
            "not_found",
        )
