"""The stall monitor and the stuck-request check (OpenSpec
add-stall-watchdog): a thread outside the event loop that names a stalled
loop and, with its switch on, ends the process, so that the container's
restart policy restarts it, and a task on the loop that names a request
running past its route class's limit.

``monitor_loop_lag``'s tick stamps the heartbeat (``StallMonitor.beat``,
every 0.5 s), and the monitor's thread reads it every 5 s. A heartbeat older
than the limit is a stall: ``event_loop_stalled`` once, with its age and the
loop thread's stack, and every thread's traceback on standard error
(``faulthandler`` writes it from C, past the logging path). When the loop
ticks again, ``event_loop_recovered`` names the stall's length. A
``faulthandler`` timer at twice the limit, armed again at every check of the
thread, dumps the tracebacks of a stall in which no Python thread runs (a C
call that keeps the interpreter). With the switch on and an exit left in the
budget it ends the process too, with ``faulthandler``'s code 1, which the
budget does not count: C code cannot write its file. A failed check and the
thread's end drop the timer, so it never fires on a healthy loop.

With ``exit_on_stall`` the monitor ends the process with code 3 after the
line and the dump. Nothing on the way waits on a lock or a write the stalled
thread may hold: the line, the dump and the log's flush each get a bounded
wait in a thread of their own, and the exit follows regardless.
``ExitBudget`` ends a restart loop: after 3 exits within 30 min a stall is
only logged.

In a stuck request the loop still runs: ``watch_requests`` reads the
requests in flight that the request watch middleware notes in
``GracefulShutdown`` every 30 s, and logs ``request_stuck`` once for each one
past its route class's limit, with its task's await chain.
"""

from __future__ import annotations

import asyncio
import faulthandler
import json
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import structlog

from scavengarr.domain.ports.telemetry import RouteClass, TelemetryPort
from scavengarr.infrastructure.graceful_shutdown import (
    GracefulShutdown,
    WatchedRequest,
)
from scavengarr.infrastructure.logging.setup import stop_async_logging

log = structlog.get_logger(__name__)

# The thread's check, and its poll while a stall lasts: the loop lag
# monitor's tick, so the stall's end is seen within one tick
CHECK_INTERVAL_S = 5.0
_STALL_POLL_S = 0.5
# The exit's code, and the bounded wait for each of the line, the dump and
# the log's flush
EXIT_CODE = 3
IO_WAIT_S = 2.0
# At most EXIT_BUDGET exits within BUDGET_WINDOW_S, counted in BUDGET_FILE
# of the cache directory (a restart of the container keeps it)
EXIT_BUDGET = 3
BUDGET_WINDOW_S = 1800.0
BUDGET_FILE = "watchdog-exits.json"
# The stack in the structured line: its innermost part
_STACK_CHARS = 4000
# The stuck-request check, every 30 s on the loop, and each route class's
# limit: a Stremio request answers by its deadline (60 s), a Torznab search
# has no overall bound
STUCK_CHECK_S = 30.0
STUCK_LIMITS: dict[RouteClass, float] = {
    "stremio_stream": 300.0,
    "stremio_catalog": 300.0,
    "torznab": 600.0,
    "other": 300.0,
}
# The await chain's depth a stuck request's stack follows at most
_CHAIN_DEPTH = 200


@dataclass(frozen=True, slots=True)
class Check:
    """One check's verdict: ``age_s`` names a new stall (the heartbeat's
    age), ``length_s`` the end of one; ``stall`` is the last heartbeat
    before the stall still in progress."""

    age_s: float | None = None
    length_s: float | None = None
    stall: float | None = None


def check(beat: float, now: float, limit: float, stall: float | None) -> Check:
    """The monitor's decision at *now* (monotonic seconds).

    A heartbeat (*beat*) older than *limit* starts a stall, named once. A
    heartbeat after the stall's last one (*stall*) ends it, and its length
    runs from that one to this one.
    """
    if stall is not None:
        if beat > stall:
            return Check(length_s=beat - stall)
        return Check(stall=stall)
    if now - beat >= limit:
        return Check(age_s=now - beat, stall=beat)
    return Check()


class ExitBudget:
    """The monitor's exits of the last 30 min, in a small JSON file.

    A missing or corrupt file counts as empty. Only the monitor's thread
    reads and writes it, at a stall and, with the switch on, at its checks.
    """

    def __init__(
        self,
        path: Path,
        *,
        exits: int = EXIT_BUDGET,
        window_s: float = BUDGET_WINDOW_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._exits = exits
        self._window = window_s
        self._clock = clock

    def allows(self) -> bool:
        """Whether an exit is left in the window."""
        return len(self._recent(self._clock())) < self._exits

    def record(self) -> None:
        """Count an exit now; ``OSError`` when the file cannot be written."""
        now = self._clock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"exits": [*self._recent(now), now]}))

    def _recent(self, now: float) -> list[float]:
        return [t for t in self._read() if now - self._window < t <= now]

    def _read(self) -> list[float]:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return []
        exits = data.get("exits") if isinstance(data, dict) else None
        if not isinstance(exits, list):
            return []
        if not all(isinstance(t, int | float) for t in exits):
            return []
        return [float(t) for t in exits]


def _flush_logs() -> None:
    """Write the queued log lines and the streams' buffers (before an exit
    that skips the interpreter's own shutdown)."""
    stop_async_logging()
    sys.stdout.flush()
    sys.stderr.flush()


def _bounded(call: Callable[[], object], seconds: float) -> None:
    """Run *call* in a thread of its own and wait at most *seconds* for it."""
    worker = threading.Thread(target=call, name="stall-monitor-io", daemon=True)
    worker.start()
    worker.join(seconds)


class StallMonitor:
    """The thread that watches the event loop's heartbeat (see the module)."""

    def __init__(
        self,
        *,
        stall_seconds: float,
        exit_on_stall: bool,
        budget: ExitBudget,
        check_interval: float = CHECK_INTERVAL_S,
        io_wait_s: float = IO_WAIT_S,
        backstop: bool = True,
        dump_file: TextIO | None = None,
        flush: Callable[[], object] = _flush_logs,
        end_process: Callable[[int], object] = os._exit,
    ) -> None:
        self._limit = stall_seconds
        self._exit_on_stall = exit_on_stall
        self._budget = budget
        self._interval = check_interval
        self._io_wait = io_wait_s
        self._backstop = backstop
        self._dump_file = dump_file
        self._flush = flush
        self._end_process = end_process
        self._beat = time.monotonic()
        self._stop = threading.Event()
        self._loop_thread: int | None = None

    def beat(self) -> None:
        """Stamp the loop's tick (``monitor_loop_lag``, every 0.5 s)."""
        self._beat = time.monotonic()

    def start(self) -> bool:
        """Start the thread, on the loop's thread once the app is ready.

        Returns False with a limit of 0, which starts no thread.
        """
        if self._limit <= 0:
            log.info("stall_monitor_off")
            return False
        self._loop_thread = threading.get_ident()
        # The startup's synchronous steps held the loop: no stall
        self._beat = time.monotonic()
        threading.Thread(target=self._run, name="stall-monitor", daemon=True).start()
        log.info(
            "stall_monitor_started",
            stall_seconds=self._limit,
            exit_on_stall=self._exit_on_stall,
            backstop_seconds=2 * self._limit if self._backstop else None,
        )
        return True

    def stop(self) -> None:
        """End the thread and its timer: first at the shutdown, whose bounded
        waits hold the loop as no stall."""
        self._stop.set()
        if self._loop_thread is None:
            return
        self._disarm()
        log.info("stall_monitor_stopped")

    def _run(self) -> None:
        stall: float | None = None
        wait = 0.0
        try:
            while not self._stop.wait(wait):
                try:
                    stall = self._step(stall)
                except Exception:
                    # A timer the failed check left would dump a healthy loop
                    self._disarm()
                    log.exception("stall_monitor_check_failed")
                wait = self._interval if stall is None else _STALL_POLL_S
                wait = min(wait, self._interval)
        finally:
            # A thread that ended leaves no timer to fire on a healthy loop
            self._disarm()

    def _step(self, stall: float | None) -> float | None:
        """One check: the timer armed again, then a stall or its end named."""
        self._arm()
        verdict = check(self._beat, time.monotonic(), self._limit, stall)
        if verdict.age_s is not None:
            self._stalled(verdict.age_s)
        elif verdict.length_s is not None:
            log.warning("event_loop_recovered", length_s=round(verdict.length_s, 1))
        return verdict.stall

    def _arm(self) -> None:
        """The backstop: dump at twice the limit (and with the switch, exit)
        unless this thread arms it again first."""
        if self._backstop:
            faulthandler.dump_traceback_later(
                2 * self._limit,
                exit=self._exit_on_stall and self._budget.allows(),
                file=self._dump_to(),
            )

    def _disarm(self) -> None:
        if self._backstop:
            faulthandler.cancel_dump_traceback_later()

    def _stalled(self, age: float) -> None:
        """Name the stall and dump every thread; with the switch on and an
        exit left in the budget, end the process."""
        exiting = self._exit_on_stall and self._budget.allows()
        fields: dict[str, object] = {
            "age_s": round(age, 1),
            "limit_s": self._limit,
            "exit": "on" if exiting else "skipped" if self._exit_on_stall else "off",
            "stack": self._loop_stack(),
        }
        if exiting:
            try:
                self._budget.record()
            except OSError as exc:
                # The next start counts one exit less; the exit still follows
                fields["budget_error"] = type(exc).__name__
        _bounded(lambda: log.error("event_loop_stalled", **fields), self._io_wait)
        _bounded(
            lambda: faulthandler.dump_traceback(file=self._dump_to(), all_threads=True),
            self._io_wait,
        )
        if exiting:
            _bounded(self._flush, self._io_wait)
            self._end_process(EXIT_CODE)

    def _loop_stack(self) -> str:
        """The loop thread's stack, its innermost part."""
        frame = sys._current_frames().get(self._loop_thread or 0)
        if frame is None:
            return ""
        return "".join(traceback.format_stack(frame))[-_STACK_CHARS:]

    def _dump_to(self) -> TextIO:
        return self._dump_file if self._dump_file is not None else sys.stderr


def await_stack(task: asyncio.Task[object]) -> str:
    """*task*'s await chain as stack lines, outermost first: where each
    coroutine of the chain waits, down to the innermost wait.

    ``Task.get_stack()`` holds only the outermost frame of a suspended
    coroutine. The lines name no source: it would be read from disk on the
    loop. The chain ends at a future, another task's included.
    """
    lines: list[str] = []
    awaited: object = task.get_coro()
    for _ in range(_CHAIN_DEPTH):
        frame = getattr(awaited, "cr_frame", None) or getattr(awaited, "gi_frame", None)
        if frame is None:
            break
        code = frame.f_code
        lines.append(
            f'  File "{code.co_filename}", line {frame.f_lineno}, in {code.co_name}'
        )
        awaited = getattr(awaited, "cr_await", None) or getattr(
            awaited, "gi_yieldfrom", None
        )
    return "\n".join(lines)[-_STACK_CHARS:]


def report_stuck(
    requests: Iterable[WatchedRequest], now: float, telemetry: TelemetryPort
) -> None:
    """Log and count each request past its route class's limit, once."""
    for request in requests:
        age = now - request.started
        if request.reported or age < STUCK_LIMITS[request.route]:
            continue
        request.reported = True
        log.warning(
            "request_stuck",
            route=request.route,
            age_s=round(age, 1),
            request_id=request.request_id,
            stack=await_stack(request.task),
        )
        telemetry.record_request_stuck(request.route)


async def watch_requests(
    shutdown: GracefulShutdown,
    telemetry: TelemetryPort,
    *,
    interval: float = STUCK_CHECK_S,
) -> None:
    """The stuck-request check every *interval* seconds, on the loop: in a
    stuck request the loop still runs, and the tasks' coroutines are read on
    the loop's own thread."""
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(interval)
        report_stuck(shutdown.watched_requests, loop.time(), telemetry)
