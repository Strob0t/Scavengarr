"""Read-only diagnostics of the production container through Komodo.

The one way to look into production (AGENTS.md §9). It sends two of Komodo
Core's read requests about the ``scavengarr`` container (``komodo.py``: its
inspect state and limits, its log's last 5000 lines) and reads the app's own
endpoints through production's public base URL (``PRODCTL_BASE_URL``).
Everything it prints is masked (``komodo.mask``: URLs shrink to their host,
no IP addresses, no secrets; an error names neither Komodo's host nor
production's), and every Komodo request passes a budget of 100 per minute
shared by all processes on the machine. Credentials: ``komodo.credentials()``.

Usage (``poetry run python scripts/prodctl.py [--insecure] ...``):
    ps                                   the container: status, health, start,
                                         restarts, OOM kill
    stats                                the app process's CPU, memory and open
                                         files from two /metrics samples 5 s
                                         apart, against the container's limits
    logs [--since 15m] [--grep RE] [--fields a,b] [--limit 60]
         [--health] [--raw] [--width 400]
                                         log lines, JSON records as key=value,
                                         cut to --width characters (0: whole,
                                         for tracebacks)
    metrics [--grep RE]                  the app's Prometheus metrics
    state [--keys a,b]                   /api/v1/stats/metrics: breakers, pools,
                                         event-loop lag
    digest [--since 24h] [--json]        one Markdown report of the window:
                                         requests, plugins, hosters, breakers,
                                         log noise, errors (``digest.py``)
    check [--base URL] [--timeout S] [--deadline S]
                                         the post-deploy check: the container,
                                         the start's records, four stream
                                         requests through the public addon URL
                                         (``--base`` up to ``/api/v1/stremio``,
                                         PRODCTL_BASE_URL's by default), the
                                         readiness probe's network gauge; one
                                         table, exit 1 on a failed check
                                         (``deploy_check.py``)

``--insecure`` skips the TLS checks of Komodo and of the public URL (the
production host's local CA). Komodo answers the log's last 5000 lines, about
4 h of it: ``logs`` and ``digest`` say how far back it reaches when that is
short of ``--since``. A probe of ``scripts/probes/`` that runs in the container
is the maintainer's, on the Docker host:
``docker exec -i scavengarr python - [ARGS] < scripts/probes/NAME.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from deploy_check import (
    ANIME_IDS,
    MOVIE_ID,
    Answer,
    container_check,
    failed,
    network_check,
    parse_time,
    startup_checks,
    stream_checks,
)
from deploy_check import render as render_checks
from digest import digest as build_digest
from digest import parse_metrics, parse_records
from digest import render as render_digest
from komodo import (
    CONTAINER,
    LOG_LINES,
    ContainerLog,
    ContainerState,
    Credentials,
    Komodo,
    KomodoError,
    RequestBudget,
    credentials,
    mask,
)

_BUDGET = Path.home() / ".cache" / "scavengarr" / "komodo-budget.json"
_DURATION = re.compile(r"(\d+(?:\.\d+)?)([smhd]?)")
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
# Liveness and readiness checks: two log lines every 30 s
_HEALTH = re.compile(r"/api/v1/(?:healthz|readyz)\b")
# The app process's counters (prometheus_client's process collector: the
# Python process, not its Chromium children)
_CPU = "process_cpu_seconds_total"
_MEMORY = "process_resident_memory_bytes"
_FILES = "process_open_fds"
# Two /metrics samples this far apart give the CPU in use
_STATS_INTERVAL_S = 5.0


def seconds(duration: str) -> float:
    """``90s``, ``15m``, ``2h``, ``1d`` (a bare number counts seconds)."""
    match = _DURATION.fullmatch(duration.strip())
    if match is None:
        raise argparse.ArgumentTypeError(f"not a duration: {duration!r}")
    return float(match[1]) * _UNITS[match[2]]


def render(line: str, fields: list[str] | None = None, *, raw: bool = False) -> str:
    """One log line: time, level, event and the record's other fields."""
    stamp, _, payload = line.partition(" ")
    clock = stamp[11:19]
    try:
        record = json.loads(payload)
    except ValueError:
        record = None
    if raw or not isinstance(record, dict):
        return mask(f"{clock} {payload}")
    record.pop("timestamp", None)
    head = [str(record.pop("level", "")), str(record.pop("event", ""))]
    keys = fields if fields is not None else list(record)
    tail = [f"{key}={record[key]}" for key in keys if key in record]
    return mask(" ".join([clock, *head, *tail]))


def select_lines(
    text: str, *, grep: str | None, health: bool, limit: int
) -> tuple[list[str], int]:
    """The last *limit* lines matching *grep*, and how many matched."""
    pattern = re.compile(grep, re.IGNORECASE) if grep else None
    lines = [
        line
        for line in text.splitlines()
        if (health or not _HEALTH.search(line))
        and (pattern is None or pattern.search(line))
    ]
    return lines[-limit:] if limit > 0 else lines, len(lines)


def _stamp(line: str) -> float | None:
    """Unix seconds of a log line's time; None without one."""
    try:
        return parse_time(line.partition(" ")[0])
    except ValueError:
        return None


def since(log: ContainerLog, start: float) -> list[str]:
    """The log's lines from *start* (Unix seconds) on; they come in time
    order."""
    for index, line in enumerate(log.lines):
        stamp = _stamp(line)
        if stamp is not None and stamp >= start:
            return list(log.lines[index:])
    return []


def reach(log: ContainerLog, start: float) -> float | None:
    """The time the log begins at, when that is after *start*: the log
    reaches no further back."""
    begins = _stamp(log.lines[0]) if log.lines else None
    return begins if begins is not None and begins > start else None


def _utc(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def ps_line(state: ContainerState) -> str:
    """Status and health, the start, the restarts and an OOM kill."""
    health = f" ({state.health})" if state.health else ""
    try:
        started = _utc(parse_time(state.started_at))
    except ValueError:
        started = "?"
    return (
        f"{CONTAINER} | {state.status}{health} | started {started}"
        f" | restarts {state.restarts}"
        f" | OOM-killed {'yes' if state.oom_killed else 'no'}"
    )


def process_counters(metrics: str) -> dict[str, float]:
    """The app process's counters of one ``/metrics`` read."""
    return {
        name: value
        for name, labels, value in parse_metrics(metrics)
        if name in (_CPU, _MEMORY, _FILES) and not labels
    }


def stats_line(
    state: ContainerState,
    first: dict[str, float],
    second: dict[str, float],
    interval: float,
) -> str:
    """The app process's CPU cores in use between two samples *interval*
    seconds apart, its resident memory and open files, against the
    container's limits."""
    cores = "?"
    if _CPU in first and _CPU in second and interval > 0:
        cores = f"{(second[_CPU] - first[_CPU]) / interval:.3f}"
    cpus = state.cpus
    cpu = f"CPU {cores} of {cpus:g} cores" if cpus else f"CPU {cores} cores (no limit)"
    resident = _size(second[_MEMORY]) if _MEMORY in second else "?"
    memory = (
        f"memory {resident} of {_size(state.memory)}"
        if state.memory
        else f"memory {resident} (no limit)"
    )
    files = f"{second[_FILES]:.0f}" if _FILES in second else "?"
    return f"{CONTAINER} app process: {cpu} | {memory} | open files {files}"


def _size(count: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(count) < 1024 or unit == "GiB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024
    raise AssertionError("unreachable")


class Production:
    """The container through Komodo, the app through its public base URL."""

    def __init__(self, found: Credentials, *, insecure: bool) -> None:
        self.komodo = Komodo(found, budget=RequestBudget(_BUDGET), verify=not insecure)
        self.base_url = found.base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url, verify=not insecure, timeout=60
        )

    def text(self, path: str) -> str:
        """The body of the app's endpoint *path*."""
        return self._http.get(path).raise_for_status().text

    def text_or_none(self, path: str) -> str | None:
        """The body, or None when the endpoint does not answer it (an older
        image lacks it)."""
        try:
            return self.text(path)
        except httpx.HTTPError:
            return None


def _ps(args: argparse.Namespace, prod: Production) -> int:
    print(mask(ps_line(prod.komodo.inspect())))
    return 0


def _stats(args: argparse.Namespace, prod: Production) -> int:
    """Two ``/metrics`` samples ``_STATS_INTERVAL_S`` apart, timed as they
    arrive."""
    state = prod.komodo.inspect()
    first = process_counters(prod.text("/metrics"))
    first_at = time.monotonic()
    time.sleep(_STATS_INTERVAL_S)
    second = process_counters(prod.text("/metrics"))
    interval = time.monotonic() - first_at
    print(stats_line(state, first, second, interval))
    print("[the app's Python process: Chromium's processes are not in it]")
    return 0


def _logs(args: argparse.Namespace, prod: Production) -> int:
    log = prod.komodo.log()
    start = time.time() - args.since
    lines, matched = select_lines(
        "\n".join(since(log, start)),
        grep=args.grep,
        health=args.health,
        limit=args.limit,
    )
    fields = args.fields.split(",") if args.fields else None
    for line in lines:
        record = render(line, fields, raw=args.raw)
        print(record[: args.width] if args.width else record)
    print(f"[{len(lines)} of {matched} matching lines]")
    begins = reach(log, start)
    if begins is not None:
        cap = f": Komodo answers its last {LOG_LINES} lines" if log.cut else ""
        print(f"[the log reaches back to {_utc(begins)} only{cap}]")
    return 0


def _metrics(args: argparse.Namespace, prod: Production) -> int:
    lines, _ = select_lines(prod.text("/metrics"), grep=args.grep, health=True, limit=0)
    for line in lines:
        if not line.startswith("#"):
            print(mask(line))
    return 0


def _state(args: argparse.Namespace, prod: Production) -> int:
    data = json.loads(prod.text("/api/v1/stats/metrics"))
    if args.keys:
        data = {key: data.get(key) for key in args.keys.split(",")}
    print(mask(json.dumps(data, indent=1, sort_keys=True)))
    return 0


def _digest(args: argparse.Namespace, prod: Production) -> int:
    """One report of the window: the log (one Komodo request), the metrics
    and the plugin record."""
    now = time.time()
    log = prod.komodo.log()
    start = now - args.since
    begins = reach(log, start)
    record = prod.text_or_none("/api/v1/stats/plugins")
    data = build_digest(
        container=CONTAINER,
        since_s=args.since,
        log_text="".join(f"{line}\n" for line in since(log, start)),
        metrics_text=prod.text("/metrics"),
        # An older image has no record: the section says so
        plugin_record=json.loads(record) if record is not None else None,
        now=now,
        log_s=None if begins is None else now - begins,
    )
    print(mask(json.dumps(data, indent=1) if args.json else render_digest(data)))
    return 0


# After the stream requests, their last log lines
_SETTLE_S = 1.0


def stream_answers(
    base: str, ids: list[str], *, insecure: bool, timeout: float
) -> list[Answer]:
    """The four stream requests through the public addon URL, one after the
    other (the deploy check measures each answer's time on its own)."""
    answers: list[Answer] = []
    with httpx.Client(verify=not insecure, timeout=timeout) as http:
        for sid in ids:
            started = time.monotonic()
            try:
                with http.stream("GET", f"{base}/stream/{sid}.json") as resp:
                    headers_seconds = time.monotonic() - started
                    resp.read()
            except httpx.HTTPError as exc:
                answers.append(
                    Answer(
                        sid,
                        None,
                        seconds=time.monotonic() - started,
                        error=type(exc).__name__,
                    )
                )
                continue
            seconds = time.monotonic() - started
            streams: list[dict[str, Any]] = []
            if resp.status_code == 200:
                try:
                    streams = list(resp.json().get("streams", []))
                except (ValueError, AttributeError):
                    streams = []
            answers.append(
                Answer(
                    sid,
                    resp.status_code,
                    streams,
                    seconds,
                    resp.headers.get("X-Request-ID"),
                    headers_seconds=headers_seconds,
                )
            )
    return answers


def _check(args: argparse.Namespace, prod: Production) -> int:
    """The post-deploy check: two Komodo requests (the container's state,
    then its log after the stream requests), four stream requests and
    ``/metrics`` through the public URL; read-only beyond those."""
    state = prod.komodo.inspect()
    checks = [container_check(state)]
    started_at = parse_time(state.started_at)
    ids = [*(sid for sid, _ in ANIME_IDS), MOVIE_ID]
    base = args.base or f"{prod.base_url}/api/v1/stremio"
    answers = stream_answers(
        base.rstrip("/"), ids, insecure=args.insecure, timeout=args.timeout
    )
    time.sleep(_SETTLE_S)
    log = prod.komodo.log()
    # A restart keeps the log: only the lines of this start count
    records = parse_records("\n".join(since(log, started_at - 1)))
    cut_at = _stamp(log.lines[0]) if log.cut else None
    checks += startup_checks(records, started_at, cut_at=cut_at)
    checks += stream_checks(answers, records, deadline_s=args.deadline)
    checks.append(network_check(prod.text_or_none("/metrics")))
    print(mask(render_checks(checks)))
    return 1 if failed(checks) else 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    root.add_argument(
        "--insecure",
        action="store_true",
        help="skip the TLS checks of Komodo and the public URL (a local CA)",
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "ps", help="the container: status, health, start, restarts"
    ).set_defaults(handler=_ps)
    commands.add_parser(
        "stats", help="the app process's CPU, memory and open files"
    ).set_defaults(handler=_stats)
    logs = commands.add_parser("logs", help="log lines")
    metrics = commands.add_parser("metrics", help="the app's Prometheus metrics")
    state = commands.add_parser("state", help="breakers, pools, event-loop lag")
    digest = commands.add_parser(
        "digest", help="one report: requests, plugins, hosters, breakers, errors"
    )
    check = commands.add_parser(
        "check", help="the post-deploy check: one table, exit 1 on a failure"
    )
    for sub, handler in (
        (logs, _logs),
        (metrics, _metrics),
        (state, _state),
        (digest, _digest),
        (check, _check),
    ):
        sub.set_defaults(handler=handler)
    logs.add_argument("--since", type=seconds, default=seconds("15m"))
    digest.add_argument("--since", type=seconds, default=seconds("24h"))
    digest.add_argument("--json", action="store_true", help="the data as JSON")
    check.add_argument(
        "--base",
        help="the public addon URL up to /api/v1/stremio (PRODCTL_BASE_URL's)",
    )
    check.add_argument(
        "--timeout", type=float, default=120, help="per stream request (seconds)"
    )
    check.add_argument(
        "--deadline",
        type=float,
        default=60,
        help="the movie answer's deadline (seconds, stream_deadline_seconds)",
    )
    logs.add_argument("--fields", help="comma-separated JSON keys")
    logs.add_argument("--limit", type=int, default=60, help="last N lines")
    logs.add_argument("--health", action="store_true", help="keep health checks")
    logs.add_argument("--raw", action="store_true", help="masked lines as logged")
    logs.add_argument(
        "--width", type=int, default=400, help="cut records to N characters (0: whole)"
    )
    for sub in (logs, metrics):
        sub.add_argument("--grep", help="regex (case-insensitive)")
    state.add_argument("--keys", help="comma-separated top-level keys")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    found = credentials()
    try:
        return args.handler(args, Production(found, insecure=args.insecure))
    except (httpx.HTTPError, KomodoError) as exc:
        # httpx names the whole URL: neither Komodo's host nor production's
        # shows
        message = mask(f"{args.command} failed: {exc}", found.url, found.base_url)
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
