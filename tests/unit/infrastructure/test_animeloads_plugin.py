"""Unit tests for the anime-loads.org plugin (Playwright-based)."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "animeloads.py"
_BASE_URL = "https://www.anime-loads.org"


@pytest.fixture()
def animeloads_mod() -> Iterator[ModuleType]:
    """Import animeloads plugin module."""
    spec = importlib.util.spec_from_file_location("animeloads", _PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["animeloads"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("animeloads", None)


# ---------------------------------------------------------------------------
# Fixtures (shape of _EXTRACT_RESULTS_JS output)
# ---------------------------------------------------------------------------


def _entry(
    title: str,
    content_type: str = "Anime Series",
    slug: str = "",
    **overrides: Any,
) -> dict[str, Any]:
    slug = slug or title.lower().replace(" ", "-")
    entry: dict[str, Any] = {
        "title": title,
        "slug": slug,
        "mediaUrl": f"{_BASE_URL}/media/{slug}",
        "dataId": "42",
        "status": "Completed",
        "type": content_type,
        "year": "2002",
        "episodes": "220/220",
        "description": "Ein junger Ninja will Hokage werden.",
        "genres": ["Action", "Abenteuer"],
        "languages": ["German", "Japanese"],
        "subtitles": ["German"],
        "poster": f"{_BASE_URL}/files/{slug}.jpg",
        "embedUrl": "",
    }
    entry.update(overrides)
    return entry


NARUTO = _entry("Naruto", embedUrl="https://voe.sx/e/abc123")
NARUTO_MOVIE = _entry(
    "Naruto the Movie", content_type="Anime Movie", year="2004", episodes="1/1"
)
NARUTO_OVA = _entry("Naruto OVA", content_type="OVA", year="", episodes="")


def _make_mock_page() -> AsyncMock:
    """Create a mock Playwright Page (``is_closed()`` is synchronous)."""
    page = AsyncMock()
    page.is_closed = MagicMock(return_value=False)
    return page


def _wire_pages(
    mod: ModuleType,
    page: AsyncMock,
    pages: dict[int, list[dict[str, Any]]],
    total_pages: int,
) -> None:
    """Serve ``pages[n]`` for the n-th search page via ``page.evaluate``."""
    current = {"page": 1}

    async def _goto(url: str, **_: Any) -> None:
        if "/search/page/" in url:
            current["page"] = int(url.split("/search/page/")[1].split("?")[0])
        else:
            current["page"] = 1

    async def _evaluate(js: str) -> Any:
        if js == mod._EXTRACT_RESULTS_JS:
            return pages.get(current["page"], [])
        if js == mod._EXTRACT_PAGINATION_JS:
            return {"total": total_pages * mod._PAGE_SIZE, "totalPages": total_pages}
        raise AssertionError("unexpected evaluate call")

    page.goto = AsyncMock(side_effect=_goto)
    page.evaluate = AsyncMock(side_effect=_evaluate)


@pytest.fixture()
def page() -> AsyncMock:
    return _make_mock_page()


@pytest.fixture()
def plugin(animeloads_mod: ModuleType, page: AsyncMock) -> Any:
    p = animeloads_mod.AnimeLoadsPlugin()
    p._domain_verified = True
    p.base_url = _BASE_URL
    p._new_page = AsyncMock(return_value=page)
    # Release expansion has its own tests (TestReleaseExpansion)
    p._expand_releases = AsyncMock(side_effect=lambda results: results)
    return p


# ---------------------------------------------------------------------------
# Plugin attributes
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name(self, animeloads_mod: ModuleType) -> None:
        assert animeloads_mod.plugin.name == "animeloads"

    def test_mode(self, animeloads_mod: ModuleType) -> None:
        assert animeloads_mod.plugin.mode == "playwright"

    def test_provides(self, animeloads_mod: ModuleType) -> None:
        # Downloads only: its series preview embeds are no episode streams
        # and no resolver plays them (Stremio got nothing in 11-15 s)
        assert animeloads_mod.plugin.provides == "download"
        assert not hasattr(type(animeloads_mod.plugin), "_media_preview")

    def test_domains(self, animeloads_mod: ModuleType) -> None:
        assert animeloads_mod.plugin._domains == [
            "www.anime-loads.org",
            "anime-loads.org",
        ]

    def test_max_pages_covers_1000_results(self, animeloads_mod: ModuleType) -> None:
        assert animeloads_mod._PAGE_SIZE * animeloads_mod._MAX_PAGES == 1000


# ---------------------------------------------------------------------------
# Category helpers
# ---------------------------------------------------------------------------


class TestCaptchaImages:
    """The grab's captcha compares images: with images blocked, every grab
    gave nothing (code review, 2026-10-06)."""

    async def test_captcha_images_load_past_the_blocker(
        self, animeloads_mod: ModuleType
    ) -> None:
        plugin = animeloads_mod.AnimeLoadsPlugin()
        ctx = AsyncMock()
        # The default-timeout setters are synchronous (playwright.timeout_ms)
        ctx.set_default_timeout = MagicMock()
        ctx.set_default_navigation_timeout = MagicMock()

        await plugin._configure_context(ctx)

        patterns = [call.args[0] for call in ctx.route.await_args_list]
        assert patterns == ["**/*", "**/files/captcha*"]
        route = AsyncMock()
        route.request = MagicMock()
        route.request.resource_type = "image"
        await ctx.route.await_args_list[-1].args[1](route)
        route.continue_.assert_awaited_once()
        route.abort.assert_not_awaited()


class TestDetectCategory:
    @pytest.mark.parametrize(
        ("content_type", "expected"),
        [
            ("Anime Series", 5070),
            ("Anime Movie", 2000),
            ("OVA", 5070),
            ("Bonus", 5070),
            ("Live Action", 5000),
            ("Hentai", 5070),
            ("  anime movie  ", 2000),
            ("", 5070),
            ("Unknown", 5070),
        ],
    )
    def test_mapping(
        self, animeloads_mod: ModuleType, content_type: str, expected: int
    ) -> None:
        assert animeloads_mod._detect_category(content_type) == expected


class TestMatchesCategory:
    @pytest.mark.parametrize(
        ("content_type", "category", "expected"),
        [
            ("Anime Movie", None, True),
            ("Anime Series", None, True),
            ("Anime Series", 5070, True),
            ("OVA", 5000, True),
            ("Live Action", 5030, True),
            ("Anime Movie", 5070, False),
            ("Anime Movie", 2000, True),
            ("Anime Series", 2000, False),
            ("Anime Series", 7000, True),
        ],
    )
    def test_matching(
        self,
        animeloads_mod: ModuleType,
        content_type: str,
        category: int | None,
        expected: bool,
    ) -> None:
        assert animeloads_mod._matches_category(content_type, category) is expected


# ---------------------------------------------------------------------------
# _build_search_result
# ---------------------------------------------------------------------------


class TestBuildSearchResult:
    def test_full_entry(self, plugin: Any) -> None:
        sr = plugin._build_search_result(NARUTO)

        assert sr.title == "Naruto (2002) [220/220]"
        assert sr.category == 5070
        assert sr.source_url == f"{_BASE_URL}/media/naruto"
        assert sr.published_date == "2002"
        assert sr.description == "Ein junger Ninja will Hokage werden."

    def test_embed_url_is_primary_link(self, plugin: Any) -> None:
        sr = plugin._build_search_result(NARUTO)

        assert sr.download_link == "https://voe.sx/e/abc123"
        assert sr.validated_links == ["https://voe.sx/e/abc123"]
        assert sr.download_links == [
            {"hoster": "Preview Stream", "link": "https://voe.sx/e/abc123"},
            {"hoster": "Media Page", "link": f"{_BASE_URL}/media/naruto"},
        ]

    def test_without_embed_falls_back_to_media_page(self, plugin: Any) -> None:
        sr = plugin._build_search_result(NARUTO_MOVIE)

        media = f"{_BASE_URL}/media/naruto-the-movie"
        assert sr.download_link == media
        assert sr.validated_links == [media]
        assert sr.download_links == [{"hoster": "Media Page", "link": media}]
        assert sr.category == 2000

    def test_media_url_built_from_slug_when_missing(self, plugin: Any) -> None:
        entry = _entry("Bleach", mediaUrl="")
        sr = plugin._build_search_result(entry)

        assert sr.source_url == f"{_BASE_URL}/media/bleach"

    def test_no_year_no_episodes(self, plugin: Any) -> None:
        sr = plugin._build_search_result(NARUTO_OVA)

        assert sr.title == "Naruto OVA"
        assert sr.published_date is None

    def test_long_description_truncated(self, plugin: Any) -> None:
        sr = plugin._build_search_result(_entry("X", description="a" * 400))

        assert sr.description is not None
        assert len(sr.description) == 300
        assert sr.description.endswith("...")

    def test_empty_description_is_none(self, plugin: Any) -> None:
        sr = plugin._build_search_result(_entry("X", description=""))

        assert sr.description is None

    def test_metadata(self, plugin: Any) -> None:
        sr = plugin._build_search_result(NARUTO)

        assert sr.metadata == {
            "type": "Anime Series",
            "genres": "Action, Abenteuer",
            "languages": "German, Japanese",
            "subtitles": "German",
            "episodes": "220/220",
            "status": "Completed",
            "poster": f"{_BASE_URL}/files/naruto.jpg",
            "data_id": "42",
        }

    def test_metadata_empty_lists(self, plugin: Any) -> None:
        sr = plugin._build_search_result(
            _entry("X", genres=[], languages=[], subtitles=[])
        )

        assert sr.metadata["genres"] == ""
        assert sr.metadata["languages"] == ""
        assert sr.metadata["subtitles"] == ""


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_returns_results(
        self, animeloads_mod: ModuleType, plugin: Any, page: AsyncMock
    ) -> None:
        _wire_pages(animeloads_mod, page, {1: [NARUTO, NARUTO_MOVIE]}, total_pages=1)

        results = await plugin.search("naruto")

        assert [r.title for r in results] == [
            "Naruto (2002) [220/220]",
            "Naruto the Movie (2004) [1/1]",
        ]
        page.goto.assert_awaited_once()
        assert page.goto.await_args.args[0] == f"{_BASE_URL}/search?q=naruto"
        page.close.assert_awaited_once()

    async def test_query_is_url_encoded(
        self, animeloads_mod: ModuleType, plugin: Any, page: AsyncMock
    ) -> None:
        pages = {1: [_entry("A1")], 2: [_entry("B1")]}
        _wire_pages(animeloads_mod, page, pages, total_pages=2)

        await plugin.search("Fast & Furious")

        urls = [c.args[0] for c in page.goto.await_args_list]
        assert urls == [
            f"{_BASE_URL}/search?q=Fast+%26+Furious",
            f"{_BASE_URL}/search/page/2?q=Fast+%26+Furious",
        ]

    async def test_empty_query(self, plugin: Any) -> None:
        assert await plugin.search("") == []
        plugin._new_page.assert_not_awaited()

    @pytest.mark.parametrize("category", [1000, 3000, 7000, 8000])
    async def test_unsupported_category(self, plugin: Any, category: int) -> None:
        assert await plugin.search("naruto", category=category) == []
        plugin._new_page.assert_not_awaited()

    async def test_movie_category_filters_series(
        self, animeloads_mod: ModuleType, plugin: Any, page: AsyncMock
    ) -> None:
        _wire_pages(animeloads_mod, page, {1: [NARUTO, NARUTO_MOVIE]}, total_pages=1)

        results = await plugin.search("naruto", category=2000)

        assert [r.category for r in results] == [2000]

    async def test_tv_category_filters_movies(
        self, animeloads_mod: ModuleType, plugin: Any, page: AsyncMock
    ) -> None:
        pages = {1: [NARUTO, NARUTO_MOVIE, NARUTO_OVA]}
        _wire_pages(animeloads_mod, page, pages, total_pages=1)

        results = await plugin.search("naruto", category=5070)

        assert len(results) == 2
        assert all(r.category == 5070 for r in results)

    async def test_season_restricts_to_tv_types(
        self, animeloads_mod: ModuleType, plugin: Any, page: AsyncMock
    ) -> None:
        _wire_pages(animeloads_mod, page, {1: [NARUTO, NARUTO_MOVIE]}, total_pages=1)

        results = await plugin.search("naruto", season=1, episode=3)

        assert [r.title for r in results] == ["Naruto (2002) [220/220]"]

    async def test_no_results(
        self, animeloads_mod: ModuleType, plugin: Any, page: AsyncMock
    ) -> None:
        _wire_pages(animeloads_mod, page, {}, total_pages=0)

        assert await plugin.search("nothing") == []
        page.close.assert_awaited_once()

    async def test_navigation_error(self, plugin: Any, page: AsyncMock) -> None:
        page.goto = AsyncMock(side_effect=RuntimeError("net::ERR_TIMED_OUT"))

        assert await plugin.search("naruto") == []
        page.evaluate.assert_not_awaited()
        page.close.assert_awaited_once()

    async def test_extract_error(self, plugin: Any, page: AsyncMock) -> None:
        page.evaluate = AsyncMock(side_effect=RuntimeError("js error"))

        assert await plugin.search("naruto") == []

    async def test_pagination_error_returns_first_page(
        self, animeloads_mod: ModuleType, plugin: Any, page: AsyncMock
    ) -> None:
        async def _evaluate(js: str) -> Any:
            if js == animeloads_mod._EXTRACT_RESULTS_JS:
                return [NARUTO]
            raise RuntimeError("pagination js error")

        page.evaluate = AsyncMock(side_effect=_evaluate)

        results = await plugin.search("naruto")

        assert len(results) == 1
        page.goto.assert_awaited_once()

    async def test_page_closed_on_unexpected_error(
        self, plugin: Any, page: AsyncMock
    ) -> None:
        plugin._verify_domain = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await plugin.search("naruto")
        page.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    async def test_fetches_subsequent_pages(
        self, animeloads_mod: ModuleType, plugin: Any, page: AsyncMock
    ) -> None:
        pages = {
            1: [_entry("A1"), _entry("A2")],
            2: [_entry("B1")],
            3: [_entry("C1")],
        }
        _wire_pages(animeloads_mod, page, pages, total_pages=3)

        results = await plugin.search("naruto")

        assert [r.title.split(" ")[0] for r in results] == ["A1", "A2", "B1", "C1"]
        urls = [c.args[0] for c in page.goto.await_args_list]
        assert urls == [
            f"{_BASE_URL}/search?q=naruto",
            f"{_BASE_URL}/search/page/2?q=naruto",
            f"{_BASE_URL}/search/page/3?q=naruto",
        ]

    async def test_stops_on_empty_page(
        self, animeloads_mod: ModuleType, plugin: Any, page: AsyncMock
    ) -> None:
        pages = {1: [_entry("A1")], 2: [], 3: [_entry("C1")]}
        _wire_pages(animeloads_mod, page, pages, total_pages=3)

        results = await plugin.search("naruto")

        assert len(results) == 1
        assert page.goto.await_count == 2

    async def test_respects_max_pages(
        self, animeloads_mod: ModuleType, plugin: Any, page: AsyncMock
    ) -> None:
        animeloads_mod._MAX_PAGES = 2
        pages = {n: [_entry(f"P{n}")] for n in range(1, 6)}
        _wire_pages(animeloads_mod, page, pages, total_pages=5)

        results = await plugin.search("naruto")

        assert len(results) == 2
        assert page.goto.await_count == 2

    async def test_stops_at_max_results(
        self, animeloads_mod: ModuleType, plugin: Any, page: AsyncMock
    ) -> None:
        plugin._max_results = 3
        pages = {n: [_entry(f"P{n}a"), _entry(f"P{n}b")] for n in range(1, 6)}
        _wire_pages(animeloads_mod, page, pages, total_pages=5)

        results = await plugin.search("naruto")

        assert len(results) == 3
        assert page.goto.await_count == 2

    async def test_first_page_already_at_max_results(
        self, animeloads_mod: ModuleType, plugin: Any, page: AsyncMock
    ) -> None:
        plugin._max_results = 1
        pages = {1: [_entry("A1"), _entry("A2")], 2: [_entry("B1")]}
        _wire_pages(animeloads_mod, page, pages, total_pages=2)

        results = await plugin.search("naruto")

        assert len(results) == 1
        page.goto.assert_awaited_once()


# ---------------------------------------------------------------------------
# DDoS-Guard handling
# ---------------------------------------------------------------------------


class TestDdosGuard:
    async def test_selector_found(self, plugin: Any, page: AsyncMock) -> None:
        assert await plugin._wait_for_ddos_guard(page) is True

    async def test_timeout_on_challenge_page(
        self, plugin: Any, page: AsyncMock
    ) -> None:
        page.wait_for_selector = AsyncMock(side_effect=TimeoutError())
        page.content = AsyncMock(return_value="<html>DDoS-Guard check</html>")

        assert await plugin._wait_for_ddos_guard(page) is False

    async def test_timeout_without_challenge(
        self, plugin: Any, page: AsyncMock
    ) -> None:
        page.wait_for_selector = AsyncMock(side_effect=TimeoutError())
        page.content = AsyncMock(return_value="<html><body>empty</body></html>")

        assert await plugin._wait_for_ddos_guard(page) is True

    async def test_search_aborts_when_challenge_unresolved(
        self, plugin: Any, page: AsyncMock
    ) -> None:
        page.wait_for_selector = AsyncMock(side_effect=TimeoutError())
        page.content = AsyncMock(return_value="ddos-guard")

        assert await plugin.search("naruto") == []
        page.evaluate.assert_not_awaited()


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    async def test_cleanup_without_browser(self, animeloads_mod: ModuleType) -> None:
        p = animeloads_mod.AnimeLoadsPlugin()

        await p.cleanup()  # Should not raise
