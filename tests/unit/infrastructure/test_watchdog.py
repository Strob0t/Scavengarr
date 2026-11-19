"""Tests for the stall monitor and the stuck-request check (OpenSpec
add-stall-watchdog, groups 2 and 3): the decision with a fake clock, the
thread, the exit switch and its budget, subprocesses whose event loop a
blocking call holds, and the check of the requests in flight."""

from __future__ import annotations

import asyncio
import faulthandler
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, TextIO, get_args

import pytest
from structlog.testing import capture_logs

from scavengarr.domain.ports.telemetry import RouteClass
from scavengarr.infrastructure.graceful_shutdown import (
    GracefulShutdown,
    WatchedRequest,
)
from scavengarr.infrastructure.telemetry import Telemetry, watchdog
from scavengarr.infrastructure.telemetry.watchdog import (
    EXIT_CODE,
    STUCK_LIMITS,
    Check,
    ExitBudget,
    StallMonitor,
    await_stack,
    check,
    report_stuck,
    watch_requests,
)

from ..waiting import eventually, until

# The subprocesses import this checkout's code, not the venv's install
_SRC = Path(__file__).resolve().parents[3] / "src"


class TestCheck:
    """The decision as a pure function of the heartbeat, the clock, the
    limit and the stall in progress."""

    def test_no_stall_below_the_limit(self) -> None:
        assert check(100.0, 219.9, 120.0, None) == Check()

    def test_a_stall_is_named_once_with_its_age(self) -> None:
        first = check(100.0, 220.5, 120.0, None)

        assert first == Check(age_s=120.5, stall=100.0)
        assert check(100.0, 225.5, 120.0, first.stall) == Check(stall=100.0)

    def test_the_recovery_names_the_length_and_the_next_stall_counts(self) -> None:
        recovered = check(230.0, 230.2, 120.0, 100.0)

        assert recovered == Check(length_s=130.0)
        assert check(230.0, 350.5, 120.0, recovered.stall) == Check(
            age_s=120.5, stall=230.0
        )


class TestExitBudget:
    def test_a_missing_file_allows_an_exit(self, tmp_path: Path) -> None:
        assert ExitBudget(tmp_path / "exits.json").allows()

    def test_three_exits_in_30_min_spend_it(self, tmp_path: Path) -> None:
        now = [10_000.0]
        budget = ExitBudget(tmp_path / "exits.json", clock=lambda: now[0])
        for _ in range(3):
            budget.record()
            now[0] += 60

        assert not budget.allows()
        now[0] += 1800
        assert budget.allows()

    def test_the_file_outlives_the_process(self, tmp_path: Path) -> None:
        for _ in range(3):
            ExitBudget(tmp_path / "exits.json").record()

        assert not ExitBudget(tmp_path / "exits.json").allows()

    @pytest.mark.parametrize(
        "content",
        ["{not json", '{"exits": "x"}', "[1, 2, 3]", '{"exits": [1, "a"]}', ""],
        ids=["no-json", "no-list", "no-object", "no-number", "empty"],
    )
    def test_a_corrupt_file_counts_as_empty(self, tmp_path: Path, content: str) -> None:
        path = tmp_path / "exits.json"
        path.write_text(content)
        budget = ExitBudget(path)

        assert budget.allows()
        budget.record()
        assert len(json.loads(path.read_text())["exits"]) == 1


@pytest.fixture
def dump(tmp_path: Path) -> Iterator[TextIO]:
    with (tmp_path / "dump.txt").open("w") as file:
        yield file


def _monitor(tmp_path: Path, dump: TextIO, **changes: Any) -> StallMonitor:
    """A monitor of short intervals, without the process-wide faulthandler
    timer: the subprocess tests prove that one."""
    options: dict[str, Any] = {
        "stall_seconds": 0.2,
        "exit_on_stall": False,
        "budget": ExitBudget(tmp_path / "exits.json"),
        "check_interval": 0.02,
        "io_wait_s": 1.0,
        "backstop": False,
        "dump_file": dump,
    }
    return StallMonitor(**(options | changes))


def _hold_the_loop(seconds: float) -> None:
    """The monitored thread does not beat: a blocking call holds it."""
    time.sleep(seconds)


def _events(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == event]


@pytest.fixture
def timer(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The faulthandler timer's arming, "to exit" when it would end the
    process, and its cancelling, recorded instead."""
    calls: list[str] = []

    def _arm(*_args: object, **kwargs: object) -> None:
        calls.append("armed to exit" if kwargs.get("exit") else "armed")

    monkeypatch.setattr(faulthandler, "dump_traceback_later", _arm)
    monkeypatch.setattr(
        faulthandler, "cancel_dump_traceback_later", lambda: calls.append("cancelled")
    )
    return calls


def _thread_ended() -> bool:
    return "stall-monitor" not in {t.name for t in threading.enumerate()}


class TestThread:
    def test_a_limit_of_zero_starts_no_thread(self, tmp_path: Path) -> None:
        monitor = StallMonitor(
            stall_seconds=0, exit_on_stall=False, budget=ExitBudget(tmp_path / "x")
        )

        assert monitor.start() is False
        assert "stall-monitor" not in {t.name for t in threading.enumerate()}

    def test_a_late_but_beating_loop_is_no_stall(
        self, tmp_path: Path, dump: TextIO
    ) -> None:
        monitor = _monitor(tmp_path, dump)
        with capture_logs() as logs:
            assert monitor.start()
            for _ in range(10):  # every tick late, each within the limit
                _hold_the_loop(0.05)
                monitor.beat()
            monitor.stop()

        assert not _events(logs, "event_loop_stalled")

    def test_a_stall_is_named_once_and_its_end_too(
        self, tmp_path: Path, dump: TextIO
    ) -> None:
        monitor = _monitor(tmp_path, dump)
        with capture_logs() as logs:
            monitor.start()
            for stalls in (1, 2):
                _hold_the_loop(0.5)
                monitor.beat()
                until(lambda: len(_events(logs, "event_loop_recovered")) == stalls)
            monitor.stop()

        stalled = _events(logs, "event_loop_stalled")
        assert len(stalled) == 2
        assert all(entry["age_s"] >= 0.2 for entry in stalled)
        assert all(entry["exit"] == "off" for entry in stalled)
        assert "_hold_the_loop" in stalled[0]["stack"]
        recovered = _events(logs, "event_loop_recovered")
        assert all(entry["length_s"] >= 0.5 for entry in recovered)
        assert "most recent call first" in Path(dump.name).read_text()

    def test_an_old_heartbeat_before_the_start_is_no_stall(
        self, tmp_path: Path, dump: TextIO, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The startup's synchronous steps held the loop before the start, past
        the limit: the first check counts from the start."""
        verdicts: list[Check] = []

        def _recorded(*args: Any) -> Check:
            verdicts.append(check(*args))
            return verdicts[-1]

        monkeypatch.setattr(watchdog, "check", _recorded)
        monitor = _monitor(tmp_path, dump, check_interval=5.0)
        _hold_the_loop(0.3)
        monitor.start()
        until(lambda: bool(verdicts))
        monitor.stop()
        until(_thread_ended)

        assert verdicts == [Check()]

    def test_stop_ends_the_thread_and_its_timer(
        self, tmp_path: Path, dump: TextIO, timer: list[str]
    ) -> None:
        """A timer left armed would dump a healthy loop at twice the limit."""
        monitor = _monitor(tmp_path, dump, backstop=True)
        monitor.start()
        until(lambda: "armed" in timer)
        monitor.stop()

        until(_thread_ended)
        assert timer[-1] == "cancelled"

    def test_a_check_that_raises_leaves_the_thread_and_its_timer(
        self,
        tmp_path: Path,
        dump: TextIO,
        timer: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The failed check drops the timer it may have left; the next check
        arms it again."""
        failures = [RuntimeError("boom")]

        def _fails_once(*args: Any) -> Check:
            if failures:
                raise failures.pop()
            return check(*args)

        monkeypatch.setattr(watchdog, "check", _fails_once)
        monitor = _monitor(tmp_path, dump, backstop=True)
        with capture_logs() as logs:
            monitor.start()
            until(lambda: timer.count("armed") >= 3)
            monitor.stop()
        until(_thread_ended)

        assert len(_events(logs, "stall_monitor_check_failed")) == 1
        assert timer[:3] == ["armed", "cancelled", "armed"]
        assert timer[-1] == "cancelled"


class TestExitSwitch:
    @pytest.mark.parametrize(
        ("exits", "armed"),
        [(2, "armed to exit"), (3, "armed")],
        ids=["an-exit-left", "budget-spent"],
    )
    def test_the_timer_exits_only_with_an_exit_left(
        self, tmp_path: Path, dump: TextIO, timer: list[str], exits: int, armed: str
    ) -> None:
        """A spent budget must not exit through the backstop either: that is
        the restart loop the budget ends."""
        now = time.time()
        (tmp_path / "exits.json").write_text(
            json.dumps({"exits": [now - 60 * (n + 1) for n in range(exits)]})
        )
        monitor = _monitor(
            tmp_path,
            dump,
            exit_on_stall=True,
            backstop=True,
            flush=lambda: None,
            end_process=lambda _code: None,
        )
        monitor.start()
        until(lambda: bool(timer))
        monitor.stop()
        until(_thread_ended)

        assert timer[0] == armed

    def test_on_a_stall_ends_the_process_with_code_3(
        self, tmp_path: Path, dump: TextIO
    ) -> None:
        calls: list[str] = []
        monitor = _monitor(
            tmp_path,
            dump,
            exit_on_stall=True,
            flush=lambda: calls.append("flush"),
            end_process=lambda code: calls.append(f"exit {code}"),
        )
        with capture_logs() as logs:
            monitor.start()
            _hold_the_loop(0.4)
            until(lambda: f"exit {EXIT_CODE}" in calls)
            monitor.stop()

        [stalled] = _events(logs, "event_loop_stalled")
        assert stalled["exit"] == "on"
        assert calls == ["flush", f"exit {EXIT_CODE}"]
        assert "most recent call first" in Path(dump.name).read_text()
        exits = json.loads((tmp_path / "exits.json").read_text())["exits"]
        assert len(exits) == 1

    def test_the_fourth_stall_in_30_min_only_logs(
        self, tmp_path: Path, dump: TextIO
    ) -> None:
        now = time.time()
        (tmp_path / "exits.json").write_text(
            json.dumps({"exits": [now - 900, now - 600, now - 60]})
        )
        calls: list[int] = []
        monitor = _monitor(
            tmp_path,
            dump,
            exit_on_stall=True,
            flush=lambda: None,
            end_process=calls.append,
        )
        with capture_logs() as logs:
            monitor.start()
            _hold_the_loop(0.4)
            monitor.beat()
            until(lambda: bool(_events(logs, "event_loop_recovered")))
            monitor.stop()

        [stalled] = _events(logs, "event_loop_stalled")
        assert stalled["exit"] == "skipped"
        assert calls == []

    def test_a_blocked_line_and_flush_do_not_hold_the_exit(
        self, tmp_path: Path, dump: TextIO, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lock the stalled thread holds in the logging path: the line and
        the flush wait a bounded time, the exit follows regardless."""
        release = threading.Event()

        class _BlockedLog:
            def info(self, *_args: object, **_kwargs: object) -> None:
                return None

            def error(self, *_args: object, **_kwargs: object) -> None:
                release.wait()

        monkeypatch.setattr(watchdog, "log", _BlockedLog())
        calls: list[int] = []
        monitor = _monitor(
            tmp_path,
            dump,
            exit_on_stall=True,
            io_wait_s=0.1,
            flush=release.wait,
            end_process=calls.append,
        )
        try:
            monitor.start()
            _hold_the_loop(0.3)
            until(lambda: calls == [EXIT_CODE])
        finally:
            release.set()
            monitor.stop()


# A loop that a blocking call holds past the limit, in a process of its own:
# argv[1] is the mode (off, exit, gil), argv[2] the budget file
_SCRIPT = """
import asyncio
import ctypes
import sys
import time
from pathlib import Path

from scavengarr.infrastructure.config import AppConfig
from scavengarr.infrastructure.logging.setup import configure_logging
from scavengarr.infrastructure.telemetry import Telemetry, monitor_loop_lag
from scavengarr.infrastructure.telemetry.watchdog import ExitBudget, StallMonitor

mode, budget = sys.argv[1], Path(sys.argv[2])
configure_logging(AppConfig(environment="prod"))
monitor = StallMonitor(
    stall_seconds=0.5,
    exit_on_stall=mode == "exit",
    budget=ExitBudget(budget),
    check_interval=0.1,
)


def _block() -> None:
    if mode == "gil":
        ctypes.PyDLL(None).sleep(2)  # a C call that keeps the interpreter
    else:
        time.sleep(1.5)


async def main() -> None:
    lag = asyncio.create_task(
        monitor_loop_lag(Telemetry(), interval=0.05, heartbeat=monitor.beat)
    )
    monitor.start()
    await asyncio.sleep(0.3)
    _block()
    await asyncio.sleep(1.0)
    monitor.stop()
    lag.cancel()


asyncio.run(main())
"""


def _run(tmp_path: Path, mode: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "stall.py"
    script.write_text(_SCRIPT)
    return subprocess.run(
        [sys.executable, str(script), mode, str(tmp_path / "exits.json")],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(_SRC)},
        check=False,
    )


def _logged(done: subprocess.CompletedProcess[str], event: str) -> list[dict[str, Any]]:
    """The JSON records of *event*: INFO and WARNING on stdout, ERROR on stderr
    beside the tracebacks."""
    records = []
    for line in (done.stdout + done.stderr).splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("event") == event:
            records.append(record)
    return records


class TestBlockedLoop:
    def test_a_blocked_loop_is_named_and_the_process_lives_on(
        self, tmp_path: Path
    ) -> None:
        done = _run(tmp_path, "off")

        assert done.returncode == 0, done.stderr[-2000:]
        [stalled] = _logged(done, "event_loop_stalled")
        assert stalled["exit"] == "off"
        assert "_block" in stalled["stack"]
        [recovered] = _logged(done, "event_loop_recovered")
        assert recovered["length_s"] >= 1.0
        assert "in _block" in done.stderr
        # One dump, the monitor's: its checks keep the timer from firing
        assert done.stderr.count("Current thread ") == 1
        assert "Timeout (" not in done.stderr

    def test_the_switch_ends_the_process_with_code_3_after_the_dump(
        self, tmp_path: Path
    ) -> None:
        done = _run(tmp_path, "exit")

        assert done.returncode == EXIT_CODE, done.stderr[-2000:]
        [stalled] = _logged(done, "event_loop_stalled")
        assert stalled["exit"] == "on"
        assert "in _block" in done.stderr
        exits = json.loads((tmp_path / "exits.json").read_text())["exits"]
        assert len(exits) == 1

    @pytest.mark.skipif(
        sys.platform != "linux", reason="ctypes.PyDLL(None) resolves libc's sleep"
    )
    def test_a_held_interpreter_is_dumped_by_the_timer(self, tmp_path: Path) -> None:
        """No Python thread runs while a C call keeps the interpreter:
        faulthandler's timer at twice the limit dumps from C."""
        done = _run(tmp_path, "gil")

        assert done.returncode == 0, done.stderr[-2000:]
        assert "Timeout (0:00:01)!" in done.stderr


async def _route_waits(release: asyncio.Event) -> None:
    await release.wait()


async def _outer(release: asyncio.Event) -> None:
    await _route_waits(release)


@pytest.fixture
async def waiting() -> AsyncIterator[asyncio.Task[None]]:
    """A task that waits in ``_route_waits``, as a request's task waits in its
    route."""
    release = asyncio.Event()
    task = asyncio.create_task(_outer(release))
    await asyncio.sleep(0)
    yield task
    release.set()
    await task


def _stuck(telemetry: Telemetry, route: RouteClass) -> float | None:
    return telemetry.registry.get_sample_value(
        "scavengarr_requests_stuck_total", {"route": route}
    )


class TestStuckRequests:
    """The check on the loop: a request past its route class's limit is logged
    once with its task's stack, and counted."""

    def test_each_route_class_has_its_limit(self) -> None:
        """A Stremio request answers by its deadline (60 s), a Torznab search
        has no overall bound."""
        assert STUCK_LIMITS == {
            "stremio_stream": 300.0,
            "stremio_catalog": 300.0,
            "torznab": 600.0,
            "other": 300.0,
        }
        assert set(STUCK_LIMITS) == set(get_args(RouteClass))

    async def test_a_request_past_its_limit_is_logged_once_and_counted(
        self, waiting: asyncio.Task[None]
    ) -> None:
        request = WatchedRequest(
            task=waiting, route="stremio_stream", started=0.0, request_id="ab12"
        )
        telemetry = Telemetry()
        with capture_logs() as logs:
            report_stuck([request], 300.5, telemetry)
            report_stuck([request], 400.0, telemetry)

        [stuck] = _events(logs, "request_stuck")
        assert stuck["route"] == "stremio_stream"
        assert stuck["age_s"] == 300.5
        assert stuck["request_id"] == "ab12"
        assert "in _route_waits" in stuck["stack"]
        assert _stuck(telemetry, "stremio_stream") == 1.0

    async def test_a_torznab_search_of_9_min_is_not_stuck(
        self, waiting: asyncio.Task[None]
    ) -> None:
        request = WatchedRequest(task=waiting, route="torznab", started=0.0)
        telemetry = Telemetry()
        with capture_logs() as logs:
            report_stuck([request], 599.0, telemetry)

        assert not _events(logs, "request_stuck")
        assert _stuck(telemetry, "torznab") == 0.0

    async def test_the_stack_follows_the_await_chain(
        self, waiting: asyncio.Task[None]
    ) -> None:
        """Outermost first, down to the innermost wait: ``Task.get_stack()``
        holds only the outermost frame of a suspended coroutine."""
        lines = await_stack(waiting).splitlines()

        assert "in _outer" in lines[0]
        assert "in _route_waits" in lines[1]
        assert lines[-1].endswith("in wait")  # asyncio.Event.wait

    async def test_the_check_runs_every_interval_and_logs_once(
        self, waiting: asyncio.Task[None], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shutdown = GracefulShutdown()
        started = asyncio.get_running_loop().time() - 301
        shutdown.watch_request(
            WatchedRequest(task=waiting, route="other", started=started)
        )
        checks: list[float] = []
        report = watchdog.report_stuck

        def _counted(*args: Any) -> None:
            checks.append(time.monotonic())
            report(*args)

        monkeypatch.setattr(watchdog, "report_stuck", _counted)
        with capture_logs() as logs:
            watcher = asyncio.create_task(
                watch_requests(shutdown, Telemetry(), interval=0.01)
            )
            await eventually(lambda: len(checks) >= 3)
            watcher.cancel()
            await asyncio.wait([watcher])

        assert len(_events(logs, "request_stuck")) == 1
