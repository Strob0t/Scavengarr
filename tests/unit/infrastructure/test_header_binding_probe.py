"""Tests for scripts/probes/header_binding.py (no live requests)."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

_PROBE = Path(__file__).resolve().parents[3] / "scripts/probes/header_binding.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("header_binding_probe", _PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses resolve their annotations through sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()

_URL = "https://cdn.example/v/file.mp4?token=secret"
_MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 100
_TS = (b"\x47" + b"\x00" * 187) * 2
_HTML = b"<html><body>403 Forbidden</body></html>"
_REFERER = {"Referer": "https://hoster.example/e/abc"}


def _answer(status: int, kind: str) -> Any:
    return probe.Answer(status, kind)


class TestSniff:
    def test_a_playlist_may_start_after_whitespace(self) -> None:
        assert probe.sniff("application/vnd.apple.mpegurl", b"\n#EXTM3U\n") == (
            "playlist"
        )

    def test_no_body_is_empty(self) -> None:
        assert probe.sniff("video/mp4", b"") == "empty"

    def test_an_html_type_is_html_whatever_the_bytes(self) -> None:
        assert probe.sniff("text/html; charset=utf-8", _MP4) == "html"

    @pytest.mark.parametrize(
        ("head", "kind"),
        [
            (_MP4, "mp4"),
            (_TS, "mpegts"),
            (b"\x1aE\xdf\xa3" + b"\x00" * 20, "mkv"),
            (_HTML, "html"),
            (b"error_wrong_ip", "bytes:6572726f725f7772"),
        ],
    )
    def test_the_play_checks_kinds_follow(self, head: bytes, kind: str) -> None:
        assert probe.sniff("application/octet-stream", head) == kind


class TestAnswer:
    @pytest.mark.parametrize(
        ("status", "kind", "media"),
        [
            (206, "mp4", True),
            (200, "mpegts", True),
            (200, "playlist", True),
            (200, "mkv", True),
            (403, "mp4", False),
            (200, "html", False),
            (206, "bytes:00", False),
            (0, "ConnectTimeout", False),
        ],
    )
    def test_media_needs_a_success_and_media_bytes(
        self, status: int, kind: str, media: bool
    ) -> None:
        assert _answer(status, kind).media is media

    def test_the_cell_names_status_and_kind_or_the_error(self) -> None:
        assert str(_answer(206, "mp4")) == "206 mp4"
        assert str(_answer(0, "ConnectTimeout")) == "ConnectTimeout"


class TestWays:
    def test_without_stored_headers_the_check_and_the_bare_player(self) -> None:
        assert probe.ways({}) == {
            "check": {"User-Agent": probe.DEFAULT_USER_AGENT},
            "bare": {"User-Agent": probe.PLAYER_UA},
        }

    def test_stored_headers_add_the_two_mixed_ways(self) -> None:
        stored = {**_REFERER, "Origin": "https://hoster.example"}
        asked = probe.ways(stored)
        assert asked == {
            "check": {"User-Agent": probe.DEFAULT_USER_AGENT, **stored},
            "bare": {"User-Agent": probe.PLAYER_UA},
            "player UA": {"User-Agent": probe.PLAYER_UA, **stored},
            "check UA": {"User-Agent": probe.DEFAULT_USER_AGENT},
        }

    def test_a_stored_user_agent_is_the_checks(self) -> None:
        asked = probe.ways({"user-agent": "Resolver/1.0", **_REFERER})
        assert asked["check"] == {"User-Agent": "Resolver/1.0", **_REFERER}
        assert asked["check UA"] == {"User-Agent": "Resolver/1.0"}
        assert asked["player UA"] == {"User-Agent": probe.PLAYER_UA, **_REFERER}

    def test_a_stored_user_agent_alone_adds_no_mixed_way(self) -> None:
        assert sorted(probe.ways({"User-Agent": "Resolver/1.0"})) == [
            "bare",
            "check",
        ]

    def test_the_player_is_not_the_check(self) -> None:
        assert probe.PLAYER_UA != probe.DEFAULT_USER_AGENT


def _answers(**ok: bool) -> dict[str, Any]:
    return {
        way.replace("_", " "): _answer(206, "mp4") if good else _answer(403, "html")
        for way, good in ok.items()
    }


class TestVerdict:
    def test_no_media_for_the_check_is_dead_here(self) -> None:
        assert probe.verdict(_answers(check=False, bare=False)) == "dead here"

    def test_no_answer_for_the_check(self) -> None:
        answers = {"check": _answer(0, "ConnectError"), "bare": _answer(0, "x")}
        assert probe.verdict(answers) == "no answer"

    def test_media_for_the_bare_player_is_free(self) -> None:
        assert probe.verdict(_answers(check=True, bare=True)) == "free"

    def test_without_mixed_ways_only_the_user_agent_differs(self) -> None:
        assert probe.verdict(_answers(check=True, bare=False)) == "user-agent"

    @pytest.mark.parametrize(
        ("player_ua", "check_ua", "expected"),
        [
            (False, True, "user-agent"),
            (True, False, "headers"),
            (False, False, "user-agent + headers"),
            (True, True, "user-agent or headers"),
        ],
    )
    def test_the_mixed_ways_name_what_the_player_lacks(
        self, player_ua: bool, check_ua: bool, expected: str
    ) -> None:
        answers = _answers(
            check=True, bare=False, player_UA=player_ua, check_UA=check_ua
        )
        assert probe.verdict(answers) == expected


class TestLink:
    @pytest.mark.parametrize(
        ("is_hls", "address_bound", "route"),
        [
            (True, False, "proxy"),
            (True, True, "proxy"),
            (False, True, "file"),
            (False, False, "play"),
        ],
    )
    def test_the_route_stremio_takes(
        self, is_hls: bool, address_bound: bool, route: str
    ) -> None:
        link = probe.Link(
            "dev", "voe", video_url=_URL, is_hls=is_hls, address_bound=address_bound
        )
        assert link.route == route

    def test_a_link_without_video_takes_no_route(self) -> None:
        assert probe.Link("dev", "filemoon", note="not resolved").route == "–"


def _dump_line(**fields: object) -> str:
    record = {
        "hoster": "vidoza",
        "title": "Interstellar",
        "video_url": _URL,
        "video_headers": json.dumps(_REFERER),
        "is_hls": False,
        "address_bound": False,
        **fields,
    }
    return json.dumps(record)


class TestDump:
    def test_the_dump_compiles(self) -> None:
        compile(probe.DUMP, "<dump>", "exec")

    def test_json_lines_become_links_and_the_rest_is_skipped(self) -> None:
        output = "\n".join(
            [
                "warning: something",
                _dump_line(),
                _dump_line(),  # a pinned copy repeats the video URL
                _dump_line(video_url=""),
                "{not json",
                _dump_line(
                    hoster="voe",
                    video_url="https://cdn.example/m.m3u8",
                    video_headers="",
                    is_hls=True,
                ),
                _dump_line(
                    hoster="mixdrop",
                    video_url="https://cdn.example/x.mp4",
                    video_headers="[1, 2]",
                    address_bound=True,
                ),
                _dump_line(
                    hoster="vinovo",
                    video_url="https://cdn.example/y.mp4",
                    video_headers="{broken",
                ),
            ]
        )
        links = probe.parse_dump(output)
        assert [(link.hoster, link.route) for link in links] == [
            ("vidoza", "play"),
            ("voe", "proxy"),
            ("mixdrop", "file"),
            ("vinovo", "play"),
        ]
        assert links[0].headers == _REFERER
        assert links[0].title == "Interstellar"
        assert links[0].source == "production"
        assert [link.headers for link in links[1:]] == [{}, {}, {}]

    def test_production_links_run_the_dump_in_the_container(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        class Container:
            def run(self, cmd: list[str]) -> tuple[str, int]:
                calls.append(cmd)
                return _dump_line() + "\n", 0

        monkeypatch.setattr(probe, "portainer_client", lambda name: Container())
        links = probe.production_links("scavengarr", 3600.0, [])
        assert [link.hoster for link in links] == ["vidoza"]
        assert calls[0][:3] == ["python", "-c", probe.DUMP]
        assert calls[0][3:] == ["3600.0"]
        probe.production_links("scavengarr", 3600.0, ["a1", "b2"])
        assert calls[1][3:] == ["0", "a1", "b2"]

    def test_a_failed_dump_names_its_exit_code_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Container:
            def run(self, cmd: list[str]) -> tuple[str, int]:
                return f"Traceback ... {_URL}", 1

        monkeypatch.setattr(probe, "portainer_client", lambda name: Container())
        with pytest.raises(SystemExit) as raised:
            probe.production_links("scavengarr", 3600.0, [])
        assert str(raised.value) == "the container's link dump failed (exit 1)"


class TestFetch:
    @respx.mock
    def test_the_first_bytes_are_asked_with_the_given_headers_only(self) -> None:
        route = respx.get(_URL).respond(206, content=_MP4 * 50)
        answer = asyncio.run(probe.fetch(_URL, {"User-Agent": probe.PLAYER_UA}))
        assert (answer.status, answer.kind) == (206, "mp4")
        sent = route.calls.last.request.headers
        assert sent["Range"] == f"bytes=0-{probe.SNIFF_BYTES - 1}"
        assert sent["User-Agent"] == probe.PLAYER_UA
        assert "Referer" not in sent

    @respx.mock
    def test_no_cookie_carries_over_to_the_next_request(self) -> None:
        route = respx.get(_URL).mock(
            side_effect=[
                httpx.Response(206, content=_MP4, headers={"Set-Cookie": "s=1"}),
                httpx.Response(206, content=_MP4),
            ]
        )

        async def twice() -> None:
            await probe.fetch(_URL, {"User-Agent": "a"})
            await probe.fetch(_URL, {"User-Agent": "b"})

        asyncio.run(twice())
        assert "cookie" not in route.calls.last.request.headers

    @respx.mock
    def test_a_network_error_is_an_answer_without_status(self) -> None:
        respx.get(_URL).mock(side_effect=httpx.ConnectError("refused"))
        answer = asyncio.run(probe.fetch(_URL, {}))
        assert (answer.status, answer.kind) == (0, "ConnectError")

    @respx.mock
    def test_an_error_page_is_html(self) -> None:
        respx.get(_URL).respond(
            403, content=_HTML, headers={"Content-Type": "text/html"}
        )
        answer = asyncio.run(probe.fetch(_URL, {}))
        assert (answer.status, answer.kind, answer.media) == (403, "html", False)


def _cdn_needing(header: str, value: str | None = None) -> None:
    """The CDN plays the file only when *header* is sent (with *value*)."""

    def answer(request: httpx.Request) -> httpx.Response:
        sent = request.headers.get(header)
        if sent is None or (value is not None and sent != value):
            return httpx.Response(
                403, content=_HTML, headers={"Content-Type": "text/html"}
            )
        return httpx.Response(206, content=_MP4)

    respx.get(_URL).mock(side_effect=answer)


class TestAsk:
    @respx.mock
    def test_a_referer_bound_cdn_is_header_bound(self) -> None:
        _cdn_needing("Referer")
        link = probe.Link("dev", "vidoza", video_url=_URL, headers=_REFERER)
        answers = asyncio.run(probe.ask(link, 0))
        assert list(answers) == list(probe.WAYS)
        assert probe.verdict(answers) == "headers"

    @respx.mock
    def test_a_user_agent_bound_cdn_is_user_agent_bound(self) -> None:
        _cdn_needing("User-Agent", probe.DEFAULT_USER_AGENT)
        link = probe.Link("dev", "veev", video_url=_URL, headers=_REFERER)
        assert probe.verdict(asyncio.run(probe.ask(link, 0))) == "user-agent"

    @respx.mock
    def test_a_free_cdn_is_asked_twice_without_stored_headers(self) -> None:
        route = respx.get(_URL).respond(206, content=_MP4)
        link = probe.Link("dev", "streamtape", video_url=_URL)
        answers = asyncio.run(probe.ask(link, 0))
        assert route.call_count == 2
        assert probe.verdict(answers) == "free"


_M3U8 = "https://cdn.example/hls/master.m3u8?token=secret"
_VARIANT = "https://cdn.example/hls/low/index.m3u8?t=1"
_MASTER = (
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\nlow/index.m3u8?t=1\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2000000\nhigh/index.m3u8\n"
)
_SEGMENTS = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nseg1.ts\n"
_RANGED = (
    "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\n#EXT-X-BYTERANGE:75232@0\n"
    "video.ts\n#EXTINF:6,\n#EXT-X-BYTERANGE:82112\nvideo.ts\n"
)


class TestPlaylists:
    def test_the_first_variant_of_a_master(self) -> None:
        assert probe.first_variant(_MASTER) == "low/index.m3u8?t=1"

    def test_a_media_playlist_has_no_variant(self) -> None:
        assert probe.first_variant(_SEGMENTS) is None

    @pytest.mark.parametrize(
        ("playlist", "ranged"),
        [
            (_SEGMENTS, False),
            (_RANGED, True),
            ('#EXTM3U\n#EXT-X-MAP:URI="i.mp4",BYTERANGE="720@0"\n', True),
            ('#EXTM3U\n#EXT-X-MAP:URI="i.mp4"\n#EXTINF:6,\ns1.m4s\n', False),
        ],
    )
    def test_segments_or_their_init_section_by_byte_range(
        self, playlist: str, ranged: bool
    ) -> None:
        assert probe.by_byte_range(playlist) is ranged


class TestByteRanges:
    _HLS = probe.Link("dev", "voe", video_url=_M3U8, headers=_REFERER, is_hls=True)

    @respx.mock
    def test_the_first_variant_is_read_like_the_proxy_reads_it(self) -> None:
        master = respx.get(_M3U8).respond(200, text=_MASTER)
        variant = respx.get(_VARIANT).respond(200, text=_RANGED)
        assert asyncio.run(probe.byte_ranges(self._HLS)) == "yes"
        for route in (master, variant):
            sent = route.calls.last.request.headers
            assert sent["Referer"] == _REFERER["Referer"]
            assert sent["User-Agent"] == probe.DEFAULT_USER_AGENT
            assert "Range" not in sent

    @respx.mock
    def test_a_media_playlist_is_read_itself(self) -> None:
        respx.get(_M3U8).respond(200, text=_SEGMENTS)
        assert asyncio.run(probe.byte_ranges(self._HLS)) == "no"

    @respx.mock
    def test_a_variant_without_playlist_names_its_answer(self) -> None:
        respx.get(_M3U8).respond(200, text=_MASTER)
        respx.get(_VARIANT).respond(403, content=_HTML)
        assert asyncio.run(probe.byte_ranges(self._HLS)) == "variant 403"

    @respx.mock
    def test_a_master_without_answer_names_the_error(self) -> None:
        respx.get(_M3U8).mock(side_effect=httpx.ConnectError("refused"))
        answer = asyncio.run(probe.byte_ranges(self._HLS))
        assert answer == "master ConnectError"


def _result(
    hoster: str,
    route: str,
    verdict_ok: dict[str, bool],
    source: str = "production",
    ranges: str = "–",
) -> Any:
    link = probe.Link(
        source,
        hoster,
        title="A | B",
        video_url=_URL,
        headers={**_REFERER, "User-Agent": "Resolver/1.0"},
        is_hls=route == "proxy",
        address_bound=route == "file",
    )
    return probe.Probed(link, _answers(**verdict_ok), ranges)


class TestRender:
    def test_a_row_names_header_names_never_values_or_urls(self) -> None:
        probed = _result(
            "vidoza",
            "play",
            {"check": True, "bare": False, "player_UA": True, "check_UA": False},
        )
        line = probe.row(probed)
        assert line == (
            "| production | vidoza | A / B | play | cdn.example"
            " | Referer, User-Agent | 206 mp4 | 403 html | 206 mp4 | 403 html"
            " | headers | – |"
        )
        assert "secret" not in line and "hoster.example" not in line

    def test_a_link_without_video_names_why(self) -> None:
        line = probe.row(probe.Probed(probe.Link("dev", "filemoon", note="gone")))
        assert line == ("| dev | filemoon | – | – | – | – | – | – | – | – | gone | – |")

    def test_the_summary_names_the_bound_hosters_of_the_play_route(self) -> None:
        results = [
            _result("vidoza", "play", {"check": True, "bare": False}),
            _result("vidoza", "play", {"check": True, "bare": True}),
            _result("voe", "proxy", {"check": True, "bare": False}, ranges="yes"),
            _result("voe", "proxy", {"check": True, "bare": True}, "dev", "no"),
            _result("mixdrop", "file", {"check": False, "bare": False}),
            _result("streamtape", "play", {"check": True, "bare": True}, "dev"),
            probe.Probed(probe.Link("dev", "filemoon", note="not resolved")),
        ]
        text = probe.summary(results)
        assert (
            "| vidoza | play | production: free ×1, production: user-agent ×1 | – |"
            in text
        )
        assert (
            "| voe | proxy | dev: free ×1, production: user-agent ×1 | no ×1, yes ×1 |"
            in text
        )
        assert "| mixdrop | file | production: dead here ×1 | – |" in text
        assert "| filemoon | – | dev: not resolved ×1 | – |" in text
        assert "(what the player lacks there): vidoza\n" in text
        assert text.endswith("by byte range (EXT-X-BYTERANGE): voe")

    def test_render_puts_the_rows_over_the_summary(self) -> None:
        text = probe.render([_result("vidoza", "play", {"check": True, "bare": True})])
        assert text.startswith(probe.TABLE_HEAD)
        assert "(what the player lacks there): none\n" in text
        assert text.endswith("(EXT-X-BYTERANGE): none")


class TestRun:
    def _args(self, **overrides: object) -> argparse.Namespace:
        args = probe.parser().parse_args([])
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def test_defaults(self) -> None:
        args = probe.parser().parse_args([])
        assert (args.source, args.since, args.ids, args.pause) == (
            "both",
            3600.0,
            [],
            0.5,
        )
        assert args.urls == probe.URLS

    @respx.mock
    def test_both_sources_are_asked_and_filtered_by_hoster(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        respx.get(_URL).respond(206, content=_MP4)
        urls = tmp_path / "urls.json"
        urls.write_text(json.dumps({"vidoza": "https://h/1", "voe": "https://h/2"}))
        seen: dict[str, object] = {}

        def production(container: str, since: float, ids: list[str]) -> list[Any]:
            seen["production"] = (container, since, ids)
            return [
                probe.Link("production", "vidoza", video_url=_URL),
                probe.Link("production", "voe", video_url=_URL),
            ]

        async def dev(asked: dict[str, str]) -> list[Any]:
            seen["dev"] = asked
            return [probe.Link("dev", "vidoza", video_url=_URL)]

        monkeypatch.setattr(probe, "production_links", production)
        monkeypatch.setattr(probe, "dev_links", dev)
        results = asyncio.run(
            probe.run(self._args(hosters="vidoza", urls=urls, pause=0.0))
        )
        assert [(p.link.source, p.link.hoster) for p in results] == [
            ("production", "vidoza"),
            ("dev", "vidoza"),
        ]
        assert all(p.outcome == "free" for p in results)
        assert seen["production"] == ("scavengarr", 3600.0, [])
        assert seen["dev"] == {"vidoza": "https://h/1"}

    def test_a_link_without_video_is_not_asked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        async def dev(asked: dict[str, str]) -> list[Any]:
            return [probe.Link("dev", "filemoon", note="not resolved")]

        monkeypatch.setattr(probe, "dev_links", dev)
        results = asyncio.run(
            probe.run(self._args(source="dev", urls=tmp_path / "none.json"))
        )
        assert results == [
            probe.Probed(probe.Link("dev", "filemoon", note="not resolved"))
        ]

    @respx.mock
    def test_only_playlists_are_read_for_byte_ranges(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        respx.get(_M3U8).respond(200, text=_SEGMENTS)
        respx.get(_URL).respond(206, content=_MP4)

        async def dev(asked: dict[str, str]) -> list[Any]:
            return [
                probe.Link("dev", "voe", video_url=_M3U8, is_hls=True),
                probe.Link("dev", "streamtape", video_url=_URL),
            ]

        monkeypatch.setattr(probe, "dev_links", dev)
        args = self._args(source="dev", urls=tmp_path / "none.json", pause=0.0)
        results = asyncio.run(probe.run(args))
        assert [(p.link.hoster, p.outcome, p.ranges) for p in results] == [
            ("voe", "free", "no"),
            ("streamtape", "free", "–"),
        ]
