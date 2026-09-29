"""Live smoke test for grab-time link resolution (``GrabResolvingPlugin``).

One grab per run on purpose: every unlocked link counts against the site's
download quota (nox: hourly/weekly limit for anonymous users).

Usage::

    poetry run pytest tests/live/test_grab_resolve_live.py -v
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from scavengarr.domain.plugins import GrabResolvingPlugin
from scavengarr.infrastructure.plugins.registry import PluginRegistry

pytestmark = pytest.mark.live

_NETWORK_ERRORS = (httpx.ConnectError, httpx.TimeoutException, asyncio.TimeoutError)


@pytest.mark.parametrize(("plugin_name", "query"), [("nox", "Iron Man")])
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
        urls = await asyncio.wait_for(
            plugin.resolve_download(results[0].download_link), timeout=60.0
        )
    except _NETWORK_ERRORS:
        pytest.skip(f"Network error reaching {plugin_name}.")
    finally:
        await plugin.cleanup()

    assert urls, f"{plugin_name}: no links for {results[0].download_link}"
    assert all(u.startswith("http") for u in urls)
    assert not any(plugin_name in u for u in urls), urls  # hoster links, not pages
