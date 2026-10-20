"""Live smoke tests for plugins.

Each test hits the real website and verifies the plugin can still scrape
valid results. Network errors and Cloudflare blocks are handled gracefully
via pytest.skip() — broken selectors or empty results cause a FAIL.
An empty run after a fetch the base logged as failed (``<plugin>_timeout``,
``_fetch_error``, ``_http_error``: the base answers those with ``None``, the
plugin with ``[]``) skips as a slow or failing site, the line in the reason.

Run:
    poetry run pytest -m live -v               # live tests only
    poetry run pytest                         # default: live tests excluded
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import structlog

from scavengarr.domain.plugins.base import PluginUnreachableError, SearchResult
from scavengarr.infrastructure.plugins.registry import PluginRegistry

from .conftest import AUTH_ENV_VARS, chromium_available, has_auth
from .smoke_outcome import failed_fetch

pytestmark = pytest.mark.live

# ---------------------------------------------------------------------------
# Plugin lists: (registry_name, search_query)
# ---------------------------------------------------------------------------

HTTPX_PLUGINS: list[tuple[str, str]] = [
    ("aniworld", "Naruto"),
    ("burningseries", "Breaking Bad"),
    ("byte", "Iron Man"),
    ("cine", "Iron Man"),
    ("cineby", "Iron Man"),
    ("crawli", "Iron Man"),
    ("dataload", "Iron Man"),
    ("einschalten", "Iron Man"),
    ("filmfans", "Iron Man"),
    ("filmo", "Interstellar"),
    ("filmpalast", "Iron Man"),
    ("fireani", "Naruto"),
    ("haschcon", "Iron Man"),
    ("hdfilme", "Iron Man"),
    ("hdsource", "Iron Man"),
    ("hdworld", "Iron Man"),
    ("jjs", "Iron Man"),
    ("kinoger", "Iron Man"),
    ("kinoking", "Iron Man"),
    ("kinox", "Iron Man"),
    # The mediatheken keep films for weeks only; Tatort is always there
    ("mediathek", "Tatort"),
    ("megakino", "Iron Man"),
    ("megakino_to", "Iron Man"),
    ("moflix", "Iron Man"),
    ("movieblog", "Iron Man"),
    ("movie2k", "Iron Man"),
    ("movie4k", "Iron Man"),
    ("nox", "Iron Man"),
    ("scnlog", "Iron Man"),
    ("serienfans", "Breaking Bad"),
    ("serienjunkies", "Iron Man"),
    ("myboerse", "Iron Man"),
    ("nima4k", "Iron Man"),
    ("sto", "Breaking Bad"),
    ("streamcloud", "Iron Man"),
    ("streamkiste", "Iron Man"),
    ("warezomen", "Iron Man"),
]

PLAYWRIGHT_PLUGINS: list[tuple[str, str]] = [
    ("animeloads", "Naruto"),
    ("boerse", "Iron Man"),
    ("ddlspot", "Iron Man"),
    ("ddlvalley", "Iron Man"),
    ("mygully", "Iron Man"),
    ("scnsrc", "Iron Man"),
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
    # No domain answered the plugin's domain check: the site is down
    PluginUnreachableError,
)

try:
    from patchright.async_api import Error as PlaywrightError

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


def _skip_after_failed_fetch(
    results: list[SearchResult], events: list[structlog.typing.EventDict], plugin: str
) -> None:
    """Skip an empty run whose plugin logged a failed fetch: the site was slow
    or failing, the parser had nothing to read."""
    line = None if results else failed_fetch(events, plugin)
    if line is not None:
        pytest.skip(f"{plugin} found nothing after a failed fetch: {line}.")


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
# Httpx plugin tests (direct plugin.search())
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
        with structlog.testing.capture_logs() as events:
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

    _skip_after_failed_fetch(results, events, plugin_name)
    _assert_results(results, plugin_name)


# ---------------------------------------------------------------------------
# Playwright plugin tests (browser-based search)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("plugin_name", "query"), PLAYWRIGHT_PLUGINS)
async def test_playwright_plugin_smoke(
    plugin_name: str,
    query: str,
    plugin_registry: PluginRegistry,
) -> None:
    """Playwright plugins: browser-based search."""
    if not await chromium_available():
        pytest.skip("Chromium not installed — skipping Playwright tests.")

    if not has_auth(plugin_name):
        pytest.skip(f"Missing credentials for {plugin_name}.")

    raw_plugin = plugin_registry.get(plugin_name)
    plugin = type(raw_plugin)()  # Fresh instance

    # Auth plugins get longer timeout
    timeout = 120.0 if plugin_name in AUTH_ENV_VARS else 90.0

    try:
        with structlog.testing.capture_logs() as events:
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

    _skip_after_failed_fetch(results, events, plugin_name)
    _assert_results(results, plugin_name)
