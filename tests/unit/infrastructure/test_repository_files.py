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
import yaml
from uvicorn.middleware.proxy_headers import _TrustedHosts

_ROOT = Path(__file__).resolve().parents[3]

_EXECUTED_DIRECTLY = [
    "docker/entrypoint.sh",
    "scripts/basedpyright.sh",
    "scripts/worktree_venv.sh",
    ".claude/hooks/block-dangerous.sh",
    ".claude/hooks/format-and-lint.sh",
    ".claude/hooks/usage-guard.sh",
    ".claude/plugins/basedpyright-lsp/scripts/langserver.sh",
    ".devcontainer/setup.sh",
    ".devcontainer/sync-jdownloader.sh",
]


def test_compose_trusts_a_reverse_proxy_on_private_networks() -> None:
    """Behind Caddy, Traefik or nginx the app must honor X-Forwarded-Proto,
    or it writes http:// stream proxy and Torznab links (a redirect per
    request, mixed content in a browser). uvicorn trusts 127.0.0.1 only; the
    proxy reaches the container from the Docker gateway or the LAN."""
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    value = compose["services"]["scavengarr"]["environment"]["FORWARDED_ALLOW_IPS"]

    trusted = _TrustedHosts(value)

    for host in ("127.0.0.1", "172.17.0.1", "172.20.0.1", "192.168.1.10", "10.1.2.3"):
        assert host in trusted, host
    assert "8.8.8.8" not in trusted


def test_shipped_config_has_no_container_only_paths() -> None:
    """`poetry run start --config data/config.yaml` must work outside Docker.

    The image sets its own paths via env (SCAVENGARR_CACHE_DIR, ...); an
    absolute path in the YAML (e.g. /data/cache) is not writable locally.
    """
    config = yaml.safe_load((_ROOT / "data" / "config.yaml").read_text())
    for section, key in (("cache", "dir"), ("plugins", "plugin_dir")):
        value = str(config[section][key])
        assert not value.startswith("/"), f"{section}.{key} = {value}"


def test_the_build_context_holds_what_the_image_copies() -> None:
    """A `COPY` source in `.dockerignore` fails the build, and only the image
    workflow builds the image (no Docker in the dev container)."""
    ignored = {
        line.strip().rstrip("/")
        for line in (_ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    sources: list[str] = []
    for line in (_ROOT / "Dockerfile.prod").read_text().splitlines():
        words = line.split()
        if words[:1] == ["COPY"] and not any(w.startswith("--from") for w in words):
            sources += [w for w in words[1:-1] if not w.startswith("--")]

    assert "plugins/" in sources
    for source in sources:
        assert source.strip("/").split("/")[0] not in ignored, source


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
