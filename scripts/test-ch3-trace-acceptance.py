#!/usr/bin/env python3
"""检查 ch3_trace 动态验收是否持续接入发布前门禁。"""

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run-ch3-trace-test.sh"
FULL_VERIFY = ROOT / "scripts" / "run-full-verification.sh"
PARALLEL_QEMU = ROOT / "scripts" / "run-parallel-qemu-regressions.py"
MAKEFILE = ROOT / "Makefile"


class Ch3TraceAcceptanceContract(unittest.TestCase):
    def test_runner_builds_and_executes_the_real_guest(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        for required in (
            "CHAPTER=3",
            "INIT_PROC=ch3_trace",
            '"${TMPDIR_CH3}/user-target/bin/ch3_trace"',
            "scripts/agent_test_runner.py",
            '--marker "Test trace OK!"',
            "--marker-mode exact-line",
            'grep -Fxq "string from task trace test"',
            'grep -Fxc "Test trace OK!"',
        ):
            self.assertIn(required, source)

    def test_build_is_adaptive_but_qemu_is_exclusive(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("scripts/resource-jobs.py --kind build", source)
        self.assertIn('make -j"${AGENTOS_BUILD_JOBS}"', source)
        self.assertIn('"${TMPDIR_CH3}/run.img"', source)
        self.assertEqual(source.count("scripts/agent_test_runner.py"), 1)
        self.assertNotIn("run-parallel-qemu-regressions.py", source)
        self.assertNotIn(
            'RegressionCase("ch3-trace"',
            PARALLEL_QEMU.read_text(encoding="utf-8"),
        )

    def test_full_verification_records_independent_raw_evidence_step(self) -> None:
        source = FULL_VERIFY.read_text(encoding="utf-8")
        gate = '[full-verify] ch3 trace compatibility'
        suite = '[full-verify] AgentOS kernel tests'
        self.assertEqual(source.count(gate), 1)
        self.assertEqual(source.count("make ch3-trace-test"), 1)
        self.assertLess(source.index(gate), source.index(suite))
        gate_line = source.rfind("\n", 0, source.index(gate))
        before_gate = source[:gate_line].rstrip().splitlines()
        self.assertEqual(before_gate[-1], "evidence_step_begin")
        block = source[source.index(gate) : source.index(suite)]
        self.assertIn('AGENTOS_BUILD_JOBS="${AGENTOS_BUILD_JOBS}"', block)
        self.assertIn('CH3_TRACE_OUTPUT_DIR="${ch3_output}"', block)
        self.assertIn('scripts/validate-kernel-test-log.py', block)
        self.assertIn('--profile ch3-trace', block)
        self.assertIn(
            'evidence_publish_file "${ch3_output}/guest.log" "ch3-trace-guest.log"',
            block,
        )
        self.assertIn('evidence_step_end "ch3-trace" "ch3-trace-guest.log"', block)
        self.assertNotIn("run-parallel-qemu-regressions.py", block)

    def test_make_entry_and_selftest_are_registered(self) -> None:
        source = MAKEFILE.read_text(encoding="utf-8")
        self.assertIn("\nch3-trace-test:\n", source)
        self.assertIn("scripts/run-ch3-trace-test.sh", source)
        self.assertIn("scripts/test-ch3-trace-acceptance.py", source)

    @unittest.skipUnless(shutil.which("bash"), "需要 bash 执行语法检查")
    def test_runner_shell_syntax(self) -> None:
        subprocess.run(
            ["bash", "-n", RUNNER.relative_to(ROOT).as_posix()],
            check=True,
            cwd=ROOT,
        )


if __name__ == "__main__":
    unittest.main()
