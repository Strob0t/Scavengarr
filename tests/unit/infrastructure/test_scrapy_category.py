"""Tests for ScrapyAdapter category resolution in scrape()."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from diskcache import Cache

from scavengarr.domain.plugins.plugin_schema import (
    ScrapingConfig,
    ScrapingStage,
    StageSelectors,
    YamlPluginDefinition,
)
from scavengarr.infrastructure.scraping.scrapy_adapter import ScrapyAdapter


def _make_plugin(
    category_map: dict[int, str] | None = None,
    url_pattern: str = "/{category_path}?s={query}",
) -> YamlPluginDefinition:
    """Create a minimal YamlPluginDefinition for testing."""
    return YamlPluginDefinition(
        name="test-plugin",
        version="1.0.0",
        base_url="https://example.com",
        category_map=category_map,
        scraping=ScrapingConfig(
            mode="scrapy",
            stages=[
                ScrapingStage(
                    name="search_results",
                    type="list",
                    url_pattern=url_pattern,
                    selectors=StageSelectors(
                        rows="div.result",
                        title="a.title",
                        link="a.title",
                    ),
                ),
            ],
            start_stage="search_results",
        ),
    )


class TestCategoryResolution:
    """Test that ScrapyAdapter.scrape() resolves category to category_path."""

    async def test_known_category_resolved(self) -> None:
        """category=4000 with category_map should produce category_path='games/'."""
        plugin = _make_plugin(category_map={4000: "games/", 2000: "movies/"})
        adapter = ScrapyAdapter(
            plugin=plugin,
            http_client=AsyncMock(),
            cache=AsyncMock(spec=Cache),
        )

        with patch.object(adapter, "scrape_stage", new_callable=AsyncMock) as mock:
            mock.return_value = {}
            await adapter.scrape("test query", category=4000)
            mock.assert_awaited_once()
            _, kwargs = mock.call_args
            assert kwargs["category_path"] == "games/"
            assert kwargs["query"] == "test query"
            assert "category" not in kwargs

    async def test_unknown_category_gives_empty_path(self) -> None:
        """category=9999 (not in map) should produce category_path=''."""
        plugin = _make_plugin(category_map={4000: "games/"})
        adapter = ScrapyAdapter(
            plugin=plugin,
            http_client=AsyncMock(),
            cache=AsyncMock(spec=Cache),
        )

        with patch.object(adapter, "scrape_stage", new_callable=AsyncMock) as mock:
            mock.return_value = {}
            await adapter.scrape("test query", category=9999)
            _, kwargs = mock.call_args
            assert kwargs["category_path"] == ""

    async def test_no_category_gives_empty_path(self) -> None:
        """No category param should produce category_path=''."""
        plugin = _make_plugin(category_map={4000: "games/"})
        adapter = ScrapyAdapter(
            plugin=plugin,
            http_client=AsyncMock(),
            cache=AsyncMock(spec=Cache),
        )

        with patch.object(adapter, "scrape_stage", new_callable=AsyncMock) as mock:
            mock.return_value = {}
            await adapter.scrape("test query")
            _, kwargs = mock.call_args
            assert kwargs["category_path"] == ""

    async def test_no_category_map_gives_empty_path(self) -> None:
        """Plugin without category_map should produce category_path=''."""
        plugin = _make_plugin(category_map=None)
        adapter = ScrapyAdapter(
            plugin=plugin,
            http_client=AsyncMock(),
            cache=AsyncMock(spec=Cache),
        )

        with patch.object(adapter, "scrape_stage", new_callable=AsyncMock) as mock:
            mock.return_value = {}
            await adapter.scrape("test query", category=4000)
            _, kwargs = mock.call_args
            assert kwargs["category_path"] == ""

    async def test_category_not_passed_to_stage(self) -> None:
        """category should be consumed by scrape() and not forwarded as url param."""
        plugin = _make_plugin(category_map={2000: "movies/"})
        adapter = ScrapyAdapter(
            plugin=plugin,
            http_client=AsyncMock(),
            cache=AsyncMock(spec=Cache),
        )

        with patch.object(adapter, "scrape_stage", new_callable=AsyncMock) as mock:
            mock.return_value = {}
            await adapter.scrape("test query", category=2000)
            _, kwargs = mock.call_args
            assert "category" not in kwargs
