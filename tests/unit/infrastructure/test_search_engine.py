"""Tests for HttpxScrapySearchEngine conversion/extraction methods."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx

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
