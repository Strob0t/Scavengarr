"""hd-source.to Python plugin for Scavengarr.

Scrapes hd-source.to (German DDL scene blog, WordPress-based) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- WordPress search via /?s={query}
- Pagination via /page/{N}/?s={query} (50 results/page, up to 20 pages)
- Single-stage: all data (title, download links, size, IMDb) on search listing pages
- Download links point to filecrypt.cc containers
- Category detection from article CSS classes (category-filme, category-serien, etc.)

No authentication required.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import quote_plus

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.constants import (
    is_movie_category,
    is_tv_category,
)
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["hd-source.to"]
_MAX_PAGES = 20  # 50 results/page → 20 pages for ~1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# WordPress category slug → Torznab ID.
_CATEGORY_MAP: dict[str, int] = {
    "filme": 2000,
    "scene": 2000,
    "p2p": 2000,
    "imdbtop250": 2000,
    "top-releases": 2000,
    "neuerscheinung": 2000,
    "serien": 5000,
    "complete": 5000,
    "laufend": 5000,
    "spiele": 4000,
}

# Hoster affiliate param → human-readable label.
_HOSTER_LABEL_MAP: dict[str, str] = {
    "rapidgator": "rapidgator",
    "ddlto": "ddl.to",
    "katfile": "katfile",
    "ddownload": "ddownload",
    "nitroflare": "nitroflare",
    "turbobit": "turbobit",
    "filestore": "filestore",
    "hexupload": "hexupload",
}

# Regex to extract the affiliate "v" parameter from hoster icon links.
_HOSTER_PARAM_RE = re.compile(r"af\.php\?v=(\w+)")

# Regex to extract size like "6072 MB" or "1.2 GB".
_SIZE_RE = re.compile(r"Größe:\s*([\d.,]+\s*(?:[KMGT]i?)?B)", re.IGNORECASE)

# Regex to extract IMDb rating like "IMDb: 7.3".
_IMDB_RE = re.compile(r"IMDb:\s*([\d.]+)")

# Regex to extract IMDb title ID.
_IMDB_ID_RE = re.compile(r"imdb\.com/title/(tt\d+)")

# Date format on site: DD.MM.YY, HH:MM
_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{2}),?\s*(\d{1,2}:\d{2})")


def _detect_category(css_classes: str) -> int:
    """Determine Torznab category from article CSS classes.

    Series takes priority when an article has both category-filme
    and category-serien.
    """
    lower = css_classes.lower()
    _series_markers = ("category-serien", "category-complete", "category-laufend")
    if any(m in lower for m in _series_markers):
        return 5000
    if "category-spiele" in lower:
        return 4000
    return 2000


def _parse_date(date_str: str) -> str:
    """Convert DD.MM.YY, HH:MM → ISO-ish YYYY-MM-DD HH:MM."""
    m = _DATE_RE.search(date_str)
    if not m:
        return ""
    day_part, time_part = m.group(1), m.group(2)
    parts = day_part.split(".")
    if len(parts) != 3:
        return ""
    day, month, year_short = parts
    year = f"20{year_short}" if int(year_short) < 80 else f"19{year_short}"
    return f"{year}-{month}-{day} {time_part}"


class _SearchPageParser(HTMLParser):
    """Parse hd-source.to WordPress search result pages.

    Each result is an ``<article>`` with CSS classes encoding categories::

        <article id="post-NNN" class="... category-filme formate-1080p ...">
          <header class="search-header">
            <h2 class="entry-title">
              <span class="blog-post-meta">15.01.26, 13:41 · </span>
              <a href="...">Title.Release.Name</a>
            </h2>
          </header>
          <div class="wrap-collapsible">
            <div class="collapsible-content">
              <div class="search-content">
                <p>Description</p>
                <p>
                  <strong>Größe:</strong> 6072 MB |
                  <a href="https://www.imdb.com/title/tt123/">IMDb: 7.3</a>
                  <a href="...af.php?v=rapidgator"><img/></a>
                  <a class="hosterlnk" href="https://filecrypt.cc/..."></a>
                  ...
                </p>
              </div>
            </div>
          </div>
        </article>
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []

        # Article tracking
        self._in_article = False
        self._article_classes = ""

        # Title tracking
        self._in_entry_title = False
        self._entry_title_depth = 0
        self._in_title_a = False
        self._title_text = ""
        self._title_url = ""

        # Date tracking
        self._in_blog_post_meta = False
        self._meta_text = ""

        # Collapsible content tracking
        self._in_search_content = False
        self._search_content_depth = 0

        # Link tracking within collapsible
        self._current_a_href = ""
        self._current_a_classes = ""
        self._in_a = False
        self._a_text = ""

        # Metadata extracted from collapsible
        self._in_strong = False
        self._strong_text = ""
        self._content_text = ""

        # Per-article accumulators
        self._date_str = ""
        self._size = ""
        self._imdb_rating = ""
        self._imdb_id = ""
        self._download_links: list[dict[str, str]] = []
        self._last_hoster = ""

    def _reset_article(self) -> None:
        self._article_classes = ""
        self._title_text = ""
        self._title_url = ""
        self._date_str = ""
        self._size = ""
        self._imdb_rating = ""
        self._imdb_id = ""
        self._download_links = []
        self._last_hoster = ""
        self._content_text = ""

    def _emit_article(self) -> None:
        if not self._title_text or not self._download_links:
            return

        category = _detect_category(self._article_classes)
        published = _parse_date(self._date_str)

        # Extract size from accumulated content text
        size = self._size
        if not size:
            m = _SIZE_RE.search(self._content_text)
            if m:
                size = m.group(1)

        self.results.append(
            {
                "title": self._title_text.strip(),
                "url": self._title_url,
                "category": category,
                "published_date": published,
                "size": size.strip() if size else "",
                "imdb_rating": self._imdb_rating,
                "imdb_id": self._imdb_id,
                "download_links": self._download_links.copy(),
            }
        )

    def handle_starttag(  # noqa: C901
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "") or ""

        # --- Article start ---
        if tag == "article":
            self._in_article = True
            self._reset_article()
            self._article_classes = classes

        if not self._in_article:
            return

        # --- H2 entry-title ---
        if tag == "h2" and "entry-title" in classes:
            self._in_entry_title = True
            self._entry_title_depth = 0
        elif tag == "h2" and self._in_entry_title:
            self._entry_title_depth += 1

        # --- Blog post meta (date) ---
        if tag == "span" and "blog-post-meta" in classes:
            self._in_blog_post_meta = True
            self._meta_text = ""

        # --- Title link inside h2 ---
        if tag == "a" and self._in_entry_title:
            href = attr_dict.get("href", "") or ""
            if href and not href.startswith("javascript"):
                self._in_title_a = True
                self._title_text = ""
                self._title_url = href

        # --- Search content div ---
        if tag == "div":
            if self._in_search_content:
                self._search_content_depth += 1
            elif "search-content" in classes:
                self._in_search_content = True
                self._search_content_depth = 0
                self._content_text = ""

        # --- Links in search-content ---
        if tag == "a" and self._in_search_content:
            href = attr_dict.get("href", "") or ""
            link_classes = classes
            self._in_a = True
            self._current_a_href = href
            self._current_a_classes = link_classes
            self._a_text = ""

            # Hoster icon link (affiliate redirect before filecrypt link)
            hoster_m = _HOSTER_PARAM_RE.search(href)
            if hoster_m:
                param = hoster_m.group(1)
                self._last_hoster = _HOSTER_LABEL_MAP.get(param, param)

            # Download link (filecrypt)
            if "hosterlnk" in link_classes:
                hoster = self._last_hoster or "filecrypt"
                self._download_links.append({"hoster": hoster, "link": href})
                self._last_hoster = ""

            # IMDb link
            if "imdb.com" in href:
                imdb_id_m = _IMDB_ID_RE.search(href)
                if imdb_id_m:
                    self._imdb_id = imdb_id_m.group(1)

        # --- Strong tag for labels ---
        if tag == "strong" and self._in_search_content:
            self._in_strong = True
            self._strong_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_title_a:
            self._title_text += data

        if self._in_blog_post_meta:
            self._meta_text += data

        if self._in_a and self._in_search_content:
            self._a_text += data

        if self._in_strong:
            self._strong_text += data

        if self._in_search_content:
            self._content_text += data

    def _handle_end_a(self) -> None:
        if self._in_title_a:
            self._in_title_a = False
        if self._in_a:
            self._in_a = False
            if "imdb.com" in self._current_a_href:
                m = _IMDB_RE.search(self._a_text)
                if m:
                    self._imdb_rating = m.group(1)

    def _handle_end_div(self) -> None:
        if self._in_search_content:
            if self._search_content_depth > 0:
                self._search_content_depth -= 1
            else:
                self._in_search_content = False

    def handle_endtag(self, tag: str) -> None:
        if tag == "article" and self._in_article:
            self._in_article = False
            self._emit_article()
            return
        if not self._in_article:
            return

        if tag == "h2" and self._in_entry_title:
            if self._entry_title_depth > 0:
                self._entry_title_depth -= 1
            else:
                self._in_entry_title = False
        elif tag == "span" and self._in_blog_post_meta:
            self._in_blog_post_meta = False
            self._date_str = self._meta_text.strip()
        elif tag == "a":
            self._handle_end_a()
        elif tag == "strong" and self._in_strong:
            self._in_strong = False
        elif tag == "div":
            self._handle_end_div()


class _PaginationParser(HTMLParser):
    """Extract the last page number from WordPress pagination.

    Pagination structure::

        <div class="nav-links">
            <span class="page-numbers current">1</span>
            <a class="page-numbers" href=".../page/2/?s=...">2</a>
            ...
            <a class="page-numbers" href=".../page/7/?s=...">7</a>
        </div>
    """

    def __init__(self) -> None:
        super().__init__()
        self.last_page = 1
        self._in_nav_links = False
        self._nav_depth = 0
        self._in_page_number = False
        self._page_text = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "") or ""

        if tag == "div":
            if self._in_nav_links:
                self._nav_depth += 1
            elif "nav-links" in classes:
                self._in_nav_links = True
                self._nav_depth = 0

        if not self._in_nav_links:
            return

        is_page_num = (
            tag in ("a", "span")
            and "page-numbers" in classes
            and "next" not in classes
            and "dots" not in classes
        )
        if is_page_num:
            self._in_page_number = True
            self._page_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_page_number:
            self._page_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag in ("a", "span") and self._in_page_number:
            self._in_page_number = False
            text = self._page_text.strip()
            if text.isdigit():
                page_num = int(text)
                if page_num > self.last_page:
                    self.last_page = page_num

        if tag == "div" and self._in_nav_links:
            if self._nav_depth > 0:
                self._nav_depth -= 1
            else:
                self._in_nav_links = False


class HdSourcePlugin(HttpxPluginBase):
    """Python plugin for hd-source.to using httpx."""

    name = "hdsource"
    provides = "download"
    _domains = _DOMAINS
    _max_concurrent = 3

    async def _search_page(self, query: str, page: int = 1) -> tuple[list[dict], int]:
        """Fetch one search results page and return (results, last_page)."""
        encoded = quote_plus(query)
        if page > 1:
            url = f"{self.base_url}/page/{page}/?s={encoded}"
        else:
            url = f"{self.base_url}/?s={encoded}"

        resp = await self._safe_fetch(url, context=f"search_page_{page}")
        if resp is None:
            return [], 1

        html = resp.text

        parser = _SearchPageParser()
        parser.feed(html)

        pag_parser = _PaginationParser()
        pag_parser.feed(html)

        self._log.info(
            "hdsource_search_page",
            query=query,
            page=page,
            results=len(parser.results),
            last_page=pag_parser.last_page,
        )
        return parser.results, pag_parser.last_page

    async def _search_all_pages(self, query: str) -> list[dict]:
        """Paginate through search results up to _max_results."""
        all_results: list[dict] = []

        first_page, last_page = await self._search_page(query, 1)
        if not first_page:
            return []
        all_results.extend(first_page)

        max_page = min(last_page, _MAX_PAGES)

        for page_num in range(2, max_page + 1):
            if len(all_results) >= self.effective_max_results:
                break
            results, _ = await self._search_page(query, page_num)
            if not results:
                break
            all_results.extend(results)

        return all_results[: self.effective_max_results]

    @staticmethod
    def _item_to_result(item: dict) -> SearchResult | None:
        """Convert a parsed article dict to a SearchResult."""
        links = item.get("download_links", [])
        if not links:
            return None

        metadata: dict[str, str] = {}
        if item.get("imdb_rating"):
            metadata["imdb_rating"] = item["imdb_rating"]
        if item.get("imdb_id"):
            metadata["imdb_id"] = item["imdb_id"]

        return SearchResult(
            title=item["title"],
            download_link=links[0]["link"],
            download_links=links,
            source_url=item.get("url", ""),
            category=item.get("category", 2000),
            size=item.get("size") or None,
            published_date=item.get("published_date") or None,
            metadata=metadata,
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search hd-source.to and return results with download links."""
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        all_items = await self._search_all_pages(query)
        if not all_items:
            return []

        results: list[SearchResult] = []
        for item in all_items:
            if category is not None:
                cat = item.get("category", 2000)
                if is_tv_category(category) and cat < 5000:
                    continue
                if is_movie_category(category) and cat >= 5000:
                    continue

            sr = self._item_to_result(item)
            if sr is not None:
                results.append(sr)

        # When season is requested but no category, restrict to TV.
        if season is not None and category is None:
            results = [r for r in results if r.category >= 5000]

        return results


plugin = HdSourcePlugin()
