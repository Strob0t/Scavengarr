"""
Scrapy Engine for Scavengarr.

Supports cascading scrape pipelines with:
- Async HTTP via httpx.AsyncClient (injected from FastAPI)
- Diskcache for URL deduplication & result caching
- Exponential backoff retry logic
- CSS selector-based extraction
- Pagination & nested data extraction
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urljoin

import httpx
import structlog
from bs4 import BeautifulSoup
from diskcache import Cache

from scavengarr.domain.plugins import (
    NestedSelector,
    ScrapingStage,
    SearchResult,
    YamlPluginDefinition,
)
from scavengarr.infrastructure.common.converters import to_int

logger = structlog.get_logger(__name__)


class StageScraper:
    """
    Single scraping stage executor.

    Responsibilities:
    - Extract data using CSS selectors
    - Extract links to next stage
    - Handle pagination
    """

    def __init__(self, stage: ScrapingStage, base_url: str):
        self.stage = stage
        self.base_url = base_url
        self.name = stage.name
        self.selectors = stage.selectors

    def build_url(self, url: str | None = None, **url_params: Any) -> str:
        """Build URL from template or use provided URL."""
        if url:
            return url

        if self.stage.url:
            return urljoin(self.base_url, self.stage.url)

        if self.stage.url_pattern:
            try:
                path = self.stage.url_pattern.format(**url_params)
                return urljoin(self.base_url, path)
            except KeyError as e:
                raise ValueError(
                    f"Stage '{self.name}': Missing URL parameter {e} for pattern '{self.stage.url_pattern}'"
                )

        raise ValueError(f"Stage '{self.name}': No URL or url_pattern defined")

    def extract_data(self, soup: BeautifulSoup) -> dict[str, Any]:
        """
        Extract data from page using selectors.
        """
        data: dict[str, Any] = {}

        # Simple selectors - with explicit attribute configuration
        simple_fields = {
            "title": (self.selectors.title, "text"),
            "description": (self.selectors.description, "text"),
            "release_name": (self.selectors.release_name, "text"),
            "download_link": (self.selectors.download_link, "attribute"),
            "seeders": (self.selectors.seeders, "text"),
            "leechers": (self.selectors.leechers, "text"),
            "size": (self.selectors.size, "text"),
            "published_date": (self.selectors.published_date, "text"),
        }

        for field, (selector, extract_type) in simple_fields.items():
            if not selector:
                continue

            elem = soup.select_one(selector)
            if not elem:
                continue

            if extract_type == "attribute":
                # Use field_attributes from stage config
                attrs = self._get_field_attributes(field)
                data[field] = self._extract_from_attributes(elem, attrs, field)
            else:
                # Text extraction
                data[field] = elem.get_text(strip=True)

        # Custom fields (all as text)
        for field, selector in self.selectors.custom.items():
            elem = soup.select_one(selector)
            if elem:
                data[field] = elem.get_text(strip=True)

        # Nested selectors
        if self.selectors.download_links:
            data["download_links"] = self._extract_nested(
                soup, self.selectors.download_links
            )

        return data

    def _get_field_attributes(self, field_name: str) -> list[str]:
        """
        Get attribute list for a field from stage config.
        Returns empty list if not configured (causes warning in _extract_from_attributes).
        """
        if not self.stage.field_attributes:
            return []
        return self.stage.field_attributes.get(field_name, [])

    def _extract_nested(
        self, soup: BeautifulSoup, nested_config: NestedSelector
    ) -> list[dict[str, Any]]:
        """
        Generic nested extraction with optional grouping.

        Two modes:
        1. item_group NOT set: Each 'items' element = 1 result
        2. item_group SET: All 'items' within each group = 1 merged result
        """
        results = []
        container = soup.select_one(nested_config.container)

        if not container:
            logger.warning(
                "nested_container_not_found",
                stage=self.name,
                selector=nested_config.container,
            )
            return results

        # MODE 1: Grouped extraction
        if nested_config.item_group:
            groups = container.select(nested_config.item_group)

            for group in groups:
                merged_data = {}

                for item in group.select(nested_config.items):
                    item_data = self._extract_item_fields(item, nested_config)
                    # Merge fields (with multi-value support)
                    merged_data = self._merge_item_data(
                        merged_data, item_data, nested_config.multi_value_fields or []
                    )

                if merged_data:
                    results.append(merged_data)

            logger.debug(
                "grouped_extraction_complete",
                stage=self.name,
                groups=len(groups),
                results=len(results),
            )

        # MODE 2: Direct extraction
        else:
            for item in container.select(nested_config.items):
                item_data = self._extract_item_fields(item, nested_config)

                if item_data:
                    results.append(item_data)

            logger.debug(
                "direct_extraction_complete",
                stage=self.name,
                items=len(results),
            )

        return results

    def _extract_item_fields(
        self, item, nested_config: NestedSelector
    ) -> dict[str, Any]:
        """
        Extract all configured fields from a single item element.
        """
        item_data = {}

        for field_name, field_selector in nested_config.fields.items():
            elem = item.select_one(field_selector)
            if not elem:
                continue

            # Link/URL extraction via attributes
            if field_name.endswith("link") or field_name.endswith("url"):
                attrs = nested_config.field_attributes.get(field_name, [])
                value = self._extract_from_attributes(elem, attrs, field_name)
            else:
                # Text extraction
                value = elem.get_text(strip=True)

            if value:
                item_data[field_name] = value

        return item_data

    def _merge_item_data(
        self,
        target: dict[str, Any],
        source: dict[str, Any],
        multi_value_fields: list[str],
    ) -> dict[str, Any]:
        """
        Merge source data into target with multi-value field support.

        Args:
            target: Existing accumulated data
            source: New data to merge
            multi_value_fields: Fields that should be collected as lists

        Returns:
            Merged dict
        """
        for key, value in source.items():
            if key in multi_value_fields:
                # Multi-value: Append to list
                if key not in target:
                    target[key] = []
                target[key].append(value)
            else:
                # Single-value: Overwrite (last wins)
                target[key] = value

        return target

    def _extract_from_attributes(
        self, elem, attributes: list[str], field_name: str
    ) -> str | None:
        """
        Extract value with smart parsing for special attributes.
        """
        import re

        for attr in attributes:
            value = elem.get(attr)
            if not value:
                continue

            # Special: onclick often contains JS code with URL
            if attr == "onclick":
                # Extract URL from onclick="embedy('https://...')"
                match = re.search(r'https?://[^\'")\s]+', value)
                if match:
                    url = match.group(0)
                    logger.debug(
                        "url_extracted_from_onclick",
                        stage=self.name,
                        field=field_name,
                        url=url[:80],
                    )
                    return url

            # Normal: Direct attribute value
            elif value:
                logger.debug(
                    "attribute_extracted",
                    stage=self.name,
                    field=field_name,
                    attribute=attr,
                    value_preview=value[:80] if len(value) > 80 else value,
                )
                return value

        logger.warning(
            "no_attributes_found",
            stage=self.name,
            field=field_name,
            tried_attributes=attributes,
            element_preview=str(elem)[:200],
        )
        return None

    def extract_links(self, soup: BeautifulSoup) -> list[str]:
        """
        Extract unique links to next stage (for list stages).
        Uses selectors.link to find all elements.
        Deduplicates while preserving order.
        """
        if not self.selectors.link:
            return []

        seen: set[str] = set()
        links: list[str] = []
        for elem in soup.select(self.selectors.link):
            href = elem.get("href")
            if href:
                full_url = urljoin(self.base_url, href)
                if full_url not in seen:
                    seen.add(full_url)
                    links.append(full_url)

        return links

    def should_process(self, data: dict[str, Any]) -> bool:
        """
        Check if stage conditions are met.

        Example condition:
          conditions:
            min_seeders: 5
        """
        if not self.stage.conditions:
            return True

        for key, threshold in self.stage.conditions.items():
            if key.startswith("min_"):
                field = key[4:]  # Remove "min_" prefix
                value = data.get(field)
                if value is None:
                    return False
                try:
                    if float(value) < threshold:
                        return False
                except (ValueError, TypeError):
                    return False

        return True


class ScrapyAdapter:
    """
    Async multi-stage scraping engine.

    Orchestrates cascading stages:
    1. Load plugin config
    2. Execute start stage
    3. Follow links to next stages recursively
    4. Aggregate results
    5. Normalize to SearchResult

    Features:
    - Async HTTP via httpx.AsyncClient (injected)
    - Diskcache for visited URL tracking
    - Exponential backoff retry logic
    - Rate limiting via asyncio.sleep
    """

    def __init__(
        self,
        plugin: YamlPluginDefinition,
        http_client: httpx.AsyncClient,
        cache: Cache,
        delay_seconds: float = 1.5,
        max_depth: int = 5,
        max_retries: int = 3,
        retry_backoff_base: float = 2.0,
    ):
        if plugin.scraping.mode != "scrapy":
            raise ValueError(
                f"Plugin '{plugin.name}' is not in scrapy mode (mode={plugin.scraping.mode})"
            )

        self.plugin = plugin

        # FIX: Extract plugin_name safely
        self.plugin_name = str(getattr(plugin, "name", "unknown"))

        self.base_url = str(plugin.base_url)
        self.delay = delay_seconds
        self.max_depth = max_depth
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base

        # Build stage executors
        self.stages: dict[str, StageScraper] = {}
        for stage_config in plugin.scraping.stages or []:
            self.stages[stage_config.name] = StageScraper(stage_config, self.base_url)

        self.start_stage_name = (
            plugin.scraping.start_stage or list(self.stages.keys())[0]
        )

        # HTTP client (injected from FastAPI)
        self.client = http_client

        # Diskcache for visited URLs
        self.cache = cache

        # FIX: Use set for visited URLs (not dict.keys())
        self.visited_urls: set[str] = set()

        logger.info(
            "scrapy_adapter_initialized",
            plugin=self.plugin_name,
            start_stage=self.start_stage_name,
            total_stages=len(self.stages),
        )

    async def _fetch_page(self, url: str) -> BeautifulSoup | None:
        """
        Fetch page with rate limiting, retry logic, and loop detection.
        Returns BeautifulSoup object or None on failure.
        """
        # Loop detection via Set — mark BEFORE yielding control
        # to prevent duplicate fetches from concurrent asyncio tasks.
        if url in self.visited_urls:
            logger.debug("url_already_visited", url=url)
            return None
        self.visited_urls.add(url)

        # Exponential backoff retry
        for attempt in range(self.max_retries):
            try:
                # Rate limiting
                await asyncio.sleep(self.delay)

                logger.debug(
                    "http_request_start",
                    url=url,
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                )

                response = await self.client.get(url)
                response.raise_for_status()

                logger.info("page_fetched", url=url, status_code=response.status_code)

                return BeautifulSoup(response.content, "html.parser")

            except httpx.HTTPStatusError as e:
                logger.warning(
                    "http_status_error",
                    url=url,
                    status_code=e.response.status_code,
                    attempt=attempt + 1,
                )

                # Don't retry on 4xx errors (client errors)
                if 400 <= e.response.status_code < 500:
                    logger.error(
                        "client_error_no_retry", url=url, status=e.response.status_code
                    )
                    return None

                # Retry on 5xx errors
                if attempt < self.max_retries - 1:
                    backoff = self.retry_backoff_base**attempt
                    logger.info(
                        "retrying_after_backoff", url=url, backoff_seconds=backoff
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error("max_retries_exceeded", url=url)
                    return None

            except httpx.RequestError as e:
                logger.warning(
                    "http_request_error",
                    url=url,
                    error=str(e),
                    attempt=attempt + 1,
                )

                if attempt < self.max_retries - 1:
                    backoff = self.retry_backoff_base**attempt
                    logger.info(
                        "retrying_after_backoff", url=url, backoff_seconds=backoff
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error("max_retries_exceeded", url=url, error=str(e))
                    return None

        return None

    async def scrape_stage(
        self,
        stage_name: str,
        url: str | None = None,
        depth: int = 0,
        **url_params: Any,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Execute single stage (recursively).

        Returns Dict[stage_name, List[items]] for aggregation.
        """
        if depth > self.max_depth:
            logger.warning(
                "max_depth_reached", stage=stage_name, depth=depth, max=self.max_depth
            )
            return {}

        if stage_name not in self.stages:
            logger.error("stage_not_found", stage=stage_name)
            return {}

        stage = self.stages[stage_name]
        stage_config = stage.stage

        # Build URL
        if url is None:
            url = stage.build_url(**url_params)

        logger.info("scrape_stage_start", stage=stage_name, depth=depth, url=url)

        # Fetch page
        soup = await self._fetch_page(url)
        if not soup:
            return {}

        # Extract data
        data = stage.extract_data(soup)

        # Add source URL to data
        data["source_url"] = url

        # Check conditions
        if not stage.should_process(data):
            logger.debug("stage_conditions_not_met", stage=stage_name, data=data)
            return {}

        # Extract links to next stage
        links = stage.extract_links(soup)

        # FIX: Return Dict[stage_name, List[items]]
        results: dict[str, list[dict[str, Any]]] = {stage_name: [data]}

        # Pagination (if enabled)
        if stage_config.pagination and stage_config.pagination.enabled:
            paginated = await self._handle_pagination(stage, soup, depth)
            if stage_name in paginated:
                results[stage_name].extend(paginated[stage_name])

        # Recurse to next stage (parallel, bounded by max_links)
        if stage_config.next_stage and links:
            next_stage_name = stage_config.next_stage

            # Limit links to prevent memory overflow
            max_links = 10
            limited_links = links[:max_links]

            # Parallel execution of next-stage scraping
            sub_results_list = await asyncio.gather(
                *(
                    self.scrape_stage(next_stage_name, url=link, depth=depth + 1)
                    for link in limited_links
                )
            )

            # Merge all sub_results
            for sub_results in sub_results_list:
                for sub_stage, sub_items in sub_results.items():
                    if sub_stage not in results:
                        results[sub_stage] = []
                    results[sub_stage].extend(sub_items)

            if len(links) > max_links:
                logger.warning(
                    "links_truncated",
                    stage=stage_name,
                    total_links=len(links),
                    processed=max_links,
                )

        return results

    async def _handle_pagination(
        self, stage: StageScraper, soup: BeautifulSoup, depth: int
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Handle pagination for list stages.
        Follows "next page" links up to max_pages.
        """
        results: dict[str, list[dict[str, Any]]] = {stage.name: []}
        pagination = stage.stage.pagination

        if not pagination or not pagination.enabled:
            return results

        next_selector = pagination.selector
        max_pages = pagination.max_pages or 1

        for page_num in range(1, max_pages):
            next_link_elem = soup.select_one(next_selector)
            if not next_link_elem:
                break

            next_url = next_link_elem.get("href")
            if not next_url:
                break

            next_url = urljoin(self.base_url, next_url)

            logger.debug(
                "pagination_next", stage=stage.name, page=page_num + 1, url=next_url
            )

            soup = await self._fetch_page(next_url)
            if not soup:
                break

            # Extract data from paginated page
            data = stage.extract_data(soup)
            data["source_url"] = next_url

            if stage.should_process(data):
                results[stage.name].append(data)

        return results

    async def scrape(
        self, query: str, **params: Any
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Start multi-stage scraping pipeline.

        Args:
            query: Search query string
            **params: Additional URL parameters (e.g., category, page)

        Returns:
            Dict[stage_name, List[items]] - raw data from all stages
        """
        logger.info(
            "scrapy_scrape_start",
            plugin=self.plugin_name,
            query=query,
            start_stage=self.start_stage_name,
        )

        # Reset visited URLs for new scrape
        self.visited_urls.clear()

        # Add query to params
        params["query"] = query

        results = await self.scrape_stage(self.start_stage_name, **params)

        logger.debug(
            "scrape_results_detail",
            plugin=self.plugin_name,
            result_type=type(results).__name__,
            stages=list(results.keys()),
            stage_counts={k: len(v) for k, v in results.items()},
        )

        logger.info(
            "scrapy_scrape_complete",
            plugin=self.plugin_name,
            total_results=sum(len(v) for v in results.values()),
        )

        return results

    def normalize_results(
        self, stage_results: dict[str, list[dict[str, Any]]]
    ) -> list[SearchResult]:
        """
        Convert stage results to SearchResult.
        Merges data from multiple stages (e.g., title from list + release_name from detail).
        """
        search_results = []

        # Process all stages (prioritize detail stages)
        for stage_name, items in stage_results.items():
            for data in items:
                # Extract SearchResult fields
                search_result = SearchResult(
                    title=data.get("title") or data.get("release_name", "Unknown"),
                    download_link=data.get("download_link", ""),
                    seeders=to_int(data.get("seeders")),
                    leechers=to_int(data.get("leechers")),
                    size=data.get("size"),
                    published_date=data.get("published_date"),
                    # Multi-stage specific
                    release_name=data.get("release_name"),
                    description=data.get("description"),
                    download_links=data.get("download_links"),
                    source_url=data.get("source_url"),
                    scraped_from_stage=stage_name,
                    metadata={
                        k: v
                        for k, v in data.items()
                        if k
                        not in [
                            "title",
                            "download_link",
                            "seeders",
                            "leechers",
                            "size",
                            "published_date",
                            "release_name",
                            "description",
                            "download_links",
                            "source_url",
                        ]
                    },
                )

                search_results.append(search_result)

        return search_results
