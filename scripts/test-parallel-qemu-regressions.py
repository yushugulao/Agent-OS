#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("run-parallel-qemu-regressions.py")
TRUSTED_PYTHON_CHILD = Path(__file__).with_name("trusted-python-child.py")
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "build" / "test-fixtures"
SPEC = importlib.util.spec_from_file_location("parallel_qemu_regressions", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load parallel QEMU runner")
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


def fixture_directory() -> tempfile.TemporaryDirectory[str]:
    """把动态脚本置于正式 Python 始终信任的仓库根内。"""

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
            with mock.patch.dict(
                os.environ, {"AGENTOS_BUILD_JOBS": "invalid"}, clear=False
            ):
                with self.assertRaises(SystemExit):
                    RUNNER.parse_args(["--output-dir", "unused"])

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
        self.assertNotIn("labbench_ucore", RUNNER.AGENT_CASE_NAMES)

    def test_child_environment_drops_nested_make_and_parent_evidence(self) -> None:
        case = RUNNER.CASE_BY_LABEL["observe-recovery"]
        with fixture_directory() as temp_name, mock.patch.dict(
            os.environ,
            {
                "MAKEFLAGS": "-j99 --eval=bad",
                "MFLAGS": "-j99",
                "AGENTOS_BUILD_JOBS": "99",
                "FINAL_EVIDENCE_STAGE": "/shared/stage",
                "EVIDENCE_INCOMING_DIR": "/shared/incoming",
                "EVIDENCE_GUEST_LOG_FILE": "/shared/guest",
                "BASH_ENV": "/hostile/bash-env",
                "ENV": "/hostile/env",
                "CDPATH": "/hostile/cdpath",
            },
            clear=False,
        ):
            output = Path(temp_name)
            temporary_root = output / "lane"
            environment = RUNNER.child_environment(
                case, output, temporary_root
            )
        for key in ("MAKEFLAGS", "MFLAGS", "BASH_ENV", "ENV", "CDPATH"):
            self.assertNotIn(key, environment)
        self.assertEqual(environment["AGENTOS_BUILD_JOBS"], "2")
        self.assertEqual(environment["AGENTOS_TEST_JOBS"], "1")
        self.assertEqual(environment["AGENTOS_QEMU_JOBS"], "1")
        self.assertEqual(environment["AGENTOS_OUTER_JOBS"], "1")
        self.assertEqual(environment["AGENTOS_PARALLEL_DEPTH"], "1")
        self.assertEqual(environment["TMPDIR"], str(temporary_root))
        self.assertEqual(environment["TEMP"], str(temporary_root))
        self.assertEqual(environment["TMP"], str(temporary_root))
        self.assertNotIn("FINAL_EVIDENCE_STAGE", environment)
        self.assertNotIn("EVIDENCE_INCOMING_DIR", environment)
        self.assertEqual(
            environment["EVIDENCE_GUEST_LOG_FILE"],
            str(output / "observe-recovery.guest"),
        )
        self.assertEqual(
            environment["OBSERVE_RECOVERY_SNAPSHOT_FILE"],
            str(output / "observe-recovery-before-reap.img"),
        )
        self.assertEqual(RUNNER.execution_jobs(4, False), 4)
        self.assertEqual(RUNNER.execution_jobs(4, True), 4)
        self.assertEqual(RUNNER.build_jobs_per_lane(24, 4), 6)
        self.assertEqual(RUNNER.build_jobs_per_lane(3, 4), 1)
        self.assertEqual(RUNNER.allocate_build_jobs(10, 4), (3, 3, 2, 2))
        self.assertEqual(sum(RUNNER.allocate_build_jobs(11, 4)), 11)
        self.assertEqual(
            RUNNER.bounded_build_jobs(
                12,
                {"MAKEFLAGS": "-j4 --jobserver-auth=3,4", "MFLAGS": ""},
            ),
            (3, 4),
        )
        self.assertEqual(
            RUNNER.bounded_build_jobs(
                12, {"MAKEFLAGS": "--jobs=7", "MFLAGS": "-j 5"}
            ),
            (4, 5),
        )
        self.assertEqual(
            RUNNER.bounded_build_jobs(6, {"MAKEFLAGS": "--jobserver-auth=3,4"}),
            (6, None),
        )

    def test_agent_suite_uses_isolated_targeted_runs_and_merges_receipts(self) -> None:
        case = RUNNER.AGENT_CASES[0]
        with fixture_directory() as temp_name:
            output = Path(temp_name) / "output"
            case_output = output / case.label
            case_output.mkdir(parents=True)
            environment = RUNNER.child_environment(
                case, case_output, Path(temp_name) / "lane"
            )
            self.assertEqual(environment["AGENT_TEST_CASE"], case.label)
            self.assertEqual(environment["AGENT_TEST_DURATION_PROFILE"], "none")
            self.assertEqual(environment["MARKER_GRACE_SECONDS"], "2s")
            self.assertEqual(environment["REQUIRE_FULL_SUITE"], "0")
            self.assertEqual(
                environment["AGENT_TEST_GUEST_LOG_FILE"],
                str(case_output / f"{case.label}.guest"),
            )
            expected = RUNNER.expected_artifacts(case, case_output)
            self.assertIn(case_output / f"{case.label}.timing", expected)

            stdout = case_output / f"{case.label}.stdout"
            guest = case_output / f"{case.label}.guest"
            combined = case_output / f"{case.label}.combined"
            timing = case_output / f"{case.label}.timing"
            stdout.write_text("stdout\n", encoding="ascii")
            guest.write_text("guest marker\n", encoding="ascii")
            combined.write_text("combined\n", encoding="ascii")
            timing.write_text(f"{case.label}\t1.250000000\n", encoding="ascii")
            now = time.time_ns()
            result = RUNNER.CaseResult(
                case=case,
                lane=1,
                status=0,
                started_ns=now,
                ended_ns=now + 1,
                stdout_file=stdout,
                guest_file=guest,
                combined_file=combined,
            )
            source = RUNNER.SourceIdentity(
                commit="a" * 40,
                tree="b" * 40,
                manifest_sha256="c" * 64,
                manifest_files=1,
            )
            RUNNER.write_reports(
                output,
                source,
                1,
                ((case,),),
                {case.label: result},
                [],
                (case,),
                "agent",
            )
            self.assertEqual(
                (output / "agent-suite-guest.log").read_text(encoding="ascii"),
                "guest marker\n",
            )
            self.assertEqual(
                (output / "agent-suite-timings.log").read_text(encoding="ascii"),
                f"{case.label}\t1.250000000\n",
            )
            summary = json.loads(
                (output / "run-summary.json").read_text(encoding="ascii")
            )
            self.assertEqual(summary["suite"], "agent")
            def fake_git(_root, *arguments, **_kwargs):
                value = source.tree if arguments[-1] == "HEAD^{tree}" else source.commit
                return value.encode("ascii")

            with mock.patch.object(RUNNER, "git", side_effect=fake_git):
                verified = RUNNER.verify_reports(
                    Path(temp_name), output, "agent", (case,)
                )
            plan = RUNNER.write_import_plan(output, verified, "agent")
            rows = [line.split("\t") for line in plan.read_text().splitlines()]
            aggregate = [row for row in rows if row[0] == "agent-suite"]
            self.assertEqual(
                [row[3] for row in aggregate],
                ["agent-suite-timings.log", "agent-suite-guest.log"],
            )
            for row in aggregate:
                payload = (output / row[4]).read_bytes()
                self.assertEqual(int(row[5]), len(payload))
                self.assertEqual(row[6], hashlib.sha256(payload).hexdigest())

    @unittest.skipUnless(shutil.which("git"), "git is required")
    def test_clean_commit_materializes_independent_worktrees(self) -> None:
        with fixture_directory() as temp_name:
            base = Path(temp_name)
            root = base / "repo"
            root.mkdir()
            self.git(root, "init", "--quiet")
            (root / "tracked.txt").write_text("base\n", encoding="ascii")
            (root / ".gitignore").write_text(
                "build\n/tokens.txt\n/*_api.txt\n", encoding="ascii"
            )
            self.git(root, "add", "tracked.txt", ".gitignore")
            self.git(
                root,
                "-c",
                "user.name=AgentOS test",
                "-c",
                "user.email=agentos@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "base",
            )
            source = RUNNER.source_identity(root)
            lanes = [base / "lane-1", base / "lane-2"]
            try:
                for lane in lanes:
                    RUNNER.materialize_lane(root, lane, source)
                    self.assertEqual(
                        (lane / "tracked.txt").read_text(encoding="ascii"),
                        "base\n",
                    )
                self.assertNotEqual(lanes[0].resolve(), lanes[1].resolve())
            finally:
                for lane in reversed(lanes):
                    if lane.exists():
                        RUNNER.remove_lane(root, lane)
                RUNNER.git(root, "worktree", "prune")
            (root / "tracked.txt").write_text("dirty\n", encoding="ascii")
            with self.assertRaisesRegex(RUNNER.RegressionError, "clean committed"):
                RUNNER.source_identity(root)

    @unittest.skipUnless(shutil.which("git"), "git is required")
    def test_dirty_source_is_snapshotted_without_touching_the_real_index(self) -> None:
        with fixture_directory() as temp_name:
            base = Path(temp_name)
            root = base / "repo"
            root.mkdir()
            self.git(root, "init", "--quiet")
            (root / "tracked.txt").write_text("base\n", encoding="ascii")
            (root / ".gitignore").write_text(
                "build\n/tokens.txt\n/*_api.txt\n", encoding="ascii"
            )
            self.git(root, "add", "tracked.txt", ".gitignore")
            self.git(
                root,
                "-c",
                "user.name=AgentOS test",
                "-c",
                "user.email=agentos@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "base",
            )
            (root / "tracked.txt").write_text("dirty\n", encoding="ascii")
            (root / "new.txt").write_text("untracked\n", encoding="ascii")
            (root / "build").mkdir()
            (root / "build" / "artifact").write_text("ignored\n", encoding="ascii")
            (root / "tokens.txt").write_text("secret\n", encoding="ascii")
            (root / "deepseek_api.txt").write_text("secret\n", encoding="ascii")
            index_before = subprocess.run(
                ["git", "-C", str(root), "diff", "--cached", "--binary"],
                stdout=subprocess.PIPE,
                check=True,
            ).stdout
            refs_before = subprocess.run(
                ["git", "-C", str(root), "show-ref"],
                stdout=subprocess.PIPE,
                check=True,
            ).stdout
            with mock.patch.dict(
                os.environ,
                {
                    "GIT_INDEX_FILE": str(root / "poison-index"),
                    "GIT_DIR": str(root / "missing-git-dir"),
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "alias.add",
                    "GIT_CONFIG_VALUE_0": "!false",
                },
                clear=False,
            ):
                source = RUNNER.snapshot_source(root, False)
            self.assertTrue(source.dirty)
            lane = base / "lane"
            try:
                RUNNER.materialize_lane(root, lane, source)
                self.assertEqual(
                    (lane / "tracked.txt").read_text(encoding="ascii"), "dirty\n"
                )
                self.assertEqual(
                    (lane / "new.txt").read_text(encoding="ascii"), "untracked\n"
                )
                self.assertFalse((lane / "build").exists())
                self.assertFalse((lane / "tokens.txt").exists())
                self.assertFalse((lane / "deepseek_api.txt").exists())
            finally:
                if lane.exists():
                    RUNNER.remove_lane(root, lane)
                RUNNER.git(root, "worktree", "prune")
            index_after = subprocess.run(
                ["git", "-C", str(root), "diff", "--cached", "--binary"],
                stdout=subprocess.PIPE,
                check=True,
            ).stdout
            self.assertEqual(index_after, index_before)
            refs_after = subprocess.run(
                ["git", "-C", str(root), "show-ref"],
                stdout=subprocess.PIPE,
                check=True,
            ).stdout
            self.assertEqual(refs_after, refs_before)
            with self.assertRaisesRegex(RUNNER.RegressionError, "clean committed"):
                RUNNER.snapshot_source(root, True)

            with mock.patch.object(
                RUNNER,
                "source_manifest",
                side_effect=[("a", 1), ("b", 1)] * 3,
            ):
                with self.assertRaisesRegex(
                    RUNNER.RegressionError, "changed while source was captured"
                ):
                    RUNNER.snapshot_source(root, False)
            self.git(root, "add", "-f", "tokens.txt")
            with self.assertRaisesRegex(
                RUNNER.RegressionError, "secret-like source path"
            ):
                RUNNER.source_manifest(root)

    def test_case_status_and_guest_log_are_fail_closed(self) -> None:
        with fixture_directory() as temp_name:
            root = Path(temp_name)
            lane = root / "lane"
            output = root / "output"
            (lane / "scripts").mkdir(parents=True)
            output.mkdir()
            runner = lane / "scripts" / "fixture.py"
            runner.write_text(
                "import os\n"
                "assert 'MAKEFLAGS' not in os.environ\n"
                "assert 'FINAL_EVIDENCE_STAGE' not in os.environ\n"
                "with open(os.environ['EVIDENCE_GUEST_LOG_FILE'], 'a', encoding='ascii') as f:\n"
                "    f.write('fixture guest\\n')\n"
                "print('fixture stdout')\n",
                encoding="ascii",
            )
            case = RUNNER.RegressionCase("fixture", "scripts/fixture.py", 1)
            result = RUNNER.run_case(1, lane, case, output, sys.executable)
            self.assertEqual(result.status, 0)
            self.assertFalse((output / case.label / "tmp").exists())
            self.assertIn(b"fixture stdout", result.combined_file.read_bytes())
            self.assertIn(b"fixture guest", result.combined_file.read_bytes())

            empty = lane / "scripts" / "empty.py"
            empty.write_text("pass\n", encoding="ascii")
            empty_case = RUNNER.RegressionCase("empty", "scripts/empty.py", 1)
            empty_result = RUNNER.run_case(
                1, lane, empty_case, output, sys.executable
            )
            self.assertEqual(empty_result.status, 65)
            self.assertIn("missing or empty artifacts", empty_result.detail)

    def test_whole_case_timeout_terminates_the_runner(self) -> None:
        with fixture_directory() as temp_name:
            root = Path(temp_name)
            lane = root / "lane"
            output = root / "output"
            (lane / "scripts").mkdir(parents=True)
            output.mkdir()
            runner = lane / "scripts" / "slow.py"
            survivor = root / "survivor"
            child = (
                "import pathlib,time;time.sleep(2);"
                f"pathlib.Path({str(survivor)!r}).touch()"
            )
            runner.write_text(
                "import subprocess,sys,time\n"
                f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
                "time.sleep(10)\n",
                encoding="utf-8",
            )
            case = RUNNER.RegressionCase("slow", "scripts/slow.py", 1)
            started = time.monotonic()
            result = RUNNER.run_case(
                1,
                lane,
                case,
                output,
                sys.executable,
                timeout=1,
            )
            self.assertEqual(result.status, 124)
            self.assertLess(time.monotonic() - started, 5)
            self.assertIn("timeout after 1s", result.detail)
            time.sleep(1.5)
            self.assertFalse(survivor.exists())

    def test_lane_layout_is_trusted_by_formal_python_child(self) -> None:
        with fixture_directory() as temp_name:
            root = Path(temp_name) / "repo"
            root.mkdir()
            root = root.resolve()
            lane_root = root / "build" / "run" / "lane-01"
            source = lane_root / "source"
            script = source / "scripts" / "probe.py"
            script.parent.mkdir(parents=True)
            script.write_text("print('trusted-lane')\n", encoding="ascii")
            environment = os.environ.copy()
            environment["TMPDIR"] = str(lane_root)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(TRUSTED_PYTHON_CHILD),
                    "--shim",
                    sys.executable,
                    "--repo",
                    str(root),
                    str(script),
                ],
                cwd=source,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertEqual(completed.stdout.strip(), "trusted-lane")

    def test_report_merge_uses_case_inventory_order(self) -> None:
        with fixture_directory() as temp_name:
            base = Path(temp_name)
            output = base / "output"
            output.mkdir()
            source = RUNNER.SourceIdentity(
                commit="a" * 40,
                tree="b" * 40,
            )
            selected = (RUNNER.CASES[0], RUNNER.CASES[1])
            results = {}
            now = time.time_ns()
            for index, case in reversed(tuple(enumerate(selected))):
                directory = output / case.label
                directory.mkdir()
                stdout = directory / f"{case.label}.stdout"
                guest = directory / f"{case.label}.guest"
                combined = directory / f"{case.label}.combined"
                stdout.write_text("stdout\n", encoding="ascii")
                guest.write_text("guest\n", encoding="ascii")
                combined.write_text(case.label + "\n", encoding="ascii")
                results[case.label] = RUNNER.CaseResult(
                    case=case,
                    lane=index + 1,
                    status=7 if index else 0,
                    started_ns=now + index,
                    ended_ns=now + index + 1,
                    stdout_file=stdout,
                    guest_file=guest,
                    combined_file=combined,
                )
            lanes = ((selected[0],), (selected[1],))
            RUNNER.write_reports(output, source, 2, lanes, results, [])
            merged = (output / "combined.log").read_text(encoding="ascii")
            self.assertLess(merged.index(selected[0].label), merged.index(selected[1].label))
            summary = (output / "run-summary.json").read_text(encoding="ascii")
            self.assertLess(summary.index(selected[0].label), summary.index(selected[1].label))
            steps = (output / "steps.tsv").read_text(encoding="ascii")
            self.assertIn("\tproc-reap.log\n", steps)
            self.assertIn("\tsyscall-fairness.log\n", steps)
            self.assertNotIn("/", steps)

    def test_report_verifier_rejects_tampering_and_links(self) -> None:
        with fixture_directory() as temp_name:
            base = Path(temp_name)
            output = base / "output"
            output.mkdir()
            source = RUNNER.SourceIdentity(
                commit="a" * 40,
                tree="b" * 40,
                manifest_sha256="c" * 64,
                manifest_files=1,
            )
            selected = (RUNNER.CASES[0], RUNNER.CASES[1])
            results = {}
            now = time.time_ns()
            for index, case in enumerate(selected):
                directory = output / case.label
                directory.mkdir()
                stdout = directory / f"{case.label}.stdout"
                guest = directory / f"{case.label}.guest"
                combined = directory / f"{case.label}.combined"
                stdout.write_text("stdout\n", encoding="ascii")
                guest.write_text("guest\n", encoding="ascii")
                combined.write_text(case.label + "\n", encoding="ascii")
                results[case.label] = RUNNER.CaseResult(
                    case=case,
                    lane=index + 1,
                    status=0,
                    started_ns=now + index,
                    ended_ns=now + index + 1,
                    stdout_file=stdout,
                    guest_file=guest,
                    combined_file=combined,
                )
            RUNNER.write_reports(
                output, source, 2, ((selected[0],), (selected[1],)), results, []
            )

            def fake_git(_root, *arguments, **_kwargs):
                value = source.tree if arguments[-1] == "HEAD^{tree}" else source.commit
                return value.encode("ascii")

            with mock.patch.object(RUNNER, "git", side_effect=fake_git):
                verified = RUNNER.verify_reports(base, output, "resource", selected)
                plan = RUNNER.write_import_plan(output, verified)
                plan_rows = [line.split("\t") for line in plan.read_text().splitlines()]
                self.assertEqual(
                    [row[0] for row in plan_rows],
                    [case.label for case in selected],
                )
                self.assertTrue(all(len(row) == 7 for row in plan_rows))
                self.assertTrue(all(row[5].isdecimal() for row in plan_rows))
                self.assertTrue(all(len(row[6]) == 64 for row in plan_rows))
                summary_path = output / "run-summary.json"
                summary = json.loads(summary_path.read_text(encoding="ascii"))
                summary["requested_build_jobs"] = 1
                summary_path.write_text(json.dumps(summary), encoding="ascii")
                with self.assertRaisesRegex(RUNNER.RegressionError, "lane inventory"):
                    RUNNER.verify_reports(base, output, "resource", selected)
                summary["requested_build_jobs"] = 4
                summary_path.write_text(json.dumps(summary), encoding="ascii")
                artifact_manifest = output / "artifacts.tsv"
                original = artifact_manifest.read_text(encoding="ascii")
                rows = original.splitlines()
                rows[1] = rows[1].split("\t", 1)[0] + "\t" + rows[0].split("\t", 1)[1]
                artifact_manifest.write_text("\n".join(rows) + "\n", encoding="ascii")
                with self.assertRaisesRegex(RUNNER.RegressionError, "artifact inventory"):
                    RUNNER.verify_reports(base, output, "resource", selected)
                artifact_manifest.write_text(original, encoding="ascii")

                combined = output / selected[0].label / f"{selected[0].label}.combined"
                outside = base / "outside.log"
                outside.write_text("outside\n", encoding="ascii")
                combined.unlink()
                try:
                    combined.symlink_to(outside)
                except OSError:
                    pass
                else:
                    # 某些 MSYS 文件系统把符号链接模拟为普通文件；仅当 Python
                    # 会跟随链接时才要求拒绝。
                    if RUNNER.is_link_or_junction(combined):
                        with self.assertRaisesRegex(
                            RUNNER.RegressionError, "contains a link"
                        ):
                            RUNNER.verify_reports(base, output, "resource", selected)

            alias = base / "report-link"
            try:
                alias.symlink_to(output, target_is_directory=True)
            except OSError:
                pass
            else:
                if (
                    RUNNER.is_link_or_junction(alias)
                    or alias.resolve(strict=True) != alias.absolute()
                ):
                    with self.assertRaisesRegex(
                        RUNNER.RegressionError, "directory is invalid"
                    ):
                        RUNNER.report_output_directory(alias)

    def test_formal_wiring_keeps_profile_v7_steps_and_serial_epoch(self) -> None:
        full = Path(__file__).with_name("run-full-verification.sh").read_text(
            encoding="utf-8"
        )
        capture = Path(__file__).with_name("capture-final-evidence.py").read_text(
            encoding="utf-8"
        )
        expected_resources = [
            case.label for case in RUNNER.RESOURCE_CASES if case.label != "fs-epoch"
        ]
        block = full.split("resource_regression_cases=(", 1)[1].split(")", 1)[0]
        self.assertEqual(block.split(), expected_resources)
        contract_block = capture.split("STEP_CONTRACT = (", 1)[1].split("\n)", 1)[0]
        contract_steps = [
            line.split('"', 2)[1]
            for line in contract_block.splitlines()
            if line.lstrip().startswith('("')
        ]
        self.assertEqual(
            contract_steps,
            [
                "target-structure",
                "kernel-budgets",
                "host-platform-alignment",
                "ch3-trace",
                "agent-suite",
                "dual-platforms",
                *expected_resources,
            ],
        )
        self.assertIn("evidence_verify_parallel_run", full)
        self.assertIn("evidence_record_parallel_case", full)
        self.assertGreater(
            full.index("filesystem ordered epoch"),
            full.index("evidence_record_parallel_case"),
        )
        wiring = Path(__file__).with_name("evidence-wiring.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('read -r -a fields', wiring)
        self.assertNotIn('artifacts.tsv"', wiring)
        self.assertNotIn('done <"${run_dir}/steps.tsv"', wiring)

    @unittest.skipUnless(shutil.which("git"), "git is required")
    def test_cli_runs_isolated_lanes_and_returns_failure(self) -> None:
        shell = shutil.which("sh") or shutil.which("bash")
        if shell is None:
            self.skipTest("POSIX shell is required")
        with fixture_directory() as temp_name:
            base = Path(temp_name)
            root = base / "repo"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            fixture = (
                "set -eu\n"
                "test -z \"${MAKEFLAGS+x}\"\n"
                "test -z \"${FINAL_EVIDENCE_STAGE+x}\"\n"
                "sleep 0.25\n"
                "name=${0##*/}\n"
                "printf '%s\\n' \"$name\" > \"$EVIDENCE_GUEST_LOG_FILE\"\n"
                "printf '%s stdout\\n' \"$name\"\n"
                "case \"$name\" in *syscall*) exit 7 ;; esac\n"
            )
            for case in RUNNER.CASES[:2]:
                (root / case.runner).write_text(fixture, encoding="ascii")
            self.git(root, "init", "--quiet")
            self.git(root, "add", ".")
            self.git(
                root,
                "-c",
                "user.name=AgentOS test",
                "-c",
                "user.email=agentos@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "fixtures",
            )
            output = base / "output"
            environment = os.environ.copy()
            environment["MAKEFLAGS"] = "-j5 --jobserver-auth=3,4"
            environment.pop("MFLAGS", None)
            environment.pop("FINAL_EVIDENCE_STAGE", None)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--root",
                    str(root),
                    "--output-dir",
                    str(output),
                    "--jobs",
                    "2",
                    "--build-jobs",
                    "9",
                    "--bash",
                    shell,
                    "--case",
                    RUNNER.CASES[0].label,
                    "--case",
                    RUNNER.CASES[1].label,
                ],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(completed.returncode, 1, completed.stdout)
            summary = json.loads(
                (output / "run-summary.json").read_text(encoding="ascii")
            )
            self.assertEqual(summary["effective_jobs"], 2)
            self.assertEqual(summary["requested_build_jobs"], 9)
            self.assertEqual(summary["effective_build_jobs"], 4)
            self.assertEqual(summary["outer_make_job_limit"], 5)
            self.assertEqual(summary["lane_build_jobs"], 2)
            self.assertEqual(summary["lane_build_job_slots"], [2, 2])
            self.assertEqual(summary["allocated_build_jobs"], 4)
            self.assertEqual(summary["case_timeout_seconds"], 3600)
            self.assertEqual(
                summary["case_order"], [case.label for case in RUNNER.CASES[:2]]
            )
            self.assertEqual(
                [result["status"] for result in summary["results"]], [0, 7]
            )
            self.assertEqual(summary["replay_manifest"], "replay.json")
            replay = json.loads(
                (output / "replay.json").read_text(encoding="ascii")
            )
            self.assertEqual(
                replay["failed_cases"], [RUNNER.CASES[1].label]
            )
            self.assertEqual(replay["source"]["archive"], "source.tar")
            self.assertGreater((output / "source.tar").stat().st_size, 0)
            self.assertEqual(
                (output / "artifacts.tsv").read_text(encoding="ascii").splitlines(),
                [
                    "proc-reap.log\tproc-reap/proc-reap.combined",
                    "syscall-fairness.log\tsyscall-fairness/syscall-fairness.combined",
                ],
            )
            intervals = [
                (float(fields[1]), float(fields[2]))
                for fields in (
                    line.split("\t")
                    for line in (output / "steps.tsv")
                    .read_text(encoding="ascii")
                    .splitlines()
                )
            ]
            self.assertLess(max(start for start, _ in intervals),
                            min(end for _, end in intervals))

            evidence_stage = base / "evidence-stage"
            evidence_stage.mkdir()
            evidence_output = evidence_stage / "resource-lanes"
            evidence_environment = environment.copy()
            evidence_environment["FINAL_EVIDENCE_STAGE"] = str(evidence_stage)
            evidence = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--root",
                    str(root),
                    "--output-dir",
                    str(evidence_output),
                    "--jobs",
                    "2",
                    "--build-jobs",
                    "9",
                    "--bash",
                    shell,
                    "--case",
                    RUNNER.CASES[0].label,
                    "--case",
                    RUNNER.CASES[1].label,
                ],
                env=evidence_environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(evidence.returncode, 1, evidence.stdout)
            evidence_summary = json.loads(
                (evidence_output / "run-summary.json").read_text(encoding="ascii")
            )
            self.assertEqual(evidence_summary["requested_jobs"], 2)
            self.assertEqual(evidence_summary["effective_jobs"], 2)
            self.assertEqual(
                evidence_summary["lane_build_job_slots"], [2, 2]
            )
            evidence_intervals = [
                (float(fields[1]), float(fields[2]))
                for fields in (
                    line.split("\t")
                    for line in (evidence_output / "steps.tsv")
                    .read_text(encoding="ascii")
                    .splitlines()
                )
            ]
            self.assertLess(
                max(start for start, _ in evidence_intervals),
                min(end for _, end in evidence_intervals),
            )
            worktrees = subprocess.run(
                ["git", "-C", str(root), "worktree", "list", "--porcelain"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            ).stdout
            self.assertEqual(worktrees.count("worktree "), 1)
            self.assertEqual(list((root / "build").iterdir()), [])
            merged = (output / "combined.log").read_text(encoding="ascii")
            self.assertLess(
                merged.index(RUNNER.CASES[0].label),
                merged.index(RUNNER.CASES[1].label),
            )


if __name__ == "__main__":
    unittest.main()
