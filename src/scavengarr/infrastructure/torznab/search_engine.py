"""Scrapy-based multi-stage search engine with link validation."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from scavengarr.domain.entities import TorznabExternalError
from scavengarr.domain.plugins import SearchResult
from scavengarr.domain.ports import CachePort
from scavengarr.infrastructure.common.converters import to_int
from scavengarr.infrastructure.scraping import ScrapyAdapter
from scavengarr.infrastructure.validation import HttpLinkValidator

log = structlog.get_logger(__name__)


class HttpxScrapySearchEngine:
    """Scrapy-based multi-stage search engine with link validation.

    Features:
        - Multi-stage scraping via ScrapyAdapter
        - Optional download link validation (HEAD requests)
        - Result filtering based on link availability
        - Configurable validation timeout and concurrency

    Args:
        http_client: Shared httpx.AsyncClient for HTTP requests.
        cache: Cache port for storing scraped data.
        validate_links: Enable download link validation (default: True).
        validation_timeout: Timeout per link validation in seconds (default: 5.0).
        validation_concurrency: Max parallel link validations (default: 20).
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        cache: CachePort,
        validate_links: bool = True,
        validation_timeout: float = 5.0,
        validation_concurrency: int = 20,
    ) -> None:
        self._http = http_client
        self._cache = cache
        self._validate_links = validate_links

        # Initialize link validator
        self._link_validator = HttpLinkValidator(
            http_client=http_client,
            timeout_seconds=validation_timeout,
            max_concurrent=validation_concurrency,
        )

        log.info(
            "search_engine_initialized",
            validate_links=validate_links,
            validation_timeout=validation_timeout,
            validation_concurrency=validation_concurrency,
        )

    async def search(
        self,
        plugin: Any,
        query: str,
        **params,
    ) -> list[SearchResult]:
        """Execute multi-stage search with optional link validation.

        Flow:
            1. Scrape indexer using ScrapyAdapter (multi-stage)
            2. Convert scraped items to SearchResult objects
            3. Validate download links (if enabled)
            4. Filter out results with dead links
            5. Return validated results

        Args:
            plugin: Plugin configuration object.
            query: Search query string.
            **params: Additional parameters (e.g., category, filters).

        Returns:
            List of search results with validated download links.

        Raises:
            TorznabExternalError: If scraping fails.
        """
        adapter = ScrapyAdapter(
            plugin=plugin,
            http_client=self._http,
            cache=self._cache,
        )

        try:
            # 1) Execute multi-stage scrape
            stage_results = await adapter.scrape(query=query, **params)

            # 2) Convert to SearchResult format
            raw_results = self._convert_stage_results(stage_results)

            if not raw_results:
                log.info(
                    "search_no_results",
                    plugin=getattr(plugin, "name", "unknown"),
                    query=query,
                )
                return []

            log.info(
                "search_scraped",
                plugin=getattr(plugin, "name", "unknown"),
                query=query,
                raw_count=len(raw_results),
            )

            # 3) Validate links (if enabled)
            if self._validate_links:
                validated_results = await self._filter_valid_links(raw_results)
            else:
                validated_results = raw_results

            log.info(
                "search_completed",
                plugin=getattr(plugin, "name", "unknown"),
                query=query,
                raw_count=len(raw_results),
                valid_count=len(validated_results),
                filtered_count=len(raw_results) - len(validated_results),
            )

            return validated_results

        except Exception as e:
            log.error(
                "search_failed",
                plugin=getattr(plugin, "name", "unknown"),
                query=query,
                error=str(e),
            )
            raise TorznabExternalError(f"scrapy search failed: {e!s}") from e

    async def validate_results(
        self,
        results: list[SearchResult],
    ) -> list[SearchResult]:
        """Validate download links on pre-built SearchResults.

        Used by Python plugins that do their own scraping and return
        SearchResult lists directly. Delegates to _filter_valid_links().
        """
        if self._validate_links:
            return await self._filter_valid_links(results)
        return results

    def _convert_stage_results(
        self,
        stage_results: dict[str, list[dict]],
    ) -> list[SearchResult]:
        """Convert multi-stage scraped items to SearchResult objects.

        Deduplicates by (title, download_link) to prevent duplicates
        from intermediate stages or concurrent scraping.

        Args:
            stage_results: Dict mapping stage_name -> list of scraped items.

        Returns:
            Flat list of unique SearchResult objects.
        """
        results: list[SearchResult] = []
        seen: set[tuple[str, str]] = set()

        for stage_name, items in stage_results.items():
            for item in items:
                result = self._convert_to_result(item, stage_name)
                if result:
                    key = (result.title, result.download_link)
                    if key in seen:
                        log.debug(
                            "duplicate_result_skipped",
                            stage=stage_name,
                            title=result.title,
                        )
                        continue
                    seen.add(key)
                    results.append(result)
                else:
                    log.debug(
                        "item_conversion_skipped",
                        stage=stage_name,
                        reason="missing title or download_link",
                        item=item,
                    )

        return results

    async def _filter_valid_links(
        self,
        results: list[SearchResult],
    ) -> list[SearchResult]:
        """Batch-validate ALL URLs and collect valid links per result.

        Flow:
            1. Collect all unique URLs across all results (primaries + alternatives).
            2. Single validate_batch() call for everything.
            3. For each result, assemble validated_links and promote if needed.
            4. Drop results with zero valid links.

        Args:
            results: Raw search results from scraper.

        Returns:
            Results with validated_links populated and reachable download_link.
        """
        all_urls = self._collect_all_urls(results)

        if not all_urls:
            log.warning("no_download_links_to_validate")
            return results

        validation_map = await self._link_validator.validate_batch(list(all_urls))

        valid_results = [
            r
            for r in results
            if r.download_link and self._apply_validation(r, validation_map)
        ]

        filtered = len(results) - len(valid_results)
        if filtered > 0:
            log.info(
                "links_filtered",
                total=len(results),
                valid=len(valid_results),
                invalid=filtered,
            )

        return valid_results

    def _collect_all_urls(self, results: list[SearchResult]) -> set[str]:
        """Collect all unique URLs from results (primaries + alternatives).

        Args:
            results: Search results to extract URLs from.

        Returns:
            Set of unique URL strings.
        """
        all_urls: set[str] = set()
        for r in results:
            if r.download_link:
                all_urls.add(r.download_link)
            if r.download_links:
                for entry in r.download_links:
                    url = self._extract_url_from_entry(entry)
                    if url:
                        all_urls.add(url)
        return all_urls

    def _apply_validation(
        self,
        result: SearchResult,
        validation_map: dict[str, bool],
    ) -> bool:
        """Apply validation results to a single SearchResult.

        Populates validated_links, promotes alternative if primary is dead.

        Args:
            result: SearchResult to process.
            validation_map: URL -> validity mapping.

        Returns:
            True if result has at least one valid link, False otherwise.
        """
        valid_links = self._collect_valid_links(result, validation_map)
        if not valid_links:
            return False

        if not validation_map.get(result.download_link, False):
            log.info(
                "alternative_link_promoted",
                title=result.title,
                failed=result.download_link,
                promoted=valid_links[0],
            )
            result.download_link = valid_links[0]

        result.validated_links = valid_links
        return True

    def _collect_valid_links(
        self,
        result: SearchResult,
        validation_map: dict[str, bool],
    ) -> list[str]:
        """Collect all valid URLs for a result (primary first, then alternatives).

        Args:
            result: SearchResult to collect links for.
            validation_map: URL -> validity mapping from batch validation.

        Returns:
            Ordered list of valid URLs (deduplicated).
        """
        valid: list[str] = []
        seen: set[str] = set()

        # Primary first
        if result.download_link and validation_map.get(result.download_link, False):
            valid.append(result.download_link)
            seen.add(result.download_link)

        # Then alternatives from download_links
        if result.download_links:
            for entry in result.download_links:
                url = self._extract_url_from_entry(entry)
                if url and url not in seen and validation_map.get(url, False):
                    valid.append(url)
                    seen.add(url)

        return valid

    @staticmethod
    def _extract_url_from_entry(entry: dict[str, str] | str | object) -> str | None:
        """Extract URL from a download_links entry (dict or string).

        Args:
            entry: A download_links list element.

        Returns:
            URL string or None.
        """
        if isinstance(entry, dict):
            return entry.get("link", "") or None
        if isinstance(entry, str):
            return entry or None
        return None

    def _convert_to_result(
        self,
        item: dict,
        stage_name: str,
    ) -> SearchResult | None:
        """Convert scraped item to SearchResult.

        Maps fields based on common naming conventions.

        Args:
            item: Scraped item dict.
            stage_name: Name of scraping stage (for debugging).

        Returns:
            SearchResult object or None if missing required fields.
        """
        # Extract title (try multiple field names)
        release_name = (item.get("release_name") or "").strip() or None
        human_title = (item.get("title") or item.get("name") or "").strip() or None

        # Prefer release_name as the final title if present
        title = (release_name or human_title or "").strip()

        # Extract download link
        link = self._extract_download_link(item)

        # Skip if missing required fields
        if not title or not link:
            return None

        # Extract optional metadata
        seeders = to_int(item.get("seeders"))
        leechers = to_int(item.get("leechers"))
        size = item.get("size")
        description = item.get("description")
        source_url = item.get("source_url")

        return SearchResult(
            title=title,
            download_link=link,
            seeders=seeders,
            leechers=leechers,
            size=size,
            release_name=release_name,
            description=description,
            source_url=source_url,
            published_date=item.get("published_date"),
            download_links=item.get("download_links"),
            scraped_from_stage=stage_name,
            metadata={},
            category=2000,  # Default: Movies
            grabs=0,
            download_volume_factor=0.0,
            upload_volume_factor=0.0,
        )

    def _extract_download_link(self, item: dict) -> str | None:
        """Extract download link from item (handles various formats).

        Supports:
            - Direct link field: {"download_link": "https://..."}
            - Link field: {"link": "https://..."}
            - Nested links: {"download_links": [{"link": "https://..."}, ...]}

        Args:
            item: Scraped item dict.

        Returns:
            Download link URL or None if not found.
        """
        # Try direct fields
        if "download_link" in item:
            return item["download_link"]

        if "link" in item:
            return item["link"]

        # Try nested download_links (take first)
        if "download_links" in item:
            links = item["download_links"]

            if isinstance(links, list) and links:
                first = links[0]

                # List of dicts: [{"hoster": "Veev", "link": "..."}]
                if isinstance(first, dict):
                    return first.get("link")

                # List of strings: ["https://...", ...]
                if isinstance(first, str):
                    return first

        return None
