"""Pydantic configuration models with validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

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
        default=5,
        description="Max parallel plugin searches for stream resolution.",
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
        description="Max streams to probe at stream time (top-ranked first).",
    )

    probe_stealth_enabled: bool = Field(
        default=True,
        description="Use Playwright Stealth to bypass Cloudflare for probed URLs.",
    )
    probe_stealth_concurrency: int = Field(
        default=3,
        description="Max parallel Playwright Stealth probes.",
    )
    probe_stealth_timeout_seconds: float = Field(
        default=15.0,
        description="Per-URL Playwright Stealth timeout in seconds.",
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

    # Plugins (YAML section: plugins.plugin_dir)
    plugin_dir: Path = Field(
        default=Path("./plugins"),
        validation_alias=AliasChoices(
            "plugin_dir",
            AliasPath("plugins", "plugin_dir"),
        ),
        description="Directory containing YAML/Python plugins.",
    )

    # HTTP / Scrapy engine (YAML section: http.*)
    http_timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices(
            "http_timeout_seconds",
            AliasPath("http", "timeout_seconds"),
        ),
        description="HTTP timeout in seconds for static scraping.",
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
    log_format: Optional[LogFormat] = Field(
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

    @field_validator("http_timeout_seconds")
    @classmethod
    def _validate_http_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("http_timeout_seconds must be > 0")
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

    def to_sectioned_dict(self) -> dict[str, Any]:
        """
        Dump configuration in the sectioned shape used by config.yaml/docs.
        """
        return {
            "app_name": self.app_name,
            "environment": self.environment,
            "plugins": {"plugin_dir": str(self.plugin_dir)},
            "http": {
                "timeout_seconds": self.http_timeout_seconds,
                "follow_redirects": self.http_follow_redirects,
                "user_agent": self.http_user_agent,
            },
            "playwright": {
                "headless": self.playwright_headless,
                "timeout_ms": self.playwright_timeout_ms,
            },
            "logging": {"level": self.log_level, "format": self.log_format},
            "cache": {
                "dir": str(self.cache_dir),
                "ttl_seconds": self.cache_ttl_seconds,
            },
            "stremio": self.stremio.model_dump(),
        }


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

    app_name: Optional[str] = None
    environment: Optional[Environment] = None

    plugin_dir: Optional[Path] = None

    http_timeout_seconds: Optional[float] = None
    http_follow_redirects: Optional[bool] = None
    http_user_agent: Optional[str] = None

    playwright_headless: Optional[bool] = None
    playwright_timeout_ms: Optional[int] = None

    log_level: Optional[LogLevel] = None
    log_format: Optional[LogFormat] = None

    cache_dir: Optional[Path] = None
    cache_ttl_seconds: Optional[int] = None

    tmdb_api_key: Optional[str] = None

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
        # Ensure Paths are Path objects in update dict (caller can stringify if needed)
        return data
