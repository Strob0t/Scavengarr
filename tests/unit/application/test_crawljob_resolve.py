"""Tests for CrawlJobResolveUseCase (grab-time link resolution)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from scavengarr.application.use_cases.crawljob_resolve import CrawlJobResolveUseCase
from scavengarr.domain.entities.crawljob import CrawlJob, CrawlJobResolveError

_PAGE = "https://nox.to/media/iron-man?release=1"


def _job(resolve_plugin: str | None = "nox") -> CrawlJob:
    return CrawlJob(
        job_id="job-1",
        text=_PAGE,
        validated_urls=[_PAGE],
        resolve_plugin=resolve_plugin,
    )


def _use_case(plugin: object) -> tuple[CrawlJobResolveUseCase, AsyncMock]:
    registry = MagicMock()
    registry.get.return_value = plugin
    repo = AsyncMock()
    return CrawlJobResolveUseCase(plugins=registry, crawljob_repo=repo), repo


class TestCrawlJobResolveUseCase:
    async def test_job_without_resolver_is_returned_unchanged(self) -> None:
        plugin = SimpleNamespace(resolve_download=AsyncMock())
        uc, repo = _use_case(plugin)
        job = _job(resolve_plugin=None)

        assert await uc.execute(job) is job
        plugin.resolve_download.assert_not_awaited()
        repo.save.assert_not_awaited()

    async def test_links_are_resolved_and_the_job_is_stored(self) -> None:
        plugin = SimpleNamespace(
            resolve_download=AsyncMock(return_value=["https://a/1", "https://b/2"])
        )
        uc, repo = _use_case(plugin)

        resolved = await uc.execute(_job())

        plugin.resolve_download.assert_awaited_once_with(_PAGE)
        assert resolved.validated_urls == ["https://a/1", "https://b/2"]
        assert resolved.text == "https://a/1\r\nhttps://b/2"
        assert resolved.resolve_plugin is None
        assert resolved.job_id == "job-1"
        repo.save.assert_awaited_once_with(resolved)

    async def test_duplicate_links_are_dropped_in_order(self) -> None:
        plugin = SimpleNamespace(
            resolve_download=AsyncMock(side_effect=[["https://a/1"], ["https://a/1"]])
        )
        uc, _ = _use_case(plugin)
        job = CrawlJob(
            validated_urls=[_PAGE, _PAGE + "2"], text="", resolve_plugin="nox"
        )

        resolved = await uc.execute(job)

        assert resolved.validated_urls == ["https://a/1"]

    async def test_no_links_raises(self) -> None:
        uc, repo = _use_case(
            SimpleNamespace(resolve_download=AsyncMock(return_value=[]))
        )

        with pytest.raises(CrawlJobResolveError, match="no download links"):
            await uc.execute(_job())
        repo.save.assert_not_awaited()

    async def test_plugin_error_is_mapped(self) -> None:
        plugin = SimpleNamespace(
            resolve_download=AsyncMock(side_effect=RuntimeError("boom"))
        )
        uc, _ = _use_case(plugin)

        with pytest.raises(CrawlJobResolveError, match="boom"):
            await uc.execute(_job())

    async def test_plugin_without_hook_raises(self) -> None:
        uc, _ = _use_case(SimpleNamespace(name="nox"))

        with pytest.raises(CrawlJobResolveError, match="cannot resolve"):
            await uc.execute(_job())

    async def test_unknown_plugin_raises(self) -> None:
        registry = MagicMock()
        registry.get.side_effect = KeyError("nox")
        uc = CrawlJobResolveUseCase(plugins=registry, crawljob_repo=AsyncMock())

        with pytest.raises(CrawlJobResolveError, match="nox"):
            await uc.execute(_job())
