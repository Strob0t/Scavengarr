"""Tests for ScrapyAdapter mirror/domain fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from bs4 import BeautifulSoup
from diskcache import Cache

from scavengarr.domain.plugins import (
    ScrapingConfig,
    ScrapingStage,
    StageSelectors,
    YamlPluginDefinition,
)
from scavengarr.infrastructure.scraping.scrapy_adapter import ScrapyAdapter


def _make_plugin(
    base_url: str = "https://filmpalast.to",
    mirror_urls: list[str] | None = None,
) -> YamlPluginDefinition:
    return YamlPluginDefinition(
        name="test-plugin",
        version="1.0.0",
        base_url=base_url,
        mirror_urls=mirror_urls,
        scraping=ScrapingConfig(
            mode="scrapy",
            stages=[
                ScrapingStage(
                    name="search",
                    type="list",
                    selectors=StageSelectors(link="a"),
                    url="/search",
                ),
            ],
        ),
    )


def _make_adapter(
    plugin: YamlPluginDefinition | None = None,
) -> ScrapyAdapter:
    plugin = plugin or _make_plugin()
    client = AsyncMock(spec=httpx.AsyncClient)
    cache = MagicMock(spec=Cache)
    return ScrapyAdapter(
        plugin=plugin,
        http_client=client,
        cache=cache,
        delay_seconds=0,
        max_retries=1,
    )


class TestReplaceomain:
    def test_swaps_scheme_and_netloc(self) -> None:
        result = ScrapyAdapter._replace_domain(
            "https://filmpalast.to/search?q=test",
            "https://filmpalast.sx",
        )
        assert result == "https://filmpalast.sx/search?q=test"

    def test_preserves_path_and_query(self) -> None:
        result = ScrapyAdapter._replace_domain(
            "https://old.example.com/a/b?x=1#frag",
            "http://new.example.org",
        )
        assert result == "http://new.example.org/a/b?x=1#frag"


class TestNoMirrors:
    @pytest.mark.asyncio
    async def test_no_mirrors_no_fallback(self) -> None:
        adapter = _make_adapter(_make_plugin(mirror_urls=None))
        adapter.client.get = AsyncMock(side_effect=httpx.ConnectError("down"))

        result = await adapter._fetch_page("https://filmpalast.to/search")
        assert result is None


class TestMirrorFallback:
    @pytest.mark.asyncio
    async def test_mirror_fallback_on_connect_error(self) -> None:
        adapter = _make_adapter(
            _make_plugin(mirror_urls=["https://filmpalast.sx"])
        )

        html = b"<html><body>OK</body></html>"
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.content = html
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        # Primary fails, mirror succeeds
        adapter.client.get = AsyncMock(
            side_effect=[httpx.ConnectError("down"), mock_response]
        )

        result = await adapter._fetch_page("https://filmpalast.to/search")

        assert result is not None
        assert isinstance(result, BeautifulSoup)
        assert adapter.base_url == "https://filmpalast.sx"

    @pytest.mark.asyncio
    async def test_all_mirrors_fail(self) -> None:
        adapter = _make_adapter(
            _make_plugin(
                mirror_urls=["https://filmpalast.sx", "https://filmpalast.im"]
            )
        )

        adapter.client.get = AsyncMock(side_effect=httpx.ConnectError("down"))

        result = await adapter._fetch_page("https://filmpalast.to/search")
        assert result is None

    @pytest.mark.asyncio
    async def test_mirror_skips_current_domain(self) -> None:
        adapter = _make_adapter(
            _make_plugin(
                mirror_urls=[
                    "https://filmpalast.to",  # same as base_url
                    "https://filmpalast.sx",
                ]
            )
        )

        html = b"<html><body>Mirror OK</body></html>"
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.content = html
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        # Primary fails, mirror succeeds
        adapter.client.get = AsyncMock(
            side_effect=[httpx.ConnectError("down"), mock_response]
        )

        result = await adapter._fetch_page("https://filmpalast.to/search")

        assert result is not None
        # Should have called get exactly twice: primary + filmpalast.sx
        # (skipping filmpalast.to in mirrors)
        assert adapter.client.get.call_count == 2
        assert adapter.base_url == "https://filmpalast.sx"


class TestSwitchDomain:
    def test_switch_domain_updates_all_stages(self) -> None:
        adapter = _make_adapter(
            _make_plugin(mirror_urls=["https://filmpalast.sx"])
        )

        assert adapter.base_url == "https://filmpalast.to"
        assert adapter.stages["search"].base_url == "https://filmpalast.to"

        adapter._switch_domain("https://filmpalast.sx")

        assert adapter.base_url == "https://filmpalast.sx"
        assert adapter.stages["search"].base_url == "https://filmpalast.sx"
