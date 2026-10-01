"""anime-loads.org Python plugin for Scavengarr.

Scrapes anime-loads.org (German anime/manga streaming & download site) via Playwright:
- GET /search?q={query} for search (server-rendered HTML behind DDoS-Guard)
- Pagination via /search/page/{n}?q={query}, 20 results per page
- Anime series, movies, OVAs, live action with rich metadata
- Torznab: one result per release of the first series (media page tab
  ``#download_<n>``: group, resolution, languages, archive password).
  Downloads only (no Stremio): a series' preview embed is no episode stream

Download links are resolved when a release is grabbed
(``resolve_download``, docs/plans/captcha-solving.md):
``POST /ajax/captcha`` with ``enc=base64(["media", slug, "downloads",
release, episode | "cnl"])`` answers ``noadblock`` → the site's "odd one out"
image captcha (5 × 48 px images; the odd one differs in ~4× as many pixels,
compared on a canvas in the page) → Click'n'Load-encrypted links per hoster.
Anonymous users need one captcha per episode ("cnl" = whole release needs a
login), so without ``SCAVENGARR_ANIMELOADS_USERNAME`` / ``_PASSWORD`` only
releases up to ``_MAX_ANON_EPISODES`` episodes are resolved. The requests run
in the page's main world with the site's jQuery (``isolated_context=False``).

DDoS-Guard protection requires browser-based access (Playwright mode).
Domain fallback: www.anime-loads.org, anime-loads.org
No authentication required for search.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

from patchright.async_api import Page

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.clicknload import decrypt_cnl
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

# Release expansion: media pages opened per search (browser pages are heavy)
_MAX_EXPANDED_SERIES = 10
_EXPAND_CONCURRENCY = 2

# Grab: captcha attempts per request, episodes an anonymous grab may solve,
# pacing between captcha steps / episodes (the site answers "wrong_captcha"
# to rapid bursts even for correct answers)
_CAPTCHA_ATTEMPTS = 5
# A rejected answer is often a rate limit (the site answers "" for both)
_REJECT_PAUSE_S = 4.0
_MAX_ANON_EPISODES = 13
_STEP_PAUSE_S = 1.0
_EPISODE_PAUSE_S = 2.0

_USERNAME_ENV = "SCAVENGARR_ANIMELOADS_USERNAME"
_PASSWORD_ENV = "SCAVENGARR_ANIMELOADS_PASSWORD"  # noqa: S105  (env var name)

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


# Releases of a media page: nav pill "Release 2: 1080p |" + tab #download_2
# with a th/td table and one a[data-loop] per episode (+ "cnl" for all).
_EXTRACT_RELEASES_JS = """
() => [...document.querySelectorAll('#downloads ul.nav-pills a[href^="#download_"]')]
  .map(a => {
    const id = parseInt(a.getAttribute('href').slice('#download_'.length), 10);
    const pane = document.getElementById('download_' + id);
    const cell = name => {
      for (const tr of pane ? pane.querySelectorAll('tr') : []) {
        const th = tr.querySelector('th'), td = tr.querySelector('td');
        if (th && td && th.textContent.trim() === name) return td;
      }
      return null;
    };
    const text = name => ((cell(name) || {}).textContent || '').trim();
    const flags = name => [...((cell(name) || document.createElement('i'))
      .querySelectorAll('[title]'))].map(f => f.getAttribute('title'));
    const episodes = document.querySelectorAll(
      '#downloads_episodes_' + id + ' a[data-loop]:not([data-loop="cnl"])').length;
    return {id, label: a.innerText.trim(), group: text('Release Group'),
            resolution: text('Resolution'), format: text('Typ'),
            size: text('Filesize'), languages: flags('Language'),
            subtitles: flags('Subtitles'), password: text('Password'),
            notes: text('Release Notes'), episodes};
  })
  .filter(r => Number.isInteger(r.id))
"""

# jQuery POST in the page's main world (the site rejects raw XHR here)
_AJAX_JS = """
([url, data]) => new Promise(resolve => $.ajax({url, type: 'post', data,
  complete: xhr => resolve(xhr.responseText || '')}))
"""

# Load a new "odd one out" captcha and return the hash of the odd image:
# the one whose pixels differ most from all others.
_CAPTCHA_PICK_JS = """
async () => {
  const hashes = JSON.parse(await new Promise(resolve => $.ajax({
    url: '/files/captcha', type: 'post', data: {cID: 0, rT: 1},
    complete: xhr => resolve(xhr.responseText || '[]')})));
  if (!Array.isArray(hashes) || hashes.length < 3) return null;
  const images = await Promise.all(hashes.map(h => new Promise((ok, fail) => {
    const img = new Image(); img.onload = () => ok(img); img.onerror = fail;
    img.src = '/files/captcha?cid=0&hash=' + h; })));
  const pixels = images.map(img => {
    const c = document.createElement('canvas');
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    const g = c.getContext('2d'); g.drawImage(img, 0, 0);
    return g.getImageData(0, 0, c.width, c.height).data; });
  const diff = (a, b) => {
    if (a.length !== b.length) return a.length + b.length;
    let n = 0;
    for (let i = 0; i < a.length; i += 4)
      if (a[i] !== b[i] || a[i + 1] !== b[i + 1] || a[i + 2] !== b[i + 2]) n++;
    return n; };
  const scores = pixels.map((a, i) =>
    pixels.reduce((s, b, j) => i === j ? s : s + diff(a, b), 0));
  return hashes[scores.indexOf(Math.max(...scores))];
}
"""

_RELEASE_LABEL_RE = re.compile(r":\s*([^|]+)")
_PACKAGE_SIZE_RE = re.compile(r"Package:\s*~?\s*([\d.,]+\s*[KMGT]i?B)", re.IGNORECASE)
_SIZE_RE = re.compile(r"([\d.,]+\s*[KMGT]i?B)", re.IGNORECASE)
_MEDIA_PATH_RE = re.compile(r"^/media/([^/]+)/?$")
_QUALITY_BY_WIDTH: tuple[tuple[int, str], ...] = (
    (3800, "2160p"),
    (1900, "1080p"),
    (1260, "720p"),
    (0, "480p"),
)


def _quality(release: dict[str, Any]) -> str:
    """ "1080p WebRip" from resolution (or the pill label) and release notes.

    The width decides (crops like 1920x800 are still 1080p), mapped to the
    standard names Arr apps recognise.
    """
    match = re.match(r"(\d{3,4})x\d{3,4}$", str(release.get("resolution", "")).strip())
    if match:
        width = int(match.group(1))
        res = next(name for w, name in _QUALITY_BY_WIDTH if width >= w)
    else:
        label = _RELEASE_LABEL_RE.search(str(release.get("label", "")))
        res = label.group(1).strip() if label else ""
    return " ".join(p for p in (res, str(release.get("notes", "")).strip()) if p)


def _release_size(text: str) -> str | None:
    """Package size of the whole release ("Ø 742 MB (Package: ~8.70 GB)")."""
    match = _PACKAGE_SIZE_RE.search(text) or _SIZE_RE.search(text)
    return match.group(1) if match else None


def _enc(slug: str, release_id: int, episode: int | str) -> str:
    """Request token of the links endpoint (episode index or "cnl" = all)."""
    payload = json.dumps(["media", slug, "downloads", release_id, episode])
    return base64.b64encode(payload.replace(" ", "").encode()).decode()


def _links_from_content(content: object) -> list[str]:
    """Decrypt the Click'n'Load package of every hoster in a success answer."""
    items = list(content.values()) if isinstance(content, dict) else content
    links: list[str] = []
    for item in items if isinstance(items, list) else []:
        cnl = item.get("cnl") if isinstance(item, dict) else None
        if isinstance(cnl, dict) and cnl.get("jk") and cnl.get("crypted"):
            links.extend(decrypt_cnl(str(cnl["jk"]), str(cnl["crypted"])))
    return links


class AnimeLoadsPlugin(PlaywrightPluginBase):
    """Python plugin for anime-loads.org using Playwright (DDoS-Guard bypass)."""

    name = "animeloads"
    version = "1.1.0"
    mode = "playwright"
    # Not for Stremio: a series' preview embed is no episode stream, and no
    # resolver plays the site's embeds
    provides = "download"
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
            await self._remember_clearance(page)  # __ddg* cookies
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
        q = quote_plus(query)
        if page_num == 1:
            url = f"{self.base_url}/search?q={q}"
        else:
            url = f"{self.base_url}/search/page/{page_num}?q={q}"

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
            results = await self._search_all_pages(page, query, effective_category)
        finally:
            await page.close()
        if results:
            results = await self._expand_releases(results)
        return results[: self.effective_max_results]

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

    # ------------------------------------------------------------------
    # Releases (Torznab: one result per release)
    # ------------------------------------------------------------------

    async def _media_releases(self, media_url: str) -> list[dict[str, Any]]:
        """Load a media page and read its releases (``[]`` on failure)."""
        page = await self._new_page()
        try:
            await page.goto(media_url, wait_until="domcontentloaded")
            if not await self._wait_for_ddos_guard(page):
                return []
            releases = await page.evaluate(_EXTRACT_RELEASES_JS)
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "animeloads_releases_failed", url=media_url, error=str(exc)
            )
            return []
        finally:
            await page.close()
        return [r for r in releases or [] if isinstance(r, dict)]

    async def _expand_releases(self, results: list[SearchResult]) -> list[SearchResult]:
        """Replace the first series results by one result per release.

        A series whose media page fails or lists no release stays as is.
        """
        head = results[:_MAX_EXPANDED_SERIES]
        sem = asyncio.Semaphore(_EXPAND_CONCURRENCY)

        async def _one(series: SearchResult) -> list[SearchResult]:
            if not series.source_url:
                return [series]
            async with sem:
                releases = await self._media_releases(series.source_url)
            return [self._release_result(series, r) for r in releases] or [series]

        expanded = await asyncio.gather(*(_one(s) for s in head))
        out = [r for group in expanded for r in group]
        self._log.info("animeloads_releases", series=len(head), results_count=len(out))
        return out + results[_MAX_EXPANDED_SERIES:]

    def _release_result(
        self, series: SearchResult, release: dict[str, Any]
    ) -> SearchResult:
        """One Torznab result for a release of *series*."""
        link = f"{series.source_url}?release={release['id']}"
        langs = ", ".join(release.get("languages") or [])
        subs = ", ".join(release.get("subtitles") or [])
        audio = " | ".join(p for p in (langs, f"Sub: {subs}" if subs else "") if p)
        parts = [_quality(release), audio, str(release.get("group", "")).strip()]
        title = series.title + "".join(f" [{p}]" for p in parts if p)

        metadata = {
            **series.metadata,
            "release_group": str(release.get("group", "")).strip(),
            "release_episodes": str(release.get("episodes", "")),
        }
        password = str(release.get("password", "")).strip()
        if password:
            metadata["archive_password"] = password

        return SearchResult(
            title=title,
            download_link=link,
            validated_links=[link],  # Pre-validated: DDoS-Guard blocks httpx
            source_url=series.source_url,
            size=_release_size(str(release.get("size", ""))),
            published_date=series.published_date,
            category=series.category,
            description=series.description,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Grab-time links (GrabResolvingPlugin)
    # ------------------------------------------------------------------

    async def resolve_download(self, url: str) -> list[str]:
        """Resolve a release URL (``/media/<slug>?release=<n>``) to hoster links.

        Series-level URLs (results that could not be expanded) are returned
        unchanged. All episodes or nothing: a partial
        season would break the grab.
        """
        parts = urlsplit(url)
        match = _MEDIA_PATH_RE.match(parts.path)
        release_param = parse_qs(parts.query).get("release", [""])[0]
        if match is None or not release_param.isdigit():
            return [url]
        slug, release_id = match.group(1), int(release_param)
        media_url = f"{parts.scheme}://{parts.netloc}{parts.path}"

        page = await self._new_page()
        try:
            await page.goto(media_url, wait_until="domcontentloaded")
            if not await self._wait_for_ddos_guard(page):
                return []
            await self._remember_clearance(page)
            releases = await page.evaluate(_EXTRACT_RELEASES_JS)
            release = next(
                (r for r in releases or [] if r.get("id") == release_id), None
            )
            if release is None:
                self._log.warning("animeloads_release_missing", url=url)
                return []
            return await self._release_links(page, slug, release_id, release)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("animeloads_resolve_failed", url=url, error=str(exc))
            return []
        finally:
            await page.close()

    async def _release_links(
        self, page: Page, slug: str, release_id: int, release: dict[str, Any]
    ) -> list[str]:
        if await self._login(page):
            links = await self._request_links(page, _enc(slug, release_id, "cnl"))
            if links is not None:
                return links

        episodes = int(release.get("episodes") or 0)
        if not 0 < episodes <= _MAX_ANON_EPISODES:
            self._log.warning(
                "animeloads_release_too_long",
                episodes=episodes,
                limit=_MAX_ANON_EPISODES,
                hint=f"set {_USERNAME_ENV}/{_PASSWORD_ENV} for whole releases",
            )
            return []
        links: list[str] = []
        for episode in range(episodes):
            if episode:
                await asyncio.sleep(_EPISODE_PAUSE_S)
            found = await self._request_links(page, _enc(slug, release_id, episode))
            if not found:
                return []
            links.extend(found)
        return links

    async def _ajax(self, page: Page, url: str, data: dict[str, Any]) -> str:
        return await page.evaluate(_AJAX_JS, [url, data], isolated_context=False)

    async def _login(self, page: Page) -> bool:
        """Sign in with the configured account (``False`` without one)."""
        username = os.environ.get(_USERNAME_ENV, "")
        password = os.environ.get(_PASSWORD_ENV, "")
        if not username or not password:
            return False
        if not await self._logged_in(page):
            await self._ajax(
                page,
                "/auth/signin",
                {"identity": username, "password": password, "remember": 1},
            )
        if await self._logged_in(page):
            return True
        self._log.warning("animeloads_login_failed", username=username)
        return False

    @staticmethod
    async def _logged_in(page: Page) -> bool:
        cookies = await page.context.cookies()
        return any("username" in unquote(str(c.get("value", ""))) for c in cookies)

    async def _request_links(self, page: Page, enc: str) -> list[str] | None:
        """Links for one request token; solves the captcha when asked to.

        ``None`` when the site refuses (rate limit, login needed, captcha
        not solved).
        """
        answer = await self._ajax(
            page, "/ajax/captcha", {"enc": enc, "response": "nocaptcha"}
        )
        for _ in range(_CAPTCHA_ATTEMPTS):
            data = json.loads(answer) if answer else {}
            if data.get("code") == "success":
                return _links_from_content(data.get("content"))
            if data.get("message") != "noadblock":
                self._log.warning(
                    "animeloads_links_refused", message=data.get("message")
                )
                return None
            answer = await self._solve_captcha(page, enc)
        self._log.warning("animeloads_captcha_unsolved", attempts=_CAPTCHA_ATTEMPTS)
        return None

    async def _solve_captcha(self, page: Page, enc: str) -> str:
        """One captcha round: pick the odd image, verify, request the links.

        Returns the links endpoint's answer, or the ``noadblock`` answer
        when the pick was rejected (the caller retries).
        """
        picked = await page.evaluate(_CAPTCHA_PICK_JS, isolated_context=False)
        await asyncio.sleep(_STEP_PAUSE_S)
        if picked:
            verified = await self._ajax(
                page, "/files/captcha", {"cID": 0, "pC": picked, "rT": 2}
            )
            if verified == "1":
                await asyncio.sleep(_STEP_PAUSE_S)
                return await self._ajax(
                    page,
                    "/ajax/captcha",
                    {
                        "enc": enc,
                        "response": "captcha",
                        "captcha-idhf": 0,
                        "captcha-hf": picked,
                    },
                )
        self._log.info("animeloads_captcha_rejected")
        await asyncio.sleep(_REJECT_PAUSE_S)
        return json.dumps({"code": "error", "message": "noadblock"})


plugin = AnimeLoadsPlugin()
