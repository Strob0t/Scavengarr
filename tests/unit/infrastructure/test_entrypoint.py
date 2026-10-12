"""The container entrypoint seeds the config a volume hides.

`docker/entrypoint.sh` runs as in the image: with the default config beside
it, `DISPLAY` set (no Xvfb) and a stand-in `python` that only exits.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT = "server:\n  port: 7979\n"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")


@pytest.fixture
def entrypoint(tmp_path: Path) -> Path:
    """The entrypoint with the default config beside it, as in /app."""
    app = tmp_path / "app"
    app.mkdir()
    script = app / "entrypoint.sh"
    shutil.copy(_ROOT / "docker" / "entrypoint.sh", script)
    (app / "config.default.yaml").write_text(_DEFAULT)
    return script


def _start(script: Path, config: Path) -> subprocess.CompletedProcess[str]:
    bin_dir = script.parent.parent / "bin"
    bin_dir.mkdir(exist_ok=True)
    python = bin_dir / "python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "DISPLAY": ":1",
        "SCAVENGARR_CONFIG": str(config),
    }
    return subprocess.run(
        ["sh", str(script)], env=env, capture_output=True, text=True, check=True
    )


def test_first_start_seeds_the_config(entrypoint: Path, tmp_path: Path) -> None:
    config = tmp_path / "config" / "config.yaml"

    started = _start(entrypoint, config)

    assert config.read_text() == _DEFAULT
    assert str(config) in started.stderr


def test_an_existing_config_stays(entrypoint: Path, tmp_path: Path) -> None:
    config = tmp_path / "config" / "config.yaml"
    config.parent.mkdir()
    config.write_text("edited: true\n")

    started = _start(entrypoint, config)

    assert config.read_text() == "edited: true\n"
    assert started.stderr == ""


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_an_unwritable_config_dir_still_starts(
    entrypoint: Path, tmp_path: Path
) -> None:
    """A root-owned bind mount: the app starts on its defaults and says why."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_dir.chmod(0o555)

    started = _start(entrypoint, config_dir / "config.yaml")

    assert not (config_dir / "config.yaml").exists()
    assert "cannot write" in started.stderr
