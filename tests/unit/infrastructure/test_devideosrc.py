"""Tests for the devideosrc.co embed API helper."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from scavengarr.infrastructure.plugins import devideosrc
from scavengarr.infrastructure.plugins.devideosrc import DevideosrcPlayer

_MOVIE = DevideosrcPlayer(kind="movie", imdb_id="tt0371746")
_SERIES = DevideosrcPlayer(kind="tv", imdb_id="tt0903747")
_EMBED_LINKS = "https://devideosrc.co/api/embed-links"
_TOKEN = "bW92aWV8dHQwMzcxNzQ2fDB8MHwxNzkwNjA4MjAw.e9d906486a7eb6f7"

_PLAYER_HTML = f"""
<script>
fetch('/api/embed-links', {{
    method: 'POST',
    body: JSON.stringify({{ type: 'movie', id: "tt0371746", token: "{_TOKEN}" }})
}})
</script>
"""

_MOVIE_PAYLOAD = {
    "ok": True,
    "type": "movie",
    "sources": [
        {
            "name": "dropload.io",
            "url": "https://dr0pstream.com/e/4x0n7a8agp0m",
            "rank": 2,
        },
        {
            "name": "doodstream.com",
            "url": "https://doodstream.com/e/98bs427knuk1",
            "rank": 1,
        },
        {"name": "broken", "url": "", "rank": 3},
    ],
}

_TV_PAYLOAD = {
    "ok": True,
    "type": "tv",
    "tv": {
        "seasons": [
            {
                "season_number": 1,
                "episodes": [
                    {
                        "episode_number": 1,
                        "sources": [
                            {"name": "dropload.io", "url": "https://dr0pstream.com/e/a"}
                        ],
                    },
                    {"episode_number": 2, "sources": []},
                ],
            },
            {
                "season_number": 2,
                "episodes": [
                    {
                        "episode_number": 3,
                        "sources": [{"name": "", "url": "https://www.mxdrop.to/e/b"}],
                    }
                ],
            },
        ]
    },
}


class TestFindPlayer:
    def test_movie_iframe(self) -> None:
        html = '<iframe src="https://devideosrc.co/movie/tt2395427"></iframe>'
        assert devideosrc.find_player(html) == DevideosrcPlayer("movie", "tt2395427")

    def test_serial_script_with_imdb_var(self) -> None:
        html = (
            "<script>var imdb = 'tt0903747';"
            "iframe.src = 'https://devideosrc.co/serial/' + imdb;</script>"
        )
        assert devideosrc.find_player(html) == DevideosrcPlayer("tv", "tt0903747")

    def test_serial_imdb_from_download_embed(self) -> None:
        html = (
            "<script>iframe.src = 'https://devideosrc.co/serial/' + x;</script>"
            '<iframe src="https://devideosrc.co/embed/download/tt0903747"></iframe>'
        )
        assert devideosrc.find_player(html) == DevideosrcPlayer("tv", "tt0903747")

    def test_download_embed_alone_is_no_player(self) -> None:
        html = '<iframe src="https://devideosrc.co/embed/download/tt1"></iframe>'
        assert devideosrc.find_player(html) is None

    def test_no_player(self) -> None:
        assert devideosrc.find_player("<html></html>") is None


class TestPlayerUrl:
    def test_movie(self) -> None:
        assert devideosrc.player_url(_MOVIE) == (
            "https://devideosrc.co/movie/tt0371746"
        )

    def test_series(self) -> None:
        assert devideosrc.player_url(_SERIES) == (
            "https://devideosrc.co/serial/tt0903747"
        )


class TestParsePayload:
    def test_movie_links_ranked_and_filtered(self) -> None:
        assert devideosrc.movie_links(_MOVIE_PAYLOAD) == [
            {
                "hoster": "doodstream",
                "link": "https://doodstream.com/e/98bs427knuk1",
                "label": "doodstream",
            },
            {
                "hoster": "dropload",
                "link": "https://dr0pstream.com/e/4x0n7a8agp0m",
                "label": "dropload",
            },
        ]

    def test_tv_links_labelled_by_episode(self) -> None:
        links = devideosrc.tv_links(_TV_PAYLOAD)

        assert [(link["label"], link["hoster"]) for link in links] == [
            ("1x1 dropload", "dropload"),
            ("2x3 mxdrop", "mxdrop"),  # no name -> URL host
        ]

    def test_malformed_payloads(self) -> None:
        assert devideosrc.movie_links({"sources": "x"}) == []
        assert devideosrc.tv_links({"tv": None}) == []
        assert devideosrc.tv_links({"tv": {"seasons": [None, {"episodes": 1}]}}) == []

    def test_numbers_sent_as_text_or_missing(self) -> None:
        source = {"name": "voe", "url": "https://voe.sx/e/a"}
        payload = {
            "tv": {
                "seasons": [
                    {
                        "season_number": "2",
                        "episodes": [
                            {"episode_number": "4", "sources": [source]},
                            {"sources": [source]},  # no number: unplayable label
                        ],
                    },
                    {"episodes": [{"episode_number": 1, "sources": [source]}]},
                ]
            }
        }
        assert [link["label"] for link in devideosrc.tv_links(payload)] == ["2x4 voe"]


class TestFetchLinks:
    @respx.mock
    @pytest.mark.asyncio
    async def test_concurrent_calls_share_one_fetch(self) -> None:
        """hdfilme, streamcloud and streamkiste ask for the same player at once."""
        page = respx.get(url__startswith="https://devideosrc.co/movie/tt0371746")
        page.respond(200, text=_PLAYER_HTML)
        route = respx.post(_EMBED_LINKS).respond(200, json=_MOVIE_PAYLOAD)

        async with httpx.AsyncClient() as client:
            found = await asyncio.gather(
                *(devideosrc.fetch_links(client, _MOVIE) for _ in range(3))
            )

        assert page.call_count == 1
        assert route.call_count == 1
        assert found[0] == found[1] == found[2]
        assert found[0].links

    @respx.mock
    @pytest.mark.asyncio
    async def test_cancelled_caller_leaves_the_fetch_running(self) -> None:
        """A plugin cut by the deadline must not cancel the others' fetch."""
        gate = asyncio.Event()

        async def slow_page(request: httpx.Request) -> httpx.Response:
            await gate.wait()
            return httpx.Response(200, text=_PLAYER_HTML)

        respx.get(url__startswith="https://devideosrc.co/movie/tt0371746").mock(
            side_effect=slow_page
        )
        respx.post(_EMBED_LINKS).respond(200, json=_MOVIE_PAYLOAD)

        async with httpx.AsyncClient() as client:
            first = asyncio.ensure_future(devideosrc.fetch_links(client, _MOVIE))
            second = asyncio.ensure_future(devideosrc.fetch_links(client, _MOVIE))
            await asyncio.sleep(0)  # both callers wait on the shared fetch
            first.cancel()
            gate.set()
            found = await second

        assert first.cancelled()
        assert found.links

    @respx.mock
    @pytest.mark.asyncio
    async def test_movie_flow(self) -> None:
        respx.get(url__startswith="https://devideosrc.co/movie/tt0371746").respond(
            200, text=_PLAYER_HTML
        )
        route = respx.post(_EMBED_LINKS).respond(200, json=_MOVIE_PAYLOAD)

        async with httpx.AsyncClient() as client:
            links = (await devideosrc.fetch_links(client, _MOVIE)).links

        assert [link["hoster"] for link in links] == ["doodstream", "dropload"]
        assert json.loads(route.calls[0].request.content) == {
            "type": "movie",
            "id": "tt0371746",
            "token": _TOKEN,
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_series_flow(self) -> None:
        respx.get(url__startswith="https://devideosrc.co/serial/tt0903747").respond(
            200, text=_PLAYER_HTML
        )
        route = respx.post(_EMBED_LINKS).respond(200, json=_TV_PAYLOAD)

        async with httpx.AsyncClient() as client:
            links = (await devideosrc.fetch_links(client, _SERIES)).links

        assert len(links) == 2
        assert json.loads(route.calls[0].request.content)["type"] == "tv"

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_token(self) -> None:
        respx.get(url__startswith="https://devideosrc.co/movie/tt0371746").respond(
            200, text=""
        )
        route = respx.post(_EMBED_LINKS)

        async with httpx.AsyncClient() as client:
            assert (await devideosrc.fetch_links(client, _MOVIE)).links == []
        assert not route.called

    @respx.mock
    @pytest.mark.asyncio
    async def test_not_ok(self) -> None:
        respx.get(url__startswith="https://devideosrc.co/movie/tt0371746").respond(
            200, text=_PLAYER_HTML
        )
        respx.post(_EMBED_LINKS).respond(200, json={"ok": False})

        async with httpx.AsyncClient() as client:
            assert (await devideosrc.fetch_links(client, _MOVIE)).links == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_http_errors(self) -> None:
        respx.get(url__startswith="https://devideosrc.co/movie/tt0371746").respond(
            200, text=_PLAYER_HTML
        )
        respx.post(_EMBED_LINKS).respond(500)
        respx.get(url__startswith="https://devideosrc.co/serial/tt0903747").mock(
            side_effect=httpx.ConnectError("down")
        )

        async with httpx.AsyncClient() as client:
            assert (await devideosrc.fetch_links(client, _MOVIE)).links == []
            assert (await devideosrc.fetch_links(client, _SERIES)).links == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_invalid_json(self) -> None:
        respx.get(url__startswith="https://devideosrc.co/movie/tt0371746").respond(
            200, text=_PLAYER_HTML
        )
        respx.post(_EMBED_LINKS).respond(200, text="not json")

        async with httpx.AsyncClient() as client:
            assert (await devideosrc.fetch_links(client, _MOVIE)).links == []


class TestPlayerPageCache:
    @respx.mock
    @pytest.mark.asyncio
    async def test_page_always_loaded_past_cache(self) -> None:
        page = respx.get(
            url__startswith="https://devideosrc.co/movie/tt0371746"
        ).respond(200, text=_PLAYER_HTML)
        respx.post(_EMBED_LINKS).respond(200, json=_MOVIE_PAYLOAD)

        async with httpx.AsyncClient() as client:
            (await devideosrc.fetch_links(client, _MOVIE)).links

        assert page.calls[0].request.url.params["r"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_429_retried_with_fresh_url(self, monkeypatch) -> None:
        sleeps: list[float] = []

        async def _sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(devideosrc.asyncio, "sleep", _sleep)
        page = respx.get(url__startswith="https://devideosrc.co/movie/tt0371746").mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(200, text=_PLAYER_HTML),
            ]
        )
        respx.post(_EMBED_LINKS).respond(200, json=_MOVIE_PAYLOAD)

        async with httpx.AsyncClient() as client:
            links = (await devideosrc.fetch_links(client, _MOVIE)).links

        assert len(links) == 2
        first, retry = (c.request.url.params["r"] for c in page.calls)
        assert first != retry  # a cached 429 would come back on the same URL
        assert sleeps == [devideosrc._PAGE_RETRY_DELAY_S]

    @respx.mock
    @pytest.mark.asyncio
    async def test_persistent_429_gives_up(self, monkeypatch) -> None:
        async def _sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(devideosrc.asyncio, "sleep", _sleep)
        page = respx.get(
            url__startswith="https://devideosrc.co/movie/tt0371746"
        ).respond(429)

        async with httpx.AsyncClient() as client:
            assert (await devideosrc.fetch_links(client, _MOVIE)).links == []
        assert page.call_count == devideosrc._PAGE_ATTEMPTS

    @respx.mock
    @pytest.mark.asyncio
    async def test_embed_links_errors_not_retried_here(self) -> None:
        """POST backoff (429/503) is the shared RetryTransport's job."""
        respx.get(url__startswith="https://devideosrc.co/movie/tt0371746").respond(
            200, text=_PLAYER_HTML
        )
        route = respx.post(_EMBED_LINKS).respond(429)

        async with httpx.AsyncClient() as client:
            assert (await devideosrc.fetch_links(client, _MOVIE)).links == []
        assert route.call_count == 1


class TestSeriesPlayerForMovies:
    @respx.mock
    @pytest.mark.asyncio
    async def test_falls_back_to_movie_player(self) -> None:
        serial = respx.get(
            url__startswith="https://devideosrc.co/serial/tt0903747"
        ).respond(200, text="<html>no token</html>")
        respx.get(url__startswith="https://devideosrc.co/movie/tt0903747").respond(
            200, text=_PLAYER_HTML
        )
        route = respx.post(_EMBED_LINKS).respond(200, json=_MOVIE_PAYLOAD)

        async with httpx.AsyncClient() as client:
            found = await devideosrc.fetch_links(client, _SERIES)

        assert serial.called
        assert found.kind == "movie"
        assert len(found.links) == 2
        assert json.loads(route.calls[0].request.content)["type"] == "movie"

    @respx.mock
    @pytest.mark.asyncio
    async def test_series_answer_keeps_tv_kind(self) -> None:
        respx.get(url__startswith="https://devideosrc.co/serial/tt0903747").respond(
            200, text=_PLAYER_HTML
        )
        respx.post(_EMBED_LINKS).respond(200, json=_TV_PAYLOAD)

        async with httpx.AsyncClient() as client:
            found = await devideosrc.fetch_links(client, _SERIES)

        assert found.kind == "tv"

    @respx.mock
    @pytest.mark.asyncio
    async def test_movie_without_token_does_not_try_series(self) -> None:
        respx.get(url__startswith="https://devideosrc.co/movie/tt0371746").respond(
            200, text=""
        )
        serial = respx.get(url__startswith="https://devideosrc.co/serial/")

        async with httpx.AsyncClient() as client:
            found = await devideosrc.fetch_links(client, _MOVIE)

        assert found.links == []
        assert not serial.called
