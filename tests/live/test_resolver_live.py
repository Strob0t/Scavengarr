"""Live contract tests for hoster resolvers.

One live case per resolver name in ``.cache/live/resolver-urls.json``
(``{resolver name: hoster page URL}``, written by
``scripts/probes/resolver_urls.py`` from the links the plugins find for the
titles of ``docs/plans/round-titles.txt``; untracked, the module skips without
it) and one dead case derived from it: the id in the URL's last path segment,
before any trailing extension (the fragment when the path has none: the UPN
Share players, ``/#<id>``), with its last four characters reversed (its
last six when four leave it unchanged). The altered id keeps the length and
alphabet of the real one, so the hoster parses it as an id and answers not
found or an error page, which the resolver reports as ``None``. An id of
digits only (fsst: small sequential numbers, so a reversed one is another
existing video) becomes zeros of the same length instead.

The registry is the one the composition root builds (the app's lifespan with a
temporary cache directory), one per module, so every resolver, the XFS and DDL
configs and the individual ones alike, is reached the way the server reaches
it. No URL reaches the test output: the parametrize ids are the resolver
names, the assertion messages name the resolver only, and the app's log (its
warnings carry the hoster URLs) is raised to errors for the module.

Run (dev container):
    xvfb-run -a poetry run pytest -m live tests/live/test_resolver_live.py
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
import pytest_asyncio
import structlog

from scavengarr.infrastructure.config.load import load_config
from scavengarr.infrastructure.hoster_resolvers.registry import HosterResolverRegistry
from scavengarr.interfaces.app import create_app
from scavengarr.interfaces.app_state import AppState

_REPO = Path(__file__).resolve().parents[2]
_URLS_FILE = _REPO / ".cache" / "live" / "resolver-urls.json"


def _live_urls() -> dict[str, str]:
    if not _URLS_FILE.exists():
        return {}
    data = json.loads(_URLS_FILE.read_text())
    return {str(name): str(url) for name, url in data.items()}


# resolver name -> hoster page URL the plugins found (file still online)
_LIVE_URLS: dict[str, str] = _live_urls()
_NAMES = sorted(_LIVE_URLS)

pytestmark = [
    pytest.mark.live,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not _LIVE_URLS,
        reason="no .cache/live/resolver-urls.json: scripts/probes/resolver_urls.py",
    ),
]

# Network-level exceptions -> pytest.skip (the registry answers None for
# most of them itself; these are the ones that escape it)
_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.TimeoutException,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)


def _altered(stem: str) -> str:
    """*stem* with its last four characters reversed, the last six when four
    leave it unchanged; digits only become zeros."""
    if stem.isdigit():
        return "0" * len(stem)
    altered = stem
    for n in (4, 6):
        altered = stem[:-n] + stem[-n:][::-1]
        if altered != stem:
            break
    return altered


def dead_url(url: str) -> str:
    """*url* with the id of its last path segment altered (``_altered``); a
    trailing extension (``.html``) stays. A URL without a path segment
    carries its id in the fragment (``/#<id>``), which is altered instead."""
    parts = urlsplit(url)
    if not parts.path.strip("/") and parts.fragment:
        return urlunsplit(parts._replace(fragment=_altered(parts.fragment)))
    path = parts.path.rstrip("/")
    slash = parts.path[len(path) :]
    head, _, segment = path.rpartition("/")
    stem, dot, ext = segment.rpartition(".")
    if not dot or not ext.isalpha() or len(ext) > 5:
        stem, dot, ext = segment, "", ""
    altered = _altered(stem)
    return urlunsplit(parts._replace(path=f"{head}/{altered}{dot}{ext}{slash}"))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def registry(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[HosterResolverRegistry]:
    """The server's hoster resolver registry, from the app's lifespan."""
    # The app's warnings name the hoster URLs; the test output must not
    # (restored afterwards: the unit tests import this module too)
    logging_before = structlog.get_config()
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR)
    )
    config = load_config(
        cli_overrides={
            "plugin_dir": _REPO / "plugins",
            "cache_dir": tmp_path_factory.mktemp("cache"),
            # no background plugin searches next to the tests
            "scoring": {"enabled": False},
        }
    )
    app = create_app(config)
    try:
        async with app.router.lifespan_context(app):
            state = cast(AppState, app.state)
            assert state.hoster_resolver_registry is not None
            yield state.hoster_resolver_registry
    finally:
        structlog.configure(**logging_before)


async def _resolve(registry: HosterResolverRegistry, name: str, url: str) -> object:
    try:
        return await asyncio.wait_for(registry.resolve(url), timeout=90.0)
    except _NETWORK_ERRORS:
        pytest.skip(f"{name}: network error reaching the hoster")


@pytest.mark.parametrize("name", _NAMES, ids=_NAMES)
async def test_live_url_resolves(registry: HosterResolverRegistry, name: str) -> None:
    """A link the plugins found today resolves to a stream."""
    # Booleans only: a stream's repr would print its video URL
    resolved = await _resolve(registry, name, _LIVE_URLS[name]) is not None
    assert resolved, f"{name}: the live link did not resolve"


@pytest.mark.parametrize("name", _NAMES, ids=_NAMES)
async def test_dead_url_returns_none(
    registry: HosterResolverRegistry, name: str
) -> None:
    """The same link with its id altered is reported dead (``None``)."""
    resolved = await _resolve(registry, name, dead_url(_LIVE_URLS[name])) is not None
    assert not resolved, f"{name}: an altered id resolved to a stream"
