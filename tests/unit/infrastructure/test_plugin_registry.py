"""Tests for PluginRegistry.get_by_provides()."""

from __future__ import annotations

from pathlib import Path

import pytest

from scavengarr.infrastructure.plugins.registry import PluginRegistry


@pytest.fixture()
def registry(tmp_path: Path) -> PluginRegistry:
    """Create a registry pointing at a temp plugin directory."""
    return PluginRegistry(tmp_path)


class TestGetByProvides:
    """Tests for get_by_provides filtering."""

    def test_yaml_download_default(
        self, registry: PluginRegistry, tmp_path: Path
    ) -> None:
        """YAML plugin without provides field defaults to 'download'."""
        yaml_content = """\
name: "test-dl"
version: "1.0.0"
base_url: "https://example.com"
scraping:
  mode: "scrapy"
  stages:
    - name: "search"
      type: "list"
      url: "/search"
      selectors:
        link: "a"
"""
        (tmp_path / "test-dl.yaml").write_text(yaml_content)

        names = registry.get_by_provides("download")

        assert "test-dl" in names

    def test_yaml_stream_explicit(
        self, registry: PluginRegistry, tmp_path: Path
    ) -> None:
        """YAML plugin with provides='stream' is returned for stream queries."""
        yaml_content = """\
name: "test-stream"
version: "1.0.0"
base_url: "https://example.com"
provides: "stream"
scraping:
  mode: "scrapy"
  stages:
    - name: "search"
      type: "list"
      url: "/search"
      selectors:
        link: "a"
"""
        (tmp_path / "test-stream.yaml").write_text(yaml_content)

        assert "test-stream" in registry.get_by_provides("stream")
        assert "test-stream" not in registry.get_by_provides("download")

    def test_yaml_both_returned_for_either(
        self, registry: PluginRegistry, tmp_path: Path
    ) -> None:
        """YAML plugin with provides='both' is returned for both stream and download."""
        yaml_content = """\
name: "test-both"
version: "1.0.0"
base_url: "https://example.com"
provides: "both"
scraping:
  mode: "scrapy"
  stages:
    - name: "search"
      type: "list"
      url: "/search"
      selectors:
        link: "a"
"""
        (tmp_path / "test-both.yaml").write_text(yaml_content)

        assert "test-both" in registry.get_by_provides("stream")
        assert "test-both" in registry.get_by_provides("download")

    def test_empty_dir_returns_empty(self, registry: PluginRegistry) -> None:
        """Empty plugin dir returns no results."""
        assert registry.get_by_provides("download") == []
        assert registry.get_by_provides("stream") == []

    def test_mixed_plugins_filtered(
        self, registry: PluginRegistry, tmp_path: Path
    ) -> None:
        """Multiple YAML plugins are correctly filtered by provides type."""
        dl_yaml = """\
name: "dl-plugin"
version: "1.0.0"
base_url: "https://dl.example.com"
scraping:
  mode: "scrapy"
  stages:
    - name: "search"
      type: "list"
      url: "/search"
      selectors:
        link: "a"
"""
        stream_yaml = """\
name: "stream-plugin"
version: "1.0.0"
base_url: "https://stream.example.com"
provides: "stream"
scraping:
  mode: "scrapy"
  stages:
    - name: "search"
      type: "list"
      url: "/search"
      selectors:
        link: "a"
"""
        (tmp_path / "dl.yaml").write_text(dl_yaml)
        (tmp_path / "stream.yaml").write_text(stream_yaml)

        dl_names = registry.get_by_provides("download")
        stream_names = registry.get_by_provides("stream")

        assert "dl-plugin" in dl_names
        assert "dl-plugin" not in stream_names
        assert "stream-plugin" in stream_names
        assert "stream-plugin" not in dl_names
