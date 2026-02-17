"""Port for CrawlJob persistence."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from scavengarr.domain.entities.crawljob import CrawlJob


@runtime_checkable
class CrawlJobRepository(Protocol):
    """Async interface for storing and retrieving CrawlJob entities."""

    async def save(self, job: CrawlJob) -> None: ...

    async def get(self, job_id: str) -> CrawlJob | None: ...
