"""The film and series plugins load their pages through the base helpers.

``_fetch_text()`` / ``_safe_fetch()`` apply the plugin's timeout and
User-Agent on the shared client, log uniformly and hand a Cloudflare
challenge to the browser fetcher; direct ``client.get()`` calls did none of
that.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase


def _load_plugin(name: str) -> Any:
    path = Path(__file__).resolve().parents[3] / "plugins" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_fetching_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.plugin


def _challenge(url: object, **_kwargs: object) -> MagicMock:
    resp = MagicMock(spec=httpx.Response, history=[])
    resp.status_code = 403
    resp.text = "<title>Just a moment...</title>"
    resp.headers = httpx.Headers()
    resp.url = httpx.URL(str(url))
    return resp


_CALLS: list[tuple[str, Callable[[Any], Awaitable[object]]]] = [
    ("streamcloud", lambda p: p._search_page("batman", 1)),
    ("streamkiste", lambda p: p._search_page("batman", 1)),
    ("hdfilme", lambda p: p._search_page("batman")),
    ("movie2k", lambda p: p._search_page("batman")),
    ("movie2k", lambda p: p._browse_pages("/movies", max_pages=1)),
    (
        "megakino",
        lambda p: p._scrape_detail({"url": f"{p.base_url}/films/1-batman.html"}),
    ),
    ("movie2k", lambda p: p._scrape_detail({"url": f"{p.base_url}/stream/x"})),
]


@pytest.mark.parametrize(("name", "call"), _CALLS)
async def test_challenged_page_is_loaded_through_the_browser(
    name: str,
    call: Callable[[Any], Awaitable[object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = AsyncMock()
    fetcher.fetch_text = AsyncMock(return_value="<html></html>")
    monkeypatch.setattr(HttpxPluginBase, "_browser_fetcher", fetcher)
    monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
    plugin = _load_plugin(name)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=_challenge)
    plugin._client = client
    plugin._domain_verified = True

    await call(plugin)

    fetcher.fetch_text.assert_awaited_once()
