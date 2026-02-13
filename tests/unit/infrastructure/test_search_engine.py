"""Tests for HttpxSearchEngine validation/filtering methods."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx

from scavengarr.domain.plugins import SearchResult
from scavengarr.infrastructure.torznab.search_engine import (
    HttpxSearchEngine,
)


def _make_engine(
    validate_links: bool = False,
) -> HttpxSearchEngine:
    """Create engine with mock dependencies (validation off by default)."""
    return HttpxSearchEngine(
        http_client=AsyncMock(spec=httpx.AsyncClient),
        cache=AsyncMock(),
        validate_links=validate_links,
    )


class TestValidateResults:
    async def test_validation_enabled_delegates_to_filter(self) -> None:
        engine = _make_engine(validate_links=True)
        engine._link_validator.validate_batch = AsyncMock(
            return_value={"https://good.com/dl": True},
        )
        results = [
            SearchResult(title="Good", download_link="https://good.com/dl"),
        ]
        validated = await engine.validate_results(results)
        assert len(validated) == 1
        assert validated[0].title == "Good"

    async def test_validation_disabled_returns_unchanged(self) -> None:
        engine = _make_engine(validate_links=False)
        results = [
            SearchResult(title="Movie", download_link="https://example.com/dl"),
        ]
        validated = await engine.validate_results(results)
        assert validated is results

    async def test_empty_list(self) -> None:
        engine = _make_engine(validate_links=True)
        validated = await engine.validate_results([])
        assert validated == []


def _result(
    title: str = "Movie",
    download_link: str = "https://primary.com/dl",
    download_links: list[dict[str, str]] | None = None,
) -> SearchResult:
    """Create a minimal SearchResult for filter tests."""
    return SearchResult(
        title=title,
        download_link=download_link,
        download_links=download_links,
    )


class TestFilterValidLinks:
    async def test_primary_valid_keeps_result(self) -> None:
        engine = _make_engine(validate_links=True)
        engine._link_validator.validate_batch = AsyncMock(
            return_value={"https://primary.com/dl": True},
        )
        results = [_result()]
        filtered = await engine._filter_valid_links(results)
        assert len(filtered) == 1
        assert filtered[0].download_link == "https://primary.com/dl"
        assert filtered[0].validated_links == ["https://primary.com/dl"]

    async def test_primary_invalid_no_alternatives_drops(self) -> None:
        engine = _make_engine(validate_links=True)
        engine._link_validator.validate_batch = AsyncMock(
            return_value={"https://primary.com/dl": False},
        )
        results = [_result()]
        filtered = await engine._filter_valid_links(results)
        assert len(filtered) == 0

    async def test_primary_invalid_alternative_valid_promotes(self) -> None:
        engine = _make_engine(validate_links=True)
        engine._link_validator.validate_batch = AsyncMock(
            return_value={
                "https://primary.com/dl": False,
                "https://alt.com/dl": True,
            },
        )

        results = [
            _result(
                download_links=[
                    {"hoster": "Veev", "link": "https://primary.com/dl"},
                    {"hoster": "Dood", "link": "https://alt.com/dl"},
                ],
            ),
        ]
        filtered = await engine._filter_valid_links(results)
        assert len(filtered) == 1
        assert filtered[0].download_link == "https://alt.com/dl"
        assert filtered[0].validated_links == ["https://alt.com/dl"]

    async def test_primary_invalid_all_alternatives_invalid_drops(
        self,
    ) -> None:
        engine = _make_engine(validate_links=True)
        engine._link_validator.validate_batch = AsyncMock(
            return_value={
                "https://primary.com/dl": False,
                "https://alt.com/dl": False,
            },
        )

        results = [
            _result(
                download_links=[
                    {"hoster": "Veev", "link": "https://primary.com/dl"},
                    {"hoster": "Dood", "link": "https://alt.com/dl"},
                ],
            ),
        ]
        filtered = await engine._filter_valid_links(results)
        assert len(filtered) == 0

    async def test_multiple_results_mixed_validity(self) -> None:
        engine = _make_engine(validate_links=True)
        engine._link_validator.validate_batch = AsyncMock(
            return_value={
                "https://good.com/dl": True,
                "https://bad.com/dl": False,
            },
        )

        results = [
            _result(title="Good", download_link="https://good.com/dl"),
            _result(title="Bad", download_link="https://bad.com/dl"),
        ]
        filtered = await engine._filter_valid_links(results)
        assert len(filtered) == 1
        assert filtered[0].title == "Good"

    async def test_collects_all_valid_links(self) -> None:
        engine = _make_engine(validate_links=True)
        engine._link_validator.validate_batch = AsyncMock(
            return_value={
                "https://primary.com/dl": True,
                "https://veev.to/dl": True,
                "https://dood.to/dl": False,
                "https://voe.to/dl": True,
            },
        )

        results = [
            _result(
                download_links=[
                    {"hoster": "Veev", "link": "https://veev.to/dl"},
                    {"hoster": "Dood", "link": "https://dood.to/dl"},
                    {"hoster": "VOE", "link": "https://voe.to/dl"},
                ],
            ),
        ]
        filtered = await engine._filter_valid_links(results)
        assert len(filtered) == 1
        assert filtered[0].validated_links == [
            "https://primary.com/dl",
            "https://veev.to/dl",
            "https://voe.to/dl",
        ]
        assert filtered[0].download_link == "https://primary.com/dl"

    async def test_empty_results(self) -> None:
        engine = _make_engine(validate_links=True)
        filtered = await engine._filter_valid_links([])
        assert filtered == []

    async def test_pre_validated_results_skip_validation(self) -> None:
        """Results with validated_links already set bypass HTTP validation."""
        engine = _make_engine(validate_links=True)
        engine._link_validator.validate_batch = AsyncMock(
            return_value={},
        )

        pre = SearchResult(
            title="Anime",
            download_link="https://animeloads.io/embed/123",
            validated_links=["https://animeloads.io/embed/123"],
        )
        filtered = await engine._filter_valid_links([pre])
        assert len(filtered) == 1
        assert filtered[0].title == "Anime"
        # validate_batch should NOT be called (no urls to validate)
        engine._link_validator.validate_batch.assert_not_called()

    async def test_pre_validated_mixed_with_needs_validation(self) -> None:
        """Pre-validated results are kept alongside normally validated ones."""
        engine = _make_engine(validate_links=True)
        engine._link_validator.validate_batch = AsyncMock(
            return_value={"https://good.com/dl": True},
        )

        pre = SearchResult(
            title="PreValidated",
            download_link="https://animeloads.io/embed/123",
            validated_links=["https://animeloads.io/embed/123"],
        )
        normal = SearchResult(
            title="Normal",
            download_link="https://good.com/dl",
        )
        filtered = await engine._filter_valid_links([pre, normal])
        assert len(filtered) == 2
        titles = {r.title for r in filtered}
        assert titles == {"PreValidated", "Normal"}

    async def test_pre_validated_mixed_with_invalid(self) -> None:
        """Pre-validated results survive even when normal results are filtered."""
        engine = _make_engine(validate_links=True)
        engine._link_validator.validate_batch = AsyncMock(
            return_value={"https://dead.com/dl": False},
        )

        pre = SearchResult(
            title="PreValidated",
            download_link="https://animeloads.io/embed/123",
            validated_links=["https://animeloads.io/embed/123"],
        )
        dead = SearchResult(
            title="Dead",
            download_link="https://dead.com/dl",
        )
        filtered = await engine._filter_valid_links([pre, dead])
        assert len(filtered) == 1
        assert filtered[0].title == "PreValidated"
