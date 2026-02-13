"""Pydantic validation models for plugin configuration."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, HttpUrl, model_validator


class HttpOverrides(BaseModel):
    timeout_seconds: float | None = None
    follow_redirects: bool | None = None
    user_agent: str | None = None


class AuthConfig(BaseModel):
    type: Literal["none", "basic", "form", "cookie"] = "none"

    username: str | None = None
    password: str | None = None

    login_url: HttpUrl | None = None
    username_field: str | None = None
    password_field: str | None = None
    submit_selector: str | None = None

    username_env: str | None = None
    password_env: str | None = None

    @model_validator(mode="after")
    def _resolve_env_credentials(self) -> "AuthConfig":
        """Resolve credentials from env vars when *_env fields are set."""
        if self.username_env and not self.username:
            val = os.environ.get(self.username_env)
            if val:
                self.username = val
        if self.password_env and not self.password:
            val = os.environ.get(self.password_env)
            if val:
                self.password = val
        return self

    def _validate_basic_auth(self) -> None:
        """Validate basic auth has username and password."""
        if not self.username or not self.password:
            raise ValueError("basic auth requires 'username' and 'password'")

    def _validate_form_auth(self) -> None:
        """Validate form auth has all required fields."""
        required_fields: dict[str, object] = {
            "login_url": self.login_url,
            "username_field": self.username_field,
            "password_field": self.password_field,
            "submit_selector": self.submit_selector,
            "username": self.username,
            "password": self.password,
        }
        missing = [k for k, v in required_fields.items() if not v]
        if missing:
            raise ValueError(f"form auth requires {', '.join(missing)}")

    @model_validator(mode="after")
    def _validate_auth_requirements(self) -> "AuthConfig":
        if self.type == "none":
            return self
        if self.type == "basic":
            self._validate_basic_auth()
        elif self.type == "form":
            self._validate_form_auth()
        return self
