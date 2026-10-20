"""Every plugin has an entry in the live smoke test.

A plugin without one shows a broken parser in no live run: the live smoke
(`tests/live/test_plugin_smoke.py`, `poetry run pytest -m live`) searches each
listed plugin's real site. The lists are read from that file's source, so the
live module (and its browser imports) is not loaded here.
"""

from __future__ import annotations

import ast
from pathlib import Path

from scavengarr.infrastructure.plugins.registry import PluginRegistry

_ROOT = Path(__file__).resolve().parents[3]
_SMOKE = _ROOT / "tests" / "live" / "test_plugin_smoke.py"
_LISTS = ("HTTPX_PLUGINS", "PLAYWRIGHT_PLUGINS")


def smoke_entries(source: str) -> list[str]:
    """The plugin names of the smoke lists in *source*, in order."""
    names: list[str] = []
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in _LISTS
            and isinstance(node.value, ast.List)
        ):
            for entry in node.value.elts:
                assert isinstance(entry, ast.Tuple)
                name = entry.elts[0]
                assert isinstance(name, ast.Constant) and isinstance(name.value, str)
                names.append(name.value)
    return names


def test_every_plugin_has_a_smoke_entry() -> None:
    registry = PluginRegistry(_ROOT / "plugins")
    registry.discover()
    entries = smoke_entries(_SMOKE.read_text(encoding="utf-8"))

    assert sorted(set(registry.list_names()) - set(entries)) == []
    assert len(entries) == len(set(entries)), "a plugin is listed twice"


def test_the_lists_are_read_from_the_source() -> None:
    source = (
        'HTTPX_PLUGINS: list[tuple[str, str]] = [("a", "x"), ("b", "y")]\n'
        'PLAYWRIGHT_PLUGINS: list[tuple[str, str]] = [("c", "z")]\n'
        'OTHER: list[tuple[str, str]] = [("d", "w")]\n'
    )

    assert smoke_entries(source) == ["a", "b", "c"]
