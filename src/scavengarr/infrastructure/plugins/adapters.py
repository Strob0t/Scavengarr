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


def to_domain_pagination(
    pydantic: infra.PaginationConfig,
) -> domain.PaginationConfig:
    """Convert Pydantic PaginationConfig to domain model."""
    return domain.PaginationConfig(
        enabled=pydantic.enabled,
        selector=pydantic.selector,
        max_pages=pydantic.max_pages,
    )


def to_domain_nested_selector(
    pydantic: infra.NestedSelector,
) -> domain.NestedSelector:
    """Convert Pydantic NestedSelector to domain model."""
    return domain.NestedSelector(
        container=pydantic.container,
        items=pydantic.items,
        fields=pydantic.fields,
        item_group=pydantic.item_group,
        field_attributes=pydantic.field_attributes,
        multi_value_fields=pydantic.multi_value_fields,
    )


def to_domain_stage_selectors(
    pydantic: infra.StageSelectors,
) -> domain.StageSelectors:
    """Convert Pydantic StageSelectors to domain model."""
    return domain.StageSelectors(
        link=pydantic.link,
        title=pydantic.title,
        description=pydantic.description,
        release_name=pydantic.release_name,
        download_link=pydantic.download_link,
        seeders=pydantic.seeders,
        leechers=pydantic.leechers,
        size=pydantic.size,
        published_date=pydantic.published_date,
        download_links=to_domain_nested_selector(pydantic.download_links)
        if pydantic.download_links
        else None,
        rows=pydantic.rows,
        custom=pydantic.custom,
    )


def to_domain_scraping_stage(
    pydantic: infra.ScrapingStage,
) -> domain.ScrapingStage:
    """Convert Pydantic ScrapingStage to domain model."""
    return domain.ScrapingStage(
        name=pydantic.name,
        type=pydantic.type,
        selectors=to_domain_stage_selectors(pydantic.selectors),
        url=pydantic.url,
        url_pattern=pydantic.url_pattern,
        next_stage=pydantic.next_stage,
        pagination=to_domain_pagination(pydantic.pagination)
        if pydantic.pagination
        else None,
        conditions=pydantic.conditions,
        query_transform=pydantic.query_transform,
        field_attributes=pydantic.field_attributes,
    )


def to_domain_scrapy_selectors(
    pydantic: infra.ScrapySelectors,
) -> domain.ScrapySelectors:
    """Convert Pydantic ScrapySelectors to domain model."""
    return domain.ScrapySelectors(
        row=pydantic.row,
        title=pydantic.title,
        download_link=pydantic.download_link,
        seeders=pydantic.seeders,
        leechers=pydantic.leechers,
        size=pydantic.size,
    )


def to_domain_playwright_locators(
    pydantic: infra.PlaywrightLocators,
) -> domain.PlaywrightLocators:
    """Convert Pydantic PlaywrightLocators to domain model."""
    return domain.PlaywrightLocators(
        row=pydantic.row,
        title=pydantic.title,
        download_link=pydantic.download_link,
        seeders=pydantic.seeders,
        leechers=pydantic.leechers,
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


def to_domain_scraping_config(
    pydantic: infra.ScrapingConfig,
) -> domain.ScrapingConfig:
    """Convert Pydantic ScrapingConfig to domain model."""
    return domain.ScrapingConfig(
        mode=pydantic.mode,
        search_url_template=pydantic.search_url_template,
        wait_for_selector=pydantic.wait_for_selector,
        locators=to_domain_playwright_locators(pydantic.locators)
        if pydantic.locators
        else None,
        stages=[to_domain_scraping_stage(s) for s in pydantic.stages]
        if pydantic.stages
        else None,
        start_stage=pydantic.start_stage,
        max_depth=pydantic.max_depth,
        delay_seconds=pydantic.delay_seconds,
    )


def to_domain_plugin_definition(
    pydantic: infra.YamlPluginDefinitionPydantic,
) -> domain.YamlPluginDefinition:
    """Convert validated Pydantic model to pure domain model."""
    urls = [str(u) for u in pydantic.base_url]
    return domain.YamlPluginDefinition(
        name=pydantic.name,
        version=pydantic.version,
        base_url=urls[0],
        scraping=to_domain_scraping_config(pydantic.scraping),
        mirror_urls=urls[1:] or None,
        category_map=pydantic.category_map,
        auth=to_domain_auth_config(pydantic.auth) if pydantic.auth else None,
        http=to_domain_http_overrides(pydantic.http) if pydantic.http else None,
    )
