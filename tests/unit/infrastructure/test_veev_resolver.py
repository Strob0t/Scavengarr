"""Tests for the veev.to resolver (player_api token decoding)."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.domain.entities.stremio import StreamQuality
from scavengarr.domain.ports.hoster_resolver import ClientBoundResolverPort
from scavengarr.infrastructure.hoster_resolvers.veev import (
    VeevResolver,
    _build_array,
    _extract_file_id,
    _lzw_decode,
    _unwrap,
)

# Real vectors captured from https://veev.to/e/5pi5uz0jufew (2026-09-28)
_TOKEN = "311020030Ă-92-5pi5uz0jufew-Ą9-1790604Ġ7Ċb4a74536ĩ071e0d505Ķc559a916c1"
_SOURCE_S = (
    "6458526d4f413dČ363đ4đ35Ĕ8ĔĖđ2ĔĐĒď3ēđĐĐĢ3ċĔĔēĞĪĠĬĘđ7ĭıĳ3Ĳđ0ĔķĩļċĐĜģĠŀĮĶļěĭĤĺĝĔŃŃīļ"
    "ĦĩőĔĨļ9ņ3ŃĩİřęĠĻĬĬĚĠđţŠŋņĬĦŢłŞŉũŋĿűġŔŊťŶ3ŜŦŨħżĖĐţūŵŁđľĕŸţŃƁżŕĻŜĩŃĬƊƃŴđŜĬŗğĴņĻĦŏĬ"
    "ƆƝŸƙŤŅƛŹƌĵŁţĬƢņƋŇƀĔƤţĩƮƕƸįŘŕƠŸŕƂƧŚŞƚŽƄŝĥƔƞƥŇǄĜĐĻĹŷǄŬǈĩƲƯŸǊĬƅǃƥţƤǀƦƨǘǆƑėǝƼżĻưŐļ"
    "ǕƇǯŒŘœǗǢĦǬĻĻƤǫƩǏǝĦƆƒĠţƏǧņŜĦŜǐŘšżǔƹĵŏǺǝȅǒƇĸȑŸŘǣǢȈżĩƿȆƥŪȢǟțƾşĵƷƉǝǑȗǞŸĻƓȖȠǝƵȌŮȯƆȀǽ"
    "ȣǋȯƺȖȳǅƸŃǼƧɇƸǷȿɋǜůǮŵǛŝȖȍǆĦǹȶȢĻȻɑǢƍȚəǄǬȦƧȮƽɏĩȬǆƤƐŸŎɝĦɃŧɔƔȏƽņɯǒĀģȼ46ĥɻŲĖĘĖī"
)
_SOURCE = (
    "https://s-gb-441928.veevcdn.co/AuxWanTajWRTax2bQHiG7MjtygxaLVt5KYStDh1aqFr5Ak"
    "AM2KpTnLSWM1VYFx5bhQfuCLNCCLWEGyujuJkX88Hp6fnFMyX5PDs8xF1HfHY3zttRPVWrU1U3nZ"
    "Ajo5hRQBuwTN4Zcj2VfGuXzJqw5TTYpjcRi2rJBWeana2E"
)
_EMBED = "https://veev.to/e/5pi5uz0jufew"
_EMBED_HTML = (
    "<html><head><title>Watch Iron Man 2008 1080p - Veev.to</title></head><body>"
    f'<script>window._vvto[ab] = "{_TOKEN}";</script></body></html>'
)
_API = "https://veev.to/dl"
_FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:156.0) Gecko/20100101 Firefox/156.0"
_GERMAN = "de,en-US;q=0.7,en;q=0.3"


class TestDecoding:
    def test_lzw_decode_plain_text(self) -> None:
        assert _lzw_decode("abc") == "abc"

    def test_lzw_decode_back_reference(self) -> None:
        # code 256 refers to the pair built from the first two symbols
        assert _lzw_decode("abĀ") == "abab"

    def test_lzw_decode_empty(self) -> None:
        assert _lzw_decode("") == ""

    def test_build_array_stops_at_non_digit(self) -> None:
        # count, then that many digits (stored reversed); "-" (-1) ends it
        assert _build_array("2013003-9") == [[1, 0], [3, 0, 0]]

    def test_unwrap_hex_and_reverse(self) -> None:
        # "hi" as hex, reversed once by step 1
        assert _unwrap("9686", [1]) == "hi"
        assert _unwrap("6869", [0]) == "hi"

    def test_real_vectors(self) -> None:
        ch = _lzw_decode(_TOKEN)
        steps = _build_array(ch)[0]
        assert _unwrap(_lzw_decode(_SOURCE_S), steps) == _SOURCE


class TestExtractFileId:
    @pytest.mark.parametrize(
        "url",
        [
            "https://veev.to/e/5pi5uz0jufew",
            "https://www.veev.to/e/5pi5uz0jufew",
            "http://veev.to/e/5pi5uz0jufew/",
            "https://veev.to/d/5pi5uz0jufew",
            "https://veev.to/5pi5uz0jufew",
        ],
    )
    def test_valid(self, url: str) -> None:
        assert _extract_file_id(url) == "5pi5uz0jufew"

    def test_long_id(self) -> None:
        url = "https://veev.to/e/2765mvZ9E9IZbJScKgPaBhtOxNTNzrk5rkHrKvA"
        assert _extract_file_id(url) == "2765mvZ9E9IZbJScKgPaBhtOxNTNzrk5rkHrKvA"

    @pytest.mark.parametrize(
        "url",
        [
            "https://veev.to/",
            "https://veev.to/e/short",
            "https://voe.sx/e/5pi5uz0jufew",
        ],
    )
    def test_invalid(self, url: str) -> None:
        assert _extract_file_id(url) is None


class TestVeevResolver:
    def test_name(self) -> None:
        assert VeevResolver(http_client=httpx.AsyncClient()).name == "veev"

    @respx.mock
    async def test_resolves_video_url(self) -> None:
        respx.get(_EMBED).respond(200, text=_EMBED_HTML)
        api = respx.get(_API).respond(
            200, json={"status": "success", "file": {"dv": [{"s": _SOURCE_S}]}}
        )

        async with httpx.AsyncClient() as client:
            result = await VeevResolver(http_client=client).resolve(_EMBED)

        assert result is not None
        assert result.video_url == _SOURCE
        assert result.is_hls is False
        assert result.quality == StreamQuality.UNKNOWN
        assert result.headers["Referer"] == "https://veev.to/"
        request = api.calls.last.request
        assert request.url.params["op"] == "player_api"
        assert request.url.params["file_code"] == "5pi5uz0jufew"
        assert request.url.params["ch"] == _lzw_decode(_TOKEN)
        assert request.headers["X-Requested-With"] == "XMLHttpRequest"

    @respx.mock
    async def test_asks_the_api_for_the_code_it_was_redirected_to(self) -> None:
        """veev redirects some embed links to another file code; the page's
        token belongs to that code, and the API answered the old one with
        "malformed request" ("hashcheck": 1; 6 links on 2026-10-04)."""
        old = "https://veev.to/e/BvdHl4N8GyKZvYh3DBKC3wGkMNasnCcjqSliKK"
        new = "https://veev.to/e/5pi5uz0jufew"
        respx.get(old).respond(302, headers={"Location": new})
        respx.get(new).respond(200, text=_EMBED_HTML)
        api = respx.get(_API).respond(
            200, json={"status": "success", "file": {"dv": [{"s": _SOURCE_S}]}}
        )

        async with httpx.AsyncClient() as client:
            result = await VeevResolver(http_client=client).resolve(old)

        assert result is not None
        assert result.video_url == _SOURCE
        request = api.calls.last.request
        assert request.url.params["file_code"] == "5pi5uz0jufew"
        assert request.headers["Referer"] == new

    @respx.mock
    async def test_playback_uses_the_resolving_user_agent(self) -> None:
        """veevcdn binds the stream token to the User-Agent that resolved it
        (other UA → 403), so playback must send the same one."""
        page = respx.get(_EMBED).respond(200, text=_EMBED_HTML)
        api = respx.get(_API).respond(
            200, json={"status": "success", "file": {"dv": [{"s": _SOURCE_S}]}}
        )

        async with httpx.AsyncClient(headers={"User-Agent": "Scavengarr/0.1"}) as c:
            result = await VeevResolver(http_client=c).resolve(_EMBED)

        assert result is not None
        ua = result.headers["User-Agent"]
        assert ua.startswith("Mozilla/5.0")
        assert page.calls.last.request.headers["User-Agent"] == ua
        assert api.calls.last.request.headers["User-Agent"] == ua

    def test_the_cdn_binds_user_agent_and_accept_language(self) -> None:
        resolver = VeevResolver(http_client=httpx.AsyncClient())

        assert isinstance(resolver, ClientBoundResolverPort)
        assert resolver.bound_headers == ("user-agent", "accept-language")

    @respx.mock
    async def test_resolves_as_the_player_asking(self) -> None:
        """veevcdn binds the token to the User-Agent and the Accept-Language
        of the resolution: Stremio's streaming server passes the browser's
        Accept-Language on, and the stored URL answered 403; resolved with
        it, 206 (production, 2026-10-06)."""
        page = respx.get(_EMBED).respond(200, text=_EMBED_HTML)
        api = respx.get(_API).respond(
            200, json={"status": "success", "file": {"dv": [{"s": _SOURCE_S}]}}
        )

        async with httpx.AsyncClient() as client:
            result = await VeevResolver(http_client=client).resolve_for_client(
                _EMBED, {"user-agent": _FIREFOX, "accept-language": _GERMAN}
            )

        assert result is not None
        assert result.video_url == _SOURCE
        assert result.headers == {
            "Referer": "https://veev.to/",
            "User-Agent": _FIREFOX,
            "Accept-Language": _GERMAN,
        }
        for call in (page.calls.last, api.calls.last):
            assert call.request.headers["User-Agent"] == _FIREFOX
            assert call.request.headers["Accept-Language"] == _GERMAN

    @respx.mock
    async def test_a_player_without_those_headers_gets_the_default_user_agent(
        self,
    ) -> None:
        page = respx.get(_EMBED).respond(200, text=_EMBED_HTML)
        respx.get(_API).respond(
            200, json={"status": "success", "file": {"dv": [{"s": _SOURCE_S}]}}
        )

        async with httpx.AsyncClient() as client:
            result = await VeevResolver(http_client=client).resolve_for_client(
                _EMBED, {}
            )

        assert result is not None
        assert result.headers["User-Agent"].startswith("Mozilla/5.0")
        assert "Accept-Language" not in result.headers
        assert "Accept-Language" not in page.calls.last.request.headers

    @respx.mock
    @pytest.mark.parametrize(
        "html",
        [
            "<html><head><title>Watch video - Veev.to</title></head></html>",
            "<html><body><h3> File not found</h3></body></html>",
        ],
    )
    async def test_offline_markers(self, html: str) -> None:
        respx.get(_EMBED).respond(200, text=html)

        async with httpx.AsyncClient() as client:
            assert await VeevResolver(http_client=client).resolve(_EMBED) is None

    @respx.mock
    async def test_missing_token(self) -> None:
        respx.get(_EMBED).respond(200, text="<html><title>Watch x - Veev.to</title>")

        async with httpx.AsyncClient() as client:
            assert await VeevResolver(http_client=client).resolve(_EMBED) is None

    @respx.mock
    async def test_api_failure_status(self) -> None:
        respx.get(_EMBED).respond(200, text=_EMBED_HTML)
        respx.get(_API).respond(200, json={"status": "fail", "error": "x"})

        async with httpx.AsyncClient() as client:
            assert await VeevResolver(http_client=client).resolve(_EMBED) is None

    @respx.mock
    async def test_api_unexpected_shape(self) -> None:
        respx.get(_EMBED).respond(200, text=_EMBED_HTML)
        respx.get(_API).respond(200, json={"status": "success", "file": {}})

        async with httpx.AsyncClient() as client:
            assert await VeevResolver(http_client=client).resolve(_EMBED) is None

    @respx.mock
    async def test_http_error(self) -> None:
        respx.get(_EMBED).respond(404)

        async with httpx.AsyncClient() as client:
            assert await VeevResolver(http_client=client).resolve(_EMBED) is None

    @respx.mock
    async def test_network_error(self) -> None:
        respx.get(_EMBED).mock(side_effect=httpx.ConnectError("down"))

        async with httpx.AsyncClient() as client:
            assert await VeevResolver(http_client=client).resolve(_EMBED) is None

    async def test_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            resolver = VeevResolver(http_client=client)
            assert await resolver.resolve("https://veev.to/") is None
