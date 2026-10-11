"""filmo.to Python plugin for Scavengarr.

filmo.to lists films (no series) with streams on VOE, Byse and others, a
German and an English row per film. The site is a Laravel app:
- GET /search?q={query}: the best matches as cards, more as a poster grid
- GET /movies/{slug}: title, metadata (``dl.ft-meta-definition-list``),
  the stream rows (``div.provider-row``: language, chips per hoster)
- a chip carries an encrypted payload (``data-p``, new on every page view);
  POST /n {"p": payload} with the page's CSRF token and the session's
  XSRF-TOKEN cookie answers a one-time token, GET /n/{token} the hoster:
  a redirect (VOE) or an interstitial page whose ``a.open`` names it (Byse)

The results carry the hoster URLs, never /n/ tokens (they expire). A
refused POST /n (419: the session's token expired) fetches the page once
more, for a new token and new payloads.

Domain: filmo.to
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus, unquote, urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import served_category
from scavengarr.infrastructure.plugins.dom import parse_page
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.relevance import relevant_hits

_DOMAINS = ["filmo.to"]
_MOVIES = 2000
_HEADERS = {"Accept-Language": "de-DE,de;q=0.9"}
# The site's row labels → the language the link converter reads
_LANGUAGES = {"deutsch": "German", "english": "English"}
_QUALITY_RE = re.compile(r"^\d{3,4}p$", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


class _Chip:
    """One hoster of a stream row (a plain class: the plugin loader keeps
    plugin modules out of ``sys.modules``, which ``@dataclass`` needs)."""

    __slots__ = ("hoster", "language", "link_id", "payload", "quality")

    def __init__(
        self, *, language: str, hoster: str, link_id: str, quality: str, payload: str
    ) -> None:
        self.language = language
        self.hoster = hoster
        self.link_id = link_id
        self.quality = quality
        self.payload = payload


class _SearchResultParser:
    """The search page's films: the cards first, then the poster grid."""

    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        seen: set[str] = set()
        cards = [
            (a, a.css_first("h4.popular-spotlight-card__title"))
            for a in tree.css(
                "section.search-top-results a.popular-spotlight-card__link"
            )
        ]
        grid = [
            (a, a.css_first("div.movie-poster-grid-card__title"))
            for a in tree.css("a.movie-poster-grid-card")
        ]
        for link, title_node in cards + grid:
            href = link.attributes.get("href") or ""
            title = " ".join((title_node.text() if title_node else "").split())
            url = urljoin(f"{self._base_url}/", href)
            if not href or not title or url in seen:
                continue
            seen.add(url)
            self.results.append({"title": title, "detail_url": url})


class _DetailPageParser:
    """A film's page: title, year, genres, CSRF token and the stream chips."""

    def __init__(self) -> None:
        self.title = ""
        self.year: int | None = None
        self.genres: list[str] = []
        self.csrf_token = ""
        self.chips: list[_Chip] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        h1 = tree.css_first("h1")
        self.title = " ".join(h1.text().split()) if h1 else ""
        meta = tree.css_first("meta[name=csrf-token]")
        self.csrf_token = (meta.attributes.get("content") or "") if meta else ""
        for dl in tree.css("dl.ft-meta-definition-list"):
            label = dl.css_first("dt")
            value = dl.css_first("dd")
            if label is None or value is None:
                continue
            name = " ".join(label.text().split())
            text = " ".join(value.text().split())
            if name == "Erscheinungsdatum":
                year = _YEAR_RE.search(text)
                self.year = int(year.group(0)) if year else None
            elif name == "Genres":
                self.genres = [g.strip() for g in text.split(",") if g.strip()]
        for row in tree.css("div.provider-rows > div.provider-row"):
            lang = row.css_first("span.provider-row__lang")
            language = " ".join(lang.text().split()) if lang else ""
            for chip in row.css("[data-provider-chip]"):
                attrs = chip.attributes
                payload = attrs.get("data-p") or ""
                hoster = attrs.get("aria-label") or ""
                if not payload or not hoster:
                    continue
                tags = [
                    " ".join(t.text().split())
                    for t in chip.css("span.provider-chip__metadata-tag")
                ]
                quality = next((t for t in tags if _QUALITY_RE.match(t)), "")
                self.chips.append(
                    _Chip(
                        language=language,
                        hoster=hoster,
                        link_id=attrs.get("data-movie-link-id") or "",
                        quality=quality,
                        payload=payload,
                    )
                )


def interstitial_link(html: str) -> str | None:
    """The hoster URL an interstitial page (``a.open``) links to."""
    link = LexborHTMLParser(html).css_first("a.open[href]")
    href = (link.attributes.get("href") or "") if link else ""
    return href if href.startswith("http") else None


class FilmoPlugin(HttpxPluginBase):
    """Python plugin for filmo.to (HTML pages and the /n link chain)."""

    name = "filmo"
    version = "1.0.0"
    provides = "stream"
    languages = ["de", "en"]  # noqa: RUF012

    _domains = _DOMAINS

    def _site_cookie(self, name: str) -> str | None:
        """The value of the site's cookie *name* in the HTTP client's jar."""
        if self._client is None:
            return None
        host = urlparse(self.base_url).hostname or ""
        for cookie in self._client.cookies.jar:
            if cookie.name == name and cookie.domain.lstrip(".") == host:
                return cookie.value
        return None

    async def _detail(self, url: str) -> _DetailPageParser | None:
        html = await self._fetch_text(url, context="detail", headers=_HEADERS)
        if html is None:
            return None
        return await parse_page(_DetailPageParser(), html)

    async def _link_token(self, payload: str, csrf: str, referer: str) -> str | None:
        """POST /n: the one-time token for a chip's *payload*."""
        headers = {
            **_HEADERS,
            "X-CSRF-TOKEN": csrf,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
            "Referer": referer,
        }
        xsrf = self._site_cookie("XSRF-TOKEN")
        if xsrf:
            headers["X-XSRF-TOKEN"] = unquote(xsrf)
        resp = await self._safe_fetch(
            f"{self.base_url}/n",
            method="POST",
            context="link_token",
            headers=headers,
            json={"p": payload},
        )
        if resp is None:
            return None
        data = self._safe_parse_json(resp, context="link_token")
        token = data.get("x") if isinstance(data, dict) else None
        return token if isinstance(token, str) and token else None

    async def _hoster_url(self, token: str, referer: str) -> str | None:
        """GET /n/{token}: the redirect's target or the interstitial's link."""
        client = await self._ensure_client()
        url = f"{self.base_url}/n/{quote_plus(token)}"
        kwargs = self._request_kwargs(client, url)
        headers = {**(kwargs.pop("headers", None) or {}), **_HEADERS}
        headers["Referer"] = referer
        try:
            resp = await client.get(
                url, follow_redirects=False, headers=headers, **kwargs
            )
        except Exception as exc:  # noqa: BLE001
            self._log.warning("filmo_link_error", error=str(exc))
            return None
        location = resp.headers.get("location") if resp.is_redirect else None
        if location:
            return urljoin(url, location)
        if resp.status_code == 200:
            return interstitial_link(resp.text)
        self._log.warning("filmo_link_status", status=resp.status_code)
        return None

    async def _resolve_chip(
        self, chip: _Chip, csrf: str, referer: str, sem: asyncio.Semaphore
    ) -> str | None:
        async with sem:
            token = await self._link_token(chip.payload, csrf, referer)
            if token is None:
                return None
            return await self._hoster_url(token, referer)

    async def _resolve_chips(
        self, page: _DetailPageParser, url: str, sem: asyncio.Semaphore
    ) -> list[tuple[_Chip, str]]:
        """Each chip with its hoster URL; refused chips get one fresh page."""

        async def _all(p: _DetailPageParser, chips: list[_Chip]) -> list[str | None]:
            return list(
                await asyncio.gather(
                    *(self._resolve_chip(c, p.csrf_token, url, sem) for c in chips)
                )
            )

        chips = page.chips
        targets = await _all(page, chips)
        failed = [i for i, t in enumerate(targets) if t is None]
        if failed:
            fresh = await self._detail(url)
            # The new page's chips in the same places (payloads change)
            if fresh is not None and len(fresh.chips) == len(chips):
                retry = [fresh.chips[i] for i in failed]
                for i, target in zip(failed, await _all(fresh, retry), strict=True):
                    targets[i] = target
            for i in failed:
                if targets[i] is None:
                    self._log.warning(
                        "filmo_chip_skipped",
                        hoster=chips[i].hoster,
                        language=chips[i].language,
                    )
        return [(c, t) for c, t in zip(chips, targets, strict=True) if t]

    def _links(self, resolved: list[tuple[_Chip, str]]) -> list[dict[str, str]]:
        """German rows first; a hoster URL once (a file in both rows)."""
        ordered = sorted(
            resolved, key=lambda ct: _LANGUAGES.get(ct[0].language.lower()) != "German"
        )
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        for chip, target in ordered:
            if target in seen:
                continue
            seen.add(target)
            links.append(
                {
                    "hoster": chip.hoster,
                    "link": target,
                    "language": _LANGUAGES.get(chip.language.lower(), chip.language),
                    "quality": chip.quality,
                }
            )
        return links

    async def _process(
        self, hit: dict[str, str], sem: asyncio.Semaphore, link_sem: asyncio.Semaphore
    ) -> SearchResult | None:
        url = hit["detail_url"]
        async with sem:
            page = await self._detail(url)
        if page is None:
            return None
        if not page.chips:
            self._log.debug("filmo_no_streams", url=url)
            return None
        links = self._links(await self._resolve_chips(page, url, link_sem))
        if not links:
            return None
        return SearchResult(
            title=page.title or hit["title"],
            download_link=links[0]["link"],
            download_links=links,
            source_url=url,
            published_date=str(page.year) if page.year else None,
            category=_MOVIES,
            metadata={"year": page.year, "genres": ", ".join(page.genres)},
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search filmo.to; films only (a series request gets nothing)."""
        if not query or season is not None:
            return []
        if category is not None and served_category(category, [_MOVIES]) is None:
            return []

        await self._verify_domain()
        html = await self._fetch_text(
            f"{self.base_url}/search?q={quote_plus(query)}",
            context="search",
            headers=_HEADERS,
        )
        if html is None:
            return []
        parser = await parse_page(_SearchResultParser(self.base_url), html)
        hits = relevant_hits(parser.results, query, lambda h: h["title"])
        if not hits:
            return []

        sem = self._new_semaphore()
        link_sem = self._new_semaphore()
        found = await asyncio.gather(*(self._process(h, sem, link_sem) for h in hits))
        results = [r for r in found if r is not None]
        return results[: self.effective_max_results]


plugin = FilmoPlugin()
