from __future__ import annotations

import pickle
from typing import Optional

import structlog

from scavengarr.domain.entities.crawljob import CrawlJob
from scavengarr.domain.ports.cache import CachePort
from scavengarr.domain.ports.crawljob_repository import CrawlJobRepository

log = structlog.get_logger(__name__)


class CacheCrawlJobRepository(CrawlJobRepository):
    """Stores CrawlJobs via CachePort (Redis or Diskcache)."""

    def __init__(self, cache: CachePort, ttl_seconds: int = 3600):
        """
        Args:
            cache: CachePort implementation (injected by factory).
            ttl_seconds: Default TTL for CrawlJobs.
        """
        self.cache = cache
        self.ttl = ttl_seconds

    async def save(self, job: CrawlJob) -> None:
        """Save CrawlJob in cache with TTL."""
        key = f"crawljob:{job.job_id}"
        # CachePort accepts Any -> store pickled directly
        await self.cache.set(key, pickle.dumps(job), ttl=self.ttl)
        log.debug("crawljob_saved", job_id=job.job_id, ttl=self.ttl)

    async def get(self, job_id: str) -> Optional[CrawlJob]:
        """Load CrawlJob from cache."""
        key = f"crawljob:{job_id}"
        data = await self.cache.get(key)
        if data is None:
            log.debug("crawljob_not_found", job_id=job_id)
            return None

        try:
            job = pickle.loads(data)
            log.debug("crawljob_loaded", job_id=job_id)
            return job
        except (pickle.PickleError, TypeError) as e:
            log.error("crawljob_deserialize_error", job_id=job_id, error=str(e))
            return None
