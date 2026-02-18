"""Pydantic configuration models with validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    AliasPath,
    BaseModel,
    Field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["json", "console"]


class ScoringConfig(BaseModel):
    """Background plugin scoring and probing configuration."""

    enabled: bool = Field(
        default=False,
        description="Enable background plugin scoring and probing.",
    )
    health_halflife_days: float = Field(
        default=2.0,
        description="EWMA half-life for health probes (days).",
    )
    search_halflife_weeks: float = Field(
        default=2.0,
        description="EWMA half-life for search probes (weeks).",
    )
    health_interval_hours: float = Field(
        default=24.0,
        description="Interval between health probe cycles (hours).",
    )
    search_runs_per_week: int = Field(
        default=2,
        description="Number of search probe runs per week per plugin.",
    )
    health_timeout_seconds: float = Field(
        default=5.0,
        description="Timeout for health probe requests (seconds).",
    )
    search_timeout_seconds: float = Field(
        default=10.0,
        description="Timeout for mini-search probe requests (seconds).",
    )
    search_max_items: int = Field(
        default=20,
        description="Max items per mini-search probe.",
    )
    health_concurrency: int = Field(
        default=5,
        description="Max parallel health probes.",
    )
    search_concurrency: int = Field(
        default=3,
        description="Max parallel search probes.",
    )
    score_ttl_days: int = Field(
        default=30,
        description="TTL for persisted score snapshots (days).",
    )
    w_health: float = Field(
        default=0.4,
        description="Weight of health score in composite.",
    )
    w_search: float = Field(
        default=0.6,
        description="Weight of search score in composite.",
    )


class PluginOverride(BaseModel):
    """Per-plugin configuration overrides."""

    timeout: float | None = Field(
        default=None,
        description="Override plugin timeout (seconds).",
    )
    max_concurrent: int | None = Field(
        default=None,
        description="Override plugin max concurrent requests.",
    )
    max_results: int | None = Field(
        default=None,
        description="Override plugin max results.",
    )
    enabled: bool = Field(
        default=True,
        description="Set False to disable this plugin entirely.",
    )


class PluginsConfig(BaseModel):
    """Plugin system configuration."""

    plugin_dir: Path = Field(
        default=Path("./plugins"),
        description="Directory containing Python plugins.",
    )
    overrides: dict[str, PluginOverride] = Field(
        default_factory=dict,
        description="Per-plugin setting overrides keyed by plugin name.",
    )


def _normalize_path(value: Any) -> Path:
    """
    Normalize a path-like value without causing filesystem side-effects.

    This function MUST NOT create directories or files.
    """
    if isinstance(value, Path):
        return value.expanduser()
    if isinstance(value, str):
        return Path(value).expanduser()
    raise TypeError(f"Expected path-like value, got: {type(value)!r}")


class CacheConfig(BaseSettings):
    """Cache configuration (backend-agnostic)."""

    backend: Literal["diskcache", "redis"] = Field(
        default="diskcache",
        description="Cache backend: 'diskcache' (SQLite) or 'redis'",
    )

    # Diskcache settings
    directory: Path = Field(
        default=Path("./cache/scavengarr"),
        alias="dir",
        description="Diskcache SQLite DB path",
    )

    # Redis settings
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL (only when backend=redis)",
    )

    # Shared settings
    ttl_seconds: int = Field(
        default=3600,
        description="Default TTL for cache entries (seconds)",
    )
    search_ttl_seconds: int = Field(
        default=900,
        description="TTL for cached search results (seconds). 0 = disabled.",
    )
    max_concurrent: int = Field(
        default=10,
        description="Max parallel cache ops (semaphore limit)",
    )

    model_config = SettingsConfigDict(
        env_prefix="CACHE_",  # Env vars: CACHE_BACKEND, CACHE_REDIS_URL, ...
        case_sensitive=False,
    )


class StremioConfig(BaseModel):
    """Configuration for Stremio addon and stream sorting.

    All values configurable via YAML (stremio section) or ENV vars.
    """

    preferred_language: str = Field(
        default="de",
        description="Preferred audio language code for stream ranking.",
    )

    language_scores: dict[str, int] = Field(
        default={
            "de": 1000,
            "de-sub": 500,
            "en-sub": 200,
            "en": 150,
        },
        description="Language ranking scores (higher = preferred).",
    )
    default_language_score: int = Field(
        default=100,
        description="Score for unknown/undetected languages.",
    )

    quality_multiplier: int = Field(
        default=10,
        description="Multiplier for quality value in ranking score.",
    )

    hoster_scores: dict[str, int] = Field(
        default={
            "supervideo": 5,
            "voe": 4,
            "filemoon": 3,
            "streamtape": 2,
            "doodstream": 1,
        },
        description="Hoster reliability bonus (tie-breaker).",
    )

    max_concurrent_plugins: int = Field(
        default=10,
        description="Max parallel plugin searches for stream resolution.",
    )

    max_concurrent_playwright: int = Field(
        default=5,
        description=(
            "Upper bound for parallel Playwright plugin searches on the "
            "shared browser. The actual concurrency is dynamically capped "
            "at min(pw_plugin_count, this value) per request."
        ),
    )

    max_concurrent_plugins_auto: bool = Field(
        default=True,
        description=(
            "Auto-tune max_concurrent_plugins based on host CPU and memory. "
            "When enabled, overrides max_concurrent_plugins at startup."
        ),
    )

    auto_tune_all: bool = Field(
        default=True,
        description=(
            "Auto-tune ALL concurrency parameters (plugins, Playwright, probes, "
            "validation) based on detected container/host resources via cgroups. "
            "Supersedes max_concurrent_plugins_auto."
        ),
    )

    max_results_per_plugin: int = Field(
        default=100,
        description=(
            "Max results per plugin in Stremio search. Limits pagination "
            "to reduce response time. Torznab uses the plugin default (1000)."
        ),
    )

    plugin_timeout_seconds: float = Field(
        default=30.0,
        description="Per-plugin timeout in seconds for stream search.",
    )

    title_match_threshold: float = Field(
        default=0.7,
        description="Minimum title similarity score to keep a stream result.",
    )

    title_year_bonus: float = Field(
        default=0.2,
        description="Score bonus when result year matches reference year.",
    )
    title_year_penalty: float = Field(
        default=0.3,
        description="Score penalty when result year does not match reference year.",
    )
    title_sequel_penalty: float = Field(
        default=0.35,
        description="Score penalty when result has sequel number that reference lacks.",
    )
    title_year_tolerance_movie: int = Field(
        default=1,
        description="Allowed year difference for movies (±N years).",
    )
    title_year_tolerance_series: int = Field(
        default=3,
        description="Allowed year difference for series (±N years).",
    )

    stream_link_ttl_seconds: int = Field(
        default=7200,
        description="TTL for cached stream links (seconds). Default 2h.",
    )

    probe_at_stream_time: bool = Field(
        default=True,
        description="Probe hoster URLs at /stream time to filter dead links.",
    )
    probe_concurrency: int = Field(
        default=10,
        description="Max parallel hoster probes at stream time.",
    )
    probe_timeout_seconds: float = Field(
        default=10.0,
        description="Per-URL probe timeout in seconds.",
    )
    max_probe_count: int = Field(
        default=50,
        description="Max streams to probe/resolve at stream time (top-ranked first).",
    )
    resolve_target_count: int = Field(
        default=15,
        description=(
            "Target number of successfully resolved video streams. "
            "Resolution stops early once this many genuine video URLs "
            "have been extracted, cancelling remaining resolve tasks. "
            "Set to 0 to disable early-stop (resolve all streams)."
        ),
    )

    probe_stealth_enabled: bool = Field(
        default=True,
        description="Use Playwright Stealth to bypass Cloudflare for probed URLs.",
    )
    probe_stealth_concurrency: int = Field(
        default=5,
        description="Max parallel Playwright Stealth probes.",
    )
    probe_stealth_timeout_seconds: float = Field(
        default=15.0,
        description="Per-URL Playwright Stealth timeout in seconds.",
    )

    # Scored plugin selection (requires scoring.enabled=True)
    scoring_enabled: bool = Field(
        default=False,
        description="Use scoring to limit plugin selection per request.",
    )
    stremio_deadline_ms: int = Field(
        default=2000,
        description="Overall deadline for stream search (ms).",
    )
    max_plugins_scored: int = Field(
        default=5,
        description="Top-N plugins when scoring is active.",
    )
    max_items_total: int = Field(
        default=50,
        description="Global result cap across all plugins.",
    )
    max_items_per_plugin: int = Field(
        default=20,
        description="Per-plugin result cap in scored mode.",
    )
    exploration_probability: float = Field(
        default=0.15,
        description="Chance to include a random mid-score plugin.",
    )


class AppConfig(BaseModel):
    """
    Canonical application configuration (validated, final).

    Note:
    - YAML is expected to be sectioned (plugins/http/playwright/logging/cache).
    - Environment variables are handled by EnvOverrides(BaseSettings) to allow strict
      precedence control (defaults < YAML < ENV < CLI) in load.py.
    """

    # General
    app_name: str = Field(default="scavengarr", description="Application name.")
    environment: Environment = Field(
        default="dev",
        description="Runtime environment (affects defaults like log format).",
    )

    # Plugins (YAML section: plugins)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    plugin_dir: Path = Field(
        default=Path("./plugins"),
        validation_alias=AliasChoices(
            "plugin_dir",
            AliasPath("plugins", "plugin_dir"),
        ),
        description="Directory containing Python plugins.",
    )

    # Scoring (YAML section: scoring)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)

    # HTTP engine (YAML section: http.*)
    http_timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices(
            "http_timeout_seconds",
            AliasPath("http", "timeout_seconds"),
        ),
        description="Default HTTP timeout in seconds (used by scraping engine).",
    )
    http_timeout_resolve_seconds: float = Field(
        default=15.0,
        validation_alias=AliasChoices(
            "http_timeout_resolve_seconds",
            AliasPath("http", "timeout_resolve_seconds"),
        ),
        description="HTTP timeout for hoster resolution requests.",
    )
    http_follow_redirects: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "http_follow_redirects",
            AliasPath("http", "follow_redirects"),
        ),
        description="Whether HTTP client follows redirects.",
    )
    http_user_agent: str = Field(
        default="Scavengarr/0.1.0 (+https://github.com/Strob0t/Scavengarr)",
        validation_alias=AliasChoices(
            "http_user_agent",
            AliasPath("http", "user_agent"),
        ),
        description="User-Agent for outgoing HTTP requests.",
    )

    # Link validation toggle
    validate_download_links: bool = Field(
        default=True,
        description="Enable download link validation (HEAD requests)",
    )
    validation_timeout_seconds: float = Field(
        default=5.0,
        description="Timeout per link validation (seconds)",
    )
    validation_max_concurrent: int = Field(
        default=20,
        description="Max parallel link validations",
    )

    # Playwright (YAML section: playwright.*)
    playwright_headless: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "playwright_headless",
            AliasPath("playwright", "headless"),
        ),
        description="Run Playwright headless.",
    )
    playwright_timeout_ms: int = Field(
        default=30_000,
        validation_alias=AliasChoices(
            "playwright_timeout_ms",
            AliasPath("playwright", "timeout_ms"),
        ),
        description="Playwright timeout in milliseconds.",
    )

    # Logging (YAML section: logging.*)
    log_level: LogLevel = Field(
        default="INFO",
        validation_alias=AliasChoices(
            "log_level",
            AliasPath("logging", "level"),
        ),
        description="Log level.",
    )
    log_format: LogFormat | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "log_format",
            AliasPath("logging", "format"),
        ),
        description=(
            "Log renderer format (console/json). If unset, derived from environment."
        ),
    )

    # TMDB API key (required for Stremio addon)
    tmdb_api_key: str | None = Field(
        default=None,
        description="TMDB API key for Stremio catalog and title lookup.",
    )

    # Stremio addon configuration (YAML section: stremio.*)
    stremio: StremioConfig = Field(default_factory=StremioConfig)

    # Cache (disk-only placeholder) (YAML section: cache.*)
    cache: CacheConfig = Field(default_factory=CacheConfig)

    cache_dir: Path = Field(
        default=Path("./.cache/scavengarr"),
        validation_alias=AliasChoices(
            "cache_dir",
            AliasPath("cache", "dir"),
        ),
        description="Cache directory (disk).",
    )
    cache_ttl_seconds: int = Field(
        default=3600,
        validation_alias=AliasChoices(
            "cache_ttl_seconds",
            AliasPath("cache", "ttl_seconds"),
        ),
        description="Cache TTL in seconds.",
    )

    @field_validator("plugin_dir", "cache_dir", mode="before")
    @classmethod
    def _validate_paths(cls, v: Any) -> Path:
        return _normalize_path(v)

    # Rate limiting (YAML section: http.*)
    rate_limit_requests_per_second: float = Field(
        default=5.0,
        validation_alias=AliasChoices(
            "rate_limit_requests_per_second",
            AliasPath("http", "rate_limit_rps"),
        ),
        description="Default per-domain rate limit (requests/second). 0 = unlimited.",
    )
    api_rate_limit_rpm: int = Field(
        default=120,
        validation_alias=AliasChoices(
            "api_rate_limit_rpm",
            AliasPath("http", "api_rate_limit_rpm"),
        ),
        description="API rate limit per IP (requests/minute). 0 = unlimited.",
    )

    # Adaptive rate limiting (YAML section: http.*)
    rate_limit_adaptive: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "rate_limit_adaptive",
            AliasPath("http", "rate_limit_adaptive"),
        ),
        description=(
            "Enable AIMD adaptive rate limiting per domain. "
            "Rate increases on success, halves on 429/503, reduces on timeout."
        ),
    )
    rate_limit_min_rps: float = Field(
        default=0.5,
        validation_alias=AliasChoices(
            "rate_limit_min_rps",
            AliasPath("http", "rate_limit_min_rps"),
        ),
        description="Minimum adaptive rate per domain (rps).",
    )
    rate_limit_max_rps: float = Field(
        default=50.0,
        validation_alias=AliasChoices(
            "rate_limit_max_rps",
            AliasPath("http", "rate_limit_max_rps"),
        ),
        description="Maximum adaptive rate per domain (rps).",
    )

    # Retry on 429/503 (YAML section: http.*)
    http_retry_max_attempts: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "http_retry_max_attempts",
            AliasPath("http", "retry_max_attempts"),
        ),
        description="Max retry attempts on 429/503 responses. 0 = no retries.",
    )
    http_retry_backoff_base: float = Field(
        default=1.0,
        validation_alias=AliasChoices(
            "http_retry_backoff_base",
            AliasPath("http", "retry_backoff_base"),
        ),
        description="Base delay in seconds for exponential backoff.",
    )
    http_retry_max_backoff: float = Field(
        default=30.0,
        validation_alias=AliasChoices(
            "http_retry_max_backoff",
            AliasPath("http", "retry_max_backoff"),
        ),
        description="Maximum backoff delay in seconds.",
    )

    @field_validator("http_timeout_seconds", "http_timeout_resolve_seconds")
    @classmethod
    def _validate_http_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("HTTP timeout must be > 0")
        return v

    @field_validator("playwright_timeout_ms")
    @classmethod
    def _validate_playwright_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("playwright_timeout_ms must be > 0")
        return v

    @field_validator("cache_ttl_seconds")
    @classmethod
    def _validate_cache_ttl(cls, v: int) -> int:
        if v < 0:
            raise ValueError("cache_ttl_seconds must be >= 0")
        return v

    @model_validator(mode="after")
    def _derive_defaults(self) -> "AppConfig":
        # Default log format: console in dev/test, json in prod.
        if self.log_format is None:
            self.log_format = "json" if self.environment == "prod" else "console"
        return self


class EnvOverrides(BaseSettings):
    """
    Environment-variable overrides (all optional).

    Intended usage:
    - load.py creates EnvOverrides() to read SCAVENGARR_* variables,
      converts to dict of set values, merges into YAML/defaults,
      then validates AppConfig.

    Supported env var examples (flat, explicit):
    - SCAVENGARR_PLUGIN_DIR
    - SCAVENGARR_HTTP_TIMEOUT_SECONDS
    - SCAVENGARR_PLAYWRIGHT_HEADLESS
    - SCAVENGARR_LOG_LEVEL
    """

    model_config = SettingsConfigDict(
        env_prefix="SCAVENGARR_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str | None = None
    environment: Environment | None = None

    plugin_dir: Path | None = None

    http_timeout_seconds: float | None = None
    http_timeout_resolve_seconds: float | None = None
    http_follow_redirects: bool | None = None
    http_user_agent: str | None = None

    rate_limit_requests_per_second: float | None = None
    rate_limit_adaptive: bool | None = None
    rate_limit_min_rps: float | None = None
    rate_limit_max_rps: float | None = None
    api_rate_limit_rpm: int | None = None

    http_retry_max_attempts: int | None = None
    http_retry_backoff_base: float | None = None
    http_retry_max_backoff: float | None = None

    playwright_headless: bool | None = None
    playwright_timeout_ms: int | None = None

    log_level: LogLevel | None = None
    log_format: LogFormat | None = None

    cache_dir: Path | None = None
    cache_ttl_seconds: int | None = None

    tmdb_api_key: str | None = None

    # Scoring env overrides (flat)
    scoring_enabled: bool | None = None
    scoring_w_health: float | None = None
    scoring_w_search: float | None = None

    @field_validator("plugin_dir", "cache_dir", mode="before")
    @classmethod
    def _validate_paths(cls, v: Any) -> Any:
        if v is None:
            return None
        return _normalize_path(v)

    def to_update_dict(self) -> dict[str, Any]:
        """
        Return only values that were actually provided (non-None), for merging.
        """
        data = self.model_dump(exclude_none=True)
        # Map flat scoring env vars into the scoring section.
        scoring: dict[str, Any] = {}
        for key in ("scoring_enabled", "scoring_w_health", "scoring_w_search"):
            if key in data:
                section_key = key.removeprefix("scoring_")
                scoring[section_key] = data.pop(key)
        if scoring:
            data.setdefault("scoring", {})
            data["scoring"].update(scoring)
        return data
