"""Container-aware resource detection via cgroup v2/v1 filesystem.

Reads CPU and memory limits from Linux cgroups — the same mechanism
used by Docker ``--cpus`` / ``--memory`` and Kubernetes resource limits.

Detection order (mirrors JVM ``UseContainerSupport`` and Go 1.25):
    1. cgroup v2:  ``/sys/fs/cgroup/cpu.max``, ``/sys/fs/cgroup/memory.max``
    2. cgroup v1:  ``cpu.cfs_quota_us``/``cpu.cfs_period_us``, ``memory.limit_in_bytes``
    3. Fallback:   ``os.cpu_count()`` + ``psutil`` (optional) or conservative defaults

``ResourceSampler`` reads how busy the CPU is and how much memory is free,
for the stealth browser's page budget.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

log = structlog.get_logger(__name__)

# cgroup v2 paths (unified hierarchy)
_CGROUP_V2_CPU = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V2_MEM = Path("/sys/fs/cgroup/memory.max")

# cgroup v1 paths (legacy hierarchy)
_CGROUP_V1_CPU_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_CPU_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
_CGROUP_V1_MEM = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")

# Memory values above 1 TB are treated as "unlimited" (host value leaked)
_MEM_UNLIMITED_THRESHOLD = 1024**4  # 1 TB

_DEFAULT_MEM_BYTES = 4 * 1024**3  # 4 GB conservative fallback

ResourceSource = Literal["cgroup_v2", "cgroup_v1", "os_fallback"]


@dataclass(frozen=True)
class DetectedResources:
    """Detected CPU and memory resources (container-aware)."""

    cpu_cores: int  # Available CPU cores (≥1)
    memory_bytes: int  # Available memory in bytes
    cpu_source: ResourceSource  # Where CPU was detected from
    mem_source: ResourceSource  # Where memory was detected from
    cgroup_limited: bool  # True if any cgroup limit was detected


def _read_file(path: Path) -> str | None:
    """Read a cgroup pseudo-file, returning None on any error."""
    try:
        return path.read_text().strip()
    except (OSError, PermissionError):
        return None


def _cpu_limit(content: str | None) -> float | None:
    """The cores of a cgroup v2 ``cpu.max``, None when unlimited.

    Format: ``"QUOTA PERIOD"`` (e.g. ``"150000 100000"`` = 1.5 cores).
    ``"max PERIOD"`` means unlimited.
    """
    if content is None:
        return None

    parts = content.split()
    if len(parts) != 2:
        return None

    quota_str, period_str = parts
    if quota_str == "max":
        return None  # unlimited

    try:
        quota = int(quota_str)
        period = int(period_str)
    except ValueError:
        return None

    if quota <= 0 or period <= 0:
        return None

    return quota / period


def _memory_limit(content: str | None) -> int | None:
    """The bytes of a cgroup memory limit, None when unlimited."""
    if content is None or content == "max":
        return None

    try:
        limit = int(content)
    except ValueError:
        return None

    if limit <= 0 or limit >= _MEM_UNLIMITED_THRESHOLD:
        return None  # unlimited or host value leaked

    return limit


def _detect_cpu_v2() -> int | None:
    """Detect CPU cores from cgroup v2 ``cpu.max`` (whole cores, at least 1)."""
    cores = _cpu_limit(_read_file(_CGROUP_V2_CPU))
    return None if cores is None else max(1, math.ceil(cores))


def _detect_cpu_v1() -> int | None:
    """Detect CPU cores from cgroup v1 ``cpu.cfs_quota_us / cpu.cfs_period_us``.

    Quota of ``-1`` means unlimited.
    """
    quota_str = _read_file(_CGROUP_V1_CPU_QUOTA)
    period_str = _read_file(_CGROUP_V1_CPU_PERIOD)

    if quota_str is None or period_str is None:
        return None

    try:
        quota = int(quota_str)
        period = int(period_str)
    except ValueError:
        return None

    if quota == -1 or quota <= 0 or period <= 0:
        return None  # unlimited or invalid

    return max(1, math.ceil(quota / period))


def _detect_mem_v2() -> int | None:
    """Detect memory limit from cgroup v2 ``memory.max``.

    Value is bytes, or ``"max"`` for unlimited.
    """
    return _memory_limit(_read_file(_CGROUP_V2_MEM))


def _detect_mem_v1() -> int | None:
    """Detect memory limit from cgroup v1 ``memory.limit_in_bytes``."""
    return _memory_limit(_read_file(_CGROUP_V1_MEM))


def _fallback_cpu() -> int:
    """Fallback CPU detection via ``os.cpu_count()``."""
    return os.cpu_count() or 2


def _fallback_mem() -> int:
    """Fallback memory detection via psutil or conservative default."""
    try:
        import psutil

        total = psutil.virtual_memory().total
        if total > 0:
            return total
    except ImportError:
        pass

    return _DEFAULT_MEM_BYTES


def detect_resources() -> DetectedResources:
    """Detect CPU and memory resources from cgroups or OS APIs.

    Tries cgroup v2 first, then v1, then falls back to OS-level detection.
    Returns a frozen dataclass with detected values and source information.
    """
    # --- CPU detection ---
    cpu_cores: int
    cpu_source: ResourceSource

    v2_cpu = _detect_cpu_v2()
    if v2_cpu is not None:
        cpu_cores = v2_cpu
        cpu_source = "cgroup_v2"
    else:
        v1_cpu = _detect_cpu_v1()
        if v1_cpu is not None:
            cpu_cores = v1_cpu
            cpu_source = "cgroup_v1"
        else:
            cpu_cores = _fallback_cpu()
            cpu_source = "os_fallback"

    # --- Memory detection ---
    mem_bytes: int
    mem_source: ResourceSource

    v2_mem = _detect_mem_v2()
    if v2_mem is not None:
        mem_bytes = v2_mem
        mem_source = "cgroup_v2"
    else:
        v1_mem = _detect_mem_v1()
        if v1_mem is not None:
            mem_bytes = v1_mem
            mem_source = "cgroup_v1"
        else:
            mem_bytes = _fallback_mem()
            mem_source = "os_fallback"

    cgroup_limited = cpu_source != "os_fallback" or mem_source != "os_fallback"

    result = DetectedResources(
        cpu_cores=cpu_cores,
        memory_bytes=mem_bytes,
        cpu_source=cpu_source,
        mem_source=mem_source,
        cgroup_limited=cgroup_limited,
    )

    log.info(
        "resource_detection",
        cpu_cores=result.cpu_cores,
        memory_mb=round(result.memory_bytes / (1024**2)),
        cpu_source=result.cpu_source,
        mem_source=result.mem_source,
        cgroup_limited=result.cgroup_limited,
    )

    return result


def own_cgroup(root: Path, proc_cgroup: Path) -> Path | None:
    """The cgroup v2 directory of this process (``0::<path>``), if any."""
    try:
        lines = proc_cgroup.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("0::"):
            directory = root / line[3:].strip().lstrip("/")
            return directory if (directory / "cpu.stat").is_file() else None
    return None


def read_cgroup_value(path: Path, key: str | None = None) -> int | None:
    """The number in *path*, or the one after *key* (``cpu.stat``)."""
    try:
        text = path.read_text()
    except OSError as exc:
        log.debug("cgroup_read_failed", path=str(path), error=str(exc))
        return None
    try:
        if key is None:
            return int(text.strip())
        for line in text.splitlines():
            name, _, value = line.partition(" ")
            if name == key:
                return int(value)
    except ValueError:
        log.debug("cgroup_value_invalid", path=str(path), key=key)
    return None


def _host_jiffies(path: Path) -> tuple[int, int] | None:
    """Busy and all CPU time of the host since boot (``/proc/stat``)."""
    content = _read_file(path)
    if content is None:
        return None
    fields = content.split("\n", 1)[0].split()
    if len(fields) < 9 or fields[0] != "cpu":
        return None
    try:
        user, nice, system, idle, iowait, irq, softirq, steal = (
            int(value) for value in fields[1:9]
        )
    except ValueError:
        return None
    busy = user + nice + system + irq + softirq + steal
    return busy, busy + idle + iowait


def _available_memory(path: Path) -> int | None:
    """``MemAvailable`` of ``/proc/meminfo`` in bytes."""
    content = _read_file(path)
    if content is None:
        return None
    for line in content.splitlines():
        name, _, value = line.partition(":")
        if name == "MemAvailable":
            try:
                return int(value.split()[0]) * 1024
            except (IndexError, ValueError):
                return None
    return None


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    """How busy the CPU is and how much memory is free; None when unknown."""

    cpu_busy: float | None  # busy share since the last sample, 0-1
    memory_free: int | None  # bytes


class ResourceSampler:
    """CPU load and free memory as the container sees them.

    The CPU's busy share since the last sample is the host's
    (``/proc/stat``) or, under a CPU limit, the container's share of it
    (cgroup v2), whichever is higher. Free memory is the host's
    ``MemAvailable`` or, under a memory limit, what the limit leaves
    (inactive page cache counts as free), whichever is lower.
    """

    def __init__(
        self,
        *,
        proc: Path = Path("/proc"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
    ) -> None:
        self._proc = proc
        self._cgroup = own_cgroup(cgroup_root, proc / "self" / "cgroup")
        self._host_last: tuple[int, int] | None = None  # busy, all jiffies
        self._container_last: tuple[float, int] | None = None  # time, usage_usec

    def sample(self) -> ResourceUsage:
        busy = [b for b in (self._host_busy(), self._container_busy()) if b is not None]
        free = [f for f in (self._host_free(), self._container_free()) if f is not None]
        return ResourceUsage(
            cpu_busy=max(busy, default=None), memory_free=min(free, default=None)
        )

    def _host_busy(self) -> float | None:
        jiffies = _host_jiffies(self._proc / "stat")
        last, self._host_last = self._host_last, jiffies
        if jiffies is None or last is None or jiffies[1] <= last[1]:
            return None
        return (jiffies[0] - last[0]) / (jiffies[1] - last[1])

    def _container_busy(self) -> float | None:
        if self._cgroup is None:
            return None
        cores = _cpu_limit(_read_file(self._cgroup / "cpu.max"))
        usage = read_cgroup_value(self._cgroup / "cpu.stat", "usage_usec")
        now = time.monotonic()
        last = self._container_last
        self._container_last = None if usage is None else (now, usage)
        if cores is None or usage is None or last is None or now <= last[0]:
            return None
        return (usage - last[1]) / 1_000_000 / ((now - last[0]) * cores)

    def _host_free(self) -> int | None:
        return _available_memory(self._proc / "meminfo")

    def _container_free(self) -> int | None:
        if self._cgroup is None:
            return None
        limit = _memory_limit(_read_file(self._cgroup / "memory.max"))
        current = read_cgroup_value(self._cgroup / "memory.current")
        if limit is None or current is None:
            return None
        inactive = read_cgroup_value(self._cgroup / "memory.stat", "inactive_file")
        return max(0, limit - current + (inactive or 0))
