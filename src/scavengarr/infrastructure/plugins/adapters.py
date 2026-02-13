"""Adapters to convert Pydantic validation models to domain models."""

from __future__ import annotations

from scavengarr.domain.plugins import plugin_schema as domain
from scavengarr.infrastructure.plugins import validation_schema as infra


def to_domain_http_overrides(
    pydantic: infra.HttpOverrides,
) -> domain.HttpOverrides:
    """Convert Pydantic HttpOverrides to domain model."""
    return domain.HttpOverrides(
        timeout_seconds=pydantic.timeout_seconds,
        follow_redirects=pydantic.follow_redirects,
        user_agent=pydantic.user_agent,
    )


def to_domain_auth_config(pydantic: infra.AuthConfig) -> domain.AuthConfig:
    """Convert Pydantic AuthConfig to domain model."""
    return domain.AuthConfig(
        type=pydantic.type,
        username=pydantic.username,
        password=pydantic.password,
        login_url=str(pydantic.login_url) if pydantic.login_url else None,
        username_field=pydantic.username_field,
        password_field=pydantic.password_field,
        submit_selector=pydantic.submit_selector,
        username_env=pydantic.username_env,
        password_env=pydantic.password_env,
    )
