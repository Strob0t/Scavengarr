"""Read access to the production container through Komodo Core's API.

Used by ``prodctl.py``. The client sends two read requests about the
``scavengarr`` container, ``InspectContainer`` and ``GetContainerLog``, as
``POST {KOMODO_URL}/read``; it uses no other path (never ``/execute``,
``/write`` or ``/terminal``), though the key reaches every container of the
host. ``credentials()`` reads ``KOMODO_URL``, ``KOMODO_API_KEY``,
``KOMODO_API_SECRET``, ``KOMODO_SERVER`` (the server's name in Komodo) and
``PRODCTL_BASE_URL`` (production's public base URL) from the main checkout's
``.env.devcontainer`` (the file wins: the shell can hold a key from before the
file was edited), else from the environment. Nothing here prints the key or
the secret.

Docker's inspect object holds the container's environment, secrets included:
``inspect()`` keeps the state and the limits and nothing else of it.

``mask()`` keeps credentials and network addresses out of everything printed:
URLs shrink to scheme and host (CDN paths and queries carry tokens and the
client's address), the hosts of the addresses it is given (Komodo's and
production's) become ``<host>``, IP addresses and secret-like values are
replaced.
"""

from __future__ import annotations

import fcntl
import heapq
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

CONTAINER = "scavengarr"
# Komodo Core runs ``docker logs --tail`` with at most 5000 lines, without an
# error for a larger request: about 4 h of production's log
LOG_LINES = 5000

_ENV_FILE = ".env.devcontainer"
_NAMES = (
    "KOMODO_URL",
    "KOMODO_API_KEY",
    "KOMODO_API_SECRET",
    "KOMODO_SERVER",
    "PRODCTL_BASE_URL",
)
# Every request is a read, safe to repeat
_RETRY = frozenset({429, 502, 503, 504})
_ATTEMPTS = 3
_MAX_RETRY_AFTER = 60.0

_SECRET = re.compile(
    r"((?:password|passwd|secret|token|api[_-]?key|authorization|cookie|username)"
    r"[\"']?\s*[:=]\s*[\"']?)(?:(?:bearer|basic)\s+)?[^\"',\s}&]+",
    re.IGNORECASE,
)
_URL = re.compile(r"\b([a-z][a-z0-9+.-]*://)([^/\s\"'<>?#]*)([^\s\"'<>]*)", re.I)
_ENCODED_URL = re.compile(r"\b[a-z][a-z0-9+.-]*%3A%2F%2F[^\s\"'&<>]*", re.I)
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6 = re.compile(
    r"(?<![\w:])(?:(?:[0-9a-f]{1,4}:){4,7}[0-9a-f]{1,4}"
    r"|(?:[0-9a-f]{1,4}:){1,7}:(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})?"
    r"|::[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})(?![\w:])",
    re.I,
)


def mask(text: str, *addresses: str) -> str:
    """*text* without credentials, URL paths and queries, or IP addresses;
    the host of each URL in *addresses* becomes ``<host>``."""
    text = _ENCODED_URL.sub("<url>", text)
    text = _URL.sub(_short_url, text)
    for host in {urlsplit(address).hostname for address in addresses} - {None}:
        text = re.sub(
            rf"(?<![\w.-]){re.escape(str(host))}(?![\w-])", "<host>", text, flags=re.I
        )
    text = _SECRET.sub(r"\1<redacted>", text)
    text = _IPV6.sub("<ip>", text)
    return _IPV4.sub("<ip>", text)


def _short_url(match: re.Match[str]) -> str:
    scheme, authority, rest = match.groups()
    if "@" in authority:  # user:password@host
        authority = "<redacted>@" + authority.rsplit("@", 1)[1]
    return f"{scheme}{authority}{'/…' if rest.strip('/') else ''}"


@dataclass(frozen=True, repr=False)
class Credentials:
    """Komodo's address, key and secret, the server's name in Komodo, and
    production's public base URL; no printable form (it would show the key)."""

    url: str
    key: str
    secret: str
    server: str
    base_url: str


def credentials(env_file: Path | None = None) -> Credentials:
    """The five values from the env file, else from the environment."""
    values: dict[str, str] = {}
    path = env_file if env_file is not None else _main_env_file()
    if path is not None and path.is_file():
        for line in path.read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() in _NAMES:
                values[name.strip()] = value.strip().strip("\"'")
    found = {name: values.get(name) or os.environ.get(name, "") for name in _NAMES}
    missing = [name for name, value in found.items() if not value]
    if missing:
        raise SystemExit(
            f"missing in {_ENV_FILE} and the environment: {', '.join(missing)}"
        )
    return Credentials(
        url=found["KOMODO_URL"],
        key=found["KOMODO_API_KEY"],
        secret=found["KOMODO_API_SECRET"],
        server=found["KOMODO_SERVER"],
        base_url=found["PRODCTL_BASE_URL"],
    )


def _main_env_file() -> Path | None:
    """The env file of this checkout, or of the main checkout from a worktree."""
    root = Path(__file__).resolve().parents[1]
    candidates = [root / _ENV_FILE]
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        candidates.append(Path(common).parent / _ENV_FILE)
    except (OSError, subprocess.CalledProcessError):
        pass
    return next((path for path in candidates if path.is_file()), None)


class RequestBudget:
    """At most *limit* requests per *window* seconds for every process on this
    machine, kept in a small locked state file; a full budget waits."""

    def __init__(
        self,
        path: Path,
        *,
        limit: int = 100,
        window: float = 60.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._path = path
        self._limit = limit
        self._window = window
        self._clock = clock
        self._sleep = sleep

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            with self._path.open("a+") as state:
                fcntl.flock(state, fcntl.LOCK_EX)
                state.seek(0)
                try:
                    stamps = [float(t) for t in json.loads(state.read() or "[]")]
                except ValueError:
                    stamps = []
                now = self._clock()
                stamps = sorted(t for t in stamps if now - t < self._window)
                if len(stamps) < self._limit:
                    state.seek(0)
                    state.truncate()
                    state.write(json.dumps([*stamps, now]))
                    return
                wait = self._window - (now - stamps[0])
            self._sleep(max(wait, 0.1))


class KomodoError(Exception):
    """A read request that Komodo refused or answered with an error."""


@dataclass(frozen=True)
class ContainerState:
    """What ``prodctl.py`` reads of ``docker inspect``."""

    status: str  # State.Status: running, restarting, exited, ...
    health: str | None  # State.Health.Status; None without a health check
    started_at: str  # State.StartedAt, RFC 3339 with nanoseconds
    oom_killed: bool  # State.OOMKilled
    restarts: int  # RestartCount
    memory: int  # HostConfig.Memory: the limit in bytes, 0 for none
    nano_cpus: int  # HostConfig.NanoCpus: 10^9 per core, 0 for none
    cpu_quota: int  # HostConfig.CpuQuota per CpuPeriod: the other CPU limit
    cpu_period: int

    @property
    def cpus(self) -> float | None:
        """The CPU limit in cores; None without one."""
        if self.nano_cpus > 0:
            return self.nano_cpus / 1e9
        if self.cpu_quota > 0 and self.cpu_period > 0:
            return self.cpu_quota / self.cpu_period
        return None


@dataclass(frozen=True)
class ContainerLog:
    """The container's last lines (at most ``LOG_LINES``), each
    ``<RFC 3339 time> <line>``."""

    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "".join(f"{line}\n" for line in self.lines)

    @property
    def begins(self) -> str | None:
        """The first line's time: as far back as the log reaches."""
        return self.lines[0].partition(" ")[0] if self.lines else None


class Komodo:
    """Komodo Core's read requests about the container on one server."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        budget: RequestBudget | None = None,
        verify: bool = True,
        timeout: float = 120,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(
            base_url=credentials.url.rstrip("/"),
            headers={
                "X-Api-Key": credentials.key,
                "X-Api-Secret": credentials.secret,
            },
            timeout=timeout,
            verify=verify,
        )
        self._server = credentials.server
        self._budget = budget
        self._sleep = sleep

    def _read(
        self,
        request: Literal["InspectContainer", "GetContainerLog"],
        **params: Any,
    ) -> dict[str, Any]:
        body = {
            "type": request,
            "params": {"server": self._server, "container": CONTAINER, **params},
        }
        for attempt in range(1, _ATTEMPTS + 1):
            if self._budget is not None:
                self._budget.acquire()
            try:
                resp = self._http.post("/read", json=body)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if attempt == _ATTEMPTS:
                    raise
                self._sleep(2.0**attempt)
                continue
            if resp.status_code in _RETRY and attempt < _ATTEMPTS:
                self._sleep(_retry_after(resp, 2.0**attempt))
                continue
            if resp.is_error:
                raise KomodoError(
                    f"{request} answered {resp.status_code}{_reason(resp)}"
                )
            try:
                answer = resp.json()
            except ValueError:
                answer = None
            if not isinstance(answer, dict):
                raise KomodoError(f"{request} answered no JSON object")
            return answer
        raise AssertionError("unreachable")

    def inspect(self) -> ContainerState:
        """The container's state and limits; the rest of the answer, its
        environment among it, is dropped unread."""
        answer = self._read("InspectContainer")
        state = _object(answer, "State")
        host = _object(answer, "HostConfig")
        health = _object(state, "Health").get("Status")
        return ContainerState(
            status=str(state.get("Status") or "unknown"),
            health=str(health) if health else None,
            started_at=str(state.get("StartedAt") or ""),
            oom_killed=state.get("OOMKilled") is True,
            restarts=_count(answer.get("RestartCount")),
            memory=_count(host.get("Memory")),
            nano_cpus=_count(host.get("NanoCpus")),
            cpu_quota=_count(host.get("CpuQuota")),
            cpu_period=_count(host.get("CpuPeriod")),
        )

    def log(self) -> ContainerLog:
        """The container log's last ``LOG_LINES`` lines. Komodo answers stdout
        and stderr apart; they merge by their times, as ``docker logs``
        interleaved them (Docker's fixed-width UTC times sort as text)."""
        answer = self._read("GetContainerLog", tail=LOG_LINES, timestamps=True)
        if answer.get("success") is False:
            error = str(answer.get("stderr") or "").strip()[-300:]
            raise KomodoError(f"GetContainerLog failed: {error}")
        streams = [str(answer.get(name) or "").split("\n") for name in _STREAMS]
        merged = heapq.merge(*streams, key=lambda line: line.partition(" ")[0])
        return ContainerLog(tuple(line for line in merged if line))


_STREAMS = ("stdout", "stderr")


def _object(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _reason(resp: httpx.Response) -> str:
    """Komodo's error text (``{"error": ...}``), when the answer has one."""
    try:
        error = resp.json().get("error")
    except (ValueError, AttributeError):
        return ""
    return f": {str(error)[:300]}" if error else ""


def _retry_after(resp: httpx.Response, default: float) -> float:
    try:
        return min(float(resp.headers["Retry-After"]), _MAX_RETRY_AFTER)
    except (KeyError, ValueError):
        return default
