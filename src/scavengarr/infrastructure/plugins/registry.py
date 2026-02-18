"""Plugin registry with lazy loading and in-memory caching."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from scavengarr.domain.plugins import (
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
    Lazy-loading plugin registry with metadata caching.

    discover():
      - indexes .py files only (no Python execution)

    get()/load_all()/list_names():
      - may load on demand and cache results

    Metadata caching:
      - get_by_provides() caches plugin metadata on first call so
        subsequent calls don't re-parse all plugin files.
    """

    def __init__(self, plugin_dir: Path) -> None:
        self._plugin_dir = plugin_dir
        self._discovered: bool = False
        self._refs: list[_PluginRef] = []
        self._meta_cache: dict[str, _PluginMeta] = {}
        self._meta_cached: bool = False

        self._python_cache: dict[str, PluginProtocol] = {}

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
        self.discover()

        names: set[str] = set()
        out: list[str] = []
        for ref in self._refs:
            name = self._peek_name(ref)
            if name is None:
                continue
            if name in names:
                continue
            names.add(name)
            out.append(name)

        return sorted(out)

    def get(self, name: str) -> PluginProtocol:
        self.discover()

        if name in self._python_cache:
            return self._python_cache[name]

        for ref in self._refs:
            ref_name = self._peek_name(ref)
            if ref_name != name:
                continue

            plugin = load_python_plugin(ref.path)
            self._python_cache[plugin.name] = plugin
            log.info("plugin_loaded", plugin_name=plugin.name, plugin_type="python")
            return plugin

        raise PluginNotFoundError(f"Plugin '{name}' not found")

    def get_by_provides(self, provides: PluginProvides) -> list[str]:
        """Return plugin names filtered by their ``provides`` attribute.

        Uses cached metadata after the first call to avoid re-parsing
        all plugin files on every invocation.
        """
        self.discover()
        self._ensure_meta_cache()

        names: list[str] = []
        for meta in self._meta_cache.values():
            if meta.provides == provides or meta.provides == "both":
                names.append(meta.name)

        return sorted(names)

    def get_languages(self, name: str) -> list[str]:
        """Return the languages list for a plugin (default ``["de"]``)."""
        self.discover()
        self._ensure_meta_cache()
        meta = self._meta_cache.get(name)
        if meta is None:
            return ["de"]
        return list(meta.languages)

    def get_mode(self, name: str) -> str:
        """Return the mode of a plugin (``'httpx'`` or ``'playwright'``)."""
        self.discover()
        self._ensure_meta_cache()
        meta = self._meta_cache.get(name)
        return meta.mode if meta is not None else "httpx"

    def _ensure_meta_cache(self) -> None:
        """Build metadata cache from all plugins (lazy, one-time)."""
        if self._meta_cached:
            return

        for ref in self._refs:
            py_plugin = self._load_python(ref)
            raw_langs = getattr(py_plugin, "languages", None)
            if raw_langs is None:
                raw_langs = ["de"]
            self._meta_cache[py_plugin.name] = _PluginMeta(
                name=py_plugin.name,
                provides=getattr(py_plugin, "provides", "download"),
                mode=getattr(py_plugin, "mode", "httpx"),
                languages=tuple(raw_langs),
            )

        self._meta_cached = True
        log.debug(
            "plugin_meta_cached",
            count=len(self._meta_cache),
        )

    def remove(self, name: str) -> None:
        """Remove a plugin by name (used for disabling via config overrides)."""
        self._refs = [r for r in self._refs if self._peek_name(r) != name]
        self._python_cache.pop(name, None)
        self._meta_cache.pop(name, None)

    def _load_python(self, ref: _PluginRef) -> PluginProtocol:
        plugin = load_python_plugin(ref.path)
        cached = self._python_cache.get(plugin.name)
        if cached is not None:
            return cached
        self._python_cache[plugin.name] = plugin
        log.info("plugin_loaded", plugin_name=plugin.name, plugin_type="python")
        return plugin

    def _peek_name(self, ref: _PluginRef) -> str | None:
        """
        Peek plugin name by importing the module and reading plugin.name.
        """
        try:
            plugin = load_python_plugin(ref.path)
            return plugin.name
        except Exception:
            return None
