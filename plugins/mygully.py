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
from typing import TYPE_CHECKING

from selectolax.lexbor import LexborHTMLParser

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    filter_by_category,
    is_series_title,
    served_category,
)
from scavengarr.infrastructure.plugins.dom import parse_page
from scavengarr.infrastructure.plugins.forum_links import (
    hoster_from_text,
    hoster_from_url,
    is_link_container,
)
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase

if TYPE_CHECKING:
    from patchright._impl._api_structures import SetCookieParam
    from patchright.async_api import BrowserContext

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
    4000: "27",  # PC      -> Games (console games too, not told apart)
}
# Forum -> category of its threads (Video: series by their title)
_FORUM_CATEGORIES: dict[str, int] = {"25": 2000, "26": 3000, "363": 7000, "27": 4050}


def _thread_category(title: str, forum_id: str) -> int:
    """Torznab category of a thread: its forum's, Video threads by title."""
    category = _FORUM_CATEGORIES.get(forum_id, 8000)
    if category == 2000 and is_series_title(title):
        return 5000
    return category


# Hosts that are internal (not download links).
_INTERNAL_HOSTS = {
    "mygully.com",
    "mygully.to",
}


class _PostLinkParser:
    """Extract download links from vBulletin post content (selectolax).

    Only captures links to known link-protection containers
    (keeplinks.org, filecrypt.cc, etc.) from post_message divs,
    nested divs included.
    The hoster name is derived from the anchor text when available.
    """

    def __init__(self) -> None:
        self.links: list[dict[str, str]] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for anchor in tree.css("div[id^='post_message'] a[href^='http']"):
            href = anchor.attributes.get("href") or ""

            # Only accept links from known container services
            if not is_link_container(href):
                continue

            # Derive hoster name from anchor text
            text = anchor.text().strip()
            hoster = hoster_from_text(text) or hoster_from_url(href)

            if href not in [entry["link"] for entry in self.links]:
                self.links.append({"hoster": hoster, "link": href})


class _ThreadLinkParser:
    """Extract thread links from vBulletin search results page (selectolax).

    Handles both friendly URLs (/thread/{id}-{slug}/) and
    classic URLs (showthread.php?t={id}). Normalizes by thread ID
    to avoid duplicates. Detects "Next Page" for pagination.
    """

    def __init__(self, base_url: str) -> None:
        self.thread_urls: list[str] = []
        self.next_page_url: str = ""
        self._base_url = base_url
        self._seen_ids: set[str] = set()

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

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for link in tree.css("a[href]"):
            href = link.attributes.get("href") or ""
            if not href:
                continue

            if "showthread.php" in href or "/thread/" in href:
                self._handle_thread_link(href)

            # Pagination links (search.php?...&page=N): vBulletin ">" or
            # "Next" or German "Weiter"; the last one wins
            if "search.php" in href and "page=" in href:
                text = link.text().strip().lower()
                if text in {">", "next", "\u00bb", "weiter"}:
                    self.next_page_url = href


class _ThreadTitleParser:
    """Extract thread title from vBulletin thread page (selectolax).

    The first ``<title>`` with text left after stripping its
    " - myGully.com (...)" suffix names the thread.
    """

    def __init__(self) -> None:
        self.title: str | None = None

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for node in tree.css("title"):
            # Strip " - myGully.com (...)" suffix from <title>
            text = re.sub(
                r"\s*-\s*myGully\.com.*$", "", node.text().strip(), flags=re.IGNORECASE
            )
            if text and self.title is None:
                self.title = text


class MyGullyPlugin(PlaywrightPluginBase):
    """Python plugin for mygully.com forum using Playwright."""

    name = "mygully"
    version = "1.0.0"
    mode = "playwright"
    provides = "download"

    _domains = _DOMAINS

    def __init__(self) -> None:
        super().__init__()
        self._logged_in: bool = False
        self._session_cookies: list[SetCookieParam] | None = None
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
                login_ctx = await browser.new_context(**self._context_options())
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
                        has_session = any(
                            c.get("name") == "bbsessionhash" for c in cookies
                        )
                        if has_session:
                            self.base_url = domain_url
                            self._session_cookies = self._cookie_params(cookies)
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

        parser = await parse_page(_ThreadLinkParser(self.base_url), html)
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

            parser = await parse_page(_ThreadLinkParser(self.base_url), html)

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

    async def _scrape_thread(self, url: str, forum_id: str) -> SearchResult | None:
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
        title_parser = await parse_page(_ThreadTitleParser(), html)
        title = title_parser.title or "Unknown"

        # Extract download links from post content
        link_parser = await parse_page(_PostLinkParser(), html)

        if not link_parser.links:
            return None

        primary_link = link_parser.links[0]["link"]

        return SearchResult(
            title=title,
            download_link=primary_link,
            download_links=link_parser.links,
            source_url=url,
            category=_thread_category(title, forum_id),
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
        forum_id = _DEFAULT_FORUM_ID
        if category is not None:
            category = served_category(category, (*_FORUM_CATEGORIES.values(), 5000))
            if category is None:
                return []  # no forum of the board has this category
            forum_id = (
                _CATEGORY_FORUM_MAP.get(category)
                or _CATEGORY_FORUM_MAP[category - category % 1000]
            )

        await self._ensure_session()
        # isolated_search() prepares its context before the login above, and
        # plain search() uses the singleton context: hand the session (login
        # + Cloudflare clearance) to the context this search really uses
        await self._prepare_context(await self._ensure_context())

        thread_urls = await self._search_threads(query, forum_id)

        if not thread_urls:
            return []

        sem = self._new_semaphore()

        async def _bounded_scrape(url: str) -> SearchResult | None:
            async with sem:
                return await self._scrape_thread(url, forum_id)

        gathered = await asyncio.gather(
            *[_bounded_scrape(url) for url in thread_urls],
            return_exceptions=True,
        )
        results = [r for r in gathered if isinstance(r, SearchResult)]
        if category is not None:
            results = filter_by_category(results, category)
        return results


plugin = MyGullyPlugin()
