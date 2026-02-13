# src/scavengarr/domain/plugins/plugin_schema.py
"""Pure domain models for plugin configuration (framework-free)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class HttpOverrides:
    """HTTP configuration overrides."""

    timeout_seconds: float | None = None
    follow_redirects: bool | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class AuthConfig:
    """Authentication configuration."""

    type: Literal["none", "basic", "form", "cookie"] = "none"
    username: str | None = None
    password: str | None = None
    login_url: str | None = None
    username_field: str | None = None
    password_field: str | None = None
    submit_selector: str | None = None
    username_env: str | None = None
    password_env: str | None = None
