"""The app reports one version: the installed package's (pyproject.toml)."""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path

import pytest
import structlog.testing

from scavengarr.infrastructure import version as version_module
from scavengarr.infrastructure.config.schema import AppConfig
from scavengarr.infrastructure.version import (
    APP_USER_AGENT,
    APP_VERSION,
    build_identity,
)


def test_version_is_the_installed_package_version() -> None:
    assert APP_VERSION == version("scavengarr")


def test_user_agent_names_the_version_and_a_contact_url() -> None:
    """Wikidata answers 403 to a User-Agent without contact information."""
    assert APP_USER_AGENT == (
        f"Scavengarr/{APP_VERSION} (+https://github.com/Strob0t/Scavengarr)"
    )


def test_config_default_user_agent_is_the_app_user_agent() -> None:
    assert AppConfig().http_user_agent == APP_USER_AGENT


def test_build_info_file_lives_in_the_package() -> None:
    """Where docker/build_info.py writes: next to the package's modules."""
    assert version_module.BUILD_INFO_FILE == (
        Path(version_module.__file__).resolve().parents[1] / "build_info.json"
    )


class TestBuildIdentity:
    """The build: version, plus commit and build time from the image build."""

    @pytest.fixture(autouse=True)
    def no_build(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("SCAVENGARR_COMMIT", raising=False)
        monkeypatch.delenv("SCAVENGARR_BUILT", raising=False)
        monkeypatch.setattr(
            version_module, "BUILD_INFO_FILE", tmp_path / "build_info.json"
        )

    def test_unknown_without_an_image_build(self) -> None:
        assert build_identity() == {
            "version": APP_VERSION,
            "commit": "unknown",
            "built": "unknown",
        }

    def test_commit_and_build_time_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_COMMIT", "7eeb50b63a57")
        monkeypatch.setenv("SCAVENGARR_BUILT", "2026-10-06T18:00:00Z")

        assert build_identity() == {
            "version": APP_VERSION,
            "commit": "7eeb50b63a57",
            "built": "2026-10-06T18:00:00Z",
        }

    def test_empty_values_count_as_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A build without the build argument passes an empty value
        monkeypatch.setenv("SCAVENGARR_COMMIT", "")
        monkeypatch.setenv("SCAVENGARR_BUILT", "")

        assert build_identity()["commit"] == "unknown"
        assert build_identity()["built"] == "unknown"

    def test_from_the_file_the_image_build_wrote(self, tmp_path: Path) -> None:
        (tmp_path / "build_info.json").write_text(
            json.dumps({"commit": "a0cf586d1e2f", "built": "2026-10-09T10:00:00Z"})
        )

        assert build_identity() == {
            "version": APP_VERSION,
            "commit": "a0cf586d1e2f",
            "built": "2026-10-09T10:00:00Z",
        }

    def test_environment_beats_the_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / "build_info.json").write_text(
            json.dumps({"commit": "a0cf586d1e2f", "built": "2026-10-09T10:00:00Z"})
        )
        monkeypatch.setenv("SCAVENGARR_COMMIT", "7eeb50b63a57")

        identity = build_identity()

        assert identity["commit"] == "7eeb50b63a57"
        assert identity["built"] == "2026-10-09T10:00:00Z"

    def test_file_without_a_commit_names_only_the_time(self, tmp_path: Path) -> None:
        # A build with no .git and no argument records the time alone
        (tmp_path / "build_info.json").write_text(
            json.dumps({"built": "2026-10-09T10:00:00Z"})
        )

        identity = build_identity()

        assert identity["commit"] == "unknown"
        assert identity["built"] == "2026-10-09T10:00:00Z"

    def test_unreadable_file_counts_as_unknown_and_is_logged(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "build_info.json").write_text("{")

        with structlog.testing.capture_logs() as logs:
            identity = build_identity()

        assert identity["commit"] == "unknown"
        assert [log["event"] for log in logs] == ["build_info_unreadable"]
