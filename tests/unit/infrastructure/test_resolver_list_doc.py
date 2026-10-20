"""docs/reference/resolvers.md is generated from the resolver registrations,
kept current."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_ROOT = Path(__file__).resolve().parents[3]
_DOC = _ROOT / "docs" / "reference" / "resolvers.md"


def _generator() -> ModuleType:
    path = _ROOT / "scripts" / "generate_resolver_list.py"
    spec = importlib.util.spec_from_file_location("generate_resolver_list", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolver_list_is_current() -> None:
    expected = _generator().render()
    actual = _DOC.read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/reference/resolvers.md is outdated: run "
        "`PYTHONPATH=src poetry run python scripts/generate_resolver_list.py`"
    )


def test_every_registered_resolver_has_one_row() -> None:
    generator = _generator()
    rows = [
        line.split("`")[1]
        for line in generator.render().splitlines()
        if line.startswith("| `")
    ]

    assert sorted(rows) == sorted(r.name for r in generator.registrations())


def test_every_resolver_lists_its_domains() -> None:
    generator = _generator()
    text = generator.render()

    for resolver in generator.registrations():
        assert f"\n- `{resolver.name}`: " in text
