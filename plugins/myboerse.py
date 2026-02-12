"""myboerse.bz Python plugin for Scavengarr.

Scrapes myboerse.bz (XenForo German DDL forum, Boerse.to successor) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- XenForo form-based authentication with CSRF token (_xfToken)
- Search via /search/search?keywords=QUERY&c[title_only]=1&order=date
- Category filtering via c[nodes][]=FORUM_ID parameter
- Pagination up to 1000 items (~20 results/page, max 50 pages)
- Download link extraction from thread posts (hide.cx, filecrypt.cc, /xtra/)
- Bounded concurrency for detail page scraping

Multi-domain support with automatic fallback (myboerse.bz / .ws / .me).
Credentials via env vars: SCAVENGARR_MYBOERSE_USERNAME / SCAVENGARR_MYBOERSE_PASSWORD
"""

from __future__ import annotations

import asyncio
import os
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

_MAX_PAGES = 50  # ~20 results/page → 50 pages for 1000

# Torznab category → list of XenForo forum node IDs.
_TORZNAB_TO_NODE_IDS: dict[int, list[int]] = {
    2000: [60, 61, 62, 75, 67, 68, 71, 72, 74],  # Movies
    5000: [63],  # TV Series
    5070: [64],  # Anime
    5080: [65],  # Documentary
    3000: [51, 50, 56, 57, 113],  # Audio/Music
    3030: [52],  # Audiobooks
    4000: [24, 25, 35],  # PC Games
    1000: [28, 29, 30, 31, 32],  # Console Games
    5020: [9, 10, 11, 13, 14, 16, 17, 21],  # Software
    7000: [37, 39, 40, 41, 42, 44, 47, 48],  # Books/Docs
}

# Reverse: forum node ID → Torznab category.
_NODE_TO_TORZNAB: dict[int, int] = {}
for _tz_cat, _nodes in _TORZNAB_TO_NODE_IDS.items():
    for _nid in _nodes:
        _NODE_TO_TORZNAB[_nid] = _tz_cat

# Forum name (lowercase) → Torznab category (fallback for text matching).
_FORUM_NAME_MAP: dict[str, int] = {
    # Movies
    "filme": 2000,
    "dvd": 2000,
    "hd": 2000,
    "uhd / 4k": 2000,
    "uhd/4k": 2000,
    "3d": 2000,
    "englisch": 2000,
    "cartoon / zeichentrick": 2000,
    "sammlungen / collections": 2000,
    "konzerte / musik": 2000,
    # TV
    "serien": 5000,
    # Anime
    "anime": 5070,
    # Documentary
    "dokumentationen": 5080,
    # Audio
    "musik": 3000,
    "diskographien und sammlungen": 3000,
    "hq audio / lossless": 3000,
    "soundtracks / ost": 3000,
    "klassik musik": 3000,
    # Audiobooks
    "hörbücher und hörspiele": 3030,
    # PC Games
    "pc spiele": 4000,
    "mac spiele": 4000,
    "linux spiele": 4000,
    # Console Games
    "microsoft": 1000,
    "sony": 1000,
    "nintendo": 1000,
    # Software
    "windows": 5020,
    "mac": 5020,
    "android": 5020,
    "ipad / iphone": 5020,
    "portable software": 5020,
    "nulled scripts / websoftware": 5020,
    # Books/Docs
    "comics": 7000,
    "fachbücher / sachbücher": 7000,
    "magazine / zeitschriften": 7000,
    "mangas": 7000,
    "unterhaltung": 7000,
    "englische ebooks": 7000,
    "fremdsprachige magazine": 7000,
}

# Known link-protection / container services for download links.
_LINK_CONTAINER_HOSTS = {
    "hide.cx",
    "filecrypt.cc",
    "filecrypt.co",
    "keeplinks.org",
    "keeplinks.eu",
    "tolink.to",
    "safelinks.to",
    "share-links.biz",
    "share-links.org",
    "protectlinks.com",
}


def _is_download_link(href: str) -> bool:
    """Check if a URL is a download link (container host or /xtra/ redirect)."""
    if "/xtra/" in href:
        return True
    host = (urlparse(href).hostname or "").replace("www.", "")
    return any(host.endswith(c) for c in _LINK_CONTAINER_HOSTS)


def _hoster_from_text(text: str) -> str:
    """Derive hoster name from anchor text like 'Online rapidgator.net'."""
    if not text:
        return ""
    m = re.search(r"online\s+(\S+)", text, re.IGNORECASE)
    if m:
        domain = m.group(1).rstrip(".")
        parts = domain.replace("www.", "").split(".")
        return parts[0].lower() if parts else ""
    if not text.startswith("http") and len(text.split()) <= 2:
        return text.strip().lower()
    return ""


def _hoster_from_url(url: str) -> str:
    """Extract hoster name from URL domain."""
    try:
        if "/xtra/" in url:
            return "myboerse"
        host = urlparse(url).hostname or ""
        parts = host.replace("www.", "").split(".")
        return parts[0] if parts else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _forum_name_to_torznab(name: str) -> int:
    """Map XenForo forum name to Torznab category ID."""
    key = name.strip().lower()
    if key in _FORUM_NAME_MAP:
        return _FORUM_NAME_MAP[key]
    for forum_key, cat_id in _FORUM_NAME_MAP.items():
        if forum_key in key or key in forum_key:
            return cat_id
    return 2000  # default: Movies


def _node_id_from_url(url: str) -> int | None:
    """Extract XenForo forum node ID from a URL like /forums/filme.60/."""
    m = re.search(r"/forums/[^/]*\.(\d+)/", url)
    return int(m.group(1)) if m else None


class _LoginTokenParser(HTMLParser):
    """Extract _xfToken from XenForo login page.

    Looks for ``<input type="hidden" name="_xfToken" value="...">``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.token: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        attr_dict = dict(attrs)
        if attr_dict.get("name") == "_xfToken" and not self.token:
            self.token = attr_dict.get("value", "") or ""


class _SearchResultParser(HTMLParser):
    """Parse XenForo search results page.

    Extracts thread URLs, titles, and forum info from search results.
    Also detects the 'Next' pagination link.

    XenForo search results have structure like::

        <li class="block-row block-row--separated">
          <div class="contentRow">
            <h3 class="contentRow-title">
              <a href="/threads/title.12345/">Thread Title</a>
            </h3>
            <div class="contentRow-minor">
              <li><a href="/forums/hd.62/">HD</a></li>
            </div>
          </div>
        </li>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self.next_page_url: str = ""
        self._base_url = base_url

        # Title link tracking
        self._in_h3 = False
        self._in_title_a = False
        self._current_href = ""
        self._current_title = ""

        # Forum link tracking
        self._pending_url = ""
        self._pending_title = ""
        self._in_forum_a = False
        self._current_forum = ""
        self._current_forum_href = ""

        # Pagination
        self._in_nav_a = False
        self._nav_a_href = ""
        self._nav_a_text = ""

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        href = attr_dict.get("href", "") or ""

        if tag == "h3":
            classes = (attr_dict.get("class") or "").split()
            if "contentRow-title" in classes:
                self._in_h3 = True

        if tag == "a" and self._in_h3 and "/threads/" in href:
            self._in_title_a = True
            self._current_href = href
            self._current_title = ""

        # Forum link (in minor content area)
        if tag == "a" and "/forums/" in href and self._pending_url:
            self._in_forum_a = True
            self._current_forum = ""
            self._current_forum_href = href

        # Pagination link
        if tag == "a" and href and "page-" in href:
            self._in_nav_a = True
            self._nav_a_href = href
            self._nav_a_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_title_a:
            self._current_title += data
        if self._in_forum_a:
            self._current_forum += data
        if self._in_nav_a:
            self._nav_a_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._in_h3:
            self._in_h3 = False

        if tag == "a":
            if self._in_title_a:
                self._in_title_a = False
                title = self._current_title.strip()
                href = self._current_href
                if title and href:
                    if self._pending_url:
                        self.results.append(
                            {
                                "title": self._pending_title,
                                "url": self._pending_url,
                                "forum": "",
                                "forum_href": "",
                            }
                        )
                    self._pending_url = urljoin(self._base_url, href)
                    self._pending_title = title

            if self._in_forum_a:
                self._in_forum_a = False
                forum = self._current_forum.strip()
                if self._pending_url:
                    self.results.append(
                        {
                            "title": self._pending_title,
                            "url": self._pending_url,
                            "forum": forum,
                            "forum_href": self._current_forum_href,
                        }
                    )
                    self._pending_url = ""
                    self._pending_title = ""

            if self._in_nav_a:
                self._in_nav_a = False
                text = self._nav_a_text.strip().lower()
                if text in {
                    "nächste",
                    "next",
                    "nächste…",
                    "next…",
                    "›",
                    "»",
                }:
                    self.next_page_url = self._nav_a_href

    def flush_pending(self) -> None:
        """Emit any pending result that has no forum yet."""
        if self._pending_url:
            self.results.append(
                {
                    "title": self._pending_title,
                    "url": self._pending_url,
                    "forum": "",
                    "forum_href": "",
                }
            )
            self._pending_url = ""
            self._pending_title = ""


class _ThreadPostParser(HTMLParser):
    """Extract download links from XenForo thread posts.

    Parses ``<div class="bbWrapper">`` blocks and extracts ``<a>``
    links pointing to known container hosts or the site's /xtra/
    redirector.
    """

    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._in_message_body = False
        self._div_depth = 0
        self._in_a = False
        self._current_href = ""
        self._current_text = ""
        self._seen_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        if tag == "div":
            if self._in_message_body:
                self._div_depth += 1
            elif "bbWrapper" in classes:
                self._in_message_body = True
                self._div_depth = 0

        if tag == "a" and self._in_message_body:
            href = attr_dict.get("href", "") or ""
            if href and href.startswith("http"):
                self._in_a = True
                self._current_href = href
                self._current_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._current_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_message_body:
            if self._div_depth > 0:
                self._div_depth -= 1
            else:
                self._in_message_body = False

        if tag == "a" and self._in_a:
            self._in_a = False
            href = self._current_href
            text = self._current_text.strip()

            if href in self._seen_urls:
                return

            if not _is_download_link(href):
                return

            # For /xtra/ redirector links, always use URL-based hoster
            if "/xtra/" in href:
                hoster = _hoster_from_url(href)
            else:
                hoster = _hoster_from_text(text) or _hoster_from_url(href)
            self._seen_urls.add(href)
            self.links.append({"hoster": hoster, "link": href})


class MyboersePlugin(HttpxPluginBase):
    """Python plugin for myboerse.bz using httpx."""

    name = "myboerse"
    provides = "download"
    _domains = ["myboerse.bz", "myboerse.ws", "myboerse.me"]
    _logged_in: bool = False

    async def cleanup(self) -> None:
        """Close httpx client and reset login state."""
        await super().cleanup()
        self._logged_in = False

    async def _login(self) -> None:
        """Authenticate with XenForo using CSRF token."""
        if self._logged_in:
            return

        username = os.environ.get("SCAVENGARR_MYBOERSE_USERNAME", "")
        password = os.environ.get("SCAVENGARR_MYBOERSE_PASSWORD", "")

        if not username or not password:
            raise RuntimeError(
                "Missing credentials: set SCAVENGARR_MYBOERSE_USERNAME "
                "and SCAVENGARR_MYBOERSE_PASSWORD"
            )

        client = await self._ensure_client()

        # Step 1: GET login page to extract _xfToken
        resp = await client.get(f"{self.base_url}/login/")
        resp.raise_for_status()

        token_parser = _LoginTokenParser()
        token_parser.feed(resp.text)

        if not token_parser.token:
            raise RuntimeError("Could not extract _xfToken from login page")

        # Step 2: POST login with credentials
        login_resp = await client.post(
            f"{self.base_url}/login/login",
            data={
                "login": username,
                "password": password,
                "_xfToken": token_parser.token,
                "remember": "1",
            },
        )
        login_resp.raise_for_status()

        # Verify login: check for xf_user cookie
        has_session = any(c.name == "xf_user" for c in client.cookies.jar)
        if not has_session:
            raise RuntimeError("Login failed: no session cookie received")

        self._logged_in = True
        self._log.info("myboerse_login_success")

    async def _search_page(
        self,
        query: str,
        node_ids: list[int] | None = None,
    ) -> tuple[list[dict[str, str]], str]:
        """Fetch the first search results page via POST.

        Returns ``(results, next_page_url)``.
        """
        client = await self._ensure_client()

        params: dict[str, str | list[str]] = {
            "keywords": query,
            "c[title_only]": "1",
            "order": "date",
        }
        if node_ids:
            params["c[nodes][]"] = [str(nid) for nid in node_ids]

        try:
            resp = await client.post(
                f"{self.base_url}/search/search",
                data=params,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("myboerse_search_failed", query=query, error=str(exc))
            return [], ""

        parser = _SearchResultParser(self.base_url)
        parser.feed(resp.text)
        parser.flush_pending()

        self._log.info(
            "myboerse_search_page",
            query=query,
            results=len(parser.results),
        )
        return parser.results, parser.next_page_url

    async def _fetch_next_page(self, next_url: str) -> tuple[list[dict[str, str]], str]:
        """Fetch a subsequent search results page by URL."""
        client = await self._ensure_client()

        full_url = urljoin(self.base_url, next_url)
        try:
            resp = await client.get(full_url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "myboerse_page_fetch_failed",
                url=full_url,
                error=str(exc),
            )
            return [], ""

        parser = _SearchResultParser(self.base_url)
        parser.feed(resp.text)
        parser.flush_pending()

        return parser.results, parser.next_page_url

    async def _scrape_thread(self, result: dict[str, str]) -> SearchResult | None:
        """Scrape a thread page for download links."""
        client = await self._ensure_client()

        try:
            resp = await client.get(result["url"])
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "myboerse_thread_fetch_failed",
                url=result.get("url", ""),
                error=str(exc),
            )
            return None

        post_parser = _ThreadPostParser()
        post_parser.feed(resp.text)

        if not post_parser.links:
            self._log.debug("myboerse_no_links", url=result.get("url", ""))
            return None

        # Determine Torznab category
        forum_href = result.get("forum_href", "")
        node_id = _node_id_from_url(forum_href)
        if node_id and node_id in _NODE_TO_TORZNAB:
            torznab_cat = _NODE_TO_TORZNAB[node_id]
        else:
            forum_name = result.get("forum", "")
            torznab_cat = _forum_name_to_torznab(forum_name)

        return SearchResult(
            title=result.get("title", "Unknown"),
            download_link=post_parser.links[0]["link"],
            download_links=post_parser.links,
            source_url=result.get("url", ""),
            category=torznab_cat,
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search myboerse.bz and return results with download links."""
        await self._ensure_client()
        await self._verify_domain()
        await self._login()

        # Map Torznab category to forum node IDs
        node_ids = _TORZNAB_TO_NODE_IDS.get(category) if category else None

        # Fetch first page
        first_results, next_url = await self._search_page(query, node_ids)
        all_results = list(first_results)

        # Paginate
        page_num = 1
        while (
            next_url and len(all_results) < self._max_results and page_num < _MAX_PAGES
        ):
            page_num += 1
            more_results, next_url = await self._fetch_next_page(next_url)
            if not more_results:
                break
            all_results.extend(more_results)

        all_results = all_results[: self._max_results]

        if not all_results:
            return []

        # Scrape thread detail pages with bounded concurrency
        sem = self._new_semaphore()

        async def _bounded_scrape(
            r: dict[str, str],
        ) -> SearchResult | None:
            async with sem:
                return await self._scrape_thread(r)

        results = await asyncio.gather(
            *[_bounded_scrape(r) for r in all_results],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, SearchResult)]


plugin = MyboersePlugin()
