"""CrawlJob repository backed by CachePort (diskcache/redis)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from scavengarr.domain.entities.crawljob import BooleanStatus, CrawlJob, Priority
from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)


def _serialize_crawljob(job: CrawlJob) -> str:
    """Serialize CrawlJob to JSON string."""
    return json.dumps(
        {
            "job_id": job.job_id,
            "text": job.text,
            "package_name": job.package_name,
            "filename": job.filename,
            "comment": job.comment,
            "validated_urls": job.validated_urls,
            "source_url": job.source_url,
            "created_at": job.created_at.isoformat(),
            "expires_at": job.expires_at.isoformat(),
            "download_folder": job.download_folder,
            "chunks": job.chunks,
            "priority": job.priority.value,
            "auto_start": job.auto_start.value,
            "auto_confirm": job.auto_confirm.value,
            "forced_start": job.forced_start.value,
            "enabled": job.enabled.value,
            "extract_after_download": job.extract_after_download.value,
            "extract_passwords": job.extract_passwords,
            "download_password": job.download_password,
            "deep_analyse_enabled": job.deep_analyse_enabled,
            "add_offline_link": job.add_offline_link,
            "overwrite_packagizer_enabled": job.overwrite_packagizer_enabled,
            "set_before_packagizer_enabled": job.set_before_packagizer_enabled,
        }
    )


def _deserialize_crawljob(data: str) -> CrawlJob:
    """Deserialize CrawlJob from JSON string."""
    d = json.loads(data)
    return CrawlJob(
        job_id=d["job_id"],
        text=d["text"],
        package_name=d["package_name"],
        filename=d.get("filename"),
        comment=d.get("comment"),
        validated_urls=d.get("validated_urls", []),
        source_url=d.get("source_url"),
        created_at=datetime.fromisoformat(d["created_at"]).replace(tzinfo=timezone.utc),
        expires_at=datetime.fromisoformat(d["expires_at"]).replace(tzinfo=timezone.utc),
        download_folder=d.get("download_folder"),
        chunks=d.get("chunks", 0),
        priority=Priority(d.get("priority", "DEFAULT")),
        auto_start=BooleanStatus(d.get("auto_start", "TRUE")),
        auto_confirm=BooleanStatus(d.get("auto_confirm", "UNSET")),
        forced_start=BooleanStatus(d.get("forced_start", "UNSET")),
        enabled=BooleanStatus(d.get("enabled", "TRUE")),
        extract_after_download=BooleanStatus(d.get("extract_after_download", "UNSET")),
        extract_passwords=d.get("extract_passwords", []),
        download_password=d.get("download_password"),
        deep_analyse_enabled=d.get("deep_analyse_enabled", False),
        add_offline_link=d.get("add_offline_link", True),
        overwrite_packagizer_enabled=d.get("overwrite_packagizer_enabled", False),
        set_before_packagizer_enabled=d.get("set_before_packagizer_enabled", False),
    )


class CacheCrawlJobRepository:
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
        await self.cache.set(key, _serialize_crawljob(job), ttl=self.ttl)
        log.debug("crawljob_saved", job_id=job.job_id, ttl=self.ttl)

    async def get(self, job_id: str) -> CrawlJob | None:
        """Load CrawlJob from cache."""
        key = f"crawljob:{job_id}"
        data = await self.cache.get(key)
        if data is None:
            log.debug("crawljob_not_found", job_id=job_id)
            return None

        try:
            job = _deserialize_crawljob(data)
            log.debug("crawljob_loaded", job_id=job_id)
            return job
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.error("crawljob_deserialize_error", job_id=job_id, error=str(e))
            return None
