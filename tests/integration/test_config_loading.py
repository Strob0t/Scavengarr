"""Integration tests for configuration loading with layered precedence.

Tests the real load_config() function with actual YAML files, environment
variables, and CLI overrides to verify precedence: defaults < YAML < ENV < CLI.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scavengarr.infrastructure.config.load import load_config
from scavengarr.infrastructure.config.schema import AppConfig

pytestmark = pytest.mark.integration


@pytest.fixture()
def yaml_config(tmp_path: Path) -> Path:
    """Write a minimal YAML config and return its path."""
    config = {
        "app_name": "scavengarr-test",
        "environment": "test",
        "plugins": {"plugin_dir": str(tmp_path / "plugins")},
        "http": {
            "timeout_seconds": 15.0,
            "user_agent": "TestAgent/1.0",
        },
        "logging": {"level": "DEBUG", "format": "console"},
        "cache": {"dir": str(tmp_path / "cache"), "ttl_seconds": 1800},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(config), encoding="utf-8")
    return path


class TestDefaultsOnly:
    """Load with no YAML, no ENV, no CLI — pure defaults."""

    def test_defaults_produce_valid_config(self) -> None:
        config = load_config()
        assert config.app_name == "scavengarr"
        assert config.environment == "dev"
        assert config.http_timeout_seconds == 30.0
        assert config.log_level == "INFO"
        assert config.log_format == "console"  # dev → console
        assert config.cache_ttl_seconds == 3600

    def test_schema_defaults_match_effective_defaults(self) -> None:
        """AppConfig() and load_config() must agree (single source of defaults)."""
        schema = AppConfig()
        loaded = load_config()
        assert schema.http_user_agent == loaded.http_user_agent
        assert schema.scoring.enabled == loaded.scoring.enabled
        assert (
            schema.stremio.max_concurrent_plugins
            == loaded.stremio.max_concurrent_plugins
        )
        assert schema.cache.directory == loaded.cache.directory

    def test_defaults_derive_log_format_from_environment(self) -> None:
        config = load_config(cli_overrides={"environment": "prod"})
        assert config.log_format == "json"


class TestYamlOverrides:
    """YAML values override defaults."""

    def test_yaml_overrides_defaults(self, yaml_config: Path) -> None:
        config = load_config(config_path=yaml_config)
        assert config.app_name == "scavengarr-test"
        assert config.environment == "test"
        assert config.http_timeout_seconds == 15.0
        assert config.http_user_agent == "TestAgent/1.0"
        assert config.log_level == "DEBUG"
        assert config.cache_ttl_seconds == 1800

    def test_yaml_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(config_path=tmp_path / "nonexistent.yaml")

    def test_yaml_partial_override_preserves_defaults(self, tmp_path: Path) -> None:
        """YAML that only sets http.timeout_seconds keeps other defaults."""
        config_data = {"http": {"timeout_seconds": 99.0}}
        path = tmp_path / "partial.yaml"
        path.write_text(yaml.dump(config_data), encoding="utf-8")

        config = load_config(config_path=path)
        assert config.http_timeout_seconds == 99.0
        assert config.http_follow_redirects is True  # default preserved
        assert config.app_name == "scavengarr"  # default preserved


class TestEnvOverrides:
    """Environment variables override YAML and defaults."""

    def test_env_overrides_yaml(
        self, yaml_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_LOG_LEVEL", "WARNING")
        monkeypatch.setenv("SCAVENGARR_HTTP_TIMEOUT_SECONDS", "60.0")

        config = load_config(config_path=yaml_config)
        assert config.log_level == "WARNING"
        assert config.http_timeout_seconds == 60.0
        # YAML values not overridden by ENV stay
        assert config.app_name == "scavengarr-test"

    def test_env_overrides_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCAVENGARR_ENVIRONMENT", "prod")

        config = load_config()
        assert config.environment == "prod"
        assert config.log_format == "json"  # prod → json

    def test_env_rate_limit_and_retry_vars_apply(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_RATE_LIMIT_ADAPTIVE", "false")
        monkeypatch.setenv("SCAVENGARR_RATE_LIMIT_MIN_RPS", "0.25")
        monkeypatch.setenv("SCAVENGARR_RATE_LIMIT_MAX_RPS", "12.5")
        monkeypatch.setenv("SCAVENGARR_HTTP_RETRY_MAX_ATTEMPTS", "7")
        monkeypatch.setenv("SCAVENGARR_HTTP_RETRY_BACKOFF_BASE", "2.5")
        monkeypatch.setenv("SCAVENGARR_HTTP_RETRY_MAX_BACKOFF", "45")

        config = load_config()
        assert config.rate_limit_adaptive is False
        assert config.rate_limit_min_rps == 0.25
        assert config.rate_limit_max_rps == 12.5
        assert config.http_retry_max_attempts == 7
        assert config.http_retry_backoff_base == 2.5
        assert config.http_retry_max_backoff == 45.0

    def test_env_cache_backend_vars_apply(
        self, yaml_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_CACHE_BACKEND", "redis")
        monkeypatch.setenv("SCAVENGARR_CACHE_REDIS_URL", "redis://redis:6379/1")
        monkeypatch.setenv("SCAVENGARR_CACHE_MAX_CONCURRENT", "25")

        config = load_config(config_path=yaml_config)
        assert config.cache.backend == "redis"
        assert config.cache.redis_url == "redis://redis:6379/1"
        assert config.cache.max_concurrent == 25


class TestCliOverrides:
    """CLI overrides beat everything (highest precedence)."""

    def test_cli_overrides_yaml_and_env(
        self, yaml_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_LOG_LEVEL", "WARNING")

        config = load_config(
            config_path=yaml_config,
            cli_overrides={"log_level": "ERROR"},
        )
        assert config.log_level == "ERROR"

    def test_cli_overrides_with_sectioned_format(self, yaml_config: Path) -> None:
        config = load_config(
            config_path=yaml_config,
            cli_overrides={"http": {"timeout_seconds": 5.0}},
        )
        assert config.http_timeout_seconds == 5.0

    def test_cli_overrides_defaults_without_yaml(self) -> None:
        config = load_config(
            cli_overrides={"app_name": "custom-app", "environment": "prod"},
        )
        assert config.app_name == "custom-app"
        assert config.environment == "prod"
        assert config.log_format == "json"


class TestPlaywrightBrowserSettings:
    """playwright.headless / playwright.browser_fallback (anti-bot defaults)."""

    def test_defaults_headful_with_browser_fallback(self) -> None:
        config = load_config()
        assert config.playwright_headless is False
        assert config.playwright_browser_fallback is True

    def test_yaml_disables_browser_fallback(self, tmp_path: Path) -> None:
        path = tmp_path / "pw.yaml"
        path.write_text(
            yaml.dump({"playwright": {"browser_fallback": False}}), encoding="utf-8"
        )

        config = load_config(config_path=path)

        assert config.playwright_browser_fallback is False
        assert config.playwright_headless is False  # default preserved

    def test_env_overrides_browser_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_PLAYWRIGHT_BROWSER_FALLBACK", "false")
        assert load_config().playwright_browser_fallback is False
