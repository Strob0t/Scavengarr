"""Shared base for XenForo 2 download forums (data-load.me, myboerse.bz).

Both forums run XenForo 2 and are scraped the same way:

- log in with ``SCAVENGARR_<NAME>_USERNAME`` / ``SCAVENGARR_<NAME>_PASSWORD``
  (form POST with the login page's ``_xfToken``). The page after the login
  carries the session's CSRF token (``<html data-csrf="...">``), which every
  XenForo POST needs (HTTP 400 without it)
- search with ``POST /search/search``: titles only, newest first, and
  ``search_type=post``, without which XenForo ignores the forum filter
  ``c[nodes][]``. A guest answer or a rejected POST means the session
  expired: log in again and retry once
- follow the ``pageNav-jump--next`` link (``?page=N``) up to 1000 results
- read the download links (link containers only) from the thread posts
- label each result by the forum node it was posted in

The plugins used to be two copies of this code; the myboerse copy had none of
the dataload fixes (CSRF token, re-login, session cookie of the own host,
``?page=N`` pagination) and returned its ``/xtra/`` affiliate link (always the
same Rapidgator file) as a download. A plugin sets ``name``, ``_domains`` and
``_node_categories``.
"""

from __future__ import annotations

import asyncio
import os
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    category_matches,
    served_category,
)
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

_MAX_PAGES = 50  # 20-25 results per page → 1000 results
# Label of results from a forum missing in ``_node_categories``
_UNKNOWN_CATEGORY = 8000

_CSRF_RE = re.compile(r'data-csrf="([^"]+)"')
# XenForo marks pages of guests (session expired or never logged in)
_LOGGED_OUT_MARKER = 'data-logged-in="false"'

# Link-protection / container services the forums post their downloads on
_LINK_CONTAINER_HOSTS = (
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
)


class _SessionExpiredError(Exception):
    """The search was answered as for a guest, or the POST was rejected."""


def _is_container_host(host: str) -> bool:
    """Check if a hostname belongs to a known link container."""
    return any(host == c or host.endswith(f".{c}") for c in _LINK_CONTAINER_HOSTS)


def _hoster_from_text(text: str) -> str:
    """Derive the hoster name from anchor text like 'Online rapidgator.net'."""
    if not text:
        return ""
    m = re.search(r"online\s+(\S+)", text, re.IGNORECASE)
    if m:
        domain = m.group(1).rstrip(".").removeprefix("www.")
        return domain.split(".")[0].lower()
    # Plain hoster name
    if not text.startswith("http") and len(text.split()) <= 2:
        return text.strip().lower()
    return ""


def _hoster_from_url(url: str) -> str:
    """Name of the URL's domain (``https://hide.cx/...`` → ``hide``)."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return "unknown"
    return host.removeprefix("www.").split(".")[0] or "unknown"


def _node_id_from_url(url: str) -> int | None:
    """XenForo forum node ID from a URL like ``/forums/filme.6/``."""
    m = re.search(r"/forums/[^/]*\.(\d+)/", url)
    return int(m.group(1)) if m else None


def _is_own_cookie(host: str, cookie_domain: str) -> bool:
    """Whether a cookie of *cookie_domain* is sent to *host*."""
    domain = cookie_domain.lstrip(".")
    return bool(domain) and (host == domain or host.endswith(f".{domain}"))


class _LoginTokenParser(HTMLParser):
    """Extract ``<input type="hidden" name="_xfToken" value="...">``."""

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
    """Parse a XenForo search results page.

    Extracts thread URLs, titles and forum info, and the next-page link::

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

        # Pagination: XenForo marks the next-page link; search result pages
        # use "?page=N", thread lists "page-N"
        if tag == "a" and "pageNav-jump--next" in (attr_dict.get("class") or ""):
            self.next_page_url = href
        elif tag == "a" and href and ("page-" in href or "page=" in href):
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
                    self.flush_pending()
                    self._pending_url = urljoin(self._base_url, href)
                    self._pending_title = title

            if self._in_forum_a:
                self._in_forum_a = False
                if self._pending_url:
                    self.results.append(
                        {
                            "title": self._pending_title,
                            "url": self._pending_url,
                            "forum": self._current_forum.strip(),
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
    """Extract the download links from the posts of a XenForo thread.

    Only ``<a>`` links inside ``<div class="bbWrapper">`` (post bodies) that
    point to a known link container count.
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
            if href.startswith("http"):
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
            if href in self._seen_urls:
                return
            if not _is_container_host(urlparse(href).hostname or ""):
                return
            text = self._current_text.strip()
            hoster = _hoster_from_text(text) or _hoster_from_url(href)
            self._seen_urls.add(href)
            self.links.append({"hoster": hoster, "link": href})


class XenForoPluginBase(HttpxPluginBase):
    """Login, search and thread scraping of a XenForo 2 download forum.

    Requests go through ``_safe_fetch()`` without the browser fallback: the
    login session lives in the HTTP client's cookie jar.
    """

    provides = "download"
    # Forum node ID → Torznab category, for every forum with downloads
    _node_categories: dict[int, int] = {}  # noqa: RUF012  # subclass overrides

    def __init__(self) -> None:
        super().__init__()
        self._logged_in = False
        self._csrf_token = ""  # _xfToken of the logged-in session

    def _credentials(self) -> tuple[str, str]:
        """Username and password from ``SCAVENGARR_<NAME>_USERNAME/_PASSWORD``."""
        prefix = f"SCAVENGARR_{self.name.upper()}_"
        username = os.environ.get(f"{prefix}USERNAME", "")
        password = os.environ.get(f"{prefix}PASSWORD", "")
        if not username or not password:
            raise RuntimeError(
                f"Missing credentials: set {prefix}USERNAME and {prefix}PASSWORD"
            )
        return username, password

    def _nodes_for(self, category: int) -> list[int]:
        """Forum nodes serving *category* (``[]``: the forum has none).

        A parent category covers its children. A child category the forum
        does not tell apart (2040 where all films are 2000) is served by the
        forums of its parent.
        """
        wanted = served_category(category, self._node_categories.values())
        if wanted is None:
            return []
        return [
            node
            for node, cat in self._node_categories.items()
            if category_matches(wanted, cat)
        ]

    async def _login(self) -> None:
        """Log in (form POST with the page's ``_xfToken``) unless logged in."""
        if self._logged_in:
            return
        username, password = self._credentials()

        page = await self._safe_fetch(f"{self.base_url}/login/", context="login")
        token_parser = _LoginTokenParser()
        token_parser.feed(page.text if page is not None else "")
        if not token_parser.token:
            raise RuntimeError("Could not extract _xfToken from login page")

        resp = await self._safe_fetch(
            f"{self.base_url}/login/login",
            method="POST",
            context="login",
            data={
                "login": username,
                "password": password,
                "_xfToken": token_parser.token,
                "remember": "1",
            },
        )
        if resp is None:
            raise RuntimeError("Login failed: the login request was rejected")

        # An xf_user cookie of *this* forum (the shared client may hold
        # another XenForo forum's cookie)
        client = await self._ensure_client()
        host = urlparse(self.base_url).hostname or ""
        if not any(
            c.name == "xf_user" and _is_own_cookie(host, str(c.domain))
            for c in client.cookies.jar
        ):
            raise RuntimeError("Login failed: no session cookie received")

        # Every XenForo POST form needs the session's CSRF token; each page
        # carries it as <html data-csrf="...">
        csrf = _CSRF_RE.search(resp.text)
        if csrf is None:
            raise RuntimeError("Login failed: no CSRF token on the page")
        self._csrf_token = csrf.group(1)
        self._logged_in = True
        self._log.info(f"{self.name}_login_success")

    def _parse_results(self, html: str) -> tuple[list[dict[str, str]], str]:
        parser = _SearchResultParser(self.base_url)
        parser.feed(html)
        parser.flush_pending()
        return parser.results, parser.next_page_url

    async def _search_page(
        self, query: str, nodes: list[int]
    ) -> tuple[list[dict[str, str]], str]:
        """Run the search; ``(rows of the first page, next page URL)``."""
        data: dict[str, str | list[str]] = {
            "keywords": query,
            # c[nodes][] only applies to post searches
            "search_type": "post",
            "c[title_only]": "1",
            "order": "date",
            "_xfToken": self._csrf_token,
        }
        if nodes:
            data["c[nodes][]"] = [str(node) for node in nodes]

        resp = await self._safe_fetch(
            f"{self.base_url}/search/search",
            method="POST",
            context="search",
            data=data,
        )
        # Expired CSRF token: XenForo rejects the POST (400/403);
        # expired session: it answers as for a guest
        if resp is None or _LOGGED_OUT_MARKER in resp.text:
            raise _SessionExpiredError
        rows, next_url = self._parse_results(resp.text)
        self._log.info(f"{self.name}_search_page", query=query, results=len(rows))
        return rows, next_url

    async def _fetch_next_page(self, next_url: str) -> tuple[list[dict[str, str]], str]:
        """Fetch a further search results page by URL."""
        resp = await self._safe_fetch(
            urljoin(self.base_url, next_url), context="next_page"
        )
        if resp is None:
            return [], ""
        return self._parse_results(resp.text)

    async def _search_rows(self, query: str, nodes: list[int]) -> list[dict[str, str]]:
        """Search result rows of all pages (one new login if the session expired)."""
        try:
            rows, next_url = await self._search_page(query, nodes)
        except _SessionExpiredError:
            self._log.info(f"{self.name}_session_expired")
            self._logged_in = False
            self._csrf_token = ""
            await self._login()
            try:
                rows, next_url = await self._search_page(query, nodes)
            except _SessionExpiredError:
                self._log.warning(f"{self.name}_search_rejected", query=query)
                return []

        page_num = 1
        while (
            next_url
            and len(rows) < self.effective_max_results
            and page_num < _MAX_PAGES
        ):
            page_num += 1
            more, next_url = await self._fetch_next_page(next_url)
            if not more:
                break
            rows.extend(more)
        return rows[: self.effective_max_results]

    async def _scrape_thread(self, row: dict[str, str]) -> SearchResult | None:
        """Read the download links of a thread; ``None`` if it has none."""
        resp = await self._safe_fetch(row["url"], context="thread")
        if resp is None:
            return None

        parser = _ThreadPostParser()
        parser.feed(resp.text)
        if not parser.links:
            self._log.debug(f"{self.name}_no_links", url=row["url"])
            return None

        node = _node_id_from_url(row.get("forum_href", ""))
        category = (
            self._node_categories.get(node, _UNKNOWN_CATEGORY)
            if node is not None
            else _UNKNOWN_CATEGORY
        )
        return SearchResult(
            title=row.get("title", "Unknown"),
            download_link=parser.links[0]["link"],
            download_links=parser.links,
            source_url=row["url"],
            category=category,
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search the forum and return its threads with download links."""
        nodes: list[int] = []
        if category is not None:
            nodes = self._nodes_for(category)
            if not nodes:
                return []  # the forum has no section for this category

        await self._ensure_client()
        await self._verify_domain()
        await self._login()

        rows = await self._search_rows(query, nodes)
        if not rows:
            return []

        sem = self._new_semaphore()

        async def _bounded_scrape(row: dict[str, str]) -> SearchResult | None:
            async with sem:
                return await self._scrape_thread(row)

        results = await asyncio.gather(
            *(_bounded_scrape(row) for row in rows), return_exceptions=True
        )
        return [r for r in results if isinstance(r, SearchResult)]

    async def cleanup(self) -> None:
        """Close the HTTP client and forget the session."""
        await super().cleanup()
        self._logged_in = False
        self._csrf_token = ""
