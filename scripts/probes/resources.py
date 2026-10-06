"""Container resources as the app sees them: cgroup limits and pressure, CPUs,
memory, load, and the processes with the most memory (proportional set size).

Read-only; prints no environment and no command lines.
"""

from __future__ import annotations

import os
from pathlib import Path

CGROUP = Path("/sys/fs/cgroup")
LIMITS = (
    "cpu.max",
    "cpu.weight",
    "memory.max",
    "memory.high",
    "memory.current",
    "memory.peak",
    "memory.swap.max",
    "cpu.pressure",
    "memory.pressure",
)
CPU_STAT_KEYS = ("usage_usec", "nr_periods", "nr_throttled", "throttled_usec")


def read(name: str) -> str:
    try:
        return (CGROUP / name).read_text().strip().replace("\n", " | ")
    except OSError as exc:
        return f"n/a ({type(exc).__name__})"


def pss_by_process() -> dict[str, list[int]]:
    """Process count and summed PSS (KiB) per command name; Chromium grouped."""
    groups: dict[str, list[int]] = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            comm = (proc / "comm").read_text().strip()
            rollup = (proc / "smaps_rollup").read_text().splitlines()
        except OSError:
            continue
        pss = next((int(ln.split()[1]) for ln in rollup if ln.startswith("Pss:")), 0)
        group = groups.setdefault(
            "chromium" if "chrom" in comm.lower() else comm, [0, 0]
        )
        group[0] += 1
        group[1] += pss
    return groups


for name in LIMITS:
    print(f"{name}: {read(name)}")
cpu_stat = (line.split() for line in read("cpu.stat").split(" | "))
print(
    "cpu.stat:", " ".join(" ".join(p) for p in cpu_stat if p and p[0] in CPU_STAT_KEYS)
)
print(
    "cpus:",
    os.cpu_count(),
    "affinity",
    len(os.sched_getaffinity(0)),
    "process_cpu_count",
    getattr(os, "process_cpu_count", lambda: None)(),
)
meminfo = dict(
    line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
)
print(
    *(
        f"{key} {meminfo[key].strip()}"
        for key in ("MemTotal", "MemAvailable", "SwapTotal")
    )
)
print("loadavg:", Path("/proc/loadavg").read_text().strip())
for name, (count, pss) in sorted(pss_by_process().items(), key=lambda kv: -kv[1][1])[
    :8
]:
    print(f"process {name}: {count} running, PSS {pss / 1024:.0f} MiB")
