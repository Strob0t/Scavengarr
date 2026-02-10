"""Tests for SearchResult and StageResult domain entities."""

from __future__ import annotations

from scavengarr.domain.plugins import SearchResult, StageResult


class TestSearchResult:
    def test_creation_with_required_fields(self) -> None:
        r = SearchResult(
            title="Test Movie",
            download_link="https://example.com/dl",
            metadata={},
        )
        assert r.title == "Test Movie"
        assert r.download_link == "https://example.com/dl"

    def test_default_optional_fields(self) -> None:
        r = SearchResult(
            title="T",
            download_link="http://x",
            metadata={},
        )
        assert r.seeders is None
        assert r.leechers is None
        assert r.size is None
        assert r.release_name is None
        assert r.description is None
        assert r.published_date is None
        assert r.download_links is None
        assert r.source_url is None
        assert r.scraped_from_stage is None

    def test_default_torznab_fields(self) -> None:
        r = SearchResult(
            title="T",
            download_link="http://x",
            metadata={},
        )
        assert r.category == 2000
        assert r.grabs == 0
        assert r.download_volume_factor == 0.0
        assert r.upload_volume_factor == 0.0

    def test_metadata_dict_independence(self) -> None:
        r1 = SearchResult(title="T", download_link="http://x", metadata={"a": 1})
        r2 = SearchResult(title="T", download_link="http://x", metadata={"b": 2})
        assert r1.metadata != r2.metadata

    def test_full_creation(self) -> None:
        r = SearchResult(
            title="Iron Man",
            download_link="https://dl.example.com/1",
            seeders=100,
            leechers=5,
            size="4.5 GB",
            release_name="Iron.Man.2008.1080p",
            description="A movie about Iron Man",
            published_date="2025-01-01",
            download_links=[{"hoster": "X", "link": "http://x"}],
            source_url="https://example.com/movie/1",
            scraped_from_stage="movie_detail",
            metadata={"extra": "info"},
            category=2000,
            grabs=42,
        )
        assert r.seeders == 100
        assert r.download_links is not None
        assert len(r.download_links) == 1


class TestStageResult:
    def test_creation(self) -> None:
        sr = StageResult(
            url="https://example.com/search/test",
            stage_name="search_results",
            depth=0,
            data={"title": "Test"},
        )
        assert sr.url == "https://example.com/search/test"
        assert sr.stage_name == "search_results"
        assert sr.depth == 0
        assert sr.data == {"title": "Test"}

    def test_default_links_empty(self) -> None:
        sr = StageResult(
            url="http://x",
            stage_name="s",
            depth=0,
            data={},
        )
        assert sr.links == []

    def test_links_mutable_default_independence(self) -> None:
        sr1 = StageResult(url="a", stage_name="s", depth=0, data={})
        sr2 = StageResult(url="b", stage_name="s", depth=0, data={})
        sr1.links.append("http://link")
        assert sr2.links == []
