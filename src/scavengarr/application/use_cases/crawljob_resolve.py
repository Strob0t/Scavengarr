"""Resolve a CrawlJob's links at grab time (``GrabResolvingPlugin``)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import structlog

from scavengarr.domain.entities.crawljob import CrawlJob, CrawlJobResolveError
from scavengarr.domain.plugins import GrabResolvingPlugin
from scavengarr.domain.ports.crawljob_repository import CrawlJobRepository
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort

log = structlog.get_logger(__name__)


class CrawlJobResolveUseCase:
    """Turn a job's page URLs into download links when it is grabbed.

    Jobs without ``resolve_plugin`` pass through unchanged. A resolved job is
    stored again without ``resolve_plugin``, so a repeated grab of the same
    job reuses the links instead of solving another captcha.
    """

    def __init__(
        self,
        *,
        plugins: PluginRegistryPort,
        crawljob_repo: CrawlJobRepository,
    ) -> None:
        self._plugins = plugins
        self._repo = crawljob_repo

    async def execute(self, job: CrawlJob) -> CrawlJob:
        plugin_name = job.resolve_plugin
        if plugin_name is None:
            return job

        try:
            plugin = self._plugins.get(plugin_name)
        except Exception as exc:
            raise CrawlJobResolveError(
                f"plugin not available: {plugin_name} ({exc!r})"
            ) from exc
        if not isinstance(plugin, GrabResolvingPlugin):
            raise CrawlJobResolveError(f"plugin cannot resolve links: {plugin_name}")

        started = time.monotonic()
        try:
            batches = await asyncio.gather(
                *(plugin.resolve_download(url) for url in job.validated_urls)
            )
        except Exception as exc:
            raise CrawlJobResolveError(
                f"{plugin_name} failed to resolve links: {exc!r}"
            ) from exc

        urls = list(dict.fromkeys(url for batch in batches for url in batch))
        duration_ms = int((time.monotonic() - started) * 1000)
        if not urls:
            log.warning(
                "crawljob_resolve_empty",
                plugin=plugin_name,
                job_id=job.job_id,
                duration_ms=duration_ms,
            )
            raise CrawlJobResolveError(f"{plugin_name} returned no download links")

        resolved = replace(
            job, text="\r\n".join(urls), validated_urls=urls, resolve_plugin=None
        )
        await self._repo.save(resolved)
        log.info(
            "crawljob_resolved",
            plugin=plugin_name,
            job_id=job.job_id,
            results_count=len(urls),
            duration_ms=duration_ms,
        )
        return resolved
