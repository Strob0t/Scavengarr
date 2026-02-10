"""Tests for TorznabSearchUseCase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from scavengarr.application.factories import CrawlJobFactory
from scavengarr.application.use_cases.torznab_search import (
    TorznabSearchUseCase,
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


def _make_uc(
    registry: MagicMock | AsyncMock,
    engine: AsyncMock,
    repo: AsyncMock,
    factory: CrawlJobFactory | None = None,
) -> TorznabSearchUseCase:
    return TorznabSearchUseCase(
        plugins=registry,
        engine=engine,
        crawljob_factory=factory or CrawlJobFactory(),
        crawljob_repo=repo,
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
        items = await uc.execute(q)
        assert items == []

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
        items = await uc.execute(q)
        assert len(items) == 1
        assert items[0].title == search_result.title
        assert items[0].job_id is not None
        assert len(items[0].job_id) == 36  # UUID4

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
