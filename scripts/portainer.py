"""Read access to a production container through Portainer's Docker API.

Shared by ``prodctl.py`` and ``stremio_profile.py``. ``credentials()`` reads
``PORTAINER_URL`` and ``PORTAINER_API_KEY`` from the main checkout's
``.env.devcontainer`` (the file wins: the shell can hold a key from before the
file was edited), else from the environment. Nothing here prints the key.

``mask()`` keeps credentials and network addresses out of everything printed:
URLs shrink to scheme and host (CDN paths and queries carry tokens and the
client's address), IP addresses and secret-like values are replaced.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

_ENV_FILE = ".env.devcontainer"
# Retried statuses: a GET is safe to repeat, an exec POST only when Portainer
# refused it before running anything (429)
_RETRY_GET = frozenset({429, 502, 503, 504})
_RETRY_POST = frozenset({429})
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


def mask(text: str) -> str:
    """*text* without credentials, URL paths and queries, or IP addresses."""
    text = _ENCODED_URL.sub("<url>", text)
    text = _URL.sub(_short_url, text)
    text = _SECRET.sub(r"\1<redacted>", text)
    text = _IPV6.sub("<ip>", text)
    return _IPV4.sub("<ip>", text)


def _short_url(match: re.Match[str]) -> str:
    scheme, authority, rest = match.groups()
    if "@" in authority:  # user:password@host
        authority = "<redacted>@" + authority.rsplit("@", 1)[1]
    return f"{scheme}{authority}{'/…' if rest.strip('/') else ''}"


def credentials(env_file: Path | None = None) -> tuple[str, str]:
    """Portainer's base URL and API key."""
    values: dict[str, str] = {}
    path = env_file if env_file is not None else _main_env_file()
    if path is not None and path.is_file():
        for line in path.read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() in ("PORTAINER_URL", "PORTAINER_API_KEY"):
                values[name.strip()] = value.strip().strip("\"'")
    url = values.get("PORTAINER_URL") or os.environ.get("PORTAINER_URL", "")
    key = values.get("PORTAINER_API_KEY") or os.environ.get("PORTAINER_API_KEY", "")
    if not url or not key:
        raise SystemExit(
            f"PORTAINER_URL and PORTAINER_API_KEY are missing ({_ENV_FILE})"
        )
    return url, key


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


class Portainer:
    """The Docker API behind Portainer, for one container."""

    def __init__(
        self,
        name: str,
        url: str,
        api_key: str,
        *,
        budget: RequestBudget | None = None,
        timeout: float = 600,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._name = name
        self._http = httpx.Client(
            base_url=url.rstrip("/"), headers={"X-API-Key": api_key}, timeout=timeout
        )
        self._budget = budget
        self._sleep = sleep
        self._base = ""

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        retry = _RETRY_GET if method == "GET" else _RETRY_POST
        for attempt in range(1, _ATTEMPTS + 1):
            if self._budget is not None:
                self._budget.acquire()
            try:
                resp = self._http.request(method, path, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if attempt == _ATTEMPTS:
                    raise
                self._sleep(2.0**attempt)
                continue
            if resp.status_code not in retry or attempt == _ATTEMPTS:
                return resp.raise_for_status()
            self._sleep(_retry_after(resp, 2.0**attempt))
        raise AssertionError("unreachable")

    def _docker(self, path: str) -> str:
        if not self._base:
            endpoints = self._request("GET", "/api/endpoints").json()
            docker = [e for e in endpoints if e.get("Type") in (1, 2)] or endpoints
            self._base = f"/api/endpoints/{docker[0]['Id']}/docker"
            # Portainer applies the access labels of a recreated container
            # only when it lists containers; until then exec and logs answer 403
            self._request("GET", f"{self._base}/containers/json")
        return f"{self._base}{path}"

    def containers(self) -> list[dict[str, Any]]:
        """Every container this Portainer user may see."""
        return self._request("GET", self._docker("/containers/json?all=1")).json()

    def run(
        self,
        cmd: list[str],
        *,
        root: bool = False,
        detach: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> tuple[str, int | None]:
        """Run *cmd* in the container: its output (empty when detached) and
        exit code (None while it runs)."""
        body: dict[str, Any] = {
            "AttachStdout": not detach,
            "AttachStderr": not detach,
            "Tty": False,
            "Cmd": cmd,
        }
        if root:
            body["User"] = "root"
        if env:
            body["Env"] = [f"{key}={value}" for key, value in env.items()]
        exec_id = self._request(
            "POST", self._docker(f"/containers/{self._name}/exec"), json=body
        ).json()["Id"]
        started = self._request(
            "POST",
            self._docker(f"/exec/{exec_id}/start"),
            json={"Detach": detach, "Tty": False},
        )
        if detach:
            return "", None
        info = self._request("GET", self._docker(f"/exec/{exec_id}/json")).json()
        return demux(started.content), info.get("ExitCode")

    def exec(self, cmd: list[str], *, root: bool = False, detach: bool = False) -> str:
        """Run *cmd*; return its output (empty when detached)."""
        return self.run(cmd, root=root, detach=detach)[0]

    def logs(self, since: int, *, timestamps: bool = False) -> str:
        """The container log since *since* (Unix seconds)."""
        params = {"stdout": 1, "stderr": 1, "since": since}
        if timestamps:
            params["timestamps"] = 1
        resp = self._request(
            "GET", self._docker(f"/containers/{self._name}/logs"), params=params
        )
        return demux(resp.content)

    def stats(self) -> dict[str, Any]:
        """One sample of ``docker stats`` (Docker waits about a second for it)."""
        return self._request(
            "GET",
            self._docker(f"/containers/{self._name}/stats"),
            params={"stream": "false"},
        ).json()


def _retry_after(resp: httpx.Response, default: float) -> float:
    try:
        return min(float(resp.headers["Retry-After"]), _MAX_RETRY_AFTER)
    except (KeyError, ValueError):
        return default


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
