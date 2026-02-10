"""boerse.sx Python plugin for Scavengarr.

Scrapes boerse.sx (vBulletin 3.8.12 forum) with:
- Domain fallback across multiple mirrors
- vBulletin form-based authentication (MD5 password hash)
- POST-based search with redirect to results
- Link anonymizer handling (actual URL in <a> text, not href)

Credentials via env vars: SCAVENGARR_BOERSE_USERNAME / SCAVENGARR_BOERSE_PASSWORD
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from html.parser import HTMLParser

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

_DOMAINS = [
    "https://boerse.am",
    "https://boerse.sx",
    "https://boerse.im",
    "https://boerse.ai",
    "https://boerse.kz",
]

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class _LinkParser(HTMLParser):
    """Extract <a> tags whose visible text starts with http (anonymizer pattern)."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self._in_a = False
        self._current_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._in_a = True
            self._current_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._current_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_a:
            self._in_a = False
            text = self._current_text.strip()
            if text.startswith("http"):
                self.links.append(text)


class _ThreadLinkParser(HTMLParser):
    """Extract thread links from vBulletin search results page."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.thread_urls: list[str] = []
        self._base_url = base_url

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr_dict = dict(attrs)
        href = attr_dict.get("href", "")
        if not href:
            return
        # vBulletin thread links: showthread.php?t=... or /threads/...
        if "showthread.php" in href or "/threads/" in href:
            if not href.startswith("http"):
                href = f"{self._base_url}/{href.lstrip('/')}"
            if href not in self.thread_urls:
                self.thread_urls.append(href)


class _ThreadTitleParser(HTMLParser):
    """Extract thread title from vBulletin thread page."""

    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self._in_title_tag = False
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        cls = attr_dict.get("class", "")
        # vBulletin thread title is typically in the page <title> or a specific class
        if tag == "title":
            self._in_title_tag = True
            self._depth = 0

    def handle_data(self, data: str) -> None:
        if self._in_title_tag and self.title is None:
            text = data.strip()
            if text:
                # Strip " - boerse.am" suffix from <title>
                text = re.sub(r"\s*-\s*boerse\.\w+$", "", text)
                if text:
                    self.title = text

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title_tag = False


class BoersePlugin:
    """Python plugin for boerse.sx forum."""

    name = "boerse"

    def __init__(self) -> None:
        self._domains = list(_DOMAINS)
        self._username = os.environ.get("SCAVENGARR_BOERSE_USERNAME", "")
        self._password = os.environ.get("SCAVENGARR_BOERSE_PASSWORD", "")
        self._client: httpx.AsyncClient | None = None
        self._logged_in = False
        self.base_url = self._domains[0]

    async def search(
        self, query: str, category: int | None = None,
    ) -> list[SearchResult]:
        """Search boerse.sx and return results with download links."""
        await self._ensure_session()
        thread_urls = await self._search_threads(query)

        if not thread_urls:
            return []

        tasks = [self._scrape_thread(url) for url in thread_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, SearchResult)]

    async def _ensure_session(self) -> None:
        """Ensure we have an authenticated httpx session."""
        if self._logged_in and self._client is not None:
            return

        if not self._username or not self._password:
            raise RuntimeError(
                "Missing credentials: set SCAVENGARR_BOERSE_USERNAME "
                "and SCAVENGARR_BOERSE_PASSWORD"
            )

        if self._client is not None:
            await self._client.aclose()

        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": _USER_AGENT},
        )

        md5_pass = hashlib.md5(  # noqa: S324
            self._password.encode()
        ).hexdigest()

        for domain in self._domains:
            try:
                resp = await self._client.post(
                    f"{domain}/login.php?do=login",
                    data={
                        "vb_login_username": self._username,
                        "vb_login_password": "",
                        "vb_login_md5password": md5_pass,
                        "do": "login",
                        "s": "",
                        "securitytoken": "guest",
                    },
                )
                # Success: bb_userid cookie set or redirect to forum index
                cookies = {c.name for c in self._client.cookies.jar}
                if "bb_userid" in cookies or resp.status_code == 200:
                    self.base_url = domain
                    self._logged_in = True
                    log.info("boerse_login_success", domain=domain)
                    return

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                log.warning(
                    "boerse_domain_unreachable",
                    domain=domain,
                    error=str(e),
                )
                continue

        raise RuntimeError("All boerse domains failed during login")

    async def _search_threads(self, query: str) -> list[str]:
        """POST search and extract thread URLs from results."""
        assert self._client is not None

        try:
            resp = await self._client.post(
                f"{self.base_url}/search.php?do=process",
                data={
                    "do": "process",
                    "query": query,
                    "titleonly": "1",
                    "showposts": "0",
                },
            )
        except (httpx.ConnectError, httpx.TimeoutException):
            self._logged_in = False
            raise

        parser = _ThreadLinkParser(self.base_url)
        parser.feed(resp.text)
        return parser.thread_urls

    async def _scrape_thread(self, url: str) -> SearchResult | None:
        """Scrape a single thread page for title and download links."""
        assert self._client is not None

        try:
            resp = await self._client.get(url)
        except (httpx.ConnectError, httpx.TimeoutException):
            return None

        if resp.status_code != 200:
            return None

        html = resp.text

        # Extract title
        title_parser = _ThreadTitleParser()
        title_parser.feed(html)
        title = title_parser.title or "Unknown"

        # Extract download links (anonymizer pattern)
        link_parser = _LinkParser()
        link_parser.feed(html)

        if not link_parser.links:
            return None

        primary_link = link_parser.links[0]
        download_links = [
            {"hoster": _hoster_from_url(link), "link": link}
            for link in link_parser.links
        ]

        return SearchResult(
            title=title,
            download_link=primary_link,
            download_links=download_links,
            source_url=url,
            category=2000,
        )


def _hoster_from_url(url: str) -> str:
    """Extract hoster name from URL domain."""
    try:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
        # Strip www. and TLD
        parts = host.replace("www.", "").split(".")
        return parts[0] if parts else "unknown"
    except Exception:
        return "unknown"


plugin = BoersePlugin()
