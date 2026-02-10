"""Tests for plugin schema domain models."""

from __future__ import annotations

from scavengarr.domain.plugins import (
    AuthConfig,
    HttpOverrides,
    NestedSelector,
    PaginationConfig,
    ScrapingConfig,
    ScrapingStage,
    StageSelectors,
    YamlPluginDefinition,
)


class TestStageSelectors:
    def test_defaults_all_none(self) -> None:
        s = StageSelectors()
        assert s.link is None
        assert s.title is None
        assert s.description is None
        assert s.release_name is None
        assert s.download_link is None
        assert s.seeders is None
        assert s.leechers is None
        assert s.size is None
        assert s.published_date is None
        assert s.download_links is None

    def test_custom_fields_empty_by_default(self) -> None:
        s = StageSelectors()
        assert s.custom == {}


class TestNestedSelector:
    def test_creation(self) -> None:
        ns = NestedSelector(
            container="div#list",
            items="li",
            fields={"link": "a"},
            field_attributes={"link": ["href"]},
        )
        assert ns.container == "div#list"
        assert ns.items == "li"
        assert ns.fields == {"link": "a"}
        assert ns.field_attributes == {"link": ["href"]}

    def test_optional_item_group(self) -> None:
        ns = NestedSelector(
            container="div",
            items="li",
            fields={},
            field_attributes={},
            item_group="ul.group",
        )
        assert ns.item_group == "ul.group"


class TestPaginationConfig:
    def test_defaults(self) -> None:
        p = PaginationConfig()
        assert p.enabled is False
        assert p.selector is None
        assert p.max_pages == 1


class TestAuthConfig:
    def test_default_type_none(self) -> None:
        a = AuthConfig()
        assert a.type == "none"
        assert a.username is None
        assert a.password is None

    def test_env_fields_default_none(self) -> None:
        a = AuthConfig()
        assert a.username_env is None
        assert a.password_env is None

    def test_env_fields_stored(self) -> None:
        a = AuthConfig(
            username_env="MY_USER_ENV",
            password_env="MY_PASS_ENV",
        )
        assert a.username_env == "MY_USER_ENV"
        assert a.password_env == "MY_PASS_ENV"


class TestHttpOverrides:
    def test_defaults_all_none(self) -> None:
        h = HttpOverrides()
        assert h.timeout_seconds is None
        assert h.follow_redirects is None
        assert h.user_agent is None


class TestScrapingStage:
    def test_list_stage(self) -> None:
        stage = ScrapingStage(
            name="search_results",
            type="list",
            selectors=StageSelectors(
                link="a[href*='/stream/']",
                title="h2.bgDark",
            ),
            url_pattern="/search/title/{query}",
            next_stage="movie_detail",
        )
        assert stage.name == "search_results"
        assert stage.type == "list"
        assert stage.next_stage == "movie_detail"

    def test_detail_stage(self) -> None:
        stage = ScrapingStage(
            name="movie_detail",
            type="detail",
            selectors=StageSelectors(title="h2.bgDark"),
        )
        assert stage.type == "detail"
        assert stage.next_stage is None


class TestScrapingConfig:
    def test_scrapy_mode(self) -> None:
        cfg = ScrapingConfig(mode="scrapy")
        assert cfg.mode == "scrapy"
        assert cfg.max_depth == 5
        assert cfg.delay_seconds == 1.5

    def test_playwright_mode(self) -> None:
        cfg = ScrapingConfig(mode="playwright")
        assert cfg.mode == "playwright"


class TestYamlPluginDefinition:
    def test_full_creation(self) -> None:
        plugin = YamlPluginDefinition(
            name="filmpalast",
            version="1.0.0",
            base_url="https://filmpalast.to",
            scraping=ScrapingConfig(
                mode="scrapy",
                stages=[
                    ScrapingStage(
                        name="search_results",
                        type="list",
                        selectors=StageSelectors(link="a[href*='/stream/']"),
                        next_stage="detail",
                    ),
                ],
                start_stage="search_results",
            ),
        )
        assert plugin.name == "filmpalast"
        assert plugin.base_url == "https://filmpalast.to"
        assert plugin.scraping.mode == "scrapy"
        assert plugin.scraping.stages is not None
        assert len(plugin.scraping.stages) == 1

    def test_optional_auth_and_http(self) -> None:
        plugin = YamlPluginDefinition(
            name="test",
            version="0.1",
            base_url="http://x",
            scraping=ScrapingConfig(mode="scrapy"),
        )
        assert plugin.auth is None
        assert plugin.http is None

    def test_mirror_urls_default_none(self) -> None:
        plugin = YamlPluginDefinition(
            name="test",
            version="0.1",
            base_url="http://x",
            scraping=ScrapingConfig(mode="scrapy"),
        )
        assert plugin.mirror_urls is None

    def test_mirror_urls_explicit_list(self) -> None:
        plugin = YamlPluginDefinition(
            name="filmpalast",
            version="1.0.0",
            base_url="https://filmpalast.to",
            scraping=ScrapingConfig(mode="scrapy"),
            mirror_urls=[
                "https://filmpalast.sx",
                "https://filmpalast.im",
            ],
        )
        assert plugin.mirror_urls == [
            "https://filmpalast.sx",
            "https://filmpalast.im",
        ]
