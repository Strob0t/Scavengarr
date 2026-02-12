"""kinoger.com Python plugin for Scavengarr.

Scrapes kinoger.com (German streaming site, DLE-based CMS) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- GET /index.php?do=search&subaction=search&story={query} for keyword search
- Pagination via search_start={N} parameter (12 results/page, up to 84 pages)
- Detail page scraping for stream tabs (iframe URLs from tab sections)
- Series detection from badge text (S01-04, S01E01-02) or "Serie" in genres
- Category filtering (Movies/TV/Anime)
- Bounded concurrency for detail page scraping

Multi-domain support: kinoger.com (primary), kinoger.to (fallback).
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
_DOMAINS = ["kinoger.com", "kinoger.to"]
_MAX_PAGES = 84  # 12 results/page → 84 pages for ~1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TV_CATEGORIES = frozenset({5000, 5010, 5020, 5030, 5040, 5050, 5060, 5070, 5080})
_MOVIE_CATEGORIES = frozenset({2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060})

# Series badge pattern: S01, S01-04, S01E01-02, etc.
_SERIES_BADGE_RE = re.compile(r"S\d+", re.IGNORECASE)


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


def _detect_series(badge: str, genres: list[str]) -> bool:
    """Detect if an item is a series based on badge text or genres."""
    if _SERIES_BADGE_RE.search(badge):
        return True
    lower_genres = [g.lower() for g in genres]
    return "serie" in lower_genres or "serien" in lower_genres


def _detect_category(genres: list[str], is_series: bool) -> int:
    """Determine Torznab category from genres and series flag."""
    if is_series:
        lower_genres = [g.lower() for g in genres]
        if "anime" in lower_genres or "animation" in lower_genres:
            return 5070
        return 5000
    lower_genres = [g.lower() for g in genres]
    if "anime" in lower_genres or "animation" in lower_genres:
        return 5070
    return 2000


def _clean_title(title: str) -> str:
    """Strip common suffixes like ' Film', ' Serie', trailing year."""
    title = title.strip()
    for suffix in (" Film", " Serie", " film", " serie"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    # Strip trailing year in parens: "Title (2023)"
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


class _SearchResultParser(HTMLParser):
    """Parse kinoger.com DLE search result page.

    Each result is a pair of sibling divs::

        <div class="titlecontrol">
          <div class="title">
            <a href="https://kinoger.com/stream/1499-matrix-1999.html">
              Matrix (1999) Film
            </a>
          </div>
        </div>
        <div class="general_box">
          <div class="headerbar">
            <ul class="postinfo">
              <li class="category">
                <a href="...">Stream</a> / <a href="...">Sci-Fi</a>
              </li>
            </ul>
          </div>
          <div class="content_text searchresult_img">
            <b><div style="text-align:right;">DVDRip</div></b>
            ...
          </div>
        </div>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str | list[str] | bool]] = []
        self._base_url = base_url

        # Phase tracking: titlecontrol → general_box
        self._in_titlecontrol = False
        self._titlecontrol_depth = 0
        self._in_title_div = False
        self._title_div_depth = 0
        self._in_title_a = False

        self._in_general_box = False
        self._general_box_depth = 0

        # Category <li> inside general_box
        self._in_category_li = False
        self._in_category_a = False
        self._category_text = ""

        # Quality from content_text
        self._in_content_text = False
        self._content_text_depth = 0
        self._in_bold = False
        self._quality_text = ""

        # Accumulated card data
        self._current_title = ""
        self._current_url = ""
        self._genres: list[str] = []
        self._quality = ""

    def _reset_card(self) -> None:
        self._current_title = ""
        self._current_url = ""
        self._genres = []
        self._quality = ""

    def _emit_card(self) -> None:
        if not self._current_title or not self._current_url:
            self._reset_card()
            return

        # Filter out "Stream" from genres
        genres = [g for g in self._genres if g.lower() != "stream"]

        # Classify quality text: might be a series badge (S01, S01-04) or quality
        quality = ""
        series_badge = ""
        if self._quality:
            if _SERIES_BADGE_RE.search(self._quality):
                series_badge = self._quality
            else:
                quality = self._quality

        is_series = _detect_series(series_badge, genres)

        # Also detect series from title suffix ("Serie" is stripped by _clean_title)
        raw_title = self._current_title.strip()
        if raw_title.endswith(" Serie") or raw_title.endswith(" serie"):
            is_series = True

        self.results.append(
            {
                "title": _clean_title(raw_title),
                "url": self._current_url,
                "genres": genres,
                "quality": quality,
                "badge": series_badge,
                "is_series": is_series,
            }
        )
        self._reset_card()

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()
        href = attr_dict.get("href", "") or ""

        # --- titlecontrol block ---
        if tag == "div":
            if self._in_titlecontrol:
                self._titlecontrol_depth += 1
                if "title" in classes and not self._in_title_div:
                    self._in_title_div = True
                    self._title_div_depth = 0
                elif self._in_title_div:
                    self._title_div_depth += 1
            elif "titlecontrol" in classes:
                self._in_titlecontrol = True
                self._titlecontrol_depth = 0

            # --- general_box block ---
            if self._in_general_box:
                self._general_box_depth += 1
                if "content_text" in classes:
                    self._in_content_text = True
                    self._content_text_depth = 0
                elif self._in_content_text:
                    self._content_text_depth += 1
            elif "general_box" in classes and self._current_url:
                # Only enter general_box if we have a pending title from titlecontrol
                self._in_general_box = True
                self._general_box_depth = 0

        # Title link inside titlecontrol
        if tag == "a" and self._in_title_div:
            if href and "/stream/" in href:
                self._current_url = urljoin(self._base_url, href)
                self._in_title_a = True
                self._current_title = ""

        # Category <li> inside general_box
        if tag == "li" and self._in_general_box and "category" in classes:
            self._in_category_li = True

        if tag == "a" and self._in_category_li:
            self._in_category_a = True
            self._category_text = ""

        # Bold tag for quality detection in content_text
        if tag == "b" and self._in_content_text:
            self._in_bold = True
            self._quality_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_title_a:
            self._current_title += data

        if self._in_category_a:
            self._category_text += data

        if self._in_bold and self._in_content_text:
            self._quality_text += data

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "a":
            if self._in_title_a:
                self._in_title_a = False
                self._current_title = self._current_title.strip()
            if self._in_category_a:
                self._in_category_a = False
                text = self._category_text.strip()
                if text:
                    self._genres.append(text)

        if tag == "li" and self._in_category_li:
            self._in_category_li = False

        if tag == "b" and self._in_bold:
            self._in_bold = False
            text = self._quality_text.strip()
            if text and not self._quality:
                self._quality = text

        if tag == "div":
            # Close title div (with depth tracking)
            if self._in_title_div and self._in_titlecontrol:
                if self._title_div_depth > 0:
                    self._title_div_depth -= 1
                else:
                    self._in_title_div = False

            # Close titlecontrol
            if self._in_titlecontrol:
                if self._titlecontrol_depth > 0:
                    self._titlecontrol_depth -= 1
                else:
                    self._in_titlecontrol = False

            # Close content_text
            if self._in_content_text and self._in_general_box:
                if self._content_text_depth > 0:
                    self._content_text_depth -= 1
                else:
                    self._in_content_text = False

            # Close general_box → emit card
            if self._in_general_box:
                if self._general_box_depth > 0:
                    self._general_box_depth -= 1
                else:
                    self._in_general_box = False
                    self._emit_card()


class _DetailPageParser(HTMLParser):
    """Parse kinoger.com detail page for stream tabs and metadata.

    Stream tabs have structure::

        <div class="tabs">
          <input id="tab1" type="radio" name="tab-control" checked>
          <label for="tab1" title="Stream HD+">Stream HD+</label>
          ...
          <section id="content1">
            <iframe src="https://fsst.online/embed/..."></iframe>
          </section>
          <section id="content2">
            <iframe src="https://kinoger.p2pplay.pro/..."></iframe>
          </section>
        </div>

    Metadata from the page body:
    - Year from text or meta
    - Genres from breadcrumbs
    - Description from content area
    - IMDb rating if present
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

        # Stream tabs
        self.stream_links: list[dict[str, str]] = []
        self._tab_labels: dict[str, str] = {}  # tab id → label text
        self._current_label_for = ""
        self._in_label = False
        self._label_title_attr = ""
        self._label_text = ""

        # Section/iframe tracking
        self._in_section = False
        self._section_id = ""
        self._section_iframe_src = ""

        # Script-in-section tracking (JS player init with embedded URLs)
        self._in_section_script = False
        self._section_script_data = ""

        # Metadata
        self.title = ""
        self.year = ""
        self.genres: list[str] = []
        self.description = ""
        self.quality = ""
        self.imdb_rating = ""
        self.runtime = ""
        self.is_series = False
        self.badge = ""

        # Title tracking (h1)
        self._in_h1 = False
        self._h1_text = ""

        # Breadcrumb tracking (genres)
        self._in_breadcrumbs = False
        self._in_breadcrumb_li = False
        self._breadcrumb_text = ""
        self._genres: list[str] = []

        # Badge tracking
        self._in_badge_span = False
        self._badge_text = ""

        # Description tracking
        self._in_desc = False
        self._desc_div_depth = 0
        self._desc_text = ""

        # IMDb tracking
        self._in_imdb_span = False
        self._imdb_text = ""

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # Tab labels: <label for="tab1" title="Stream HD+">
        if tag == "label":
            for_attr = attr_dict.get("for", "") or ""
            if for_attr.startswith("tab"):
                self._in_label = True
                self._current_label_for = for_attr
                self._label_title_attr = attr_dict.get("title", "") or ""
                self._label_text = ""

        # Sections with iframes: <section id="content1">
        if tag == "section":
            section_id = attr_dict.get("id", "") or ""
            if section_id.startswith("content"):
                self._in_section = True
                self._section_id = section_id
                self._section_iframe_src = ""

        # Iframe inside section
        if tag == "iframe" and self._in_section:
            src = attr_dict.get("src", "") or ""
            if src:
                self._section_iframe_src = src

        # Script inside section (JS player init with embedded URLs)
        if tag == "script" and self._in_section:
            self._in_section_script = True
            self._section_script_data = ""

        # h1
        if tag == "h1":
            self._in_h1 = True
            self._h1_text = ""

        # Breadcrumbs: <ul class="breadcrumbs">
        if tag == "ul" and "breadcrumbs" in classes:
            self._in_breadcrumbs = True

        if tag == "li" and self._in_breadcrumbs:
            self._in_breadcrumb_li = True
            self._breadcrumb_text = ""

        # Badge span
        if tag == "span" and "badge" in classes:
            self._in_badge_span = True
            self._badge_text = ""

        # Description area: <div class="full-text">
        if tag == "div":
            if self._in_desc:
                self._desc_div_depth += 1
            elif "full-text" in classes:
                self._in_desc = True
                self._desc_div_depth = 0
                self._desc_text = ""

        # IMDb rating span: <span class="imdb">
        if tag == "span" and "imdb" in classes:
            self._in_imdb_span = True
            self._imdb_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_section_script:
            self._section_script_data += data

        if self._in_label:
            self._label_text += data

        if self._in_h1:
            self._h1_text += data

        if self._in_breadcrumb_li:
            self._breadcrumb_text += data

        if self._in_badge_span:
            self._badge_text += data

        if self._in_desc:
            self._desc_text += data

        if self._in_imdb_span:
            self._imdb_text += data

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "label" and self._in_label:
            self._in_label = False
            label_text = self._label_title_attr or self._label_text.strip()
            if self._current_label_for and label_text:
                # Map tab ID → content ID: "tab1" → "content1"
                num = self._current_label_for.replace("tab", "")
                content_id = f"content{num}"
                self._tab_labels[content_id] = label_text

        # End of <script> inside section — extract URLs from JS player init
        if tag == "script" and self._in_section_script:
            self._in_section_script = False
            if not self._section_iframe_src and self._section_script_data:
                # Extract URLs from JS patterns like:
                #   fsst.show(1,[['https://fsst.online/embed/905450/']],0.2)
                #   ollhd.show(1,[['https://kinoger.p2pplay.pro/#n6lc6']],0.2)
                m = re.search(
                    r"""\.show\(\d+,\s*\[\[['"]?(https?://[^'"\]]+)""",
                    self._section_script_data,
                )
                if m:
                    self._section_iframe_src = m.group(1)

        if tag == "section" and self._in_section:
            self._in_section = False
            self._in_section_script = False
            if self._section_iframe_src:
                label = self._tab_labels.get(self._section_id, "")
                domain = _domain_from_url(self._section_iframe_src)
                self.stream_links.append(
                    {
                        "hoster": domain,
                        "link": self._section_iframe_src,
                        "label": label,
                    }
                )

        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.title = _clean_title(self._h1_text)

        if tag == "li" and self._in_breadcrumb_li:
            self._in_breadcrumb_li = False
            text = self._breadcrumb_text.strip()
            if text:
                self._genres.append(text)

        if tag == "ul" and self._in_breadcrumbs:
            self._in_breadcrumbs = False
            # Filter out "Stream" from genres
            self.genres = [g for g in self._genres if g.lower() != "stream"]

        if tag == "span" and self._in_badge_span:
            self._in_badge_span = False
            text = self._badge_text.strip()
            if text:
                self.badge = text
                if _SERIES_BADGE_RE.search(text):
                    self.is_series = True
                elif text.upper() in (
                    "WEBRIP",
                    "BDRIP",
                    "CAMRIP",
                    "TS",
                    "HD",
                    "SD",
                    "4K",
                    "HDTV",
                ):
                    self.quality = text

        if tag == "div" and self._in_desc:
            if self._desc_div_depth > 0:
                self._desc_div_depth -= 1
            else:
                self._in_desc = False
                self.description = self._desc_text.strip()

        if tag == "span" and self._in_imdb_span:
            self._in_imdb_span = False
            text = self._imdb_text.strip()
            m = re.search(r"(\d+\.?\d*)", text)
            if m:
                self.imdb_rating = m.group(1)

    def finalize(self) -> None:
        """Post-processing: detect series from genres, extract year/runtime."""
        lower_genres = [g.lower() for g in self.genres]
        if "serie" in lower_genres or "serien" in lower_genres:
            self.is_series = True

        # Try to extract year from title or description
        if not self.year:
            m = re.search(r"\b(19|20)\d{2}\b", self.description)
            if m:
                self.year = m.group(0)


class KinogerPlugin(HttpxPluginBase):
    """Python plugin for kinoger.com using httpx."""

    name = "kinoger"
    provides = "stream"
    _domains = _DOMAINS

    async def _search_page(
        self,
        query: str,
        page: int = 1,
    ) -> list[dict[str, str | list[str] | bool]]:
        """Fetch a search results page.

        DLE CMS search uses::
            GET /index.php?do=search&subaction=search&story={query}

        Pagination::
            GET /index.php?do=search&subaction=search&search_start={N}&story={query}
        """
        client = await self._ensure_client()

        params: dict[str, str] = {
            "do": "search",
            "subaction": "search",
            "story": query,
        }
        if page > 1:
            params["search_start"] = str(page)

        try:
            resp = await client.get(
                f"{self.base_url}/index.php",
                params=params,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "kinoger_search_failed",
                query=query,
                page=page,
                error=str(exc),
            )
            return []

        parser = _SearchResultParser(self.base_url)
        parser.feed(resp.text)

        self._log.info(
            "kinoger_search_page",
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

        for page_num in range(1, _MAX_PAGES + 1):
            results = await self._search_page(query, page_num)
            if not results:
                break
            all_results.extend(results)
            if len(all_results) >= self._max_results:
                break

        return all_results[: self._max_results]

    async def _scrape_detail(
        self,
        result: dict[str, str | list[str] | bool],
    ) -> SearchResult | None:
        """Scrape a detail page for stream tabs and metadata."""
        client = await self._ensure_client()
        detail_url = str(result["url"])

        try:
            resp = await client.get(detail_url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "kinoger_detail_failed",
                url=detail_url,
                error=str(exc),
            )
            return None

        parser = _DetailPageParser(self.base_url)
        parser.feed(resp.text)
        parser.finalize()

        if not parser.stream_links:
            self._log.debug("kinoger_no_streams", url=detail_url)
            return None

        title = parser.title or str(result.get("title", ""))
        genres = parser.genres or list(result.get("genres", []))
        is_series = parser.is_series or bool(result.get("is_series", False))
        quality = parser.quality or str(result.get("quality", ""))
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
            "imdb_rating": parser.imdb_rating,
            "runtime": parser.runtime,
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
        """Search kinoger.com and return results with stream links."""
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


plugin = KinogerPlugin()
