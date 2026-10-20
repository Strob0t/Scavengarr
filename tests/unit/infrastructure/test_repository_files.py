"""Repository files that are executed directly must be executable in git.

`core.fileMode=false` hides a missing executable bit locally, but a fresh
clone (and the Docker build context) gets the mode stored in git: a
100644 entrypoint makes the container exit 126, a 100644 Claude hook
exits 126 and lets every guarded command through.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from uvicorn.middleware.proxy_headers import _TrustedHosts

_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOWS = _ROOT / ".github" / "workflows"

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


def _dependabot_updates() -> list[dict[str, Any]]:
    return yaml.safe_load((_ROOT / ".github" / "dependabot.yml").read_text())["updates"]


def test_dependabot_opens_its_pull_requests_against_staging() -> None:
    """`main` takes merges from `staging` only (AGENTS.md §1): an update PR
    against `main` would bypass `staging`, so every ecosystem names it."""
    targets = {
        update["package-ecosystem"]: update.get("target-branch")
        for update in _dependabot_updates()
    }

    assert targets == {
        "pip": "staging",
        "github-actions": "staging",
        "docker": "staging",
        "pre-commit": "staging",
    }


def test_dependabot_waits_a_week_after_a_release() -> None:
    """A release pulled again within days never reaches `staging`. A security
    alert shows at once; its fix comes with the weekly update after the
    cooldown, or by hand."""
    for update in _dependabot_updates():
        assert update.get("cooldown") == {"default-days": 7}, update


def _workflow(name: str) -> dict[Any, Any]:
    """A workflow of `.github/workflows`; YAML 1.1 reads its key `on` as true."""
    return yaml.safe_load((_WORKFLOWS / name).read_text())


def test_ci_ends_in_the_one_check_the_main_ruleset_requires() -> None:
    """The `main` ruleset requires `CI passed`, not the matrix legs, so the
    matrix changes without the ruleset. It runs after a failed or cancelled
    leg too: a skipped required check counts as passed."""
    gate = _workflow("ci.yml")["jobs"]["passed"]

    assert gate["name"] == "CI passed"
    assert gate["needs"] == "check"
    assert gate["if"] == "always()"
    (step,) = gate["steps"]
    assert step["env"] == {"RESULT": "${{ needs.check.result }}"}
    assert step["run"] == 'test "$RESULT" = success'


@pytest.mark.parametrize("name", sorted(p.name for p in _WORKFLOWS.glob("*.yml")))
def test_workflow_scripts_take_their_values_through_env(name: str) -> None:
    """An expression inside `run:` is pasted into the script before the shell
    reads it, so a branch name or commit message could run as code; the
    values come through `env:` and the script quotes them."""
    for job in _workflow(name)["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), (job.get("name"), step)


def test_the_dependabot_merge_runs_no_pull_request_code() -> None:
    """The merge runs from `main` with write access after a pull request's
    CI run: it checks out nothing, uses no action and starts only for a green
    run of a Dependabot pull request of this repository."""
    workflow = _workflow("dependabot-merge.yml")
    (job,) = workflow["jobs"].values()

    assert workflow[True] == {
        "workflow_run": {
            "workflows": ["CI"],
            "types": ["completed"],
            "branches": ["dependabot/**"],
        }
    }
    assert workflow["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
        "actions": "write",
    }
    assert all("uses" not in step for step in job["steps"])
    for condition in (
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'pull_request'",
        "github.event.workflow_run.actor.login == 'dependabot[bot]'",
        "github.event.workflow_run.head_repository.full_name == github.repository",
    ):
        assert condition in job["if"]


_HEAD = "0123456789abcdef0123456789abcdef01234567"
_FAKE_GH = """#!/bin/bash
printf '%s\\n' "$*" >> "$FAKE_GH_CALLS"
case $1 in
  pr) if [[ $2 == list ]]; then cat "$FAKE_GH_PRS"; fi ;;
  api) cat "$FAKE_GH_COMMIT" ;;
esac
"""


def _dependabot_merge(
    tmp_path: Path, branch: str, message: str, *, pr_head: str = _HEAD
) -> list[str]:
    """The merge step's `gh` calls after a green CI run of the commit `_HEAD`,
    whose message is *message*; `gh` is a fake that answers the pull request
    list (pull request 7 of *branch* at *pr_head*, an older one beside it)
    and the commit."""
    (step,) = _workflow("dependabot-merge.yml")["jobs"]["merge"]["steps"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(_FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    prs = [
        {"number": 6, "headRefName": "dependabot/pip/old", "headRefOid": "f" * 40},
        {"number": 7, "headRefName": branch, "headRefOid": pr_head},
    ]
    (tmp_path / "prs.json").write_text(json.dumps(prs))
    (tmp_path / "commit.json").write_text(json.dumps({"commit": {"message": message}}))
    (tmp_path / "step.sh").write_text(step["run"])
    calls = tmp_path / "calls"
    calls.touch()

    subprocess.run(
        ["bash", "-e", str(tmp_path / "step.sh")],
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GH_REPO": "owner/repo",
            "HEAD_SHA": _HEAD,
            "FAKE_GH_CALLS": str(calls),
            "FAKE_GH_PRS": str(tmp_path / "prs.json"),
            "FAKE_GH_COMMIT": str(tmp_path / "commit.json"),
        },
        check=True,
        capture_output=True,
    )
    return calls.read_text().splitlines()


def _dependabot_message(*update_types: str) -> str:
    """A Dependabot commit message with one dependency per update type."""
    entries = "".join(
        f"- dependency-name: dep{n}\n"
        f"  dependency-version: 1.{n}.0\n"
        "  dependency-type: direct:production\n"
        f"  update-type: {update_type}\n"
        for n, update_type in enumerate(update_types)
    )
    return (
        "Bump the minor-and-patch group\n\n"
        f"---\nupdated-dependencies:\n{entries}...\n\n"
        "Signed-off-by: dependabot[bot] <support@github.com>\n"
    )


_NEEDS_GH = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="the merge step runs in bash with jq",
)


@_NEEDS_GH
def test_the_dependabot_merge_takes_a_green_minor_and_patch_update(
    tmp_path: Path,
) -> None:
    """The pull request whose head CI tested merges, at that head only, and
    CI and the image run on `staging`: a merge with `GITHUB_TOKEN` starts no
    push workflow."""
    calls = _dependabot_merge(
        tmp_path,
        "dependabot/pip/minor-and-patch-1a2b3c4d5e",
        _dependabot_message(
            "version-update:semver-minor", "version-update:semver-patch"
        ),
    )

    assert calls == [
        "pr list --author app/dependabot --base staging --state open"
        " --json number,headRefName,headRefOid",
        f"api repos/{{owner}}/{{repo}}/commits/{_HEAD}",
        f"pr merge 7 --merge --match-head-commit {_HEAD}",
        "workflow run ci.yml --ref staging",
        "workflow run image.yml --ref staging",
    ]


@_NEEDS_GH
@pytest.mark.parametrize(
    ("branch", "update_types"),
    [
        pytest.param(
            "dependabot/pip/minor-and-patch-1a2b3c4d5e",
            ("version-update:semver-patch", "version-update:semver-major"),
            id="a major update among them",
        ),
        pytest.param(
            "dependabot/pip/minor-and-patch-1a2b3c4d5e", (), id="no update type"
        ),
        pytest.param(
            "dependabot/docker/python-3.15.0-slim",
            ("version-update:semver-patch",),
            # A base image's next Python is a runtime change no CI leg builds
            id="a base image",
        ),
        pytest.param(
            "dependabot/github_actions/actions/checkout-7.1.0",
            ("version-update:semver-minor",),
            # GITHUB_TOKEN may not change a workflow file, a merge included
            id="an action",
        ),
    ],
)
def test_the_dependabot_merge_leaves_the_rest_to_the_maintainer(
    tmp_path: Path, branch: str, update_types: tuple[str, ...]
) -> None:
    calls = _dependabot_merge(tmp_path, branch, _dependabot_message(*update_types))

    assert not [call for call in calls if call.startswith(("pr merge", "workflow"))]


@_NEEDS_GH
def test_the_dependabot_merge_needs_the_tested_head(tmp_path: Path) -> None:
    """A pull request with a newer head waits for that head's CI run."""
    calls = _dependabot_merge(
        tmp_path,
        "dependabot/pip/minor-and-patch-1a2b3c4d5e",
        _dependabot_message("version-update:semver-patch"),
        pr_head="e" * 40,
    )

    assert not [call for call in calls if call.startswith(("api", "pr merge"))]


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


def test_the_image_healthcheck_asks_readiness() -> None:
    """Liveness answers over loopback while every outbound call fails: the
    image asks readiness, which tells a lost network (row 125)."""
    text = (_ROOT / "Dockerfile.prod").read_text()
    healthcheck = text[text.index("HEALTHCHECK") :].split("\n\n")[0]

    assert "/api/v1/readyz" in healthcheck
    assert "/api/v1/healthz" not in healthcheck


def test_agents_md_holds_rules_only() -> None:
    """Every session reads AGENTS.md whole, so it keeps the rules under 3,000
    words and links the details in `docs/` (the dev container's in
    `docs/how-to/dev-container.md`)."""
    words = len((_ROOT / "AGENTS.md").read_text(encoding="utf-8").split())

    assert words < 3000, f"AGENTS.md has {words} words: move details into docs/"


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
