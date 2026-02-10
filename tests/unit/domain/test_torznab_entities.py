"""Tests for Torznab domain entities and exceptions."""

from __future__ import annotations

import pytest

from scavengarr.domain.entities import (
    TorznabBadRequest,
    TorznabCaps,
    TorznabError,
    TorznabExternalError,
    TorznabIndexInfo,
    TorznabItem,
    TorznabNoPluginsAvailable,
    TorznabPluginNotFound,
    TorznabQuery,
    TorznabUnsupportedAction,
    TorznabUnsupportedPlugin,
)


class TestTorznabQuery:
    def test_creation_with_required_fields(self) -> None:
        q = TorznabQuery(
            action="search", plugin_name="test", query="hello"
        )
        assert q.action == "search"
        assert q.plugin_name == "test"
        assert q.query == "hello"

    def test_optional_fields_default_to_none(self) -> None:
        q = TorznabQuery(
            action="search", plugin_name="test", query="hello"
        )
        assert q.category is None
        assert q.extended is None
        assert q.offset is None
        assert q.limit is None

    def test_frozen_immutability(self) -> None:
        q = TorznabQuery(
            action="search", plugin_name="test", query="hello"
        )
        with pytest.raises(AttributeError):
            q.query = "changed"  # type: ignore[misc]


class TestTorznabItem:
    def test_creation_with_required_fields(self) -> None:
        item = TorznabItem(
            title="Test Movie",
            download_url="https://example.com/dl",
        )
        assert item.title == "Test Movie"
        assert item.download_url == "https://example.com/dl"

    def test_default_values(self) -> None:
        item = TorznabItem(title="T", download_url="http://x")
        assert item.job_id is None
        assert item.seeders is None
        assert item.peers is None
        assert item.size is None
        assert item.category == 2000
        assert item.grabs == 0
        assert item.download_volume_factor == 0.0
        assert item.upload_volume_factor == 0.0

    def test_frozen_immutability(self) -> None:
        item = TorznabItem(title="T", download_url="http://x")
        with pytest.raises(AttributeError):
            item.title = "changed"  # type: ignore[misc]


class TestTorznabCaps:
    def test_default_limits(self) -> None:
        caps = TorznabCaps(
            server_title="scavengarr (test)",
            server_version="0.1.0",
        )
        assert caps.limits_max == 100
        assert caps.limits_default == 50

    def test_custom_limits(self) -> None:
        caps = TorznabCaps(
            server_title="t",
            server_version="1.0",
            limits_max=200,
            limits_default=25,
        )
        assert caps.limits_max == 200
        assert caps.limits_default == 25


class TestTorznabIndexInfo:
    def test_creation(self) -> None:
        info = TorznabIndexInfo(
            name="filmpalast", version="1.0.0", mode="scrapy"
        )
        assert info.name == "filmpalast"
        assert info.version == "1.0.0"
        assert info.mode == "scrapy"

    def test_nullable_fields(self) -> None:
        info = TorznabIndexInfo(name="test", version=None, mode=None)
        assert info.version is None
        assert info.mode is None


class TestTorznabExceptions:
    def test_all_inherit_from_torznab_error(self) -> None:
        exceptions = [
            TorznabBadRequest,
            TorznabUnsupportedAction,
            TorznabNoPluginsAvailable,
            TorznabPluginNotFound,
            TorznabUnsupportedPlugin,
            TorznabExternalError,
        ]
        for exc_class in exceptions:
            assert issubclass(exc_class, TorznabError)

    def test_exception_message(self) -> None:
        exc = TorznabBadRequest("missing query")
        assert str(exc) == "missing query"

    def test_raise_and_catch(self) -> None:
        with pytest.raises(TorznabPluginNotFound):
            raise TorznabPluginNotFound("unknown-plugin")
