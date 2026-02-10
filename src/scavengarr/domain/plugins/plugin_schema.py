# src/scavengarr/domain/plugins/plugin_schema.py
"""Pure domain models for plugin configuration (framework-free)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class HttpOverrides:
    """HTTP configuration overrides."""

    timeout_seconds: float | None = None
    follow_redirects: bool | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class PaginationConfig:
    """Pagination configuration for list stages."""

    enabled: bool = False
    selector: str | None = None
    max_pages: int = 1


@dataclass(frozen=True)
class NestedSelector:
    """
    Nested selector with flexible grouping strategies.
    """

    container: str
    items: str
    fields: dict[str, str] = field(default_factory=dict)
    item_group: str | None = None
    field_attributes: dict[str, list[str]] = field(default_factory=dict)
    multi_value_fields: list[str] | None = None


@dataclass(frozen=True)
class StageSelectors:
    """
    Selectors for a single scraping stage.

    Can contain:
    - Simple selectors (str): CSS selector for text/attribute
    - Nested selectors (NestedSelector): For complex structures
    - Link selector (implicit via 'link' or 'next_link' key)
    """

    # For list stages: extract links to next stage
    link: str | None = None

    # Simple extractors
    title: str | None = None
    description: str | None = None
    release_name: str | None = None

    # Torrent-specific (from original schema)
    download_link: str | None = None
    seeders: str | None = None
    leechers: str | None = None
    size: str | None = None
    published_date: str | None = None

    # Nested extractors
    download_links: NestedSelector | None = None

    # Custom fields (extensible)
    custom: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ScrapingStage:
    """
    Single stage in multi-stage scraping pipeline.

    Example:
      - name: "movie_list"
        type: "list"
        url: "/movies/new"
        selectors:
          link: "a[href*='/stream/']"
          title: "h2"
        next_stage: "movie_detail"
    """

    name: str
    type: Literal["list", "detail"]
    selectors: StageSelectors
    url: str | None = None
    url_pattern: str | None = None
    next_stage: str | None = None
    pagination: PaginationConfig | None = None
    conditions: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScrapySelectors:
    """Legacy single-stage selectors (deprecated in favor of StageSelectors)."""

    row: str
    title: str
    download_link: str
    seeders: str | None = None
    leechers: str | None = None
    size: str | None = None


@dataclass(frozen=True)
class PlaywrightLocators:
    """Legacy Playwright locators."""

    row: str
    title: str
    download_link: str
    seeders: str | None = None
    leechers: str | None = None


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


@dataclass(frozen=True)
class ScrapingConfig:
    """Scraping configuration."""

    mode: Literal["scrapy", "playwright"]

    # === Legacy Playwright ===
    search_url_template: str | None = None
    wait_for_selector: str | None = None
    locators: PlaywrightLocators | None = None

    # === Scrapy Pipeline ===
    stages: list[ScrapingStage] | None = None
    start_stage: str | None = None
    max_depth: int = 5
    delay_seconds: float = 1.5


@dataclass(frozen=True)
class YamlPluginDefinition:
    """
    Declarative YAML plugin definition (domain model - no validation).

    All plugin info lives in this file: name/version/base_url/scraping/auth.

    Supports both legacy single-stage and new multi-stage scraping.
    """

    name: str
    version: str
    base_url: str
    scraping: ScrapingConfig
    auth: AuthConfig | None = None
    http: HttpOverrides | None = None
