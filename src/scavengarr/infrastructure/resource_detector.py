"""Container-aware resource detection via cgroup v2/v1 filesystem.

Reads CPU and memory limits from Linux cgroups — the same mechanism
used by Docker ``--cpus`` / ``--memory`` and Kubernetes resource limits.

Detection order (mirrors JVM ``UseContainerSupport`` and Go 1.25):
    1. cgroup v2:  ``/sys/fs/cgroup/cpu.max``, ``/sys/fs/cgroup/memory.max``
    2. cgroup v1:  ``cpu.cfs_quota_us``/``cpu.cfs_period_us``, ``memory.limit_in_bytes``
    3. Fallback:   ``os.cpu_count()`` + ``psutil`` (optional) or conservative defaults
"""

from __future__ import annotations

import math
import os
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


def _detect_cpu_v2() -> int | None:
    """Detect CPU cores from cgroup v2 ``cpu.max``.

    Format: ``"QUOTA PERIOD"`` (e.g. ``"200000 100000"`` = 2 cores).
    ``"max PERIOD"`` means unlimited.
    """
    content = _read_file(_CGROUP_V2_CPU)
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

    return max(1, math.ceil(quota / period))


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
    content = _read_file(_CGROUP_V2_MEM)
    if content is None or content == "max":
        return None

    try:
        limit = int(content)
    except ValueError:
        return None

    if limit <= 0 or limit >= _MEM_UNLIMITED_THRESHOLD:
        return None  # unlimited or host value leaked

    return limit


def _detect_mem_v1() -> int | None:
    """Detect memory limit from cgroup v1 ``memory.limit_in_bytes``."""
    content = _read_file(_CGROUP_V1_MEM)
    if content is None:
        return None

    try:
        limit = int(content)
    except ValueError:
        return None

    if limit <= 0 or limit >= _MEM_UNLIMITED_THRESHOLD:
        return None  # unlimited or host value leaked

    return limit


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
