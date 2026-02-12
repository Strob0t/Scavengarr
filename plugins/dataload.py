"""data-load.me Python plugin for Scavengarr.

Scrapes data-load.me (XenForo 2024 German DDL forum) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- XenForo form-based authentication with CSRF token (_xfToken)
- Search via /search/search?keywords=QUERY&c[title_only]=1&order=date
- Category filtering via c[nodes][]=FORUM_ID parameter
- Pagination up to 1000 items (~20 results/page, max 50 pages)
- Download link extraction from thread posts (hide.cx, filecrypt.cc, etc.)
- Bounded concurrency for detail page scraping

Credentials via env vars: SCAVENGARR_DATALOAD_USERNAME / SCAVENGARR_DATALOAD_PASSWORD
"""

from __future__ import annotations

import asyncio
import os
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

_BASE_URL = "https://www.data-load.me"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_RESULTS = 1000
_MAX_PAGES = 50  # ~20 results/page → 50 pages for 1000

# Torznab category → list of XenForo forum node IDs.
_TORZNAB_TO_NODE_IDS: dict[int, list[int]] = {
    2000: [6, 7, 8, 9, 10, 11, 95, 108, 109, 161, 34, 35, 36, 37, 38, 39, 99],
    5000: [12, 13, 14, 15, 16, 96, 116],
    5070: [27, 28, 29, 30, 31, 98],
    5080: [17, 18, 19, 147, 145, 110, 97],
    3000: [41, 42, 43, 44, 46, 47, 48, 169],
    3030: [45],
    4000: [50, 51, 52, 53, 54, 55, 56, 57, 58, 130, 168, 106],
    5020: [59, 61, 64, 65, 66, 67, 107, 115, 68, 69],
    7000: [70, 71, 72, 160, 94, 73, 74, 75, 76],
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
    "sd": 2000,
    "hd": 2000,
    "uhd/4k": 2000,
    "dvd": 2000,
    "3d": 2000,
    "complete bluray": 2000,
    "sport": 2000,
    "musikvideos": 2000,
    "fremdsprachige filme": 2000,
    # Animation
    "animation/zeichentrick": 2000,
    # TV
    "serien": 5000,
    "reality-tv": 5000,
    # Anime
    "anime": 5070,
    # Docs
    "dokumentationen": 5080,
    # Audio
    "audio": 3000,
    "alben": 3000,
    "singles": 3000,
    "diskographien": 3000,
    "soundtracks": 3000,
    "sampler": 3000,
    "lossless": 3000,
    "samples/sfx": 3000,
    # Audiobooks
    "hörbücher": 3030,
    # Games
    "spiele": 4000,
    "pc": 4000,
    "mac": 4000,
    "linux": 4000,
    "sony": 4000,
    "microsoft": 4000,
    "nintendo": 4000,
    "android": 4000,
    "ios": 4000,
    "vr": 4000,
    "pen&paper": 4000,
    "sonstiges": 4000,
    # Software
    "software": 5020,
    "windows": 5020,
    "dauerangebote": 5020,
    "pda/navigator": 5020,
    "auto&motor": 5020,
    "freischaltung": 5020,
    "tutorials": 5020,
    # Books
    "dokumente": 7000,
    "unterhaltung": 7000,
    "magazine": 7000,
    "fremdspr.magazine": 7000,
    "fachbücher": 7000,
    "ebooks": 7000,
    "comics": 7000,
    "manga": 7000,
    "fremdspr.comics": 7000,
    "fremdspr.manga": 7000,
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


def _is_container_host(host: str) -> bool:
    """Check if a hostname belongs to a known link container."""
    host = host.replace("www.", "")
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
    # Plain hoster name
    if not text.startswith("http") and len(text.split()) <= 2:
        return text.strip().lower()
    return ""


def _hoster_from_url(url: str) -> str:
    """Extract hoster name from URL domain."""
    try:
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
    # Try partial match
    for forum_key, cat_id in _FORUM_NAME_MAP.items():
        if forum_key in key or key in forum_key:
            return cat_id
    return 2000  # default: Movies


def _node_id_from_url(url: str) -> int | None:
    """Extract XenForo forum node ID from a URL like /forums/filme.6/."""
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
              <li><a href="/forums/hd.8/">HD</a></li>
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
                    # Flush any pending result without forum info
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
                if text in {"nächste", "next", "nächste…", "next…", "›", "»"}:
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

    Parses ``<article class="message">`` blocks and extracts ``<a>``
    links pointing to known container hosts (hide.cx, filecrypt.cc, etc.)
    from ``<div class="bbWrapper">`` content.
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

            # Only accept links from known container services
            host = (urlparse(href).hostname or "").replace("www.", "")
            if not _is_container_host(host):
                return

            hoster = _hoster_from_text(text) or _hoster_from_url(href)
            self._seen_urls.add(href)
            self.links.append({"hoster": hoster, "link": href})


class DataloadPlugin:
    """Python plugin for data-load.me using httpx."""

    name = "dataload"
    version = "1.0.0"
    mode = "httpx"
    provides = "download"
    default_language = "de"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._logged_in = False
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

    async def _login(self) -> None:
        """Authenticate with XenForo using CSRF token."""
        if self._logged_in:
            return

        username = os.environ.get("SCAVENGARR_DATALOAD_USERNAME", "")
        password = os.environ.get("SCAVENGARR_DATALOAD_PASSWORD", "")

        if not username or not password:
            raise RuntimeError(
                "Missing credentials: set SCAVENGARR_DATALOAD_USERNAME "
                "and SCAVENGARR_DATALOAD_PASSWORD"
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
        log.info("dataload_login_success")

    async def _search_page(
        self,
        query: str,
        node_ids: list[int] | None = None,
        page_num: int = 1,
    ) -> tuple[list[dict[str, str]], str]:
        """Fetch a single search results page.

        Returns ``(results, next_page_url)``.
        """
        client = await self._ensure_client()

        if page_num == 1:
            # Initial search — POST to /search/search
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
                log.warning("dataload_search_failed", query=query, error=str(exc))
                return [], ""
        else:
            # Subsequent page — need to use the next_page_url passed in
            # This is handled by the caller; this branch shouldn't be hit
            return [], ""

        parser = _SearchResultParser(self.base_url)
        parser.feed(resp.text)
        parser.flush_pending()

        log.info(
            "dataload_search_page",
            query=query,
            page=page_num,
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
            log.warning("dataload_page_fetch_failed", url=full_url, error=str(exc))
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
            log.warning(
                "dataload_thread_fetch_failed",
                url=result.get("url", ""),
                error=str(exc),
            )
            return None

        post_parser = _ThreadPostParser()
        post_parser.feed(resp.text)

        if not post_parser.links:
            log.debug("dataload_no_links", url=result.get("url", ""))
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
        """Search data-load.me and return results with download links."""
        await self._ensure_client()
        await self._login()

        # Map Torznab category to forum node IDs
        node_ids = _TORZNAB_TO_NODE_IDS.get(category) if category else None

        # Fetch first page
        first_results, next_url = await self._search_page(query, node_ids)
        all_results = list(first_results)

        # Paginate
        page_num = 1
        while next_url and len(all_results) < _MAX_RESULTS and page_num < _MAX_PAGES:
            page_num += 1
            more_results, next_url = await self._fetch_next_page(next_url)
            if not more_results:
                break
            all_results.extend(more_results)

        all_results = all_results[:_MAX_RESULTS]

        if not all_results:
            return []

        # Scrape thread detail pages with bounded concurrency
        sem = asyncio.Semaphore(_MAX_CONCURRENT_DETAIL)

        async def _bounded_scrape(r: dict[str, str]) -> SearchResult | None:
            async with sem:
                return await self._scrape_thread(r)

        results = await asyncio.gather(
            *[_bounded_scrape(r) for r in all_results],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, SearchResult)]

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._logged_in = False


plugin = DataloadPlugin()
