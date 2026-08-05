#!/usr/bin/env python3
"""Choose bounded worker counts from the host CPU and memory budget."""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path


MIB = 1024 * 1024
POLICY = {
    "build": (16, 768 * MIB),
    "host": (16, 384 * MIB),
    "qemu": (6, 1280 * MIB),
}


def available_memory() -> int | None:
    meminfo = Path("/proc/meminfo")
    try:
        for line in meminfo.read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass

    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
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
            return int(status.avail_phys)
    return None


def choose_jobs(kind: str, cpus: int | None = None,
                memory: int | None = None) -> int:
    cap, bytes_per_job = POLICY[kind]
    cpus = max(1, cpus or os.cpu_count() or 1)
    cpu_budget = max(1, cpus - 1) if cpus > 2 else cpus
    jobs = min(cap, cpu_budget)
    if memory is not None:
        # Leave at least 1 GiB for Windows, QEMU and the filesystem cache.
        memory_budget = max(0, memory - 1024 * MIB)
        jobs = min(jobs, max(1, memory_budget // bytes_per_job))
    override = os.environ.get("AGENTOS_MAX_JOBS")
    if override:
        try:
            jobs = min(jobs, max(1, int(override)))
        except ValueError:
            raise ValueError("AGENTOS_MAX_JOBS must be a positive integer")
    return max(1, int(jobs))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=tuple(POLICY), required=True)
    args = parser.parse_args()
    try:
        print(choose_jobs(args.kind, memory=available_memory()))
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
