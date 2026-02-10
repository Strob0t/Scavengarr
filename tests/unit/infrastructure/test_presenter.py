"""Tests for Torznab XML presenter."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from scavengarr.domain.entities import TorznabCaps, TorznabItem
from scavengarr.infrastructure.torznab.presenter import (
    render_caps_xml,
    render_rss_xml,
)

_TORZNAB_NS = "http://torznab.com/schemas/2015/feed"


class TestRenderCapsXml:
    def test_returns_xml_bytes(self) -> None:
        caps = TorznabCaps(
            server_title="scavengarr (test)",
            server_version="0.1.0",
        )
        rendered = render_caps_xml(caps)
        assert isinstance(rendered.payload, bytes)
        assert rendered.media_type == "application/xml"

    def test_xml_contains_server_element(self) -> None:
        caps = TorznabCaps(
            server_title="scavengarr (test)",
            server_version="0.1.0",
        )
        rendered = render_caps_xml(caps)
        root = ET.fromstring(rendered.payload)
        server = root.find("server")
        assert server is not None
        assert server.get("title") == "scavengarr (test)"
        assert server.get("version") == "0.1.0"

    def test_xml_contains_limits(self) -> None:
        caps = TorznabCaps(
            server_title="t",
            server_version="1.0",
            limits_max=100,
            limits_default=50,
        )
        rendered = render_caps_xml(caps)
        root = ET.fromstring(rendered.payload)
        limits = root.find("limits")
        assert limits is not None
        assert limits.get("max") == "100"
        assert limits.get("default") == "50"

    def test_xml_contains_categories(self) -> None:
        caps = TorznabCaps(server_title="t", server_version="1.0")
        rendered = render_caps_xml(caps)
        root = ET.fromstring(rendered.payload)
        categories = root.find("categories")
        assert categories is not None
        cat_ids = [c.get("id") for c in categories.findall("category")]
        assert "2000" in cat_ids
        assert "5000" in cat_ids
        assert "8000" in cat_ids

    def test_xml_contains_search_support(self) -> None:
        caps = TorznabCaps(server_title="t", server_version="1.0")
        rendered = render_caps_xml(caps)
        root = ET.fromstring(rendered.payload)
        searching = root.find("searching")
        assert searching is not None
        search = searching.find("search")
        assert search is not None
        assert search.get("available") == "yes"
        assert search.get("supportedParams") == "q"


class TestRenderRssXml:
    def test_empty_items(self) -> None:
        rendered = render_rss_xml(
            title="Test Feed",
            items=[],
            scavengarr_base_url="http://localhost:7979/",
        )
        root = ET.fromstring(rendered.payload)
        channel = root.find("channel")
        assert channel is not None
        assert channel.find("title").text == "Test Feed"
        assert len(channel.findall("item")) == 0

    def test_single_item_structure(self) -> None:
        item = TorznabItem(
            title="Test Movie",
            download_url="https://example.com/dl",
            job_id="abc-123",
            seeders=10,
            peers=5,
            size="4.5 GB",
            category=2000,
        )
        rendered = render_rss_xml(
            title="Feed",
            items=[item],
            scavengarr_base_url="http://localhost:7979/",
        )
        root = ET.fromstring(rendered.payload)
        channel = root.find("channel")
        xml_items = channel.findall("item")
        assert len(xml_items) == 1

    def test_item_title(self) -> None:
        item = TorznabItem(
            title="Test Movie",
            download_url="https://example.com/dl",
        )
        rendered = render_rss_xml(
            title="Feed",
            items=[item],
            scavengarr_base_url="http://localhost:7979/",
        )
        root = ET.fromstring(rendered.payload)
        xml_item = root.find("channel/item")
        assert xml_item.find("title").text == "Test Movie"

    def test_item_prefers_release_name_as_title(self) -> None:
        item = TorznabItem(
            title="Test Movie",
            download_url="https://example.com/dl",
            release_name="Test.Movie.2025.1080p",
        )
        rendered = render_rss_xml(
            title="Feed",
            items=[item],
            scavengarr_base_url="http://localhost:7979/",
        )
        root = ET.fromstring(rendered.payload)
        xml_item = root.find("channel/item")
        assert xml_item.find("title").text == "Test.Movie.2025.1080p"

    def test_crawljob_url_in_link(self) -> None:
        item = TorznabItem(
            title="T",
            download_url="https://example.com/dl",
            job_id="abc-123",
        )
        rendered = render_rss_xml(
            title="Feed",
            items=[item],
            scavengarr_base_url="http://localhost:7979/",
        )
        root = ET.fromstring(rendered.payload)
        xml_item = root.find("channel/item")
        link = xml_item.find("link").text
        assert link == "http://localhost:7979/api/v1/download/abc-123"

    def test_guid_uses_original_download_url(self) -> None:
        item = TorznabItem(
            title="T",
            download_url="https://original.com/dl",
            job_id="abc-123",
        )
        rendered = render_rss_xml(
            title="Feed",
            items=[item],
            scavengarr_base_url="http://localhost:7979/",
        )
        root = ET.fromstring(rendered.payload)
        xml_item = root.find("channel/item")
        guid = xml_item.find("guid")
        assert guid.text == "https://original.com/dl"
        assert guid.get("isPermaLink") == "false"

    def test_enclosure_type_is_crawljob(self) -> None:
        item = TorznabItem(
            title="T",
            download_url="https://example.com/dl",
            job_id="abc-123",
        )
        rendered = render_rss_xml(
            title="Feed",
            items=[item],
            scavengarr_base_url="http://localhost:7979/",
        )
        root = ET.fromstring(rendered.payload)
        xml_item = root.find("channel/item")
        enclosure = xml_item.find("enclosure")
        assert enclosure.get("type") == "application/x-crawljob"

    def test_torznab_attributes_present(self) -> None:
        item = TorznabItem(
            title="T",
            download_url="https://example.com/dl",
            seeders=10,
            peers=5,
            category=2000,
        )
        rendered = render_rss_xml(
            title="Feed",
            items=[item],
            scavengarr_base_url="http://localhost:7979/",
        )
        root = ET.fromstring(rendered.payload)
        xml_item = root.find("channel/item")
        attrs = xml_item.findall(f"{{{_TORZNAB_NS}}}attr")
        attr_names = [a.get("name") for a in attrs]
        assert "category" in attr_names
        assert "seeders" in attr_names
        assert "peers" in attr_names
        assert "size" in attr_names

    def test_multiple_items(self) -> None:
        items = [
            TorznabItem(title=f"Movie {i}", download_url=f"http://dl/{i}")
            for i in range(3)
        ]
        rendered = render_rss_xml(
            title="Feed",
            items=items,
            scavengarr_base_url="http://localhost:7979/",
        )
        root = ET.fromstring(rendered.payload)
        assert len(root.find("channel").findall("item")) == 3
