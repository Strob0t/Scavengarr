"""Plugin registry with lazy loading and in-memory caching."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from scavengarr.domain.plugins import (
    PluginLoadError,
    PluginNotFoundError,
    PluginProtocol,
    PluginProvides,
)

from .loader import load_python_plugin

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _PluginRef:
    path: Path


@dataclass(frozen=True)
class _PluginMeta:
    """Cached plugin metadata to avoid re-parsing for filtering queries."""

    name: str
    provides: str
    mode: str
    languages: tuple[str, ...]


class PluginRegistry:
    """
    Lazy-loading plugin registry.

    discover():
      - indexes .py files only (no Python execution)

    Every other method loads all discovered files on first use, each file
    exactly once: the plugin name is only known after executing the file,
    and /healthz lists the names on every probe. A file that fails to load
    is logged (``plugin_load_failed``) and skipped; on duplicate names the
    first file (by file name) wins.
    """

    def __init__(self, plugin_dir: Path) -> None:
        self._plugin_dir = plugin_dir
        self._discovered: bool = False
        self._refs: list[_PluginRef] = []
        self._loaded: bool = False
        self._plugins: dict[str, PluginProtocol] = {}
        self._meta_cache: dict[str, _PluginMeta] = {}

    @property
    def plugin_dir(self) -> Path:
        return self._plugin_dir

    @property
    def discovered_count(self) -> int:
        """Number of discovered plugin files (no parsing required)."""
        return len(self._refs)

    def discover(self) -> None:
        if self._discovered:
            return

        self._discovered = True
        self._refs = []

        if not self._plugin_dir.exists():
            log.warning("plugin_directory_not_found", directory=str(self._plugin_dir))
            return

        if not self._plugin_dir.is_dir():
            log.warning("plugin_directory_not_found", directory=str(self._plugin_dir))
            return

        for path in sorted(self._plugin_dir.iterdir(), key=lambda p: p.name):
            if path.is_dir():
                continue
            if path.suffix.lower() == ".py":
                self._refs.append(_PluginRef(path=path))

        log.info(
            "plugins_discovered",
            count=len(self._refs),
            directory=str(self._plugin_dir),
        )

        if not self._refs:
            log.warning("no_plugins_found", directory=str(self._plugin_dir))

    def list_names(self) -> list[str]:
        self._ensure_loaded()
        return sorted(self._plugins)

    def get(self, name: str) -> PluginProtocol:
        self._ensure_loaded()
        plugin = self._plugins.get(name)
        if plugin is None:
            raise PluginNotFoundError(f"Plugin '{name}' not found")
        return plugin

    def get_by_provides(self, provides: PluginProvides) -> list[str]:
        """Return plugin names filtered by their ``provides`` attribute."""
        self._ensure_loaded()

        names: list[str] = []
        for meta in self._meta_cache.values():
            if meta.provides == provides or meta.provides == "both":
                names.append(meta.name)

        return sorted(names)

    def get_languages(self, name: str) -> list[str]:
        """Return the languages list for a plugin (default ``["de"]``)."""
        self._ensure_loaded()
        meta = self._meta_cache.get(name)
        if meta is None:
            return ["de"]
        return list(meta.languages)

    def get_mode(self, name: str) -> str:
        """Return the mode of a plugin (``'httpx'`` or ``'playwright'``)."""
        self._ensure_loaded()
        meta = self._meta_cache.get(name)
        return meta.mode if meta is not None else "httpx"

    def remove(self, name: str) -> None:
        """Remove a plugin by name (used for disabling via config overrides)."""
        self._ensure_loaded()
        self._plugins.pop(name, None)
        self._meta_cache.pop(name, None)

    def _ensure_loaded(self) -> None:
        """Load every discovered plugin file once (lazy, one-time)."""
        self.discover()
        if self._loaded:
            return
        self._loaded = True

        for ref in self._refs:
            try:
                plugin = load_python_plugin(ref.path)
            except PluginLoadError:
                continue  # logged by the loader as plugin_load_failed
            if plugin.name in self._plugins:
                log.warning(
                    "plugin_name_duplicate",
                    plugin_name=plugin.name,
                    plugin_file=str(ref.path),
                )
                continue
            self._plugins[plugin.name] = plugin
            raw_langs = getattr(plugin, "languages", None)
            self._meta_cache[plugin.name] = _PluginMeta(
                name=plugin.name,
                provides=getattr(plugin, "provides", "download"),
                mode=getattr(plugin, "mode", "httpx"),
                languages=tuple(raw_langs if raw_langs is not None else ["de"]),
            )
            log.info("plugin_loaded", plugin_name=plugin.name, plugin_type="python")

        log.debug("plugin_meta_cached", count=len(self._meta_cache))
