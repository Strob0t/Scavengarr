"""docs/plugins.md is generated from the plugin metadata and must stay current."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_ROOT = Path(__file__).resolve().parents[3]


def _generator() -> ModuleType:
    path = _ROOT / "scripts" / "generate_plugin_list.py"
    spec = importlib.util.spec_from_file_location("generate_plugin_list", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_list_is_current() -> None:
    expected = _generator().render(_ROOT / "plugins")
    actual = (_ROOT / "docs" / "plugins.md").read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/plugins.md is outdated: run "
        "`poetry run python scripts/generate_plugin_list.py`"
    )


def test_every_plugin_is_listed() -> None:
    text = _generator().render(_ROOT / "plugins")
    plugin_files = [p for p in (_ROOT / "plugins").glob("*.py")]
    rows = [line for line in text.splitlines() if line.startswith("| `")]
    assert len(rows) == len(plugin_files)
