"""Integration tests for configuration loading with layered precedence.

Tests the real load_config() function with actual YAML files, environment
variables, and CLI overrides to verify precedence: defaults < YAML < ENV < CLI.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from scavengarr.infrastructure.config.load import (
    _FLAT_KEYS,
    _unknown_keys,
    changed_values,
    load_config,
)
from scavengarr.infrastructure.config.schema import AppConfig, ConfigSource

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

    def test_default_user_agent_names_a_contact(self) -> None:
        """Wikidata (German titles without a TMDB key) answers 403 to a
        User-Agent without contact information (Wikimedia robot policy)."""
        assert "(+https://" in load_config().http_user_agent

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


class TestShippedConfig:
    """data/config.yaml, the config of the Docker image."""

    _PATH = Path(__file__).resolve().parents[2] / "data" / "config.yaml"

    def test_dead_cineby_is_disabled(self) -> None:
        """Its API host (db.videasy.net) is gone; enabled: true brings it back."""
        overrides = load_config(config_path=self._PATH).plugins.overrides
        assert overrides["cineby"].enabled is False

    def test_every_key_is_known(self) -> None:
        """The image's config raises no config_unknown_keys warning."""
        assert load_config(config_path=self._PATH).source.unknown_keys == ()


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

    def test_crawljob_ttl_from_yaml(self, tmp_path: Path) -> None:
        assert load_config().cache.crawljob_ttl_seconds == 3600
        path = tmp_path / "ttl.yaml"
        path.write_text(yaml.dump({"cache": {"crawljob_ttl_seconds": 7200}}))

        assert load_config(config_path=path).cache.crawljob_ttl_seconds == 7200

    def test_removed_probe_keys_are_ignored(self, tmp_path: Path) -> None:
        """Configs from before the stream-time probe was removed still load."""
        path = tmp_path / "old.yaml"
        old_keys = {
            "probe_at_stream_time": True,
            "probe_timeout_seconds": 5.0,
            "probe_stealth_enabled": True,
            "probe_stealth_concurrency": 5,
        }
        path.write_text(yaml.dump({"stremio": old_keys}))

        config = load_config(config_path=path)

        assert not hasattr(config.stremio, "probe_at_stream_time")

    def test_crawljob_ttl_must_be_positive(self, tmp_path: Path) -> None:
        path = tmp_path / "ttl.yaml"
        path.write_text(yaml.dump({"cache": {"crawljob_ttl_seconds": 0}}))

        with pytest.raises(ValueError, match="crawljob_ttl_seconds"):
            load_config(config_path=path)


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

    def test_unprefixed_cache_vars_are_not_read(
        self, yaml_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The cache section used to be a BaseSettings with env_prefix CACHE_
        # and read these below the YAML (the docs said it did not)
        monkeypatch.setenv("CACHE_MAX_CONCURRENT", "42")
        monkeypatch.setenv("CACHE_SEARCH_TTL_SECONDS", "11")
        monkeypatch.setenv("CACHE_REDIS_URL", "redis://elsewhere:6379/9")

        config = load_config(config_path=yaml_config)

        assert config.cache.max_concurrent == 10
        assert config.cache.search_ttl_seconds == 900
        assert config.cache.redis_url == "redis://localhost:6379/0"


class TestTelemetrySettings:
    """Tracing is off unless an OTLP/HTTP endpoint is set."""

    def test_tracing_off_by_default(self) -> None:
        assert load_config().telemetry.tracing_endpoint is None

    def test_endpoint_from_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.dump({"telemetry": {"tracing_endpoint": "http://tempo:4318"}}),
            encoding="utf-8",
        )

        assert load_config(config_path=path).telemetry.tracing_endpoint == (
            "http://tempo:4318"
        )

    def test_endpoint_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "SCAVENGARR_TELEMETRY_TRACING_ENDPOINT", "http://192.168.1.2:4318"
        )

        assert load_config().telemetry.tracing_endpoint == "http://192.168.1.2:4318"

    @pytest.mark.parametrize("empty", ["", "  "])
    def test_an_empty_endpoint_turns_tracing_off(
        self, monkeypatch: pytest.MonkeyPatch, empty: str
    ) -> None:
        """A compose file keeps tracing optional with
        ``${TRACING_ENDPOINT:-}``; the empty value stopped the app at start
        (code review, 2026-10-06)."""
        monkeypatch.setenv("SCAVENGARR_TELEMETRY_TRACING_ENDPOINT", empty)

        assert load_config().telemetry.tracing_endpoint is None

    def test_endpoint_must_be_http(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.dump({"telemetry": {"tracing_endpoint": "tempo:4318"}}),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="tracing_endpoint"):
            load_config(config_path=path)


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

    def test_solver_url_defaults_to_none(self) -> None:
        assert load_config().playwright_solver_url is None

    def test_yaml_sets_solver_url(self, tmp_path: Path) -> None:
        path = tmp_path / "pw.yaml"
        path.write_text(
            yaml.dump({"playwright": {"solver_url": "http://byparr:8191"}}),
            encoding="utf-8",
        )
        assert load_config(config_path=path).playwright_solver_url == (
            "http://byparr:8191"
        )

    def test_env_sets_solver_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCAVENGARR_PLAYWRIGHT_SOLVER_URL", "http://byparr:8191")
        assert load_config().playwright_solver_url == "http://byparr:8191"


@pytest.fixture()
def no_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """SCAVENGARR_* values of the shell would count as changed values."""
    for name in list(os.environ):
        if name.upper().startswith("SCAVENGARR_"):
            monkeypatch.delenv(name)


@pytest.mark.usefixtures("no_env_overrides")
class TestStartupReport:
    """What the startup log tells about the configuration: the values away
    from the defaults (config_effective) and the YAML keys no field accepts
    (config_unknown_keys)."""

    @staticmethod
    def _load(tmp_path: Path, data: dict[str, Any]) -> AppConfig:
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(data), encoding="utf-8")
        return load_config(config_path=path)

    def test_defaults_change_nothing(self) -> None:
        config = load_config()

        assert changed_values(config) == {}
        assert config.source == ConfigSource()

    def test_changed_values_are_the_non_defaults(self, tmp_path: Path) -> None:
        config = self._load(
            tmp_path,
            {
                "http": {"timeout_seconds": 15.0},
                "stremio": {"max_concurrent_plugins": 15},
                "plugins": {"overrides": {"cineby": {"enabled": False}}},
            },
        )

        # A dict-typed field shows its keys only
        assert changed_values(config) == {
            "http_timeout_seconds": 15.0,
            "stremio.max_concurrent_plugins": 15,
            "plugins.overrides": ["cineby"],
        }

    def test_secrets_are_masked(self, tmp_path: Path) -> None:
        config = self._load(tmp_path, {"tmdb_api_key": "0123456789abcdef"})

        assert changed_values(config) == {"tmdb_api_key": "***"}

    def test_paths_are_strings(self, tmp_path: Path) -> None:
        config = self._load(tmp_path, {"cache": {"dir": "/srv/cache"}})

        assert changed_values(config) == {
            "cache.directory": "/srv/cache",
            "cache_dir": "/srv/cache",
        }

    def test_source_names_the_file(self, tmp_path: Path) -> None:
        config = self._load(tmp_path, {})

        assert config.source == ConfigSource(file=tmp_path / "config.yaml")

    def test_unknown_keys_are_collected(self, tmp_path: Path) -> None:
        config = self._load(
            tmp_path,
            {
                "htttp": {"timeout_seconds": 1.0},  # misspelled section
                "http": {"timeout_secs": 1.0, "rate_limit_rps": 2.0},
                "stremio": {"probe_at_stream_time": True, "max_probe_count": 80},
                "plugins": {"overrides": {"anything": {"whatever": 1}}},
                "cache": {"dir": "/srv/cache"},  # CacheConfig.directory
                "log_level": "INFO",  # flat key, mapped to logging.level
                "scoring_enabled": False,  # flat scoring keys: env only
            },
        )

        assert config.source.unknown_keys == (
            "http.timeout_secs",
            "htttp",
            "scoring_enabled",
            "stremio.probe_at_stream_time",
        )

    def test_every_flat_key_lands_on_a_known_key(self) -> None:
        """The loader's flat-key map and the schema agree."""
        sectioned: dict[str, dict[str, Any]] = {}
        for section, key in _FLAT_KEYS.values():
            sectioned.setdefault(section, {})[key] = 1

        assert _unknown_keys(sectioned) == ()


_REPO = Path(__file__).resolve().parents[2]


def _production_env() -> dict[str, str]:
    """The ``environment:`` entries of the production example in the docs."""
    doc = (_REPO / "docs/features/configuration.md").read_text(encoding="utf-8")
    block = doc.split("<!-- production-env:start -->")[1]
    block = block.split("<!-- production-env:end -->")[0]
    body = block.split("```yaml")[1].split("```")[0]
    entries = yaml.safe_load(body)["environment"]
    return {name: str(value) for name, value in entries.items()}


@pytest.mark.usefixtures("no_env_overrides")
class TestSectionedEnv:
    """Every setting of a section reads ``SCAVENGARR_<SECTION>_<KEY>``."""

    def test_a_section_key_reaches_its_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_STREMIO_PLUGIN_TIMEOUT_SECONDS", "20")
        monkeypatch.setenv("SCAVENGARR_STREMIO_VERIFY_STREAMS", "false")
        monkeypatch.setenv("SCAVENGARR_CACHE_SEARCH_TTL_SECONDS", "1800")
        monkeypatch.setenv("SCAVENGARR_LOGGING_LEVEL", "DEBUG")

        config = load_config()

        assert config.stremio.plugin_timeout_seconds == 20.0
        assert config.stremio.verify_streams is False
        assert config.cache.search_ttl_seconds == 1800
        assert config.log_level == "DEBUG"

    def test_names_are_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("scavengarr_stremio_resolve_target_count", "3")

        assert load_config().stremio.resolve_target_count == 3

    def test_top_level_keys_and_json_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_VALIDATION_MAX_CONCURRENT", "30")
        monkeypatch.setenv(
            "SCAVENGARR_PLUGINS_OVERRIDES", '{"cineby": {"enabled": false}}'
        )
        monkeypatch.setenv("SCAVENGARR_STREMIO_HOSTER_SCORES", '{"voe": 9}')

        config = load_config()

        assert config.validation_max_concurrent == 30
        assert config.plugins.overrides["cineby"].enabled is False
        assert config.stremio.hoster_scores == {"voe": 9}

    def test_env_overrides_the_yaml_value(
        self, yaml_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_HTTP_TIMEOUT_RESOLVE_SECONDS", "7")
        monkeypatch.setenv("SCAVENGARR_CACHE_TTL_SECONDS", "60")

        config = load_config(config_path=yaml_config)

        assert config.http_timeout_resolve_seconds == 7.0
        assert config.cache_ttl_seconds == 60  # the YAML said 1800

    def test_the_flat_alias_wins_and_the_conflict_is_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_RATE_LIMIT_REQUESTS_PER_SECOND", "10")
        monkeypatch.setenv("SCAVENGARR_HTTP_RATE_LIMIT_RPS", "3")

        config = load_config()

        assert config.rate_limit_requests_per_second == 10.0
        assert config.source.env_conflicts == (
            (
                "SCAVENGARR_RATE_LIMIT_REQUESTS_PER_SECOND",
                "SCAVENGARR_HTTP_RATE_LIMIT_RPS",
            ),
        )

    def test_agreeing_forms_are_no_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("SCAVENGARR_LOGGING_LEVEL", "DEBUG")

        assert load_config().source.env_conflicts == ()

    def test_a_misspelled_key_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_STREMIO_PLUGIN_TIMEOUT_SECOND", "20")
        # Not settings: plugin credentials, the build identity, the config path
        monkeypatch.setenv("SCAVENGARR_BOERSE_USERNAME", "user")
        monkeypatch.setenv("SCAVENGARR_COMMIT", "abc")
        monkeypatch.setenv("SCAVENGARR_CONFIG", "/nowhere.yaml")

        config = load_config()

        assert config.stremio.plugin_timeout_seconds == 30.0
        assert config.source.unknown_env == (
            "SCAVENGARR_STREMIO_PLUGIN_TIMEOUT_SECOND",
        )

    def test_a_flat_name_read_as_sectioned_is_the_same_setting(self) -> None:
        """SCAVENGARR_HTTP_HTTP2 is both a flat alias and a sectioned name: both
        readings must set the same key, or one variable would set two values."""
        for flat, (section, key) in _FLAT_KEYS.items():
            if flat.startswith(f"{section}_"):
                assert flat.removeprefix(f"{section}_") == key, flat

    def test_each_value_names_its_layer(
        self, yaml_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_STREMIO_PLUGIN_TIMEOUT_SECONDS", "20")
        monkeypatch.setenv("SCAVENGARR_HTTP_TIMEOUT_SECONDS", "45")

        config = load_config(
            config_path=yaml_config, cli_overrides={"log_level": "ERROR"}
        )

        sources = config.source.value_sources
        assert sources["app_name"] == "yaml"
        assert sources["cache_ttl_seconds"] == "yaml"
        assert sources["stremio.plugin_timeout_seconds"] == "env"
        assert sources["http_timeout_seconds"] == "env"  # the YAML said 15
        assert sources["log_level"] == "cli"  # the YAML said DEBUG

    def test_production_example_matches_the_seed_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The docs' environment: entries give what the image's seed file gives."""
        seeded = changed_values(load_config(config_path=_REPO / "data/config.yaml"))
        for name, value in _production_env().items():
            monkeypatch.setenv(name, value)

        config = load_config()

        assert changed_values(config) == seeded
        assert config.source.unknown_env == ()
        assert config.source.env_conflicts == ()

    def test_compose_file_shows_the_production_example(self) -> None:
        compose = (_REPO / "docker-compose.yml").read_text(encoding="utf-8")
        commented = {
            line.strip().removeprefix("#").strip()
            for line in compose.splitlines()
            if line.strip().startswith("#")
        }

        for name, value in _production_env().items():
            assert any(
                line.startswith(f"{name}:") and value in line for line in commented
            ), name
