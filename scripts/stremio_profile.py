"""Measure stream requests of a running instance: time, CPU, outbound requests.

Usage (against a running container):
    poetry run python scripts/stremio_profile.py --base https://scavengarr.lan \\
        (--portainer | --docker) [--container scavengarr] [--insecure] \\
        [--connect-to scavengarr.lan:192.168.88.2] [--py-spy] [IDS]

IDS are Stremio ids (``movie/tt0816692``, ``series/tt0903747:1:2``); without
them the baseline set of ``docs/plans/pi-performance.md`` is measured. Per id
it prints:

- the request's wall time;
- the CPU seconds the container's Python and Chromium processes used
  (``/proc`` before and after);
- the httpx requests the request caused, per host. These come from the
  container log's ``HTTP Request:`` lines, so they need the JSON log format,
  the production default.

At the end it prints the event-loop lag from ``/api/v1/stats/metrics``.

``--py-spy`` samples the app process during all requests (100 Hz, threads
holding the GIL only) and prints the CPU share per category. py-spy is
installed into the container's ``/tmp`` and runs as root; the container needs
``cap_add: [SYS_PTRACE]``.

Container access:

- ``--portainer`` reads ``PORTAINER_URL`` and ``PORTAINER_API_KEY`` from the
  environment. The Portainer user needs access to the container, e.g. through
  the label ``io.portainer.accesscontrol.users``.
- ``--docker`` uses the local docker CLI, so run the script on the Docker host.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import time
from collections import Counter
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

# The titles of the 2026-10-04 baseline (docs/plans/pi-performance.md)
BASELINE_IDS = ("movie/tt0816692", "series/tt0903747:1:2", "movie/tt0133093")

# CPU seconds of the container's Python and Chromium processes, as JSON
_CPU_SNIPPET = """
import json, os
tick = os.sysconf("SC_CLK_TCK")
cpu = {"python": 0.0, "chrome": 0.0}
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace")
        stat = open(f"/proc/{pid}/stat").read()
    except OSError:
        continue
    fields = stat[stat.rindex(")") + 2:].split()
    seconds = (int(fields[11]) + int(fields[12])) / tick
    kind = "chrome" if "chrom" in cmd else "python" if "python" in cmd else None
    if kind:
        cpu[kind] += seconds
print(json.dumps(cpu))
"""
# PID of the app process
_PID_SNIPPET = """
import os
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        if b"scavengarr.interfaces.cli" in open(f"/proc/{pid}/cmdline", "rb").read():
            print(pid)
            break
    except OSError:
        pass
"""
# SIGINT to py-spy: it stops sampling and writes its output
_STOP_PY_SPY = """
import os, signal
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        if b"py-spy" in open(f"/proc/{pid}/cmdline", "rb").read():
            os.kill(int(pid), signal.SIGINT)
    except OSError:
        pass
"""
_PY_SPY_DIR = "/tmp/py-spy"
_PY_SPY = f"{_PY_SPY_DIR}/bin/py-spy"
_PROFILE = "/tmp/stremio-profile.txt"

# Self-time categories of a profile, by the file of the innermost frame
_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("TLS (ssl)", r"ssl\.py"),
    ("httpx/httpcore/h11", r"httpx|httpcore|h11"),
    ("event loop (asyncio/anyio/selectors)", r"asyncio/|anyio|selectors|uvloop"),
    ("HTML parsing", r"html/parser\.py|_markupbase|selectolax|lxml"),
    ("cookiejar", r"cookiejar"),
    ("thread pool", r"concurrent/futures"),
    ("cache (diskcache/sqlite)", r"diskcache|sqlite"),
    ("socket", r"socket\.py"),
    ("regex", r"/re/"),
    ("json", r"/json/|orjson"),
    ("logging/structlog", r"structlog|/logging/"),
    ("web (fastapi/starlette/uvicorn/pydantic)", r"fastapi|starlette|uvicorn|pydantic"),
)
_FRAME_FILE = re.compile(r"\((.*?):\d+\)")


class Container(Protocol):
    """Run commands in the app container and read its log."""

    def exec(self, cmd: list[str], *, root: bool = False, detach: bool = False) -> str:
        """Run *cmd*; return its output (empty when detached)."""
        ...

    def logs(self, since: int) -> str:
        """The container log since *since* (Unix seconds)."""
        ...


class DockerCli:
    """The local docker CLI (run the script on the Docker host)."""

    def __init__(self, name: str) -> None:
        self._name = name

    def exec(self, cmd: list[str], *, root: bool = False, detach: bool = False) -> str:
        argv = ["docker", "exec"]
        if root:
            argv += ["--user", "root"]
        if detach:
            argv.append("--detach")
        argv += [self._name, *cmd]
        done = subprocess.run(argv, capture_output=True, text=True, check=True)
        return done.stdout

    def logs(self, since: int) -> str:
        argv = ["docker", "logs", "--since", str(since), self._name]
        done = subprocess.run(argv, capture_output=True, text=True, check=True)
        return done.stdout + done.stderr


class Portainer:
    """The Docker API behind Portainer (``PORTAINER_URL``, ``PORTAINER_API_KEY``)."""

    def __init__(self, name: str, url: str, api_key: str) -> None:
        self._name = name
        self._http = httpx.Client(
            base_url=url.rstrip("/"), headers={"X-API-Key": api_key}, timeout=600
        )
        self._base = ""

    def _docker(self, path: str) -> str:
        if not self._base:
            endpoints = self._http.get("/api/endpoints").raise_for_status().json()
            docker = [e for e in endpoints if e.get("Type") in (1, 2)] or endpoints
            self._base = f"/api/endpoints/{docker[0]['Id']}/docker"
        # Portainer applies the access labels of a recreated container only
        # when it lists containers; until then exec and logs answer 403
        self._http.get(f"{self._base}/containers/json").raise_for_status()
        return f"{self._base}{path}"

    def exec(self, cmd: list[str], *, root: bool = False, detach: bool = False) -> str:
        body: dict[str, Any] = {
            "AttachStdout": not detach,
            "AttachStderr": not detach,
            "Tty": False,
            "Cmd": cmd,
        }
        if root:
            body["User"] = "root"
        created = self._http.post(
            self._docker(f"/containers/{self._name}/exec"), json=body
        ).raise_for_status()
        started = self._http.post(
            self._docker(f"/exec/{created.json()['Id']}/start"),
            json={"Detach": detach, "Tty": False},
        ).raise_for_status()
        return "" if detach else demux(started.content)

    def logs(self, since: int) -> str:
        resp = self._http.get(
            self._docker(f"/containers/{self._name}/logs"),
            params={"stdout": 1, "stderr": 1, "since": since},
        ).raise_for_status()
        return demux(resp.content)


def demux(raw: bytes) -> str:
    """Docker's multiplexed stream (8-byte frame headers) as text."""
    if len(raw) < 8 or raw[0] not in (0, 1, 2) or raw[1:4] != b"\0\0\0":
        return raw.decode("utf-8", "replace")
    chunks: list[bytes] = []
    pos = 0
    while pos + 8 <= len(raw):
        size = int.from_bytes(raw[pos + 4 : pos + 8], "big")
        chunks.append(raw[pos + 8 : pos + 8 + size])
        pos += 8 + size
    return b"".join(chunks).decode("utf-8", "replace")


def count_requests(log_text: str) -> Counter[str]:
    """Outbound httpx requests per host in a JSON container log."""
    hosts: Counter[str] = Counter()
    for line in log_text.splitlines():
        try:
            event = json.loads(line).get("event", "")
        except (ValueError, AttributeError):
            continue
        if isinstance(event, str) and event.startswith("HTTP Request:"):
            parts = event.split()
            if len(parts) > 3:
                hosts[urlparse(parts[3]).hostname or "?"] += 1
    return hosts


def categorize(profile: str) -> tuple[int, list[tuple[str, float]]]:
    """Self-time shares per category of a py-spy raw profile.

    Stacks inside imports (startup, lazily loaded plugins) are left out.
    Returns the number of samples counted and (category, share) pairs.
    """
    totals: Counter[str] = Counter()
    for line in profile.splitlines():
        stack, _, count = line.rpartition(" ")
        if not stack or not count.isdigit() or "importlib" in stack:
            continue
        match = _FRAME_FILE.search(stack.split(";")[-1])
        leaf = match.group(1) if match else stack.split(";")[-1]
        name = next((n for n, pat in _CATEGORIES if re.search(pat, leaf)), "other")
        totals[name] += int(count)
    samples = sum(totals.values())
    shares = (
        [(name, n / samples) for name, n in totals.most_common()] if samples else []
    )
    return samples, shares


def cpu_seconds(container: Container) -> dict[str, float]:
    """CPU seconds used so far by the container's Python and Chromium processes."""
    output = container.exec(["python", "-c", _CPU_SNIPPET]).strip().splitlines()
    return json.loads(output[-1])


def start_py_spy(container: Container) -> None:
    """Install py-spy into the container if needed and start sampling the app."""
    install = f"python -m pip install -q --target {_PY_SPY_DIR} py-spy"
    container.exec(["sh", "-c", f"test -x {_PY_SPY} || {install}"], root=True)
    pid = container.exec(["python", "-c", _PID_SNIPPET]).strip()
    if not pid:
        raise SystemExit("app process not found in the container")
    container.exec(["rm", "-f", _PROFILE], root=True)
    container.exec(
        [_PY_SPY, "record", "--pid", pid, "--rate", "100", "--nonblocking", "--gil"]
        + ["--format", "raw", "--duration", "3600", "--output", _PROFILE],
        root=True,
        detach=True,
    )


def stop_py_spy(container: Container) -> str:
    """Stop py-spy and return its raw profile (folded stacks)."""
    container.exec(["python", "-c", _STOP_PY_SPY], root=True)
    for _ in range(30):
        time.sleep(1)
        profile = container.exec(["sh", "-c", f"cat {_PROFILE} 2>/dev/null"], root=True)
        if profile.strip():
            return profile
    raise SystemExit("py-spy wrote no profile (is cap_add SYS_PTRACE set?)")


def _connect_to(mapping: str) -> None:
    """Resolve a host to a fixed IP (like curl --connect-to HOST:IP)."""
    host, _, ip = mapping.rpartition(":")
    original = socket.getaddrinfo

    def _resolve(name: Any, *args: Any, **kwargs: Any) -> Any:
        target = name.decode() if isinstance(name, bytes) else name
        return original(ip if target == host else name, *args, **kwargs)

    socket.getaddrinfo = _resolve


def _container(args: argparse.Namespace) -> Container:
    if args.docker:
        return DockerCli(args.container)
    return Portainer(
        args.container, os.environ["PORTAINER_URL"], os.environ["PORTAINER_API_KEY"]
    )


def measure(
    http: httpx.Client, base: str, container: Container, sid: str
) -> dict[str, Any]:
    """One stream request: streams, wall time, CPU used, httpx requests per host.

    Prints one line per request and its busiest hosts.
    """
    before = cpu_seconds(container)
    since = int(time.time()) - 1
    start = time.monotonic()
    resp = http.get(f"{base}/api/v1/stremio/stream/{sid}.json")
    wall = time.monotonic() - start
    streams = len(resp.json().get("streams", [])) if resp.is_success else 0
    time.sleep(1)  # the request's last log lines
    after = cpu_seconds(container)
    hosts = count_requests(container.logs(since))
    row: dict[str, Any] = {
        "streams": streams,
        "wall": wall,
        "python": after["python"] - before["python"],
        "chrome": after["chrome"] - before["chrome"],
        "requests": sum(hosts.values()),
    }
    print(
        f"{sid}: {streams} streams, {wall:.1f} s wall, "
        f"CPU Python {row['python']:.1f} s, Chromium {row['chrome']:.1f} s, "
        f"{row['requests']} httpx requests to {len(hosts)} hosts"
    )
    print("   top hosts:", ", ".join(f"{h} {n}" for h, n in hosts.most_common(8)))
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base", required=True, help="the instance's base URL")
    access = parser.add_mutually_exclusive_group(required=True)
    access.add_argument("--portainer", action="store_true")
    access.add_argument("--docker", action="store_true")
    parser.add_argument("--container", default="scavengarr")
    parser.add_argument("--insecure", action="store_true", help="skip TLS checks")
    parser.add_argument(
        "--connect-to", help="HOST:IP, e.g. scavengarr.lan:192.168.88.2"
    )
    parser.add_argument(
        "--py-spy", action="store_true", help="CPU profile (SYS_PTRACE)"
    )
    parser.add_argument(
        "--repeat", type=int, default=1, help="passes over the ids (1st cold)"
    )
    parser.add_argument("ids", nargs="*")
    args = parser.parse_args()
    if args.connect_to:
        _connect_to(args.connect_to)
    container = _container(args)
    ids = args.ids or list(BASELINE_IDS)

    if args.py_spy:
        start_py_spy(container)
    with httpx.Client(verify=not args.insecure, timeout=120) as http:
        for run in range(1, args.repeat + 1):
            rows = [measure(http, args.base, container, sid) for sid in ids]
            n = len(rows)
            print(
                f"pass {run} mean over {n}: "
                f"{sum(r['wall'] for r in rows) / n:.1f} s wall, "
                f"CPU Python {sum(r['python'] for r in rows) / n:.1f} s, "
                f"Chromium {sum(r['chrome'] for r in rows) / n:.1f} s, "
                f"{sum(r['requests'] for r in rows) / n:.0f} httpx requests, "
                f"{sum(r['streams'] for r in rows) / n:.1f} streams"
            )
        lag = http.get(f"{args.base}/api/v1/stats/metrics").json().get("event_loop")
    print("event-loop lag (last 5 min):", lag)
    if args.py_spy:
        samples, shares = categorize(stop_py_spy(container))
        print(f"py-spy: {samples} samples (~{samples / 100:.1f} s CPU on the GIL)")
        for name, share in shares:
            print(f"   {100 * share:5.1f}%  {name}")


if __name__ == "__main__":
    main()
