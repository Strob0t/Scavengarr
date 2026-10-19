"""The tech-stack reference names every dependency of ``pyproject.toml``.

``docs/reference/tech-stack.md`` gives each runtime and development dependency
one line with its purpose and the reason for it, and names the alternatives
not used. A commit that adds or removes a dependency updates it, or this
fails; an alternative listed as not used must not be a dependency.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[3]
_DOC = _ROOT / "docs" / "reference" / "tech-stack.md"
_POETRY = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
    "poetry"
]
_DECLARED = {
    "Runtime": set(_POETRY["dependencies"]),
    "Development": set(_POETRY["group"]["dev"]["dependencies"]),
}


def _normalized(name: str) -> str:
    """A package name as PyPI compares it (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _first_cells(section: str) -> list[str]:
    """The first cell of every table row under the ``## <section>`` heading,
    the tables of its ``###`` subsections included."""
    text = _DOC.read_text(encoding="utf-8")
    match = re.search(rf"^## {section}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"{_DOC.name} has no section '## {section}'"
    rows = [line for line in match.group(1).splitlines() if line.startswith("| ")]
    return [row.split(" | ")[0].removeprefix("| ") for row in rows]


def _packages(cell: str) -> set[str]:
    """The backticked package names of *cell*: a header cell names none."""
    return {_normalized(name) for name in re.findall(r"`([^`]+)`", cell)}


@pytest.mark.parametrize("section", sorted(_DECLARED))
def test_every_dependency_has_its_line(section: str) -> None:
    declared = {_normalized(name) for name in _DECLARED[section]}
    documented = set().union(*map(_packages, _first_cells(section)))

    assert sorted(declared - documented) == [], (
        f"dependencies without a line under '## {section}' in {_DOC.name}"
    )
    assert sorted(documented - declared) == [], (
        f"lines under '## {section}' in {_DOC.name} name no dependency"
    )


def test_no_alternative_not_used_is_a_dependency() -> None:
    declared = {_normalized(name) for names in _DECLARED.values() for name in names}
    named = set().union(*map(_packages, _first_cells("Not used")))

    assert named, f"'## Not used' in {_DOC.name} names no package"
    assert sorted(named & declared) == []
