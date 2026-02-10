"""Pydantic validation models for plugin YAML files."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

PLUGIN_NAME_RE = r"^[a-z0-9-]+$"
SEMVER_RE = r"^\d+\.\d+\.\d+$"


class HttpOverrides(BaseModel):
    timeout_seconds: Optional[float] = None
    follow_redirects: Optional[bool] = None
    user_agent: Optional[str] = None

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        if v <= 0:
            raise ValueError("http.timeout_seconds must be > 0")
        return v


# === Multi-Stage Components ===


class PaginationConfig(BaseModel):
    """Pagination configuration for list stages"""

    enabled: bool = False
    selector: Optional[str] = None
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
    item_group: Optional[str] = Field(
        default=None,
        description="Optional intermediate container. If set, all 'items' within each group are merged into one result.",
    )

    items: str = Field(
        ..., description="Item selector (relative to container or item_group)"
    )

    fields: Dict[str, str] = Field(
        default_factory=dict, description="Field name to CSS selector mapping"
    )

    field_attributes: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="For link/url fields: list of HTML attributes to try (in order)",
    )

    # Fields that may contain multiple values per item
    multi_value_fields: Optional[List[str]] = Field(
        default=None,
        description="Fields that should collect multiple values as list instead of overwriting",
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
    custom: Dict[str, str] = Field(default_factory=dict)

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
    url: Optional[str] = None
    url_pattern: Optional[str] = None  # e.g., "/search/{category}/{page}"

    # Selectors for this stage
    selectors: StageSelectors

    # Navigation
    next_stage: Optional[str] = None
    pagination: Optional[PaginationConfig] = None

    # Conditions for processing (optional)
    conditions: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _validate_stage(self) -> "ScrapingStage":
        # Must have either url or url_pattern
        if not self.url and not self.url_pattern:
            raise ValueError("stage must define either 'url' or 'url_pattern'")

        # List stages should have link selector
        if self.type == "list" and not self.selectors.link:
            raise ValueError("list stage should define 'link' selector")

        return self


# === Legacy Single-Stage Selectors (Backward Compatibility) ===


class ScrapySelectors(BaseModel):
    """Legacy single-stage selectors (deprecated in favor of StageSelectors)"""

    row: str
    title: str
    download_link: str
    seeders: Optional[str] = None
    leechers: Optional[str] = None
    size: Optional[str] = None


class PlaywrightLocators(BaseModel):
    """Legacy Playwright locators"""

    row: str
    title: str
    download_link: str
    seeders: Optional[str] = None
    leechers: Optional[str] = None


# === Auth Config (unchanged) ===


class AuthConfig(BaseModel):
    type: Literal["none", "basic", "form", "cookie"] = "none"

    username: Optional[str] = None
    password: Optional[str] = None

    login_url: Optional[HttpUrl] = None
    username_field: Optional[str] = None
    password_field: Optional[str] = None
    submit_selector: Optional[str] = None

    @model_validator(mode="after")
    def _validate_auth_requirements(self) -> "AuthConfig":
        if self.type == "none":
            return self

        if self.type == "basic":
            if not self.username or not self.password:
                raise ValueError("basic auth requires 'username' and 'password'")
            return self

        if self.type == "form":
            missing: list[str] = []
            if not self.login_url:
                missing.append("login_url")
            if not self.username_field:
                missing.append("username_field")
            if not self.password_field:
                missing.append("password_field")
            if not self.submit_selector:
                missing.append("submit_selector")
            if not self.username:
                missing.append("username")
            if not self.password:
                missing.append("password")

            if missing:
                raise ValueError(f"form auth requires {', '.join(missing)}")
            return self

        return self


# === Scraping Config ===


class ScrapingConfig(BaseModel):
    mode: Literal["scrapy", "playwright"]

    # === Legacy Playwright ===
    search_url_template: Optional[str] = None
    wait_for_selector: Optional[str] = None
    locators: Optional[PlaywrightLocators] = None

    # === scrapy Pipeline ===
    stages: Optional[List[ScrapingStage]] = None
    start_stage: Optional[str] = None  # Which stage to begin with
    max_depth: int = 5  # Recursion limit
    delay_seconds: float = 1.5  # Rate limiting

    @model_validator(mode="after")
    def _validate_mode_requirements(self) -> "ScrapingConfig":
        if self.mode == "scrapy":
            if not self.stages:
                raise ValueError("scrapy mode requires 'stages' list")
            if len(self.stages) == 0:
                raise ValueError("scrapy mode requires at least one stage")

            # Validate stage references
            stage_names = {s.name for s in self.stages}
            for stage in self.stages:
                if stage.next_stage and stage.next_stage not in stage_names:
                    raise ValueError(
                        f"stage '{stage.name}' references unknown next_stage '{stage.next_stage}'"
                    )

            # Validate start_stage
            if self.start_stage and self.start_stage not in stage_names:
                raise ValueError(
                    f"start_stage '{self.start_stage}' not found in stages"
                )

            # Validate delay
            if self.delay_seconds < 0:
                raise ValueError("delay_seconds must be >= 0")

            return self

        if self.mode == "playwright":
            if not self.search_url_template:
                raise ValueError("playwright mode requires 'search_url_template' field")
            if not self.wait_for_selector:
                raise ValueError("playwright mode requires 'wait_for_selector' field")
            if self.locators is None:
                raise ValueError("playwright mode requires 'locators' field")
            return self

        return self


# === Main Plugin Definition ===


class YamlPluginDefinitionPydantic(BaseModel):
    """
    Pydantic validation model for YAML plugins.

    After validation, this is converted to domain.plugins.plugin_schema.YamlPluginDefinition.
    """

    name: str = Field(pattern=PLUGIN_NAME_RE)
    version: str = Field(pattern=SEMVER_RE)
    base_url: HttpUrl

    scraping: ScrapingConfig
    auth: Optional[AuthConfig] = None

    # Optional per-plugin overrides for HTTP behaviour
    http: Optional[HttpOverrides] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _default_auth(self) -> "YamlPluginDefinitionPydantic":
        if self.auth is None:
            self.auth = AuthConfig(type="none")
        return self
