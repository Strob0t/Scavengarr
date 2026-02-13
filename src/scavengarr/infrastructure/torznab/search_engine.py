"""Search engine with link validation for Python plugins."""

from __future__ import annotations

import httpx
import structlog

from scavengarr.domain.plugins import SearchResult
from scavengarr.domain.ports import CachePort
from scavengarr.infrastructure.validation import HttpLinkValidator

log = structlog.get_logger(__name__)


class HttpxScrapySearchEngine:
    """Search engine providing link validation for Python plugin results.

    Features:
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

    async def _filter_valid_links(
        self,
        results: list[SearchResult],
    ) -> list[SearchResult]:
        """Batch-validate URLs and collect valid links per result.

        Results that already have ``validated_links`` populated are treated
        as pre-validated and passed through without HTTP checks.  This
        allows plugins behind anti-bot protection (e.g. DDoS-Guard) to
        skip link validation that would always fail via httpx.

        Flow:
            1. Separate pre-validated results from those needing validation.
            2. Collect all unique URLs across results needing validation.
            3. Single validate_batch() call for everything.
            4. For each result, assemble validated_links and promote if needed.
            5. Drop results with zero valid links.

        Args:
            results: Raw search results from scraper.

        Returns:
            Results with validated_links populated and reachable download_link.
        """
        pre_validated = [r for r in results if r.validated_links]
        needs_validation = [r for r in results if not r.validated_links]

        if not needs_validation:
            return results

        all_urls = self._collect_all_urls(needs_validation)

        if not all_urls:
            log.warning("no_download_links_to_validate")
            return pre_validated + needs_validation

        validation_map = await self._link_validator.validate_batch(list(all_urls))

        valid_results = [
            r
            for r in needs_validation
            if r.download_link and self._apply_validation(r, validation_map)
        ]

        filtered = len(needs_validation) - len(valid_results)
        if filtered > 0:
            log.info(
                "links_filtered",
                total=len(needs_validation),
                valid=len(valid_results),
                invalid=filtered,
                pre_validated=len(pre_validated),
            )

        return pre_validated + valid_results

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
