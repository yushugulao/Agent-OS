#!/usr/bin/env python3

from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


RUNNER = Path(__file__).with_name("run-parallel-tests.py")


class ParallelTestRunnerTests(unittest.TestCase):
    def make_test(self, root: Path, name: str, body: str) -> None:
        (root / name).write_text(body, encoding="ascii")

    def invoke(
        self,
        root: Path,
        jobs: int,
        *tests: str,
        python: str = sys.executable,
        env: dict[str, str] | None = None,
        extra: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        if env is None:
            env = os.environ.copy()
            for name in (
                "AGENTOS_BUILD_JOBS",
                "AGENTOS_OUTER_JOBS",
                "AGENTOS_PARALLEL_DEPTH",
                "AGENTOS_QEMU_JOBS",
                "AGENTOS_TEST_JOBS",
            ):
                env.pop(name, None)
        return subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "--jobs",
                str(jobs),
                "--python",
                python,
                *extra,
                *tests,
            ],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            env=env,
        )

    def test_runs_concurrently_and_prints_in_inventory_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            for name in ("first.py", "second.py"):
                self.make_test(
                    root,
                    name,
                    "from pathlib import Path\n"
                    "import time\n"
                    f"Path('{name}.ready').touch()\n"
                    "deadline = time.monotonic() + 3\n"
                    "while len(list(Path('.').glob('*.ready'))) != 2:\n"
                    "    if time.monotonic() >= deadline:\n"
                    "        raise SystemExit(9)\n"
                    "    time.sleep(0.01)\n"
                    f"print('{name}')\n",
                )
            result = self.invoke(root, 2, "first.py", "second.py")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertLess(result.stdout.index("first.py status=ok"),
                            result.stdout.index("second.py status=ok"))
            self.assertIn("total=2 failed=0 jobs=2", result.stdout)

    def test_evaluation_smoke_reuses_the_parallel_host_runner(self) -> None:
        source = RUNNER.with_name("run-evaluation-suite.sh").read_text(
            encoding="utf-8"
        )
        smoke = source.split("run_smoke() {", 1)[1].split(
            "\nrun_scenario_campaign()", 1
        )[0]
        self.assertIn('PARALLEL_TEST_RUNNER="scripts/run-parallel-tests.py"', source)
        self.assertIn("evaluation_jobs host AGENTOS_TEST_JOBS 24", smoke)
        self.assertIn('"${PARALLEL_TEST_RUNNER}"', smoke)
        self.assertNotIn('"${PYTHON_BIN}" "${test}"', smoke)

    def test_reports_every_failure_and_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            self.make_test(root, "ok.py", "print('ok payload')\n")
            self.make_test(root, "bad.py", "print('bad payload')\nraise SystemExit(7)\n")
            result = self.invoke(root, 2, "ok.py", "bad.py")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("ok.py status=ok", result.stdout)
            self.assertIn("bad.py status=failed", result.stdout)
            self.assertIn("bad.py status=failed returncode=7", result.stdout)
            self.assertIn("total=2 failed=1 jobs=2", result.stdout)

    def test_selected_python_failure_is_reported_for_every_test(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            self.make_test(root, "one.py", "pass\n")
            self.make_test(root, "two.py", "pass\n")
            missing = str(root / "missing-python")
            result = self.invoke(
                root, 2, "one.py", "two.py", python=missing
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(result.stdout.count("returncode=126"), 2)
            self.assertEqual(result.stdout.count("cannot start Python"), 2)

    def test_child_environment_drops_build_and_python_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            self.make_test(
                root,
                "environment.py",
                "import os\n"
                "from pathlib import Path\n"
                "blocked = ('BASH_ENV', 'ENV', 'FINAL_EVIDENCE_STAGE', "
                "'GNUMAKEFLAGS', 'MAKEFILES', "
                "'MAKEFLAGS', 'MAKEOVERRIDES', 'MFLAGS', 'PYTHONHOME', "
                "'PYTHONINSPECT', 'PYTHONPATH', 'PYTHONSTARTUP')\n"
                "assert not any(name in os.environ for name in blocked)\n"
                "assert not any(name.startswith('BASH_FUNC_') for name in os.environ)\n"
                "assert os.environ['AGENTOS_TEST_JOBS'] == '1'\n"
                "assert os.environ['AGENTOS_QEMU_JOBS'] == '1'\n"
                "assert os.environ['AGENTOS_PARALLEL_DEPTH'] == '1'\n"
                "assert os.environ['AGENTOS_OUTER_JOBS'] == '1'\n"
                "assert os.environ['PYTHONHASHSEED'] == '0'\n"
                "assert os.environ['PYTHONNOUSERSITE'] == '1'\n"
                "if os.name == 'nt':\n"
                "    assert os.environ['TMPDIR'] == os.environ['TEMP']\n"
                "    assert os.environ['TMPDIR'] == os.environ['TMP']\n"
                "assert Path(os.environ['TMPDIR']).is_dir()\n",
            )
            environment = os.environ.copy()
            for name in (
                "AGENTOS_BUILD_JOBS",
                "AGENTOS_OUTER_JOBS",
                "AGENTOS_PARALLEL_DEPTH",
            ):
                environment.pop(name, None)
            environment.update(
                {
                    "BASH_ENV": "poison",
                    "FINAL_EVIDENCE_STAGE": "poison-stage",
                    "MAKEFLAGS": "-j99 --eval=poison",
                    "MAKEFILES": "poison.mk",
                    "PYTHONPATH": "poison",
                    "PYTHONSTARTUP": "poison.py",
                    "BASH_FUNC_poison%%": "() { :; }",
                }
            )
            result = self.invoke(
                root, 1, "environment.py", env=environment
            )
            self.assertEqual(result.returncode, 0, result.stdout)

    def test_rejects_invalid_job_count_and_duplicate_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            self.make_test(root, "one.py", "pass\n")
            self.assertEqual(self.invoke(root, 0, "one.py").returncode, 2)
            duplicate = self.invoke(root, 1, "one.py", "one.py")
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn("duplicate test", duplicate.stdout)
            environment = os.environ.copy()
            environment["AGENTOS_HOST_TEST_TIMEOUT"] = "invalid"
            invalid_timeout = self.invoke(
                root, 1, "one.py", env=environment
            )
            self.assertEqual(invalid_timeout.returncode, 2)
            self.assertIn("invalid int value", invalid_timeout.stdout)
            environment = os.environ.copy()
            environment["AGENTOS_BUILD_JOBS"] = "25"
            invalid_build_budget = self.invoke(
                root, 1, "one.py", env=environment
            )
            self.assertEqual(invalid_build_budget.returncode, 2)
            self.assertIn("must not exceed 24", invalid_build_budget.stdout)

    def test_timeout_kills_test_and_keeps_replay_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            failure_root = root / "failures"
            self.make_test(root, "slow.py", "import time\ntime.sleep(10)\n")
            started = __import__("time").monotonic()
            result = self.invoke(
                root,
                1,
                "slow.py",
                extra=(
                    "--timeout",
                    "1",
                    "--failure-root",
                    str(failure_root),
                ),
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertLess(__import__("time").monotonic() - started, 5)
            self.assertIn("returncode=124", result.stdout)
            manifests = list(failure_root.glob("*/replay.json"))
            self.assertEqual(len(manifests), 1)
            payload = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["tests"][0]["returncode"], 124)
            self.assertEqual(payload["replay_argv"][-1], "slow.py")

    def test_formal_evidence_keeps_shared_tests_parallel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            for name in ("one.py", "two.py"):
                self.make_test(
                    root,
                    name,
                    "from pathlib import Path\n"
                    "import time\n"
                    f"Path('{name}.ready').touch()\n"
                    "deadline = time.monotonic() + 3\n"
                    "while len(list(Path('.').glob('*.ready'))) != 2:\n"
                    "    if time.monotonic() >= deadline:\n"
                    "        raise SystemExit(9)\n"
                    "    time.sleep(0.01)\n",
                )
            environment = os.environ.copy()
            for name in (
                "AGENTOS_BUILD_JOBS",
                "AGENTOS_OUTER_JOBS",
                "AGENTOS_PARALLEL_DEPTH",
            ):
                environment.pop(name, None)
            environment["FINAL_EVIDENCE_STAGE"] = str(root / "stage")
            result = self.invoke(
                root, 2, "one.py", "two.py", env=environment
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("start total=2 jobs=2", result.stdout)
            self.assertIn("exclusive=0 evidence=1", result.stdout)
            self.assertIn("total=2 failed=0 jobs=2", result.stdout)

    def test_exclusive_test_is_an_inventory_ordered_barrier(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            self.make_test(
                root,
                "before.py",
                "from pathlib import Path\n"
                "import time\n"
                "assert not Path('exclusive.running').exists()\n"
                "Path('before.running').touch()\n"
                "time.sleep(0.1)\n"
                "Path('before.running').unlink()\n",
            )
            self.make_test(
                root,
                "exclusive.py",
                "from pathlib import Path\n"
                "import time\n"
                "assert not list(Path('.').glob('*.running'))\n"
                "Path('exclusive.running').touch()\n"
                "time.sleep(0.1)\n"
                "Path('exclusive.running').unlink()\n",
            )
            self.make_test(
                root,
                "after.py",
                "from pathlib import Path\n"
                "assert not Path('exclusive.running').exists()\n",
            )
            result = self.invoke(
                root,
                3,
                "before.py",
                "exclusive.py",
                "after.py",
                extra=("--exclusive-test", "exclusive.py"),
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("exclusive=1", result.stdout)

    def test_nested_job_budgets_are_partitioned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            for name in ("one.py", "two.py"):
                self.make_test(
                    root,
                    name,
                    "import os\n"
                    "assert os.environ['AGENTOS_BUILD_JOBS'] == '4'\n"
                    "assert os.environ['AGENTOS_TEST_JOBS'] == '1'\n"
                    "assert os.environ['AGENTOS_QEMU_JOBS'] == '1'\n"
                    "assert os.environ['AGENTOS_OUTER_JOBS'] == '6'\n"
                    "assert os.environ['AGENTOS_PARALLEL_DEPTH'] == '3'\n",
                )
            environment = os.environ.copy()
            environment.update(
                {
                    "AGENTOS_BUILD_JOBS": "8",
                    "AGENTOS_OUTER_JOBS": "3",
                    "AGENTOS_PARALLEL_DEPTH": "2",
                }
            )
            result = self.invoke(
                root, 2, "one.py", "two.py", env=environment
            )
            self.assertEqual(result.returncode, 0, result.stdout)

    def test_exclusive_selection_must_reference_the_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            self.make_test(root, "one.py", "pass\n")
            result = self.invoke(
                root,
                1,
                "one.py",
                extra=("--exclusive-test", "missing.py"),
            )
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("not in the test inventory", result.stdout)


if __name__ == "__main__":
    unittest.main()
