"""Tests for TorznabSearchUseCase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from scavengarr.application.factories import CrawlJobFactory
from scavengarr.application.use_cases.torznab_search import (
    SearchResponse,
    TorznabSearchUseCase,
    _search_cache_key,
)
from scavengarr.domain.entities import (
    TorznabBadRequest,
    TorznabExternalError,
    TorznabPluginNotFound,
    TorznabQuery,
    TorznabUnsupportedPlugin,
)
from scavengarr.domain.plugins import SearchResult


@dataclass
class _FakeScrapingConfig:
    mode: str = "scrapy"


@dataclass
class _FakePlugin:
    name: str = "filmpalast"
    version: str = "1.0.0"
    scraping: Any = None

    def __post_init__(self) -> None:
        if self.scraping is None:
            self.scraping = _FakeScrapingConfig()


class _FakePythonPlugin:
    """Python plugin: has search(), no scraping attribute."""

    def __init__(self) -> None:
        self.name = "boerse"
        self.base_url = "https://boerse.am"
        self._results: list[Any] = []

    async def search(
        self,
        query: str,
        category: int | None = None,
    ) -> list[Any]:
        return self._results


def _make_uc(
    registry: MagicMock | AsyncMock,
    engine: AsyncMock,
    repo: AsyncMock,
    factory: CrawlJobFactory | None = None,
    cache: AsyncMock | None = None,
    search_ttl: int = 900,
) -> TorznabSearchUseCase:
    return TorznabSearchUseCase(
        plugins=registry,
        engine=engine,
        crawljob_factory=factory or CrawlJobFactory(),
        crawljob_repo=repo,
        cache=cache,
        search_ttl=search_ttl,
    )


class TestQueryValidation:
    async def test_wrong_action_raises_bad_request(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
        )
        q = TorznabQuery(action="caps", plugin_name="filmpalast", query="test")
        with pytest.raises(TorznabBadRequest, match="action=search"):
            await uc.execute(q)

    async def test_missing_query_raises_bad_request(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
        )
        q = TorznabQuery(action="search", plugin_name="filmpalast", query="")
        with pytest.raises(TorznabBadRequest, match="Missing query"):
            await uc.execute(q)

    async def test_missing_plugin_name_raises_bad_request(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
        )
        q = TorznabQuery(action="search", plugin_name="", query="iron man")
        with pytest.raises(TorznabBadRequest, match="Missing plugin"):
            await uc.execute(q)


class TestPluginValidation:
    async def test_plugin_not_found(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        registry = MagicMock()
        registry.get.side_effect = KeyError("not found")
        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="nonexistent",
            query="test",
        )
        with pytest.raises(TorznabPluginNotFound):
            await uc.execute(q)

    async def test_unsupported_mode(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        plugin = _FakePlugin(scraping=_FakeScrapingConfig(mode="playwright"))
        registry = MagicMock()
        registry.get.return_value = plugin
        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        with pytest.raises(TorznabUnsupportedPlugin):
            await uc.execute(q)


class TestSearchExecution:
    async def test_empty_results(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        mock_search_engine.search.return_value = []
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="unknown movie",
        )
        response = await uc.execute(q)
        assert response.items == []

    async def test_happy_path_returns_torznab_items(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        mock_search_engine.search.return_value = [search_result]
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="iron man",
        )
        response = await uc.execute(q)
        assert len(response.items) == 1
        assert response.items[0].title == search_result.title
        assert response.items[0].job_id is not None
        assert len(response.items[0].job_id) == 36  # UUID4

    async def test_crawljob_saved_to_repo(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        mock_search_engine.search.return_value = [search_result]
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="iron man",
        )
        await uc.execute(q)
        mock_crawljob_repo.save.assert_awaited_once()

    async def test_engine_error_raises_external_error(
        self,
        mock_plugin_registry: MagicMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        engine = AsyncMock()
        engine.search.side_effect = RuntimeError("connection failed")
        uc = _make_uc(mock_plugin_registry, engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        with pytest.raises(TorznabExternalError):
            await uc.execute(q)


class TestPythonPluginDispatch:
    async def test_happy_path(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        result = SearchResult(
            title="SpongeBob",
            download_link="https://example.com/dl",
        )
        py_plugin = _FakePythonPlugin()
        py_plugin._results = [result]

        registry = MagicMock()
        registry.get.return_value = py_plugin
        mock_search_engine.validate_results.return_value = [result]

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="boerse",
            query="SpongeBob",
        )
        response = await uc.execute(q)

        assert len(response.items) == 1
        assert response.items[0].title == "SpongeBob"
        assert response.items[0].job_id is not None
        mock_search_engine.validate_results.assert_awaited_once_with([result])
        mock_search_engine.search.assert_not_awaited()

    async def test_empty_results(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        py_plugin = _FakePythonPlugin()
        py_plugin._results = []

        registry = MagicMock()
        registry.get.return_value = py_plugin
        mock_search_engine.validate_results.return_value = []

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="boerse",
            query="nothing",
        )
        response = await uc.execute(q)
        assert response.items == []

    async def test_search_error_raises_external_error(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        py_plugin = _FakePythonPlugin()

        # Make the search method raise
        async def _failing_search(query: str, category: int | None = None) -> list:
            raise RuntimeError("login failed")

        py_plugin.search = _failing_search  # type: ignore[assignment]

        registry = MagicMock()
        registry.get.return_value = py_plugin

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="boerse",
            query="test",
        )
        with pytest.raises(TorznabExternalError, match="Python plugin"):
            await uc.execute(q)

    async def test_validate_results_called(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        result = SearchResult(
            title="Movie",
            download_link="https://example.com/dl",
        )
        py_plugin = _FakePythonPlugin()
        py_plugin._results = [result]

        registry = MagicMock()
        registry.get.return_value = py_plugin
        mock_search_engine.validate_results.return_value = [result]

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="boerse",
            query="test",
        )
        await uc.execute(q)
        mock_search_engine.validate_results.assert_awaited_once()


class TestSearchCacheKey:
    def test_deterministic(self) -> None:
        key1 = _search_cache_key("filmpalast", "iron man", 2000)
        key2 = _search_cache_key("filmpalast", "iron man", 2000)
        assert key1 == key2

    def test_case_insensitive_query(self) -> None:
        key_lower = _search_cache_key("filmpalast", "iron man", None)
        key_upper = _search_cache_key("filmpalast", "Iron Man", None)
        assert key_lower == key_upper

    def test_whitespace_stripped(self) -> None:
        key_clean = _search_cache_key("filmpalast", "iron man", None)
        key_padded = _search_cache_key("filmpalast", "  iron man  ", None)
        assert key_clean == key_padded

    def test_different_categories_different_keys(self) -> None:
        key_movie = _search_cache_key("filmpalast", "test", 2000)
        key_tv = _search_cache_key("filmpalast", "test", 5000)
        assert key_movie != key_tv

    def test_none_vs_explicit_category(self) -> None:
        key_none = _search_cache_key("filmpalast", "test", None)
        key_zero = _search_cache_key("filmpalast", "test", 0)
        assert key_none != key_zero

    def test_different_plugins_different_keys(self) -> None:
        key1 = _search_cache_key("filmpalast", "test", None)
        key2 = _search_cache_key("boerse", "test", None)
        assert key1 != key2

    def test_prefix(self) -> None:
        key = _search_cache_key("filmpalast", "test", None)
        assert key.startswith("search:")


class TestSearchCaching:
    async def test_cache_hit_returns_cached_results(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        mock_cache.get.return_value = [search_result]
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
            cache=mock_cache,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="iron man",
        )
        response = await uc.execute(q)

        assert response.cache_hit is True
        assert len(response.items) == 1
        mock_search_engine.search.assert_not_awaited()

    async def test_cache_miss_executes_search(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        mock_cache.get.return_value = None
        mock_search_engine.search.return_value = [search_result]
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
            cache=mock_cache,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="iron man",
        )
        response = await uc.execute(q)

        assert response.cache_hit is False
        assert len(response.items) == 1
        mock_search_engine.search.assert_awaited_once()

    async def test_cache_stores_results_after_miss(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        mock_cache.get.return_value = None
        mock_search_engine.search.return_value = [search_result]
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
            cache=mock_cache,
            search_ttl=600,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="iron man",
        )
        await uc.execute(q)

        mock_cache.set.assert_awaited_once()
        call_kwargs = mock_cache.set.call_args
        assert call_kwargs.kwargs["ttl"] == 600

    async def test_cache_disabled_when_ttl_zero(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
    ) -> None:
        mock_search_engine.search.return_value = []
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
            cache=mock_cache,
            search_ttl=0,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        await uc.execute(q)

        mock_cache.get.assert_not_awaited()
        mock_cache.set.assert_not_awaited()

    async def test_cache_disabled_when_no_cache(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        mock_search_engine.search.return_value = []
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
            cache=None,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        response = await uc.execute(q)
        assert response.cache_hit is False

    async def test_cache_read_error_falls_back_to_search(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        mock_cache.get.side_effect = RuntimeError("cache down")
        mock_search_engine.search.return_value = [search_result]
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
            cache=mock_cache,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        response = await uc.execute(q)

        assert response.cache_hit is False
        assert len(response.items) == 1
        mock_search_engine.search.assert_awaited_once()

    async def test_cache_write_error_does_not_fail(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        mock_cache.get.return_value = None
        mock_cache.set.side_effect = RuntimeError("cache write failed")
        mock_search_engine.search.return_value = [search_result]
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
            cache=mock_cache,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        response = await uc.execute(q)

        assert len(response.items) == 1

    async def test_empty_results_not_cached(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
    ) -> None:
        mock_cache.get.return_value = None
        mock_search_engine.search.return_value = []
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
            cache=mock_cache,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="nothing",
        )
        await uc.execute(q)

        mock_cache.set.assert_not_awaited()

    async def test_cache_hit_still_generates_crawljobs(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        mock_cache.get.return_value = [search_result]
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
            cache=mock_cache,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="iron man",
        )
        response = await uc.execute(q)

        assert len(response.items) == 1
        assert response.items[0].job_id is not None
        mock_crawljob_repo.save.assert_awaited_once()

    async def test_response_type_is_search_response(
        self,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        mock_search_engine.search.return_value = []
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        response = await uc.execute(q)
        assert isinstance(response, SearchResponse)
