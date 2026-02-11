"""Plugin registry with lazy loading and in-memory caching."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog
import yaml

from scavengarr.domain.plugins import (
    DuplicatePluginError,
    PluginNotFoundError,
    PluginProtocol,
    YamlPluginDefinition,
)

from .loader import load_python_plugin, load_yaml_plugin

log = structlog.get_logger(__name__)

PluginType = Literal["yaml", "python"]


@dataclass(frozen=True)
class _PluginRef:
    path: Path
    plugin_type: PluginType


class PluginRegistry:
    """
    Lazy-loading plugin registry.

    discover():
      - indexes files only (no YAML parsing, no Python execution)

    get()/get_by_mode()/load_all()/list_names():
      - may load/parse on demand and cache results
    """

    def __init__(self, plugin_dir: Path) -> None:
        self._plugin_dir = plugin_dir
        self._discovered: bool = False
        self._refs: list[_PluginRef] = []

        self._yaml_cache: dict[str, YamlPluginDefinition] = {}
        self._python_cache: dict[str, PluginProtocol] = {}

    @property
    def plugin_dir(self) -> Path:
        return self._plugin_dir

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
            suffix = path.suffix.lower()
            if suffix in {".yaml", ".yml"}:
                self._refs.append(_PluginRef(path=path, plugin_type="yaml"))
            elif suffix == ".py":
                self._refs.append(_PluginRef(path=path, plugin_type="python"))
            else:
                continue

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
                # list_names should not crash the app;
                # duplicates are surfaced on load_all()
                continue
            names.add(name)
            out.append(name)

        return sorted(out)

    def get(self, name: str) -> YamlPluginDefinition | PluginProtocol:
        self.discover()

        if name in self._yaml_cache:
            return self._yaml_cache[name]
        if name in self._python_cache:
            return self._python_cache[name]

        # Find first matching plugin by peeking the name from the file.
        for ref in self._refs:
            ref_name = self._peek_name(ref)
            if ref_name != name:
                continue

            if ref.plugin_type == "yaml":
                plugin = load_yaml_plugin(ref.path)
                self._yaml_cache[plugin.name] = plugin
                log.info("plugin_loaded", plugin_name=plugin.name, plugin_type="yaml")
                return plugin

            plugin = load_python_plugin(ref.path)
            self._python_cache[plugin.name] = plugin
            log.info("plugin_loaded", plugin_name=plugin.name, plugin_type="python")
            return plugin

        raise PluginNotFoundError(f"Plugin '{name}' not found")

    def get_by_mode(
        self, mode: Literal["scrapy", "playwright"]
    ) -> list[YamlPluginDefinition]:
        """
        Return YAML plugins filtered by scraping mode.
        Python plugins are intentionally excluded.
        """
        self.discover()

        result: list[YamlPluginDefinition] = []
        for ref in self._refs:
            if ref.plugin_type != "yaml":
                continue

            plugin = self._load_yaml(ref)
            if plugin.scraping.mode == mode:
                result.append(plugin)

        return sorted(result, key=lambda p: p.name)

    def load_all(self) -> None:
        """
        Force-load all discovered plugins.

        Note: This may raise DuplicatePluginError/validation/load errors, by design.
        """
        self.discover()

        loaded_names: set[str] = set()

        for ref in self._refs:
            if ref.plugin_type == "yaml":
                plugin = self._load_yaml(ref)
                if plugin.name in loaded_names:
                    raise DuplicatePluginError(
                        f"Plugin name '{plugin.name}' already exists"
                    )
                loaded_names.add(plugin.name)
                continue

            plugin = self._load_python(ref)
            if plugin.name in loaded_names:
                raise DuplicatePluginError(
                    f"Plugin name '{plugin.name}' already exists"
                )
            loaded_names.add(plugin.name)

    def _load_yaml(self, ref: _PluginRef) -> YamlPluginDefinition:
        # If already cached by name, return it. But name is
        # only known after parsing; parse once, cache by name.
        plugin = load_yaml_plugin(ref.path)
        cached = self._yaml_cache.get(plugin.name)
        if cached is not None:
            return cached
        self._yaml_cache[plugin.name] = plugin
        log.info("plugin_loaded", plugin_name=plugin.name, plugin_type="yaml")
        return plugin

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
        Peek plugin name without full validation where possible.

        - YAML: yaml.safe_load + read top-level 'name'
        - Python: import module and read plugin.name
          (this executes code; acceptable outside discover())
        """
        if ref.plugin_type == "yaml":
            try:
                raw = ref.path.read_text(encoding="utf-8")
                data = yaml.safe_load(raw)
                if not isinstance(data, dict):
                    return None
                name = data.get("name")
                return name if isinstance(name, str) and name.strip() else None
            except Exception:
                return None

        # python
        try:
            plugin = load_python_plugin(ref.path)
            return plugin.name
        except Exception:
            return None
