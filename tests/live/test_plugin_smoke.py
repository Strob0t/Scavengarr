"""Live smoke tests for all 32 plugins.

Each test hits the real website and verifies the plugin can still scrape
valid results. Network errors and Cloudflare blocks are handled gracefully
via pytest.skip() — broken selectors or empty results cause a FAIL.

Run:
    poetry run pytest tests/live/ -v          # live tests only
    poetry run pytest                         # all tests incl. live
    poetry run pytest -m "not live"           # skip live tests
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.plugins.plugin_schema import YamlPluginDefinition
from scavengarr.infrastructure.plugins.registry import PluginRegistry

from .conftest import AUTH_ENV_VARS, chromium_available, has_auth

pytestmark = pytest.mark.live

# ---------------------------------------------------------------------------
# Plugin lists: (registry_name, search_query)
# ---------------------------------------------------------------------------

YAML_PLUGINS: list[tuple[str, str]] = [
    ("filmpalast", "Iron Man"),
    ("scnlog", "Iron Man"),
    ("warezomen", "Iron Man"),
]

HTTPX_PLUGINS: list[tuple[str, str]] = [
    ("aniworld", "Naruto"),
    ("burningseries", "Breaking Bad"),
    ("cine", "Iron Man"),
    ("dataload", "Iron Man"),
    ("einschalten", "Iron Man"),
    ("filmfans", "Iron Man"),
    ("fireani", "Naruto"),
    ("haschcon", "Iron Man"),
    ("hdfilme", "Iron Man"),
    ("kinoger", "Iron Man"),
    ("kinoking", "Iron Man"),
    ("kinox", "Iron Man"),
    ("megakino", "Iron Man"),
    ("megakino_to", "Iron Man"),
    ("movie4k", "Iron Man"),
    ("myboerse", "Iron Man"),
    ("nima4k", "Iron Man"),
    ("sto", "Breaking Bad"),
    ("streamcloud", "Iron Man"),
    ("streamkiste", "Iron Man"),
]

PLAYWRIGHT_PLUGINS: list[tuple[str, str]] = [
    ("animeloads", "Naruto"),
    ("boerse", "Iron Man"),
    ("byte", "Iron Man"),
    ("ddlspot", "Iron Man"),
    ("ddlvalley", "Iron Man"),
    ("moflix", "Iron Man"),
    ("mygully", "Iron Man"),
    ("scnsrc", "Iron Man"),
    ("streamworld", "Iron Man"),
]

# ---------------------------------------------------------------------------
# Network-level exceptions → pytest.skip (site unreachable, not plugin bug)
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

try:
    from playwright.async_api import Error as PlaywrightError

    _NETWORK_ERRORS = (*_NETWORK_ERRORS, PlaywrightError)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_cloudflare_block(exc: Exception) -> bool:
    """Heuristic: detect Cloudflare challenge blocks in error messages."""
    msg = str(exc).lower()
    return any(kw in msg for kw in ("cloudflare", "challenge", "cf-", "403"))


def _assert_results(results: list[SearchResult], plugin_name: str) -> None:
    """Assert that results are valid — FAIL if plugin appears broken."""
    assert len(results) > 0, (
        f"Plugin '{plugin_name}' returned 0 results — selectors may be broken "
        f"or the site changed its HTML structure."
    )
    for i, r in enumerate(results):
        assert r.title and r.title.strip(), (
            f"Plugin '{plugin_name}' result #{i} has empty title."
        )
        assert r.download_link and r.download_link.startswith("http"), (
            f"Plugin '{plugin_name}' result #{i} has invalid download_link: "
            f"{r.download_link!r}"
        )


# ---------------------------------------------------------------------------
# YAML plugin tests (3 plugins via HttpxScrapySearchEngine)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("plugin_name", "query"), YAML_PLUGINS)
async def test_yaml_plugin_smoke(
    plugin_name: str,
    query: str,
    plugin_registry: PluginRegistry,
    yaml_search_engine: Any,
) -> None:
    """YAML plugins: scrape via HttpxScrapySearchEngine."""
    plugin = plugin_registry.get(plugin_name)
    assert isinstance(plugin, YamlPluginDefinition), (
        f"Expected YAML plugin, got {type(plugin).__name__}"
    )

    try:
        results = await asyncio.wait_for(
            yaml_search_engine.search(plugin, query),
            timeout=60.0,
        )
    except _NETWORK_ERRORS:
        pytest.skip(f"Network error reaching {plugin_name} — site may be down.")
    except Exception as exc:
        if _is_cloudflare_block(exc):
            pytest.skip(f"Cloudflare block on {plugin_name}.")
        raise

    _assert_results(results, plugin_name)


# ---------------------------------------------------------------------------
# Httpx plugin tests (20 plugins via direct plugin.search())
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("plugin_name", "query"), HTTPX_PLUGINS)
async def test_httpx_plugin_smoke(
    plugin_name: str,
    query: str,
    plugin_registry: PluginRegistry,
) -> None:
    """Httpx plugins: direct plugin.search() call."""
    if not has_auth(plugin_name):
        pytest.skip(f"Missing credentials for {plugin_name}.")

    raw_plugin = plugin_registry.get(plugin_name)
    plugin = type(raw_plugin)()  # Fresh instance

    try:
        results = await asyncio.wait_for(
            plugin.search(query),
            timeout=60.0,
        )
    except _NETWORK_ERRORS:
        pytest.skip(f"Network error reaching {plugin_name} — site may be down.")
    except RuntimeError as exc:
        if "credentials" in str(exc).lower():
            pytest.skip(f"Missing credentials for {plugin_name}.")
        raise
    except Exception as exc:
        if _is_cloudflare_block(exc):
            pytest.skip(f"Cloudflare block on {plugin_name}.")
        raise
    finally:
        await plugin.cleanup()

    _assert_results(results, plugin_name)


# ---------------------------------------------------------------------------
# Playwright plugin tests (9 plugins via browser-based search)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("plugin_name", "query"), PLAYWRIGHT_PLUGINS)
async def test_playwright_plugin_smoke(
    plugin_name: str,
    query: str,
    plugin_registry: PluginRegistry,
) -> None:
    """Playwright plugins: browser-based search."""
    if not chromium_available():
        pytest.skip("Chromium not installed — skipping Playwright tests.")

    if not has_auth(plugin_name):
        pytest.skip(f"Missing credentials for {plugin_name}.")

    raw_plugin = plugin_registry.get(plugin_name)
    plugin = type(raw_plugin)()  # Fresh instance

    # Auth plugins get longer timeout
    timeout = 120.0 if plugin_name in AUTH_ENV_VARS else 90.0

    try:
        results = await asyncio.wait_for(
            plugin.search(query),
            timeout=timeout,
        )
    except _NETWORK_ERRORS:
        pytest.skip(f"Network error reaching {plugin_name} — site may be down.")
    except RuntimeError as exc:
        if "credentials" in str(exc).lower():
            pytest.skip(f"Missing credentials for {plugin_name}.")
        raise
    except Exception as exc:
        if _is_cloudflare_block(exc):
            pytest.skip(f"Cloudflare block on {plugin_name}.")
        raise
    finally:
        await plugin.cleanup()

    _assert_results(results, plugin_name)
