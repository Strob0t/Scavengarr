"""Tests for plugin schema domain models."""

from __future__ import annotations

from scavengarr.domain.plugins import (
    AuthConfig,
    HttpOverrides,
)


class TestAuthConfig:
    def test_default_type_none(self) -> None:
        a = AuthConfig()
        assert a.type == "none"
        assert a.username is None
        assert a.password is None

    def test_env_fields_default_none(self) -> None:
        a = AuthConfig()
        assert a.username_env is None
        assert a.password_env is None

    def test_env_fields_stored(self) -> None:
        a = AuthConfig(
            username_env="MY_USER_ENV",
            password_env="MY_PASS_ENV",
        )
        assert a.username_env == "MY_USER_ENV"
        assert a.password_env == "MY_PASS_ENV"


class TestHttpOverrides:
    def test_defaults_all_none(self) -> None:
        h = HttpOverrides()
        assert h.timeout_seconds is None
        assert h.follow_redirects is None
        assert h.user_agent is None
