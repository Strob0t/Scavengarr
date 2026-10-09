"""docker/build_info.py records the build: commit and time, at image build."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parents[3] / "docker" / "build_info.py"
_SHA = "7eeb50b63a57c0ffee0123456789abcdef012345"
_BUILT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_info_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_info = _load()


@pytest.fixture
def no_build_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCAVENGARR_COMMIT", raising=False)
    monkeypatch.delenv("SCAVENGARR_BUILT", raising=False)


class TestCommitOf:
    """The checked-out commit from a git directory, without a git binary."""

    def test_detached_head_holds_the_commit(self, tmp_path: Path) -> None:
        (tmp_path / "HEAD").write_text(f"{_SHA}\n")

        assert build_info.commit_of(tmp_path) == _SHA[:12]

    def test_branch_head_points_at_a_loose_ref(self, tmp_path: Path) -> None:
        (tmp_path / "HEAD").write_text("ref: refs/heads/staging\n")
        (tmp_path / "refs" / "heads").mkdir(parents=True)
        (tmp_path / "refs" / "heads" / "staging").write_text(f"{_SHA}\n")

        assert build_info.commit_of(tmp_path) == _SHA[:12]

    def test_branch_head_points_at_a_packed_ref(self, tmp_path: Path) -> None:
        (tmp_path / "HEAD").write_text("ref: refs/heads/staging\n")
        (tmp_path / "packed-refs").write_text(
            "# pack-refs with: peeled fully-peeled sorted\n"
            f"{'0' * 40} refs/heads/main\n"
            f"{_SHA} refs/heads/staging\n"
        )

        assert build_info.commit_of(tmp_path) == _SHA[:12]

    def test_none_without_a_git_directory(self, tmp_path: Path) -> None:
        assert build_info.commit_of(tmp_path / ".git") is None

    def test_none_for_a_worktree_pointer_file(self, tmp_path: Path) -> None:
        # A worktree's .git is a file ("gitdir: ..."), not a directory
        (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")

        assert build_info.commit_of(tmp_path / ".git") is None

    def test_none_for_an_unresolvable_ref(self, tmp_path: Path) -> None:
        (tmp_path / "HEAD").write_text("ref: refs/heads/gone\n")

        assert build_info.commit_of(tmp_path) is None


class TestMain:
    """Writes the JSON file build_identity() reads."""

    def test_build_arguments_win(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_COMMIT", "abc123abc123")
        monkeypatch.setenv("SCAVENGARR_BUILT", "2026-10-09T10:00:00Z")
        (tmp_path / "HEAD").write_text(f"{_SHA}\n")
        out = tmp_path / "out" / "build_info.json"

        assert build_info.main(["--git", str(tmp_path), "--out", str(out)]) == 0

        assert json.loads(out.read_text()) == {
            "commit": "abc123abc123",
            "built": "2026-10-09T10:00:00Z",
        }

    def test_git_directory_and_clock_without_arguments(
        self, tmp_path: Path, no_build_env: None
    ) -> None:
        (tmp_path / "HEAD").write_text(f"{_SHA}\n")
        out = tmp_path / "build_info.json"

        build_info.main(["--git", str(tmp_path), "--out", str(out)])

        data = json.loads(out.read_text())
        assert data["commit"] == _SHA[:12]
        assert _BUILT_RE.match(data["built"])

    def test_unknown_or_empty_argument_counts_as_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # docker-compose's `${SCAVENGARR_COMMIT:-unknown}` passes the word
        monkeypatch.setenv("SCAVENGARR_COMMIT", "unknown")
        monkeypatch.setenv("SCAVENGARR_BUILT", "")
        (tmp_path / "HEAD").write_text(f"{_SHA}\n")
        out = tmp_path / "build_info.json"

        build_info.main(["--git", str(tmp_path), "--out", str(out)])

        data = json.loads(out.read_text())
        assert data["commit"] == _SHA[:12]
        assert _BUILT_RE.match(data["built"])

    def test_only_the_time_without_a_commit(
        self, tmp_path: Path, no_build_env: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "build_info.json"

        build_info.main(["--git", str(tmp_path / ".git"), "--out", str(out)])

        data = json.loads(out.read_text())
        assert "commit" not in data
        assert _BUILT_RE.match(data["built"])
        assert "commit=unknown" in capsys.readouterr().out
