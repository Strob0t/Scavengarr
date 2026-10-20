"""Source code depends inward only: the rule table of docs/target/architecture.md.

Every module under ``src/scavengarr`` is parsed with ``ast``; each ``import``
and ``from … import`` of the ``scavengarr`` package counts, the ones under
``if TYPE_CHECKING:`` and inside functions included. Modules directly under
the package (``__init__``, ``__main__``) count as ``interfaces``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "scavengarr"

# docs/target/architecture.md → The dependency rule: what each layer may import
_ALLOWED: dict[str, frozenset[str]] = {
    "domain": frozenset({"domain"}),
    "application": frozenset({"domain", "application"}),
    "infrastructure": frozenset({"domain", "infrastructure"}),
    "interfaces": frozenset({"domain", "application", "infrastructure", "interfaces"}),
}


def _module(path: Path, src: Path) -> str:
    parts = list(path.relative_to(src.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _layer(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[1] in _ALLOWED else "interfaces"


def _imports(path: Path, module: str) -> list[str]:
    """The ``scavengarr`` modules *path* imports, relative imports resolved."""
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                found.append(".".join([*base, *([node.module] if node.module else [])]))
            elif node.module:
                found.append(node.module)
    return [name for name in found if name.split(".")[0] == "scavengarr"]


def breaches(src: Path = _SRC) -> list[str]:
    """Every import against the rule, as ``module imports target``."""
    found: list[str] = []
    for path in sorted(src.rglob("*.py")):
        module = _module(path, src)
        layer = _layer(module)
        for target in _imports(path, module):
            if _layer(target) not in _ALLOWED[layer]:
                found.append(f"{module} imports {target}")
    return found


def test_every_import_points_inward() -> None:
    assert breaches() == []


def test_the_package_is_parsed() -> None:
    assert len(list(_SRC.rglob("*.py"))) > 100
    assert _imports(
        _SRC / "interfaces" / "composition.py", "scavengarr.interfaces.composition"
    )


def _write(root: Path, name: str, text: str) -> None:
    path = root / "scavengarr" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_breach_names_the_module_and_the_import(tmp_path: Path) -> None:
    _write(tmp_path, "__init__.py", "")
    _write(tmp_path, "domain/__init__.py", "")
    _write(
        tmp_path,
        "domain/model.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from scavengarr.infrastructure.cache import Cache\n",
    )
    _write(tmp_path, "infrastructure/__init__.py", "")
    _write(tmp_path, "infrastructure/cache.py", "from ..application import flow\n")
    _write(tmp_path, "application/__init__.py", "")
    _write(tmp_path, "application/flow.py", "from scavengarr.domain import model\n")

    assert breaches(tmp_path / "scavengarr") == [
        "scavengarr.domain.model imports scavengarr.infrastructure.cache",
        "scavengarr.infrastructure.cache imports scavengarr.application",
    ]
