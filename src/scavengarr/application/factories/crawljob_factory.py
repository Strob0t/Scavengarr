"""Factory for creating CrawlJob entities from SearchResults."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from scavengarr.domain.entities.crawljob import BooleanStatus, CrawlJob, Priority
from scavengarr.domain.plugins import SearchResult

log = structlog.get_logger(__name__)


class CrawlJobFactory:
    """Factory for creating CrawlJob entities from validated search results.

    Converts SearchResult (scraper output) → CrawlJob (JDownloader format).
    """

    def __init__(
        self,
        *,
        default_ttl_hours: int = 1,
        auto_start: bool = True,
        default_priority: Priority = Priority.DEFAULT,
    ) -> None:
        """Initialize factory with default settings.

        Args:
            default_ttl_hours: Time-to-live for CrawlJobs (hours).
            auto_start: Enable auto-start by default.
            default_priority: Default download priority.
        """
        self.default_ttl_hours = default_ttl_hours
        self.auto_start = auto_start
        self.default_priority = default_priority

    def create_from_search_result(
        self,
        result: SearchResult,
        *,
        job_id: str | None = None,
    ) -> CrawlJob:
        """Create CrawlJob from validated SearchResult.

        Args:
            result: Validated search result (with reachable download_link).
            job_id: Optional custom job ID (default: auto-generated UUID4).

        Returns:
            CrawlJob entity with JDownloader-compatible fields.
        """
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=self.default_ttl_hours)

        # Extract metadata from SearchResult
        package_name = result.title or "Scavengarr Download"
        comment = self._build_comment(result)

        # Build text field (newline-separated links)
        # For now, single link per job. Future: support multi-part archives.
        text = result.download_link

        crawl_job = CrawlJob(
            text=text,
            package_name=package_name,
            comment=comment,
            validated_urls=[result.download_link],
            source_url=result.source_url,
            created_at=now,
            expires_at=expires_at,
            auto_start=BooleanStatus.TRUE if self.auto_start else BooleanStatus.FALSE,
            priority=self.default_priority,
            filename=result.release_name,  # Override filename if present
        )

        log.debug(
            "crawljob_created",
            job_id=crawl_job.job_id,
            package_name=package_name,
            link_count=len(crawl_job.validated_urls),
            ttl_hours=self.default_ttl_hours,
        )

        return crawl_job

    def _build_comment(self, result: SearchResult) -> str:
        """Build comment string with metadata.

        Args:
            result: Search result.

        Returns:
            Formatted comment with size, etc.
        """
        parts = []

        if result.description:
            parts.append(result.description)

        # if result.seeders is not None:
        #     parts.append(f"Seeders: {result.seeders}")

        # if result.leechers is not None:
        #     parts.append(f"Leechers: {result.leechers}")

        if result.size:
            parts.append(f"Size: {result.size}")

        if result.source_url:
            parts.append(f"Source: {result.source_url}")

        return " | ".join(parts) if parts else "Downloaded via Scavengarr"
