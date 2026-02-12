"""Shared fixtures for integration tests.

These tests use real infrastructure components (DiskcacheAdapter,
HttpLinkValidator, ScrapyAdapter, etc.) with mocked HTTP via respx.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from scavengarr.infrastructure.cache.diskcache_adapter import DiskcacheAdapter


@pytest.fixture()
def http_client() -> httpx.AsyncClient:
    """Real httpx.AsyncClient for use with respx mocking."""
    return httpx.AsyncClient()


@pytest.fixture()
async def diskcache(tmp_path: Path) -> DiskcacheAdapter:
    """Real DiskcacheAdapter backed by tmp_path (auto-cleaned)."""
    adapter = DiskcacheAdapter(
        directory=tmp_path / "cache",
        ttl_seconds=3600,
        max_concurrent=5,
    )
    async with adapter:
        yield adapter


@pytest.fixture()
def respx_mock() -> respx.MockRouter:
    """Explicit respx mock router for request interception."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture()
def fixtures_dir() -> Path:
    """Path to HTML fixtures directory."""
    return Path(__file__).parent.parent / "fixtures" / "html"
