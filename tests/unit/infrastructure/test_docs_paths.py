"""The docs name repository paths that exist.

Every backticked path in the instruction files and the feature and
architecture docs (plans and OpenSpec changes name future files and are
not checked) must exist, so a moved module shows up here and not in a
reader's shell (finding 10 of ``docs/plans/ideas-backlog.md``). The
docs cite paths relative to the package (``infrastructure/plugins/dom.py``)
and the architecture tables relative to their layer (``entities/torznab.py``,
``plugins/base.py``, ``cache/redis_adapter.py``); those count as present when
they exist under ``src/scavengarr/`` or ``src/scavengarr/<layer>/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[3]
_DOCS = [
    _ROOT / "AGENTS.md",
    _ROOT / "README.md",
    _ROOT / "CONTRIBUTING.md",
    *sorted((_ROOT / "docs" / "features").glob("*.md")),
    *sorted((_ROOT / "docs" / "architecture").glob("*.md")),
]
_PREFIXES = (
    "src/",
    "scripts/",
    "tests/",
    "plugins/",
    "docs/",
    "docker/",
    "openspec/",
    ".github/",
    ".claude/",
    ".devcontainer/",
)
_EXTENSIONS = (".py", ".md", ".yml", ".yaml", ".sh", ".json", ".toml", ".txt", ".html")
_LAYERS = ("domain", "application", "infrastructure", "interfaces")
_BACKTICKED = re.compile(r"`([^`\n]+)`")
# Placeholders, globs and code name no single file
_PATTERN_CHARS = frozenset("<>*{}() ")


def named_paths(doc: Path) -> list[str]:
    """The paths *doc* names in backticks: tokens with a directory and a
    file extension, from the repository root or relative to a layer."""
    return [
        token
        for token in _BACKTICKED.findall(doc.read_text(encoding="utf-8"))
        if "/" in token
        and token.endswith(_EXTENSIONS)
        and not _PATTERN_CHARS.intersection(token)
        # Not repository paths: absolute and home paths, at-references
        # (`@/openspec/AGENTS.md`), unversioned dot directories (`.devdata/`)
        and not token.startswith(("/", "./", "../", "~", "@"))
        and (not token.startswith(".") or token.startswith(_PREFIXES))
    ]


def _exists(path: str, root: Path) -> bool:
    if (root / path).exists():
        return True
    if path.startswith(_PREFIXES) and not path.startswith("plugins/"):
        return False
    package = root / "src" / "scavengarr"
    bases = (package, *(package / layer for layer in _LAYERS))
    return any((base / path).exists() for base in bases)


def missing_paths(doc: Path, root: Path = _ROOT) -> list[str]:
    """The paths *doc* names that exist neither from *root* nor relative to
    the package or one of its layers."""
    return [path for path in named_paths(doc) if not _exists(path, root)]


@pytest.mark.parametrize("doc", _DOCS, ids=lambda doc: str(doc.relative_to(_ROOT)))
def test_the_named_paths_exist(doc: Path) -> None:
    missing = missing_paths(doc)

    assert not missing, (
        f"{doc.relative_to(_ROOT)} names paths that do not exist: {missing}"
    )


def test_a_renamed_path_is_missing(tmp_path: Path) -> None:
    doc = tmp_path / "scratch.md"
    doc.write_text(
        "Read `src/scavengarr/domain/plugins/base.py`, `plugins/base.py` and "
        "`entities/torznab.py`; `src/scavengarr/domain/plugins/base_v2.py` "
        "and `entities/torznab_v2.py` moved. `tests/unit/infrastructure/"
        "test_<name>_plugin.py` is a pattern, `plugin.search()` code.\n"
    )

    assert missing_paths(doc) == [
        "src/scavengarr/domain/plugins/base_v2.py",
        "entities/torznab_v2.py",
    ]
