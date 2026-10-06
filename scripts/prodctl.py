"""Read-only diagnostics of the production containers through Portainer.

The one way to look into production (AGENTS.md §9). Everything it prints is
masked (``portainer.mask``: URLs shrink to their host, no IP addresses, no
secrets), and every Portainer request passes a budget of 100 per minute shared
by all processes on the machine. Credentials: ``portainer.credentials()``.

Usage (``poetry run python scripts/prodctl.py ...``):
    ps                                   containers and their state
    stats [-c NAME]                      CPU, memory, processes, network
    logs [-c NAME] [--since 15m] [--grep RE] [--fields a,b] [--limit 60]
         [--health] [--raw]              log lines, JSON records as key=value
    metrics [--grep RE]                  the app's Prometheus metrics
    state [--keys a,b]                   /api/v1/stats/metrics: breakers, pools,
                                         event-loop lag
    probe [-c NAME] [--timeout S] [--env K=V] NAME|FILE [ARGS...]
                                         a Python probe run in the container

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

from portainer import Portainer, RequestBudget, credentials, mask

PROBES = Path(__file__).resolve().parent / "probes"
_BUDGET = Path.home() / ".cache" / "scavengarr" / "portainer-budget.json"
_DURATION = re.compile(r"(\d+(?:\.\d+)?)([smhd]?)")
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
# Liveness and readiness checks: two log lines every 30 s
_HEALTH = re.compile(r"/api/v1/(?:healthz|readyz)\b")
# The app's own endpoints, fetched inside the container
_FETCH = (
    "import os, sys, urllib.request\n"
    "port = os.environ.get('PORT', '7979')\n"
    "url = f'http://127.0.0.1:{port}' + sys.argv[1]\n"
    "print(urllib.request.urlopen(url, timeout=30).read().decode())\n"
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


def stats_line(name: str, sample: dict[str, Any]) -> str:
    """CPU cores in use, memory without page cache, processes, network totals."""
    cpu = sample.get("cpu_stats", {})
    pre = sample.get("precpu_stats", {})
    used = _nested(cpu, "cpu_usage", "total_usage") - _nested(
        pre, "cpu_usage", "total_usage"
    )
    system = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    cpus = cpu.get("online_cpus") or len(
        cpu.get("cpu_usage", {}).get("percpu_usage") or [1]
    )
    cores = used / system * cpus if system > 0 else 0.0
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


def _portainer(container: str, timeout: float = 120) -> Portainer:
    url, key = credentials()
    return Portainer(
        container, url, key, budget=RequestBudget(_BUDGET), timeout=timeout
    )


def _fetch(container: str, path: str) -> str:
    output, code = _portainer(container).run(["python", "-c", _FETCH, path])
    if code:
        raise SystemExit(mask(f"{path} failed (exit {code}): {output.strip()[-300:]}"))
    return output


def _ps(args: argparse.Namespace) -> int:
    for row in _portainer("").containers():
        name = row["Names"][0].lstrip("/")
        print(mask(f"{name} | {row['State']} | {row['Status']}"))
    return 0


def _stats(args: argparse.Namespace) -> int:
    print(stats_line(args.container, _portainer(args.container).stats()))
    return 0


def _logs(args: argparse.Namespace) -> int:
    since = int(time.time() - args.since)
    text = _portainer(args.container).logs(since, timestamps=True)
    lines, matched = select_lines(
        text, grep=args.grep, health=args.health, limit=args.limit
    )
    fields = args.fields.split(",") if args.fields else None
    for line in lines:
        print(render(line, fields, raw=args.raw)[:400])
    print(f"[{len(lines)} of {matched} matching lines]")
    return 0


def _metrics(args: argparse.Namespace) -> int:
    text = _fetch(args.container, "/metrics")
    lines, _ = select_lines(text, grep=args.grep, health=True, limit=0)
    for line in lines:
        if not line.startswith("#"):
            print(mask(line))
    return 0


def _state(args: argparse.Namespace) -> int:
    data = json.loads(_fetch(args.container, "/api/v1/stats/metrics"))
    if args.keys:
        data = {key: data.get(key) for key in args.keys.split(",")}
    print(mask(json.dumps(data, indent=1, sort_keys=True)))
    return 0


def _probe(args: argparse.Namespace) -> int:
    source = probe_source(args.probe)
    env = _env(args.env)
    started = time.monotonic()
    output, code = _portainer(args.container, args.timeout).run(
        ["python", "-c", source, *args.args], env=env
    )
    print(mask(output), end="" if output.endswith("\n") else "\n")
    print(f"[exit {code} after {time.monotonic() - started:.1f}s]")
    return code or 0


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
    for sub, handler in (
        (stats, _stats),
        (logs, _logs),
        (metrics, _metrics),
        (state, _state),
        (probe, _probe),
    ):
        sub.add_argument("-c", "--container", default="scavengarr")
        sub.set_defaults(handler=handler)
    logs.add_argument("--since", type=seconds, default=seconds("15m"))
    logs.add_argument("--fields", help="comma-separated JSON keys")
    logs.add_argument("--limit", type=int, default=60, help="last N lines")
    logs.add_argument("--health", action="store_true", help="keep health checks")
    logs.add_argument("--raw", action="store_true", help="masked lines as logged")
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
