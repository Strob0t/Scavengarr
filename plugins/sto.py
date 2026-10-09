"""s.to (SerienStream) Python plugin for Scavengarr.

Scrapes s.to (German TV series streaming site) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- Search via /suche?term={query} with pagination (&page=N)
- Series detail pages at /serie/{slug} for seasons/episodes
- Episode pages at /serie/{slug}/staffel-{n}/episode-{n} for hoster buttons
- Hoster redirect resolution via /r?t={token} → 302 to actual hoster URL
- A Stremio request's episode of an anime (5070) located on the site's own
  season pages (``locates_episodes``: the index of ``episode_index.py``)
- Bounded concurrency for series and episode detail scraping

Multi-domain support with automatic fallback (s.to, serienstream.to, 186.2.175.5).
TV-only site: all results use Torznab category 5000+.
No authentication required.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import replace
from urllib.parse import urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.entities.stremio import EpisodeRef
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.browser_fetcher import BrowserFetcherPort
from scavengarr.infrastructure.plugins.categories import (
    filter_by_category,
    served_category,
)
from scavengarr.infrastructure.plugins.dom import parse_page
from scavengarr.infrastructure.plugins.episode_index import (
    Located,
    RowSelectors,
    episode_index,
    locate,
)
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.relevance import (
    SINGLE_TITLE_HITS,
    hit_title,
    relevant_hits,
)

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
# s.to is gone (NXDOMAIN; dead in JDownloader's SerienStreamTo too)
_DOMAINS = ["serienstream.to", "186.2.175.5"]
_MAX_PAGES = 42  # 24 results/page → 42 pages for ~1000
_RESULTS_PER_PAGE = 24
# Hoster buttons of an episode page; each opens a /r?t= link-out
_LINK_BOX = "button.link-box[data-play-url]"
# Provider name of the button that links to the series' streaming service
_OFFICIAL_PROVIDER = "Provider"
# The browser's time to pass the link-out gate: Turnstile took ~6 s at home,
# 28 s on a Raspberry Pi 4 behind a VPN (2026-10-04). The pass runs in the
# background, a Stremio request does not wait for it
_GATE_TIMEOUT_S = 60.0
# After a failed pass, link-outs go without the browser for this long; after
# gated link-outs, whole seasons do not request theirs for this long
_GATE_RETRY_S = 300.0

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Site genre name (lowercase) → Torznab TV sub-category. Only anime and
# documentaries have one; the other TV children are qualities (5030 SD,
# 5040 HD, ...), which genres do not tell.
_GENRE_CATEGORY_MAP: dict[str, int] = {
    "anime": 5070,
    "animation": 5070,
    "zeichentrick": 5070,
    "dokumentation": 5080,
    "documentary": 5080,
    "doku-soap": 5080,
}
# The labels of this site's results
_CATEGORIES = (5000, 5070, 5080)
# Season link of a series page: /serie/{slug}/staffel-{n}
_SEASON_RE = re.compile(r"/staffel-(\d+)")
_EPISODE_NUMBER_RE = re.compile(r"^\d+$")
# Images of an episode row that are no hoster icon
_NOT_HOSTER_ICONS = frozenset({"flag", "poster", "cover"})
# A season page's episode rows: the number in the first cell, the German
# title in <strong>, the English one in <span>; an anime's span holds the
# absolute number in place of a title ("Episode 062")
_ROW_SELECTORS = RowSelectors(
    row="tr.episode-row",
    number="th.episode-number-cell",
    german="strong.episode-title-ger",
    english="span.episode-title-eng",
)
# The anime label; only such results are located on the site's seasons
_ANIME = 5070


def _genre_to_torznab(genre: str) -> int:
    """Map s.to genre name to Torznab TV sub-category."""
    key = genre.lower().strip()
    return _GENRE_CATEGORY_MAP.get(key, 5000)


def _determine_category(genres: list[str]) -> int:
    """Torznab category of a series from its genres (5000 by default)."""
    for genre in genres:
        mapped = _genre_to_torznab(genre)
        if mapped != 5000:
            return mapped
    return 5000


def _episode_title(series: str, season: int, episode: int, episode_title: str) -> str:
    """The result title: ``Series - S01E02 - Episode title``."""
    title = f"{series} - S{season:02d}E{episode:02d}"
    if episode_title:
        title += f" - {episode_title}"
    return title


def _relevant_series(
    series: list[dict[str, str]], query: str, *, limit: int | None = None
) -> list[dict[str, str]]:
    """The series worth scraping for *query*, each once, closest first.

    The site's search also lists unrelated series ("Breaking Bad" finds
    "Better Call Saul"); scraping each one costs a detail page, an episode
    page and its link-outs, and bursts of link-outs make the site gate them
    behind Turnstile (``relevant_hits``). The result page links a series
    from its card and from its episode hits.
    """
    by_key: dict[str, dict[str, str]] = {}
    for entry in series:
        key = entry.get("slug") or entry.get("url") or entry.get("title", "")
        by_key.setdefault(key, entry)
    return relevant_hits(list(by_key.values()), query, hit_title, limit=limit)


class _SearchSeriesParser:
    """Parse the series cards of an s.to search results page (selectolax).

    A card nests one ``/serie/{slug}`` anchor in another and names the
    series in the ``h6.show-title`` after the inner one::

        <a href="/serie/{slug}"><div class="card">
          <a href="/serie/{slug}" class="show-cover">...</a>
          <h6 class="show-title" title="Series Title">Series Title</h6>
        </div></a>

    A title belongs to the last ``/serie/`` anchor before its end. The
    episode hits further down (``h6.small`` in the "Episoden" section)
    belong to other series whose episode titles contain the term and are
    skipped. The page renders its results twice (two layouts), so each
    series is kept once.
    """

    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        href = ""
        seen: set[str] = set()
        tree = LexborHTMLParser(html)
        for node in tree.css("a[href*='/serie/'], h6.show-title"):
            if node.tag == "a":
                href = node.attributes.get("href") or ""
                continue
            # An anchor inside the title comes before the title's end
            inner = node.css("a[href*='/serie/']")
            if inner:
                href = inner[-1].attributes.get("href") or ""
            title = node.text().strip()
            url = urljoin(self._base_url, href)
            if not title or not href or url in seen:
                continue
            seen.add(url)
            self.results.append(
                {"title": title, "url": url, "slug": href.rstrip("/").split("/")[-1]}
            )


class _SeriesDetailParser:
    """Parse s.to series detail page for genres, seasons, and episodes.

    Page structure (selectolax):
    - ``<h1>Series Title</h1>``
    - Genre links: ``<a href="/genre/{name}">Genre</a>``
    - Season nav: ``<a href="/serie/{slug}/staffel-{n}">Staffel {n}</a>``
    - Episode table with rows containing:
      - ``<th>`` with episode number
      - ``<strong>`` with German title
      - ``<img alt="Hoster">`` for hoster icons
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self.title = ""
        self.genres: list[str] = []
        self.seasons: list[int] = []
        # Episode data for the currently displayed season
        self.episodes: list[dict[str, str]] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for h1 in tree.css("h1"):
            self.title = h1.text().strip()
        for link in tree.css("a[href*='/genre/']"):
            genre = link.text().strip()
            if genre and genre not in self.genres:
                self.genres.append(genre)
        for link in tree.css("a[href*='/staffel-']"):
            season = _SEASON_RE.search(link.attributes.get("href") or "")
            if season and int(season.group(1)) not in self.seasons:
                self.seasons.append(int(season.group(1)))
        for row in tree.css("tr"):
            self._add_episode(row)

    def _add_episode(self, row: LexborNode) -> None:
        number = ""
        for th in row.css("th"):
            text = th.text().strip()
            if _EPISODE_NUMBER_RE.match(text):
                number = text
        if not number:
            return
        de_title = next(
            (text for strong in row.css("strong") if (text := strong.text().strip())),
            "",
        )
        hosters = [
            alt
            for img in row.css("img[alt]")
            if (alt := img.attributes.get("alt") or "")
            and alt.lower() not in _NOT_HOSTER_ICONS
        ]
        self.episodes.append(
            {
                "number": number,
                "de_title": de_title,
                "en_title": "",
                "hosters": ",".join(hosters),
            }
        )


class _EpisodeHosterParser:
    """Parse s.to episode page for hoster buttons (selectolax).

    Episode pages contain hoster buttons grouped by language::

        <h5>Deutsch</h5>
        <button class="link-box ..."
                data-play-url="/r?t={token}"
                data-provider-name="VOE"
                data-language-label="Deutsch"
                data-language-id="1">
          ...
        </button>
    """

    def __init__(self) -> None:
        self.hosters: list[dict[str, str]] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for button in tree.css("button[data-play-url], a[data-play-url]"):
            attrs = button.attributes
            play_url = attrs.get("data-play-url") or ""
            provider = attrs.get("data-provider-name") or ""
            # "Anbieter" (Provider) links to the streaming service that owns
            # the series, no hoster to play
            if play_url and provider and provider != _OFFICIAL_PROVIDER:
                self.hosters.append(
                    {
                        "play_url": play_url,
                        "provider": provider,
                        "language": attrs.get("data-language-label") or "",
                    }
                )


def _episode_number(
    ep: dict[str, str | list[dict[str, str]]],
) -> int | None:
    """Extract the episode number from a scraped episode dict."""
    url = str(ep.get("url", ""))
    m = re.search(r"/episode-(\d+)", url)
    return int(m.group(1)) if m else None


class StoPlugin(HttpxPluginBase):
    """Python plugin for s.to (SerienStream) using httpx.

    Supports multiple domains with automatic fallback:
    s.to, serienstream.to, 186.2.175.5.
    """

    name = "sto"
    provides = "stream"
    _domains = _DOMAINS

    # A Stremio request's episode of an anime is located on the site's
    # season pages (the Staffeln are the site's own, not IMDb's seasons)
    locates_episodes = True

    def __init__(self) -> None:
        super().__init__()
        self._gate_task: asyncio.Task[bool] | None = None
        self._gate_failed_at = -_GATE_RETRY_S
        # When link-outs last stayed gated (see _episode_links)
        self._gated_at = -_GATE_RETRY_S

    async def _search_series(
        self,
        query: str,
        page_num: int = 1,
    ) -> list[dict[str, str]]:
        """Fetch one search page and return parsed series entries."""
        params = {"term": query}
        if page_num > 1:
            params["page"] = str(page_num)

        html = await self._fetch_text(
            f"{self.base_url}/suche", params=params, context="search"
        )
        if html is None:
            return []

        parser = await parse_page(_SearchSeriesParser(self.base_url), html)

        self._log.info(
            "sto_search_page",
            query=query,
            page=page_num,
            count=len(parser.results),
        )
        return parser.results

    async def _scrape_series_detail(
        self,
        series: dict[str, str],
    ) -> _SeriesDetailParser:
        """Fetch series detail page and return parsed data."""
        html = await self._fetch_text(series["url"], context="detail")
        if html is None:
            return _SeriesDetailParser(self.base_url)

        parser = await parse_page(_SeriesDetailParser(self.base_url), html)
        return parser

    async def _scrape_episode_hosters(
        self,
        episode_url: str,
    ) -> list[dict[str, str]]:
        """Fetch episode page and return hoster button data."""
        html = await self._fetch_text(episode_url, context="episode")
        if html is None:
            return []

        parser = await parse_page(_EpisodeHosterParser(), html)
        return parser.hosters

    async def _resolve_hoster_url(self, play_url: str, *, referer: str) -> str:
        """Resolve a /r?t={token} link-out to the hoster URL.

        The site redirects a link-out only when it is opened from its episode
        page (*referer*) or with that page's session; otherwise it answers
        with a page that works only inside its player iframe. Falls back to
        the link-out itself when it does not resolve (JDownloader can still
        follow it).
        """
        full_url = urljoin(self.base_url, play_url)
        target = await self._resolve_redirect(
            full_url, context="hoster", referer=referer
        )
        return target or full_url

    async def _read_episode_links(
        self, episode_url: str, *, resolve: bool = True
    ) -> list[dict[str, str]]:
        """Hoster links of an episode page, link-outs resolved in parallel.

        Without *resolve* the link-outs themselves are returned.
        """
        hosters = await self._scrape_episode_hosters(episode_url)
        if resolve:
            resolved = await asyncio.gather(
                *(
                    self._resolve_hoster_url(h["play_url"], referer=episode_url)
                    for h in hosters
                )
            )
        else:
            resolved = [urljoin(self.base_url, h["play_url"]) for h in hosters]
        return [
            {
                "hoster": h["provider"].lower(),
                "link": url,
                "language": h.get("language", ""),
            }
            for h, url in zip(hosters, resolved, strict=True)
        ]

    async def _episode_links(
        self, episode_url: str, *, pass_gate: bool = True
    ) -> list[dict[str, str]]:
        """Hoster links of an episode page.

        When no link-out of the page resolves, the site gates them. For one
        episode (*pass_gate*, a stream request) the browser passes the gate
        once and the page is read again, its link-outs now minted for the
        adopted session. For whole seasons (Torznab) a pass does not help:
        it unlocks 3 link-outs of the ~950 of a search (VPN IP, 2026-10-04).
        Gated link-outs stay unresolved (JDownloader can still follow them),
        and for ``_GATE_RETRY_S`` seasons do not request theirs at all.
        """
        if not pass_gate and time.monotonic() - self._gated_at < _GATE_RETRY_S:
            return await self._read_episode_links(episode_url, resolve=False)
        links = await self._read_episode_links(episode_url)
        if self._gated(links) and pass_gate and await self._pass_link_gate(episode_url):
            links = await self._read_episode_links(episode_url)
        if self._gated(links):
            self._gated_at = time.monotonic()
        return links

    def _gated(self, links: list[dict[str, str]]) -> bool:
        """Whether no link-out resolved: all links stay on the site."""
        site = urlparse(self.base_url).hostname
        return bool(links) and all(
            urlparse(link["link"]).hostname == site for link in links
        )

    async def _pass_link_gate(self, episode_url: str) -> bool:
        """Let the browser pass the link-out gate and adopt its session.

        After bursts of link-outs from one IP the site answers them for every
        new session with a Turnstile widget in its player iframe; a session
        that passed the widget once gets redirects again, also through httpx
        with its cookies. The pass runs as a task of its own: a search cut by
        the Stremio deadline does not cancel it (the next request profits),
        and concurrent episodes wait for the same pass. A failed pass is not
        retried for ``_GATE_RETRY_S``.
        """
        fetcher = self._browser_fetcher
        if fetcher is None:
            return False
        if self._gate_task is None or self._gate_task.done():
            if time.monotonic() - self._gate_failed_at < _GATE_RETRY_S:
                return False
            self._gate_task = asyncio.create_task(
                self._run_gate_pass(fetcher, episode_url)
            )
        return await asyncio.shield(self._gate_task)

    async def _run_gate_pass(
        self, fetcher: BrowserFetcherPort, episode_url: str
    ) -> bool:
        passed = await fetcher.click_through(
            episode_url, _LINK_BOX, timeout=_GATE_TIMEOUT_S
        )
        if passed is None:
            self._gate_failed_at = time.monotonic()
            self._log.warning("sto_link_gate_unsolved", url=episode_url)
            return False
        await self._use_browser_session(episode_url, passed.cookies)
        self._log.info("sto_link_gate_passed", url=episode_url)
        return True

    async def _scrape_season_episodes(
        self,
        slug: str,
        season_num: int,
        detail: _SeriesDetailParser,
    ) -> list[dict[str, str | list[dict[str, str]]]]:
        """Scrape all episodes in a season for hoster links.

        Returns one entry per episode with title and resolved hoster URLs.
        """
        sem = self._new_semaphore()

        # Build episode URLs from the detail parser's episode list
        episode_urls: list[tuple[str, str]] = []
        for ep in detail.episodes:
            ep_num = ep["number"]
            ep_title = ep["de_title"] or ep["en_title"]
            url = f"{self.base_url}/serie/{slug}/staffel-{season_num}/episode-{ep_num}"
            episode_urls.append((url, ep_title))

        if not episode_urls:
            return []

        async def _fetch_episode(
            ep_url: str,
            ep_title: str,
        ) -> dict[str, str | list[dict[str, str]]] | None:
            async with sem:
                links = await self._episode_links(ep_url, pass_gate=False)
                if not links:
                    return None

                return {
                    "title": ep_title,
                    "url": ep_url,
                    "links": links,
                }

        gathered = await asyncio.gather(
            *[_fetch_episode(url, title) for url, title in episode_urls],
            return_exceptions=True,
        )

        results: list[dict[str, str | list[dict[str, str]]]] = []
        for item in gathered:
            if isinstance(item, dict):
                results.append(item)

        return results

    async def _paginate_search(self, query: str) -> list[dict[str, str]]:
        """Paginate through search pages to collect series entries."""
        all_series: list[dict[str, str]] = []
        for page_num in range(1, _MAX_PAGES + 1):
            series = await self._search_series(query, page_num)
            if not series:
                break
            all_series.extend(series)
            if len(series) < _RESULTS_PER_PAGE:
                break
            if len(all_series) >= self.effective_max_results:
                break
        return all_series[: self.effective_max_results]

    async def _fetch_all_details(
        self,
        all_series: list[dict[str, str]],
    ) -> list[tuple[dict[str, str], _SeriesDetailParser]]:
        """Scrape series detail pages with bounded concurrency."""
        sem = self._new_semaphore()

        async def _bounded(
            s: dict[str, str],
        ) -> tuple[dict[str, str], _SeriesDetailParser]:
            async with sem:
                detail = await self._scrape_series_detail(s)
                return s, detail

        gathered = await asyncio.gather(
            *[_bounded(s) for s in all_series],
            return_exceptions=True,
        )
        return [item for item in gathered if isinstance(item, tuple)]

    def _build_episode_result(
        self,
        ep: dict[str, str | list[dict[str, str]]],
        detail: _SeriesDetailParser,
        season: int,
        torznab_cat: int,
    ) -> SearchResult | None:
        """Convert a scraped episode dict into a SearchResult."""
        ep_title = str(ep.get("title", ""))
        ep_url = str(ep.get("url", ""))
        ep_links = ep.get("links", [])

        if not ep_links or not isinstance(ep_links, list):
            return None

        ep_num_match = re.search(r"/episode-(\d+)", ep_url)
        ep_num = ep_num_match.group(1) if ep_num_match else "0"
        full_title = _episode_title(detail.title, season, int(ep_num), ep_title)

        first_link = ep_links[0]
        download_link = (
            first_link["link"] if isinstance(first_link, dict) else str(first_link)
        )

        # ints, as the Stremio episode filter reads them; an episode the URL
        # does not name stays out
        metadata: dict[str, str | int] = {
            "series": detail.title,
            "season": season,
            "genres": ", ".join(detail.genres),
        }
        if ep_num_match:
            metadata["episode"] = int(ep_num)

        return SearchResult(
            title=full_title,
            download_link=download_link,
            download_links=ep_links,
            source_url=ep_url,
            category=torznab_cat,
            metadata=metadata,
        )

    async def _resolve_season_detail(
        self,
        slug: str,
        detail: _SeriesDetailParser,
        season: int | None,
    ) -> tuple[int, _SeriesDetailParser] | None:
        """Determine the target season and return it with its detail data.

        Returns ``None`` when the requested season is unavailable.
        """
        target = season if season is not None else detail.seasons[0]

        if season is not None and target not in detail.seasons:
            return None

        if target != detail.seasons[0]:
            url = f"{self.base_url}/serie/{slug}/staffel-{target}"
            season_detail = await self._scrape_series_detail({"url": url, "slug": slug})
            return target, season_detail

        return target, detail

    async def _scrape_single_episode(
        self,
        slug: str,
        season_num: int,
        episode_num: int,
        detail: _SeriesDetailParser,
        *,
        title: str | None = None,
    ) -> list[dict[str, str | list[dict[str, str]]]]:
        """Scrape a single episode directly by number instead of all episodes.

        Much faster than ``_scrape_season_episodes`` when the target episode
        is already known (e.g. Stremio stream requests). The episode's
        *title* when known (a located row), else the detail parser's list
        names it.
        """
        ep_url = (
            f"{self.base_url}/serie/{slug}/staffel-{season_num}/episode-{episode_num}"
        )
        ep_title = title or ""
        if title is None:
            for ep in detail.episodes:
                if ep["number"] == str(episode_num):
                    ep_title = ep["de_title"] or ep["en_title"]
                    break

        links = await self._episode_links(ep_url)
        # Only stream requests ask for one episode (Torznab passes no season
        # or episode): a link-out the gate kept on the site fails in the
        # serienstream resolver from this address, so only hoster links go.
        site = urlparse(self.base_url).hostname
        resolved = [link for link in links if urlparse(link["link"]).hostname != site]
        if len(resolved) < len(links):
            self._log.info(
                "sto_gated_links_dropped",
                plugin=self.name,
                count=len(links) - len(resolved),
                kept=len(resolved),
            )
        if not resolved:
            return []

        return [{"title": ep_title, "url": ep_url, "links": resolved}]

    async def _process_series(
        self,
        series_info: dict[str, str],
        detail: _SeriesDetailParser,
        category: int | None,
        season: int | None,
        episode: int | None,
        episode_ref: EpisodeRef | None = None,
    ) -> list[SearchResult]:
        """Process a single series into SearchResults.

        An anime's episode is located by *episode_ref* on the site's own
        season pages; every other series answers the request's numbers.
        """
        if not detail.seasons:
            return []

        slug = series_info.get("slug", "")
        if not slug:
            return []

        torznab_cat = _determine_category(detail.genres)
        if episode_ref is not None and torznab_cat == _ANIME:
            return await self._process_located(slug, detail, episode_ref)
        # A full-series search (no season, no episode) covers every season
        seasons: list[int | None] = (
            list(detail.seasons) if season is None and episode is None else [season]
        )

        results: list[SearchResult] = []
        for wanted in seasons:
            resolved = await self._resolve_season_detail(slug, detail, wanted)
            if resolved is None:
                continue
            target_season, season_detail = resolved

            if episode is not None:
                episodes = await self._scrape_single_episode(
                    slug, target_season, episode, season_detail
                )
            else:
                episodes = await self._scrape_season_episodes(
                    slug, target_season, season_detail
                )

            for ep in episodes:
                result = self._build_episode_result(
                    ep, detail, target_season, torznab_cat
                )
                if result:
                    results.append(result)
            if len(results) >= self.effective_max_results:
                break
        return results

    async def _process_located(
        self, slug: str, detail: _SeriesDetailParser, ref: EpisodeRef
    ) -> list[SearchResult]:
        """The located episode of an anime as a result: the row's page, the
        request's season and episode, and the site's as ``site_season`` and
        ``site_episode`` with the evidence as ``episode_located_by``; no row
        means no result."""
        located = await self._locate_episode(slug, detail.seasons, ref)
        if located is None:
            return []
        row = located.row
        episodes = await self._scrape_single_episode(
            slug, row.season, row.episode, detail, title=row.german
        )
        results: list[SearchResult] = []
        for ep in episodes:
            result = self._build_episode_result(ep, detail, row.season, _ANIME)
            if result is None:
                continue
            results.append(
                replace(
                    result,
                    title=_episode_title(
                        detail.title, ref.season, ref.episode, row.german
                    ),
                    metadata={
                        **result.metadata,
                        "season": ref.season,
                        "episode": ref.episode,
                        "site_season": row.season,
                        "site_episode": row.episode,
                        "episode_located_by": located.by,
                    },
                )
            )
        return results

    async def _locate_episode(
        self, slug: str, seasons: list[int], ref: EpisodeRef
    ) -> Located | None:
        """The row of the series' season pages *ref* means (the index is
        cached per series); ``None``, logged, when no row matches."""
        index = await episode_index(
            cache=self._cache,
            key=f"sto:episodes:v1:{slug}",
            seasons=seasons,
            season_url=lambda number: f"{self.base_url}/serie/{slug}/staffel-{number}",
            fetch_html=self._season_html,
            selectors=_ROW_SELECTORS,
            semaphore=self._new_semaphore(),
        )
        located = locate(index, ref)
        reference = {
            "slug": slug,
            "season": ref.season,
            "episode": ref.episode,
            "absolute": ref.absolute,
        }
        if located is None:
            self._log.info("sto_episode_not_located", rows=len(index), **reference)
            return None
        self._log.info(
            "sto_episode_located",
            site_season=located.row.season,
            site_episode=located.row.episode,
            located_by=located.by,
            **reference,
        )
        return located

    async def _season_html(self, url: str) -> str | None:
        return await self._fetch_text(url, context="season")

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
        *,
        episode_ref: EpisodeRef | None = None,
    ) -> list[SearchResult]:
        """Search s.to and return results with hoster links.

        Each episode becomes one SearchResult. Only TV categories are
        returned since s.to is a TV-only site.

        When *season* and *episode* are specified (typical for Stremio stream
        requests), only that specific episode is fetched per series — avoiding
        the expensive scrape of every episode in the season. With
        *episode_ref* an anime's episode is located on the site's own season
        pages instead of the request's numbers.
        """
        # s.to is TV-only — reject non-TV category requests early.
        if category is not None:
            category = served_category(category, _CATEGORIES)
            if category is None:
                return []
        await self._ensure_client()
        await self._verify_domain()

        all_series = _relevant_series(
            await self._paginate_search(query),
            query,
            limit=SINGLE_TITLE_HITS if season is not None else None,
        )
        if not all_series:
            return []

        detail_results = await self._fetch_all_details(all_series)

        # Series in parallel (bounded): one after another, a query matching
        # several series took several times as long
        sem = self._new_semaphore()

        async def _bounded(
            series_info: dict[str, str], detail: _SeriesDetailParser
        ) -> list[SearchResult]:
            async with sem:
                return await self._process_series(
                    series_info, detail, category, season, episode, episode_ref
                )

        gathered = await asyncio.gather(
            *(_bounded(info, detail) for info, detail in detail_results),
            return_exceptions=True,
        )

        search_results: list[SearchResult] = []
        for item in gathered:
            if isinstance(item, BaseException):
                self._log.warning("sto_series_failed", error=repr(item))
                continue
            search_results.extend(item)

        if category is not None:
            search_results = filter_by_category(search_results, category)
        return search_results[: self.effective_max_results]


plugin = StoPlugin()
