"""Tests for the filmo.to plugin (httpx, a Laravel site's link chain).

The HTML below is cut from the site's pages of 2026-10-11 (the search for
Interstellar, its detail page, Byse's interstitial); the real pages are
tested in ``test_real_pages.py``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "filmo.py"
_BASE = "https://filmo.to"
_DETAIL = f"{_BASE}/movies/interstellar"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("filmo_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()


def _card(slug: str, genre: str, title: str) -> str:
    return f"""
    <div class="col-6 col-lg-3">
      <article class="popular-spotlight-card rounded overflow-hidden h-100">
        <a href="{_BASE}/movies/{slug}" class="popular-spotlight-card__link d-block">
          <div class="label-container swiper-card-badge">{genre}</div>
          <h4 class="popular-spotlight-card__title mb-0">{title}</h4>
        </a>
      </article>
    </div>"""


def _grid(slug: str, title: str) -> str:
    return f"""
    <div class="col-6 col-md-3">
      <a href="{_BASE}/movies/{slug}" class="movie-poster-grid-card">
        <figure class="movie-poster-grid-card__figure">
          <img class="movie-poster-grid-card__img" alt="{title}">
        </figure>
        <div class="movie-poster-grid-card__title">{title}</div>
      </a>
    </div>"""


_SEARCH_HTML = f"""<html lang="de"><head>
<meta name="csrf-token" content="search-token"></head><body><main>
<section class="search-top-results pb-3"><div class="container-wrapper">
<div class="row">
{_card("interstellar", "Abenteuer", "Interstellar")}
{_card("supergirl", "Action", "Supergirl")}
{_card("the-beyond", "Science Fiction", "The Beyond")}
</div></div></section>
<div class="container-wrapper"><div class="row">
{_grid("interstellar", "Interstellar")}
{_grid("interstellar-wars", "Interstellar Wars")}
</div></div>
</main></body></html>"""


def _chip(hoster: str, link_id: str, p: str) -> str:
    return f"""
    <div class="label-container provider-chip" role="button" data-provider-chip=""
         data-movie-link-id="{link_id}" data-p="{p}" aria-label="{hoster}">
      <span class="provider-chip__name">{hoster}</span>
      <span class="provider-chip__metadata-tag">BluRay</span>
      <span class="provider-chip__metadata-tag">720p</span>
    </div>"""


def _row(lang: str, chips: str) -> str:
    return f"""
    <div class="provider-row">
      <div class="provider-row__head"><span class="provider-row__lang-group">
        <span class="provider-row__lang">{lang}</span></span></div>
      <div class="provider-row__chips">{chips}</div>
    </div>"""


def _meta(label: str, value: str) -> str:
    return f"""
    <dl class="mb-4 entry-wrapper ft-meta-definition-list">
      <dt class="entry-title my-2"><h3 class="section-headline">{label}</h3></dt>
      <dd class="entry-description">{value}</dd>
    </dl>"""


def _chips(lang: str, render: str) -> str:
    """VOE and Byse; the site gives both rows the same link ids."""
    return _chip("VOE", "2723", f"{lang}-voe-{render}") + _chip(
        "Byse", "10973", f"{lang}-byse-{render}"
    )


def _detail_html(csrf: str = "page-token", render: str = "1") -> str:
    """The detail page; *render* marks the payloads (new on every render)."""
    return f"""<html lang="de"><head>
<meta name="csrf-token" content="{csrf}"></head><body>
<h1 class="title">Interstellar</h1>
{_meta("Erscheinungsdatum", "2014-11-05")}
{_meta("Laufzeit", "169 Min.")}
{_meta("Genres", "Abenteuer, Drama, Science Fiction")}
<div class="provider-rows">
{_row("English", _chips("en", render))}
{_row("Deutsch", _chips("de", render))}
</div></body></html>"""


_BYSE_PAGE = """<html><body><div class="wrap"><div class="card">
<p class="lead">Dieses Video wird auf einer externen Seite gehostet.</p>
<a class="open" href="https://bysezejataos.com/d/6cko7pwix0pz" target="_blank">
  Hier klicken, um das Video in einem neuen Tab zu öffnen</a>
<span class="meta">Byse</span>
</div></div></body></html>"""

_VOE = {
    "en": "https://voe.sx/e/abc123?default_audio_language=en",
    "de": "https://voe.sx/e/abc123?default_audio_language=de",
}


class TestParsers:
    def test_search_lists_cards_and_grid_without_the_genre(self) -> None:
        parser = _mod._SearchResultParser(_BASE)
        parser.feed(_SEARCH_HTML)

        assert parser.results == [
            {"title": "Interstellar", "detail_url": _DETAIL},
            {"title": "Supergirl", "detail_url": f"{_BASE}/movies/supergirl"},
            {"title": "The Beyond", "detail_url": f"{_BASE}/movies/the-beyond"},
            {
                "title": "Interstellar Wars",
                "detail_url": f"{_BASE}/movies/interstellar-wars",
            },
        ]

    def test_detail_page_rows_chips_and_metadata(self) -> None:
        parser = _mod._DetailPageParser()
        parser.feed(_detail_html())

        assert parser.title == "Interstellar"
        assert parser.year == 2014
        assert parser.genres == ["Abenteuer", "Drama", "Science Fiction"]
        assert parser.csrf_token == "page-token"
        assert [
            (c.language, c.hoster, c.link_id, c.quality, c.payload)
            for c in parser.chips
        ] == [
            ("English", "VOE", "2723", "720p", "en-voe-1"),
            ("English", "Byse", "10973", "720p", "en-byse-1"),
            ("Deutsch", "VOE", "2723", "720p", "de-voe-1"),
            ("Deutsch", "Byse", "10973", "720p", "de-byse-1"),
        ]

    def test_interstitial_names_the_hoster_link(self) -> None:
        assert (
            _mod.interstitial_link(_BYSE_PAGE)
            == "https://bysezejataos.com/d/6cko7pwix0pz"
        )
        assert _mod.interstitial_link("<html><body>nothing</body></html>") is None


def _plugin() -> Any:
    plugin = _mod.FilmoPlugin()
    plugin._domain_verified = True
    return plugin


def _mock_site(*, detail_pages: list[str] | None = None) -> dict[str, respx.Route]:
    """The site: the search, the detail page, ``/n`` and ``/n/<token>``.

    ``POST /n`` answers the token ``t-<payload>``; ``GET /n/t-*-voe-*``
    redirects to VOE in the row's language, the Byse ones answer the
    interstitial.
    """
    routes: dict[str, respx.Route] = {}
    routes["search"] = respx.get(f"{_BASE}/search").respond(
        200,
        text=_SEARCH_HTML,
        headers=[
            ("Set-Cookie", "XSRF-TOKEN=xsrf%3Dvalue; Path=/"),
            ("Set-Cookie", "filmo-session=session; Path=/"),
        ],
    )
    pages = detail_pages or [_detail_html()]
    routes["detail"] = respx.get(_DETAIL).mock(
        side_effect=[httpx.Response(200, text=p) for p in pages]
    )
    routes["other_details"] = respx.get(url__startswith=f"{_BASE}/movies/").respond(404)

    def _token(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)["p"]
        return httpx.Response(200, json={"x": f"t-{payload}"})

    routes["n"] = respx.post(f"{_BASE}/n").mock(side_effect=_token)

    def _link(request: httpx.Request) -> httpx.Response:
        token = request.url.path.rsplit("/", 1)[1]
        lang = token.split("-")[1]
        if "-voe-" in token:
            return httpx.Response(302, headers={"Location": _VOE[lang]})
        return httpx.Response(200, text=_BYSE_PAGE)

    routes["link"] = respx.get(url__regex=rf"{_BASE}/n/t-.+").mock(side_effect=_link)
    return routes


class TestSearch:
    @respx.mock
    async def test_the_film_with_its_hoster_links(self) -> None:
        routes = _mock_site()
        async with httpx.AsyncClient() as client:
            plugin = _plugin()
            plugin._client = client
            results = await plugin.search("Interstellar", 2000)

        assert [(r.title, r.category, r.source_url) for r in results] == [
            ("Interstellar", 2000, _DETAIL)
        ]
        result = results[0]
        assert result.metadata["year"] == 2014
        assert result.metadata["genres"] == "Abenteuer, Drama, Science Fiction"
        # German first (the site lists English first); Byse names the same
        # file in both rows, so it is one link
        assert result.download_links == [
            {
                "hoster": "VOE",
                "link": _VOE["de"],
                "language": "German",
                "quality": "720p",
            },
            {
                "hoster": "Byse",
                "link": "https://bysezejataos.com/d/6cko7pwix0pz",
                "language": "German",
                "quality": "720p",
            },
            {
                "hoster": "VOE",
                "link": _VOE["en"],
                "language": "English",
                "quality": "720p",
            },
        ]
        assert result.download_link == _VOE["de"]
        assert all("/n/" not in link["link"] for link in result.download_links)
        # The search's loose matches (Supergirl, The Beyond) cost no request
        assert routes["detail"].call_count == 1
        fetched = {c.request.url.path for c in routes["other_details"].calls}
        assert fetched == {"/movies/interstellar-wars"}

    @respx.mock
    async def test_link_calls_carry_the_sessions_tokens(self) -> None:
        routes = _mock_site()
        async with httpx.AsyncClient() as client:
            plugin = _plugin()
            plugin._client = client
            await plugin.search("Interstellar")

        assert routes["search"].calls[0].request.headers["Accept-Language"] == (
            "de-DE,de;q=0.9"
        )
        post = routes["n"].calls[0].request
        assert post.headers["X-CSRF-TOKEN"] == "page-token"
        assert post.headers["X-XSRF-TOKEN"] == "xsrf=value"
        assert post.headers["X-Requested-With"] == "XMLHttpRequest"
        assert post.headers["Accept"] == "application/json"
        assert post.headers["Referer"] == _DETAIL
        assert sorted(
            json.loads(c.request.content)["p"] for c in routes["n"].calls
        ) == [
            "de-byse-1",
            "de-voe-1",
            "en-byse-1",
            "en-voe-1",
        ]
        link = routes["link"].calls[0].request
        assert link.headers["Referer"] == _DETAIL

    @pytest.mark.parametrize(
        ("category", "season"), [(5000, None), (5070, None), (None, 1), (7000, None)]
    )
    @respx.mock
    async def test_tv_and_other_requests_get_nothing(
        self, category: int | None, season: int | None
    ) -> None:
        routes = _mock_site()
        async with httpx.AsyncClient() as client:
            plugin = _plugin()
            plugin._client = client
            assert await plugin.search("Interstellar", category, season=season) == []
        assert not routes["search"].called

    @respx.mock
    async def test_an_expired_token_refetches_the_page_once(self) -> None:
        """A 419 to ``POST /n``: the page again (new token, new payloads),
        then the chip's call with the new ones."""
        routes = _mock_site(
            detail_pages=[_detail_html(), _detail_html(csrf="fresh", render="2")]
        )
        first: set[str] = set()

        def _token(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)["p"]
            if payload == "de-voe-1" and payload not in first:
                first.add(payload)
                return httpx.Response(419, json={"message": "CSRF token mismatch."})
            return httpx.Response(200, json={"x": f"t-{payload}"})

        routes["n"].mock(side_effect=_token)
        async with httpx.AsyncClient() as client:
            plugin = _plugin()
            plugin._client = client
            results = await plugin.search("Interstellar")

        assert routes["detail"].call_count == 2
        retried = [
            c.request
            for c in routes["n"].calls
            if json.loads(c.request.content)["p"] == "de-voe-2"
        ]
        assert len(retried) == 1
        assert retried[0].headers["X-CSRF-TOKEN"] == "fresh"
        assert _VOE["de"] in [link["link"] for link in results[0].download_links or []]

    @respx.mock
    async def test_a_chip_still_refused_after_the_refetch_is_skipped(self) -> None:
        routes = _mock_site(
            detail_pages=[_detail_html(), _detail_html(csrf="fresh", render="2")]
        )

        def _token(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)["p"]
            if payload.startswith("de-voe"):
                return httpx.Response(419, json={"message": "CSRF token mismatch."})
            return httpx.Response(200, json={"x": f"t-{payload}"})

        routes["n"].mock(side_effect=_token)
        async with httpx.AsyncClient() as client:
            plugin = _plugin()
            plugin._client = client
            results = await plugin.search("Interstellar")

        assert routes["detail"].call_count == 2
        links = [link["link"] for link in results[0].download_links or []]
        assert _VOE["de"] not in links
        assert links == [
            "https://bysezejataos.com/d/6cko7pwix0pz",
            _VOE["en"],
        ]

    @respx.mock
    async def test_a_film_without_links_is_no_result(self) -> None:
        routes = _mock_site()
        routes["detail"].mock(
            side_effect=[httpx.Response(200, text="<html><h1>Interstellar</h1></html>")]
        )
        async with httpx.AsyncClient() as client:
            plugin = _plugin()
            plugin._client = client
            assert await plugin.search("Interstellar") == []


def test_plugin_attributes() -> None:
    plugin = _mod.plugin
    assert plugin.name == "filmo"
    assert plugin.provides == "stream"
    assert plugin._domains == ["filmo.to"]
    assert plugin.languages == ["de", "en"]


@respx.mock
async def test_a_search_page_that_times_out_is_a_timeout() -> None:
    """A slow site is a timeout in the plugin record, not a search without
    results."""
    respx.get("https://filmo.to/search").mock(side_effect=httpx.ReadTimeout("slow"))
    plugin = _mod.FilmoPlugin()
    plugin._client = httpx.AsyncClient()
    plugin._domain_verified = True

    with pytest.raises(TimeoutError):
        await plugin.search("interstellar")
