#!/usr/bin/env python3
"""Source-level regressions for the traditional performance campaign."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def hardcoded_measurement(text: str) -> bool:
    return re.search(
        r"(?:duration_us|duration_ticks|elapsed\.(?:us|ticks)|outcome_hash)"
        r"\s*=\s*[1-9][0-9]*[uUlL]*\s*;",
        text,
    ) is not None


class TraditionalPerformanceContractTests(unittest.TestCase):
    def test_same_guest_sources_are_compiled_for_both_targets(self) -> None:
        agentos = source("user/Makefile")
        baseline = source("baseline_ucore/user/Makefile")
        for relative in (
            "evaluation_guest/traditional_perf.c",
            "evaluation_guest/traditional_execprobe.c",
        ):
            self.assertIn(relative, agentos)
            self.assertIn(relative, baseline)
        self.assertIn("CHAPTER), traditional_perf", agentos)
        self.assertIn("CHAPTER), traditional_perf", baseline)

    def test_guest_measurements_are_observed_not_literal_evidence(self) -> None:
        guest = source("evaluation_guest/traditional_perf.c")
        self.assertFalse(hardcoded_measurement(guest))
        self.assertIn("sys_get_time", guest)
        self.assertIn("sample.ticks = now.sec * 1000ULL + now.usec / 1000ULL;", guest)
        self.assertIn("elapsed->us += end->us - start->us;", guest)
        self.assertIn("elapsed->ticks = elapsed->us / 1000ULL;", guest)
        self.assertNotIn('printf("agentos:tradperf', guest)
        for workload in (
            "cache_read_4k",
            "open_close",
            "tiny_write_fsync",
            "fork_wait",
            "warm_exec",
        ):
            self.assertIn(workload, guest)

    def test_hardcoded_duration_mutation_is_rejected(self) -> None:
        guest = source("evaluation_guest/traditional_perf.c")
        self.assertFalse(hardcoded_measurement(guest))
        self.assertTrue(hardcoded_measurement(guest + "\nduration_us = 777ULL;\n"))

    def test_campaign_builds_in_parallel_then_runs_strict_ab_ba(self) -> None:
        runner = source("scripts/run-traditional-performance.sh")
        prepare = runner.index("traditional_performance.py prepare")
        first_qemu = runner.index("scripts/agent_test_runner.py")
        render = runner.index("traditional_performance.py render")
        self.assertLess(prepare, first_qemu)
        self.assertLess(first_qemu, render)
        self.assertIn('build_agentos_sample "${sample}"', runner)
        self.assertIn('build_baseline_sample "${sample}"', runner)
        self.assertGreaterEqual(runner.count('-j"${BUILD_JOBS}"'), 4)
        self.assertRegex(
            runner,
            re.compile(
                r"if \(\( sample % 2 == 1 \)\); then\s+"
                r'run_target "\$\{sample\}" "\$\{tag\}" agentos\s+'
                r'run_target "\$\{sample\}" "\$\{tag\}" baseline\s+'
                r"else\s+"
                r'run_target "\$\{sample\}" "\$\{tag\}" baseline\s+'
                r'run_target "\$\{sample\}" "\$\{tag\}" agentos',
                re.MULTILINE,
            ),
        )

    def test_runner_attestation_binds_source_plan_and_artifacts(self) -> None:
        runner = source("scripts/run-traditional-performance.sh")
        for option in (
            "--attestation-file",
            "--source-commit",
            "--source-tree",
            "--campaign-nonce",
            "--calibration-plan-sha256",
            "--round-nonce",
            "--session-nonce",
            "--execution-nonce",
            "--toolchain-cc",
        ):
            self.assertIn(option, runner)
        self.assertIn('${target}-fs.img', runner)
        self.assertIn('${target}-run.img', runner)
        self.assertIn("build-manifest.json", runner)

    def test_dashboard_contract_forbids_status_claims(self) -> None:
        analyzer = source("host_tools/traditional_performance.py")
        self.assertIn("empirical", analyzer.casefold())
        self.assertNotRegex(analyzer, r">\s*(?:通过|passed|pass)\s*<")


if __name__ == "__main__":
    unittest.main()
