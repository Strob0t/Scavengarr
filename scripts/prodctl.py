"""Read-only diagnostics of the production containers through Portainer.

The one way to look into production (AGENTS.md §9). Everything it prints is
masked (``portainer.mask``: URLs shrink to their host, no IP addresses, no
secrets), and every Portainer request passes a budget of 100 per minute shared
by all processes on the machine. Credentials: ``portainer.credentials()``.

Usage (``poetry run python scripts/prodctl.py ...``):
    ps                                   containers and their state
    stats [-c NAME]                      CPU, memory, processes, network
    logs [-c NAME] [--since 15m] [--grep RE] [--fields a,b] [--limit 60]
         [--health] [--raw] [--width 400]
                                         log lines, JSON records as key=value,
                                         cut to --width characters (0: whole,
                                         for tracebacks)
    metrics [--grep RE]                  the app's Prometheus metrics
    state [--keys a,b]                   /api/v1/stats/metrics: breakers, pools,
                                         event-loop lag
    probe [-c NAME] [--timeout S] [--env K=V] NAME|FILE [ARGS...]
                                         a Python probe run in the container
    digest [-c NAME] [--since 24h] [--json]
                                         one Markdown report of the window:
                                         requests, plugins, hosters, breakers,
                                         log noise, errors (``digest.py``)
    check --base URL [--insecure] [-c NAME] [--timeout S] [--deadline S]
                                         the post-deploy check: the container,
                                         the start's records, four stream
                                         requests through the public addon URL
                                         (``--base``, up to ``/api/v1/stremio``);
                                         one table, exit 1 on a failed check
                                         (``deploy_check.py``)

Probes are Python files run with ``python -c`` inside the container: the
read-only ones in ``scripts/probes/`` by name (``probe resources``), any other
file by path. A probe must not change production state.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from deploy_check import (
    ANIME_IDS,
    MOVIE_ID,
    Answer,
    container_check,
    failed,
    parse_time,
    startup_checks,
    stream_checks,
)
from deploy_check import render as render_checks
from digest import digest as build_digest
from digest import parse_records
from digest import render as render_digest
from portainer import Portainer, RequestBudget, credentials, mask

PROBES = Path(__file__).resolve().parent / "probes"
_BUDGET = Path.home() / ".cache" / "scavengarr" / "portainer-budget.json"
_DURATION = re.compile(r"(\d+(?:\.\d+)?)([smhd]?)")
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
# Liveness and readiness checks: two log lines every 30 s
_HEALTH = re.compile(r"/api/v1/(?:healthz|readyz)\b")
# The app's own endpoints, fetched inside the container in one exec: a JSON
# object of path -> body, or {"error": ...} for a path that failed
_FETCH = (
    "import json, os, sys, urllib.request\n"
    "base = 'http://127.0.0.1:' + os.environ.get('PORT', '7979')\n"
    "out = {}\n"
    "for path in sys.argv[1:]:\n"
    "    try:\n"
    "        answer = urllib.request.urlopen(base + path, timeout=30)\n"
    "        out[path] = answer.read().decode()\n"
    "    except Exception as exc:\n"
    "        out[path] = {'error': str(exc)}\n"
    "print(json.dumps(out))\n"
)


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


def cpu_cores(sample: dict[str, Any]) -> tuple[float, int]:
    """The CPU cores a ``docker stats`` sample shows in use (the sample's
    interval against its predecessor), and the cores online."""
    cpu = sample.get("cpu_stats", {})
    pre = sample.get("precpu_stats", {})
    used = _nested(cpu, "cpu_usage", "total_usage") - _nested(
        pre, "cpu_usage", "total_usage"
    )
    system = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    cpus = cpu.get("online_cpus") or len(
        cpu.get("cpu_usage", {}).get("percpu_usage") or [1]
    )
    return (used / system * cpus if system > 0 else 0.0), cpus


def stats_line(name: str, sample: dict[str, Any]) -> str:
    """CPU cores in use, memory without page cache, processes, network totals."""
    cores, cpus = cpu_cores(sample)
    memory = sample.get("memory_stats", {})
    page_cache = memory.get("stats", {})
    resident = memory.get("usage", 0) - page_cache.get(
        "inactive_file", page_cache.get("total_inactive_file", 0)
    )
    networks = list(sample.get("networks", {}).values())
    rx = sum(n.get("rx_bytes", 0) for n in networks)
    tx = sum(n.get("tx_bytes", 0) for n in networks)
    return (
        f"{name}: CPU {cores:.2f} of {cpus} cores | memory {_size(resident)}"
        f" of {_size(memory.get('limit', 0))} | processes"
        f" {sample.get('pids_stats', {}).get('current', '?')}"
        f" | network rx {_size(rx)} tx {_size(tx)}"
    )


def _nested(data: dict[str, Any], *keys: str) -> float:
    for key in keys:
        data = data.get(key, {})
    return float(data or 0)


def _size(count: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(count) < 1024 or unit == "GiB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024
    raise AssertionError("unreachable")


def probe_source(name: str) -> str:
    """A probe from ``scripts/probes/`` by name, else the file at *name*."""
    path = PROBES / f"{name}.py"
    if not path.is_file():
        path = Path(name)
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in PROBES.glob("*.py")))
        raise SystemExit(f"no probe {name!r} (known: {known})")
    return path.read_text()


def _env(pairs: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--env takes KEY=VALUE, not {pair!r}")
        env[key] = value
    return env


def portainer_client(container: str, timeout: float = 120) -> Portainer:
    url, key = credentials()
    return Portainer(
        container, url, key, budget=RequestBudget(_BUDGET), timeout=timeout
    )


def _fetch(container: str, *paths: str) -> dict[str, Any]:
    """The bodies of the app's own endpoints *paths*, fetched in one exec;
    a path that failed maps to ``{"error": ...}``."""
    output, code = portainer_client(container).run(["python", "-c", _FETCH, *paths])
    if code:
        raise SystemExit(
            mask(f"{' '.join(paths)} failed (exit {code}): {output.strip()[-300:]}")
        )
    return json.loads(output)


def _fetch_one(container: str, path: str) -> str:
    body = _fetch(container, path)[path]
    if not isinstance(body, str):
        raise SystemExit(mask(f"{path} failed: {body.get('error', body)}"))
    return body


def _ps(args: argparse.Namespace) -> int:
    for row in portainer_client("").containers():
        name = row["Names"][0].lstrip("/")
        print(mask(f"{name} | {row['State']} | {row['Status']}"))
    return 0


def _stats(args: argparse.Namespace) -> int:
    print(stats_line(args.container, portainer_client(args.container).stats()))
    return 0


def _logs(args: argparse.Namespace) -> int:
    since = int(time.time() - args.since)
    text = portainer_client(args.container).logs(since, timestamps=True)
    lines, matched = select_lines(
        text, grep=args.grep, health=args.health, limit=args.limit
    )
    fields = args.fields.split(",") if args.fields else None
    for line in lines:
        record = render(line, fields, raw=args.raw)
        print(record[: args.width] if args.width else record)
    print(f"[{len(lines)} of {matched} matching lines]")
    return 0


def _metrics(args: argparse.Namespace) -> int:
    text = _fetch_one(args.container, "/metrics")
    lines, _ = select_lines(text, grep=args.grep, health=True, limit=0)
    for line in lines:
        if not line.startswith("#"):
            print(mask(line))
    return 0


def _state(args: argparse.Namespace) -> int:
    data = json.loads(_fetch_one(args.container, "/api/v1/stats/metrics"))
    if args.keys:
        data = {key: data.get(key) for key in args.keys.split(",")}
    print(mask(json.dumps(data, indent=1, sort_keys=True)))
    return 0


def _probe(args: argparse.Namespace) -> int:
    source = probe_source(args.probe)
    env = _env(args.env)
    started = time.monotonic()
    output, code = portainer_client(args.container, args.timeout).run(
        ["python", "-c", source, *args.args], env=env
    )
    print(mask(output), end="" if output.endswith("\n") else "\n")
    print(f"[exit {code} after {time.monotonic() - started:.1f}s]")
    return code or 0


def _digest(args: argparse.Namespace) -> int:
    """One report of the window: the log (one request), the metrics and
    the plugin record (one exec)."""
    now = time.time()
    log_text = portainer_client(args.container).logs(
        int(now - args.since), timestamps=True
    )
    fetched = _fetch(args.container, "/metrics", "/api/v1/stats/plugins")
    metrics_text = fetched["/metrics"]
    if not isinstance(metrics_text, str):
        raise SystemExit(mask(f"/metrics failed: {metrics_text.get('error')}"))
    record = fetched["/api/v1/stats/plugins"]
    data = build_digest(
        container=args.container,
        since_s=args.since,
        log_text=log_text,
        metrics_text=metrics_text,
        # An older image has no record: the section says so
        plugin_record=json.loads(record) if isinstance(record, str) else None,
        now=now,
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
                resp = http.get(f"{base}/stream/{sid}.json")
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
                )
            )
    return answers


def _check(args: argparse.Namespace) -> int:
    """The post-deploy check: two Portainer requests (the containers, the
    log since the start) and four stream requests; read-only beyond those."""
    client = portainer_client(args.container)
    checks = [container_check(client.containers(), args.container)]
    started_at = parse_time(client.inspect()["State"]["StartedAt"])
    ids = [*(sid for sid, _ in ANIME_IDS), MOVIE_ID]
    answers = stream_answers(
        args.base.rstrip("/"), ids, insecure=args.insecure, timeout=args.timeout
    )
    time.sleep(_SETTLE_S)
    records = parse_records(client.logs(int(started_at) - 1, timestamps=True))
    checks += startup_checks(records, started_at)
    checks += stream_checks(answers, records, deadline_s=args.deadline)
    print(mask(render_checks(checks)))
    return 1 if failed(checks) else 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("ps", help="containers and their state").set_defaults(
        handler=_ps
    )
    stats = commands.add_parser("stats", help="CPU, memory, processes, network")
    logs = commands.add_parser("logs", help="log lines")
    metrics = commands.add_parser("metrics", help="the app's Prometheus metrics")
    state = commands.add_parser("state", help="breakers, pools, event-loop lag")
    probe = commands.add_parser("probe", help="run a Python probe in the container")
    digest = commands.add_parser(
        "digest", help="one report: requests, plugins, hosters, breakers, errors"
    )
    check = commands.add_parser(
        "check", help="the post-deploy check: one table, exit 1 on a failure"
    )
    for sub, handler in (
        (stats, _stats),
        (logs, _logs),
        (metrics, _metrics),
        (state, _state),
        (probe, _probe),
        (digest, _digest),
        (check, _check),
    ):
        sub.add_argument("-c", "--container", default="scavengarr")
        sub.set_defaults(handler=handler)
    logs.add_argument("--since", type=seconds, default=seconds("15m"))
    digest.add_argument("--since", type=seconds, default=seconds("24h"))
    digest.add_argument("--json", action="store_true", help="the data as JSON")
    check.add_argument(
        "--base", required=True, help="the public addon URL up to /api/v1/stremio"
    )
    check.add_argument("--insecure", action="store_true", help="skip TLS checks")
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
    probe.add_argument("--timeout", type=float, default=300)
    probe.add_argument("--env", action="append", default=[], help="KEY=VALUE")
    probe.add_argument("probe", help="a name in scripts/probes/ or a file")
    probe.add_argument("args", nargs=argparse.REMAINDER)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
