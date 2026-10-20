"""The README stays current.

The README is the project's front page: its documentation table links every
document under ``docs/current/``, ``docs/how-to/`` and ``docs/reference/``,
and the counts it names are those of the generated lists (the plugins, the
plugins on the browser engine, the hoster resolvers). A row that adds a
document or a plugin updates the README in the same commit, or this fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[3]
_README = (_ROOT / "README.md").read_text(encoding="utf-8")
_LINKED_FOLDERS = ("current", "how-to", "reference")
_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _link_targets() -> set[str]:
    return set(re.findall(r"\]\(([^)#]+)\)", _README))


@pytest.mark.parametrize(
    "doc",
    [
        doc
        for folder in _LINKED_FOLDERS
        for doc in sorted((_ROOT / "docs" / folder).glob("*.md"))
    ],
    ids=lambda doc: doc.relative_to(_ROOT).as_posix(),
)
def test_readme_links_every_current_howto_and_reference_doc(doc: Path) -> None:
    rel = doc.relative_to(_ROOT).as_posix()
    assert rel in _link_targets(), (
        f"{rel} is missing from the README's documentation table"
    )


def _plugin_rows() -> list[str]:
    text = (_ROOT / "docs" / "reference" / "plugins.md").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith("| `")]


def test_readme_plugin_count_is_the_generated_list() -> None:
    expected = len(_plugin_rows())
    counts = {int(n) for n in re.findall(r"\b(\d+) plugins\b", _README)}
    assert counts == {expected}, (
        f"README says {counts} plugins, the list has {expected}"
    )


def test_readme_browser_plugin_count_is_the_generated_list() -> None:
    expected = sum("| Playwright |" in row for row in _plugin_rows())
    match = re.search(r"\b(\w+) of them use the browser engine", _README)
    assert match is not None, (
        "the README no longer says how many plugins use the browser"
    )
    word = match.group(1).lower()
    stated = _WORDS[word] if word in _WORDS else int(word)
    assert stated == expected, (
        f"README says {stated} browser plugins, the list has {expected}"
    )


def test_readme_resolver_count_is_the_generated_list() -> None:
    text = (_ROOT / "docs" / "reference" / "resolvers.md").read_text(encoding="utf-8")
    match = re.search(r"^(\d+) resolvers:", text, re.MULTILINE)
    assert match is not None, "docs/reference/resolvers.md names no count"
    expected = int(match.group(1))
    counts = {int(n) for n in re.findall(r"\b(\d+) hoster resolvers\b", _README)}
    assert counts == {expected}, (
        f"README says {counts} resolvers, the list has {expected}"
    )
