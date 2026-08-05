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
                "assert os.environ['PYTHONHASHSEED'] == '0'\n"
                "assert os.environ['PYTHONNOUSERSITE'] == '1'\n"
                "if os.name == 'nt':\n"
                "    assert os.environ['TMPDIR'] == os.environ['TEMP']\n"
                "    assert os.environ['TMPDIR'] == os.environ['TMP']\n"
                "assert Path(os.environ['TMPDIR']).is_dir()\n",
            )
            environment = os.environ.copy()
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

    def test_formal_evidence_forces_deterministic_serial_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            for name in ("one.py", "two.py"):
                self.make_test(root, name, "import time\ntime.sleep(0.05)\n")
            environment = os.environ.copy()
            environment["FINAL_EVIDENCE_STAGE"] = str(root / "stage")
            result = self.invoke(
                root, 2, "one.py", "two.py", env=environment
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("start total=2 jobs=1", result.stdout)
            self.assertIn("total=2 failed=0 jobs=1", result.stdout)


if __name__ == "__main__":
    unittest.main()
