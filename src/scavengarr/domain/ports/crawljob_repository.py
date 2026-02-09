from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from scavengarr.domain.entities.crawljob import CrawlJob


class CrawlJobRepository(ABC):
    """Port for CrawlJob storage."""

    @abstractmethod
    async def save(self, job: CrawlJob) -> None:
        pass

    @abstractmethod
    async def get(self, job_id: str) -> Optional[CrawlJob]:
        pass
