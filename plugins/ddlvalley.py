"""ddlvalley.me Python plugin for Scavengarr.

Scrapes ddlvalley.me (WordPress DDL blog) with:
- Playwright for Cloudflare Turnstile bypass
- WordPress search via /?s=query
- Category filtering via /category/xxx/?s=query URL prefix
- Download link extraction from detail pages (direct hoster links)
- Bounded concurrency for detail page scraping

No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus, urljoin, urlparse

from patchright.async_api import Error as PlaywrightError
from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    is_series_title,
    served_category,
)
from scavengarr.infrastructure.plugins.dom import ancestors, classes
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["www.ddlvalley.me"]
_MAX_PAGES = 100  # ~10 posts/page → 100 pages for 1000
_RETRY_BACKOFF_S: tuple[float, ...] = (2.0, 4.0)  # rate-limited post pages

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# WordPress category (URL prefix) -> Torznab category of its posts
_SECTION_CATEGORIES: dict[str, int] = {
    "category/movies": 2000,
    "category/tv-shows": 5000,
    "category/games": 4050,
    "category/apps": 4000,
    "category/music": 3000,
    "category/reading": 7000,
}
# The categories to search for a Torznab category (PC: apps and games)
_CATEGORY_SECTIONS: dict[int, tuple[str, ...]] = {
    2000: ("category/movies",),
    5000: ("category/tv-shows",),
    4050: ("category/games",),
    4000: ("category/apps", "category/games"),
    3000: ("category/music",),
    7000: ("category/reading",),
}

# Known file hoster domains for download link detection.
_HOSTER_DOMAINS: set[str] = {
    "rapidgator.net",
    "rg.to",
    "uploaded.net",
    "uploaded.to",
    "ul.to",
    "go4up.com",
    "nitroflare.com",
    "nitro.download",
    "ddownload.com",
    "1fichier.com",
    "katfile.com",
    "turbobit.net",
    "filefactory.com",
    "hexupload.net",
    "filestore.me",
    "uptobox.com",
    "clicknupload.click",
    "clicknupload.co",
}


class _SearchResultParser:
    """Extract post links from WordPress search/listing pages (selectolax).

    Finds <h2><a href="/slug/">Title</a></h2> patterns that link
    to detail pages on the same domain.
    """

    def __init__(self, base_url: str) -> None:
        self.posts: list[dict[str, str]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        seen = {p["url"] for p in self.posts}
        for link in LexborHTMLParser(html).css("h2 a"):
            href = link.attributes.get("href") or ""
            title = link.text().strip()
            if not href or not title:
                continue
            url = urljoin(self._base_url, href)
            # Only accept links to our own domain
            if url.startswith(self._base_url) and url not in seen:
                seen.add(url)
                self.posts.append({"title": title, "url": url})


class _DetailPageParser:
    """Extract download links from DDLValley detail page (selectolax).

    Inside ``<div class="cont ...">`` looks for ``<a href>`` links
    pointing to known file hoster domains.  Tracks the current hoster
    group from preceding ``<strong>`` tags::

        <div class="cont cl">
          <strong>Rapidgator</strong><br>
          <a href="https://rapidgator.net/file/abc">link1</a><br>
          <strong>Uploaded</strong><br>
          <a href="https://ul.to/xyz">link2</a><br>
        </div>

    Any bold text names the group from its end on, across ``cont`` divs;
    of nested ``<strong>`` tags the innermost ones count.
    """

    def __init__(self) -> None:
        self.links: list[dict[str, str]] = []
        self._current_hoster = ""
        self._seen_urls: set[str] = set()

    def feed(self, html: str) -> None:
        for cont in LexborHTMLParser(html).css("div.cont"):
            # A cont div inside another one is read with the outer one
            if not any(_is_cont(parent) for parent in ancestors(cont)):
                self._read_cont(cont)

    def _read_cont(self, cont: LexborNode) -> None:
        # A <strong> names the group at its end: the links inside it still
        # belong to the group before it
        strong: LexborNode | None = None
        for node in cont.css("strong, a"):
            if strong is not None and not _inside(node, strong):
                self._set_hoster(strong)
                strong = None
            if node.tag == "strong":
                strong = node  # a nested one replaces the outer one
            else:
                self._add_link(node)
        if strong is not None:
            self._set_hoster(strong)

    def _set_hoster(self, strong: LexborNode) -> None:
        text = strong.text().strip().lower()
        if text:
            self._current_hoster = text

    def _add_link(self, link: LexborNode) -> None:
        href = link.attributes.get("href") or ""
        if not href.startswith("http"):
            return
        host = (urlparse(href).hostname or "").replace("www.", "")
        if _is_hoster_domain(host) and href not in self._seen_urls:
            self._seen_urls.add(href)
            hoster = self._current_hoster or _hoster_from_domain(host)
            self.links.append({"hoster": hoster, "link": href})


def _is_cont(node: LexborNode) -> bool:
    """Whether *node* is a post body (``<div class="cont ...">``)."""
    return node.tag == "div" and "cont" in classes(node)


def _inside(node: LexborNode, container: LexborNode) -> bool:
    """Whether *node* lies inside *container*."""
    return any(parent.mem_id == container.mem_id for parent in ancestors(node))


class _TitleParser:
    """Extract page title from ``<title>`` tag, stripping site suffix (selectolax)."""

    def __init__(self) -> None:
        self.title: str | None = None

    def feed(self, html: str) -> None:
        if self.title is not None:
            return
        # The first title with more than the site suffix counts
        for node in LexborHTMLParser(html).css("title"):
            text = re.sub(r"\s*\|\s*DDLValley.*$", "", node.text().strip())
            if text:
                self.title = text
                return


class DDLValleyPlugin(PlaywrightPluginBase):
    """Python plugin for ddlvalley.me using Playwright."""

    name = "ddlvalley"
    version = "1.0.0"
    mode = "playwright"
    provides = "download"
    languages = ["en"]

    _domains = _DOMAINS
    # nginx rate-limits post pages (503 "Service Temporarily Unavailable"):
    # 5 parallel fetches lost ~75% of the posts in a live run.
    _max_concurrent = 2

    async def _search_posts(
        self,
        query: str,
        category_path: str = "",
        page_num: int = 1,
    ) -> list[dict[str, str]]:
        """Search DDLValley and return post URLs with titles.

        WordPress pagination: ``/page/N/?s=query`` for page >= 2.
        """
        if category_path:
            base = f"{self.base_url}/{category_path}"
        else:
            base = self.base_url

        q = quote_plus(query)
        if page_num > 1:
            url = f"{base}/page/{page_num}/?s={q}"
        else:
            url = f"{base}/?s={q}"

        ctx = await self._ensure_context()
        page = await ctx.new_page()
        try:
            # Server-rendered WordPress: no need to wait for networkidle
            await self._navigate_and_wait(page, url, wait_for_idle=False)

            html = await page.content()
            parser = _SearchResultParser(self.base_url)
            parser.feed(html)

            self._log.info(
                "ddlvalley_search_results",
                query=query,
                category_path=category_path,
                page=page_num,
                count=len(parser.posts),
            )
            return parser.posts
        finally:
            if not page.is_closed():
                await page.close()

    async def _scrape_detail(self, post: dict[str, str]) -> SearchResult | None:
        """Scrape a detail page for download links."""
        html = await self._fetch_page_html(
            post["url"], wait_for_idle=False, retry_backoff_s=_RETRY_BACKOFF_S
        )
        if not html:
            return None

        # Extract title from <title> tag (more reliable than search page)
        title_parser = _TitleParser()
        title_parser.feed(html)
        title = title_parser.title or post.get("title", "Unknown")

        # Extract download links
        link_parser = _DetailPageParser()
        link_parser.feed(html)

        if not link_parser.links:
            self._log.debug("ddlvalley_no_links", url=post["url"], title=title)
            return None

        primary_link = link_parser.links[0]["link"]

        return SearchResult(
            title=title,
            download_link=primary_link,
            download_links=link_parser.links,
            source_url=post["url"],
            # Posts of a category search carry its category; site-wide rows
            # only their title
            category=_SECTION_CATEGORIES.get(post.get("section", ""))
            or (5000 if is_series_title(title) else 8000),
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search ddlvalley.me and return results with download links.

        Paginates through WordPress search pages to collect up to
        1000 results before scraping detail pages.
        """
        sections: tuple[str, ...] = ("",)
        if category is not None:
            served = served_category(category, _SECTION_CATEGORIES.values())
            if served is None:
                return []  # no category of the site holds it
            sections = _CATEGORY_SECTIONS[served]
        await self._ensure_browser()

        # Paginate search results (WordPress: ~10 posts/page)
        all_posts: list[dict[str, str]] = []
        for section in sections:
            page_num = 1
            while (
                len(all_posts) < self.effective_max_results and page_num <= _MAX_PAGES
            ):
                try:
                    posts = await self._search_posts(query, section, page_num)
                except PlaywrightError as exc:
                    # Keep the pages already collected
                    self._log.warning(
                        "ddlvalley_search_page_failed", page=page_num, error=str(exc)
                    )
                    break
                if not posts:
                    break
                all_posts.extend({**post, "section": section} for post in posts)
                page_num += 1

        if not all_posts:
            return []

        all_posts = all_posts[: self.effective_max_results]

        sem = self._new_semaphore()

        async def _bounded_scrape(
            post: dict[str, str],
        ) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(post)

        results = await asyncio.gather(
            *[_bounded_scrape(p) for p in all_posts],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, SearchResult)]


def _is_hoster_domain(host: str) -> bool:
    """Check if a hostname belongs to a known file hoster."""
    return any(host.endswith(h) for h in _HOSTER_DOMAINS)


def _hoster_from_domain(host: str) -> str:
    """Extract hoster name from domain."""
    parts = host.replace("www.", "").split(".")
    return parts[0] if parts else "unknown"


plugin = DDLValleyPlugin()
