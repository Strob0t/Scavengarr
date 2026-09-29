"""Live smoke test for grab-time link resolution (``GrabResolvingPlugin``).

One grab per plugin and run on purpose: nox counts every unlocked link
against an hourly/weekly quota, anime-loads needs one captcha per episode
(Playwright plugin: needs a display, run under ``xvfb-run -a``).

Usage::

    poetry run pytest tests/live/test_grab_resolve_live.py -v
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from scavengarr.domain.plugins import GrabResolvingPlugin, SearchResult
from scavengarr.infrastructure.plugins.registry import PluginRegistry

pytestmark = pytest.mark.live

_NETWORK_ERRORS = (httpx.ConnectError, httpx.TimeoutException, asyncio.TimeoutError)


def _grabbable(results: list[SearchResult]) -> SearchResult:
    """First result an anonymous grab can resolve (animeloads: short releases)."""
    for result in results:
        episodes = result.metadata.get("release_episodes")
        if episodes is None or 0 < int(episodes or 0) <= 13:
            return result
    return results[0]


@pytest.mark.parametrize(
    ("plugin_name", "query"), [("nox", "Iron Man"), ("animeloads", "One Punch Man")]
)
async def test_first_result_resolves_on_grab(
    plugin_name: str,
    query: str,
    plugin_registry: PluginRegistry,
) -> None:
    plugin = type(plugin_registry.get(plugin_name))()
    assert isinstance(plugin, GrabResolvingPlugin)
    try:
        results = await asyncio.wait_for(plugin.search(query), timeout=60.0)
        if not results:
            pytest.skip(f"{plugin_name} returned no results for {query!r}.")
        target = _grabbable(results)
        urls = await asyncio.wait_for(
            plugin.resolve_download(target.download_link), timeout=180.0
        )
    except _NETWORK_ERRORS:
        pytest.skip(f"Network error reaching {plugin_name}.")
    finally:
        await plugin.cleanup()

    assert urls, f"{plugin_name}: no links for {target.download_link}"
    assert all(u.startswith("http") for u in urls)
    assert not any(plugin_name in u for u in urls), urls  # hoster links, not pages
