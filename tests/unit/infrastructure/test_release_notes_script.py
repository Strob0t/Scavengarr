"""scripts/release_notes.py writes a tag's release page from CHANGELOG.md."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_ROOT = Path(__file__).parents[3]
_SCRIPT = _ROOT / "scripts" / "release_notes.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_notes_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release_notes = _load()

_SAMPLE = """# Changelog

All notable changes. Newest entries first.

---

## Unreleased (staging)

### Added: Not Released Yet

- On staging only.

## v1.2.0 - 2026-01-02

The summary of the release, on two lines
that stay one paragraph.

### Fixed: A Bug: Gone

- Its details.

### Added: A Feature

- Its details.

### Perf: Faster Search

- Its details.

### Removed: An Old Flag

### Changed: A Default

### Live Tests Opt-In

### Added: Another Feature

---

## v1.1.0 - 2025-12-01

### Fixed: An Older Bug
"""


def test_the_notes_group_the_titles_by_kind() -> None:
    """The summary, then Added, Changed, Fixed and Removed, then any other kind
    as written in the order it first appears, a heading without a kind under
    Other; the image and the full section at the tag close the page."""
    notes = release_notes.release_notes(_SAMPLE, "v1.2.0")

    assert notes == (
        "The summary of the release, on two lines\n"
        "that stay one paragraph.\n"
        "\n"
        "### Added\n\n- A Feature\n- Another Feature\n"
        "\n"
        "### Changed\n\n- A Default\n"
        "\n"
        "### Fixed\n\n- A Bug: Gone\n"
        "\n"
        "### Removed\n\n- An Old Flag\n"
        "\n"
        "### Perf\n\n- Faster Search\n"
        "\n"
        "### Other\n\n- Live Tests Opt-In\n"
        "\n"
        "Image: `ghcr.io/strob0t/scavengarr:v1.2.0`\n"
        "\n"
        "All changes: [CHANGELOG.md at v1.2.0]"
        "(https://github.com/Strob0t/Scavengarr/blob/v1.2.0/CHANGELOG.md"
        "#v120---2026-01-02)\n"
    )


def test_a_section_without_a_summary_starts_with_its_entries() -> None:
    notes = release_notes.release_notes(_SAMPLE, "v1.1.0")

    assert notes is not None
    assert notes.startswith("### Fixed\n\n- An Older Bug\n")


def test_a_tag_without_a_section_has_no_notes() -> None:
    """`v1.2` is no section: a heading names the whole tag."""
    assert release_notes.release_notes(_SAMPLE, "v1.3.0") is None
    assert release_notes.release_notes(_SAMPLE, "v1.2") is None


def _run(tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), tag], capture_output=True, text=True, check=False
    )


def test_the_script_fails_for_a_missing_version() -> None:
    """The release workflow stops before it creates a page without notes."""
    done = _run("v9.9.9")

    assert done.returncode != 0
    assert done.stdout == ""
    assert "CHANGELOG.md has no section for v9.9.9" in done.stderr


def test_the_script_writes_v0_5_0_from_the_changelog() -> None:
    """Every entry of the real section once, under its kind."""
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = changelog[changelog.index("## v0.5.0 ") : changelog.index("## v0.4.0 ")]
    entries = re.findall(r"^### \w+: (.+)$", section, re.M)

    done = _run("v0.5.0")

    assert done.returncode == 0, done.stderr
    notes = done.stdout
    assert notes.startswith("Challenges and captchas: ")
    kinds = re.findall(r"^### (.+)$", notes, re.M)
    assert kinds == ["Added", "Changed", "Fixed", "Removed"]
    assert sorted(re.findall(r"^- (.+)$", notes, re.M)) == sorted(entries)
    assert "- The Public Broadcasters' Mediatheken\n" in notes
    assert "Image: `ghcr.io/strob0t/scavengarr:v0.5.0`\n" in notes
    assert notes.endswith(
        "(https://github.com/Strob0t/Scavengarr/blob/v0.5.0/CHANGELOG.md"
        "#v050---2026-10-20)\n"
    )


def test_the_link_takes_githubs_anchor_of_the_heading() -> None:
    """Punctuation goes, spaces become hyphens: the v0.1.0 heading."""
    assert (
        release_notes.anchor("v0.1.0 - 2026-02-09 (Initial Release)")
        == "v010---2026-02-09-initial-release"
    )
