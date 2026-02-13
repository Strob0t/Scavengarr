"""anime-loads.org Python plugin for Scavengarr.

Scrapes anime-loads.org (German anime/manga streaming & download site) via Playwright:
- GET /search?q={query} for search (server-rendered HTML behind DDoS-Guard)
- Pagination via /search/page/{n}?q={query}, 20 results per page
- Anime series, movies, OVAs, live action with rich metadata
- Download/stream links behind per-episode captcha — provides media page URLs

DDoS-Guard protection requires browser-based access (Playwright mode).
Domain fallback: www.anime-loads.org, anime-loads.org
No authentication required for search.
"""

from __future__ import annotations

from playwright.async_api import Page

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = [
    "www.anime-loads.org",
    "anime-loads.org",
]
_PAGE_SIZE = 20
_MAX_PAGES = 50  # 20/page * 50 = 1000
_DDOS_TIMEOUT = 30_000  # ms to wait for DDoS-Guard resolution
_NAV_TIMEOUT = 30_000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Map content types (lowercase) to Torznab categories.
_TYPE_CATEGORY: dict[str, int] = {
    "anime series": 5070,
    "anime movie": 2000,
    "ova": 5070,
    "bonus": 5070,
    "live action": 5000,
    "hentai": 5070,
}

# Torznab categories that accept TV-like types.
_TV_TYPES = {"anime series", "ova", "bonus", "live action", "hentai"}
_MOVIE_TYPES = {"anime movie"}

# JavaScript to extract search results from the DOM.
_EXTRACT_RESULTS_JS = """
() => {
    const panels = document.querySelectorAll('.panel.panel-default');
    const results = [];
    for (const panel of panels) {
        const titleEl = panel.querySelector('h4.title-list a');
        if (!titleEl) continue;

        const title = titleEl.textContent.trim();
        const mediaUrl = titleEl.getAttribute('href') || '';
        const slug = mediaUrl.split('/media/').pop() || '';

        const coverImg = panel.querySelector('.cover-img img');
        const poster = coverImg ? coverImg.getAttribute('src') : '';

        const listStatus = panel.querySelector('.list-status');
        const dataId = listStatus ? listStatus.getAttribute('data-id') : '';

        const statusLabel = panel.querySelector('.list-status .label');
        const status = statusLabel ? statusLabel.textContent.trim() : '';

        const labels = panel.querySelectorAll('.label-group .label');
        let type = '', year = '', episodes = '';
        for (const label of labels) {
            const text = label.textContent.trim();
            if (['Anime Series', 'Anime Movie', 'OVA', 'Bonus',
                 'Live Action', 'Hentai'].some(t => text.includes(t))) {
                type = text;
            } else if (/^\\d{4}$/.test(text)) {
                year = text;
            } else if (/\\d+\\/\\d+/.test(text) || /^\\d+$/.test(text)) {
                episodes = text;
            }
        }

        let description = '';
        const allDivs = panel.querySelectorAll('.col-sm-7 > div');
        for (const div of allDivs) {
            if (!div.classList.contains('label-group') &&
                !div.querySelector('h4') &&
                !div.querySelector('.flag') &&
                !div.querySelector('a[href*="genre"]') &&
                !div.querySelector('a[href*="main-genre"]') &&
                div.textContent.trim().length > 30) {
                description = div.textContent.trim();
                break;
            }
        }

        const genreLinks = panel.querySelectorAll(
            'a[href*="/main-genre/"], a[href*="/genre/"]');
        const genres = [];
        for (const g of genreLinks) genres.push(g.textContent.trim());

        const langFlags = panel.querySelectorAll('[title*="Language"]');
        const languages = [];
        for (const f of langFlags)
            languages.push(f.getAttribute('title').replace('Language: ', ''));

        const subFlags = panel.querySelectorAll('[title*="Subtitles"]');
        const subtitles = [];
        for (const f of subFlags)
            subtitles.push(f.getAttribute('title').replace('Subtitles: ', ''));

        const embedBtn = panel.querySelector('.quickstream');
        const embedUrl = embedBtn ? embedBtn.getAttribute('data-embed') : '';

        results.push({
            title, slug, mediaUrl, dataId, status, type, year,
            episodes, description, genres, languages, subtitles,
            poster, embedUrl
        });
    }
    return results;
}
"""

# JavaScript to extract pagination info.
_PAGINATION_RE = r"/Showing\s+(\d+)\s+to\s+(\d+)\s+of\s+(\d+)\s+entries/"

_EXTRACT_PAGINATION_JS = (
    "() => {"
    "  const text = document.body.innerText;"
    f"  const match = text.match({_PAGINATION_RE});"
    "  if (!match) return {total: 0, totalPages: 0};"
    "  const total = parseInt(match[3]);"
    "  const totalPages = Math.ceil(total / 20);"
    "  return {total, totalPages};"
    "}"
)


def _detect_category(content_type: str) -> int:
    """Map anime-loads content type to Torznab category."""
    return _TYPE_CATEGORY.get(content_type.lower().strip(), 5070)


def _matches_category(content_type: str, category: int | None) -> bool:
    """Check whether a content type matches the requested Torznab category."""
    if category is None:
        return True
    ct = content_type.lower().strip()
    if 5000 <= category < 6000:
        return ct in _TV_TYPES
    if 2000 <= category < 3000:
        return ct in _MOVIE_TYPES
    return True


class AnimeLoadsPlugin(PlaywrightPluginBase):
    """Python plugin for anime-loads.org using Playwright (DDoS-Guard bypass)."""

    name = "animeloads"
    version = "1.0.0"
    mode = "playwright"
    provides = "both"
    default_language = "de"

    _domains = _DOMAINS

    async def _wait_for_ddos_guard(self, page: "Page") -> bool:
        """Wait for DDoS-Guard JS challenge to resolve.

        Uses ``nav`` and ``.panel-default`` as indicators that the real
        page has loaded.  ``h1`` is intentionally excluded because the
        DDoS-Guard challenge page contains its own ``<h1>`` heading.
        """
        try:
            await page.wait_for_selector(
                "nav, .panel-default",
                timeout=_DDOS_TIMEOUT,
            )
            return True
        except Exception:  # noqa: BLE001
            # Check if we're still on the DDoS-Guard page
            content = await page.content()
            if "ddos-guard" in content.lower():
                self._log.warning("animeloads_ddos_guard_timeout")
                return False
            # Page loaded but no expected elements
            return True

    async def _search_page(
        self,
        page: "Page",
        query: str,
        page_num: int,
    ) -> tuple[list[dict], int]:
        """Fetch one page of search results.

        Returns (results, total_pages).
        """
        if page_num == 1:
            url = f"{self.base_url}/search?q={query}"
        else:
            url = f"{self.base_url}/search/page/{page_num}?q={query}"

        try:
            await page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "animeloads_search_nav_failed",
                query=query,
                page=page_num,
                error=str(exc),
            )
            return [], 0

        if not await self._wait_for_ddos_guard(page):
            return [], 0

        try:
            results = await page.evaluate(_EXTRACT_RESULTS_JS)
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "animeloads_extract_failed",
                query=query,
                page=page_num,
                error=str(exc),
            )
            return [], 0

        try:
            pagination = await page.evaluate(_EXTRACT_PAGINATION_JS)
            total_pages = pagination.get("totalPages", 0)
        except Exception:  # noqa: BLE001
            total_pages = 0

        self._log.info(
            "animeloads_search_page",
            query=query,
            page=page_num,
            results=len(results),
            total_pages=total_pages,
        )
        return results, total_pages

    def _build_search_result(self, entry: dict) -> SearchResult:
        """Build a SearchResult from extracted search entry data."""
        title = entry.get("title", "")
        year = entry.get("year", "")
        content_type = entry.get("type", "")
        slug = entry.get("slug", "")
        episodes = entry.get("episodes", "")

        display_title = f"{title} ({year})" if year else title
        if episodes:
            display_title += f" [{episodes}]"

        category = _detect_category(content_type)
        source_url = entry.get("mediaUrl", "") or f"{self.base_url}/media/{slug}"

        # Use embed URL as primary download link, fallback to media page
        embed_url = entry.get("embedUrl", "")
        download_link = embed_url if embed_url else source_url

        # Build download_links list
        download_links: list[dict[str, str]] = []
        if embed_url:
            download_links.append({"hoster": "Preview Stream", "link": embed_url})
        download_links.append({"hoster": "Media Page", "link": source_url})

        # Description
        desc = entry.get("description", "") or ""
        if len(desc) > 300:
            desc = desc[:297] + "..."

        # Metadata
        genres = entry.get("genres", [])
        languages = entry.get("languages", [])
        subtitles = entry.get("subtitles", [])
        poster = entry.get("poster", "")

        return SearchResult(
            title=display_title,
            download_link=download_link,
            download_links=download_links or None,
            validated_links=[download_link],  # Pre-validated: DDoS-Guard blocks httpx
            source_url=source_url,
            published_date=year if year else None,
            category=category,
            description=desc or None,
            metadata={
                "type": content_type,
                "genres": ", ".join(genres) if genres else "",
                "languages": ", ".join(languages) if languages else "",
                "subtitles": ", ".join(subtitles) if subtitles else "",
                "episodes": episodes,
                "status": entry.get("status", ""),
                "poster": poster,
                "data_id": entry.get("dataId", ""),
            },
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search anime-loads.org and return results with metadata.

        Uses Playwright to bypass DDoS-Guard and extract search results
        from server-rendered HTML pages.

        When *season*/*episode* are provided the results are filtered
        to TV-like types only (no movies), since the site doesn't
        support direct episode navigation from search.
        """
        if not query:
            return []

        # Accept movies (2xxx), TV/Anime (5xxx)
        if category is not None:
            if not (2000 <= category < 3000 or 5000 <= category < 6000):
                return []

        # When season/episode requested, restrict to TV types
        effective_category = category
        if season is not None and effective_category is None:
            effective_category = 5070

        if season is not None or episode is not None:
            self._log.info(
                "animeloads_season_episode_hint",
                season=season,
                episode=episode,
            )

        page = await self._new_page()
        try:
            await self._verify_domain()
            return await self._search_all_pages(page, query, effective_category)
        finally:
            await page.close()

    async def _search_all_pages(
        self,
        page: "Page",
        query: str,
        category: int | None,
    ) -> list[SearchResult]:
        """Fetch all search result pages up to _max_results."""
        first_results, total_pages = await self._search_page(page, query, 1)
        if not first_results:
            return []

        all_results = self._filter_and_build(first_results, category)
        if total_pages <= 1 or len(all_results) >= self.effective_max_results:
            return all_results[: self.effective_max_results]

        pages_to_fetch = min(total_pages, _MAX_PAGES)
        for page_num in range(2, pages_to_fetch + 1):
            if len(all_results) >= self.effective_max_results:
                break
            page_results, _ = await self._search_page(page, query, page_num)
            if not page_results:
                break
            batch = self._filter_and_build(page_results, category)
            all_results.extend(batch)

        return all_results[: self.effective_max_results]

    def _filter_and_build(
        self,
        entries: list[dict],
        category: int | None,
    ) -> list[SearchResult]:
        """Filter entries by category and build SearchResult objects."""
        results: list[SearchResult] = []
        for entry in entries:
            content_type = entry.get("type", "")
            if not _matches_category(content_type, category):
                continue
            sr = self._build_search_result(entry)
            results.append(sr)
        return results


plugin = AnimeLoadsPlugin()
