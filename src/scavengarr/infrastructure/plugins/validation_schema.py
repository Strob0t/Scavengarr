"""Pydantic validation models for plugin YAML files."""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

PLUGIN_NAME_RE = r"^[a-z0-9-]+$"
SEMVER_RE = r"^\d+\.\d+\.\d+$"


class HttpOverrides(BaseModel):
    timeout_seconds: float | None = None
    follow_redirects: bool | None = None
    user_agent: str | None = None

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if v <= 0:
            raise ValueError("http.timeout_seconds must be > 0")
        return v


# === Multi-Stage Components ===


class PaginationConfig(BaseModel):
    """Pagination configuration for list stages"""

    enabled: bool = False
    selector: str | None = None
    max_pages: int = 1

    @model_validator(mode="after")
    def _validate_pagination(self) -> "PaginationConfig":
        if self.enabled and not self.selector:
            raise ValueError("pagination requires 'selector' when enabled")
        if self.max_pages < 1:
            raise ValueError("max_pages must be >= 1")
        return self


class NestedSelector(BaseModel):
    """
    Nested selector with flexible grouping strategies.
    """

    container: str = Field(..., description="Main container selector")

    # Optional grouping container
    item_group: str | None = Field(
        default=None,
        description=(
            "Optional intermediate container. If set, all"
            " 'items' within each group are merged into"
            " one result."
        ),
    )

    items: str = Field(
        ..., description="Item selector (relative to container or item_group)"
    )

    fields: dict[str, str] = Field(
        default_factory=dict, description="Field name to CSS selector mapping"
    )

    field_attributes: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "For link/url fields: list of HTML attributes"
            " to try (in order)"
        ),
    )

    # Fields that may contain multiple values per item
    multi_value_fields: list[str] | None = Field(
        default=None,
        description=(
            "Fields that should collect multiple values"
            " as list instead of overwriting"
        ),
    )

    @model_validator(mode="after")
    def _validate_fields(self) -> "NestedSelector":
        if not self.fields:
            raise ValueError("nested selector requires at least one field")

        # Link/URL fields MUST be defined in field_attributes
        for field_name in self.fields.keys():
            if field_name.endswith("link") or field_name.endswith("url"):
                if field_name not in self.field_attributes:
                    raise ValueError(
                        f"Field '{field_name}' ends with 'link' or 'url' but has no "
                        f"attribute definition in 'field_attributes'. "
                        f'Add: field_attributes.{field_name}: ["attr1", "attr2", ...]'
                    )

        return self


class StageSelectors(BaseModel):
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

    # Row-based extraction (iterate over multiple result rows)
    rows: str | None = None

    # Custom fields (extensible)
    custom: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_selectors(self) -> "StageSelectors":
        # At least one selector must be defined
        has_selector = any(
            [
                self.link,
                self.title,
                self.description,
                self.release_name,
                self.download_link,
                self.seeders,
                self.leechers,
                self.size,
                self.published_date,
                self.download_links,
                self.rows,
                self.custom,
            ]
        )
        if not has_selector:
            raise ValueError("stage must define at least one selector")
        return self


class ScrapingStage(BaseModel):
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

    name: str = Field(pattern=r"^[a-z0-9_]+$")
    type: Literal["list", "detail"]

    # URL definition
    url: str | None = None
    url_pattern: str | None = None  # e.g., "/search/{category}/{page}"

    # Selectors for this stage
    selectors: StageSelectors

    # Navigation
    next_stage: str | None = None
    pagination: PaginationConfig | None = None

    # Conditions for processing (optional)
    conditions: dict[str, Any] | None = None

    # Query transformation (e.g., "slugify")
    query_transform: str | None = None

    # Field attribute extraction at stage level
    field_attributes: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_stage(self) -> "ScrapingStage":
        # Must have either url or url_pattern
        if not self.url and not self.url_pattern:
            raise ValueError("stage must define either 'url' or 'url_pattern'")

        # List stages should have link selector OR rows selector
        if self.type == "list" and not self.selectors.link and not self.selectors.rows:
            raise ValueError("list stage should define 'link' or 'rows' selector")

        return self


# === Legacy Single-Stage Selectors (Backward Compatibility) ===


class ScrapySelectors(BaseModel):
    """Legacy single-stage selectors (deprecated in favor of StageSelectors)"""

    row: str
    title: str
    download_link: str
    seeders: str | None = None
    leechers: str | None = None
    size: str | None = None


class PlaywrightLocators(BaseModel):
    """Legacy Playwright locators"""

    row: str
    title: str
    download_link: str
    seeders: str | None = None
    leechers: str | None = None


# === Auth Config (unchanged) ===


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
            raise ValueError(
                "basic auth requires 'username' and 'password'"
            )

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
            raise ValueError(
                f"form auth requires {', '.join(missing)}"
            )

    @model_validator(mode="after")
    def _validate_auth_requirements(self) -> "AuthConfig":
        if self.type == "none":
            return self
        if self.type == "basic":
            self._validate_basic_auth()
        elif self.type == "form":
            self._validate_form_auth()
        return self


# === Scraping Config ===


class ScrapingConfig(BaseModel):
    mode: Literal["scrapy", "playwright"]

    # === Legacy Playwright ===
    search_url_template: str | None = None
    wait_for_selector: str | None = None
    locators: PlaywrightLocators | None = None

    # === scrapy Pipeline ===
    stages: list[ScrapingStage] | None = None
    start_stage: str | None = None  # Which stage to begin with
    max_depth: int = 5  # Recursion limit
    delay_seconds: float = 1.5  # Rate limiting

    def _validate_scrapy_stages(self) -> None:
        """Validate scrapy stage references and constraints."""
        if not self.stages or len(self.stages) == 0:
            raise ValueError(
                "scrapy mode requires at least one stage"
            )
        stage_names = {s.name for s in self.stages}
        for stage in self.stages:
            if (
                stage.next_stage
                and stage.next_stage not in stage_names
            ):
                raise ValueError(
                    f"stage '{stage.name}' references unknown"
                    f" next_stage '{stage.next_stage}'"
                )
        if self.start_stage and self.start_stage not in stage_names:
            raise ValueError(
                f"start_stage '{self.start_stage}'"
                " not found in stages"
            )
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds must be >= 0")

    def _validate_playwright_fields(self) -> None:
        """Validate playwright mode has all required fields."""
        if not self.search_url_template:
            raise ValueError(
                "playwright mode requires"
                " 'search_url_template' field"
            )
        if not self.wait_for_selector:
            raise ValueError(
                "playwright mode requires"
                " 'wait_for_selector' field"
            )
        if self.locators is None:
            raise ValueError(
                "playwright mode requires 'locators' field"
            )

    @model_validator(mode="after")
    def _validate_mode_requirements(self) -> "ScrapingConfig":
        if self.mode == "scrapy":
            self._validate_scrapy_stages()
        elif self.mode == "playwright":
            self._validate_playwright_fields()
        return self


# === Main Plugin Definition ===


class YamlPluginDefinitionPydantic(BaseModel):
    """Pydantic validation model for YAML plugins.

    After validation, this is converted to
    domain.plugins.plugin_schema.YamlPluginDefinition.
    """

    name: str = Field(pattern=PLUGIN_NAME_RE)
    version: str = Field(pattern=SEMVER_RE)
    base_url: list[HttpUrl]

    scraping: ScrapingConfig
    category_map: dict[int, str] | None = None
    auth: AuthConfig | None = None

    # Optional per-plugin overrides for HTTP behaviour
    http: HttpOverrides | None = None

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, v: object) -> list[object]:
        """Accept a single URL string or a list of URL strings."""
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            if len(v) == 0:
                raise ValueError("base_url must contain at least one URL")
            return v
        return [v]

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _default_auth(self) -> "YamlPluginDefinitionPydantic":
        if self.auth is None:
            self.auth = AuthConfig(type="none")
        return self
