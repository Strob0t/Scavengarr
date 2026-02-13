"""Live contract tests for hoster resolvers.

Each test hits a real hoster URL and verifies the resolver correctly
determines whether the file is online or offline.  Network errors are
handled gracefully via pytest.skip().

Populate ``_LIVE_URLS`` and ``_DEAD_URLS`` with known URLs as they are
discovered.  Keep the dictionaries keyed by resolver name.

Run:
    poetry run pytest tests/live/test_resolver_live.py -v
    poetry run pytest -m live -v               # all live tests
    poetry run pytest -m "not live"             # skip live tests
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.xfs import (
    ALL_XFS_CONFIGS,
    XFSResolver,
)

pytestmark = pytest.mark.live

# ---------------------------------------------------------------------------
# Known live/dead URLs — populate as URLs are discovered
# ---------------------------------------------------------------------------

# resolver name -> URL expected to resolve successfully (file still online)
_LIVE_URLS: dict[str, str] = {}

# resolver name -> URL expected to return None (file offline/deleted)
_DEAD_URLS: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Network-level exceptions -> pytest.skip
# ---------------------------------------------------------------------------

_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.TimeoutException,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)


# ---------------------------------------------------------------------------
# XFS resolver live tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [c for c in ALL_XFS_CONFIGS if c.name in _LIVE_URLS],
    ids=[c.name for c in ALL_XFS_CONFIGS if c.name in _LIVE_URLS],
)
async def test_xfs_live_url_resolves(config) -> None:
    """Known-alive XFS URL should resolve to a ResolvedStream."""
    url = _LIVE_URLS[config.name]
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resolver = XFSResolver(config=config, http_client=client)
        try:
            result = await asyncio.wait_for(resolver.resolve(url), timeout=30.0)
        except _NETWORK_ERRORS:
            pytest.skip(f"Network error reaching {config.name} — site may be down.")
    assert result is not None, f"{config.name}: expected live URL to resolve: {url}"


@pytest.mark.parametrize(
    "config",
    [c for c in ALL_XFS_CONFIGS if c.name in _DEAD_URLS],
    ids=[c.name for c in ALL_XFS_CONFIGS if c.name in _DEAD_URLS],
)
async def test_xfs_dead_url_returns_none(config) -> None:
    """Known-dead XFS URL should return None (offline markers detected)."""
    url = _DEAD_URLS[config.name]
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resolver = XFSResolver(config=config, http_client=client)
        try:
            result = await asyncio.wait_for(resolver.resolve(url), timeout=30.0)
        except _NETWORK_ERRORS:
            pytest.skip(f"Network error reaching {config.name} — site may be down.")
    assert result is None, f"{config.name}: expected dead URL to return None: {url}"
