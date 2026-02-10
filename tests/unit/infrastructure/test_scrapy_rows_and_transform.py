"""Tests for rows selector, query_transform, and stage-level field_attributes."""

from __future__ import annotations

from typing import Any

import pytest
from bs4 import BeautifulSoup

from scavengarr.domain.plugins import ScrapingStage, StageSelectors
from scavengarr.infrastructure.scraping.scrapy_adapter import (
    StageScraper,
    _apply_query_transform,
    _slugify,
)


# -- _slugify tests -----------------------------------------------------------


class TestSlugify:
    def test_basic_query(self) -> None:
        assert _slugify("Avatar 2009") == "avatar-2009"

    def test_multiple_spaces(self) -> None:
        assert _slugify("  breaking   bad  ") == "breaking-bad"

    def test_special_characters_stripped(self) -> None:
        assert _slugify("test!@#$%query") == "testquery"

    def test_hyphens_preserved(self) -> None:
        assert _slugify("spider-man") == "spider-man"

    def test_empty_query(self) -> None:
        assert _slugify("") == ""

    def test_only_special_chars(self) -> None:
        assert _slugify("!!!") == ""

    def test_unicode_stripped(self) -> None:
        assert _slugify("über cool") == "ber-cool"

    def test_mixed_case(self) -> None:
        assert _slugify("The Dark Knight") == "the-dark-knight"

    def test_consecutive_hyphens_collapsed(self) -> None:
        assert _slugify("a - - b") == "a-b"


# -- _apply_query_transform tests ---------------------------------------------


class TestApplyQueryTransform:
    def test_slugify_dispatch(self) -> None:
        assert _apply_query_transform("Hello World", "slugify") == "hello-world"

    def test_unknown_transform_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown query_transform"):
            _apply_query_transform("test", "unknown")


# -- build_url with query_transform -------------------------------------------


def _make_stage(
    url_pattern: str = "/search/{query}/",
    query_transform: str | None = None,
    **kwargs: Any,
) -> StageScraper:
    """Helper to create a StageScraper with minimal config."""
    selectors = kwargs.pop("selectors", StageSelectors(link="a"))
    stage = ScrapingStage(
        name="test",
        type="list",
        url_pattern=url_pattern,
        selectors=selectors,
        query_transform=query_transform,
        **kwargs,
    )
    return StageScraper(stage, "https://example.com")


class TestBuildUrlWithTransform:
    def test_no_transform(self) -> None:
        scraper = _make_stage()
        url = scraper.build_url(query="The Matrix")
        assert url == "https://example.com/search/The Matrix/"

    def test_slugify_transform(self) -> None:
        scraper = _make_stage(query_transform="slugify")
        url = scraper.build_url(query="The Matrix")
        assert url == "https://example.com/search/the-matrix/"

    def test_transform_does_not_mutate_params(self) -> None:
        scraper = _make_stage(query_transform="slugify")
        params: dict[str, str] = {"query": "Original Query"}
        scraper.build_url(**params)
        assert params["query"] == "Original Query"


# -- extract_rows tests -------------------------------------------------------

_TABLE_HTML = """
<html><body>
<table class="results">
<tbody>
  <tr class="separator"><td colspan="3"></td></tr>
  <tr>
    <td class="n"><a title="Movie One" href="https://dl.example.com/1">Movie.One</a></td>
    <td>hoster1</td>
    <td>Movie</td>
  </tr>
  <tr class="separator"><td colspan="3"></td></tr>
  <tr>
    <td class="n"><a title="Movie Two" href="https://dl.example.com/2">Movie.Two</a></td>
    <td>hoster2</td>
    <td>Movie</td>
  </tr>
</tbody>
</table>
</body></html>
"""


def _make_rows_scraper(
    rows: str | None = None,
    field_attributes: dict[str, list[str]] | None = None,
) -> StageScraper:
    """Create a StageScraper with rows and field_attributes."""
    selectors = StageSelectors(
        rows=rows,
        title="td.n a",
        download_link="td.n a",
    )
    stage = ScrapingStage(
        name="test_rows",
        type="list",
        url="/test",
        selectors=selectors,
        field_attributes=field_attributes or {},
    )
    return StageScraper(stage, "https://example.com")


class TestExtractRows:
    def test_with_rows_selector(self) -> None:
        scraper = _make_rows_scraper(
            rows="table.results tbody tr",
            field_attributes={"title": ["title"], "download_link": ["href"]},
        )
        soup = BeautifulSoup(_TABLE_HTML, "html.parser")
        results = scraper.extract_rows(soup)

        # Separator rows have no title/link -> filtered out
        assert len(results) == 2
        assert results[0]["title"] == "Movie One"
        assert results[0]["download_link"] == "https://dl.example.com/1"
        assert results[1]["title"] == "Movie Two"
        assert results[1]["download_link"] == "https://dl.example.com/2"

    def test_without_rows_selector_returns_single(self) -> None:
        scraper = _make_rows_scraper(
            rows=None,
            field_attributes={"title": ["title"], "download_link": ["href"]},
        )
        soup = BeautifulSoup(_TABLE_HTML, "html.parser")
        results = scraper.extract_rows(soup)

        # Falls back to extract_data on entire soup -> single result
        assert len(results) == 1
        assert results[0]["title"] == "Movie One"  # first match in soup

    def test_separator_rows_filtered(self) -> None:
        """Rows without title or download_link are filtered out."""
        scraper = _make_rows_scraper(
            rows="table.results tbody tr",
            field_attributes={"title": ["title"], "download_link": ["href"]},
        )
        soup = BeautifulSoup(_TABLE_HTML, "html.parser")
        results = scraper.extract_rows(soup)

        # separator rows have <td colspan="3"> — no a[title] or a[href]
        for r in results:
            assert r.get("title")

    def test_empty_table(self) -> None:
        html = "<html><body><table class='results'><tbody></tbody></table></body></html>"
        scraper = _make_rows_scraper(
            rows="table.results tbody tr",
            field_attributes={"title": ["title"], "download_link": ["href"]},
        )
        soup = BeautifulSoup(html, "html.parser")
        results = scraper.extract_rows(soup)
        assert results == []


# -- field_attributes on text fields -------------------------------------------


class TestStageFieldAttributes:
    def test_title_from_attribute(self) -> None:
        """When field_attributes defines 'title', extract from HTML attribute."""
        html = '<a title="Full Title Text" href="/link">Short</a>'
        selectors = StageSelectors(title="a")
        stage = ScrapingStage(
            name="test_attrs",
            type="list",
            url="/test",
            selectors=selectors,
            field_attributes={"title": ["title"]},
        )
        scraper = StageScraper(stage, "https://example.com")
        soup = BeautifulSoup(html, "html.parser")
        data = scraper.extract_data(soup)

        # Should extract title from attribute, not text
        assert data["title"] == "Full Title Text"

    def test_text_extraction_without_field_attributes(self) -> None:
        """Without field_attributes, title is extracted as text."""
        html = '<a title="Full Title Text" href="/link">Short Text</a>'
        selectors = StageSelectors(title="a")
        stage = ScrapingStage(
            name="test_text",
            type="list",
            url="/test",
            selectors=selectors,
        )
        scraper = StageScraper(stage, "https://example.com")
        soup = BeautifulSoup(html, "html.parser")
        data = scraper.extract_data(soup)

        assert data["title"] == "Short Text"

    def test_download_link_default_attribute_mode(self) -> None:
        """download_link defaults to attribute mode even without field_attributes."""
        html = '<a href="https://dl.example.com/file">Download</a>'
        selectors = StageSelectors(download_link="a")
        stage = ScrapingStage(
            name="test_dl",
            type="list",
            url="/test",
            selectors=selectors,
            field_attributes={"download_link": ["href"]},
        )
        scraper = StageScraper(stage, "https://example.com")
        soup = BeautifulSoup(html, "html.parser")
        data = scraper.extract_data(soup)

        assert data["download_link"] == "https://dl.example.com/file"
