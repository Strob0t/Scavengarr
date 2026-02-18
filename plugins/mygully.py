"""mygully.com Python plugin for Scavengarr.

Scrapes mygully.com (vBulletin forum) with:
- Domain fallback (mygully.com, mygully.to)
- Playwright for Cloudflare Turnstile bypass
- vBulletin form-based authentication (MD5 password hash)
- Search form submission with forum/category filtering
- Download link extraction from post content (link container services)
- Bounded concurrency for thread scraping
- Pagination up to 1000 results

Credentials via env vars: SCAVENGARR_MYGULLY_USERNAME / SCAVENGARR_MYGULLY_PASSWORD
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["mygully.com", "mygully.to"]
_DEFAULT_FORUM_ID = "25"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Torznab category -> vBulletin forum ID mapping.
# Uses parent forum IDs with childforums=1 for broad matching.
# "25" (Video) is the default.
_CATEGORY_FORUM_MAP: dict[int, str] = {
    2000: "25",  # Movies  -> Video (Filme, HD, DVD, Bluray, UHD, 3D, Doku)
    5000: "25",  # TV      -> Video (Serien, Anime)
    3000: "26",  # Audio   -> Audio (Alben, Lossless, Singles, Soundtracks)
    7000: "363",  # Books   -> Text & HowTos (eBooks, Magazine, Comics)
    4000: "27",  # PC      -> Games
    1000: "27",  # Console -> Games
}

# Hosts that are internal (not download links).
_INTERNAL_HOSTS = {
    "mygully.com",
    "mygully.to",
}

# Known link-protection / container services.
# Only links from these domains are treated as download links.
_LINK_CONTAINER_HOSTS = {
    "keeplinks.org",
    "keeplinks.eu",
    "keeplinks.co",
    "share-links.biz",
    "share-links.org",
    "filecrypt.cc",
    "filecrypt.co",
    "safelinks.to",
    "protectlinks.com",
    "hide.cx",
    "linkcrypt.ws",
}


class _PostLinkParser(HTMLParser):
    """Extract download links from vBulletin post content.

    Only captures links to known link-protection containers
    (keeplinks.org, filecrypt.cc, etc.) from post_message divs.
    The hoster name is derived from the anchor text when available.
    """

    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._in_post = False
        self._div_depth = 0
        self._in_a = False
        self._current_href = ""
        self._current_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "div":
            if self._in_post:
                self._div_depth += 1
            else:
                div_id = attr_dict.get("id", "")
                if div_id.startswith("post_message"):
                    self._in_post = True
                    self._div_depth = 0
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
            if self._div_depth > 0:
                self._div_depth -= 1
            else:
                self._in_post = False
        if tag == "a" and self._in_a:
            self._in_a = False
            href = self._current_href
            text = self._current_text.strip()

            # Only accept links from known container services
            host = (urlparse(href).hostname or "").replace("www.", "")
            if not _is_container_host(host):
                return

            # Derive hoster name from anchor text
            hoster = _hoster_from_text(text) or _hoster_from_url(href)

            if href not in [entry["link"] for entry in self.links]:
                self.links.append({"hoster": hoster, "link": href})


class _ThreadLinkParser(HTMLParser):
    """Extract thread links from vBulletin search results page.

    Handles both friendly URLs (/thread/{id}-{slug}/) and
    classic URLs (showthread.php?t={id}). Normalizes by thread ID
    to avoid duplicates. Detects "Next Page" for pagination.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.thread_urls: list[str] = []
        self.next_page_url: str = ""
        self._base_url = base_url
        self._seen_ids: set[str] = set()
        self._in_nav_a = False
        self._nav_a_href = ""
        self._nav_a_text = ""

    def _handle_thread_link(self, href: str) -> None:
        # Try classic format: showthread.php?t=12345
        m = re.search(r"[?&]t=(\d+)", href)
        if m:
            tid = m.group(1)
            if tid in self._seen_ids:
                return
            self._seen_ids.add(tid)
            url = f"{self._base_url}/showthread.php?t={tid}"
            self.thread_urls.append(url)
            return

        # Try friendly URL format: /thread/12345-slug/
        m = re.search(r"/thread/(\d+)", href)
        if m:
            tid = m.group(1)
            if tid in self._seen_ids:
                return
            self._seen_ids.add(tid)
            url = f"{self._base_url}/showthread.php?t={tid}"
            self.thread_urls.append(url)
            return

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr_dict = dict(attrs)
        href = attr_dict.get("href", "")
        if not href:
            return

        if "showthread.php" in href or "/thread/" in href:
            self._handle_thread_link(href)

        # Detect pagination links (search.php?...&page=N)
        if "search.php" in href and "page=" in href:
            self._in_nav_a = True
            self._nav_a_href = href
            self._nav_a_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_nav_a:
            self._nav_a_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_nav_a:
            self._in_nav_a = False
            text = self._nav_a_text.strip().lower()
            # vBulletin ">" or "Next" or German "Weiter"
            if text in {">", "next", "\u00bb", "weiter"}:
                self.next_page_url = self._nav_a_href


class _ThreadTitleParser(HTMLParser):
    """Extract thread title from vBulletin thread page."""

    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self._in_title_tag = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title_tag = True

    def handle_data(self, data: str) -> None:
        if self._in_title_tag and self.title is None:
            text = data.strip()
            if text:
                # Strip " - myGully.com (...)" suffix from <title>
                text = re.sub(r"\s*-\s*myGully\.com.*$", "", text, flags=re.IGNORECASE)
                if text:
                    self.title = text

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title_tag = False


class MyGullyPlugin(PlaywrightPluginBase):
    """Python plugin for mygully.com forum using Playwright."""

    name = "mygully"
    version = "1.0.0"
    mode = "playwright"
    provides = "download"
    default_language = "de"

    _domains = _DOMAINS

    def __init__(self) -> None:
        super().__init__()
        self._logged_in: bool = False
        self._session_cookies: list[dict] | None = None
        self._login_lock = asyncio.Lock()

    async def _prepare_context(self, ctx: BrowserContext) -> None:  # type: ignore[override]
        """Inject session cookies into a per-request BrowserContext."""
        if self._session_cookies:
            await ctx.add_cookies(self._session_cookies)

    async def _ensure_session(self) -> None:
        """Ensure we have authenticated session cookies.

        Uses a temporary BrowserContext for login, exports cookies,
        and stores them for injection into per-request contexts.
        """
        if self._logged_in and self._session_cookies:
            return

        async with self._login_lock:
            # Double-check after acquiring lock
            if self._logged_in and self._session_cookies:
                return

            username = os.environ.get("SCAVENGARR_MYGULLY_USERNAME", "")
            password = os.environ.get("SCAVENGARR_MYGULLY_PASSWORD", "")

            if not username or not password:
                raise RuntimeError(
                    "Missing credentials: set SCAVENGARR_MYGULLY_USERNAME "
                    "and SCAVENGARR_MYGULLY_PASSWORD"
                )

            browser = await self._ensure_browser()
            md5_pass = hashlib.md5(  # noqa: S324
                password.encode(),
            ).hexdigest()

            for domain in self._domains:
                domain_url = f"https://{domain}"
                login_ctx = await browser.new_context(
                    user_agent=self._user_agent,
                    viewport={"width": 1280, "height": 720},
                )
                try:
                    page = await login_ctx.new_page()
                    try:
                        # Load homepage to get the login form
                        await page.goto(
                            domain_url,
                            wait_until="domcontentloaded",
                        )
                        await self._wait_for_cloudflare(page)

                        # Fill and submit the vBulletin login form
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

                        # Verify login: check for session cookie
                        cookies = await login_ctx.cookies()
                        has_session = any(c["name"] == "bbsessionhash" for c in cookies)
                        if has_session:
                            self.base_url = domain_url
                            self._session_cookies = cookies
                            self._logged_in = True
                            self._log.info("mygully_login_success", domain=domain)
                            return

                    finally:
                        if not page.is_closed():
                            await page.close()

                except Exception as exc:  # noqa: BLE001
                    self._log.warning(
                        "mygully_domain_unreachable",
                        domain=domain,
                        error=str(exc),
                    )
                    continue
                finally:
                    await login_ctx.close()

            raise RuntimeError("All mygully domains failed during login")

    async def _submit_search_form(self, query: str, forum_id: str) -> str:
        """Submit the vBulletin search form and return results HTML."""
        ctx = await self._ensure_context()

        page = await ctx.new_page()
        try:
            await page.goto(
                f"{self.base_url}/search.php",
                wait_until="domcontentloaded",
            )
            await self._wait_for_cloudflare(page)

            await page.evaluate(
                """([q, fid]) => {
                    const form = document.getElementById('searchform');
                    if (!form) throw new Error('no searchform');
                    form.querySelector(
                        'input[name="query"]'
                    ).value = q;
                    form.querySelector(
                        'select[name="titleonly"]'
                    ).value = '1';
                    const sel = form.querySelector(
                        'select[name="forumchoice[]"]'
                    );
                    for (const o of sel.options) o.selected = false;
                    for (const o of sel.options) {
                        if (o.value === fid) {
                            o.selected = true;
                            break;
                        }
                    }
                    const cb = form.querySelector(
                        'input[name="childforums"]'
                    );
                    if (cb) cb.checked = true;
                    for (const r of form.querySelectorAll(
                        'input[name="showposts"]'
                    )) {
                        r.checked = (r.value === '0');
                    }
                }""",
                [query, forum_id],
            )

            async with page.expect_navigation(
                wait_until="domcontentloaded", timeout=15_000
            ):
                await page.evaluate(
                    """() => {
                        document.getElementById('searchform').submit();
                    }"""
                )

            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:  # noqa: BLE001
                pass

            return await page.content()
        except Exception:
            self._logged_in = False
            raise
        finally:
            if not page.is_closed():
                await page.close()

    async def _search_threads(self, query: str, forum_id: str = "25") -> list[str]:
        """Submit search form and paginate through results.

        Collects up to 1000 thread URLs by following "Next Page" links.
        """
        html = await self._submit_search_form(query, forum_id)

        all_urls: list[str] = []
        seen: set[str] = set()

        parser = _ThreadLinkParser(self.base_url)
        parser.feed(html)
        for url in parser.thread_urls:
            if url not in seen:
                seen.add(url)
                all_urls.append(url)

        next_url = parser.next_page_url
        while next_url and len(all_urls) < self.effective_max_results:
            if not next_url.startswith("http"):
                next_url = f"{self.base_url}/{next_url.lstrip('/')}"

            try:
                html = await self._fetch_page_html(next_url)
            except Exception:  # noqa: BLE001
                break

            parser = _ThreadLinkParser(self.base_url)
            parser.feed(html)

            new_count = 0
            for url in parser.thread_urls:
                if url not in seen:
                    seen.add(url)
                    all_urls.append(url)
                    new_count += 1

            if new_count == 0:
                break
            next_url = parser.next_page_url

        return all_urls[: self.effective_max_results]

    async def _scrape_thread(self, url: str) -> SearchResult | None:
        """Scrape a single thread page for title and download links."""
        ctx = await self._ensure_context()

        page = await ctx.new_page()
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
        link_parser = _PostLinkParser()
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

    async def cleanup(self) -> None:
        """Close browser and reset login state."""
        await super().cleanup()
        self._logged_in = False
        self._session_cookies = None

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search mygully.com and return results with download links."""
        await self._ensure_session()

        forum_id = _CATEGORY_FORUM_MAP.get(category or 2000, _DEFAULT_FORUM_ID)
        thread_urls = await self._search_threads(query, forum_id)

        if not thread_urls:
            return []

        sem = self._new_semaphore()

        async def _bounded_scrape(url: str) -> SearchResult | None:
            async with sem:
                return await self._scrape_thread(url)

        results = await asyncio.gather(
            *[_bounded_scrape(url) for url in thread_urls],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, SearchResult)]


def _is_container_host(host: str) -> bool:
    """Check if a hostname belongs to a known link container."""
    return any(host.endswith(c) for c in _LINK_CONTAINER_HOSTS)


def _hoster_from_text(text: str) -> str:
    """Derive hoster name from anchor text.

    Handles patterns like 'RapidGator' or 'download via ddownload.com'.
    """
    if not text:
        return ""
    # "download via rapidgator.net" -> "rapidgator"
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


plugin = MyGullyPlugin()
