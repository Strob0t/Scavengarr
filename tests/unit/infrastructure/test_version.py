"""The app reports one version: the installed package's (pyproject.toml)."""

from __future__ import annotations

from importlib.metadata import version

import pytest

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


class TestBuildIdentity:
    """The build: version, plus commit and build time from the image build."""

    def test_unknown_without_an_image_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SCAVENGARR_COMMIT", raising=False)
        monkeypatch.delenv("SCAVENGARR_BUILT", raising=False)

        assert build_identity() == {
            "version": APP_VERSION,
            "commit": "unknown",
            "built": "unknown",
        }

    def test_commit_and_build_time_from_the_image(
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
