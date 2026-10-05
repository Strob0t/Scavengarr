"""megakino plugin for Scavengarr.

Scrapes megakino (German streaming site, DLE-based CMS) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- POST /index.php?do=search with story={query} for keyword search
- Pagination via search_start/result_from POST params (20 results/page)
- Detail page scraping for stream iframe URLs (film) and select hosters (series)
- Series detection from "Serien" in genre text or "Staffel" in title
- Category filtering (Movies/TV/Animation)
- Bounded concurrency for detail page scraping

Domain chain: megakino1.biz (primary), megakino1.ws, megakino1.net, megakino.me.
No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    STREAM_CATEGORIES,
    filter_by_category,
    served_category,
    stream_category,
)
from scavengarr.infrastructure.plugins.dom import classes
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.relevance import (
    SINGLE_TITLE_HITS,
    hit_title,
    relevant_hits,
)

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["megakino1.biz", "megakino1.ws", "megakino1.net", "megakino.me"]
_RESULTS_PER_PAGE = 20
_MAX_PAGES = 50  # 1000 / 20

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_QUALITY_LABELS = frozenset(
    {
        "hd",
        "sd",
        "4k",
        "webrip",
        "bdrip",
        "camrip",
        "ts",
        "cam/md",
        "hdtv",
        "cam",
        "hdcam",
    }
)


def _detect_series(categories_text: str, title: str) -> bool:
    """Detect if an item is a series from category text or title."""
    parts = [p.strip().lower() for p in categories_text.split("/")]
    if "serien" in parts:
        return True
    if "staffel" in title.lower():
        return True
    return False


def _clean_title(title: str) -> str:
    """Strip common suffixes and trailing year."""
    title = title.strip()
    for suffix in (" Film", " Serie", " film", " serie"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
    return title.strip()


_EPISODE_NUM_RE = re.compile(r"(\d{1,2})\s*[xX]\s*(\d{1,4})")


_STAFFEL_RE = re.compile(r"staffel\s*(\d{1,3})", re.IGNORECASE)


def _other_season(title: str, season: int) -> bool:
    """True when *title* names a season ("X - Staffel N") other than *season*.

    megakino has one page per season; a title without season number is
    kept (single-season shows).
    """
    m = _STAFFEL_RE.search(title)
    return m is not None and int(m.group(1)) != season


def _label_matches_episode(label: str, episode: int) -> bool:
    """Check if a link label matches the requested episode number."""
    m = _EPISODE_NUM_RE.search(label)
    if m:
        return int(m.group(2)) == episode
    return False


def _domain_from_url(url: str) -> str:
    """Extract domain name from a URL for hoster labeling."""
    try:
        host = urlparse(url).hostname or ""
        parts = host.replace("www.", "").split(".")
        return parts[0] if parts and parts[0] else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _parse_genres(text: str) -> list[str]:
    """Parse genres from 'Filme / Action / Thriller' format.

    Filters out top-level site categories.
    """
    parts = [p.strip() for p in text.split("/")]
    skip = {"filme", "kinofilme", "serien", "dokumentationen"}
    return [p for p in parts if p and p.lower() not in skip]


def _parse_year(text: str) -> str:
    """Extract four-digit year from text."""
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return m.group(0) if m else ""


def _parse_runtime(text: str) -> str:
    """Extract runtime minutes from text like 'Country, 2024, 120 min'."""
    m = re.search(r"(\d+)\s*min", text)
    return m.group(1) if m else ""


def _last_text(node: LexborNode, selector: str) -> str:
    """The text of the last match of *selector* in *node*, "" without one."""
    matches = node.css(selector)
    return matches[-1].text() if matches else ""


def _description(tree: LexborHTMLParser) -> str:
    """The plot: the first ``<div class="... full-text ...">`` with text.

    User comments below the plot are "full-text" divs too.
    """
    for div in tree.css("div.full-text"):
        if "comment-item__main" in classes(div):
            continue
        text = div.text().strip()
        if text:
            return text
    return ""


def _poster_url(tree: LexborHTMLParser, base_url: str) -> str:
    """The first poster image of ``<div class="pmovie__poster ...">``."""
    for img in tree.css("div.pmovie__poster img"):
        src = img.attributes.get("data-src") or img.attributes.get("src") or ""
        if src and "/no-img" not in src:
            return urljoin(base_url, src)
    return ""


def _rating(node: LexborNode) -> str:
    """The number of a rating element, "" without one.

    Only the text before the first closing div or span counts: the site
    rating's vote count follows in a span of its own::

        <div class="pmovie__subrating pmovie__subrating--site"><img ...>
          <div><span><span>+963</span></span><span>1317</span></div>
        </div>
    """
    parts: list[str] = []
    _text_until_closed(node, parts)
    m = re.search(r"(\d+[.,]?\d*)", "".join(parts))
    return m.group(1).replace(",", ".") if m else ""


def _text_until_closed(node: LexborNode, parts: list[str]) -> bool:
    """Add the text of *node* to *parts* until a div or span in it closes.

    Returns whether one closed.
    """
    for child in node.iter(include_text=True):
        if child.is_text_node:
            parts.append(child.text_content or "")
        elif _text_until_closed(child, parts) or child.tag in ("div", "span"):
            return True
    return False


class _SearchResultParser:
    """Parse megakino.me search result page (selectolax).

    Each result card::

        <a class="poster grid-item ..." href="/crime/4692-title.html">
          <div class="poster__img ...">
            <img data-src="..." alt="...">
            <div class="poster__label">HD</div>
          </div>
          <div class="poster__desc">
            <h3 class="poster__title ...">Title</h3>
            <ul class="poster__subtitle ...">
              <li>Country, Year</li>
              <li>Genre1 / Genre2 / Category</li>
            </ul>
            <div class="poster__text ...">Description</div>
          </div>
        </a>

    A card needs a title and a link. Of several titles, labels or texts in
    a card the last one counts; the poster is the first image with a
    ``data-src``.
    """

    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str | list[str] | bool]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for card in tree.css("a.poster.grid-item"):
            self._add_card(card)

    def _add_card(self, card: LexborNode) -> None:
        href = card.attributes.get("href") or ""
        title = _last_text(card, "h3.poster__title")
        if not title or not href:
            return

        subtitle = [
            text
            for li in card.css("ul.poster__subtitle li")
            if (text := li.text().strip())
        ]
        categories_text = subtitle[1] if len(subtitle) > 1 else ""
        label = _last_text(card, "div.poster__label").strip()
        poster = next(
            (
                src
                for img in card.css("img")
                if (src := img.attributes.get("data-src") or "")
            ),
            "",
        )

        self.results.append(
            {
                "title": _clean_title(title),
                "url": urljoin(self._base_url, href),
                "genres": _parse_genres(categories_text),
                "quality": label if label.lower() in _QUALITY_LABELS else "",
                "label": label,
                "is_series": _detect_series(categories_text, title),
                "year": _parse_year(subtitle[0]) if subtitle else "",
                "description": _last_text(card, "div.poster__text").strip(),
                "poster_url": urljoin(self._base_url, poster) if poster else "",
                "categories_text": categories_text,
            }
        )


class _DetailPageParser:
    """Parse megakino.me detail page for stream links and metadata (selectolax).

    Film hosters use tabs::

        <div class="tabs-block__select ...">
          <span class="is-active">Voe</span>
          <span>Doodstream</span>
        </div>
        <div class="tabs-block__content ...">
          <a href="/dl/12345">...</a>
        </div>

    Series hosters use select elements::

        <select class="mr-select" id="ep1">
          <option value="https://voe.sx/e/abc">Voe</option>
        </select>
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

        # Stream links (final output)
        self.stream_links: list[dict[str, str]] = []

        self.title = ""
        self.year = ""
        self.runtime = ""
        self.genres: list[str] = []
        self.categories_text = ""
        self.description = ""
        self.kp_rating = ""
        self.site_rating = ""
        self.poster_url = ""

        # Series flag
        self.is_series = False

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        self._read_metadata(tree)
        self._read_hosters(tree)

    def _read_metadata(self, tree: LexborHTMLParser) -> None:
        # Title: the first h1 with text
        for h1 in tree.css("h1"):
            self.title = _clean_title(h1.text())
            if self.title:
                break

        # Year/runtime: <div class="pmovie__year">
        for div in tree.css("div.pmovie__year"):
            text = div.text()
            self.year = _parse_year(text)
            self.runtime = _parse_runtime(text)

        # Genres: <div class="pmovie__genres">
        for div in tree.css("div.pmovie__genres"):
            self.categories_text = div.text().strip()
            self.genres = _parse_genres(self.categories_text)

        self.description = _description(tree)

        # Ratings: the last one with a number
        for node in tree.css(".pmovie__subrating--kp"):
            self.kp_rating = _rating(node) or self.kp_rating
        for node in tree.css(".pmovie__subrating--site"):
            self.site_rating = _rating(node) or self.site_rating

        self.poster_url = _poster_url(tree, self._base_url)

    def _read_hosters(self, tree: LexborHTMLParser) -> None:
        # The n-th tabs-block__content plays the n-th tab name listed before it
        names: list[str] = []
        tabs = 0
        for node in tree.css(
            "div.tabs-block__select span, div.tabs-block__content, select.mr-select"
        ):
            if node.tag == "span":
                name = node.text().strip()
                if name:
                    names.append(name)
            elif node.tag == "div":
                self._add_tab(node, names[tabs] if tabs < len(names) else "")
                tabs += 1
            else:
                self._add_episode(node)

    def _add_tab(self, content: LexborNode, label: str) -> None:
        """Add the stream of a film tab: the last link in its content."""
        link = ""
        for node in content.css("iframe, a"):
            if node.tag == "iframe":
                # <iframe data-src="https://voe.sx/e/..." />
                src = node.attributes.get("data-src") or node.attributes.get("src")
                if src and src.startswith("http"):
                    link = src
            else:
                # Legacy: <a href="/dl/...">
                href = node.attributes.get("href") or ""
                if "/dl/" in href:
                    link = urljoin(self._base_url, href)
        if link:
            self.stream_links.append(
                {
                    "hoster": label.lower() if label else _domain_from_url(link),
                    "link": link,
                    "label": label,
                }
            )

    def _add_episode(self, select: LexborNode) -> None:
        """Add the hosters of an episode's ``<select class="mr-select">``."""
        # Episode number from id="ep1", "ep2", etc.
        m = re.match(r"ep(\d+)", select.attributes.get("id") or "")
        episode = int(m.group(1)) if m else 0
        for option in select.css("option"):
            link = option.attributes.get("value") or ""
            if not link.startswith("http"):
                continue
            name = option.text().strip()
            domain = _domain_from_url(link)
            label = f"1x{episode} {name or domain}" if episode else name or domain
            self.stream_links.append(
                {
                    "hoster": name.lower() if name else domain,
                    "link": link,
                    "label": label,
                }
            )

    def finalize(self) -> None:
        """Post-processing: detect series from genres."""
        self.is_series = _detect_series(self.categories_text, self.title)


class MegakinoPlugin(HttpxPluginBase):
    """Python plugin for megakino using httpx."""

    name = "megakino"
    provides = "stream"
    _domains = _DOMAINS
    _token_acquired = False

    async def _ensure_token(self) -> None:
        """Acquire the yg_token cookie required for detail page access.

        The site returns a JS challenge on first GET unless the
        ``yg_token`` cookie is present.  Fetching ``/index.php?yg=token``
        sets this cookie via a 204 response.
        """
        if self._token_acquired:
            return
        resp = await self._safe_fetch(
            f"{self.base_url}/index.php?yg=token", context="token"
        )
        self._token_acquired = resp is not None

    async def _search_page(
        self,
        query: str,
        page: int = 0,
    ) -> list[dict[str, str | list[str] | bool]]:
        """Fetch a search results page via POST.

        DLE CMS search uses::
            POST /index.php?do=search
            Form data: do=search, subaction=search, story={query},
                        search_start={N}, result_from={offset}
        """
        form_data: dict[str, str] = {
            "do": "search",
            "subaction": "search",
            "story": query,
            "search_start": str(page),
            "full_search": "0",
            "result_from": str(max(1, (page - 1) * _RESULTS_PER_PAGE + 1)),
        }

        resp = await self._safe_fetch(
            f"{self.base_url}/index.php?do=search",
            method="POST",
            context="search",
            data=form_data,
        )
        if resp is None:
            return []

        parser = await self._feed(_SearchResultParser(self.base_url), resp.text)

        self._log.info(
            "megakino_search_page",
            query=query,
            page=page,
            results=len(parser.results),
        )
        return parser.results

    async def _search_all_pages(
        self,
        query: str,
    ) -> list[dict[str, str | list[str] | bool]]:
        """Fetch search results with pagination up to _max_results."""
        all_results: list[dict[str, str | list[str] | bool]] = []

        # DLE: search_start 0 and 1 are the same first page
        for page_num in range(1, _MAX_PAGES + 1):
            results = await self._search_page(query, page_num)
            if not results:
                break
            all_results.extend(results)
            if len(all_results) >= self.effective_max_results:
                break
            # If we got fewer results than a full page, no more pages
            if len(results) < _RESULTS_PER_PAGE:
                break

        return all_results[: self.effective_max_results]

    async def _scrape_detail(
        self,
        result: dict[str, str | list[str] | bool],
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Scrape a detail page for stream links and metadata."""
        detail_url = str(result["url"])
        html = await self._fetch_text(detail_url, context="detail")
        if html is None:
            return None

        parser = await self._feed(_DetailPageParser(self.base_url), html)
        parser.finalize()

        # Filter series links by episode (ep1, ep2, ... labels)
        if parser.is_series and episode is not None and parser.stream_links:
            filtered = [
                lnk
                for lnk in parser.stream_links
                if _label_matches_episode(lnk.get("label", ""), episode)
            ]
            # No match: this page does not have the episode (no fallback to
            # all episodes, that would present wrong episodes as matches)
            parser.stream_links = filtered

        if not parser.stream_links:
            self._log.debug("megakino_no_streams", url=detail_url)
            return None

        title = parser.title or str(result.get("title", ""))
        listed = result.get("genres")
        genres = parser.genres or (list(listed) if isinstance(listed, list) else [])
        is_series = parser.is_series or bool(result.get("is_series", False))
        quality = str(result.get("quality", ""))
        category = stream_category(genres, is_series=is_series)

        description_parts: list[str] = []
        if genres:
            description_parts.append(", ".join(genres))
        if parser.year:
            description_parts.append(f"({parser.year})")
        if parser.description:
            description_parts.append(parser.description)
        description = " ".join(description_parts) if description_parts else ""

        metadata: dict[str, str] = {
            "year": parser.year,
            "genres": ", ".join(genres),
            "quality": quality,
            "kp_rating": parser.kp_rating,
            "site_rating": parser.site_rating,
            "poster_url": parser.poster_url,
        }

        return SearchResult(
            title=title,
            download_link=parser.stream_links[0]["link"],
            download_links=parser.stream_links,
            source_url=detail_url,
            category=category,
            description=description,
            metadata=metadata,
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search megakino and return results with stream links."""
        if category is None and season is not None:
            category = 5000  # a season request is a series request
        if category is not None:
            category = served_category(category, STREAM_CATEGORIES)
            if category is None:
                return []  # the site has films and series only
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        # Each hit costs a detail page and its player: scrape real matches only
        all_items = relevant_hits(
            await self._search_all_pages(query),
            query,
            hit_title,
            limit=SINGLE_TITLE_HITS if season is not None else None,
        )
        if season is not None:
            all_items = [
                r
                for r in all_items
                if not _other_season(str(r.get("title", "")), season)
            ]
        if not all_items:
            return []

        # Token is required for detail page GET requests (JS challenge).
        await self._ensure_token()

        # Scrape detail pages with bounded concurrency
        sem = self._new_semaphore()

        async def _bounded(
            r: dict[str, str | list[str] | bool],
        ) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(r, season=season, episode=episode)

        gathered = await asyncio.gather(
            *[_bounded(r) for r in all_items],
            return_exceptions=True,
        )

        results: list[SearchResult] = [
            r for r in gathered if isinstance(r, SearchResult)
        ]

        if category is not None:
            results = filter_by_category(results, category)

        return results


plugin = MegakinoPlugin()
