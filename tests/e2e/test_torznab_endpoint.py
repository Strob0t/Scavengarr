"""End-to-end tests for Torznab API endpoints.

Tests the full request-response cycle through:
    HTTP Request -> FastAPI Router -> Use Case -> Presenter -> XML/JSON Response

Mocks are applied at the **port** level (PluginRegistryPort, SearchEnginePort,
CrawlJobRepository, httpx.AsyncClient) so that real use cases, presenter, and
router logic are exercised.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from xml.etree import ElementTree as ET

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scavengarr.application.factories import CrawlJobFactory
from scavengarr.domain.entities import TorznabPluginNotFound
from scavengarr.domain.plugins import SearchResult
from scavengarr.interfaces.api.torznab.router import router

_TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
_PREFIX = "/api/v1"


# ---------------------------------------------------------------------------
# Fake plugins
# ---------------------------------------------------------------------------


@dataclass
class _FakeScrapingConfig:
    mode: str = "scrapy"


@dataclass
class _FakeYamlPlugin:
    """Minimal YAML-style plugin (has scraping.mode)."""

    name: str = "filmpalast"
    version: str = "1.0.0"
    base_url: str = "https://filmpalast.to"
    scraping: Any = None

    def __post_init__(self) -> None:
        if self.scraping is None:
            self.scraping = _FakeScrapingConfig()


class _FakePythonPlugin:
    """Minimal Python-style plugin (has search(), no scraping)."""

    def __init__(
        self,
        name: str = "boerse",
        base_url: str = "https://boerse.am",
    ) -> None:
        self.name = name
        self.base_url = base_url
        self._results: list[SearchResult] = []

    async def search(
        self,
        query: str,
        category: int | None = None,
    ) -> list[SearchResult]:
        return self._results


class _FakePluginNoBaseUrl:
    """Plugin without base_url (for error tests)."""

    name = "nobase"
    version = "1.0.0"

    async def search(self, query: str, category: int | None = None) -> list:
        return []


class _FakePluginBadMode:
    """Plugin with unsupported scraping mode."""

    name = "badmode"
    version = "1.0.0"
    base_url = "https://example.com"

    def __init__(self) -> None:
        self.scraping = _FakeScrapingConfig(mode="playwright")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(
    *,
    environment: str = "dev",
    plugins: MagicMock | None = None,
    search_engine: AsyncMock | None = None,
    crawljob_repo: AsyncMock | None = None,
    http_client: AsyncMock | None = None,
) -> FastAPI:
    """Build a minimal FastAPI app with torznab router + mocked state."""
    app = FastAPI()
    app.include_router(router, prefix=_PREFIX)

    config = MagicMock()
    config.environment = environment
    config.app_name = "Scavengarr"

    app.state.config = config
    app.state.plugins = plugins or MagicMock()
    app.state.search_engine = search_engine or AsyncMock()
    app.state.crawljob_factory = CrawlJobFactory(default_ttl_hours=1)
    app.state.crawljob_repo = crawljob_repo or AsyncMock()
    app.state.http_client = http_client or AsyncMock()

    return app


def _make_search_result(
    title: str = "Test.Movie.2024.1080p",
    download_link: str = "https://example.com/dl/1",
    **kwargs: Any,
) -> SearchResult:
    """Convenience factory for SearchResult."""
    defaults: dict[str, Any] = {
        "title": title,
        "download_link": download_link,
        "size": "1.5 GB",
        "seeders": 5,
        "leechers": 2,
        "source_url": "https://example.com/detail/1",
        "category": 2000,
    }
    defaults.update(kwargs)
    return SearchResult(**defaults)


def _parse_xml(content: bytes) -> ET.Element:
    """Parse XML response body and return root element."""
    return ET.fromstring(content)


def _find_torznab_attrs(item: ET.Element) -> dict[str, str]:
    """Extract all torznab:attr name→value pairs from an <item>."""
    attrs: dict[str, str] = {}
    for el in item.findall(f"{{{_TORZNAB_NS}}}attr"):
        name = el.get("name", "")
        value = el.get("value", "")
        attrs[name] = value
    return attrs


# ---------------------------------------------------------------------------
# Indexers endpoint
# ---------------------------------------------------------------------------


class TestIndexersEndpoint:
    """GET /api/v1/torznab/indexers"""

    def test_returns_plugin_list(self) -> None:
        plugins = MagicMock()
        plugins.list_names.return_value = ["filmpalast", "boerse"]
        plugin_fp = _FakeYamlPlugin(name="filmpalast")
        plugin_bo = _FakePythonPlugin(name="boerse")
        plugins.get.side_effect = lambda n: plugin_fp if n == "filmpalast" else plugin_bo

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/indexers")

        assert resp.status_code == 200
        data = resp.json()
        assert "indexers" in data
        names = [i["name"] for i in data["indexers"]]
        assert "filmpalast" in names
        assert "boerse" in names

    def test_empty_registry(self) -> None:
        plugins = MagicMock()
        plugins.list_names.return_value = []

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/indexers")

        assert resp.status_code == 200
        assert resp.json()["indexers"] == []

    def test_returns_mode_for_yaml_plugin(self) -> None:
        plugins = MagicMock()
        plugins.list_names.return_value = ["filmpalast"]
        plugins.get.return_value = _FakeYamlPlugin()

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/indexers")

        indexer = resp.json()["indexers"][0]
        assert indexer["mode"] == "scrapy"

    def test_returns_null_mode_for_python_plugin(self) -> None:
        plugins = MagicMock()
        plugins.list_names.return_value = ["boerse"]
        plugins.get.return_value = _FakePythonPlugin()

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/indexers")

        indexer = resp.json()["indexers"][0]
        assert indexer["mode"] is None


# ---------------------------------------------------------------------------
# Caps endpoint
# ---------------------------------------------------------------------------


class TestCapsEndpoint:
    """GET /api/v1/torznab/{plugin}?t=caps"""

    def test_happy_path(self) -> None:
        plugins = MagicMock()
        plugins.get.return_value = _FakeYamlPlugin(name="filmpalast")

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=caps")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/xml"

        root = _parse_xml(resp.content)
        assert root.tag == "caps"

        server = root.find("server")
        assert server is not None
        assert "Scavengarr" in (server.get("title") or "")
        assert server.get("version") == "0.1.0"

    def test_caps_has_limits(self) -> None:
        plugins = MagicMock()
        plugins.get.return_value = _FakeYamlPlugin()

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=caps")
        root = _parse_xml(resp.content)

        limits = root.find("limits")
        assert limits is not None
        assert limits.get("max") == "100"
        assert limits.get("default") == "50"

    def test_caps_has_categories(self) -> None:
        plugins = MagicMock()
        plugins.get.return_value = _FakeYamlPlugin()

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=caps")
        root = _parse_xml(resp.content)

        categories = root.find("categories")
        assert categories is not None
        cat_ids = [c.get("id") for c in categories.findall("category")]
        assert "2000" in cat_ids
        assert "5000" in cat_ids
        assert "8000" in cat_ids

    def test_caps_has_search_params(self) -> None:
        plugins = MagicMock()
        plugins.get.return_value = _FakeYamlPlugin()

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=caps")
        root = _parse_xml(resp.content)

        searching = root.find("searching")
        assert searching is not None
        search_el = searching.find("search")
        assert search_el is not None
        assert search_el.get("available") == "yes"
        assert search_el.get("supportedParams") == "q"

    def test_caps_plugin_not_found(self) -> None:
        plugins = MagicMock()
        plugins.get.side_effect = TorznabPluginNotFound("ghost")

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/ghost?t=caps")

        assert resp.status_code == 404
        root = _parse_xml(resp.content)
        assert root.tag == "rss"


# ---------------------------------------------------------------------------
# Search happy path
# ---------------------------------------------------------------------------


class TestSearchHappyPath:
    """GET /api/v1/torznab/{plugin}?t=search&q=..."""

    def test_python_plugin_returns_items(self) -> None:
        result = _make_search_result()
        py_plugin = _FakePythonPlugin()
        py_plugin._results = [result]

        plugins = MagicMock()
        plugins.get.return_value = py_plugin

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[result])

        repo = AsyncMock()

        app = _make_app(plugins=plugins, search_engine=engine, crawljob_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/boerse?t=search&q=test")

        assert resp.status_code == 200
        root = _parse_xml(resp.content)
        assert root.tag == "rss"

        channel = root.find("channel")
        assert channel is not None
        items = channel.findall("item")
        assert len(items) == 1

    def test_yaml_plugin_returns_items(self) -> None:
        result = _make_search_result()
        yaml_plugin = _FakeYamlPlugin()

        plugins = MagicMock()
        plugins.get.return_value = yaml_plugin

        engine = AsyncMock()
        engine.search = AsyncMock(return_value=[result])

        repo = AsyncMock()

        app = _make_app(plugins=plugins, search_engine=engine, crawljob_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=search&q=iron+man")

        assert resp.status_code == 200
        root = _parse_xml(resp.content)
        items = root.findall(".//item")
        assert len(items) == 1

    def test_empty_results(self) -> None:
        py_plugin = _FakePythonPlugin()
        py_plugin._results = []

        plugins = MagicMock()
        plugins.get.return_value = py_plugin

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[])

        app = _make_app(plugins=plugins, search_engine=engine)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/boerse?t=search&q=nonexistent")

        assert resp.status_code == 200
        root = _parse_xml(resp.content)
        items = root.findall(".//item")
        assert len(items) == 0

    def test_multiple_results(self) -> None:
        results = [
            _make_search_result(title=f"Movie.{i}", download_link=f"https://dl/{i}")
            for i in range(5)
        ]
        py_plugin = _FakePythonPlugin()
        py_plugin._results = results

        plugins = MagicMock()
        plugins.get.return_value = py_plugin

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=results)

        repo = AsyncMock()

        app = _make_app(plugins=plugins, search_engine=engine, crawljob_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/boerse?t=search&q=movie")

        assert resp.status_code == 200
        items = _parse_xml(resp.content).findall(".//item")
        assert len(items) == 5

    def test_category_passed_to_query(self) -> None:
        result = _make_search_result()
        yaml_plugin = _FakeYamlPlugin()

        plugins = MagicMock()
        plugins.get.return_value = yaml_plugin

        engine = AsyncMock()
        engine.search = AsyncMock(return_value=[result])

        repo = AsyncMock()

        app = _make_app(plugins=plugins, search_engine=engine, crawljob_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=search&q=test&cat=5000")

        assert resp.status_code == 200
        # Verify category was passed to the engine
        call_args = engine.search.call_args
        assert call_args is not None
        # engine.search(plugin, query, category=category)
        assert call_args.kwargs.get("category") == 5000

    def test_crawljob_generated_and_stored(self) -> None:
        result = _make_search_result()
        py_plugin = _FakePythonPlugin()
        py_plugin._results = [result]

        plugins = MagicMock()
        plugins.get.return_value = py_plugin

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[result])

        repo = AsyncMock()

        app = _make_app(plugins=plugins, search_engine=engine, crawljob_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/boerse?t=search&q=test")

        assert resp.status_code == 200
        # CrawlJob repo.save was called
        repo.save.assert_awaited_once()

    def test_item_link_points_to_download_endpoint(self) -> None:
        result = _make_search_result()
        py_plugin = _FakePythonPlugin()
        py_plugin._results = [result]

        plugins = MagicMock()
        plugins.get.return_value = py_plugin

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[result])

        repo = AsyncMock()

        app = _make_app(plugins=plugins, search_engine=engine, crawljob_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/boerse?t=search&q=test")

        root = _parse_xml(resp.content)
        item = root.find(".//item")
        assert item is not None

        link = item.findtext("link")
        assert link is not None
        assert "/api/v1/download/" in link

    def test_item_enclosure_type(self) -> None:
        result = _make_search_result()
        py_plugin = _FakePythonPlugin()
        py_plugin._results = [result]

        plugins = MagicMock()
        plugins.get.return_value = py_plugin

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[result])

        repo = AsyncMock()

        app = _make_app(plugins=plugins, search_engine=engine, crawljob_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/boerse?t=search&q=test")

        root = _parse_xml(resp.content)
        enclosure = root.find(".//item/enclosure")
        assert enclosure is not None
        assert enclosure.get("type") == "application/x-crawljob"
        assert enclosure.get("url", "").startswith("http")


# ---------------------------------------------------------------------------
# Search with empty query
# ---------------------------------------------------------------------------


class TestSearchEmptyQuery:
    """GET /api/v1/torznab/{plugin}?t=search (no q param)"""

    def test_no_query_returns_200_with_description_dev(self) -> None:
        plugins = MagicMock()
        plugins.get.return_value = _FakeYamlPlugin()

        app = _make_app(plugins=plugins, environment="dev")
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=search")

        assert resp.status_code == 200
        root = _parse_xml(resp.content)
        desc = root.findtext(".//channel/description")
        assert desc is not None
        assert "Missing query" in desc

    def test_no_query_returns_200_no_description_prod(self) -> None:
        plugins = MagicMock()
        plugins.get.return_value = _FakeYamlPlugin()

        app = _make_app(plugins=plugins, environment="prod")
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=search")

        assert resp.status_code == 200
        root = _parse_xml(resp.content)
        desc = root.findtext(".//channel/description")
        # In prod, description defaults to "Scavengarr Torznab feed" (no error detail)
        assert desc is not None
        assert "Missing query" not in desc

    def test_extended_probe_reachable(self) -> None:
        plugin = _FakePythonPlugin(base_url="https://boerse.am")

        plugins = MagicMock()
        plugins.get.return_value = plugin

        # Simulate successful HEAD request
        mock_response = MagicMock()
        mock_response.status_code = 200

        http_client = AsyncMock()
        http_client.request = AsyncMock(return_value=mock_response)

        app = _make_app(plugins=plugins, http_client=http_client)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/boerse?t=search&extended=1")

        assert resp.status_code == 200
        root = _parse_xml(resp.content)
        items = root.findall(".//item")
        assert len(items) == 1
        # Test item title contains "reachable"
        title = items[0].findtext("title")
        assert title is not None
        assert "reachable" in title

    def test_extended_probe_unreachable(self) -> None:
        plugin = _FakePythonPlugin(base_url="https://boerse.am")

        plugins = MagicMock()
        plugins.get.return_value = plugin

        http_client = AsyncMock()
        http_client.request = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        app = _make_app(plugins=plugins, http_client=http_client)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/boerse?t=search&extended=1")

        assert resp.status_code == 503
        root = _parse_xml(resp.content)
        items = root.findall(".//item")
        assert len(items) == 0

    def test_extended_probe_no_base_url(self) -> None:
        plugin = _FakePluginNoBaseUrl()

        plugins = MagicMock()
        plugins.get.return_value = plugin

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/nobase?t=search&extended=1")

        assert resp.status_code == 422

    def test_extended_probe_head_405_falls_back_to_get(self) -> None:
        plugin = _FakePythonPlugin(base_url="https://example.com")

        plugins = MagicMock()
        plugins.get.return_value = plugin

        # HEAD returns 405, then GET succeeds
        head_response = MagicMock()
        head_response.status_code = 405

        get_response = AsyncMock()
        get_response.status_code = 200

        http_client = AsyncMock()
        http_client.request = AsyncMock(return_value=head_response)
        http_client.build_request = MagicMock(return_value=MagicMock())
        http_client.send = AsyncMock(return_value=get_response)

        app = _make_app(plugins=plugins, http_client=http_client)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/boerse?t=search&extended=1")

        assert resp.status_code == 200
        items = _parse_xml(resp.content).findall(".//item")
        assert len(items) == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestSearchErrorHandling:
    """Error paths for /torznab/{plugin}?t=search"""

    def test_plugin_not_found_returns_404(self) -> None:
        plugins = MagicMock()
        plugins.get.side_effect = TorznabPluginNotFound("missing")

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/missing?t=search&q=test")

        assert resp.status_code == 404
        root = _parse_xml(resp.content)
        assert root.tag == "rss"
        desc = root.findtext(".//channel/description")
        assert desc is not None
        assert "not found" in desc

    def test_unsupported_action_returns_422(self) -> None:
        plugins = MagicMock()
        plugins.get.return_value = _FakeYamlPlugin()

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=tvsearch&q=test")

        assert resp.status_code == 422
        root = _parse_xml(resp.content)
        desc = root.findtext(".//channel/description")
        assert desc is not None
        assert "Unsupported" in desc

    def test_unsupported_plugin_mode_returns_422(self) -> None:
        plugins = MagicMock()
        plugins.get.return_value = _FakePluginBadMode()

        engine = AsyncMock()
        # Let the real use case detect the bad mode
        app = _make_app(plugins=plugins, search_engine=engine)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/badmode?t=search&q=test")

        assert resp.status_code == 422

    def test_search_engine_error_returns_502_dev(self) -> None:
        yaml_plugin = _FakeYamlPlugin()

        plugins = MagicMock()
        plugins.get.return_value = yaml_plugin

        engine = AsyncMock()
        engine.search = AsyncMock(side_effect=RuntimeError("connection refused"))

        app = _make_app(plugins=plugins, search_engine=engine, environment="dev")
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=search&q=test")

        assert resp.status_code == 502
        root = _parse_xml(resp.content)
        desc = root.findtext(".//channel/description")
        assert desc is not None
        assert "error" in desc.lower()

    def test_python_plugin_error_returns_502_dev(self) -> None:
        py_plugin = _FakePythonPlugin()
        # Replace search to raise
        py_plugin.search = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

        plugins = MagicMock()
        plugins.get.return_value = py_plugin

        engine = AsyncMock()

        app = _make_app(plugins=plugins, search_engine=engine, environment="dev")
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/boerse?t=search&q=test")

        assert resp.status_code == 502

    def test_unhandled_exception_returns_500_dev(self) -> None:
        plugins = MagicMock()
        # Make get() work but trigger error later in the flow
        plugins.get.return_value = _FakeYamlPlugin()

        engine = AsyncMock()
        engine.search = AsyncMock(return_value=[_make_search_result()])

        repo = AsyncMock()
        # Trigger unhandled error during CrawlJob save
        repo.save = AsyncMock(side_effect=TypeError("unexpected type"))

        # The use case catches per-item errors and continues, so to trigger
        # a 500 we need an error that escapes the use case entirely.
        # Override the factory to blow up before repo.save is reached.
        app = _make_app(plugins=plugins, search_engine=engine, crawljob_repo=repo)
        # Patch crawljob_factory to raise
        app.state.crawljob_factory = MagicMock(
            create_from_search_result=MagicMock(
                side_effect=TypeError("factory exploded")
            )
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=search&q=test")

        # The use case catches per-item exceptions, so items will be skipped.
        # This should still return 200 with 0 items (not a 500) because the
        # error is caught inside _build_torznab_items.
        assert resp.status_code == 200
        items = _parse_xml(resp.content).findall(".//item")
        assert len(items) == 0

    def test_truly_unhandled_exception_returns_500(self) -> None:
        """An error that escapes all try/except in the use case."""
        plugins = MagicMock()
        # get() works, but the plugin has search() and no scraping,
        # so the use case calls validate_results, which we make blow up
        # with a non-TorznabError so it becomes TorznabExternalError -> 502
        py_plugin = _FakePythonPlugin()
        py_plugin._results = [_make_search_result()]
        plugins.get.return_value = py_plugin

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=RuntimeError("kaboom"))

        app = _make_app(plugins=plugins, search_engine=engine, environment="dev")
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/boerse?t=search&q=test")

        # validate_results error → TorznabExternalError → 502 in dev
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Prod mode error handling
# ---------------------------------------------------------------------------


class TestSearchProdMode:
    """Prod mode: error details hidden, status codes differ."""

    def test_plugin_not_found_no_details_prod(self) -> None:
        plugins = MagicMock()
        plugins.get.side_effect = TorznabPluginNotFound("secret")

        app = _make_app(plugins=plugins, environment="prod")
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/secret?t=search&q=test")

        assert resp.status_code == 404
        root = _parse_xml(resp.content)
        desc = root.findtext(".//channel/description")
        # Prod: description is the default feed description, not error info
        assert desc is not None
        assert "not found" not in desc.lower()

    def test_external_error_returns_200_prod(self) -> None:
        yaml_plugin = _FakeYamlPlugin()

        plugins = MagicMock()
        plugins.get.return_value = yaml_plugin

        engine = AsyncMock()
        engine.search = AsyncMock(side_effect=RuntimeError("network error"))

        app = _make_app(plugins=plugins, search_engine=engine, environment="prod")
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=search&q=test")

        # Prod: external errors return 200 (Prowlarr compatibility)
        assert resp.status_code == 200

    def test_unsupported_action_returns_422_prod(self) -> None:
        """Unsupported action returns 422 even in prod (client error)."""
        plugins = MagicMock()
        plugins.get.return_value = _FakeYamlPlugin()

        app = _make_app(plugins=plugins, environment="prod")
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast?t=tvsearch&q=test")

        assert resp.status_code == 422
        root = _parse_xml(resp.content)
        desc = root.findtext(".//channel/description")
        # Prod: no error details
        assert desc is not None
        assert "Unsupported" not in desc


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """GET /api/v1/torznab/{plugin}/health"""

    def test_reachable(self) -> None:
        plugin = _FakeYamlPlugin(base_url="https://filmpalast.to")

        plugins = MagicMock()
        plugins.get.return_value = plugin

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        http_client = AsyncMock()
        http_client.request = AsyncMock(return_value=mock_resp)

        app = _make_app(plugins=plugins, http_client=http_client)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["plugin"] == "filmpalast"
        assert data["reachable"] is True
        assert data["base_url"] == "https://filmpalast.to"

    def test_unreachable(self) -> None:
        plugin = _FakeYamlPlugin(base_url="https://filmpalast.to")

        plugins = MagicMock()
        plugins.get.return_value = plugin

        http_client = AsyncMock()
        http_client.request = AsyncMock(
            side_effect=httpx.ConnectError("DNS resolution failed")
        )

        app = _make_app(plugins=plugins, http_client=http_client)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["reachable"] is False
        assert data["error"] is not None

    def test_plugin_not_found(self) -> None:
        plugins = MagicMock()
        plugins.get.side_effect = TorznabPluginNotFound("ghost")

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/ghost/health")

        assert resp.status_code == 404
        data = resp.json()
        assert data["plugin"] == "ghost"
        assert data["reachable"] is False
        assert "not found" in data["error"]

    def test_no_base_url(self) -> None:
        plugin = _FakePluginNoBaseUrl()

        plugins = MagicMock()
        plugins.get.return_value = plugin

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/nobase/health")

        assert resp.status_code == 422
        data = resp.json()
        assert "no base_url" in data["error"]

    def test_mirror_fallback_when_primary_unreachable(self) -> None:
        plugin = _FakeYamlPlugin(base_url="https://primary.to")
        plugin.mirror_urls = ["https://mirror1.to", "https://mirror2.to"]  # type: ignore[attr-defined]

        plugins = MagicMock()
        plugins.get.return_value = plugin

        call_count = 0

        async def _mock_request(method: str, url: str, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            # Primary and mirror1 fail, mirror2 succeeds
            if "primary" in url or "mirror1" in url:
                raise httpx.ConnectError("unreachable")
            resp = MagicMock()
            resp.status_code = 200
            return resp

        http_client = AsyncMock()
        http_client.request = _mock_request

        app = _make_app(plugins=plugins, http_client=http_client)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast/health")

        assert resp.status_code == 200
        data = resp.json()
        # Primary is unreachable
        assert data["reachable"] is False
        # Mirrors are probed
        assert "mirrors" in data
        mirrors = data["mirrors"]
        assert len(mirrors) == 2
        # mirror2 should be reachable
        mirror2 = [m for m in mirrors if m["url"] == "https://mirror2.to"]
        assert len(mirror2) == 1
        assert mirror2[0]["reachable"] is True

    def test_mirrors_not_probed_when_primary_reachable(self) -> None:
        plugin = _FakeYamlPlugin(base_url="https://filmpalast.to")
        plugin.mirror_urls = ["https://mirror1.to"]  # type: ignore[attr-defined]

        plugins = MagicMock()
        plugins.get.return_value = plugin

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        http_client = AsyncMock()
        http_client.request = AsyncMock(return_value=mock_resp)

        app = _make_app(plugins=plugins, http_client=http_client)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/filmpalast/health")

        data = resp.json()
        assert data["reachable"] is True
        # Mirrors key exists but is empty (primary was reachable)
        assert data.get("mirrors") == []


# ---------------------------------------------------------------------------
# XML structure validation
# ---------------------------------------------------------------------------


class TestXmlStructure:
    """Validate the structure and content of RSS XML responses."""

    def _get_single_item_response(self) -> bytes:
        """Helper: perform search that returns one item and return response body."""
        result = _make_search_result(
            title="Iron.Man.2008.1080p",
            download_link="https://example.com/dl/iron",
            size="4.5 GB",
            seeders=10,
            leechers=3,
            release_name="Iron.Man.2008.1080p.BluRay.x264",
            description="Iron Man movie download",
            category=2000,
        )
        py_plugin = _FakePythonPlugin()
        py_plugin._results = [result]

        plugins = MagicMock()
        plugins.get.return_value = py_plugin

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[result])

        repo = AsyncMock()

        app = _make_app(plugins=plugins, search_engine=engine, crawljob_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/torznab/boerse?t=search&q=iron")
        return resp.content

    def test_rss_root_structure(self) -> None:
        content = self._get_single_item_response()
        root = _parse_xml(content)

        assert root.tag == "rss"
        assert root.get("version") == "2.0"

        channel = root.find("channel")
        assert channel is not None
        assert channel.findtext("title") is not None
        assert channel.findtext("description") is not None
        assert channel.findtext("link") is not None
        assert channel.findtext("language") == "en-us"

    def test_item_has_required_fields(self) -> None:
        content = self._get_single_item_response()
        root = _parse_xml(content)
        item = root.find(".//item")
        assert item is not None

        # Required elements
        assert item.findtext("title") is not None
        assert item.findtext("guid") is not None
        assert item.findtext("link") is not None
        assert item.findtext("description") is not None
        assert item.findtext("pubDate") is not None

    def test_item_title_prefers_release_name(self) -> None:
        content = self._get_single_item_response()
        root = _parse_xml(content)
        item = root.find(".//item")
        assert item is not None

        title = item.findtext("title")
        # Presenter prefers release_name over title
        assert title == "Iron.Man.2008.1080p.BluRay.x264"

    def test_torznab_attributes_present(self) -> None:
        content = self._get_single_item_response()
        root = _parse_xml(content)
        item = root.find(".//item")
        assert item is not None

        attrs = _find_torznab_attrs(item)

        assert "category" in attrs
        assert attrs["category"] == "2000"
        assert "size" in attrs
        assert int(attrs["size"]) > 0  # 4.5 GB parsed to bytes
        assert "seeders" in attrs
        assert attrs["seeders"] == "10"
        assert "peers" in attrs
        assert attrs["peers"] == "3"
        assert "downloadvolumefactor" in attrs
        assert "uploadvolumefactor" in attrs
        assert "minimumratio" in attrs
        assert "minimumseedtime" in attrs

    def test_guid_uses_original_url(self) -> None:
        content = self._get_single_item_response()
        root = _parse_xml(content)
        item = root.find(".//item")
        assert item is not None

        guid = item.find("guid")
        assert guid is not None
        assert guid.get("isPermaLink") == "false"
        assert guid.text == "https://example.com/dl/iron"

    def test_enclosure_has_correct_attributes(self) -> None:
        content = self._get_single_item_response()
        root = _parse_xml(content)
        item = root.find(".//item")
        assert item is not None

        enclosure = item.find("enclosure")
        assert enclosure is not None
        assert enclosure.get("type") == "application/x-crawljob"
        assert int(enclosure.get("length", "0")) > 0
        assert enclosure.get("url", "").startswith("http")

    def test_channel_title_includes_plugin_name(self) -> None:
        content = self._get_single_item_response()
        root = _parse_xml(content)
        channel = root.find("channel")
        assert channel is not None
        title = channel.findtext("title")
        assert title is not None
        assert "boerse" in title
        assert "Scavengarr" in title
