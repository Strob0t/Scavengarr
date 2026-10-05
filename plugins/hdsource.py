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
from urllib.parse import quote_plus

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    category_matches,
    served_category,
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
    "spiele": 4050,
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

# Page numbers of the pagination (the "next" link and the "dots" gap are none).
_PAGE_NUMBERS = (
    "div[class*='nav-links'] :is(a, span)[class*='page-numbers']"
    ":not([class*='next']):not([class*='dots'])"
)


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
        return 4050
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


class _SearchPageParser:
    """Parse hd-source.to WordPress search result pages (selectolax).

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

    The last link of the ``entry-title`` heading names the result, the last
    ``blog-post-meta`` span dates it and the last ``search-content`` div
    holds its size; an article without download links is skipped.
    """

    def __init__(self) -> None:
        self.results: list[dict] = []

    def feed(self, html: str) -> None:
        for article in LexborHTMLParser(html).css("article"):
            self._add_article(article)

    def _add_article(self, article: LexborNode) -> None:
        title = url = ""
        for link in article.css("h2[class*='entry-title'] a"):
            href = link.attributes.get("href") or ""
            if href and not href.startswith("javascript"):
                title, url = link.text(), href
        download_links = _download_links(article)
        if not title or not download_links:
            return

        imdb_rating = imdb_id = ""
        for link in article.css("div[class*='search-content'] a[href*='imdb.com']"):
            imdb = _IMDB_ID_RE.search(link.attributes.get("href") or "")
            if imdb:
                imdb_id = imdb.group(1)
            rating = _IMDB_RE.search(link.text())
            if rating:
                imdb_rating = rating.group(1)
        metas = article.css("span[class*='blog-post-meta']")
        contents = article.css("div[class*='search-content']")
        size = _SIZE_RE.search(contents[-1].text()) if contents else None

        self.results.append(
            {
                "title": title.strip(),
                "url": url,
                "category": _detect_category(article.attributes.get("class") or ""),
                "published_date": _parse_date(metas[-1].text()) if metas else "",
                "size": size.group(1).strip() if size else "",
                "imdb_rating": imdb_rating,
                "imdb_id": imdb_id,
                "download_links": download_links,
            }
        )


def _download_links(article: LexborNode) -> list[dict[str, str]]:
    """The ``hosterlnk`` links of an article's ``search-content`` div.

    Each is labelled by the hoster icon link before it (``af.php?v=<hoster>``),
    else "filecrypt".
    """
    links: list[dict[str, str]] = []
    hoster = ""
    for link in article.css("div[class*='search-content'] a"):
        href = link.attributes.get("href") or ""
        param = _HOSTER_PARAM_RE.search(href)
        if param:
            hoster = _HOSTER_LABEL_MAP.get(param.group(1), param.group(1))
        if "hosterlnk" in (link.attributes.get("class") or ""):
            links.append({"hoster": hoster or "filecrypt", "link": href})
            hoster = ""
    return links


class _PaginationParser:
    """Extract the last page number from WordPress pagination (selectolax).

    Pagination structure::

        <div class="nav-links">
            <span class="page-numbers current">1</span>
            <a class="page-numbers" href=".../page/2/?s=...">2</a>
            ...
            <a class="page-numbers" href=".../page/7/?s=...">7</a>
        </div>
    """

    def __init__(self) -> None:
        self.last_page = 1

    def feed(self, html: str) -> None:
        for number in LexborHTMLParser(html).css(_PAGE_NUMBERS):
            text = number.text().strip()
            if text.isdigit():
                self.last_page = max(self.last_page, int(text))


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
        if category is None and season is not None:
            category = 5000  # a season request is a series request
        if category is not None:
            category = served_category(category, _CATEGORY_MAP.values())
            if category is None:
                return []  # the site has films, series and games only
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        all_items = await self._search_all_pages(query)
        if not all_items:
            return []

        results: list[SearchResult] = []
        for item in all_items:
            if not category_matches(category, int(item.get("category", 2000))):
                continue
            sr = self._item_to_result(item)
            if sr is not None:
                results.append(sr)
        return results


plugin = HdSourcePlugin()
