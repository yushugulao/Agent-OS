#!/usr/bin/env python3
"""Contract tests for resource-adaptive worker selection."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("resource-jobs.py")
SPEC = importlib.util.spec_from_file_location("resource_jobs", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ResourceJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved_environment = {
            name: os.environ.pop(name)
            for name in ("AGENTOS_MAX_JOBS", "AGENTOS_OUTER_JOBS")
            if name in os.environ
        }

    def tearDown(self) -> None:
        os.environ.update(self.saved_environment)

    def test_cpu_memory_and_operator_caps_bound_jobs(self) -> None:
        self.assertEqual(MODULE.choose_jobs("build", cpus=12), 11)
        self.assertEqual(MODULE.choose_jobs("host", cpus=64), 12)
        memory = 1024 * MODULE.MIB + 3 * 768 * MODULE.MIB
        self.assertEqual(
            MODULE.choose_jobs("host", cpus=64, memory=memory), 3
        )
        qemu_memory = 1024 * MODULE.MIB + 2 * 1280 * MODULE.MIB
        self.assertEqual(
            MODULE.choose_jobs("qemu", cpus=32, memory=qemu_memory), 2
        )
        with patch.dict(os.environ, {"AGENTOS_MAX_JOBS": "3"}):
            self.assertEqual(MODULE.choose_jobs("host", cpus=32), 3)
        self.assertEqual(MODULE.choose_jobs("build", cpus=1, memory=1), 1)

    def test_outer_parallelism_partitions_nested_budgets(self) -> None:
        memory = 1024 * MODULE.MIB + 8 * 768 * MODULE.MIB
        self.assertEqual(
            MODULE.choose_jobs(
                "build", cpus=17, memory=memory, outer_jobs=4
            ),
            2,
        )
        with patch.dict(os.environ, {"AGENTOS_OUTER_JOBS": "4"}):
            self.assertEqual(MODULE.choose_jobs("host", cpus=17), 4)

    def test_linux_affinity_and_cgroup_v2_limits_are_combined(self) -> None:
        with patch.object(MODULE.os, "cpu_count", return_value=32), patch.object(
            MODULE, "process_affinity_count", return_value=6
        ), patch.object(MODULE, "cgroup_cpuset_count", return_value=4), patch.object(
            MODULE, "cgroup_cpu_count", return_value=2
        ):
            self.assertEqual(MODULE.available_cpu_count(), 2)

        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "cgroup"
            parent = root / "parent"
            current = parent / "worker"
            current.mkdir(parents=True)
            proc = Path(temp_name) / "self.cgroup"
            proc.write_text("0::/parent/worker\n", encoding="ascii")
            (current / "cpu.max").write_text(
                "250000 100000\n", encoding="ascii"
            )
            (parent / "cpu.max").write_text(
                "100000 100000\n", encoding="ascii"
            )
            (current / "cpuset.cpus.effective").write_text(
                "0-1,4,6-7\n", encoding="ascii"
            )
            (current / "memory.max").write_text("max\n", encoding="ascii")
            (current / "memory.current").write_text("1024\n", encoding="ascii")
            (parent / "memory.max").write_text("8192\n", encoding="ascii")
            (parent / "memory.current").write_text("4096\n", encoding="ascii")
            self.assertEqual(MODULE.cgroup_cpu_count(root, proc), 1)
            self.assertEqual(MODULE.cgroup_cpuset_count(root, proc), 5)
            self.assertEqual(MODULE.cgroup_available_memory(root, proc), 4096)

    def test_available_memory_takes_host_and_cgroup_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "cgroup"
            current = root / "worker"
            current.mkdir(parents=True)
            proc = Path(temp_name) / "self.cgroup"
            proc.write_text("0::/worker\n", encoding="ascii")
            (current / "memory.max").write_text(
                str(6 * MODULE.MIB), encoding="ascii"
            )
            (current / "memory.current").write_text(
                str(2 * MODULE.MIB), encoding="ascii"
            )
            meminfo = Path(temp_name) / "meminfo"
            meminfo.write_text("MemAvailable: 8192 kB\n", encoding="ascii")
            self.assertEqual(
                MODULE.available_memory(root, proc, meminfo), 4 * MODULE.MIB
            )

    def test_windows_process_affinity_mask_is_honored(self) -> None:
        class Kernel32:
            @staticmethod
            def GetCurrentProcess() -> int:
                return 1

            @staticmethod
            def GetProcessAffinityMask(_process, process_mask, system_mask) -> int:
                process_mask._obj.value = 0b10101
                system_mask._obj.value = 0b11111
                return 1

        with patch.object(
            MODULE.os, "sched_getaffinity", None, create=True
        ), patch.object(MODULE.os, "name", "nt"), patch.object(
            MODULE.ctypes,
            "windll",
            SimpleNamespace(kernel32=Kernel32()),
            create=True,
        ):
            self.assertEqual(MODULE.process_affinity_count(), 3)

    def test_invalid_nested_or_operator_limits_fail_closed(self) -> None:
        for name, value in (
            ("AGENTOS_MAX_JOBS", "many"),
            ("AGENTOS_MAX_JOBS", "0"),
            ("AGENTOS_OUTER_JOBS", "0"),
        ):
            with self.subTest(name=name, value=value), patch.dict(
                os.environ, {name: value}
            ):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    MODULE.choose_jobs("build", cpus=8)

    def test_makefile_uses_one_adaptive_qemu_budget(self) -> None:
        makefile = SCRIPT.parent.parent.joinpath("Makefile").read_text(
            encoding="utf-8"
        )
        self.assertEqual(makefile.count("AGENTOS_QEMU_JOBS ?="), 1)
        self.assertIn("resource-jobs.py --kind qemu", makefile)
        self.assertIn("AGENTOS_QEMU_JOBS must be an integer between 1 and 8", makefile)
        full_verify = makefile.split("\nfull-verify:\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("AGENTOS_QEMU_JOBS=$(call shell_quote", full_verify)


if __name__ == "__main__":
    unittest.main()
