"""nima4k.org Python plugin for Scavengarr.

Scrapes nima4k.org (German 4K UHD release site) with:
- httpx for all requests (no Cloudflare protection)
- POST /search for keyword search (returns all results, no pagination)
- GET /{category}/page-{n} for category browsing with pagination
- Download link construction from release ID (ddl.to + rapidgator)
- Category mapping: Movies, TV (Serien), Docs, Sports, Music

No authentication required. Password for all releases: NIMA4K
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

_BASE_URL = "https://nima4k.org"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_RESULTS = 1000
_MAX_PAGES = 100  # 10 results/page → 100 pages for 1000

# Torznab category → site URL path segment.
_CATEGORY_PATH_MAP: dict[int, str] = {
    2000: "movies",
    5000: "serien",
    5070: "dokumentationen",
    5060: "sports",
    3000: "music",
}

# Site category name (from genre pills / URL) → Torznab category ID.
_CATEGORY_NAME_MAP: dict[str, int] = {
    "movies": 2000,
    "filme": 2000,
    "serien": 5000,
    "tv": 5000,
    "dokumentationen": 5070,
    "dokus": 5070,
    "sports": 5060,
    "sport": 5060,
    "music": 3000,
    "musik": 3000,
    "regrades": 2000,
}


class _ListingParser(HTMLParser):
    """Parse article cards from nima4k.org listing/search pages.

    Each article is a ``<div class="article">`` containing:
    - ``<h2><a class="release-details" href="/release/ID/slug">Title</a></h2>``
    - ``<span class="subtitle">Release.Name</span>``
    - ``<ul class="release-infos">`` with size info
    - ``<ul class="genre-pills">`` with category pills
    - ``<p class="meta"><span>Date</span></p>``

    Also detects pagination via ``<ul class="uk-pagination">``.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str | list[str]]] = []
        self.has_next_page: bool = False
        self._base_url = base_url

        # Article tracking
        self._in_article = False
        self._article_div_depth = 0

        # Title/URL
        self._in_h2 = False
        self._in_release_a = False
        self._current_title = ""
        self._current_url = ""

        # Subtitle (release name)
        self._in_subtitle = False
        self._current_subtitle = ""

        # Release infos (size)
        self._in_release_infos = False
        self._in_li = False
        self._li_text = ""
        self._current_size = ""
        self._li_count = 0

        # Genre pills (categories)
        self._in_genre_pills = False
        self._in_pill_a = False
        self._pill_href = ""
        self._pill_text = ""
        self._current_categories: list[str] = []

        # Meta (date)
        self._in_meta_p = False
        self._in_meta_span = False
        self._current_date = ""

        # Pagination
        self._in_pagination = False
        self._found_next = False

    def _reset_article(self) -> None:
        self._current_title = ""
        self._current_url = ""
        self._current_subtitle = ""
        self._current_size = ""
        self._current_categories = []
        self._current_date = ""

    def _emit_article(self) -> None:
        if not self._current_title or not self._current_url:
            return
        self.results.append(
            {
                "title": self._current_title,
                "url": self._current_url,
                "release_name": self._current_subtitle,
                "size": self._current_size,
                "categories": self._current_categories.copy(),
                "date": self._current_date,
            }
        )

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # Article boundary
        if tag == "div":
            if self._in_article:
                self._article_div_depth += 1
            elif "article" in classes:
                self._in_article = True
                self._article_div_depth = 0
                self._reset_article()

        if not self._in_article and tag == "ul" and "uk-pagination" in classes:
            self._in_pagination = True

        if self._in_pagination and tag == "a":
            href = attr_dict.get("href", "") or ""
            if href:
                self._found_next = True

        if not self._in_article:
            return

        # h2 → title link
        if tag == "h2":
            self._in_h2 = True

        if tag == "a" and self._in_h2 and "release-details" in classes:
            self._in_release_a = True
            href = attr_dict.get("href", "") or ""
            if href:
                self._current_url = urljoin(self._base_url, href)
            self._current_title = ""

        # Genre pill link — skip IMDb / xREL links
        if tag == "a" and self._in_genre_pills:
            href = attr_dict.get("href", "") or ""
            if href and "imdb.com" not in href and "xrel.to" not in href:
                self._in_pill_a = True
                self._pill_href = href
                self._pill_text = ""

        # Subtitle span
        if tag == "span" and "subtitle" in classes:
            self._in_subtitle = True
            self._current_subtitle = ""

        # Release infos list
        if tag == "ul" and "release-infos" in classes:
            self._in_release_infos = True
            self._li_count = 0

        if tag == "li" and self._in_release_infos:
            self._in_li = True
            self._li_text = ""
            self._li_count += 1

        # Genre pills list
        if tag == "ul" and "genre-pills" in classes:
            self._in_genre_pills = True

        # Meta paragraph
        if tag == "p" and "meta" in classes:
            self._in_meta_p = True

        if tag == "span" and self._in_meta_p:
            self._in_meta_span = True

    def handle_data(self, data: str) -> None:
        text = data.strip()

        if self._in_release_a:
            self._current_title += data

        if self._in_subtitle:
            self._current_subtitle += data

        if self._in_li and self._in_release_infos:
            self._li_text += data

        if self._in_pill_a:
            self._pill_text += data

        if self._in_meta_span and text:
            if not self._current_date:
                self._current_date = text

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "a":
            if self._in_release_a:
                self._in_release_a = False
                self._current_title = self._current_title.strip()
            if self._in_pill_a:
                self._in_pill_a = False
                pill = self._pill_text.strip()
                if pill:
                    self._current_categories.append(pill)

        if tag == "h2":
            self._in_h2 = False

        if tag == "span":
            if self._in_subtitle:
                self._in_subtitle = False
                self._current_subtitle = self._current_subtitle.strip()
            if self._in_meta_span:
                self._in_meta_span = False

        if tag == "li" and self._in_li:
            self._in_li = False
            text = self._li_text.strip()
            # First li with a size-like pattern is the size
            if text and not self._current_size and _looks_like_size(text):
                self._current_size = text

        if tag == "ul":
            if self._in_release_infos:
                self._in_release_infos = False
            if self._in_genre_pills:
                self._in_genre_pills = False
            if self._in_pagination:
                self._in_pagination = False
                if self._found_next:
                    self.has_next_page = True

        if tag == "p" and self._in_meta_p:
            self._in_meta_p = False

        if tag == "div" and self._in_article:
            if self._article_div_depth > 0:
                self._article_div_depth -= 1
            else:
                self._in_article = False
                self._emit_article()


def _looks_like_size(text: str) -> bool:
    """Check if text looks like a file size (e.g. '45.2 GB', '800 MB')."""
    return bool(re.search(r"\d+[.,]?\d*\s*(?:GB|MB|TB|KB)", text, re.IGNORECASE))


def _extract_release_id(url: str) -> str | None:
    """Extract the numeric release ID from a nima4k.org detail URL.

    Example: ``/release/4296/batman-begins-...`` → ``"4296"``
    """
    m = re.search(r"/release/(\d+)/", url)
    return m.group(1) if m else None


def _build_download_links(release_id: str, base_url: str) -> list[dict[str, str]]:
    """Construct download links from a release ID."""
    return [
        {"hoster": "ddl.to", "link": f"{base_url}/go/{release_id}/ddl.to"},
        {"hoster": "rapidgator", "link": f"{base_url}/go/{release_id}/rapidgator"},
    ]


def _category_to_torznab(categories: list[str]) -> int:
    """Map site category names to Torznab category ID."""
    for cat in categories:
        key = cat.lower().strip()
        if key in _CATEGORY_NAME_MAP:
            return _CATEGORY_NAME_MAP[key]
    return 2000  # default: Movies


class Nima4kPlugin:
    """Python plugin for nima4k.org using httpx."""

    name = "nima4k"
    version = "1.0.0"
    mode = "httpx"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self.base_url = _BASE_URL

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Create httpx client if not already running."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            )
        return self._client

    async def _search_post(self, query: str) -> list[dict[str, str | list[str]]]:
        """Execute POST search and return parsed results."""
        client = await self._ensure_client()

        try:
            resp = await client.post(
                f"{self.base_url}/search",
                data={"search": query},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("nima4k_search_failed", query=query, error=str(exc))
            return []

        parser = _ListingParser(self.base_url)
        parser.feed(resp.text)

        log.info("nima4k_search_post", query=query, count=len(parser.results))
        return parser.results

    async def _browse_category_page(
        self,
        category_path: str,
        page_num: int = 1,
    ) -> tuple[list[dict[str, str | list[str]]], bool]:
        """Fetch one category page. Returns (results, has_next_page)."""
        client = await self._ensure_client()

        if page_num > 1:
            url = f"{self.base_url}/{category_path}/page-{page_num}"
        else:
            url = f"{self.base_url}/{category_path}"

        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "nima4k_browse_failed",
                category=category_path,
                page=page_num,
                error=str(exc),
            )
            return [], False

        parser = _ListingParser(self.base_url)
        parser.feed(resp.text)

        log.info(
            "nima4k_browse_page",
            category=category_path,
            page=page_num,
            count=len(parser.results),
            has_next=parser.has_next_page,
        )
        return parser.results, parser.has_next_page

    async def _browse_category(
        self,
        category_path: str,
    ) -> list[dict[str, str | list[str]]]:
        """Browse a category with pagination up to _MAX_RESULTS items."""
        all_results: list[dict[str, str | list[str]]] = []

        for page_num in range(1, _MAX_PAGES + 1):
            results, has_next = await self._browse_category_page(
                category_path, page_num
            )
            if not results:
                break
            all_results.extend(results)
            if len(all_results) >= _MAX_RESULTS or not has_next:
                break

        return all_results[:_MAX_RESULTS]

    def _build_search_result(
        self,
        item: dict[str, str | list[str]],
        forced_category: int | None = None,
    ) -> SearchResult | None:
        """Convert a parsed listing item to a SearchResult."""
        url = str(item.get("url", ""))
        release_id = _extract_release_id(url)
        if not release_id:
            return None

        dl_links = _build_download_links(release_id, self.base_url)
        categories = item.get("categories", [])
        cat_list = categories if isinstance(categories, list) else []

        torznab_cat = (
            forced_category if forced_category else _category_to_torznab(cat_list)
        )

        title = str(item.get("title", ""))
        release_name = str(item.get("release_name", "")) or None
        size = str(item.get("size", "")) or None
        date = str(item.get("date", "")) or None

        return SearchResult(
            title=title,
            download_link=dl_links[0]["link"],
            download_links=dl_links,
            source_url=url,
            release_name=release_name,
            size=size,
            published_date=date,
            category=torznab_cat,
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
    ) -> list[SearchResult]:
        """Search nima4k.org and return results.

        If a query is provided, uses POST search.
        If only a category is provided, browses the category pages.
        """
        await self._ensure_client()

        if query:
            items = await self._search_post(query)
        elif category and category in _CATEGORY_PATH_MAP:
            category_path = _CATEGORY_PATH_MAP[category]
            items = await self._browse_category(category_path)
        else:
            return []

        results: list[SearchResult] = []
        for item in items:
            sr = self._build_search_result(item, forced_category=category)
            if sr:
                results.append(sr)

        return results

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


plugin = Nima4kPlugin()
