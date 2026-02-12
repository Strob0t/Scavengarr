"""Integration tests for CrawlJob lifecycle.

Tests the full flow: SearchResult → CrawlJobFactory → CacheCrawlJobRepository
→ DiskcacheAdapter (real SQLite), verifying round-trip persistence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scavengarr.application.factories.crawljob_factory import CrawlJobFactory
from scavengarr.domain.entities.crawljob import BooleanStatus, Priority
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.cache.diskcache_adapter import DiskcacheAdapter
from scavengarr.infrastructure.persistence.crawljob_cache import CacheCrawlJobRepository

pytestmark = pytest.mark.integration


@pytest.fixture()
def factory() -> CrawlJobFactory:
    return CrawlJobFactory(default_ttl_hours=2, auto_start=True)


@pytest.fixture()
def repo(diskcache: DiskcacheAdapter) -> CacheCrawlJobRepository:
    return CacheCrawlJobRepository(cache=diskcache, ttl_seconds=7200)


class TestCrawlJobRoundTrip:
    """Factory → Repository save → Repository get round-trip."""

    async def test_create_save_and_retrieve(
        self,
        factory: CrawlJobFactory,
        repo: CacheCrawlJobRepository,
    ) -> None:
        result = SearchResult(
            title="Iron.Man.2008.German.DL.1080p.BluRay",
            download_link="https://hoster.example.com/file1.mp4",
            size="4.5 GB",
            release_name="Iron.Man.2008.German.DL.1080p.BluRay.x264",
            source_url="https://filmpalast.to/movie/iron-man",
            validated_links=[
                "https://hoster.example.com/file1.mp4",
                "https://hoster2.example.com/file1.mp4",
            ],
        )

        job = factory.create_from_search_result(result)
        await repo.save(job)

        loaded = await repo.get(job.job_id)
        assert loaded is not None
        assert loaded.job_id == job.job_id
        assert loaded.package_name == "Iron.Man.2008.German.DL.1080p.BluRay"
        assert loaded.validated_urls == [
            "https://hoster.example.com/file1.mp4",
            "https://hoster2.example.com/file1.mp4",
        ]
        assert loaded.source_url == "https://filmpalast.to/movie/iron-man"
        assert loaded.auto_start == BooleanStatus.TRUE

    async def test_retrieve_nonexistent_returns_none(
        self,
        repo: CacheCrawlJobRepository,
    ) -> None:
        loaded = await repo.get("nonexistent-job-id")
        assert loaded is None

    async def test_crawljob_format_after_round_trip(
        self,
        factory: CrawlJobFactory,
        repo: CacheCrawlJobRepository,
    ) -> None:
        """Ensure .crawljob serialization works after persistence round-trip."""
        result = SearchResult(
            title="The.Matrix.1999.1080p",
            download_link="https://hoster.example.com/matrix.mp4",
            validated_links=["https://hoster.example.com/matrix.mp4"],
        )

        job = factory.create_from_search_result(result)
        await repo.save(job)

        loaded = await repo.get(job.job_id)
        assert loaded is not None

        crawljob_text = loaded.to_crawljob_format()
        assert "text=https://hoster.example.com/matrix.mp4" in crawljob_text
        assert "packageName=The.Matrix.1999.1080p" in crawljob_text

    async def test_multiple_jobs_independent(
        self,
        factory: CrawlJobFactory,
        repo: CacheCrawlJobRepository,
    ) -> None:
        """Multiple jobs stored independently, no cross-contamination."""
        results = [
            SearchResult(
                title=f"Movie.{i}.1080p",
                download_link=f"https://hoster.example.com/file{i}.mp4",
            )
            for i in range(3)
        ]

        jobs = [factory.create_from_search_result(r) for r in results]
        for job in jobs:
            await repo.save(job)

        for i, job in enumerate(jobs):
            loaded = await repo.get(job.job_id)
            assert loaded is not None
            assert loaded.package_name == f"Movie.{i}.1080p"

    async def test_factory_preserves_release_name_as_filename(
        self,
        factory: CrawlJobFactory,
        repo: CacheCrawlJobRepository,
    ) -> None:
        result = SearchResult(
            title="Inception",
            download_link="https://hoster.example.com/inception.mp4",
            release_name="Inception.2010.German.DL.1080p.BluRay.x264",
        )

        job = factory.create_from_search_result(result)
        assert job.filename == "Inception.2010.German.DL.1080p.BluRay.x264"

        await repo.save(job)
        loaded = await repo.get(job.job_id)
        assert loaded is not None
        assert loaded.filename == "Inception.2010.German.DL.1080p.BluRay.x264"

    async def test_factory_default_priority(
        self,
        factory: CrawlJobFactory,
    ) -> None:
        result = SearchResult(
            title="Test",
            download_link="https://hoster.example.com/test.mp4",
        )
        job = factory.create_from_search_result(result)
        assert job.priority == Priority.DEFAULT
        assert not job.is_expired()
