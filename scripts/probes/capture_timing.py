"""A browser-capture resolver's capture, timed in the container.

    probe capture_timing -- URL [--hoster filemoon] [--runs 2] [--timeout S]
        [--diagnose]

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
production's two bounds. ``--diagnose`` adds per run the page's requests
(domain, path with its ids masked as ``/api/videos/<id>/embed/playback``,
resource type, and the status, ``redirect``, the browser's error or
``pending``) and, after the clicks, what each frame can play: its video
element (MediaError code, network state) and the answers of MediaSource
and canPlayType for H.264, AAC and HLS. Prints no URLs: domains, paths
with ids masked and times only. Run it while nothing plays: its browser
shares the Pi's CPU with the app.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
from collections import Counter
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
# --diagnose: a path segment shows when it is a plain word (sub-words of
# letters split by -, _ or ., each up to nine letters and a final digit at
# most: ids are longer or mix digits in), else it reads <id>
_SUBWORD_RE = re.compile(r"[A-Za-z]{1,9}\d?")
_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_SHAPE_DEPTH = 8
# ...the kinds of request summed up in one line, the lines per run
_QUIET_KINDS = frozenset({"image", "font", "stylesheet"})
_MAX_REQUEST_LINES = 40
# ...the frames read after the clicks, the time each gets
_MAX_FRAMES = 6
_FRAME_READ_S = 3.0
# Asked in each frame after the clicks: the player's video element (its
# MediaError code and network state) and what the browser can play
_FRAME_CHECK = """() => {
  const mse = (type) => (window.MediaSource ? MediaSource.isTypeSupported(type) : null);
  const video = document.querySelector('video');
  return {
    video: video !== null,
    error: video && video.error ? video.error.code : null,
    network: video ? video.networkState : null,
    h264: mse('video/mp4; codecs="avc1.42E01E"'),
    aac: mse('audio/mp4; codecs="mp4a.40.2"'),
    hls: document.createElement('video').canPlayType('application/vnd.apple.mpegurl'),
  };
}"""


def path_kind(url: str) -> str:
    """The first segment of *url*'s path (``/d/``), no ids."""
    segment = urlsplit(url).path.strip("/").split("/", 1)[0]
    return f"/{segment}/" if segment else "/"


def domain(url: str) -> str:
    """*url*'s domain: the host's last two labels, ``<ip>`` for an address,
    the scheme for a URL without a host (``blob``)."""
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        return parts.scheme or "?"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return ".".join(host.split(".")[-2:])
    return "<ip>"


def _plain(segment: str) -> bool:
    return all(_SUBWORD_RE.fullmatch(word) for word in re.split(r"[-_.]", segment))


def shape(url: str) -> str:
    """*url*'s path with each segment that is no plain word masked
    (``/api/videos/<id>/embed/playback``); extensions stay, no query."""
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    shaped = []
    for segment in segments[:_SHAPE_DEPTH]:
        stem, extension = segment, ""
        if (found := _EXTENSION_RE.search(segment)) and found.start():
            stem, extension = segment[: found.start()], found.group()
        shaped.append((stem if _plain(stem) else "<id>") + extension)
    if len(segments) > _SHAPE_DEPTH:
        shaped.append("…")
    return "/" + "/".join(shaped)


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
class RequestNote:
    """A request of a watched page: domain, path shape, resource type and
    outcome (the status, ``redirect``, the browser's error or ``pending``)."""

    domain: str
    shape: str
    kind: str
    outcome: str = "pending"


@dataclass(frozen=True)
class FrameNote:
    """What a frame of the capture's page can play, read after the clicks;
    ``video`` None: the frame could not be read."""

    domain: str
    main: bool  # the page itself, not a frame in it
    video: bool | None = None
    error: int | None = None  # the video's MediaError code
    network: int | None = None  # the video's networkState
    h264: bool | None = None  # None: no MediaSource
    aac: bool | None = None
    hls: str = ""  # canPlayType: "", "maybe" or "probably"


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
    # --diagnose
    requests: list[RequestNote] = field(default_factory=list)
    frames: list[FrameNote] = field(default_factory=list)


def watch_requests(page: Any, run: Run) -> None:
    """Note each request *page* makes in *run*, with its outcome."""
    notes: dict[Any, RequestNote] = {}

    def requested(request: Any) -> None:
        # Web requests only: no data: URLs, no extension or browser pages
        if not request.url.startswith(("http:", "https:")):
            return
        hop = notes.get(request.redirected_from)
        if hop is not None and hop.outcome == "pending":
            hop.outcome = "redirect"
        note = RequestNote(
            domain(request.url), shape(request.url), request.resource_type
        )
        notes[request] = note
        run.requests.append(note)

    def answered(response: Any) -> None:
        if (note := notes.get(response.request)) is not None:
            note.outcome = str(response.status)

    def failed(request: Any) -> None:
        if (note := notes.get(request)) is not None:
            note.outcome = request.failure or "failed"

    page.on("request", requested)
    page.on("response", answered)
    page.on("requestfailed", failed)


async def frame_notes(page: Any) -> list[FrameNote]:
    """What each web frame of *page* (the page first) can play."""
    notes: list[FrameNote] = []
    frames = [f for f in page.frames if f.url.startswith(("http:", "https:"))]
    for frame in frames[:_MAX_FRAMES]:
        where, main = domain(frame.url), frame is page.main_frame
        try:
            async with asyncio.timeout(_FRAME_READ_S):
                found = await frame.evaluate(_FRAME_CHECK)
        except Exception:  # noqa: BLE001
            found = None  # detached, or busy past the bound
        if not isinstance(found, dict):
            notes.append(FrameNote(where, main))
            continue
        notes.append(
            FrameNote(
                where,
                main,
                video=bool(found.get("video")),
                error=found.get("error"),
                network=found.get("network"),
                h264=found.get("h264"),
                aac=found.get("aac"),
                hls=str(found.get("hls") or ""),
            )
        )
    return notes


class TimedStealthPool(StealthPool):
    """The app's stealth pool, noting its page loads and captures in ``run``;
    with ``diagnose`` each page's requests too (``watch_requests``, once
    per page)."""

    run: Run | None = None
    diagnose: bool = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._watched: list[Any] = []

    async def _navigate(
        self,
        page: Any,
        url: str,
        *,
        wait_until: Literal["commit", "domcontentloaded"],
        timeout_ms: int,
        done: Callable[[], bool] = lambda: False,
    ) -> bool:
        if (
            self.diagnose
            and self.run is not None
            and not any(watched is page for watched in self._watched)
        ):
            self._watched.append(page)
            watch_requests(page, self.run)
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
            self.run.cdn = domain(media.url)
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


def note_frames(pool: TimedStealthPool) -> None:
    """With ``pool.diagnose``, note what the page's frames can play once
    the capture's clicks are done (the capture looks ``_start_player`` up
    in its module)."""
    start_player = stealth._start_player

    async def noted(page: Any, found: Any, player: str | None) -> CapturedMedia | None:
        media = await start_player(page, found, player)
        if pool.diagnose and pool.run is not None:
            pool.run.frames = await frame_notes(page)
        return media

    stealth._start_player = noted


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


def request_lines(run: Run) -> list[str]:
    """*run*'s requests in the order they came, alike ones counted
    together; images, fonts and stylesheets summed up in one line."""
    alike: Counter[tuple[str, str, str, str]] = Counter()
    quiet: Counter[str] = Counter()
    for note in run.requests:
        if note.kind in _QUIET_KINDS:
            quiet[note.kind] += 1
        else:
            alike[note.domain, note.shape, note.kind, note.outcome] += 1
    lines = [
        f"- {where} {path} {kind}: {outcome}" + (f" ×{count}" if count > 1 else "")
        for (where, path, kind, outcome), count in alike.items()
    ]
    if len(lines) > _MAX_REQUEST_LINES:
        more = f"- … {len(lines) - _MAX_REQUEST_LINES} more"
        lines[_MAX_REQUEST_LINES:] = [more]
    if quiet:
        summed = ", ".join(f"{count} {kind}" for kind, count in sorted(quiet.items()))
        lines.append(f"- also {summed}")
    return lines


def _answer(value: bool | None) -> str:
    return "no MediaSource" if value is None else ("yes" if value else "no")


def frame_line(note: FrameNote) -> str:
    where = f"{'page' if note.main else 'frame'} {note.domain}"
    if note.video is None:
        return f"- {where}: unreadable"
    video = "no video element"
    if note.video:
        error = f"error {note.error}" if note.error else "no error"
        video = f"video element, {error}, network state {note.network}"
    return (
        f"- {where}: {video}; MediaSource H.264 {_answer(note.h264)},"
        f" AAC {_answer(note.aac)}; canPlayType HLS {note.hls or 'no'}"
    )


def diagnosis_lines(number: int, run: Run) -> list[str]:
    lines = [
        f"### Run {number}: requests (domain, path with ids masked, type: outcome)"
    ]
    lines.extend(request_lines(run) or ["- none"])
    lines.append("frames after the clicks:")
    lines.extend([frame_line(note) for note in run.frames] or ["- none read"])
    return lines


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
    pool.diagnose = args.diagnose
    note_clicks(pool)
    note_frames(pool)
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
        chromium, _ = await browser.warmup()
        # Major and build: the full version reads like an address to the masks
        version = chromium.version.split(".")
        build = version[2] if len(version) > 2 else "?"
        display = os.environ.get("DISPLAY", "none")
        print(
            f"browser launch {time.monotonic() - started:.1f} s"
            f" (Chromium {version[0]}, build {build}, DISPLAY {display})"
        )
        print("| run | pages (path: start, load) | clicks at | stream at | resolve |")
        print("|---|---|---|---|---|")
        runs: list[Run] = []
        for number in range(1, args.runs + 1):
            run = Run(time.monotonic())
            runs.append(run)
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
        if args.diagnose:
            for number, run in enumerate(runs, 1):
                print("\n".join(diagnosis_lines(number, run)))
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
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="print each run's requests and what its frames can play",
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
