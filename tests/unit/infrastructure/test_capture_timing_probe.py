"""Tests for the pre-checks, process cleanup, timing, diagnosis and report
of scripts/probes/capture_timing.py."""

from __future__ import annotations

import importlib.util
import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from scavengarr.infrastructure.browser import stealth_pool as stealth
from scavengarr.infrastructure.browser.stealth_pool import CapturedMedia, StealthPool

_PROBES = Path(__file__).resolve().parents[3] / "scripts" / "probes"


def _load() -> ModuleType:
    # By path: on sys.path the probes' redis.py would shadow the redis package
    spec = importlib.util.spec_from_file_location(
        "capture_timing_probe", _PROBES / "capture_timing.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses resolve their annotations through sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load()
_MiB = 1048576


class TestNames:
    def test_the_path_kind_names_no_id(self) -> None:
        assert _mod.path_kind("https://byse.example/d/abc123?x=1") == "/d/"
        assert _mod.path_kind("https://byse.example/") == "/"

    def test_the_cdn_is_the_last_two_labels(self) -> None:
        assert _mod.domain("https://edge-1.cdn.example.com/hls/master.m3u8") == (
            "example.com"
        )


class TestPreChecks:
    def test_only_browser_processes_count(self) -> None:
        cmdlines = [
            [
                "/ms-playwright/chromium-1/chrome-linux/chrome",
                "--disable-dev-shm-usage",
            ],
            ["/ms-playwright/chromium-1/chrome-linux/chrome", "--type=renderer"],
            ["/usr/bin/chromium", "--no-sandbox"],
            ["/ms-playwright/chromium-1/chrome-linux/chrome_crashpad_handler"],
            ["python", "-m", "scavengarr"],
            [],
        ]

        assert _mod.shm_flags(cmdlines) == [True, False]

    def test_short_memory_or_shm_refuses_the_launch(self) -> None:
        assert _mod.refusals(2048 * _MiB, 64 * _MiB) == []
        assert _mod.refusals(512 * _MiB, 16 * _MiB) == [
            "memory short: 512 MiB available",
            "/dev/shm short: 16 MiB free",
        ]


class TestProcesses:
    def test_the_descendants_of_a_process(self) -> None:
        parent_of = {2: 1, 3: 2, 4: 2, 5: 9, 6: 4}

        assert sorted(_mod.descendants_of(1, parent_of)) == [2, 3, 4, 6]
        assert _mod.descendants_of(7, parent_of) == []

    def test_the_kill_takes_the_probes_tree_by_parent_pid(self) -> None:
        """The app's Chromium (201, below PID 1) stays: only the probe's
        own descendants get SIGKILL."""
        me = os.getpid()
        parent_of = {101: me, 102: 101, 103: 102, 201: 1, 202: 201}
        killed: list[tuple[int, int]] = []

        count = _mod.kill_descendants(
            parent_of, kill=lambda pid, sig: killed.append((pid, sig))
        )

        assert count == 3
        assert sorted(pid for pid, _ in killed) == [101, 102, 103]
        assert {sig for _, sig in killed} == {signal.SIGKILL}


class TestTimedPool:
    async def test_a_page_load_is_noted_with_its_path_kind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def navigate(*_args: object, **_kwargs: object) -> bool:
            return True

        monkeypatch.setattr(StealthPool, "_navigate", navigate)
        pool = _mod.TimedStealthPool(browser_pool=MagicMock())
        pool.run = _mod.Run(time.monotonic())

        loaded = await pool._navigate(
            MagicMock(),
            "https://byse.example/d/abc",
            wait_until="domcontentloaded",
            timeout_ms=1000,
        )

        assert loaded
        assert [(p.kind, p.loaded) for p in pool.run.pages] == [("/d/", True)]

    async def test_a_capture_notes_the_stream_and_its_cdn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def capture(*_args: object, **_kwargs: object) -> CapturedMedia:
            return CapturedMedia("https://edge.cdn.example.com/master.m3u8", None)

        monkeypatch.setattr(StealthPool, "capture_media", capture)
        pool = _mod.TimedStealthPool(browser_pool=MagicMock())
        pool.run = _mod.Run(time.monotonic())

        await pool.capture_media("https://byse.example/d/abc", player="iframe")

        assert pool.run.stream_at is not None
        assert pool.run.cdn == "example.com"

    async def test_each_click_is_noted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def click_point(_page: object, _player: object) -> tuple[float, float]:
            return 1.0, 2.0

        # restores the module's own function after the test
        monkeypatch.setattr(stealth, "_click_point", click_point)
        pool = _mod.TimedStealthPool(browser_pool=MagicMock())
        pool.run = _mod.Run(time.monotonic())
        _mod.note_clicks(pool)

        point = await stealth._click_point(MagicMock(), None)

        assert point == (1.0, 2.0)
        assert len(pool.run.clicks) == 1


class TestReport:
    def test_a_captured_run(self) -> None:
        run = _mod.Run(0.0, stream_at=12.24, cdn="example.com", took=12.6)
        run.pages.append(_mod.PageLoad("/d/", 0.42, 2.9, True))
        run.clicks.extend([6.4, 11.4])
        run.resolved = True

        assert _mod.report_row(1, run) == (
            "| 1 | /d/: 0.4 s, 2.9 s | 6.4, 11.4 s | 12.2 s (example.com) | 12.6 s |"
        )

    def test_a_run_without_a_stream_and_a_cut_run(self) -> None:
        nothing = _mod.Run(0.0, took=18.5)
        nothing.pages.append(_mod.PageLoad("/e/", 0.1, 10.0, False))
        cut = _mod.Run(0.0, took=90.0, cut=True)

        assert _mod.report_row(1, nothing) == (
            "| 1 | /e/: 0.1 s, 10.0 s (failed) | – | none | 18.5 s (nothing) |"
        )
        assert _mod.report_row(2, cut) == "| 2 | none | – | none | cut at 90 s |"


def test_the_capture_resolvers_by_name() -> None:
    names = _mod.capture_resolvers(httpx.AsyncClient(), MagicMock())

    assert {"filemoon", "supervideo", "doodstream", "vixeo"} <= set(names)


def test_two_runs_by_default() -> None:
    args = _mod._parser().parse_args(["https://byse.example/e/abc"])

    assert (args.hoster, args.runs, args.timeout, args.diagnose, args.block) == (
        "filemoon",
        2,
        None,
        False,
        [],
    )


def test_blocks_repeat() -> None:
    args = _mod._parser().parse_args(
        ["https://byse.example/e/abc", "--block", "llvpn.com", "--block", "rtmark.net"]
    )

    assert args.block == ["llvpn.com", "rtmark.net"]


class _Page:
    """Stands in for a Playwright page: its handlers by event and its frames."""

    def __init__(self, frames: list[_Frame] | None = None) -> None:
        self.handlers: dict[str, list[Callable[[Any], None]]] = {}
        self.frames = frames or []
        self.main_frame = self.frames[0] if self.frames else None

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, payload: object) -> None:
        for handler in self.handlers.get(event, []):
            handler(payload)


class _Request:
    def __init__(
        self,
        url: str,
        resource_type: str = "fetch",
        *,
        redirected_from: _Request | None = None,
        failure: str | None = None,
    ) -> None:
        self.url = url
        self.resource_type = resource_type
        self.redirected_from = redirected_from
        self.failure = failure


class _Response:
    def __init__(self, request: _Request, status: int) -> None:
        self.request = request
        self.status = status


class _Frame:
    def __init__(
        self,
        url: str,
        answer: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.url = url
        self.answer = answer
        self.error = error

    async def evaluate(self, _script: str) -> dict[str, Any] | None:
        if self.error is not None:
            raise self.error
        return self.answer


def _plays(**changes: object) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "video": False,
        "error": None,
        "network": None,
        "h264": True,
        "aac": True,
        "hls": "",
    }
    answer.update(changes)
    return answer


class TestShapes:
    def test_the_shape_masks_ids_and_drops_the_query(self) -> None:
        shape = _mod.shape

        assert (
            shape("https://byse.example/api/videos/aa6n0tuxqz1h/embed/playback?t=x")
            == "/api/videos/<id>/embed/playback"
        )
        assert (
            shape(
                "https://edge.example.com/hls2/01/04711/abcdefghijkl_h/master.m3u8?t=a"
            )
            == "/hls2/<id>/<id>/<id>/master.m3u8"
        )
        # A twelve-letter id without a digit is masked by its length
        assert shape("https://filemoon.example/e/abcdefghijkl") == "/e/<id>"
        assert (
            shape("https://cdn.example/assets/jwplayer.core.controls.js")
            == "/assets/jwplayer.core.controls.js"
        )
        assert shape("https://byse.example/") == "/"

    def test_the_domain_hides_addresses(self) -> None:
        assert _mod.domain("https://a.b.example.com/x") == "example.com"
        assert _mod.domain("https://203.0.113.7/x") == "<ip>"
        assert _mod.domain("https://[2001:db8::1]/x") == "<ip>"
        assert _mod.domain("blob:https://byse.example/1f2e") == "blob"


class TestRequests:
    def test_each_request_is_noted_with_its_outcome(self) -> None:
        page, run = _Page(), _mod.Run(0.0)
        _mod.watch_requests(page, run)
        document = _Request("https://byse.example/d/abc123def456", "document")
        playback = _Request(
            "https://byse.example/api/videos/aa6n0tuxqz1h/embed/playback"
        )
        media = _Request(
            "https://edge.cdn.example.com/a1b2c3d4/master.m3u8",
            "media",
            failure="net::ERR_FAILED",
        )
        attest = _Request("https://byse.example/api/videos/access/attest", "xhr")
        for request in (document, playback, media, attest):
            page.emit("request", request)
        page.emit("request", _Request("data:image/png;base64,AAAA", "image"))
        page.emit(
            "request", _Request("chrome-extension://mhjfbmdgcfjb/main.js", "script")
        )
        page.emit("response", _Response(document, 200))
        page.emit("response", _Response(playback, 403))
        page.emit("requestfailed", media)

        assert [(n.domain, n.shape, n.kind, n.outcome) for n in run.requests] == [
            ("byse.example", "/d/<id>", "document", "200"),
            ("byse.example", "/api/videos/<id>/embed/playback", "fetch", "403"),
            ("example.com", "/<id>/master.m3u8", "media", "net::ERR_FAILED"),
            ("byse.example", "/api/videos/access/attest", "xhr", "pending"),
        ]

    def test_a_redirect_marks_its_hop(self) -> None:
        page, run = _Page(), _mod.Run(0.0)
        _mod.watch_requests(page, run)
        first = _Request("https://a.example/go", "document")
        second = _Request(
            "https://b.example/d/abc123def456", "document", redirected_from=first
        )
        page.emit("request", first)
        page.emit("request", second)
        page.emit("response", _Response(second, 200))

        assert [n.outcome for n in run.requests] == ["redirect", "200"]

    def test_the_lines_count_alike_requests_and_sum_up_the_quiet_kinds(self) -> None:
        note = _mod.RequestNote
        run = _mod.Run(0.0)
        run.requests.extend(
            [
                note("byse.example", "/api/videos/<id>/embed/playback", "fetch", "403"),
                note("ads.example", "/<id>.png", "image", "200"),
                note("byse.example", "/api/videos/<id>/embed/playback", "fetch", "403"),
                note("fonts.example", "/<id>.woff2", "font", "200"),
            ]
        )

        assert _mod.request_lines(run) == [
            "- byse.example /api/videos/<id>/embed/playback fetch: 403 ×2",
            "- also 1 font, 1 image",
        ]


class TestFrames:
    async def test_each_frame_says_what_it_can_play(self) -> None:
        page = _Page(
            [
                _Frame("https://byse.example/d/abc", _plays()),
                _Frame(
                    "https://player.example.net/e/abc",
                    _plays(video=True, error=4, network=3, h264=False, hls="maybe"),
                ),
                _Frame("about:blank", _plays()),
                _Frame("https://ads.example.org/x", error=RuntimeError("detached")),
            ]
        )

        notes = await _mod.frame_notes(page)

        assert [_mod.frame_line(n) for n in notes] == [
            "- page byse.example: no video element; MediaSource H.264 yes,"
            " AAC yes; canPlayType HLS no",
            "- frame example.net: video element, error 4, network state 3;"
            " MediaSource H.264 no, AAC yes; canPlayType HLS maybe",
            "- frame example.org: unreadable",
        ]

    async def test_the_frames_are_read_after_the_clicks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def start_player(*_args: object) -> None:
            return None

        # restores the module's own function after the test
        monkeypatch.setattr(stealth, "_start_player", start_player)
        pool = _mod.TimedStealthPool(browser_pool=MagicMock())
        pool.diagnose = True
        pool.run = _mod.Run(time.monotonic())
        _mod.note_frames(pool)
        page = _Page([_Frame("https://byse.example/d/abc", _plays())])

        assert await stealth._start_player(page, MagicMock(), None) is None
        assert [n.domain for n in pool.run.frames] == ["byse.example"]

    async def test_without_diagnose_no_frame_is_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def start_player(*_args: object) -> None:
            return None

        monkeypatch.setattr(stealth, "_start_player", start_player)
        pool = _mod.TimedStealthPool(browser_pool=MagicMock())
        pool.run = _mod.Run(time.monotonic())
        _mod.note_frames(pool)

        await stealth._start_player(
            _Page([_Frame("https://byse.example/d/abc")]), None, None
        )

        assert pool.run.frames == []


class TestDiagnosedPool:
    async def test_a_page_is_watched_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def navigate(*_args: object, **_kwargs: object) -> bool:
            return True

        monkeypatch.setattr(StealthPool, "_navigate", navigate)
        pool = _mod.TimedStealthPool(browser_pool=MagicMock())
        pool.diagnose = True
        pool.run = _mod.Run(time.monotonic())
        page = _Page()

        for url in ("https://filemoon.example/e/abc", "https://byse.example/d/abc"):
            await pool._navigate(
                page, url, wait_until="domcontentloaded", timeout_ms=1000
            )

        assert len(page.handlers["request"]) == 1
        assert len(page.handlers["popup"]) == 1

    def test_the_diagnosis_of_a_run(self) -> None:
        run = _mod.Run(0.0)
        run.requests.append(
            _mod.RequestNote("byse.example", "/d/<id>", "document", "200")
        )
        run.popups = 2
        run.frames.append(_mod.FrameNote("byse.example", main=True))

        assert _mod.diagnosis_lines(1, run) == [
            "### Run 1: requests (domain, path with ids masked, type: outcome)",
            "- byse.example /d/<id> document: 200",
            "popups opened: 2 (the capture closes them)",
            "frames after the clicks:",
            "- page byse.example: unreadable",
        ]


class TestPopups:
    def test_the_popups_of_a_page_are_counted(self) -> None:
        page, run = _Page(), _mod.Run(0.0)
        _mod.count_popups(page, run)

        page.emit("popup", object())
        page.emit("popup", object())

        assert run.popups == 2


class _Context:
    """Stands in for a browser context: the routes set on it."""

    def __init__(self) -> None:
        self.routes: list[tuple[Callable[[str], bool], object]] = []

    async def route(self, url: Callable[[str], bool], handler: object) -> None:
        self.routes.append((url, handler))


class _RoutedPage(_Page):
    """A page with routes, in *context*."""

    def __init__(self, context: _Context) -> None:
        super().__init__()
        self.context = context
        self.routes: list[tuple[Callable[[str], bool], object]] = []

    async def route(self, url: Callable[[str], bool], handler: object) -> None:
        self.routes.append((url, handler))


class _Route:
    def __init__(self) -> None:
        self.aborted: list[str | None] = []

    async def abort(self, error_code: str | None = None) -> None:
        self.aborted.append(error_code)


class TestBlocks:
    def test_a_domain_and_its_subdomains_match(self) -> None:
        matches = _mod.blocked_by(["llvpn.com", "Rtmark.net."])

        assert matches("https://llvpn.com/tag.min.js")
        assert matches("https://cdn.LLVPN.com/tag.min.js")
        assert matches("https://rtmark.net/gid.js")
        assert not matches("https://notllvpn.com/tag.min.js")
        assert not matches("https://llvpn.com.example/tag.min.js")
        assert not matches("https://n1mwq.org/player/abc/bafsd.js")
        assert not matches("data:text/plain,llvpn.com")

    async def test_a_block_aborts_as_the_client(self) -> None:
        route = _Route()

        await _mod.block_request(route)

        assert route.aborted == ["blockedbyclient"]

    def test_a_blocked_request_reads_blocked(self) -> None:
        page, run = _Page(), _mod.Run(0.0)
        _mod.watch_requests(page, run)
        tag = _Request(
            "https://llvpn.com/tag.min.js",
            "script",
            failure="net::ERR_BLOCKED_BY_CLIENT.Inspector",
        )
        page.emit("request", tag)
        page.emit("requestfailed", tag)

        assert [n.outcome for n in run.requests] == ["blocked"]

    async def test_each_page_and_its_context_get_the_blocks_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def navigate(*_args: object, **_kwargs: object) -> bool:
            return True

        monkeypatch.setattr(StealthPool, "_navigate", navigate)
        pool = _mod.TimedStealthPool(browser_pool=MagicMock())
        pool.blocked = ("llvpn.com",)
        context = _Context()
        first, second = _RoutedPage(context), _RoutedPage(context)

        for page in (first, first, second):
            await pool._navigate(
                page, "https://byse.example/d/abc", wait_until="commit", timeout_ms=1
            )

        assert [len(first.routes), len(second.routes), len(context.routes)] == [
            1,
            1,
            1,
        ]
        matches, handler = context.routes[0]
        assert matches("https://cdn.llvpn.com/tag.min.js")
        assert not matches("https://byse.example/d/abc")
        assert handler is _mod.block_request
        assert first.routes[0][1] is _mod.block_request

    async def test_without_blocks_nothing_is_routed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def navigate(*_args: object, **_kwargs: object) -> bool:
            return True

        monkeypatch.setattr(StealthPool, "_navigate", navigate)
        pool = _mod.TimedStealthPool(browser_pool=MagicMock())
        context = _Context()
        page = _RoutedPage(context)

        await pool._navigate(
            page, "https://byse.example/d/abc", wait_until="commit", timeout_ms=1
        )

        assert (page.routes, context.routes) == ([], [])
