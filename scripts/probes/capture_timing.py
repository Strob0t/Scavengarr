"""A browser-capture resolver's capture, timed in the container.

    probe capture_timing -- URL [--hoster filemoon] [--runs 2] [--timeout S]

Resolves the hoster page *URL* (a link as the plugins store it) with the
app's resolver for ``--hoster`` (one that captures in the stealth browser:
filemoon, supervideo, doodstream, vixeo or an XFS hoster) in a browser of
the probe's own: a second Chromium on the app's display (the entrypoint's
Xvfb ``:99``), its temporary files in a directory of its own (removed at
exit), a stealth pool without a clearance store and no cache, breaker or
resolver cache, so the probe writes nothing the app reads. Before the
launch it reads the container's available memory, the free ``/dev/shm``
and whether the app's Chromium runs with ``--disable-dev-shm-usage``; it
launches nothing when memory or shared memory is short.

``--timeout`` bounds navigation and the challenge (default: production's
``stremio.probe_stealth_timeout_seconds``); the player's clicks keep the
app's waits, and the registry's bound (``http_timeout_resolve_seconds``)
is left out, so a slow capture shows its whole time. A run ends after
90 s at the latest, the whole probe after its runs' share plus a minute,
and at exit the probe kills every process it started. Per run it prints
the pages the browser loaded (the path's first segment, when the load
started, how long it took), the clicks on the player, when the stream was
captured (its CDN's domain) and the resolver's whole time, then
production's two bounds. Prints no URLs: domains, the first path segment
and times only. Run it while nothing plays: its browser shares the Pi's
CPU with the app.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
import structlog

from scavengarr.domain.ports.hoster_resolver import HosterResolverPort
from scavengarr.infrastructure.browser import stealth_pool as stealth
from scavengarr.infrastructure.browser.shared_browser import SharedBrowserPool
from scavengarr.infrastructure.browser.stealth_pool import CapturedMedia, StealthPool
from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.hoster_resolvers.doodstream import DoodStreamResolver
from scavengarr.infrastructure.hoster_resolvers.filemoon import FilemoonResolver
from scavengarr.infrastructure.hoster_resolvers.supervideo import SuperVideoResolver
from scavengarr.infrastructure.hoster_resolvers.vixeo import VixeoResolver
from scavengarr.infrastructure.hoster_resolvers.xfs import create_all_xfs_resolvers
from scavengarr.interfaces.composition import build_http_client

RUN_LIMIT_S = 90.0
MIN_AVAILABLE = 1024 * 1048576  # bytes of memory left for the second browser
MIN_SHM = 32 * 1048576  # free bytes in /dev/shm
SHM_FLAG = "--disable-dev-shm-usage"
# The browser process's executable (its renderers, helpers and the crash
# handler run under other names or with --type=)
_BROWSER_NAMES = frozenset({"chrome", "chromium", "chromium-browser", "headless_shell"})
# The entrypoint's Xvfb: the app's browser runs headful on it
_DISPLAY = ":99"
_X_SOCKET = Path("/tmp/.X11-unix/X99")
_MEM_AVAILABLE_RE = re.compile(r"MemAvailable:\s+(\d+) kB")


def path_kind(url: str) -> str:
    """The first segment of *url*'s path (``/d/``), no ids."""
    segment = urlsplit(url).path.strip("/").split("/", 1)[0]
    return f"/{segment}/" if segment else "/"


def cdn(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return ".".join(host.split(".")[-2:])


# ---------------------------------------------------------------------------
# Before the launch: memory, shared memory, the app's browser
# ---------------------------------------------------------------------------


def available_memory() -> int:
    """Bytes the container can still use: the cgroup's headroom when it has
    a limit, else the kernel's MemAvailable."""
    match = _MEM_AVAILABLE_RE.search(Path("/proc/meminfo").read_text())
    available = int(match.group(1)) * 1024 if match else 0
    limit, current = (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory.current"),
    )
    if limit.exists() and current.exists():
        cap = limit.read_text().strip()
        if cap != "max":
            available = min(available, int(cap) - int(current.read_text()))
    return available


def shm_free() -> int:
    stats = os.statvfs("/dev/shm")
    return stats.f_bavail * stats.f_frsize


def shm_flags(cmdlines: Iterable[list[str]]) -> list[bool]:
    """For each Chromium browser process among *cmdlines* (not its
    renderers and helpers, which carry ``--type=``): whether it keeps out of
    ``/dev/shm``."""
    return [
        SHM_FLAG in args
        for args in cmdlines
        if args
        and Path(args[0]).name in _BROWSER_NAMES
        and not any(arg.startswith("--type=") for arg in args)
    ]


def _cmdlines() -> list[list[str]]:
    found: list[list[str]] = []
    for proc in Path("/proc").iterdir():
        if proc.name.isdigit():
            try:
                raw = (proc / "cmdline").read_bytes()
            except OSError:
                continue
            found.append(
                [part.decode(errors="replace") for part in raw.split(b"\0") if part]
            )
    return found


def refusals(available: int, shm: int) -> list[str]:
    """Why the probe must not launch a second browser (none: it may)."""
    reasons: list[str] = []
    if available < MIN_AVAILABLE:
        reasons.append(f"memory short: {available / 1048576:.0f} MiB available")
    if shm < MIN_SHM:
        reasons.append(f"/dev/shm short: {shm / 1048576:.0f} MiB free")
    return reasons


# ---------------------------------------------------------------------------
# The processes the probe started
# ---------------------------------------------------------------------------


def descendants_of(pid: int, parent_of: Mapping[int, int]) -> list[int]:
    """The processes below *pid* in the tree *parent_of* (pid -> parent)."""
    children: dict[int, list[int]] = {}
    for child, parent in parent_of.items():
        children.setdefault(parent, []).append(child)
    found: list[int] = []
    todo = [pid]
    while todo:
        for child in sorted(children.get(todo.pop(), [])):
            found.append(child)
            todo.append(child)
    return found


def _parents() -> dict[int, int]:
    parent_of: dict[int, int] = {}
    for proc in Path("/proc").iterdir():
        if proc.name.isdigit():
            try:
                stat = (proc / "stat").read_text()
            except OSError:
                continue
            # pid (comm) state ppid ...: comm may hold spaces and parentheses
            parent_of[int(proc.name)] = int(stat.rsplit(")", 1)[1].split()[1])
    return parent_of


def kill_descendants(
    parent_of: Mapping[int, int] | None = None,
    kill: Callable[[int, int], None] = os.kill,
) -> int:
    """SIGKILL every process below the probe in the process tree; their
    count. By parent PID, never by name: the app's browser runs in the
    same container."""
    tree = _parents() if parent_of is None else parent_of
    pids = descendants_of(os.getpid(), tree)
    for pid in pids:
        try:
            kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return len(pids)


def watchdog(seconds: float, workdir: Path) -> None:
    """End the probe after *seconds* whatever it waits for: the exec that
    started it may be gone, and no browser may stay behind."""

    def fire() -> None:
        time.sleep(seconds)
        killed = kill_descendants()
        shutil.rmtree(workdir, ignore_errors=True)
        print(
            f"watchdog: the probe ran past {seconds:.0f} s, {killed} processes killed",
            flush=True,
        )
        os._exit(3)

    threading.Thread(target=fire, daemon=True).start()


# ---------------------------------------------------------------------------
# The timed capture
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageLoad:
    kind: str  # path_kind() of the page
    at: float  # seconds after the run's start
    took: float
    loaded: bool


@dataclass
class Run:
    """One resolution, its times in seconds after its start."""

    started: float
    pages: list[PageLoad] = field(default_factory=list)
    clicks: list[float] = field(default_factory=list)
    stream_at: float | None = None
    cdn: str = ""
    took: float = 0.0
    resolved: bool = False
    cut: bool = False


class TimedStealthPool(StealthPool):
    """The app's stealth pool, noting its page loads and captures in ``run``."""

    run: Run | None = None

    async def _navigate(
        self,
        page: Any,
        url: str,
        *,
        wait_until: Literal["commit", "domcontentloaded"],
        timeout_ms: int,
        done: Callable[[], bool] = lambda: False,
    ) -> bool:
        started = time.monotonic()
        loaded = await super()._navigate(
            page, url, wait_until=wait_until, timeout_ms=timeout_ms, done=done
        )
        if self.run is not None:
            self.run.pages.append(
                PageLoad(
                    path_kind(url),
                    started - self.run.started,
                    time.monotonic() - started,
                    loaded,
                )
            )
        return loaded

    async def capture_media(
        self, url: str, *, timeout: float | None = None, player: str | None = None
    ) -> CapturedMedia | None:
        media = await super().capture_media(url, timeout=timeout, player=player)
        if self.run is not None and media is not None:
            self.run.stream_at = time.monotonic() - self.run.started
            self.run.cdn = cdn(media.url)
        return media


def note_clicks(pool: TimedStealthPool) -> None:
    """Note each click on the player in ``pool.run`` (the capture looks
    ``_click_point`` up in its module at every click)."""
    click_point = stealth._click_point

    async def noted(page: Any, player: str | None) -> tuple[float, float]:
        if pool.run is not None:
            pool.run.clicks.append(time.monotonic() - pool.run.started)
        return await click_point(page, player)

    stealth._click_point = noted


def capture_resolvers(
    http: httpx.AsyncClient, pool: StealthPool
) -> dict[str, HosterResolverPort]:
    """The app's resolvers that capture in the stealth browser, by name."""
    resolvers: list[HosterResolverPort] = [
        FilemoonResolver(http_client=http, stealth_pool=pool),
        SuperVideoResolver(http_client=http, stealth_pool=pool),
        DoodStreamResolver(http_client=http, stealth_pool=pool),
        VixeoResolver(http_client=http, stealth_pool=pool),
        *create_all_xfs_resolvers(http, stealth_pool=pool),
    ]
    return {resolver.name: resolver for resolver in resolvers}


def report_row(number: int, run: Run) -> str:
    pages = ", ".join(
        f"{p.kind}: {p.at:.1f} s, {p.took:.1f} s{'' if p.loaded else ' (failed)'}"
        for p in run.pages
    )
    clicks = ", ".join(f"{at:.1f}" for at in run.clicks)
    if run.stream_at is not None:
        stream = f"{run.stream_at:.1f} s ({run.cdn})"
    else:
        stream = "none"
    took = f"cut at {run.took:.0f} s" if run.cut else f"{run.took:.1f} s"
    if not run.cut and not run.resolved:
        took += " (nothing)"
    return (
        f"| {number} | {pages or 'none'} | {clicks + ' s' if clicks else '–'}"
        f" | {stream} | {took} |"
    )


async def _close(label: str, closing: Any) -> None:
    try:
        async with asyncio.timeout(10):
            await closing
    except Exception as exc:  # noqa: BLE001
        print(f"cleanup: {label} failed ({type(exc).__name__})")


async def probe(args: argparse.Namespace) -> int:
    env = os.getenv("SCAVENGARR_CONFIG")
    config = load_config(config_path=Path(env) if env else None)
    timeout = args.timeout or config.stremio.probe_stealth_timeout_seconds
    available, shm = available_memory(), shm_free()
    flags = shm_flags(_cmdlines())
    app_browser = (
        f"{sum(flags)} of {len(flags)} with {SHM_FLAG}" if flags else "none running"
    )
    print(
        f"container: {available / 1073741824:.1f} GiB available,"
        f" /dev/shm {shm / 1048576:.0f} MiB free, the app's Chromium: {app_browser}"
    )
    if reasons := refusals(available, shm):
        print("not launched:", "; ".join(reasons))
        return 1
    if "DISPLAY" not in os.environ and _X_SOCKET.exists():
        os.environ["DISPLAY"] = _DISPLAY
    browser = SharedBrowserPool(headless=config.playwright_headless)
    pool = TimedStealthPool(browser_pool=browser, timeout_ms=int(timeout * 1000))
    note_clicks(pool)
    http = build_http_client(config)
    try:
        resolver = capture_resolvers(http, pool).get(args.hoster)
        if resolver is None:
            print(f"no capture resolver {args.hoster!r}")
            return 1
        print(
            f"# capture_timing: {args.hoster} · {urlsplit(args.url).hostname}"
            f" · navigation bound {timeout:g} s"
        )
        started = time.monotonic()
        await browser.warmup()
        print(
            f"browser launch {time.monotonic() - started:.1f} s"
            f" (DISPLAY {os.environ.get('DISPLAY', 'none')})"
        )
        print("| run | pages (path: start, load) | clicks at | stream at | resolve |")
        print("|---|---|---|---|---|")
        for number in range(1, args.runs + 1):
            run = Run(time.monotonic())
            pool.run = run
            try:
                async with asyncio.timeout(RUN_LIMIT_S):
                    run.resolved = await resolver.resolve(args.url) is not None
            except TimeoutError:
                run.cut = True
            run.took = time.monotonic() - run.started
            print(report_row(number, run), flush=True)
        print(
            f"Production's bounds: resolve {config.http_timeout_resolve_seconds:g} s"
            " (http_timeout_resolve_seconds; the wait for a browser page does not"
            f" count), navigation and challenge"
            f" {config.stremio.probe_stealth_timeout_seconds:g} s"
            " (stremio.probe_stealth_timeout_seconds)"
        )
    finally:
        pool.run = None
        await _close("stealth pool", pool.cleanup())
        await _close("browser", browser.cleanup())
        await _close("http client", http.aclose())
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="capture_timing")
    parser.add_argument("url", help="the hoster page, as the plugins store it")
    parser.add_argument("--hoster", default="filemoon")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="navigation and challenge bound in seconds (default: production's)",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    # The resolvers log at info level; the probe prints its own lines
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    workdir = Path(tempfile.mkdtemp(prefix="capture_timing-"))
    # The browser's profile and artifacts land here (Node's os.tmpdir())
    os.environ["TMPDIR"] = str(workdir)
    watchdog(args.runs * RUN_LIMIT_S + 60, workdir)
    code = 1
    try:
        code = asyncio.run(probe(args))
    finally:
        killed = kill_descendants()
        shutil.rmtree(workdir, ignore_errors=True)
        print(
            f"cleanup: {killed} leftover processes killed, temporary directory removed"
        )
    sys.exit(code)


if __name__ == "__main__":
    main()
