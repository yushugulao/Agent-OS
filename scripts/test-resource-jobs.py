#!/usr/bin/env python3
"""Contract tests for adaptive build and test worker selection."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("resource-jobs.py")
SPEC = importlib.util.spec_from_file_location("resource_jobs", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ResourceJobsTests(unittest.TestCase):
    def test_cpu_count_is_used_instead_of_fixed_worker_count(self) -> None:
        self.assertEqual(MODULE.choose_jobs("build", cpus=12), 11)
        self.assertEqual(MODULE.choose_jobs("host", cpus=6), 5)

    def test_nested_host_tools_keep_a_process_headroom(self) -> None:
        self.assertEqual(MODULE.choose_jobs("host", cpus=64), 12)
        memory = 1024 * MODULE.MIB + 3 * 768 * MODULE.MIB
        self.assertEqual(MODULE.choose_jobs("host", cpus=64, memory=memory), 3)

    def test_memory_bounds_parallel_qemu_lanes(self) -> None:
        memory = 1024 * MODULE.MIB + 2 * 1280 * MODULE.MIB
        self.assertEqual(MODULE.choose_jobs("qemu", cpus=32, memory=memory), 2)

    def test_small_machine_always_gets_one_worker(self) -> None:
        self.assertEqual(MODULE.choose_jobs("build", cpus=1, memory=1), 1)

    def test_operator_can_apply_a_lower_cap(self) -> None:
        with patch.dict(os.environ, {"AGENTOS_MAX_JOBS": "3"}):
            self.assertEqual(MODULE.choose_jobs("host", cpus=32), 3)

    def test_invalid_operator_cap_fails_closed(self) -> None:
        with patch.dict(os.environ, {"AGENTOS_MAX_JOBS": "many"}):
            with self.assertRaisesRegex(ValueError, "positive integer"):
                MODULE.choose_jobs("build", cpus=8)


if __name__ == "__main__":
    unittest.main()
