"""scnsrc.me (SceneSource) Python plugin for Scavengarr.

Scrapes scnsrc.me (WordPress scene info blog) with:
- Playwright for Cloudflare Turnstile bypass
- WordPress search via /?s=query
- Category filtering via /category/xxx/?s=query URL prefix
- Single-stage: all data (title, release name, download links) on listing pages
- Download links point to torrent search (limetorrents) and usenet (nzbindex)
- Multi-domain support with automatic fallback (scnsrc.me, scenesource.me, scnsrc.net)

No authentication required.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = [
    "www.scnsrc.me",
    "scnsrc.me",
    "www.scenesource.me",
    "scenesource.me",
    "www.scnsrc.net",
    "scnsrc.net",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Torznab category -> URL path segment mapping.
_CATEGORY_PATH_MAP: dict[int, str] = {
    2000: "category/films",
    5000: "category/tv",
    4000: "category/games",
    5020: "category/applications",
    3000: "category/new-music",
    7000: "category/ebooks",
}

# Reverse mapping: site category name -> Torznab category ID.
_CATEGORY_NAME_MAP: dict[str, int] = {
    # Films
    "films": 2000,
    "movies": 2000,
    "hd": 2000,
    "bluray": 2000,
    "bdrip": 2000,
    "bdscr": 2000,
    "uhd": 2000,
    "dvdrip": 2000,
    "dvdscr": 2000,
    "cam": 2000,
    "r5": 2000,
    "scr": 2000,
    "telecine": 2000,
    "telesync": 2000,
    "workprint": 2000,
    "3d": 2000,
    # TV
    "tv": 5000,
    "miniseries": 5000,
    "ppv": 5000,
    "preair": 5000,
    "sports-tv": 5060,
    "uhd-tv": 5000,
    "dvd": 5000,
    # Games
    "games": 4000,
    "iso": 4000,
    "rip": 4000,
    "clone": 4000,
    "dox": 4000,
    "nds": 4000,
    "ps3": 4000,
    "ps4": 4000,
    "psp": 4000,
    "wii": 4000,
    "wiiu": 4000,
    "xbox360": 4000,
    # Applications
    "applications": 5020,
    "windows-applications": 5020,
    "macosx": 5020,
    "linux": 5020,
    "iphone": 5020,
    # Music
    "new-music": 3000,
    "music": 3000,
    "concert": 3000,
    "flac": 3040,
    "music-videos": 3000,
    # Other
    "ebooks": 7000,
    "p2p": 2000,
}


class _PostParser(HTMLParser):
    """Extract posts from scnsrc.me listing/search pages.

    Each post has:
    - ``<div class="post" id="post-NNN">``
    - ``<h2><a href="/slug/">Title</a></h2>``
    - ``<div class="cat meta">`` with category link
    - ``<div class="tvshow_info">`` with release name + download links
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str | list[dict[str, str]]]] = []
        self._base_url = base_url

        # State tracking
        self._in_post = False
        self._post_div_depth = 0
        self._in_h2 = False
        self._in_h2_a = False
        self._in_tvshow_info = False
        self._tvshow_div_depth = 0
        self._in_strong = False
        self._in_cat_meta = False
        self._in_cat_a = False

        # Current post data
        self._current_title = ""
        self._current_url = ""
        self._current_release = ""
        self._current_links: list[dict[str, str]] = []
        self._current_category = ""
        self._strong_text = ""
        self._after_download_label = False

    def _reset_post(self) -> None:
        self._current_title = ""
        self._current_url = ""
        self._current_release = ""
        self._current_links = []
        self._current_category = ""
        self._after_download_label = False

    def _emit_post(self) -> None:
        title = self._current_release or self._current_title
        if title and (self._current_links or self._current_url):
            self.results.append(
                {
                    "title": title,
                    "url": self._current_url,
                    "release_name": self._current_release,
                    "links": self._current_links.copy(),
                    "category": self._current_category,
                }
            )

    def _handle_div_start(self, attr_dict: dict[str, str | None]) -> None:
        classes = (attr_dict.get("class", "") or "").split()

        if self._in_post:
            self._post_div_depth += 1
        elif "post" in classes:
            post_id = attr_dict.get("id", "")
            if post_id and str(post_id).startswith("post-"):
                self._in_post = True
                self._post_div_depth = 0
                self._reset_post()

        if self._in_post and "tvshow_info" in (attr_dict.get("class", "") or ""):
            self._in_tvshow_info = True
            self._tvshow_div_depth = 0
        elif self._in_tvshow_info:
            self._tvshow_div_depth += 1

        if self._in_post and "cat" in classes:
            self._in_cat_meta = True

    def _handle_a_start(self, attr_dict: dict[str, str | None]) -> None:
        href = str(attr_dict.get("href", "") or "")

        if self._in_h2 and href:
            self._in_h2_a = True
            self._current_url = _clean_wayback_url(urljoin(self._base_url, href))
            self._current_title = ""

        if self._in_cat_meta and "category" in (attr_dict.get("rel", "") or ""):
            self._in_cat_a = True

        if self._in_tvshow_info and href and self._after_download_label:
            self._current_links.append(
                {
                    "hoster": "",
                    "link": _clean_wayback_url(href),
                }
            )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "div":
            self._handle_div_start(attr_dict)
        elif tag == "h2" and self._in_post:
            self._in_h2 = True
        elif tag == "a":
            self._handle_a_start(attr_dict)
        elif tag == "strong" and self._in_tvshow_info:
            self._in_strong = True
            self._strong_text = ""

    def handle_data(self, data: str) -> None:
        text = data.strip()

        if self._in_h2_a:
            self._current_title += data

        if self._in_strong:
            self._strong_text += data

        if self._in_cat_a and text:
            self._current_category = text

        # Set hoster name on last link from anchor text
        if (
            self._in_tvshow_info
            and self._current_links
            and not self._in_strong
            and text
        ):
            last = self._current_links[-1]
            if not last["hoster"] and text.lower() in {
                "torrent",
                "usenet",
                "nzb",
                "ddl",
            }:
                last["hoster"] = text.lower()

    def _handle_strong_end(self) -> None:
        self._in_strong = False
        text = self._strong_text.strip()
        lower = text.lower()
        if lower.startswith("download"):
            self._after_download_label = True
        elif lower.startswith("info"):
            # Stop collecting links after "Info:" label
            self._after_download_label = False
        elif text and not self._current_release:
            if "." in text and len(text) > 10:
                self._current_release = text

    def _handle_div_end(self) -> None:
        if self._in_tvshow_info:
            if self._tvshow_div_depth > 0:
                self._tvshow_div_depth -= 1
            else:
                self._in_tvshow_info = False
                self._after_download_label = False

        if self._in_cat_meta and not self._in_tvshow_info:
            self._in_cat_meta = False

        if self._in_post and not self._in_tvshow_info:
            if self._post_div_depth > 0:
                self._post_div_depth -= 1
            else:
                self._in_post = False
                self._emit_post()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            if self._in_h2_a:
                self._in_h2_a = False
                self._current_title = self._current_title.strip()
            if self._in_cat_a:
                self._in_cat_a = False
        elif tag == "h2":
            self._in_h2 = False
        elif tag == "strong" and self._in_strong:
            self._handle_strong_end()
        elif tag == "div":
            self._handle_div_end()


def _clean_wayback_url(url: str) -> str:
    """Strip Wayback Machine URL prefix if present."""
    m = re.match(r"https?://web\.archive\.org/web/\d+/(https?://.+)", url)
    return m.group(1) if m else url


def _category_to_torznab(category_name: str) -> int:
    """Map site category name to Torznab category ID."""
    key = category_name.lower().strip()
    return _CATEGORY_NAME_MAP.get(key, 2000)


class ScnSrcPlugin(PlaywrightPluginBase):
    """Python plugin for scnsrc.me using Playwright.

    Supports multiple domains with automatic fallback:
    scnsrc.me, scenesource.me, scnsrc.net.
    """

    name = "scnsrc"
    version = "1.1.0"
    mode = "playwright"
    provides = "download"
    default_language = "en"
    _stealth = True

    _domains = _DOMAINS

    async def _fetch_page(self, url: str) -> str:
        """Navigate to a URL and return page content."""
        ctx = await self._ensure_context()
        page = await ctx.new_page()
        try:
            await self._navigate_and_wait(page, url)
            return await page.content()
        finally:
            if not page.is_closed():
                await page.close()

    async def _search_page(
        self,
        query: str,
        category_path: str = "",
        page_num: int = 1,
    ) -> list[dict[str, str | list[dict[str, str]]]]:
        """Fetch one search page and return parsed posts.

        WordPress pagination: ``/page/N/?s=query`` for page >= 2.
        """
        if category_path:
            base = f"{self.base_url}/{category_path}"
        else:
            base = self.base_url

        if page_num > 1:
            url = f"{base}/page/{page_num}/?s={query}"
        else:
            url = f"{base}/?s={query}"

        html = await self._fetch_page(url)

        parser = _PostParser(self.base_url)
        parser.feed(html)

        self._log.info(
            "scnsrc_search_page",
            query=query,
            category_path=category_path,
            page=page_num,
            count=len(parser.results),
        )
        return parser.results

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search scnsrc.me (or fallback domain) and return results.

        Paginates through WordPress search pages to collect up to
        1000 results.
        """
        await self._ensure_browser()
        await self._verify_domain()

        category_path = _CATEGORY_PATH_MAP.get(category, "") if category else ""

        # Paginate search results (WordPress: ~10 posts/page)
        all_posts: list[dict[str, str | list[dict[str, str]]]] = []
        page_num = 1
        while len(all_posts) < self.effective_max_results:
            posts = await self._search_page(query, category_path, page_num)
            if not posts:
                break
            all_posts.extend(posts)
            page_num += 1

        all_posts = all_posts[: self.effective_max_results]

        results: list[SearchResult] = []
        for post in all_posts:
            links = post.get("links", [])
            if not links:
                continue

            primary_link = links[0]["link"]
            cat_name = post.get("category", "")
            torznab_cat = category if category else _category_to_torznab(str(cat_name))

            results.append(
                SearchResult(
                    title=str(post["title"]),
                    download_link=primary_link,
                    download_links=links,
                    source_url=str(post.get("url", "")),
                    category=torznab_cat,
                )
            )

        return results


plugin = ScnSrcPlugin()
