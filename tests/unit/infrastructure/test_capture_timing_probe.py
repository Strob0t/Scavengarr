"""Tests for the pre-checks, process cleanup, timing and report of
scripts/probes/capture_timing.py."""

from __future__ import annotations

import importlib.util
import os
import signal
import sys
import time
from pathlib import Path
from types import ModuleType
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
        assert _mod.cdn("https://edge-1.cdn.example.com/hls/master.m3u8") == (
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

    assert (args.hoster, args.runs, args.timeout) == ("filemoon", 2, None)
