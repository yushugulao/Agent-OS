#!/usr/bin/env python3
"""Choose bounded worker counts from the resources visible to this process."""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path, PurePosixPath


MIB = 1024 * 1024
POLICY = {
    "build": (16, 768 * MIB),
    "host": (12, 768 * MIB),
    "qemu": (6, 1280 * MIB),
}
CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_CGROUP = Path("/proc/self/cgroup")
PROC_MEMINFO = Path("/proc/meminfo")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None


def _cgroup_v2_lineage(
    root: Path = CGROUP_ROOT, proc_cgroup: Path = PROC_CGROUP
) -> tuple[Path, ...]:
    payload = _read(proc_cgroup)
    if payload is None:
        return ()
    membership = next(
        (line.split(":", 2)[2] for line in payload.splitlines()
         if line.startswith("0::")),
        None,
    )
    if membership is None:
        return ()
    group = PurePosixPath(membership)
    if not group.is_absolute() or ".." in group.parts:
        return ()
    root = root.resolve(strict=False)
    current = root.joinpath(*group.parts[1:]).resolve(strict=False)
    try:
        current.relative_to(root)
    except ValueError:
        return ()
    lineage = []
    while True:
        lineage.append(current)
        if current == root:
            return tuple(lineage)
        current = current.parent


def parse_cpu_set(value: str) -> int | None:
    cpus: set[int] = set()
    try:
        for item in value.split(","):
            bounds = item.strip().split("-", 1)
            if not bounds[0]:
                continue
            first = int(bounds[0], 10)
            last = int(bounds[-1], 10)
            if first < 0 or last < first:
                return None
            cpus.update(range(first, last + 1))
    except ValueError:
        return None
    return len(cpus) or None


def cgroup_cpu_count(
    root: Path = CGROUP_ROOT, proc_cgroup: Path = PROC_CGROUP
) -> int | None:
    limits = []
    for directory in _cgroup_v2_lineage(root, proc_cgroup):
        fields = (_read(directory / "cpu.max") or "").split()
        if len(fields) != 2 or fields[0] == "max":
            continue
        try:
            quota, period = int(fields[0], 10), int(fields[1], 10)
        except ValueError:
            continue
        if quota > 0 and period > 0:
            # Fractional quotas must not create an extra runnable worker.
            limits.append(max(1, quota // period))
    return min(limits) if limits else None


def cgroup_cpuset_count(
    root: Path = CGROUP_ROOT, proc_cgroup: Path = PROC_CGROUP
) -> int | None:
    limits = []
    for directory in _cgroup_v2_lineage(root, proc_cgroup):
        value = _read(directory / "cpuset.cpus.effective")
        count = parse_cpu_set(value) if value else None
        if count is not None:
            limits.append(count)
    return min(limits) if limits else None


def cgroup_available_memory(
    root: Path = CGROUP_ROOT, proc_cgroup: Path = PROC_CGROUP
) -> int | None:
    limits = []
    for directory in _cgroup_v2_lineage(root, proc_cgroup):
        maximum = _read(directory / "memory.max")
        current = _read(directory / "memory.current")
        if maximum in (None, "max") or current is None:
            continue
        try:
            limits.append(max(0, int(maximum, 10) - int(current, 10)))
        except ValueError:
            continue
    return min(limits) if limits else None


def process_affinity_count() -> int | None:
    get_affinity = getattr(os, "sched_getaffinity", None)
    if get_affinity is not None:
        try:
            return len(get_affinity(0)) or None
        except (OSError, ValueError):
            pass
    if os.name == "nt":
        process_mask = ctypes.c_size_t()
        system_mask = ctypes.c_size_t()
        kernel32 = ctypes.windll.kernel32
        if kernel32.GetProcessAffinityMask(
            kernel32.GetCurrentProcess(),
            ctypes.byref(process_mask),
            ctypes.byref(system_mask),
        ):
            return int(process_mask.value).bit_count() or None
    return None


def available_cpu_count() -> int:
    limits = [os.cpu_count() or 1]
    limits.extend(
        value
        for value in (
            process_affinity_count(),
            cgroup_cpuset_count(),
            cgroup_cpu_count(),
        )
        if value is not None
    )
    return max(1, min(limits))


def available_memory(
    root: Path = CGROUP_ROOT,
    proc_cgroup: Path = PROC_CGROUP,
    meminfo: Path = PROC_MEMINFO,
) -> int | None:
    host_available = None
    payload = _read(meminfo)
    if payload is not None:
        try:
            host_available = next(
                int(line.split()[1]) * 1024
                for line in payload.splitlines()
                if line.startswith("MemAvailable:")
            )
        except (StopIteration, ValueError, IndexError):
            pass
    if host_available is None and os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong),
                ("avail_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended", ctypes.c_ulonglong),
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            host_available = int(status.avail_phys)
    cgroup_available = cgroup_available_memory(root, proc_cgroup)
    available = [value for value in (host_available, cgroup_available)
                 if value is not None]
    return min(available) if available else None


def _positive_environment(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)), 10)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def choose_jobs(
    kind: str,
    cpus: int | None = None,
    memory: int | None = None,
    outer_jobs: int | None = None,
) -> int:
    cap, bytes_per_job = POLICY[kind]
    cpus = max(1, available_cpu_count() if cpus is None else cpus)
    outer_jobs = (
        _positive_environment("AGENTOS_OUTER_JOBS", 1)
        if outer_jobs is None else outer_jobs
    )
    if outer_jobs < 1:
        raise ValueError("outer_jobs must be a positive integer")
    cpu_budget = max(1, cpus - 1) if cpus > 2 else cpus
    jobs = min(cap, max(1, cpu_budget // outer_jobs))
    if memory is not None:
        memory_budget = max(0, memory - 1024 * MIB) // outer_jobs
        jobs = min(jobs, max(1, memory_budget // bytes_per_job))
    return min(jobs, _positive_environment("AGENTOS_MAX_JOBS", jobs))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=tuple(POLICY), required=True)
    args = parser.parse_args()
    try:
        print(choose_jobs(args.kind, memory=available_memory()))
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
