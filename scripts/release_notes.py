"""Print the release page's notes of a tag from CHANGELOG.md.

Usage:
    python scripts/release_notes.py vX.Y.Z

The notes are the summary paragraph of the tag's section, its entries'
titles grouped by kind (Added, Changed, Fixed, Removed, then any other kind
in the order it first appears; a heading without a kind under Other), the
image of the tag and a link to the full section at the tag. A tag without a
section exits non-zero. `.github/workflows/release.yml` passes the notes to
`gh release create --notes-file`; the standard library is all it needs, so
the workflow runs it without installing the project.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
_IMAGE = "ghcr.io/strob0t/scavengarr"
_REPOSITORY = "https://github.com/Strob0t/Scavengarr"
_KINDS = ("Added", "Changed", "Fixed", "Removed")
# "### Kind: Title"; a title may hold a colon of its own
_ENTRY = re.compile(r"^### (?:(?P<kind>[^:]+): )?(?P<title>.+)$")


def anchor(heading: str) -> str:
    """GitHub's anchor of a Markdown heading: lower case, the punctuation but
    hyphens and underscores dropped, the spaces as hyphens."""
    kept = "".join(c for c in heading.lower() if c.isalnum() or c in " -_")
    return kept.replace(" ", "-")


def release_notes(changelog: str, tag: str) -> str | None:
    """The notes of *tag*'s section of *changelog*, or None without one."""
    lines = changelog.splitlines()
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if line == f"## {tag}" or line.startswith(f"## {tag} ")
        ),
        None,
    )
    if start is None:
        return None
    summary: list[str] = []
    titles: dict[str, list[str]] = {}
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if entry := _ENTRY.match(line):
            titles.setdefault(entry["kind"] or "Other", []).append(entry["title"])
        elif not titles and line != "---":
            summary.append(line)
    kinds = [k for k in _KINDS if k in titles] + [k for k in titles if k not in _KINDS]
    heading = lines[start].removeprefix("## ")
    parts = [
        "\n".join(summary).strip(),
        *(f"### {k}\n\n" + "\n".join(f"- {t}" for t in titles[k]) for k in kinds),
        f"Image: `{_IMAGE}:{tag}`",
        f"All changes: [CHANGELOG.md at {tag}]"
        f"({_REPOSITORY}/blob/{tag}/CHANGELOG.md#{anchor(heading)})",
    ]
    return "\n\n".join(part for part in parts if part) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the release tag, e.g. v0.5.0")
    tag = parser.parse_args().tag
    notes = release_notes(_CHANGELOG.read_text(encoding="utf-8"), tag)
    if notes is None:
        sys.exit(f"CHANGELOG.md has no section for {tag}")
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
