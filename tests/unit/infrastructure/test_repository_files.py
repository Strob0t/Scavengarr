"""Repository files that are executed directly must be executable in git.

`core.fileMode=false` hides a missing executable bit locally, but a fresh
clone (and the Docker build context) gets the mode stored in git: a
100644 entrypoint makes the container exit 126, a 100644 Claude hook
exits 126 and lets every guarded command through.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]

_EXECUTED_DIRECTLY = [
    "docker/entrypoint.sh",
    ".claude/hooks/block-dangerous.sh",
    ".claude/hooks/format-and-lint.sh",
    ".devcontainer/setup.sh",
    ".devcontainer/sync-jdownloader.sh",
]


@pytest.mark.skipif(
    shutil.which("git") is None or not (_ROOT / ".git").exists(),
    reason="needs a git checkout",
)
@pytest.mark.parametrize("path", _EXECUTED_DIRECTLY)
def test_script_is_executable_in_git(path: str) -> None:
    out = subprocess.run(
        ["git", "ls-files", "-s", "--", path],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.split()[0] == "100755", f"{path} is stored as {out.split()[0]}"
