#!/usr/bin/env python3
"""Focused regression tests for the offline contest demo."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import contest_demo
from evaluation_contract import load_suite
from test_evaluation_contract import make_log


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "0000000000000001"
COMMIT = "a" * 40
SOURCE_BYTES = b"bound source\n"
SOURCE_SAMPLE = (
    (
        "source.c",
        len(SOURCE_BYTES),
        hashlib.sha256(SOURCE_BYTES).hexdigest(),
        "b" * 40,
    ),
)
SOURCE_RECEIPT = {
    "schema": "test-measurement-source-receipt",
    "sources": [
        {
            "path": SOURCE_SAMPLE[0][0],
            "bytes": SOURCE_SAMPLE[0][1],
            "sha256": SOURCE_SAMPLE[0][2],
        }
    ],
}


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def evaluation_log() -> str:
    suite = load_suite(ROOT / "ci" / "evaluation-suite.json")
    challenge, content = make_log(suite, 0)
    assert challenge == RUN_ID
    return content


def lab_log(*, omit: str | None = None) -> str:
    lines = {
        "startup": "labdemo_ucore: startup_barrier ready=3 event_queued=1 released=3",
        "audit": "labdemo_ucore: global_audit=1 records=19 agents=3 context=1 event=1 sched=1 prefetch=1",
        "timeline": "labdemo_ucore: unified_timeline records=24 context=1 event=1 sched=1 prefetch=1",
        "provenance": "labdemo_ucore: provenance_graph edges=9 message=1 prefetch=1",
        "scenario": "labdemo_ucore: passed",
        "parent": "labdemo_ucore: parent passed",
    }
    return "\n".join(value for key, value in lines.items() if key != omit) + "\n"


def report_fixture(root: Path, *, missing_lab_marker: str | None = None):
    suite = root / "ci" / "evaluation-suite.json"
    suite.parent.mkdir(parents=True)
    suite.write_bytes((ROOT / "ci" / "evaluation-suite.json").read_bytes())
    evaluation = write(root / "evaluation-qemu.log", evaluation_log())
    lab = write(root / "lab-qemu.log", lab_log(omit=missing_lab_marker))
    artifacts = [
        write(root / name, f"{name}\n")
        for name in (
            "evaluation-kernel",
            "evaluation-fs.img",
            "lab-kernel",
            "lab-fs.img",
        )
    ]
    return root, evaluation, lab, RUN_ID, COMMIT, 83.25, artifacts


def _build(fixture):
    with mock.patch.object(
        contest_demo, "clean_source_identity", return_value=COMMIT
    ), mock.patch.object(
        contest_demo, "_measurement_source_sample", return_value=SOURCE_SAMPLE
    ), mock.patch.object(
        contest_demo,
        "build_measurement_source_receipt",
        return_value=SOURCE_RECEIPT,
    ) as source_contract:
        report = contest_demo.build_report(*fixture)
    source_contract.assert_called_once()
    snapshot_root = source_contract.call_args.args[0]
    assert snapshot_root != fixture[0]
    assert source_contract.call_args.kwargs == {"source_commit": COMMIT}
    return report


def test_report_uses_canonical_receipts_and_honest_coverage() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        fixture = report_fixture(root)
        canonical = contest_demo.validate_guest_log
        with mock.patch.object(
            contest_demo, "validate_guest_log", wraps=canonical
        ) as validator:
            report = _build(fixture)
        validator.assert_called_once()
        assert report["status"] == "partial"
        assert report["formal_claim"] is False
        assert report["qemu_boots"] == 2
        assert report["measurement_source_receipt"] == SOURCE_RECEIPT
        assert report["functional_receipt"]["functional_receipts"] == 6
        assert report["functional_receipt"]["catalog_descriptors"] >= 3
        assert all(report["tasks"][f"task{number}"]["status"] == "passed" for number in range(1, 6))
        assert report["tasks"]["task6"]["status"] == "partial"
        assert report["tasks"]["task6"]["formal_scenario_status"] == "unavailable"
        assert report["performance"]["experiment"] == "file_query_path_index"
        assert report["performance"]["loads"]["96"]["baseline_records_per_query"] == 96
        assert report["performance"]["loads"]["96"]["treatment_records_per_query"] == 1

        output = root / "output"
        contest_demo.publish(report, output)
        assert json.loads((output / "summary.json").read_text("utf-8")) == report
        page = (output / "index.html").read_text("utf-8")
        assert "6 / 6" not in page
        assert "5 完整" in page
        assert "完整 rp_* unavailable" in page
        assert "file_query_path_index" in page
        assert "N 路径遍历" in page
        assert "http://" not in page and "https://" not in page
        assert "不替代正式多启动统计" in page


def test_task2_context_and_path_index_fail_closed() -> None:
    mutations = (
        ("catalog", lambda text: "\n".join(
            line for line in text.splitlines()
            if not line.startswith("agenteval_ucore: catalog ")
        ) + "\n"),
        ("context", lambda text: "\n".join(
            line for line in text.splitlines()
            if " functional schema=1 task=task3 " not in line
        ) + "\n"),
        ("file_query_path_index", lambda text: "\n".join(
            line for line in text.splitlines()
            if "experiment=file_query_path_index " not in line
        ) + "\n"),
    )
    for label, mutate in mutations:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = list(report_fixture(Path(temporary)))
            fixture[1].write_text(mutate(fixture[1].read_text("utf-8")), encoding="utf-8")
            try:
                _build(fixture)
            except contest_demo.ContestDemoError as error:
                assert "evaluation failed" in str(error)
            else:
                raise AssertionError(f"missing {label} evidence was accepted")


def test_source_contract_and_task6_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        fixture = report_fixture(Path(temporary), missing_lab_marker="provenance")
        try:
            _build(fixture)
        except contest_demo.ContestDemoError as error:
            assert "provenance" in str(error)
        else:
            raise AssertionError("missing Task 6 marker was accepted")
    with tempfile.TemporaryDirectory() as temporary:
        fixture = report_fixture(Path(temporary))
        with mock.patch.object(
            contest_demo, "clean_source_identity", return_value=COMMIT
        ), mock.patch.object(
            contest_demo, "_measurement_source_sample", return_value=SOURCE_SAMPLE
        ), mock.patch.object(
            contest_demo,
            "build_measurement_source_receipt",
            side_effect=ValueError("forged source"),
        ):
            try:
                contest_demo.build_report(*fixture)
            except contest_demo.ContestDemoError as error:
                assert "forged source" in str(error)
            else:
                raise AssertionError("source-contract failure was masked")


def test_shared_guest_failure_classifier_is_stage_aware() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        benign = write(root / "build-riscv64-ch6b_panic.log", "compiler: success\n")
        assert contest_demo._read_guest(benign, "benign") == ["compiler: success"]
        panic = write(root / "guest.log", "[PANIC 0-0] kernel.c:1: injected\n")
        try:
            contest_demo._read_guest(panic, "panic")
        except contest_demo.ContestDemoError as error:
            assert "panic" in str(error)
        else:
            raise AssertionError("real Guest panic was accepted")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _git_text(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def test_source_identity_rejects_dirty_worktrees() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        _git(root, "init", "-q")
        _git(root, "config", "user.name", "Contest Test")
        _git(root, "config", "user.email", "contest@example.invalid")
        tracked = write(root / "tracked.txt", "clean\n")
        _git(root, "add", "tracked.txt")
        _git(root, "commit", "-q", "-m", "fixture")
        assert contest_demo.COMMIT.fullmatch(contest_demo.clean_source_identity(root))
        tracked.write_text("dirty\n", encoding="utf-8")
        try:
            contest_demo.clean_source_identity(root)
        except contest_demo.ContestDemoError as error:
            assert "dirty" in str(error)
        else:
            raise AssertionError("modified source was accepted")
        tracked.write_text("clean\n", encoding="utf-8")
        write(root / "untracked.txt", "untracked\n")
        try:
            contest_demo.clean_source_identity(root)
        except contest_demo.ContestDemoError as error:
            assert "dirty" in str(error)
        else:
            raise AssertionError("untracked source was accepted")


def test_source_identity_rejects_hidden_index_flags() -> None:
    for flag in ("--assume-unchanged", "--skip-worktree"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _git(root, "init", "-q")
            _git(root, "config", "user.name", "Contest Test")
            _git(root, "config", "user.email", "contest@example.invalid")
            write(root / "source.c", "int value = 1;\n")
            _git(root, "add", "source.c")
            _git(root, "commit", "-q", "-m", "fixture")
            _git(root, "update-index", flag, "source.c")
            assert _git_text(
                root, "status", "--porcelain=v1", "--untracked-files=all"
            ) == ""
            try:
                contest_demo.clean_source_identity(root)
            except contest_demo.ContestDemoError as error:
                assert "hidden or nonstandard tracked flag" in str(error)
            else:
                raise AssertionError(f"{flag} source was accepted")


def test_measurement_source_samples_commit_blobs_and_index_flags() -> None:
    for hidden_flag in (None, "--assume-unchanged", "--skip-worktree"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _git(root, "init", "-q")
            _git(root, "config", "user.name", "Contest Test")
            _git(root, "config", "user.email", "contest@example.invalid")
            source = write(root / "source.c", "int value = 1;\n")
            _git(root, "add", "source.c")
            _git(root, "commit", "-q", "-m", "fixture")
            commit = _git_text(root, "rev-parse", "HEAD")
            if hidden_flag is not None:
                _git(root, "update-index", hidden_flag, "source.c")
                try:
                    with mock.patch.object(
                        contest_demo,
                        "_receipt_source_paths",
                        return_value=("source.c",),
                    ):
                        contest_demo._measurement_source_sample(root, commit)
                except contest_demo.ContestDemoError as error:
                    assert "hidden or nonstandard tracked flag" in str(error)
                else:
                    raise AssertionError(f"{hidden_flag} receipt source was accepted")
                continue
            with mock.patch.object(
                contest_demo, "_receipt_source_paths", return_value=("source.c",)
            ):
                snapshot = root / "snapshot"
                snapshot.mkdir()
                sample = contest_demo._measurement_source_sample(
                    root, commit, snapshot_root=snapshot
                )
                assert sample[0][0] == "source.c"
                assert sample[0][1] == source.stat().st_size
                assert (snapshot / "source.c").read_bytes() == source.read_bytes()
                source.write_text("int value = 2;\n", encoding="utf-8")
                assert (snapshot / "source.c").read_text("utf-8") == "int value = 1;\n"
                try:
                    contest_demo._measurement_source_sample(root, commit)
                except contest_demo.ContestDemoError as error:
                    assert "differs from commit blob" in str(error)
                else:
                    raise AssertionError("worktree bytes outside the commit were accepted")


def test_source_sampling_accepts_autocrlf_clone_with_lf_attributes() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        parent = Path(temporary)
        origin = parent / "origin"
        clone = parent / "clone"
        origin.mkdir()
        _git(origin, "init", "-q")
        _git(origin, "config", "user.name", "Contest Test")
        _git(origin, "config", "user.email", "contest@example.invalid")
        files = {
            ".gitattributes": "* text=auto eol=lf\n*.bin -text\n",
            "README.md": "# Demo\nLF source identity\n",
            "docs/design.md": "# Design\nCommitted bytes\n",
            "ci/demo.yml": "demo:\n  script: verify\n",
        }
        for relative, content in files.items():
            write(origin / relative, content)
        _git(origin, "add", ".")
        _git(origin, "commit", "-q", "-m", "fixture")
        subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=true",
                "clone",
                "-q",
                str(origin),
                str(clone),
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _git(clone, "config", "core.autocrlf", "true")
        commit = _git_text(clone, "rev-parse", "HEAD")
        assert contest_demo.clean_source_identity(clone) == commit
        paths = tuple(files)
        with mock.patch.object(
            contest_demo, "_receipt_source_paths", return_value=paths
        ):
            sample = contest_demo._measurement_source_sample(clone, commit)
        assert tuple(record[0] for record in sample) == paths
        for relative in paths:
            assert b"\r\n" not in (clone / relative).read_bytes()


def test_report_rejects_source_sample_and_receipt_drift() -> None:
    changed = (("source.c", 1, "c" * 64, "d" * 40),)
    with tempfile.TemporaryDirectory() as temporary:
        fixture = report_fixture(Path(temporary))
        with mock.patch.object(
            contest_demo, "clean_source_identity", return_value=COMMIT
        ), mock.patch.object(
            contest_demo,
            "_measurement_source_sample",
            side_effect=(SOURCE_SAMPLE, changed),
        ), mock.patch.object(
            contest_demo,
            "build_measurement_source_receipt",
            return_value=SOURCE_RECEIPT,
        ):
            try:
                contest_demo.build_report(*fixture)
            except contest_demo.ContestDemoError as error:
                assert "between committed source samples" in str(error)
            else:
                raise AssertionError("source drift between samples was accepted")
    with tempfile.TemporaryDirectory() as temporary:
        fixture = report_fixture(Path(temporary))
        forged = {**SOURCE_RECEIPT, "sources": []}
        with mock.patch.object(
            contest_demo, "clean_source_identity", return_value=COMMIT
        ), mock.patch.object(
            contest_demo, "_measurement_source_sample", return_value=SOURCE_SAMPLE
        ), mock.patch.object(
            contest_demo,
            "build_measurement_source_receipt",
            return_value=forged,
        ):
            try:
                contest_demo.build_report(*fixture)
            except contest_demo.ContestDemoError as error:
                assert "receipt differs from committed source sample" in str(error)
            else:
                raise AssertionError("receipt outside the committed sample was accepted")


def test_report_validates_an_immutable_committed_snapshot() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        fixture = report_fixture(Path(temporary))
        mutable_source = write(fixture[0] / "source.c", "committed\n")
        sample_calls = 0

        def sample(_root, _commit, *, snapshot_root=None):
            nonlocal sample_calls
            sample_calls += 1
            if snapshot_root is not None:
                write(snapshot_root / "source.c", "committed\n")
            return SOURCE_SAMPLE

        def validate_snapshot(snapshot_root, *, source_commit):
            assert source_commit == COMMIT
            mutable_source.write_text("transient-forgery\n", encoding="utf-8")
            assert (snapshot_root / "source.c").read_text("utf-8") == "committed\n"
            return SOURCE_RECEIPT

        with mock.patch.object(
            contest_demo, "clean_source_identity", return_value=COMMIT
        ), mock.patch.object(
            contest_demo, "_measurement_source_sample", side_effect=sample
        ), mock.patch.object(
            contest_demo,
            "build_measurement_source_receipt",
            side_effect=validate_snapshot,
        ):
            contest_demo.build_report(*fixture)
        assert sample_calls == 2
        assert mutable_source.read_text("utf-8") == "transient-forgery\n"


def test_repository_wiring_and_fstat_reauthorization() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    runner = (ROOT / "scripts" / "run-contest-demo.sh").read_text(encoding="utf-8")
    demo = (ROOT / "host_tools" / "contest_demo.py").read_text(encoding="utf-8")
    syscall = (ROOT / "os" / "syscall.c").read_text(encoding="utf-8")
    agentvfs = (ROOT / "user" / "src" / "agentvfs_ucore.c").read_text(encoding="utf-8")
    trusted_entry = (ROOT / "scripts" / "trusted-python-entry.py").read_text(encoding="utf-8")
    assert "contest-demo:" in makefile and "contest-demo-check:" in makefile
    assert runner.count("scripts/agent_test_runner.py") == 1
    assert runner.count("build_and_run ") == 2
    assert "build_and_run agenteval_ucore evaluation \"${run_id}\"" in runner
    assert "build_and_run labdemo_ucore lab" in runner
    assert 'chapter="agent_eval"' in runner
    assert 'CHAPTER="${chapter}"' in runner
    assert "AGENT_EVAL_CHALLENGE_HEX=${challenge}" in runner
    assert "--evaluation-log" in runner
    assert "agentfinal_ucore" not in runner and "agentbench_ucore" not in runner
    assert "host_tools/contest_demo.py identity --root ." in runner
    assert "host_tools/contest_demo.py render" in runner
    assert runner.count("scripts/trusted-python-entry.py") == 2
    assert '"host_tools/contest_demo.py"' in trusted_entry
    assert not any(token in runner for token in ("curl ", "wget ", "http://", "https://"))
    assert "build_measurement_source_receipt" in demo
    assert "validate_guest_log" in demo
    assert "FUNCTIONAL_PATTERNS" not in demo
    assert "guest_failure_classifier.py" in demo
    assert "vfs_cred_from_proc(p, &cred);" in syscall
    assert "vfs_inode_authorize(f->ip, &cred, VFS_OP_READ)" in syscall
    assert "status.nlink = f->ip->removed ? 0U : 1U;" in syscall
    assert "fstat(INHERITED_FD, &inherited_status) == -1" in agentvfs
    assert "agentvfs_ucore: fstat_reauthorize=1" in agentvfs


def main() -> int:
    test_report_uses_canonical_receipts_and_honest_coverage()
    test_task2_context_and_path_index_fail_closed()
    test_source_contract_and_task6_fail_closed()
    test_shared_guest_failure_classifier_is_stage_aware()
    test_source_identity_rejects_dirty_worktrees()
    test_source_identity_rejects_hidden_index_flags()
    test_measurement_source_samples_commit_blobs_and_index_flags()
    test_source_sampling_accepts_autocrlf_clone_with_lf_attributes()
    test_report_rejects_source_sample_and_receipt_drift()
    test_report_validates_an_immutable_committed_snapshot()
    test_repository_wiring_and_fstat_reauthorization()
    print("test_contest_demo: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
