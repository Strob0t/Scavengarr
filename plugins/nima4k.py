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
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    filter_by_category,
    is_series_title,
    served_category,
    stream_category,
)
from scavengarr.infrastructure.plugins.dom import ancestors, classes, parse_page
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["nima4k.org"]
_MAX_PAGES = 100  # 10 results/page → 100 pages for 1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Torznab category → site section, for browsing without a query.
_CATEGORY_PATH_MAP: dict[int, str] = {
    2000: "movies",
    5000: "serien",
    5060: "sports",
    3000: "music",
}
# The labels ``_category_of()`` gives
_CATEGORIES = (2000, 3020, 5000, 5060, 5070)

_CONCERT_PILLS = frozenset({"konzert", "music", "musik"})
_SPORT_PILLS = frozenset({"sport", "sports"})
# Section pills of older listings; live listings carry genre pills only
_SERIES_PILLS = frozenset({"serien", "tv"})


class _ListingParser:
    """Parse article cards from nima4k.org listing/search pages (selectolax).

    Each article is a ``<div class="article">`` containing:
    - ``<h2><a class="release-details" href="/release/ID/slug">Title</a></h2>``
    - ``<span class="subtitle">Release.Name</span>``
    - ``<ul class="release-infos">`` with size info
    - ``<ul class="genre-pills">`` with category pills
    - ``<p class="meta"><span>Date</span></p>``

    Also detects pagination via ``<ul class="uk-pagination">``.
    """

    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str | list[str]]] = []
        self.has_next_page: bool = False
        self._base_url = base_url

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        # An article div nested in an article is part of the outer one
        for article in tree.css("div.article"):
            if not _inside_article(article):
                self._add_article(article)
        # Any link of a pagination outside the articles counts as a next page
        if any(
            link.attributes.get("href")
            for pagination in tree.css("ul.uk-pagination")
            if not _inside_article(pagination)
            for link in pagination.css("a")
        ):
            self.has_next_page = True

    def _add_article(self, article: LexborNode) -> None:
        # The last title link names the article; one without href keeps the
        # URL of an earlier one
        title = url = ""
        for link in article.css("h2 a.release-details"):
            title = link.text().strip()
            if href := link.attributes.get("href"):
                url = urljoin(self._base_url, href)
        if not title or not url:
            return
        subtitles = article.css("span.subtitle")
        # The first info that reads like a size ("Größe 36,90 GB")
        size = next(
            (
                text
                for li in article.css("ul.release-infos li")
                if (text := li.text().strip()) and _looks_like_size(text)
            ),
            "",
        )
        # Genre pills, without the IMDb / xREL links
        categories = [
            text
            for link in article.css("ul.genre-pills a")
            if (href := link.attributes.get("href"))
            and "imdb.com" not in href
            and "xrel.to" not in href
            and (text := link.text().strip())
        ]
        # The date: the first span of the meta line with text
        date = next(
            (
                text
                for span in article.css("p.meta span")
                if (text := span.text().strip())
            ),
            "",
        )
        self.results.append(
            {
                "title": title,
                "url": url,
                "release_name": subtitles[-1].text().strip() if subtitles else "",
                "size": size,
                "categories": categories,
                "date": date,
            }
        )


def _inside_article(node: LexborNode) -> bool:
    """Whether *node* lies in an article card (``<div class="article">``)."""
    return any(
        parent.tag == "div" and "article" in classes(parent)
        for parent in ancestors(node)
    )


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


def _category_of(item: dict[str, str | list[str]]) -> int:
    """Torznab category of a listing item, from its genre pills and release.

    Concerts 3020, sport 5060; otherwise films 2000 (documentaries too) and
    series (season packs such as ``Show.S03...``) 5000, animation series 5070.
    """
    raw = item.get("categories", [])
    pills = {p.strip().lower() for p in raw} if isinstance(raw, list) else set()
    if pills & _CONCERT_PILLS:
        return 3020
    if pills & _SPORT_PILLS:
        return 5060
    # Season packs "Stranger.Things.S03...", titles "... (Staffel 3)"
    is_series = (
        bool(pills & _SERIES_PILLS)
        or is_series_title(str(item.get("release_name", "")))
        or is_series_title(str(item.get("title", "")))
    )
    return stream_category(pills, is_series=is_series)


class Nima4kPlugin(HttpxPluginBase):
    """Python plugin for nima4k.org using httpx."""

    name = "nima4k"
    provides = "download"
    _domains = _DOMAINS

    async def _search_post(self, query: str) -> list[dict[str, str | list[str]]]:
        """Execute POST search and return parsed results."""
        resp = await self._safe_fetch(
            f"{self.base_url}/search",
            method="POST",
            context="search",
            data={"search": query},
        )
        if resp is None:
            return []

        parser = await parse_page(_ListingParser(self.base_url), resp.text)

        self._log.info("nima4k_search_post", query=query, count=len(parser.results))
        return parser.results

    async def _browse_category_page(
        self,
        category_path: str,
        page_num: int = 1,
    ) -> tuple[list[dict[str, str | list[str]]], bool]:
        """Fetch one category page. Returns (results, has_next_page)."""
        if page_num > 1:
            url = f"{self.base_url}/{category_path}/page-{page_num}"
        else:
            url = f"{self.base_url}/{category_path}"

        resp = await self._safe_fetch(url, context="browse")
        if resp is None:
            return [], False

        parser = await parse_page(_ListingParser(self.base_url), resp.text)

        self._log.info(
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
        """Browse a category with pagination up to max_results items."""
        all_results: list[dict[str, str | list[str]]] = []

        for page_num in range(1, _MAX_PAGES + 1):
            results, has_next = await self._browse_category_page(
                category_path, page_num
            )
            if not results:
                break
            all_results.extend(results)
            if len(all_results) >= self.effective_max_results or not has_next:
                break

        return all_results[: self.effective_max_results]

    def _build_search_result(
        self, item: dict[str, str | list[str]]
    ) -> SearchResult | None:
        """Convert a parsed listing item to a SearchResult."""
        url = str(item.get("url", ""))
        release_id = _extract_release_id(url)
        if not release_id:
            return None

        dl_links = _build_download_links(release_id, self.base_url)

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
            category=_category_of(item),
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search nima4k.org and return results.

        If a query is provided, uses POST search.
        If only a category is provided, browses the category pages.
        Results are labelled from their release and filtered by *category*.
        """
        if category is not None:
            category = served_category(category, _CATEGORIES)
            if category is None:
                return []  # no section of the site has this category
        await self._ensure_client()

        if query:
            items = await self._search_post(query)
        elif category is not None:
            path = _CATEGORY_PATH_MAP.get(category) or _CATEGORY_PATH_MAP.get(
                category - category % 1000
            )
            items = await self._browse_category(path) if path else []
        else:
            return []

        results = [sr for item in items if (sr := self._build_search_result(item))]
        if category is not None:
            results = filter_by_category(results, category)
        return results


plugin = Nima4kPlugin()
