"""Record which build runs: ``src/scavengarr/build_info.json``.

Run at image build time, by ``Dockerfile.prod`` and by a Dockerfile that
fetches the source itself. ``build_identity()`` in
``infrastructure/version.py`` reads the file; an environment variable of the
same name beats it at runtime.

The commit comes from the build argument ``SCAVENGARR_COMMIT`` or, without
one, from the git directory's ``HEAD`` (a detached checkout holds the commit,
a branch checkout points at ``refs/heads/<branch>`` or ``packed-refs``), so
no git binary is needed; without either the file names only the time. The
time comes from ``SCAVENGARR_BUILT`` or the clock. Standard library only: it
runs before the app's venv exists.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

SHORT_COMMIT = 12
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DEFAULT_OUT = Path("src/scavengarr/build_info.json")


def commit_of(git_dir: Path) -> str | None:
    """The checked-out commit of a git directory, ``SHORT_COMMIT`` characters.

    ``None`` without a readable ``HEAD`` (no checkout, a worktree's pointer
    file) or a ref that resolves nowhere.
    """
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if _SHA_RE.match(head):
        return head[:SHORT_COMMIT]
    if not head.startswith("ref: "):
        return None
    ref = head.removeprefix("ref: ").strip()
    sha = _loose_ref(git_dir, ref) or _packed_ref(git_dir, ref)
    return sha[:SHORT_COMMIT] if sha else None


def _loose_ref(git_dir: Path, ref: str) -> str | None:
    try:
        sha = (git_dir / ref).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return sha if _SHA_RE.match(sha) else None


def _packed_ref(git_dir: Path, ref: str) -> str | None:
    try:
        lines = (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        sha, _, name = line.partition(" ")
        if name == ref and _SHA_RE.match(sha):
            return sha
    return None


def _given(value: str | None) -> str | None:
    """A build argument's value, or ``None`` for empty and ``unknown``."""
    if not value or value == "unknown":
        return None
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record the build (commit, time) for build_identity()"
    )
    parser.add_argument(
        "--git",
        type=Path,
        default=Path(".git"),
        help="git directory the commit is read from without SCAVENGARR_COMMIT",
    )
    parser.add_argument(
        "--out", type=Path, default=_DEFAULT_OUT, help="the file to write"
    )
    args = parser.parse_args(argv)

    info: dict[str, str] = {}
    commit = _given(os.environ.get("SCAVENGARR_COMMIT")) or commit_of(args.git)
    if commit:
        info["commit"] = commit
    info["built"] = _given(os.environ.get("SCAVENGARR_BUILT")) or datetime.now(
        UTC
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(info) + "\n", encoding="utf-8")
    print(f"build_info commit={commit or 'unknown'} built={info['built']} file={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
