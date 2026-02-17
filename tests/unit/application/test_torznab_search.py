"""Tests for TorznabSearchUseCase."""

from __future__ import annotations

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
)
from scavengarr.domain.plugins import SearchResult


class _FakePythonPlugin:
    """Minimal fake Python plugin for inline test use."""

    def __init__(
        self, name: str = "boerse", base_url: str = "https://boerse.am"
    ) -> None:
        self.name = name
        self.base_url = base_url
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


class TestSearchExecution:
    async def test_happy_path(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        result = SearchResult(
            title="SpongeBob",
            download_link="https://example.com/dl",
        )
        py_plugin = _FakePythonPlugin(name="filmpalast")
        py_plugin._results = [result]

        registry = MagicMock()
        registry.get.return_value = py_plugin
        mock_search_engine.validate_results.return_value = [result]

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="SpongeBob",
        )
        response = await uc.execute(q)

        assert len(response.items) == 1
        assert response.items[0].title == "SpongeBob"
        assert response.items[0].job_id is not None
        assert len(response.items[0].job_id) == 36  # UUID4
        mock_search_engine.validate_results.assert_awaited_once_with([result])

    async def test_empty_results(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        py_plugin = _FakePythonPlugin(name="filmpalast")
        py_plugin._results = []

        registry = MagicMock()
        registry.get.return_value = py_plugin
        mock_search_engine.validate_results.return_value = []

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="nothing",
        )
        response = await uc.execute(q)
        assert response.items == []

    async def test_search_error_raises_external_error(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        py_plugin = _FakePythonPlugin(name="filmpalast")

        async def _failing_search(query: str, category: int | None = None) -> list:
            raise RuntimeError("login failed")

        py_plugin.search = _failing_search  # type: ignore[assignment]

        registry = MagicMock()
        registry.get.return_value = py_plugin

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        with pytest.raises(TorznabExternalError, match="Plugin search"):
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
        py_plugin = _FakePythonPlugin(name="filmpalast")
        py_plugin._results = [result]

        registry = MagicMock()
        registry.get.return_value = py_plugin
        mock_search_engine.validate_results.return_value = [result]

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        await uc.execute(q)
        mock_search_engine.validate_results.assert_awaited_once()

    async def test_crawljob_saved_to_repo(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        py_plugin = _FakePythonPlugin(name="filmpalast")
        py_plugin._results = [search_result]

        registry = MagicMock()
        registry.get.return_value = py_plugin
        mock_search_engine.validate_results.return_value = [search_result]

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="iron man",
        )
        await uc.execute(q)
        mock_crawljob_repo.save.assert_awaited_once()

    async def test_validation_error_raises_external_error(
        self,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        result = SearchResult(
            title="Movie",
            download_link="https://example.com/dl",
        )
        py_plugin = _FakePythonPlugin(name="filmpalast")
        py_plugin._results = [result]

        registry = MagicMock()
        registry.get.return_value = py_plugin

        engine = AsyncMock()
        engine.validate_results.side_effect = RuntimeError("validation crash")

        uc = _make_uc(registry, engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        with pytest.raises(TorznabExternalError, match="Result validation"):
            await uc.execute(q)


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

    async def test_cache_miss_executes_search(
        self,
        fake_plugin: _FakePythonPlugin,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        mock_cache.get.return_value = None
        fake_plugin._results = [search_result]
        mock_search_engine.validate_results.return_value = [search_result]
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

    async def test_cache_stores_results_after_miss(
        self,
        fake_plugin: _FakePythonPlugin,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        mock_cache.get.return_value = None
        fake_plugin._results = [search_result]
        mock_search_engine.validate_results.return_value = [search_result]
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
        mock_search_engine.validate_results.return_value = []
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
        mock_search_engine.validate_results.return_value = []
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
        fake_plugin: _FakePythonPlugin,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        mock_cache.get.side_effect = RuntimeError("cache down")
        fake_plugin._results = [search_result]
        mock_search_engine.validate_results.return_value = [search_result]
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

    async def test_cache_write_error_does_not_fail(
        self,
        fake_plugin: _FakePythonPlugin,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        mock_cache.get.return_value = None
        mock_cache.set.side_effect = RuntimeError("cache write failed")
        fake_plugin._results = [search_result]
        mock_search_engine.validate_results.return_value = [search_result]
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
        mock_search_engine.validate_results.return_value = []
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
        mock_search_engine.validate_results.return_value = []
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

    async def test_plugin_cache_ttl_overrides_global(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        """Plugin with cache_ttl=300 should override global search_ttl=900."""
        plugin = _FakePythonPlugin(name="filmpalast")
        plugin._results = [search_result]
        plugin.cache_ttl = 300  # type: ignore[attr-defined]
        registry = MagicMock()
        registry.get.return_value = plugin
        mock_cache.get.return_value = None
        mock_search_engine.validate_results.return_value = [search_result]
        uc = _make_uc(
            registry,
            mock_search_engine,
            mock_crawljob_repo,
            cache=mock_cache,
            search_ttl=900,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        await uc.execute(q)

        mock_cache.set.assert_awaited_once()
        assert mock_cache.set.call_args.kwargs["ttl"] == 300

    async def test_plugin_without_cache_ttl_uses_global(
        self,
        fake_plugin: _FakePythonPlugin,
        mock_plugin_registry: MagicMock,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        """Plugin without cache_ttl should use global search_ttl."""
        mock_cache.get.return_value = None
        fake_plugin._results = [search_result]
        mock_search_engine.validate_results.return_value = [search_result]
        uc = _make_uc(
            mock_plugin_registry,
            mock_search_engine,
            mock_crawljob_repo,
            cache=mock_cache,
            search_ttl=900,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        await uc.execute(q)

        mock_cache.set.assert_awaited_once()
        assert mock_cache.set.call_args.kwargs["ttl"] == 900

    async def test_python_plugin_cache_ttl_overrides_global(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
        mock_cache: AsyncMock,
        search_result: SearchResult,
    ) -> None:
        """Python plugin with cache_ttl should override global TTL."""
        plugin = _FakePythonPlugin()
        plugin.cache_ttl = 120  # type: ignore[attr-defined]
        plugin._results = [search_result]
        registry = MagicMock()
        registry.get.return_value = plugin
        mock_cache.get.return_value = None
        mock_search_engine.validate_results.return_value = [search_result]
        uc = _make_uc(
            registry,
            mock_search_engine,
            mock_crawljob_repo,
            cache=mock_cache,
            search_ttl=900,
        )
        q = TorznabQuery(
            action="search",
            plugin_name="boerse",
            query="test",
        )
        await uc.execute(q)

        mock_cache.set.assert_awaited_once()
        assert mock_cache.set.call_args.kwargs["ttl"] == 120


class TestPagination:
    """Tests for offset/limit pagination in TorznabSearchUseCase."""

    @staticmethod
    def _make_results(n: int) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"Movie {i}",
                download_link=f"https://example.com/dl/{i}",
            )
            for i in range(n)
        ]

    async def test_default_pagination_returns_first_100(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        results = self._make_results(150)
        py_plugin = _FakePythonPlugin(name="filmpalast")
        py_plugin._results = results

        registry = MagicMock()
        registry.get.return_value = py_plugin
        mock_search_engine.validate_results.return_value = results

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
        )
        response = await uc.execute(q)
        assert len(response.items) == 100
        assert response.items[0].title == "Movie 0"
        assert response.items[99].title == "Movie 99"

    async def test_offset_slices_results(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        results = self._make_results(50)
        py_plugin = _FakePythonPlugin(name="filmpalast")
        py_plugin._results = results

        registry = MagicMock()
        registry.get.return_value = py_plugin
        mock_search_engine.validate_results.return_value = results

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
            offset=10,
            limit=100,
        )
        response = await uc.execute(q)
        assert len(response.items) == 40
        assert response.items[0].title == "Movie 10"

    async def test_limit_caps_results(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        results = self._make_results(50)
        py_plugin = _FakePythonPlugin(name="filmpalast")
        py_plugin._results = results

        registry = MagicMock()
        registry.get.return_value = py_plugin
        mock_search_engine.validate_results.return_value = results

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
            offset=0,
            limit=5,
        )
        response = await uc.execute(q)
        assert len(response.items) == 5
        assert response.items[0].title == "Movie 0"
        assert response.items[4].title == "Movie 4"

    async def test_offset_beyond_results_returns_empty(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        results = self._make_results(10)
        py_plugin = _FakePythonPlugin(name="filmpalast")
        py_plugin._results = results

        registry = MagicMock()
        registry.get.return_value = py_plugin
        mock_search_engine.validate_results.return_value = results

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
            offset=100,
            limit=100,
        )
        response = await uc.execute(q)
        assert response.items == []

    async def test_offset_plus_limit_partial_page(
        self,
        mock_search_engine: AsyncMock,
        mock_crawljob_repo: AsyncMock,
    ) -> None:
        results = self._make_results(25)
        py_plugin = _FakePythonPlugin(name="filmpalast")
        py_plugin._results = results

        registry = MagicMock()
        registry.get.return_value = py_plugin
        mock_search_engine.validate_results.return_value = results

        uc = _make_uc(registry, mock_search_engine, mock_crawljob_repo)
        q = TorznabQuery(
            action="search",
            plugin_name="filmpalast",
            query="test",
            offset=20,
            limit=100,
        )
        response = await uc.execute(q)
        assert len(response.items) == 5
        assert response.items[0].title == "Movie 20"
