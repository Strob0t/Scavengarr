from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


PLUGIN_NAME_RE = r"^[a-z0-9-]+$"
SEMVER_RE = r"^\d+\.\d+\.\d+$"


class ScrapySelectors(BaseModel):
    row: str
    title: str
    download_link: str
    seeders: Optional[str] = None
    leechers: Optional[str] = None
    size: Optional[str] = None


class PlaywrightLocators(BaseModel):
    row: str
    title: str
    download_link: str
    seeders: Optional[str] = None
    leechers: Optional[str] = None


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
                raise ValueError(
                    "basic auth requires 'username' and 'password'")
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

        # cookie: placeholder for later; schema allows it but no extra requirements yet
        return self


class ScrapingConfig(BaseModel):
    mode: Literal["scrapy", "playwright"]

    # Scrapy
    search_path: Optional[str] = None
    selectors: Optional[ScrapySelectors] = None

    # Playwright (later)
    search_url_template: Optional[str] = None
    wait_for_selector: Optional[str] = None
    locators: Optional[PlaywrightLocators] = None

    @model_validator(mode="after")
    def _validate_mode_requirements(self) -> "ScrapingConfig":
        if self.mode == "scrapy":
            if not self.search_path:
                raise ValueError("scrapy mode requires 'search_path' field")
            if self.selectors is None:
                raise ValueError("scrapy mode requires 'selectors' field")
            return self

        if self.mode == "playwright":
            if not self.search_url_template:
                raise ValueError(
                    "playwright mode requires 'search_url_template' field")
            if not self.wait_for_selector:
                raise ValueError(
                    "playwright mode requires 'wait_for_selector' field")
            if self.locators is None:
                raise ValueError("playwright mode requires 'locators' field")
            return self

        return self


class YamlPluginDefinition(BaseModel):
    """
    Declarative YAML plugin.

    All plugin info lives in this file: name/version/base_url/scraping/auth.
    """

    name: str = Field(pattern=PLUGIN_NAME_RE)
    version: str = Field(pattern=SEMVER_RE)
    base_url: HttpUrl

    scraping: ScrapingConfig
    auth: Optional[AuthConfig] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        # Extra guard: no leading/trailing whitespace
        return v.strip()

    @model_validator(mode="after")
    def _default_auth(self) -> "YamlPluginDefinition":
        if self.auth is None:
            self.auth = AuthConfig(type="none")
        return self
