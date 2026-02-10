"""Tests for AuthConfig env var resolution in validation schema."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from scavengarr.infrastructure.plugins.validation_schema import AuthConfig


class TestAuthEnvResolution:
    def test_username_env_resolves_from_environment(self) -> None:
        with patch.dict(os.environ, {"TEST_USER": "alice"}):
            auth = AuthConfig(
                type="basic",
                username_env="TEST_USER",
                password="secret",
            )
        assert auth.username == "alice"

    def test_password_env_resolves_from_environment(self) -> None:
        with patch.dict(os.environ, {"TEST_PASS": "secret123"}):
            auth = AuthConfig(
                type="basic",
                username="alice",
                password_env="TEST_PASS",
            )
        assert auth.password == "secret123"

    def test_both_env_fields_resolve(self) -> None:
        with patch.dict(os.environ, {"TEST_USER": "alice", "TEST_PASS": "secret123"}):
            auth = AuthConfig(
                type="basic",
                username_env="TEST_USER",
                password_env="TEST_PASS",
            )
        assert auth.username == "alice"
        assert auth.password == "secret123"

    def test_explicit_value_takes_precedence_over_env(self) -> None:
        with patch.dict(os.environ, {"TEST_USER": "env_user"}):
            auth = AuthConfig(
                type="basic",
                username="explicit_user",
                username_env="TEST_USER",
                password="secret",
            )
        assert auth.username == "explicit_user"

    def test_missing_env_var_leaves_field_none(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            auth = AuthConfig(
                type="none",
                username_env="NONEXISTENT_VAR",
            )
        assert auth.username is None

    def test_env_resolution_enables_form_auth(self) -> None:
        with patch.dict(os.environ, {"FORM_USER": "alice", "FORM_PASS": "secret"}):
            auth = AuthConfig(
                type="form",
                username_env="FORM_USER",
                password_env="FORM_PASS",
                login_url="https://example.com/login",
                username_field="user",
                password_field="pass",
                submit_selector="#submit",
            )
        assert auth.username == "alice"
        assert auth.password == "secret"

    def test_form_auth_fails_without_env_or_explicit(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="form auth requires"):
                AuthConfig(
                    type="form",
                    username_env="MISSING_VAR",
                    password_env="MISSING_VAR",
                    login_url="https://example.com/login",
                    username_field="user",
                    password_field="pass",
                    submit_selector="#submit",
                )
