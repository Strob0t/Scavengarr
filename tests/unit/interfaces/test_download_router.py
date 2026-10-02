"""Tests for the .crawljob download endpoint (incl. grab-time resolution)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scavengarr.domain.entities.crawljob import CrawlJob
from scavengarr.interfaces.api.download.router import router

_PAGE = "https://nox.to/media/iron-man?release=1"


def _client(job: CrawlJob | None, plugin: object | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    repo = AsyncMock()
    repo.get.return_value = job
    registry = MagicMock()
    registry.get.return_value = plugin
    app.state.crawljob_repo = repo
    app.state.plugins = registry
    return TestClient(app)


def _job(resolve_plugin: str | None) -> CrawlJob:
    return CrawlJob(
        job_id="job-1",
        text=_PAGE,
        validated_urls=[_PAGE],
        package_name="Iron Man",
        resolve_plugin=resolve_plugin,
    )


class TestDownloadCrawljob:
    def test_plain_job_is_served_as_stored(self) -> None:
        resp = _client(_job(None)).get("/api/v1/download/job-1")

        assert resp.status_code == 200
        assert f"text={_PAGE}" in resp.text

    def test_non_latin1_title_is_served(self) -> None:
        """HTTP header values are Latin-1; an en dash or CJK title gave 500."""
        job = CrawlJob(
            job_id="job-1",
            text=_PAGE,
            validated_urls=[_PAGE],
            package_name="Spider-Man – No Way Home 東京",
        )

        resp = _client(job).get("/api/v1/download/job-1")

        assert resp.status_code == 200
        disposition = resp.headers["Content-Disposition"]
        assert disposition.startswith("attachment; filename=")
        assert "filename*=UTF-8''" in disposition
        assert resp.headers["X-CrawlJob-Package"].isascii()

    def test_missing_job_is_404(self) -> None:
        resp = _client(None).get("/api/v1/download/job-1")

        assert resp.status_code == 404

    def test_links_are_resolved_on_grab(self) -> None:
        plugin = SimpleNamespace(
            resolve_download=AsyncMock(return_value=["https://filer.net/folder/x"])
        )

        resp = _client(_job("nox"), plugin).get("/api/v1/download/job-1")

        assert resp.status_code == 200
        assert "text=https://filer.net/folder/x" in resp.text
        assert resp.headers["X-CrawlJob-Links"] == "1"
        plugin.resolve_download.assert_awaited_once_with(_PAGE)

    def test_failed_resolution_is_502(self) -> None:
        plugin = SimpleNamespace(resolve_download=AsyncMock(return_value=[]))

        resp = _client(_job("nox"), plugin).get("/api/v1/download/job-1")

        assert resp.status_code == 502
