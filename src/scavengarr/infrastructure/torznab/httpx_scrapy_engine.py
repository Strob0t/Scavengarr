"""Scrapy-based multi-stage search engine with link validation."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from scavengarr.infrastructure.scraping import ScrapyAdapter
from scavengarr.domain.entities import TorznabExternalError
from scavengarr.domain.plugins import SearchResult
from scavengarr.domain.ports import CachePort
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

    def _convert_stage_results(
        self,
        stage_results: dict[str, list[dict]],
    ) -> list[SearchResult]:
        """Convert multi-stage scraped items to SearchResult objects.

        Args:
            stage_results: Dict mapping stage_name -> list of scraped items.

        Returns:
            Flat list of SearchResult objects.
        """
        results: list[SearchResult] = []

        for stage_name, items in stage_results.items():
            for item in items:
                result = self._convert_to_result(item, stage_name)
                if result:
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
        """Validate download links and filter out dead links.

        Args:
            results: Raw search results from scraper.

        Returns:
            Only results with reachable download links.
        """
        # Extract all download links
        urls = [r.download_link for r in results if r.download_link]

        if not urls:
            log.warning("no_download_links_to_validate")
            return results  # No links to validate

        # Batch validation (parallel HEAD requests)
        validation_map = await self._link_validator.validate_batch(urls)

        # Filter results: keep only valid links
        valid_results = [
            r
            for r in results
            if r.download_link and validation_map.get(r.download_link, False)
        ]

        # Log filtered results
        if len(valid_results) < len(results):
            invalid_links = [
                r.download_link
                for r in results
                if r.download_link and not validation_map.get(r.download_link, False)
            ]
            log.info(
                "links_filtered",
                total=len(results),
                valid=len(valid_results),
                invalid=len(results) - len(valid_results),
                sample_invalid=invalid_links[:3],  # Log first 3 dead links
            )

        return valid_results

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
        seeders = _to_int(item.get("seeders"))
        leechers = _to_int(item.get("leechers"))
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


def _to_int(raw: str | int | None) -> int | None:
    """Convert string or int to int, return None if invalid.

    Args:
        raw: Input value (str, int, or None).

    Returns:
        Integer or None if conversion fails.
    """
    if raw is None:
        return None

    if isinstance(raw, int):
        return raw

    if isinstance(raw, str):
        # Extract digits only (handles "1,234" -> 1234)
        txt = "".join(ch for ch in raw if ch.isdigit())
        if not txt:
            return None
        try:
            return int(txt)
        except ValueError:
            return None

    return None
