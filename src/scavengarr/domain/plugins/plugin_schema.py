# src/scavengarr/domain/plugins/plugin_schema.py
"""Pure domain models for plugin configuration (framework-free)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass(frozen=True)
class HttpOverrides:
    """HTTP configuration overrides."""

    timeout_seconds: Optional[float] = None
    follow_redirects: Optional[bool] = None
    user_agent: Optional[str] = None


@dataclass(frozen=True)
class PaginationConfig:
    """Pagination configuration for list stages."""

    enabled: bool = False
    selector: Optional[str] = None
    max_pages: int = 1


@dataclass(frozen=True)
class NestedSelector:
    """
    Nested selector with flexible grouping strategies.
    """

    container: str
    items: str
    fields: Dict[str, str] = field(default_factory=dict)
    item_group: Optional[str] = None
    field_attributes: Dict[str, List[str]] = field(default_factory=dict)
    multi_value_fields: Optional[List[str]] = None


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
    link: Optional[str] = None

    # Simple extractors
    title: Optional[str] = None
    description: Optional[str] = None
    release_name: Optional[str] = None

    # Torrent-specific (from original schema)
    download_link: Optional[str] = None
    seeders: Optional[str] = None
    leechers: Optional[str] = None
    size: Optional[str] = None
    published_date: Optional[str] = None

    # Nested extractors
    download_links: Optional[NestedSelector] = None

    # Custom fields (extensible)
    custom: Dict[str, str] = field(default_factory=dict)


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
    url: Optional[str] = None
    url_pattern: Optional[str] = None
    next_stage: Optional[str] = None
    pagination: Optional[PaginationConfig] = None
    conditions: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ScrapySelectors:
    """Legacy single-stage selectors (deprecated in favor of StageSelectors)."""

    row: str
    title: str
    download_link: str
    seeders: Optional[str] = None
    leechers: Optional[str] = None
    size: Optional[str] = None


@dataclass(frozen=True)
class PlaywrightLocators:
    """Legacy Playwright locators."""

    row: str
    title: str
    download_link: str
    seeders: Optional[str] = None
    leechers: Optional[str] = None


@dataclass(frozen=True)
class AuthConfig:
    """Authentication configuration."""

    type: Literal["none", "basic", "form", "cookie"] = "none"
    username: Optional[str] = None
    password: Optional[str] = None
    login_url: Optional[str] = None
    username_field: Optional[str] = None
    password_field: Optional[str] = None
    submit_selector: Optional[str] = None


@dataclass(frozen=True)
class ScrapingConfig:
    """Scraping configuration."""

    mode: Literal["scrapy", "playwright"]

    # === Legacy Playwright ===
    search_url_template: Optional[str] = None
    wait_for_selector: Optional[str] = None
    locators: Optional[PlaywrightLocators] = None

    # === Scrapy Pipeline ===
    stages: Optional[List[ScrapingStage]] = None
    start_stage: Optional[str] = None
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
    auth: Optional[AuthConfig] = None
    http: Optional[HttpOverrides] = None
