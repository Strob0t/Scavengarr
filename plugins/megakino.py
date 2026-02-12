"""megakino.me Python plugin for Scavengarr.

Scrapes megakino.me (German streaming site, DLE-based CMS) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- POST /index.php?do=search with story={query} for keyword search
- Pagination via search_start/result_from POST params (20 results/page)
- Detail page scraping for stream tab links (film) and select hosters (series)
- Series detection from "Serien" in genre text or "Staffel" in title
- Category filtering (Movies/TV/Animation)
- Bounded concurrency for detail page scraping

Single domain: megakino.me (other megakino domains have JS challenges).
No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["megakino.me"]
_RESULTS_PER_PAGE = 20
_MAX_PAGES = 50  # 1000 / 20

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TV_CATEGORIES = frozenset({5000, 5010, 5020, 5030, 5040, 5050, 5060, 5070, 5080})
_MOVIE_CATEGORIES = frozenset({2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060})

_QUALITY_LABELS = frozenset(
    {
        "hd",
        "sd",
        "4k",
        "webrip",
        "bdrip",
        "camrip",
        "ts",
        "cam/md",
        "hdtv",
        "cam",
        "hdcam",
    }
)


def _filter_by_category(
    results: list[SearchResult],
    category: int,
) -> list[SearchResult]:
    """Filter results by Torznab category type."""
    if category in _TV_CATEGORIES:
        return [r for r in results if r.category >= 5000]
    if category in _MOVIE_CATEGORIES:
        return [r for r in results if r.category < 5000]
    return results


def _detect_series(categories_text: str, title: str) -> bool:
    """Detect if an item is a series from category text or title."""
    parts = [p.strip().lower() for p in categories_text.split("/")]
    if "serien" in parts:
        return True
    if "staffel" in title.lower():
        return True
    return False


def _detect_category(genres: list[str], is_series: bool) -> int:
    """Determine Torznab category from genres and series flag."""
    lower_genres = [g.lower() for g in genres]
    if "animation" in lower_genres:
        return 5070
    if is_series:
        return 5000
    return 2000


def _clean_title(title: str) -> str:
    """Strip common suffixes and trailing year."""
    title = title.strip()
    for suffix in (" Film", " Serie", " film", " serie"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
    return title.strip()


def _domain_from_url(url: str) -> str:
    """Extract domain name from a URL for hoster labeling."""
    try:
        host = urlparse(url).hostname or ""
        parts = host.replace("www.", "").split(".")
        return parts[0] if parts and parts[0] else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _parse_genres(text: str) -> list[str]:
    """Parse genres from 'Filme / Action / Thriller' format.

    Filters out top-level site categories.
    """
    parts = [p.strip() for p in text.split("/")]
    skip = {"filme", "kinofilme", "serien", "dokumentationen"}
    return [p for p in parts if p and p.lower() not in skip]


def _parse_year(text: str) -> str:
    """Extract four-digit year from text."""
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return m.group(0) if m else ""


def _parse_runtime(text: str) -> str:
    """Extract runtime minutes from text like 'Country, 2024, 120 min'."""
    m = re.search(r"(\d+)\s*min", text)
    return m.group(1) if m else ""


class _SearchResultParser(HTMLParser):
    """Parse megakino.me search result page.

    Each result card::

        <a class="poster grid-item ..." href="/crime/4692-title.html">
          <div class="poster__img ...">
            <img data-src="..." alt="...">
            <div class="poster__label">HD</div>
          </div>
          <div class="poster__desc">
            <h3 class="poster__title ...">Title</h3>
            <ul class="poster__subtitle ...">
              <li>Country, Year</li>
              <li>Genre1 / Genre2 / Category</li>
            </ul>
            <div class="poster__text ...">Description</div>
          </div>
        </a>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str | list[str] | bool]] = []
        self._base_url = base_url

        self._in_card = False
        self._current_url = ""

        self._in_title = False
        self._title_text = ""

        self._in_label = False
        self._label_text = ""

        self._in_subtitle = False
        self._in_subtitle_li = False
        self._subtitle_li_text = ""
        self._subtitle_items: list[str] = []

        self._in_desc = False
        self._desc_text = ""

        self._poster_url = ""

    def _reset_card(self) -> None:
        self._current_url = ""
        self._title_text = ""
        self._label_text = ""
        self._subtitle_items = []
        self._desc_text = ""
        self._poster_url = ""

    def _emit_card(self) -> None:
        if not self._title_text or not self._current_url:
            return

        categories_text = (
            self._subtitle_items[1] if len(self._subtitle_items) > 1 else ""
        )
        genres = _parse_genres(categories_text)
        is_series = _detect_series(categories_text, self._title_text)

        year = ""
        if self._subtitle_items:
            year = _parse_year(self._subtitle_items[0])

        quality = ""
        if self._label_text.strip().lower() in _QUALITY_LABELS:
            quality = self._label_text.strip()

        self.results.append(
            {
                "title": _clean_title(self._title_text),
                "url": self._current_url,
                "genres": genres,
                "quality": quality,
                "label": self._label_text.strip(),
                "is_series": is_series,
                "year": year,
                "description": self._desc_text.strip(),
                "poster_url": self._poster_url,
                "categories_text": categories_text,
            }
        )

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # Card boundary: <a class="poster grid-item ..." href="...">
        if tag == "a" and "poster" in classes and "grid-item" in classes:
            self._in_card = True
            self._reset_card()
            href = attr_dict.get("href", "") or ""
            if href:
                self._current_url = urljoin(self._base_url, href)
            return

        if not self._in_card:
            return

        # Title: <h3 class="poster__title ...">
        if tag == "h3" and "poster__title" in classes:
            self._in_title = True
            self._title_text = ""

        # Label: <div class="poster__label">
        if tag == "div" and "poster__label" in classes:
            self._in_label = True
            self._label_text = ""

        # Subtitle list: <ul class="poster__subtitle ...">
        if tag == "ul" and "poster__subtitle" in classes:
            self._in_subtitle = True

        if tag == "li" and self._in_subtitle:
            self._in_subtitle_li = True
            self._subtitle_li_text = ""

        # Description: <div class="poster__text ...">
        if tag == "div" and "poster__text" in classes:
            self._in_desc = True
            self._desc_text = ""

        # Poster image: <img data-src="...">
        if tag == "img" and not self._poster_url:
            data_src = attr_dict.get("data-src", "") or ""
            if data_src:
                self._poster_url = urljoin(self._base_url, data_src)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_text += data
        if self._in_label:
            self._label_text += data
        if self._in_subtitle_li:
            self._subtitle_li_text += data
        if self._in_desc:
            self._desc_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._in_title:
            self._in_title = False

        if tag == "div" and self._in_label:
            self._in_label = False

        if tag == "li" and self._in_subtitle_li:
            self._in_subtitle_li = False
            text = self._subtitle_li_text.strip()
            if text:
                self._subtitle_items.append(text)

        if tag == "ul" and self._in_subtitle:
            self._in_subtitle = False

        if tag == "div" and self._in_desc:
            self._in_desc = False

        # Card closes when the wrapping <a> tag ends
        if tag == "a" and self._in_card:
            self._in_card = False
            self._emit_card()


class _DetailPageParser(HTMLParser):
    """Parse megakino.me detail page for stream links and metadata.

    Film hosters use tabs::

        <div class="tabs-block__select ...">
          <span class="is-active">Voe</span>
          <span>Doodstream</span>
        </div>
        <div class="tabs-block__content ...">
          <a href="/dl/12345">...</a>
        </div>

    Series hosters use select elements::

        <select class="mr-select" id="ep1">
          <option value="https://voe.sx/e/abc">Voe</option>
        </select>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

        # Stream links (final output)
        self.stream_links: list[dict[str, str]] = []

        # Film tabs tracking
        self._in_tabs_select = False
        self._tabs_select_div_depth = 0
        self._in_tab_span = False
        self._tab_span_text = ""
        self._tab_names: list[str] = []

        self._in_tabs_content = False
        self._tabs_content_div_depth = 0
        self._content_dl_href = ""
        self._content_index = 0

        # Series select tracking
        self._in_mr_select = False
        self._first_mr_select_done = False
        self._in_mr_option = False
        self._mr_option_value = ""
        self._mr_option_text = ""

        # Title
        self._in_h1 = False
        self._h1_text = ""
        self.title = ""

        # Year/runtime
        self._in_year_div = False
        self._year_text = ""
        self.year = ""
        self.runtime = ""

        # Genres
        self._in_genres_div = False
        self._genres_text = ""
        self.genres: list[str] = []
        self.categories_text = ""

        # Description
        self._in_desc = False
        self._desc_div_depth = 0
        self._desc_text = ""
        self.description = ""

        # Ratings
        self._in_kp_rating = False
        self._kp_text = ""
        self.kp_rating = ""

        self._in_site_rating = False
        self._site_rating_text = ""
        self.site_rating = ""

        # Poster
        self._in_poster_div = False
        self.poster_url = ""

        # Series flag
        self.is_series = False

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # h1 title
        if tag == "h1" and not self.title:
            self._in_h1 = True
            self._h1_text = ""

        # Year/runtime: <div class="pmovie__year">
        if tag == "div" and "pmovie__year" in classes:
            self._in_year_div = True
            self._year_text = ""

        # Genres: <div class="pmovie__genres">
        if tag == "div" and "pmovie__genres" in classes:
            self._in_genres_div = True
            self._genres_text = ""

        # Description: <div class="... full-text ...">
        if tag == "div":
            if self._in_desc:
                self._desc_div_depth += 1
            elif "full-text" in classes:
                self._in_desc = True
                self._desc_div_depth = 0
                self._desc_text = ""

        # KP rating
        if "pmovie__subrating--kp" in classes:
            self._in_kp_rating = True
            self._kp_text = ""

        # Site rating
        if "pmovie__subrating--site" in classes:
            self._in_site_rating = True
            self._site_rating_text = ""

        # Poster: <div class="pmovie__poster ...">
        if tag == "div" and "pmovie__poster" in classes:
            self._in_poster_div = True

        if tag == "img" and self._in_poster_div and not self.poster_url:
            data_src = attr_dict.get("data-src", "") or attr_dict.get("src", "") or ""
            if data_src and "/no-img" not in data_src:
                self.poster_url = urljoin(self._base_url, data_src)

        # --- Film hosters: tabs-block ---
        if tag == "div" and "tabs-block__select" in classes:
            self._in_tabs_select = True
            self._tabs_select_div_depth = 0
        elif tag == "div" and self._in_tabs_select:
            self._tabs_select_div_depth += 1

        if tag == "span" and self._in_tabs_select:
            self._in_tab_span = True
            self._tab_span_text = ""

        if tag == "div" and "tabs-block__content" in classes:
            self._in_tabs_content = True
            self._tabs_content_div_depth = 0
            self._content_dl_href = ""
        elif tag == "div" and self._in_tabs_content:
            self._tabs_content_div_depth += 1

        # <a href="/dl/..."> inside tabs-block__content
        if tag == "a" and self._in_tabs_content:
            href = attr_dict.get("href", "") or ""
            if href and "/dl/" in href:
                self._content_dl_href = urljoin(self._base_url, href)

        # --- Series hosters: mr-select ---
        if tag == "select" and "mr-select" in classes:
            if not self._first_mr_select_done:
                self._in_mr_select = True

        if tag == "option" and self._in_mr_select:
            value = attr_dict.get("value", "") or ""
            if value and value.startswith("http"):
                self._in_mr_option = True
                self._mr_option_value = value
                self._mr_option_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._h1_text += data
        if self._in_year_div:
            self._year_text += data
        if self._in_genres_div:
            self._genres_text += data
        if self._in_desc:
            self._desc_text += data
        if self._in_kp_rating:
            self._kp_text += data
        if self._in_site_rating:
            self._site_rating_text += data
        if self._in_tab_span:
            self._tab_span_text += data
        if self._in_mr_option:
            self._mr_option_text += data

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        # h1
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.title = _clean_title(self._h1_text)

        # Year div
        if tag == "div" and self._in_year_div:
            self._in_year_div = False
            self.year = _parse_year(self._year_text)
            self.runtime = _parse_runtime(self._year_text)

        # Genres div
        if tag == "div" and self._in_genres_div:
            self._in_genres_div = False
            self.categories_text = self._genres_text.strip()
            self.genres = _parse_genres(self.categories_text)

        # Description
        if tag == "div" and self._in_desc:
            if self._desc_div_depth > 0:
                self._desc_div_depth -= 1
            else:
                self._in_desc = False
                self.description = self._desc_text.strip()

        # KP rating
        if self._in_kp_rating and tag in ("div", "span"):
            self._in_kp_rating = False
            m = re.search(r"(\d+[.,]?\d*)", self._kp_text)
            if m:
                self.kp_rating = m.group(1).replace(",", ".")

        # Site rating
        if self._in_site_rating and tag in ("div", "span"):
            self._in_site_rating = False
            m = re.search(r"(\d+[.,]?\d*)", self._site_rating_text)
            if m:
                self.site_rating = m.group(1).replace(",", ".")

        # Poster div
        if tag == "div" and self._in_poster_div:
            self._in_poster_div = False

        # Tab select span
        if tag == "span" and self._in_tab_span:
            self._in_tab_span = False
            name = self._tab_span_text.strip()
            if name:
                self._tab_names.append(name)

        # Tabs select div
        if tag == "div" and self._in_tabs_select:
            if self._tabs_select_div_depth > 0:
                self._tabs_select_div_depth -= 1
            else:
                self._in_tabs_select = False

        # Tabs content div
        if tag == "div" and self._in_tabs_content:
            if self._tabs_content_div_depth > 0:
                self._tabs_content_div_depth -= 1
            else:
                self._in_tabs_content = False
                if self._content_dl_href:
                    label = ""
                    if self._content_index < len(self._tab_names):
                        label = self._tab_names[self._content_index]
                    hoster = _domain_from_url(self._content_dl_href)
                    if label:
                        hoster = label.lower()
                    self.stream_links.append(
                        {
                            "hoster": hoster,
                            "link": self._content_dl_href,
                            "label": label,
                        }
                    )
                self._content_index += 1

        # mr-select option
        if tag == "option" and self._in_mr_option:
            self._in_mr_option = False
            name = self._mr_option_text.strip()
            if self._mr_option_value:
                domain = _domain_from_url(self._mr_option_value)
                self.stream_links.append(
                    {
                        "hoster": name.lower() if name else domain,
                        "link": self._mr_option_value,
                        "label": name or domain,
                    }
                )

        # mr-select close
        if tag == "select" and self._in_mr_select:
            self._in_mr_select = False
            self._first_mr_select_done = True

    def finalize(self) -> None:
        """Post-processing: detect series from genres."""
        self.is_series = _detect_series(self.categories_text, self.title)


class MegakinoPlugin(HttpxPluginBase):
    """Python plugin for megakino.me using httpx."""

    name = "megakino"
    provides = "stream"
    _domains = _DOMAINS

    async def _search_page(
        self,
        query: str,
        page: int = 0,
    ) -> list[dict[str, str | list[str] | bool]]:
        """Fetch a search results page via POST.

        DLE CMS search uses::
            POST /index.php?do=search
            Form data: do=search, subaction=search, story={query},
                        search_start={N}, result_from={offset}
        """
        client = await self._ensure_client()

        form_data: dict[str, str] = {
            "do": "search",
            "subaction": "search",
            "story": query,
            "search_start": str(page),
            "full_search": "0",
            "result_from": str(max(1, (page - 1) * _RESULTS_PER_PAGE + 1)),
        }

        try:
            resp = await client.post(
                f"{self.base_url}/index.php?do=search",
                data=form_data,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "megakino_search_failed",
                query=query,
                page=page,
                error=str(exc),
            )
            return []

        parser = _SearchResultParser(self.base_url)
        parser.feed(resp.text)

        self._log.info(
            "megakino_search_page",
            query=query,
            page=page,
            results=len(parser.results),
        )
        return parser.results

    async def _search_all_pages(
        self,
        query: str,
    ) -> list[dict[str, str | list[str] | bool]]:
        """Fetch search results with pagination up to _max_results."""
        all_results: list[dict[str, str | list[str] | bool]] = []

        for page_num in range(_MAX_PAGES):
            results = await self._search_page(query, page_num)
            if not results:
                break
            all_results.extend(results)
            if len(all_results) >= self._max_results:
                break
            # If we got fewer results than a full page, no more pages
            if len(results) < _RESULTS_PER_PAGE:
                break

        return all_results[: self._max_results]

    async def _scrape_detail(
        self,
        result: dict[str, str | list[str] | bool],
    ) -> SearchResult | None:
        """Scrape a detail page for stream links and metadata."""
        client = await self._ensure_client()
        detail_url = str(result["url"])

        try:
            resp = await client.get(detail_url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "megakino_detail_failed",
                url=detail_url,
                error=str(exc),
            )
            return None

        parser = _DetailPageParser(self.base_url)
        parser.feed(resp.text)
        parser.finalize()

        if not parser.stream_links:
            self._log.debug("megakino_no_streams", url=detail_url)
            return None

        title = parser.title or str(result.get("title", ""))
        genres = parser.genres or list(result.get("genres", []))
        is_series = parser.is_series or bool(result.get("is_series", False))
        quality = str(result.get("quality", ""))
        category = _detect_category(genres, is_series)

        description_parts: list[str] = []
        if genres:
            description_parts.append(", ".join(genres))
        if parser.year:
            description_parts.append(f"({parser.year})")
        if parser.description:
            description_parts.append(parser.description)
        description = " ".join(description_parts) if description_parts else ""

        metadata: dict[str, str] = {
            "year": parser.year,
            "genres": ", ".join(genres),
            "quality": quality,
            "kp_rating": parser.kp_rating,
            "site_rating": parser.site_rating,
            "poster_url": parser.poster_url,
        }

        return SearchResult(
            title=title,
            download_link=parser.stream_links[0]["link"],
            download_links=parser.stream_links,
            source_url=detail_url,
            category=category,
            description=description,
            metadata=metadata,
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search megakino.me and return results with stream links."""
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        all_items = await self._search_all_pages(query)
        if not all_items:
            return []

        # Scrape detail pages with bounded concurrency
        sem = self._new_semaphore()

        async def _bounded(
            r: dict[str, str | list[str] | bool],
        ) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(r)

        gathered = await asyncio.gather(
            *[_bounded(r) for r in all_items],
            return_exceptions=True,
        )

        results: list[SearchResult] = [
            r for r in gathered if isinstance(r, SearchResult)
        ]

        # When season is requested, restrict to series results
        effective_category = category
        if season is not None and effective_category is None:
            effective_category = 5000

        if effective_category is not None:
            results = _filter_by_category(results, effective_category)

        return results


plugin = MegakinoPlugin()
