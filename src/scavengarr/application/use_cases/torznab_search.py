"""Torznab search use case with CrawlJob generation and link validation."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from typing import Any, cast

import structlog

from scavengarr.application.factories import CrawlJobFactory
from scavengarr.domain.entities import (
    TorznabBadRequest,
    TorznabExternalError,
    TorznabItem,
    TorznabPluginNotFound,
    TorznabQuery,
)
from scavengarr.domain.ports import PluginRegistryPort
from scavengarr.domain.ports.cache import CachePort
from scavengarr.domain.ports.crawljob_repository import CrawlJobRepository
from scavengarr.domain.ports.search_engine import SearchEnginePort

log = structlog.get_logger(__name__)


def _search_cache_key(plugin_name: str, query: str, category: int | None) -> str:
    """Compute deterministic cache key for a search query."""
    cat_str = str(category) if category is not None else "none"
    raw = f"{plugin_name}:{query.lower().strip()}:{cat_str}"
    return f"search:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


@dataclass(frozen=True)
class SearchResponse:
    """Use case response carrying items + cache metadata."""

    items: list[TorznabItem]
    cache_hit: bool = False


class TorznabSearchUseCase:
    """Executes Torznab search queries with link validation and CrawlJob generation.

    Flow:
        1. Validate query and plugin
        2. Execute search via Python plugin
        3. Validate download links via SearchEngine
        4. Convert each SearchResult -> CrawlJob (via Factory)
        5. Store CrawlJobs in repository
        6. Return enriched TorznabItems with job_id fields
    """

    def __init__(
        self,
        plugins: PluginRegistryPort,
        engine: SearchEnginePort,
        crawljob_factory: CrawlJobFactory,
        crawljob_repo: CrawlJobRepository,
        cache: CachePort | None = None,
        search_ttl: int = 900,
    ):
        """Initialize use case with dependencies.

        Args:
            plugins: Plugin registry for discovering indexers.
            engine: Search engine (with link validation).
            crawljob_factory: Factory for creating CrawlJobs from SearchResults.
            crawljob_repo: Repository for storing CrawlJobs.
            cache: Optional cache port for search result caching.
            search_ttl: TTL for cached search results (seconds). 0 = disabled.
        """
        self.plugins: PluginRegistryPort = plugins
        self.engine: SearchEnginePort = engine
        self.crawljob_factory: CrawlJobFactory = crawljob_factory
        self.crawljob_repo: CrawlJobRepository = crawljob_repo
        self._cache = cache
        self._search_ttl = search_ttl

    async def execute(self, q: TorznabQuery) -> SearchResponse:
        """Execute Torznab search with link validation and CrawlJob generation.

        Args:
            q: TorznabQuery with action, plugin_name, query, category, etc.

        Returns:
            SearchResponse with TorznabItems and cache metadata.

        Raises:
            TorznabBadRequest: Invalid query parameters.
            TorznabPluginNotFound: Plugin does not exist.
            TorznabExternalError: Search engine failure.
        """
        # Validate query
        if q.action != "search":
            raise TorznabBadRequest("TorznabSearchUseCase only supports action=search")
        if not q.query:
            raise TorznabBadRequest("Missing query parameter 'q'")
        if not q.plugin_name:
            raise TorznabBadRequest("Missing plugin name")

        # Resolve plugin
        try:
            plugin = self.plugins.get(q.plugin_name)
        except Exception as e:
            raise TorznabPluginNotFound(q.plugin_name) from e

        # --- cache lookup ---
        cache_key = _search_cache_key(q.plugin_name, q.query, q.category)
        raw_results = await self._cache_read(cache_key, q)
        cache_hit = raw_results is not None

        # --- cache miss: execute search ---
        if raw_results is None:
            raw_results = await self._execute_plugin(plugin, q)

            if raw_results:
                await self._cache_write(cache_key, raw_results, q, plugin)

        if not raw_results:
            log.info(
                "torznab_search_no_results",
                plugin=q.plugin_name,
                query=q.query,
            )
            return SearchResponse(items=[], cache_hit=cache_hit)

        items = await self._build_torznab_items(raw_results, q)
        paginated = items[q.offset : q.offset + q.limit]
        return SearchResponse(items=paginated, cache_hit=cache_hit)

    async def _cache_read(self, cache_key: str, q: TorznabQuery) -> list[Any] | None:
        """Try to read cached search results. Returns None on miss or error."""
        if not self._cache or self._search_ttl <= 0:
            return None
        try:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                log.info(
                    "search_cache_hit",
                    plugin=q.plugin_name,
                    query=q.query,
                    cache_key=cache_key,
                    result_count=len(cached),
                )
                return cached
        except Exception:
            log.warning(
                "search_cache_read_error",
                cache_key=cache_key,
                exc_info=True,
            )
        return None

    async def _cache_write(
        self,
        cache_key: str,
        results: list[Any],
        q: TorznabQuery,
        plugin: Any = None,
    ) -> None:
        """Store search results in cache. Silently ignores errors.

        Uses the plugin's ``cache_ttl`` attribute if set, otherwise
        falls back to the global ``self._search_ttl``.
        """
        if not self._cache or self._search_ttl <= 0:
            return
        ttl = self._search_ttl
        plugin_ttl = getattr(plugin, "cache_ttl", None)
        if plugin_ttl is not None and plugin_ttl > 0:
            ttl = plugin_ttl
        try:
            await self._cache.set(cache_key, results, ttl=ttl)
            log.debug(
                "search_cache_stored",
                plugin=q.plugin_name,
                cache_key=cache_key,
                ttl=ttl,
                result_count=len(results),
            )
        except Exception:
            log.warning(
                "search_cache_store_error",
                cache_key=cache_key,
                exc_info=True,
            )

    async def _execute_plugin(
        self,
        plugin: Any,
        q: TorznabQuery,
    ) -> list[Any]:
        """Execute search via Python plugin and validate results."""
        try:
            raw_results = await plugin.search(q.query, category=q.category)
        except Exception as e:
            raise TorznabExternalError(f"Plugin search error: {e!s}") from e

        try:
            return await self.engine.validate_results(raw_results)
        except Exception as e:
            raise TorznabExternalError(f"Result validation error: {e!s}") from e

    async def _build_torznab_items(
        self,
        raw_results: list[Any],
        q: TorznabQuery,
    ) -> list[TorznabItem]:
        """Transform SearchResults into TorznabItems with CrawlJob generation."""
        items: list[TorznabItem] = []
        save_coros: list[Any] = []
        for raw_result in raw_results:
            try:
                base_item = TorznabItem(
                    title=cast(str, getattr(raw_result, "title", "Unknown")),
                    download_url=cast(str, getattr(raw_result, "download_link", "")),
                    seeders=cast(int | None, getattr(raw_result, "seeders", None)),
                    peers=cast(int | None, getattr(raw_result, "leechers", None)),
                    size=cast(str | None, getattr(raw_result, "size", None)),
                    source_url=cast(
                        str | None, getattr(raw_result, "source_url", None)
                    ),
                    release_name=cast(
                        str | None, getattr(raw_result, "release_name", None)
                    ),
                    description=cast(
                        str | None, getattr(raw_result, "description", None)
                    ),
                    category=cast(int, getattr(raw_result, "category", 2000)),
                )

                crawljob = self.crawljob_factory.create_from_search_result(raw_result)
                save_coros.append(self.crawljob_repo.save(crawljob))
                enriched_item = dataclass_replace(base_item, job_id=crawljob.job_id)
                items.append(enriched_item)

                log.debug(
                    "crawljob_generated",
                    plugin=q.plugin_name,
                    query=q.query,
                    job_id=enriched_item.job_id,
                    title=enriched_item.title,
                    validated_url_count=len(crawljob.validated_urls),
                )

            except Exception as e:
                log.warning(
                    "crawljob_generation_failed",
                    plugin=q.plugin_name,
                    query=q.query,
                    result_title=getattr(raw_result, "title", "unknown"),
                    error=str(e),
                )
                continue

        # Batch-save all crawljobs in parallel
        if save_coros:
            await asyncio.gather(*save_coros, return_exceptions=True)

        log.info(
            "torznab_search_completed",
            plugin=q.plugin_name,
            query=q.query,
            raw_result_count=len(raw_results),
            crawljob_count=len(items),
        )
        return items
