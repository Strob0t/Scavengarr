# src/scavengarr/adapters/scraping/httpx_scrapy_engine.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

import httpx
from diskcache import Cache

from scavengarr.adapters.scraping import ScrapyAdapter
from scavengarr.domain.entities import TorznabExternalError


@dataclass(frozen=True)
class SearchResult:
    """Search result from scrapy engine."""

    title: str
    download_link: str
    seeders: int | None = None
    leechers: int | None = None
    size: str | None = None
    release_name: str | None = None
    description: str | None = None
    source_url: str | None = None


class HttpxScrapySearchEngine:
    """
    Scrapy-based multi-stage search engine.

    Uses ScrapyAdapter for all scraping operations.
    """

    def __init__(self, *, http_client: httpx.AsyncClient, cache: Cache) -> None:
        self._http = http_client
        self._cache = cache

    async def search(self, plugin: Any, query: str, **params) -> List[SearchResult]:
        """
        Execute multi-stage search using plugin configuration.

        Args:
            plugin: Plugin configuration object
            query: Search query string
            **params: Additional parameters (e.g., category, filters)

        Returns:
            List of search results

        Raises:
            TorznabExternalError: If scraping fails
        """
        adapter = ScrapyAdapter(
            plugin=plugin, http_client=self._http, cache=self._cache
        )

        try:
            # Execute multi-stage scrape
            stage_results = await adapter.scrape(query=query, **params)

            # Convert to SearchResult format
            results: list[SearchResult] = []
            for stage_name, items in stage_results.items():
                for item in items:
                    result = self._convert_to_result(item, stage_name)
                    if result:
                        results.append(result)

            return results

        except Exception as e:
            raise TorznabExternalError(f"scrapy search failed: {e!s}") from e

    def _convert_to_result(self, item: dict, stage_name: str) -> SearchResult | None:
        """
        Convert scraped item to SearchResult.

        Maps fields based on common naming conventions.
        """
        # Extract title (try multiple field names)
        release_name = (item.get("release_name") or "").strip() or None
        human_title = (item.get("title") or item.get("name") or "").strip() or None

        # Prefer release_name as the final title if present.
        title = (release_name or human_title or "").strip()

        # Extract download link
        link = self._extract_download_link(item)
        if not title or not link:
            return None

        # Extract optional metadata
        seeders = _to_int(item.get("seeders"))
        leechers = _to_int(item.get("leechers"))
        size = item.get("size")
        release_name = item.get("release_name")
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
        )

    def _extract_download_link(self, item: dict) -> str | None:
        """
        Extract download link from item (handles various formats).

        Supports:
        - Direct link field: {"download_link": "https://..."}
        - Link field: {"link": "https://..."}
        - Nested links: {"download_links": [{"link": "https://..."}, ...]}
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
    """Convert string or int to int, return None if invalid."""
    if raw is None:
        return None

    if isinstance(raw, int):
        return raw

    if isinstance(raw, str):
        # Extract digits only
        txt = "".join(ch for ch in raw if ch.isdigit())
        if not txt:
            return None
        try:
            return int(txt)
        except ValueError:
            return None

    return None
