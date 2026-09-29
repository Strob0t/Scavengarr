"""Tests for PluginRegistry.get_by_provides()."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from scavengarr.domain.plugins import PluginNotFoundError
from scavengarr.infrastructure.plugins import registry as registry_module
from scavengarr.infrastructure.plugins.registry import PluginRegistry


def _write_plugin(tmp_path: Path, filename: str, code: str) -> None:
    """Write a Python plugin file with proper formatting."""
    (tmp_path / filename).write_text(textwrap.dedent(code))


@pytest.fixture()
def registry(tmp_path: Path) -> PluginRegistry:
    """Create a registry pointing at a temp plugin directory."""
    return PluginRegistry(tmp_path)


class TestGetByProvides:
    """Tests for get_by_provides filtering."""

    def test_download_default(self, registry: PluginRegistry, tmp_path: Path) -> None:
        """Plugin without provides field defaults to 'download'."""
        _write_plugin(
            tmp_path,
            "test_dl.py",
            """\
            class _Plugin:
                name = "test-dl"
                async def search(self, query, category=None):
                    return []
            plugin = _Plugin()
            """,
        )

        names = registry.get_by_provides("download")

        assert "test-dl" in names

    def test_stream_explicit(self, registry: PluginRegistry, tmp_path: Path) -> None:
        """Plugin with provides='stream' is returned for stream queries."""
        _write_plugin(
            tmp_path,
            "test_stream.py",
            """\
            class _Plugin:
                name = "test-stream"
                provides = "stream"
                async def search(self, query, category=None):
                    return []
            plugin = _Plugin()
            """,
        )

        assert "test-stream" in registry.get_by_provides("stream")
        assert "test-stream" not in registry.get_by_provides("download")

    def test_both_returned_for_either(
        self, registry: PluginRegistry, tmp_path: Path
    ) -> None:
        """Plugin with provides='both' is returned for both stream and download."""
        _write_plugin(
            tmp_path,
            "test_both.py",
            """\
            class _Plugin:
                name = "test-both"
                provides = "both"
                async def search(self, query, category=None):
                    return []
            plugin = _Plugin()
            """,
        )

        assert "test-both" in registry.get_by_provides("stream")
        assert "test-both" in registry.get_by_provides("download")

    def test_empty_dir_returns_empty(self, registry: PluginRegistry) -> None:
        """Empty plugin dir returns no results."""
        assert registry.get_by_provides("download") == []
        assert registry.get_by_provides("stream") == []

    def test_mixed_plugins_filtered(
        self, registry: PluginRegistry, tmp_path: Path
    ) -> None:
        """Multiple plugins are correctly filtered by provides type."""
        _write_plugin(
            tmp_path,
            "dl.py",
            """\
            class _Plugin:
                name = "dl-plugin"
                async def search(self, query, category=None):
                    return []
            plugin = _Plugin()
            """,
        )
        _write_plugin(
            tmp_path,
            "stream.py",
            """\
            class _Plugin:
                name = "stream-plugin"
                provides = "stream"
                async def search(self, query, category=None):
                    return []
            plugin = _Plugin()
            """,
        )

        dl_names = registry.get_by_provides("download")
        stream_names = registry.get_by_provides("stream")

        assert "dl-plugin" in dl_names
        assert "dl-plugin" not in stream_names
        assert "stream-plugin" in stream_names
        assert "stream-plugin" not in dl_names


class TestMetadataCache:
    """Tests for plugin metadata caching in get_by_provides()."""

    def test_plugin_files_execute_once(
        self,
        registry: PluginRegistry,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Names, lookups and metadata come from one load per file.

        /healthz calls list_names() on every probe: it must not execute
        every plugin module again.
        """
        for name in ("one", "two"):
            _write_plugin(
                tmp_path,
                f"{name}.py",
                f"""\
                class _Plugin:
                    name = "{name}"
                    async def search(self, query, category=None):
                        return []
                plugin = _Plugin()
                """,
            )
        loads: list[Path] = []
        real_load = registry_module.load_python_plugin

        def counting_load(path: Path) -> object:
            loads.append(path)
            return real_load(path)

        monkeypatch.setattr(registry_module, "load_python_plugin", counting_load)

        for _ in range(3):
            assert registry.list_names() == ["one", "two"]
        registry.get("two")
        registry.get_by_provides("download")
        registry.get_mode("one")

        assert sorted(p.name for p in loads) == ["one.py", "two.py"]

    def test_second_call_uses_cache(
        self, registry: PluginRegistry, tmp_path: Path
    ) -> None:
        """Second get_by_provides() call uses cached metadata."""
        _write_plugin(
            tmp_path,
            "cached_plugin.py",
            """\
            class _Plugin:
                name = "cached-plugin"
                async def search(self, query, category=None):
                    return []
            plugin = _Plugin()
            """,
        )

        result1 = registry.get_by_provides("download")
        result2 = registry.get_by_provides("download")
        assert result1 == result2


class TestBrokenAndDuplicatePlugins:
    def test_broken_plugin_is_skipped(
        self, registry: PluginRegistry, tmp_path: Path
    ) -> None:
        """One broken plugin file must not break every other plugin."""
        _write_plugin(
            tmp_path,
            "good.py",
            """\
            class _Plugin:
                name = "good"
                provides = "stream"
                async def search(self, query, category=None):
                    return []
            plugin = _Plugin()
            """,
        )
        _write_plugin(tmp_path, "broken.py", "def search(:\n")

        assert registry.get_by_provides("stream") == ["good"]
        assert registry.list_names() == ["good"]
        with pytest.raises(PluginNotFoundError):
            registry.get("broken")

    def test_first_file_wins_on_duplicate_names(
        self, registry: PluginRegistry, tmp_path: Path
    ) -> None:
        for filename, provides in (("a.py", "stream"), ("b.py", "download")):
            _write_plugin(
                tmp_path,
                filename,
                f"""\
                class _Plugin:
                    name = "same"
                    provides = "{provides}"
                    async def search(self, query, category=None):
                        return []
                plugin = _Plugin()
                """,
            )

        assert registry.list_names() == ["same"]
        assert registry.get("same").provides == "stream"
        assert registry.get_by_provides("download") == []


class TestGetLanguages:
    """Tests for get_languages() filtering."""

    def test_default_language(self, registry: PluginRegistry, tmp_path: Path) -> None:
        """Plugin without languages attribute defaults to ['de']."""
        _write_plugin(
            tmp_path,
            "default_lang.py",
            """\
            class _Plugin:
                name = "default-lang"
                async def search(self, query, category=None):
                    return []
            plugin = _Plugin()
            """,
        )

        assert registry.get_languages("default-lang") == ["de"]

    def test_explicit_single_language(
        self, registry: PluginRegistry, tmp_path: Path
    ) -> None:
        """Plugin with languages=['en'] returns ['en']."""
        _write_plugin(
            tmp_path,
            "en_plugin.py",
            """\
            class _Plugin:
                name = "en-plugin"
                languages = ["en"]
                async def search(self, query, category=None):
                    return []
            plugin = _Plugin()
            """,
        )

        assert registry.get_languages("en-plugin") == ["en"]

    def test_multi_language(self, registry: PluginRegistry, tmp_path: Path) -> None:
        """Plugin with languages=['de', 'en'] returns both."""
        _write_plugin(
            tmp_path,
            "multi_lang.py",
            """\
            class _Plugin:
                name = "multi-lang"
                languages = ["de", "en"]
                async def search(self, query, category=None):
                    return []
            plugin = _Plugin()
            """,
        )

        assert registry.get_languages("multi-lang") == ["de", "en"]

    def test_unknown_plugin_returns_default(self, registry: PluginRegistry) -> None:
        """Unknown plugin name returns default ['de']."""
        assert registry.get_languages("nonexistent") == ["de"]


class TestGetMode:
    """Tests for get_mode() plugin mode lookup."""

    def test_httpx_default(self, registry: PluginRegistry, tmp_path: Path) -> None:
        """Plugin without explicit mode defaults to 'httpx'."""
        _write_plugin(
            tmp_path,
            "httpx_plugin.py",
            """\
            class _Plugin:
                name = "httpx-default"
                async def search(self, query, category=None):
                    return []
            plugin = _Plugin()
            """,
        )

        assert registry.get_mode("httpx-default") == "httpx"

    def test_playwright_mode(self, registry: PluginRegistry, tmp_path: Path) -> None:
        """Plugin with mode='playwright' is detected correctly."""
        _write_plugin(
            tmp_path,
            "pw_plugin.py",
            """\
            class _Plugin:
                name = "pw-plugin"
                mode = "playwright"
                async def search(self, query, category=None):
                    return []
            plugin = _Plugin()
            """,
        )

        assert registry.get_mode("pw-plugin") == "playwright"

    def test_unknown_plugin_returns_httpx(self, registry: PluginRegistry) -> None:
        """Unknown plugin name returns 'httpx' as safe default."""
        assert registry.get_mode("nonexistent") == "httpx"
