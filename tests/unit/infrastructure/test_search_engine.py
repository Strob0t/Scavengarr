"""Tests for HttpxScrapySearchEngine conversion/extraction/filtering methods."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx

from scavengarr.domain.plugins import SearchResult
from scavengarr.infrastructure.torznab.search_engine import (
    HttpxScrapySearchEngine,
)


def _make_engine(
    validate_links: bool = False,
) -> HttpxScrapySearchEngine:
    """Create engine with mock dependencies (validation off by default)."""
    return HttpxScrapySearchEngine(
        http_client=AsyncMock(spec=httpx.AsyncClient),
        cache=AsyncMock(),
        validate_links=validate_links,
    )


class TestExtractDownloadLink:
    def test_direct_download_link_field(self) -> None:
        engine = _make_engine()
        item = {"download_link": "https://example.com/dl"}
        assert engine._extract_download_link(item) == ("https://example.com/dl")

    def test_link_field(self) -> None:
        engine = _make_engine()
        item = {"link": "https://example.com/dl"}
        assert engine._extract_download_link(item) == ("https://example.com/dl")

    def test_nested_download_links_dict_list(self) -> None:
        engine = _make_engine()
        item = {
            "download_links": [
                {"hoster": "Veev", "link": "https://veev.to/dl/1"},
                {"hoster": "Dood", "link": "https://dood.to/dl/1"},
            ]
        }
        assert engine._extract_download_link(item) == ("https://veev.to/dl/1")

    def test_nested_download_links_string_list(self) -> None:
        engine = _make_engine()
        item = {
            "download_links": [
                "https://example.com/dl/1",
                "https://example.com/dl/2",
            ]
        }
        assert engine._extract_download_link(item) == ("https://example.com/dl/1")

    def test_empty_download_links(self) -> None:
        engine = _make_engine()
        item = {"download_links": []}
        assert engine._extract_download_link(item) is None

    def test_no_link_fields(self) -> None:
        engine = _make_engine()
        item = {"title": "No links here"}
        assert engine._extract_download_link(item) is None


class TestConvertToResult:
    def test_full_item(self) -> None:
        engine = _make_engine()
        item = {
            "title": "Iron Man",
            "release_name": "Iron.Man.2008.1080p",
            "download_link": "https://example.com/dl",
            "seeders": "10",
            "leechers": "2",
            "size": "4.5 GB",
            "description": "A movie",
            "source_url": "https://example.com/movie/1",
        }
        result = engine._convert_to_result(item, "movie_detail")
        assert result is not None
        assert result.title == "Iron.Man.2008.1080p"
        assert result.download_link == "https://example.com/dl"
        assert result.scraped_from_stage == "movie_detail"

    def test_missing_title_returns_none(self) -> None:
        engine = _make_engine()
        item = {"download_link": "https://example.com/dl"}
        result = engine._convert_to_result(item, "s")
        assert result is None

    def test_missing_link_returns_none(self) -> None:
        engine = _make_engine()
        item = {"title": "Movie"}
        result = engine._convert_to_result(item, "s")
        assert result is None

    def test_empty_title_returns_none(self) -> None:
        engine = _make_engine()
        item = {"title": "  ", "download_link": "https://example.com/dl"}
        result = engine._convert_to_result(item, "s")
        assert result is None

    def test_release_name_preferred_over_title(self) -> None:
        engine = _make_engine()
        item = {
            "title": "Iron Man",
            "release_name": "Iron.Man.2008",
            "download_link": "https://example.com/dl",
        }
        result = engine._convert_to_result(item, "s")
        assert result is not None
        assert result.title == "Iron.Man.2008"


class TestConvertStageResults:
    def test_deduplication_by_title_and_link(self) -> None:
        engine = _make_engine()
        stage_results = {
            "stage1": [
                {
                    "title": "Movie",
                    "download_link": "https://a.com/dl",
                },
                {
                    "title": "Movie",
                    "download_link": "https://a.com/dl",
                },
            ],
        }
        results = engine._convert_stage_results(stage_results)
        assert len(results) == 1

    def test_different_links_not_deduplicated(self) -> None:
        engine = _make_engine()
        stage_results = {
            "stage1": [
                {
                    "title": "Movie",
                    "download_link": "https://a.com/dl/1",
                },
                {
                    "title": "Movie",
                    "download_link": "https://a.com/dl/2",
                },
            ],
        }
        results = engine._convert_stage_results(stage_results)
        assert len(results) == 2

    def test_skips_items_without_required_fields(self) -> None:
        engine = _make_engine()
        stage_results = {
            "stage1": [
                {"title": "NoLink"},
                {"download_link": "https://a.com/dl"},
                {
                    "title": "Valid",
                    "download_link": "https://a.com/dl2",
                },
            ],
        }
        results = engine._convert_stage_results(stage_results)
        assert len(results) == 1
        assert results[0].title == "Valid"

    def test_multiple_stages_combined(self) -> None:
        engine = _make_engine()
        stage_results = {
            "search": [
                {
                    "title": "A",
                    "download_link": "https://a.com/1",
                }
            ],
            "detail": [
                {
                    "title": "B",
                    "download_link": "https://b.com/1",
                }
            ],
        }
        results = engine._convert_stage_results(stage_results)
        assert len(results) == 2

    def test_empty_stages(self) -> None:
        engine = _make_engine()
        results = engine._convert_stage_results({})
        assert results == []


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
