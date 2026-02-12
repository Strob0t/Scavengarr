"""Integration tests for the YAML plugin → ScrapyAdapter → SearchEngine pipeline.

Tests the full multi-stage scraping flow using a filmpalast-like YAML plugin
definition, real ScrapyAdapter, real HttpxScrapySearchEngine, and respx-mocked
HTTP responses backed by HTML fixture files.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from scavengarr.domain.plugins.plugin_schema import (
    NestedSelector,
    ScrapingConfig,
    ScrapingStage,
    StageSelectors,
    YamlPluginDefinition,
)
from scavengarr.infrastructure.cache.diskcache_adapter import DiskcacheAdapter
from scavengarr.infrastructure.scraping.scrapy_adapter import ScrapyAdapter
from scavengarr.infrastructure.torznab.search_engine import HttpxScrapySearchEngine

pytestmark = pytest.mark.integration

BASE_URL = "https://testsite.example.com"


def _build_test_plugin() -> YamlPluginDefinition:
    """Build a filmpalast-like 2-stage YAML plugin for testing."""
    return YamlPluginDefinition(
        name="testsite",
        version="1.0.0",
        base_url=BASE_URL,
        scraping=ScrapingConfig(
            mode="scrapy",
            start_stage="search_results",
            max_depth=4,
            delay_seconds=0.0,  # No delay in tests
            stages=[
                ScrapingStage(
                    name="search_results",
                    type="list",
                    url_pattern="/search/title/{query}",
                    selectors=StageSelectors(
                        rows="article",
                        title="h2 a",
                        link="h2 a",
                    ),
                    field_attributes={"link": ["href"]},
                    next_stage="movie_detail",
                ),
                ScrapingStage(
                    name="movie_detail",
                    type="detail",
                    url="/",
                    selectors=StageSelectors(
                        title="h2.bgDark",
                        release_name="span#release_text",
                        description="span[itemprop='description']",
                        download_links=NestedSelector(
                            container="div#grap-stream-list",
                            item_group="ul.currentStreamLinks",
                            items="li",
                            fields={
                                "hoster_name": "p.hostName, p",
                                "link": "a.button.iconPlay, a.button",
                            },
                            field_attributes={
                                "link": ["data-player-url", "href"],
                            },
                        ),
                    ),
                ),
            ],
        ),
    )


def _read_fixture(fixtures_dir: Path, name: str) -> bytes:
    return (fixtures_dir / "testsite" / name).read_bytes()


class TestScrapyAdapterMultiStage:
    """ScrapyAdapter executes multi-stage scraping with real HTML parsing."""

    @respx.mock
    async def test_two_stage_scrape(
        self,
        http_client: httpx.AsyncClient,
        diskcache: DiskcacheAdapter,
        fixtures_dir: Path,
    ) -> None:
        """Search page → detail pages → extracted data."""
        plugin = _build_test_plugin()

        # Mock search results page
        respx.get(f"{BASE_URL}/search/title/iron-man").respond(
            200,
            content=_read_fixture(fixtures_dir, "search_results.html"),
            headers={"content-type": "text/html"},
        )
        # Mock detail pages
        respx.get(f"{BASE_URL}/movie/iron-man").respond(
            200,
            content=_read_fixture(fixtures_dir, "movie_detail.html"),
            headers={"content-type": "text/html"},
        )
        respx.get(f"{BASE_URL}/movie/iron-man-2").respond(
            200,
            content=_read_fixture(fixtures_dir, "movie_detail_2.html"),
            headers={"content-type": "text/html"},
        )
        respx.get(f"{BASE_URL}/movie/iron-man-3").respond(
            404,
        )

        adapter = ScrapyAdapter(
            plugin=plugin,
            http_client=http_client,
            cache=diskcache,
            delay_seconds=0.0,
        )

        results = await adapter.scrape(query="iron-man")

        # Should have results from both stages
        assert "search_results" in results
        assert "movie_detail" in results

        # Search results: 3 articles found
        assert len(results["search_results"]) == 3

        # Detail results: 2 succeeded (iron-man-3 was 404)
        detail_items = results["movie_detail"]
        assert len(detail_items) == 2

        # Verify extracted data from first detail page
        titles = [item.get("title") for item in detail_items]
        assert "Iron Man" in titles
        assert "Iron Man 2" in titles

        # Verify release name extraction
        release_names = [item.get("release_name") for item in detail_items]
        assert "Iron.Man.2008.German.DL.1080p.BluRay.x264" in release_names

        # Verify nested download_links extraction
        for item in detail_items:
            assert "download_links" in item
            assert len(item["download_links"]) > 0

    @respx.mock
    async def test_empty_search_results(
        self,
        http_client: httpx.AsyncClient,
        diskcache: DiskcacheAdapter,
        fixtures_dir: Path,
    ) -> None:
        plugin = _build_test_plugin()

        respx.get(f"{BASE_URL}/search/title/nonexistent").respond(
            200,
            content=_read_fixture(fixtures_dir, "empty_search.html"),
            headers={"content-type": "text/html"},
        )

        adapter = ScrapyAdapter(
            plugin=plugin,
            http_client=http_client,
            cache=diskcache,
            delay_seconds=0.0,
        )

        results = await adapter.scrape(query="nonexistent")

        # Empty search has no valid rows (no title/download_link)
        total = sum(len(v) for v in results.values())
        assert total == 0

    @respx.mock
    async def test_search_page_server_error(
        self,
        http_client: httpx.AsyncClient,
        diskcache: DiskcacheAdapter,
    ) -> None:
        """Server error on search page results in empty results."""
        plugin = _build_test_plugin()

        respx.get(f"{BASE_URL}/search/title/test").respond(500)

        adapter = ScrapyAdapter(
            plugin=plugin,
            http_client=http_client,
            cache=diskcache,
            delay_seconds=0.0,
            max_retries=1,
        )

        results = await adapter.scrape(query="test")
        assert results == {}


class TestSearchEnginePipeline:
    """Full SearchEngine pipeline: scrape → convert → validate."""

    @respx.mock
    async def test_search_with_link_validation(
        self,
        http_client: httpx.AsyncClient,
        diskcache: DiskcacheAdapter,
        fixtures_dir: Path,
    ) -> None:
        """Full pipeline: scrape → convert to SearchResult → validate links."""
        plugin = _build_test_plugin()

        # Mock search and detail pages
        respx.get(f"{BASE_URL}/search/title/iron-man").respond(
            200,
            content=_read_fixture(fixtures_dir, "search_results.html"),
            headers={"content-type": "text/html"},
        )
        respx.get(f"{BASE_URL}/movie/iron-man").respond(
            200,
            content=_read_fixture(fixtures_dir, "movie_detail.html"),
            headers={"content-type": "text/html"},
        )
        respx.get(f"{BASE_URL}/movie/iron-man-2").respond(
            200,
            content=_read_fixture(fixtures_dir, "movie_detail_2.html"),
            headers={"content-type": "text/html"},
        )
        respx.get(f"{BASE_URL}/movie/iron-man-3").respond(404)

        # Mock link validation (HEAD requests to hoster URLs)
        respx.head("https://voe.sx/embed/abc123").respond(200)
        respx.head("https://streamtape.com/e/def456").respond(200)
        respx.head("https://filemoon.sx/e/ghi789").respond(403)
        respx.get("https://filemoon.sx/e/ghi789").respond(200)

        engine = HttpxScrapySearchEngine(
            http_client=http_client,
            cache=diskcache,
            validate_links=True,
            validation_timeout=5.0,
            validation_concurrency=10,
        )

        results = await engine.search(plugin, "iron-man")

        # Should have results from detail pages with valid links
        assert len(results) > 0

        # All results have download_link set
        for r in results:
            assert r.download_link

    @respx.mock
    async def test_search_without_validation(
        self,
        http_client: httpx.AsyncClient,
        diskcache: DiskcacheAdapter,
        fixtures_dir: Path,
    ) -> None:
        """Pipeline without link validation passes all results through."""
        plugin = _build_test_plugin()

        respx.get(f"{BASE_URL}/search/title/iron-man").respond(
            200,
            content=_read_fixture(fixtures_dir, "search_results.html"),
            headers={"content-type": "text/html"},
        )
        respx.get(f"{BASE_URL}/movie/iron-man").respond(
            200,
            content=_read_fixture(fixtures_dir, "movie_detail.html"),
            headers={"content-type": "text/html"},
        )
        respx.get(f"{BASE_URL}/movie/iron-man-2").respond(
            200,
            content=_read_fixture(fixtures_dir, "movie_detail_2.html"),
            headers={"content-type": "text/html"},
        )
        respx.get(f"{BASE_URL}/movie/iron-man-3").respond(404)

        engine = HttpxScrapySearchEngine(
            http_client=http_client,
            cache=diskcache,
            validate_links=False,
        )

        results = await engine.search(plugin, "iron-man")

        # Without validation, all scraped results pass through
        assert len(results) > 0

    @respx.mock
    async def test_search_deduplicates_results(
        self,
        http_client: httpx.AsyncClient,
        diskcache: DiskcacheAdapter,
        fixtures_dir: Path,
    ) -> None:
        """Engine deduplicates by (title, download_link)."""
        plugin = _build_test_plugin()

        # Only one search result → one detail page
        single_result_html = b"""<!DOCTYPE html>
<html><body>
<div class="results">
  <article><h2><a href="/movie/iron-man">Iron Man</a></h2></article>
</div>
</body></html>"""

        respx.get(f"{BASE_URL}/search/title/iron-man").respond(
            200,
            content=single_result_html,
            headers={"content-type": "text/html"},
        )
        respx.get(f"{BASE_URL}/movie/iron-man").respond(
            200,
            content=_read_fixture(fixtures_dir, "movie_detail.html"),
            headers={"content-type": "text/html"},
        )

        engine = HttpxScrapySearchEngine(
            http_client=http_client,
            cache=diskcache,
            validate_links=False,
        )

        results = await engine.search(plugin, "iron-man")

        # Check no duplicate (title, download_link) pairs
        seen = set()
        for r in results:
            key = (r.title, r.download_link)
            assert key not in seen, f"Duplicate result: {key}"
            seen.add(key)
