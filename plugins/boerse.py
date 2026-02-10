"""boerse.sx Python plugin for Scavengarr.

Scrapes boerse.sx (vBulletin 3.8.12 forum) with:
- Domain fallback across multiple mirrors
- Playwright for Cloudflare JS challenge bypass
- vBulletin form-based authentication (MD5 password hash)
- Quick-search via #lsa_input form (includes securitytoken)
- Download link extraction from post content (keeplinks.org containers)
- Bounded concurrency for thread scraping

Credentials via env vars: SCAVENGARR_BOERSE_USERNAME / SCAVENGARR_BOERSE_PASSWORD
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import structlog
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

_DOMAINS = [
    "https://boerse.am",
    "https://boerse.sx",
    "https://boerse.im",
    "https://boerse.ai",
    "https://boerse.kz",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_PAGES = 3

# Hosts that are internal (not download links)
_INTERNAL_HOSTS = {"boerse.am", "boerse.sx", "boerse.im", "boerse.ai", "boerse.kz"}

# Known link-protection / container services
_LINK_CONTAINER_HOSTS = {"keeplinks.org", "keeplinks.eu"}


class _PostLinkParser(HTMLParser):
    """Extract download links from vBulletin post content.

    Captures external links from ``[id^="post_message"]`` divs.
    Links to known link-protection containers (keeplinks.org) or
    external hosters are collected; internal boerse links are skipped.
    The hoster name is derived from the anchor text when available.
    """

    def __init__(self, base_domain: str) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._base_domain = base_domain
        self._in_post = False
        self._in_a = False
        self._current_href = ""
        self._current_text = ""

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        if tag == "div":
            div_id = attr_dict.get("id", "")
            if div_id.startswith("post_message"):
                self._in_post = True
        if tag == "a" and self._in_post:
            href = attr_dict.get("href", "")
            if href and href.startswith("http"):
                self._in_a = True
                self._current_href = href
                self._current_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._current_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_post:
            self._in_post = False
        if tag == "a" and self._in_a:
            self._in_a = False
            href = self._current_href
            text = self._current_text.strip()

            # Skip internal boerse links
            host = (urlparse(href).hostname or "").replace("www.", "")
            if host in _INTERNAL_HOSTS or not host:
                return

            # Derive hoster name from anchor text
            hoster = _hoster_from_text(text) or _hoster_from_url(href)

            if href not in [entry["link"] for entry in self.links]:
                self.links.append({"hoster": hoster, "link": href})


class _ThreadLinkParser(HTMLParser):
    """Extract thread links from vBulletin search results page."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.thread_urls: list[str] = []
        self._base_url = base_url

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
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

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == "title":
            self._in_title_tag = True

    def handle_data(self, data: str) -> None:
        if self._in_title_tag and self.title is None:
            text = data.strip()
            if text:
                # Strip " - Boerse.AM (...)" suffix from <title>
                text = re.sub(
                    r"\s*-\s*[Bb]oerse\.\w+.*$", "", text
                )
                if text:
                    self.title = text

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title_tag = False


class BoersePlugin:
    """Python plugin for boerse.sx forum using Playwright."""

    name = "boerse"

    def __init__(self) -> None:
        self._domains = list(_DOMAINS)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._logged_in = False
        self.base_url = self._domains[0]

    async def _ensure_browser(self) -> None:
        """Launch Chromium if not already running."""
        if self._browser is not None:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
        )
        self._context = await self._browser.new_context(
            user_agent=_USER_AGENT,
        )

    async def _wait_for_cloudflare(self, page: Page) -> None:
        """If Cloudflare challenge is detected, wait for it to resolve."""
        try:
            await page.wait_for_function(
                "() => !document.title.includes('Just a moment')",
                timeout=15_000,
            )
        except Exception:  # noqa: BLE001
            pass  # proceed anyway — page may still be usable

    async def _ensure_session(self) -> None:
        """Ensure we have an authenticated Playwright session."""
        await self._ensure_browser()
        if self._logged_in:
            return

        username = os.environ.get("SCAVENGARR_BOERSE_USERNAME", "")
        password = os.environ.get("SCAVENGARR_BOERSE_PASSWORD", "")

        if not username or not password:
            raise RuntimeError(
                "Missing credentials: set SCAVENGARR_BOERSE_USERNAME "
                "and SCAVENGARR_BOERSE_PASSWORD"
            )

        assert self._context is not None
        md5_pass = hashlib.md5(  # noqa: S324
            password.encode(),
        ).hexdigest()

        for domain in self._domains:
            try:
                page = await self._context.new_page()
                try:
                    # Load homepage to get the real login form
                    await page.goto(
                        domain,
                        wait_until="domcontentloaded",
                    )
                    await self._wait_for_cloudflare(page)

                    # Fill and submit the existing login form
                    async with page.expect_navigation(
                        wait_until="domcontentloaded",
                        timeout=15_000,
                    ):
                        await page.evaluate(
                            """([user, md5]) => {
                                const f = document.querySelector(
                                    'form[action*="login"]'
                                );
                                if (!f) throw new Error('no login form');
                                const u = f.querySelector(
                                    'input[name="vb_login_username"]'
                                );
                                const p = f.querySelector(
                                    'input[name="vb_login_password"]'
                                );
                                const m = f.querySelector(
                                    'input[name="vb_login_md5password"]'
                                );
                                if (u) u.value = user;
                                if (p) p.value = '';
                                if (m) m.value = md5;
                                f.submit();
                            }""",
                            [username, md5_pass],
                        )

                    # Wait for redirect to complete
                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=10_000
                        )
                    except Exception:  # noqa: BLE001
                        pass

                    # Verify login: check for welcome message
                    body = await page.evaluate(
                        "() => document.body.innerText"
                    )
                    if username.lower() in body.lower():
                        self.base_url = domain
                        self._logged_in = True
                        log.info(
                            "boerse_login_success", domain=domain
                        )
                        await page.close()
                        return

                finally:
                    if not page.is_closed():
                        await page.close()

            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "boerse_domain_unreachable",
                    domain=domain,
                    error=str(exc),
                )
                continue

        raise RuntimeError("All boerse domains failed during login")

    async def _search_threads(self, query: str) -> list[str]:
        """Navigate to search.php, fill the form, extract thread URLs."""
        assert self._context is not None

        page = await self._context.new_page()
        try:
            # Load the search page (contains real form with securitytoken)
            await page.goto(
                f"{self.base_url}/search.php",
                wait_until="domcontentloaded",
            )
            await self._wait_for_cloudflare(page)

            # Fill the quick-search input (#lsa_input) and submit
            await page.fill("#lsa_input", query)

            async with page.expect_navigation(
                wait_until="domcontentloaded", timeout=15_000
            ):
                await page.evaluate(
                    """() => {
                        const input = document.getElementById(
                            'lsa_input'
                        );
                        input.closest('form').submit();
                    }"""
                )

            # Wait for results page to fully load
            try:
                await page.wait_for_load_state(
                    "networkidle", timeout=10_000
                )
            except Exception:  # noqa: BLE001
                pass

            html = await page.content()
            parser = _ThreadLinkParser(self.base_url)
            parser.feed(html)
            return parser.thread_urls
        except Exception:
            self._logged_in = False
            raise
        finally:
            if not page.is_closed():
                await page.close()

    async def _scrape_thread(self, url: str) -> SearchResult | None:
        """Scrape a single thread page for title and download links."""
        assert self._context is not None

        page = await self._context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            await self._wait_for_cloudflare(page)

            html = await page.content()
        except Exception:  # noqa: BLE001
            return None
        finally:
            if not page.is_closed():
                await page.close()

        # Extract title
        title_parser = _ThreadTitleParser()
        title_parser.feed(html)
        title = title_parser.title or "Unknown"

        # Extract download links from post content
        base_domain = urlparse(self.base_url).hostname or ""
        link_parser = _PostLinkParser(base_domain)
        link_parser.feed(html)

        if not link_parser.links:
            return None

        primary_link = link_parser.links[0]["link"]

        return SearchResult(
            title=title,
            download_link=primary_link,
            download_links=link_parser.links,
            source_url=url,
            category=2000,
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
    ) -> list[SearchResult]:
        """Search boerse.sx and return results with download links."""
        await self._ensure_session()
        thread_urls = await self._search_threads(query)

        if not thread_urls:
            return []

        sem = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)

        async def _bounded_scrape(url: str) -> SearchResult | None:
            async with sem:
                return await self._scrape_thread(url)

        results = await asyncio.gather(
            *[_bounded_scrape(url) for url in thread_urls],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, SearchResult)]

    async def cleanup(self) -> None:
        """Close browser and Playwright resources."""
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._logged_in = False


def _hoster_from_text(text: str) -> str:
    """Derive hoster name from anchor text.

    Handles patterns like 'RapidGator' or 'download via ddownload.com'.
    """
    if not text:
        return ""
    # "download via rapidgator.net" → "rapidgator"
    m = re.search(r"via\s+(\S+)", text, re.IGNORECASE)
    if m:
        host = m.group(1).rstrip(".")
        parts = host.replace("www.", "").split(".")
        return parts[0].lower() if parts else ""
    # Plain hoster name like "RapidGator", "DDownload"
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


plugin = BoersePlugin()
