"""Tests for CSS-selector-based HTML extraction helpers."""

from __future__ import annotations

from scavengarr.infrastructure.common.html_selectors import (
    extract_all_attrs,
    extract_attr,
    extract_links,
    extract_text,
    parse_html,
    select_items,
)

# ---------------------------------------------------------------------------
# Fixture HTML
# ---------------------------------------------------------------------------

_CARD_HTML = """\
<html><body>
<div class="results">
  <div class="card movie-item">
    <a class="movie-title" href="/film/batman-2022" title="The Batman">
      <h3>The Batman</h3>
    </a>
    <span class="year">2022</span>
    <span class="quality">1080p</span>
    <img src="/poster.jpg" alt="poster" data-id="42">
  </div>
  <div class="card movie-item">
    <a class="movie-title" href="/film/dark-knight" title="The Dark Knight">
      <h3>The Dark Knight</h3>
    </a>
    <span class="year">2008</span>
  </div>
  <div class="card movie-item empty">
    <a class="movie-title" href="/film/empty">
    </a>
  </div>
</div>
</body></html>
"""

_LINKS_HTML = """\
<div class="hosters">
  <a href="/link/voe" class="hoster-btn">VOE</a>
  <a href="/link/vidoza" class="hoster-btn">Vidoza</a>
  <a class="broken">No href</a>
  <a href="/link/streamtape" class="hoster-btn">Streamtape</a>
</div>
"""


# ---------------------------------------------------------------------------
# parse_html
# ---------------------------------------------------------------------------


class TestParseHtml:
    def test_returns_soup(self) -> None:
        soup = parse_html("<div>hello</div>")
        assert soup is not None
        assert soup.find("div") is not None

    def test_empty_html(self) -> None:
        soup = parse_html("")
        assert soup is not None


# ---------------------------------------------------------------------------
# select_items
# ---------------------------------------------------------------------------


class TestSelectItems:
    def test_primary_selector_matches(self) -> None:
        soup = parse_html(_CARD_HTML)
        items = select_items(soup, "div.card")
        assert len(items) == 3

    def test_fallback_selector_used(self) -> None:
        soup = parse_html(_CARD_HTML)
        # Primary won't match; fallback will
        items = select_items(soup, "div.nonexistent", "div.card")
        assert len(items) == 3

    def test_no_match_returns_empty(self) -> None:
        soup = parse_html(_CARD_HTML)
        items = select_items(soup, "div.nope", "span.nope")
        assert items == []

    def test_primary_preferred_over_fallback(self) -> None:
        soup = parse_html(_CARD_HTML)
        # Primary matches 3 cards; fallback would match 2 links
        items = select_items(soup, "div.card", "a.movie-title")
        assert len(items) == 3  # Primary wins


# ---------------------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_primary_selector(self) -> None:
        soup = parse_html(_CARD_HTML)
        card = soup.select_one("div.card")
        assert card is not None
        text = extract_text(card, "h3")
        assert text == "The Batman"

    def test_fallback_selector(self) -> None:
        soup = parse_html(_CARD_HTML)
        card = soup.select_one("div.card")
        assert card is not None
        text = extract_text(card, "h1", "h2", "h3")
        assert text == "The Batman"

    def test_default_when_no_match(self) -> None:
        soup = parse_html(_CARD_HTML)
        card = soup.select_one("div.card")
        assert card is not None
        text = extract_text(card, "h1", "h2", default="N/A")
        assert text == "N/A"

    def test_empty_selector_returns_own_text(self) -> None:
        soup = parse_html(_CARD_HTML)
        span = soup.select_one("span.year")
        assert span is not None
        text = extract_text(span, "")
        assert text == "2022"

    def test_skips_empty_text_matches(self) -> None:
        """If primary matches but has empty text, tries fallback."""
        soup = parse_html(_CARD_HTML)
        empty_card = soup.select("div.card")[2]  # The empty one
        # The <a> in this card has no text, so fallback to h3 (also empty)
        text = extract_text(empty_card, "a", "h3", default="fallback")
        assert text == "fallback"


# ---------------------------------------------------------------------------
# extract_attr
# ---------------------------------------------------------------------------


class TestExtractAttr:
    def test_primary_selector(self) -> None:
        soup = parse_html(_CARD_HTML)
        card = soup.select_one("div.card")
        assert card is not None
        href = extract_attr(card, "a.movie-title", "href")
        assert href == "/film/batman-2022"

    def test_fallback_selector(self) -> None:
        soup = parse_html(_CARD_HTML)
        card = soup.select_one("div.card")
        assert card is not None
        href = extract_attr(card, "a.nonexistent", "href", "a.movie-title")
        assert href == "/film/batman-2022"

    def test_default_when_no_match(self) -> None:
        soup = parse_html(_CARD_HTML)
        card = soup.select_one("div.card")
        assert card is not None
        val = extract_attr(card, "a.nope", "href", default="none")
        assert val == "none"

    def test_empty_selector_reads_own_attr(self) -> None:
        soup = parse_html(_CARD_HTML)
        img = soup.select_one("img")
        assert img is not None
        val = extract_attr(img, "", "data-id")
        assert val == "42"

    def test_title_attr(self) -> None:
        soup = parse_html(_CARD_HTML)
        card = soup.select_one("div.card")
        assert card is not None
        title = extract_attr(card, "a[title]", "title")
        assert title == "The Batman"


# ---------------------------------------------------------------------------
# extract_all_attrs
# ---------------------------------------------------------------------------


class TestExtractAllAttrs:
    def test_extracts_all_hrefs(self) -> None:
        soup = parse_html(_LINKS_HTML)
        container = soup.select_one("div.hosters")
        assert container is not None
        hrefs = extract_all_attrs(container, "a[href]", "href")
        assert hrefs == ["/link/voe", "/link/vidoza", "/link/streamtape"]

    def test_fallback_used(self) -> None:
        soup = parse_html(_LINKS_HTML)
        container = soup.select_one("div.hosters")
        assert container is not None
        hrefs = extract_all_attrs(container, "a.nonexistent", "href", "a.hoster-btn")
        assert len(hrefs) == 3

    def test_empty_when_no_match(self) -> None:
        soup = parse_html(_LINKS_HTML)
        container = soup.select_one("div.hosters")
        assert container is not None
        result = extract_all_attrs(container, "div.nope", "href")
        assert result == []


# ---------------------------------------------------------------------------
# extract_links
# ---------------------------------------------------------------------------


class TestExtractLinks:
    def test_extracts_all_valid_links(self) -> None:
        soup = parse_html(_LINKS_HTML)
        container = soup.select_one("div.hosters")
        assert container is not None
        links = extract_links(container)
        assert len(links) == 3
        assert links[0]["text"] == "VOE"
        assert links[0]["href"] == "/link/voe"

    def test_with_base_url(self) -> None:
        soup = parse_html(_LINKS_HTML)
        container = soup.select_one("div.hosters")
        assert container is not None
        links = extract_links(container, base_url="https://example.com")
        assert links[0]["href"] == "https://example.com/link/voe"

    def test_custom_selector(self) -> None:
        soup = parse_html(_LINKS_HTML)
        container = soup.select_one("div.hosters")
        assert container is not None
        links = extract_links(container, "a.hoster-btn")
        assert len(links) == 3

    def test_fallback_selector_for_links(self) -> None:
        soup = parse_html(_LINKS_HTML)
        container = soup.select_one("div.hosters")
        assert container is not None
        links = extract_links(container, "a.nonexistent", "a.hoster-btn")
        assert len(links) == 3

    def test_skips_links_without_href(self) -> None:
        soup = parse_html(_LINKS_HTML)
        container = soup.select_one("div.hosters")
        assert container is not None
        # Select all <a> tags (including the broken one)
        all_a = container.select("a")
        assert len(all_a) == 4  # One has no href
        links = extract_links(container)
        assert len(links) == 3  # Broken one skipped
