from __future__ import annotations

from typing import Protocol

from scavengarr.domain.entities.crawljob import CrawlJob


class CrawlJobRepository(Protocol):
    """Port for CrawlJob storage."""

    async def save(self, job: CrawlJob) -> None: ...

    async def get(self, job_id: str) -> CrawlJob | None: ...
