"""The app reports one version: the installed package's (pyproject.toml)."""

from __future__ import annotations

from importlib.metadata import version

from scavengarr.infrastructure.config.schema import AppConfig
from scavengarr.infrastructure.version import APP_USER_AGENT, APP_VERSION


def test_version_is_the_installed_package_version() -> None:
    assert APP_VERSION == version("scavengarr")


def test_user_agent_names_the_version_and_a_contact_url() -> None:
    """Wikidata answers 403 to a User-Agent without contact information."""
    assert APP_USER_AGENT == (
        f"Scavengarr/{APP_VERSION} (+https://github.com/Strob0t/Scavengarr)"
    )


def test_config_default_user_agent_is_the_app_user_agent() -> None:
    assert AppConfig().http_user_agent == APP_USER_AGENT
