"""scnsrc.me (SceneSource) Python plugin for Scavengarr.

Scrapes scnsrc.me (WordPress scene info blog) with:
- Playwright for Cloudflare Turnstile bypass
- WordPress search via /?s=query
- Category filtering via /category/xxx/?s=query URL prefix
- Two-stage: search pages list posts (title, category); release name and
  download links come from each post page (bounded parallel fetches)
- Download links point to torrent search (limetorrents) and usenet (nzbindex)
- Multi-domain support with automatic fallback (scnsrc.me, scenesource.me, scnsrc.net)

No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus, urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    category_matches,
    served_category,
)
from scavengarr.infrastructure.plugins.dom import ancestors
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

_RETRY_BACKOFF_S: tuple[float, ...] = (2.0, 4.0)  # rate-limited pages
_MAX_PAGES = 100  # ~10 posts/page → 1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Torznab category -> the site sections (URL prefixes) holding it
_SEARCH_PATHS: dict[int, tuple[str, ...]] = {
    2000: ("category/films",),
    5000: ("category/tv",),
    1000: ("category/games",),
    4050: ("category/games",),
    4000: ("category/applications", "category/games"),
    3000: ("category/new-music",),
    7000: ("category/ebooks",),
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
    # Games: PC
    "games": 4050,
    "iso": 4050,
    "rip": 4050,
    "clone": 4050,
    "dox": 4050,
    # Games: consoles
    "nds": 1010,
    "psp": 1020,
    "wii": 1030,
    "xbox360": 1050,
    "ps3": 1080,
    "wiiu": 1130,
    "ps4": 1180,
    # Applications
    "applications": 4000,
    "windows-applications": 4000,
    "linux": 4000,
    "macosx": 4030,
    "iphone": 4060,
    # Music
    "new-music": 3000,
    "music": 3000,
    "concert": 3020,
    "flac": 3040,
    "music-videos": 3020,
    # Other
    "ebooks": 7000,
    "p2p": 2000,
}


def _outermost(nodes: list[LexborNode]) -> list[LexborNode]:
    """*nodes* without the ones nested in another of them (part of it)."""
    found = {node.mem_id for node in nodes}
    return [
        node
        for node in nodes
        if not any(parent.mem_id in found for parent in ancestors(node))
    ]


class _PostParser:
    """Extract posts from scnsrc.me listing/search pages (selectolax).

    Each post has:
    - ``<div class="post" id="post-NNN">``
    - ``<h2><a href="/slug/">Title</a></h2>``
    - ``<div class="cat meta">`` with category link
    - ``<div class="tvshow_info">`` with release name + download links

    A post nested in another one is part of it. The last title link and the
    last category link win.
    """

    _HOSTER_LABELS = frozenset({"torrent", "usenet", "nzb", "ddl"})

    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str | list[dict[str, str]]]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for post in _outermost(tree.css("div.post[id^='post-']")):
            self._add_post(post)

    def _add_post(self, post: LexborNode) -> None:
        title = url = category = ""
        for link in post.css("h2 a[href]"):
            href = link.attributes.get("href") or ""
            if href:
                url = _clean_wayback_url(urljoin(self._base_url, href))
                title = link.text().strip()
        # "s": selectors match rel values case-insensitively otherwise
        for link in post.css("div.cat a[rel*='category' s]"):
            if text := link.text().strip():
                category = text
        release, links = self._read_tvshow_info(post)
        title = release or title
        if title and (links or url):
            self.results.append(
                {
                    "title": title,
                    "url": url,
                    "release_name": release,
                    "links": links,
                    "category": category,
                }
            )

    def _read_tvshow_info(self, post: LexborNode) -> tuple[str, list[dict[str, str]]]:
        """Release name and download links of the post's ``tvshow_info``.

        The release name is the first ``<strong>`` that looks like a scene
        name. A ``Download:`` label starts the links, an ``Info:`` label (or
        the block's end) stops them; a link's text names its hoster.
        """
        release = ""
        links: list[dict[str, str]] = []
        for box in _outermost(post.css("div[class*='tvshow_info']")):
            after_download_label = False
            for node in box.css("strong, a"):
                if node.tag == "strong":
                    text = node.text().strip()
                    lower = text.lower()
                    if lower.startswith("download"):
                        after_download_label = True
                    elif lower.startswith("info"):
                        after_download_label = False
                    elif not release and "." in text and len(text) > 10:
                        release = text
                elif after_download_label and (href := node.attributes.get("href")):
                    label = node.text().strip().lower()
                    links.append(
                        {
                            "hoster": label if label in self._HOSTER_LABELS else "",
                            "link": _clean_wayback_url(href),
                        }
                    )
        return release, links


def _clean_wayback_url(url: str) -> str:
    """Strip Wayback Machine URL prefix if present."""
    m = re.match(r"https?://web\.archive\.org/web/\d+/(https?://.+)", url)
    return m.group(1) if m else url


def _category_to_torznab(category_name: str) -> int:
    """Map site category name to Torznab category ID (8000 if unknown)."""
    key = category_name.lower().strip()
    return _CATEGORY_NAME_MAP.get(key, 8000)


def _search_paths(category: int) -> tuple[str, ...]:
    """The sections to search for *category* (its parent's if not listed)."""
    return _SEARCH_PATHS.get(category) or _SEARCH_PATHS.get(
        category - category % 1000, ("",)
    )


def _is_release_name(text: str) -> bool:
    """Whether *text* looks like a scene release name (``Title.2023.1080p-GRP``)."""
    return "." in text and len(text) > 10 and not any(c.isspace() for c in text)


class _PostPageParser:
    """Extract release name and download links from a single post page (selectolax).

    Covers both layouts: TV posts (``tvshow_info`` block) and film/P2P posts
    (info table). In both, the release name is the first ``<strong>`` that
    looks like a scene name (a period, more than 10 characters, no spaces:
    film posts bold an awards line before it), and the download links are
    anchors labelled Torrent / Usenet / NZB inside ``div.storycontent``.
    """

    _LINK_LABELS = frozenset({"torrent", "usenet", "nzb"})

    def __init__(self) -> None:
        self.release_name = ""
        self.links: list[dict[str, str]] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for story in _outermost(tree.css("div.storycontent")):
            for strong in story.css("strong"):
                text = strong.text().strip()
                if not self.release_name and _is_release_name(text):
                    self.release_name = text
            for link in story.css("a[href]"):
                href = link.attributes.get("href") or ""
                label = link.text().strip().lower()
                if label in self._LINK_LABELS and href.startswith("http"):
                    self.links.append(
                        {"hoster": label, "link": _clean_wayback_url(href)}
                    )


class ScnSrcPlugin(PlaywrightPluginBase):
    """Python plugin for scnsrc.me using Playwright.

    Supports multiple domains with automatic fallback:
    scnsrc.me, scenesource.me, scnsrc.net.
    """

    name = "scnsrc"
    version = "1.1.0"
    mode = "playwright"
    provides = "download"
    languages = ["en"]

    _domains = _DOMAINS
    # nginx rate-limits post pages (503) under parallel fetches
    _max_concurrent = 2

    async def _fetch_page(self, url: str) -> str:
        """Navigate to a URL and return page content ("" on failure).

        Server-rendered WordPress, so no ``networkidle`` wait. Rate-limit
        503s are retried with backoff.
        """
        return await self._fetch_page_html(
            url, wait_for_idle=False, retry_backoff_s=_RETRY_BACKOFF_S
        )

    async def _enrich_post(
        self, post: dict[str, str | list[dict[str, str]]]
    ) -> dict[str, str | list[dict[str, str]]]:
        """Fill release name and links from the post page.

        Search pages only list title and category; the ``tvshow_info`` block
        with the download links lives on the post page.
        """
        if post.get("links") or not post.get("url"):
            return post
        parser = _PostPageParser()
        parser.feed(await self._fetch_page(str(post["url"])))
        return {**post, "release_name": parser.release_name, "links": parser.links}

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

        q = quote_plus(query)
        if page_num > 1:
            url = f"{base}/page/{page_num}/?s={q}"
        else:
            url = f"{base}/?s={q}"

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
        if category is not None:
            category = served_category(category, _CATEGORY_NAME_MAP.values())
            if category is None:
                return []  # no section of the site has this category
        await self._ensure_browser()
        await self._verify_domain()

        # Paginate search results (WordPress: ~10 posts/page); keep the posts
        # of the requested category before their pages are loaded
        all_posts: list[dict[str, str | list[dict[str, str]]]] = []
        for category_path in _search_paths(category) if category else ("",):
            for page_num in range(1, _MAX_PAGES + 1):
                posts = await self._search_page(query, category_path, page_num)
                if not posts:
                    break
                all_posts.extend(
                    p
                    for p in posts
                    if category_matches(
                        category, _category_to_torznab(str(p.get("category", "")))
                    )
                )
                if len(all_posts) >= self.effective_max_results:
                    break

        all_posts = all_posts[: self.effective_max_results]

        sem = self._new_semaphore()

        async def _bounded_enrich(
            post: dict[str, str | list[dict[str, str]]],
        ) -> dict[str, str | list[dict[str, str]]]:
            async with sem:
                return await self._enrich_post(post)

        all_posts = list(await asyncio.gather(*map(_bounded_enrich, all_posts)))

        results: list[SearchResult] = []
        for post in all_posts:
            links = post.get("links")
            if not isinstance(links, list) or not links:
                continue

            primary_link = links[0]["link"]
            torznab_cat = _category_to_torznab(str(post.get("category", "")))

            # Scene release name (from the post page) parses best downstream
            title = str(post.get("release_name") or post["title"])
            results.append(
                SearchResult(
                    title=title,
                    download_link=primary_link,
                    download_links=links,
                    source_url=str(post.get("url", "")),
                    category=torznab_cat,
                )
            )

        return results


plugin = ScnSrcPlugin()
