#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("run-parallel-qemu-regressions.py")
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "build" / "test-fixtures"
SPEC = importlib.util.spec_from_file_location("parallel_qemu_regressions", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load parallel QEMU runner")
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


def fixture_directory() -> tempfile.TemporaryDirectory[str]:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=FIXTURE_ROOT)


class ParallelQemuRegressionTests(unittest.TestCase):
    def git(self, root: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def init_repository(self, root: Path) -> None:
        root.mkdir()
        self.git(root, "init", "--quiet")
        self.git(root, "config", "user.name", "Runner Test")
        self.git(root, "config", "user.email", "runner@example.invalid")

    def commit_all(self, root: Path) -> None:
        self.git(root, "add", "-A")
        self.git(root, "commit", "--quiet", "-m", "fixture")

    def test_lane_assignment_is_bounded_complete_and_deterministic(self) -> None:
        first = RUNNER.assign_lanes(RUNNER.CASES, 4)
        second = RUNNER.assign_lanes(RUNNER.CASES, 4)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)
        labels = [case.label for lane in first for case in lane]
        self.assertCountEqual(labels, [case.label for case in RUNNER.CASES])
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                RUNNER.parse_args(["--jobs", "0", "--output-dir", "unused"])
            with self.assertRaises(SystemExit):
                RUNNER.parse_args(["--jobs", "9", "--output-dir", "unused"])

    def test_default_lane_and_build_budgets_are_resource_adaptive(self) -> None:
        environment = os.environ.copy()
        environment.pop("AGENTOS_QEMU_JOBS", None)
        environment.pop("AGENTOS_BUILD_JOBS", None)
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            RUNNER,
            "adaptive_jobs",
            side_effect=lambda kind: {"qemu": 3, "build": 7}[kind],
        ):
            args = RUNNER.parse_args(["--output-dir", "unused"])
        self.assertEqual(args.jobs, 3)
        self.assertEqual(args.build_jobs, 7)
        self.assertIn("agentcontract_ucore", RUNNER.AGENT_CASE_NAMES)
        self.assertIn("agent_eevdf_ucore", RUNNER.AGENT_CASE_NAMES)
        self.assertIn("agenttask_ucore", RUNNER.AGENT_CASE_NAMES)

    def test_child_environment_isolates_nested_build_and_test_controls(self) -> None:
        case = RUNNER.CASE_BY_LABEL["workflow-teardown-race"]
        with fixture_directory() as temporary:
            output = Path(temporary) / "output"
            scratch = output / "tmp"
            output.mkdir()
            scratch.mkdir()
            with mock.patch.dict(
                os.environ,
                {
                    "MAKEFLAGS": "-j99",
                    "AGENTOS_QEMU_JOBS": "7",
                    "AGENT_TEST_CASE": "wrong",
                    "WORKFLOW_TEARDOWN_RUNS": "99",
                    "BASH_FUNC_bad%%": "() { :; }",
                },
                clear=False,
            ):
                environment = RUNNER.child_environment(
                    case,
                    output,
                    scratch,
                    build_jobs=3,
                    outer_jobs=4,
                    parallel_depth=2,
                )
        self.assertNotIn("MAKEFLAGS", environment)
        self.assertNotIn("AGENT_TEST_CASE", environment)
        self.assertFalse(any(key.startswith("BASH_FUNC_") for key in environment))
        self.assertEqual(environment["AGENTOS_BUILD_JOBS"], "3")
        self.assertEqual(environment["AGENTOS_OUTER_JOBS"], "4")
        self.assertEqual(environment["AGENTOS_PARALLEL_DEPTH"], "2")
        self.assertEqual(environment["AGENTOS_QEMU_JOBS"], "1")
        self.assertEqual(environment["WORKFLOW_TEARDOWN_RUNS"], "1")

    def test_agent_environment_records_real_case_timing(self) -> None:
        case = next(case for case in RUNNER.AGENT_CASES if case.agent_case)
        with fixture_directory() as temporary:
            output = Path(temporary) / "output"
            scratch = output / "tmp"
            output.mkdir()
            scratch.mkdir()
            environment = RUNNER.child_environment(case, output, scratch)
        self.assertEqual(environment["AGENT_TEST_CASE"], case.agent_case)
        self.assertEqual(
            environment["AGENT_TEST_TIMING_FILE"],
            str(output / f"{case.label}.timing"),
        )
        self.assertEqual(environment["REQUIRE_FULL_SUITE"], "0")

    def test_build_budget_respects_parent_make_limit(self) -> None:
        self.assertEqual(RUNNER.outer_make_job_limit({"MAKEFLAGS": "-j12"}), 12)
        self.assertEqual(
            RUNNER.outer_make_job_limit(
                {"MAKEFLAGS": "--jobserver-auth=3,4 --jobs=8", "MFLAGS": "-j6"}
            ),
            6,
        )
        self.assertEqual(RUNNER.bounded_build_jobs(24, {"MAKEFLAGS": "-j8"}), (7, 8))
        self.assertEqual(RUNNER.bounded_build_jobs(4, {}), (4, None))
        self.assertEqual(RUNNER.allocate_build_jobs(7, 3), (3, 2, 2))
        self.assertEqual(RUNNER.build_jobs_per_lane(24, 4), 6)

    def test_live_workspace_copy_keeps_dirty_and_untracked_product_files(self) -> None:
        with fixture_directory() as temporary:
            base = Path(temporary)
            root = base / "repo"
            self.init_repository(root)
            (root / ".gitignore").write_text("build/\n", encoding="ascii")
            (root / "tracked.txt").write_text("committed\n", encoding="ascii")
            self.commit_all(root)

            (root / "tracked.txt").write_text("dirty\n", encoding="ascii")
            (root / "new.txt").write_text("untracked\n", encoding="ascii")
            (root / "build").mkdir()
            (root / "build" / "ignored.txt").write_text("ignored\n", encoding="ascii")

            paths = RUNNER.workspace_paths(root)
            self.assertIn("tracked.txt", paths)
            self.assertIn("new.txt", paths)
            self.assertNotIn("build/ignored.txt", paths)
            lane = base / "lane"
            RUNNER.materialize_lane(root, lane, paths)
            self.assertEqual((lane / "tracked.txt").read_text(encoding="ascii"), "dirty\n")
            self.assertEqual((lane / "new.txt").read_text(encoding="ascii"), "untracked\n")
            self.assertFalse((lane / ".git").exists())
            self.assertFalse((lane / "build").exists())
            RUNNER.remove_lane(root, lane)
            self.assertFalse(lane.exists())

    def test_run_case_keeps_output_and_cleans_temporary_directory(self) -> None:
        with fixture_directory() as temporary:
            root = Path(temporary)
            lane = root / "lane"
            output = root / "output"
            (lane / "scripts").mkdir(parents=True)
            output.mkdir()
            script = lane / "scripts" / "case.py"
            script.write_text(
                "import os\n"
                "print('fixture-output')\n"
                "assert os.environ['AGENTOS_QEMU_JOBS'] == '1'\n",
                encoding="ascii",
            )
            case = RUNNER.RegressionCase("fixture", "scripts/case.py", 1)
            result = RUNNER.run_case(1, lane, case, output, sys.executable, timeout=5)
            self.assertEqual(result.status, 0)
            self.assertIn("fixture-output", result.log_file.read_text(encoding="utf-8"))
            self.assertFalse((output / "fixture" / "tmp").exists())

    def test_run_case_reports_failure_and_timeout(self) -> None:
        with fixture_directory() as temporary:
            root = Path(temporary)
            lane = root / "lane"
            output = root / "output"
            (lane / "scripts").mkdir(parents=True)
            output.mkdir()
            (lane / "scripts" / "fail.py").write_text(
                "print('real failure detail')\nraise SystemExit(7)\n",
                encoding="ascii",
            )
            failed_case = RUNNER.RegressionCase("failed", "scripts/fail.py", 1)
            failed = RUNNER.run_case(
                1, lane, failed_case, output, sys.executable, timeout=5
            )
            self.assertEqual(failed.status, 7)
            self.assertIn("real failure detail", failed.log_file.read_text(encoding="utf-8"))

            (lane / "scripts" / "slow.py").write_text(
                "import time\ntime.sleep(10)\n",
                encoding="ascii",
            )
            slow_case = RUNNER.RegressionCase("slow", "scripts/slow.py", 1)
            slow = RUNNER.run_case(
                1, lane, slow_case, output, sys.executable, timeout=1
            )
            self.assertEqual(slow.status, 124)
            self.assertIn("timeout after 1s", slow.log_file.read_text(encoding="utf-8"))
            self.assertFalse((output / "slow" / "tmp").exists())

    def test_agent_case_requires_timing_output(self) -> None:
        with fixture_directory() as temporary:
            root = Path(temporary)
            lane = root / "lane"
            output = root / "output"
            (lane / "scripts").mkdir(parents=True)
            output.mkdir()
            script = lane / "scripts" / "agent.py"
            script.write_text("print('agent ran')\n", encoding="ascii")
            case = RUNNER.RegressionCase(
                "agent", "scripts/agent.py", 1, agent_case="agent_ucore"
            )
            missing = RUNNER.run_case(
                1, lane, case, output, sys.executable, timeout=5
            )
            self.assertEqual(missing.status, 65)

            script.write_text(
                "import os\nfrom pathlib import Path\n"
                "Path(os.environ['AGENT_TEST_TIMING_FILE']).write_text('agent\\t1\\n')\n"
                "print('agent ran')\n",
                encoding="ascii",
            )
            passing_case = RUNNER.RegressionCase(
                "agent-ok", "scripts/agent.py", 1, agent_case="agent_ucore"
            )
            passing = RUNNER.run_case(
                1, lane, passing_case, output, sys.executable, timeout=5
            )
            self.assertEqual(passing.status, 0)
            self.assertTrue(passing.timing_file.is_file())

    def test_cli_runs_current_workspace_and_writes_plain_summary(self) -> None:
        with fixture_directory() as temporary:
            base = Path(temporary)
            root = base / "repo"
            self.init_repository(root)
            (root / ".gitignore").write_text("build/\n", encoding="ascii")
            scripts = root / "scripts"
            scripts.mkdir()
            runner = scripts / "run-proc-reap-tests.sh"
            runner.write_text("print('first implementation')\n", encoding="ascii")
            self.commit_all(root)

            output = base / "passing-output"
            status = RUNNER.main(
                [
                    "--root",
                    str(root),
                    "--output-dir",
                    str(output),
                    "--work-root",
                    str(base / "work"),
                    "--jobs",
                    "1",
                    "--build-jobs",
                    "2",
                    "--timeout",
                    "5",
                    "--bash",
                    sys.executable,
                    "--case",
                    "proc-reap",
                ]
            )
            self.assertEqual(status, 0)
            summary = json.loads((output / "run-summary.json").read_text(encoding="ascii"))
            self.assertEqual(summary["case_order"], ["proc-reap"])
            self.assertEqual(summary["results"][0]["status"], 0)
            self.assertEqual(summary["cleanup_errors"], [])
            self.assertEqual(list((base / "work").glob("agentos-qemu-lanes-*")), [])
            self.assertIn(
                "first implementation",
                (output / "proc-reap" / "proc-reap.log").read_text(encoding="utf-8"),
            )

            # The second run deliberately uses an uncommitted fix/regression.
            runner.write_text(
                "print('dirty implementation failed')\nraise SystemExit(9)\n",
                encoding="ascii",
            )
            failed_output = base / "failed-output"
            failed_status = RUNNER.main(
                [
                    "--root",
                    str(root),
                    "--output-dir",
                    str(failed_output),
                    "--work-root",
                    str(base / "work"),
                    "--jobs",
                    "1",
                    "--build-jobs",
                    "1",
                    "--timeout",
                    "5",
                    "--bash",
                    sys.executable,
                    "--case",
                    "proc-reap",
                ]
            )
            self.assertEqual(failed_status, 1)
            failed_summary = json.loads(
                (failed_output / "run-summary.json").read_text(encoding="ascii")
            )
            self.assertEqual(failed_summary["results"][0]["status"], 9)
            self.assertIn(
                "dirty implementation failed",
                (failed_output / "proc-reap" / "proc-reap.log").read_text(
                    encoding="utf-8"
                ),
            )

    def test_case_selection_rejects_duplicates_and_unknown_names(self) -> None:
        selected = RUNNER.select_cases(["file-resource", "proc-reap"])
        self.assertEqual(
            [case.label for case in selected], ["proc-reap", "file-resource"]
        )
        with self.assertRaisesRegex(RUNNER.RegressionError, "duplicate"):
            RUNNER.select_cases(["proc-reap", "proc-reap"])
        with self.assertRaisesRegex(RUNNER.RegressionError, "unknown"):
            RUNNER.select_cases(["not-a-case"])


if __name__ == "__main__":
    unittest.main()
