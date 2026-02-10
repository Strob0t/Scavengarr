"""Torznab search use case with CrawlJob generation and link validation."""

from __future__ import annotations

from dataclasses import replace as dataclass_replace
from typing import cast

import structlog

from scavengarr.application.factories import CrawlJobFactory
from scavengarr.domain.entities import (
    TorznabBadRequest,
    TorznabExternalError,
    TorznabItem,
    TorznabPluginNotFound,
    TorznabQuery,
    TorznabUnsupportedPlugin,
)
from scavengarr.domain.ports import PluginRegistryPort
from scavengarr.domain.ports.crawljob_repository import CrawlJobRepository
from scavengarr.domain.ports.search_engine import SearchEnginePort

log = structlog.get_logger(__name__)


class TorznabSearchUseCase:
    """Executes Torznab search queries with link validation and CrawlJob generation.

    Flow:
        1. Validate query and plugin
        2. Execute search via SearchEngine (includes link validation)
        3. Convert each SearchResult → CrawlJob (via Factory)
        4. Store CrawlJobs in repository
        5. Return enriched TorznabItems with job_id fields
    """

    def __init__(
        self,
        plugins: PluginRegistryPort,
        engine: SearchEnginePort,
        crawljob_factory: CrawlJobFactory,
        crawljob_repo: CrawlJobRepository,
    ):
        """Initialize use case with dependencies.

        Args:
            plugins: Plugin registry for discovering indexers.
            engine: Search engine (with link validation).
            crawljob_factory: Factory for creating CrawlJobs from SearchResults.
            crawljob_repo: Repository for storing CrawlJobs.
        """
        self.plugins: PluginRegistryPort = plugins
        self.engine: SearchEnginePort = engine
        self.crawljob_factory: CrawlJobFactory = crawljob_factory
        self.crawljob_repo: CrawlJobRepository = crawljob_repo

    async def execute(self, q: TorznabQuery) -> list[TorznabItem]:
        """Execute Torznab search with link validation and CrawlJob generation.

        Args:
            q: TorznabQuery with action, plugin_name, query, category, etc.

        Returns:
            List of TorznabItems with enriched job_id fields.

        Raises:
            TorznabBadRequest: Invalid query parameters.
            TorznabPluginNotFound: Plugin does not exist.
            TorznabUnsupportedPlugin: Plugin has unsupported scraping mode.
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

        try:
            mode = plugin.scraping.mode  # type: ignore[attr-defined]
        except Exception as e:
            raise TorznabUnsupportedPlugin(
                "Plugin does not expose scraping.mode"
            ) from e
        if mode != "scrapy":
            raise TorznabUnsupportedPlugin(f"Unsupported scraping.mode: {mode}")

        # Execute search (includes link validation)
        try:
            raw_results: list = await self.engine.search(
                plugin,
                q.query,
                category=q.category,
            )
        except TorznabExternalError:
            raise
        except Exception as e:
            raise TorznabExternalError(f"Search engine error: {str(e)}") from e

        if not raw_results:
            log.info(
                "torznab_search_no_results",
                plugin=q.plugin_name,
                query=q.query,
            )
            return []

        # Transform results: SearchResult -> CrawlJob -> TorznabItem
        items: list[TorznabItem] = []
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
                await self.crawljob_repo.save(crawljob)
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

        log.info(
            "torznab_search_completed",
            plugin=q.plugin_name,
            query=q.query,
            raw_result_count=len(raw_results),
            crawljob_count=len(items),
        )
        return items
