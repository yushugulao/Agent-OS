#!/usr/bin/env python3
"""Focused regression tests for the offline contest demo."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import contest_demo
from measured_experiments import MeasurementError


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "1234567890abcdef"
COMMIT = "a" * 40


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def functional_log() -> str:
    return "\n".join(
        (
            "agentfinal_ucore: context size=32768 capacity=128",
            "agentfinal_ucore: context_commit_lane=1 sequence=1..3 hash=1",
            "agentfinal_ucore: context_rollback_branch=1 sequence_reuse=0 provenance_bound=1",
            "agentfinal_ucore: context_active_path=1 archive_retained=1 direct_query=1 fifo_suffix=1",
            "agentfinal_ucore: batch first_seq=1 last_seq=64",
            "agentfinal_ucore: context_rollback_negative nonexistent=1 evicted=1",
            "agentfinal_ucore: file_query hits=2 scanned=11 used_index=1",
            "agentfinal_ucore: prefetch_hints=1 count=2 first_stage=analyze",
            "agentfinal_ucore: generic_action_abi=1",
            "agentfinal_ucore: llm_template_relay=1",
            "agentfinal_ucore: legacy_name_protocol=1",
            "agentfinal_ucore: event_wait=1 payload=self wake",
            "agentfinal_ucore: runtime_trace=1 records=17 context=1 sched=1 wait=1",
            "agentfinal_ucore: timeline_wait=1 timeout=-7 source_gate=1 event_gate=1 wake=1 query=1 read=1 sleeps=2 wakeups=2",
            "agentfinal_ucore: parent passed",
        )
    ) + "\n"


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


def benchmark_receipt() -> dict[str, object]:
    common = {
        "experiment": "file_metadata",
        "load": 100,
        "trial": 1,
        "operations": 8,
        "duration_unit": "us",
        "measurement_kind": "guest-syscall",
        "commit": COMMIT,
        "run_id": RUN_ID,
    }
    rows = []
    for path, records, duration, rebuild in (
        ("traversal", 800, 8000, 0),
        ("cold_index", 80, 2400, 100),
        ("warm_index", 80, 800, 0),
    ):
        rows.append(
            {
                **common,
                "path": path,
                "primary_metric": "records_touched",
                "primary_value": records,
                "duration_value": duration,
                "rebuild_records": rebuild,
            }
        )
    return {"kind": "agentos-measured-experiments", "status": "measured", "rows": rows}


def report_fixture(root: Path, *, missing_lab_marker: str | None = None):
    functional = write(root / "functional-qemu.log", functional_log())
    benchmark = write(
        root / "benchmark-qemu.log",
        "agentbench_ucore: file_query_benchmark schema=2 status=measured\n"
        "agentbench_ucore: parent passed\n",
    )
    lab = write(root / "lab-qemu.log", lab_log(omit=missing_lab_marker))
    artifacts = [
        write(root / name, f"{name}\n")
        for name in ("functional-kernel", "benchmark-kernel", "lab-kernel")
    ]
    return root, functional, benchmark, lab, RUN_ID, COMMIT, 83.25, artifacts


def test_report_is_derived_from_verified_logs() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with mock.patch.object(
            contest_demo, "clean_source_identity", return_value=COMMIT
        ), mock.patch.object(
            contest_demo, "extract_file_query_measurements", return_value=benchmark_receipt()
        ) as extractor:
            report = contest_demo.build_report(*report_fixture(root))
        extractor.assert_called_once()
        assert report["status"] == "passed"
        assert report["formal_claim"] is False
        assert tuple(report["tasks"]) == tuple(f"task{number}" for number in range(1, 7))
        assert report["qemu_boots"] == 3
        assert report["performance"]["comparison"]["speedup"] == 10.0
        output = root / "output"
        contest_demo.publish(report, output)
        published = json.loads((output / "summary.json").read_text("utf-8"))
        assert published == report
        page = (output / "index.html").read_text("utf-8")
        assert "6 / 6" in page
        assert "真实 RISC-V QEMU" in page
        assert "不是仅凭通过字符串" in page
        assert "http://" not in page and "https://" not in page
        assert "不替代正式多启动统计" in page


def test_missing_mechanism_marker_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        fixture = list(report_fixture(root))
        fixture[1].write_text(
            functional_log().replace("agentfinal_ucore: generic_action_abi=1\n", ""),
            encoding="utf-8",
        )
        with mock.patch.object(contest_demo, "clean_source_identity", return_value=COMMIT), mock.patch.object(
            contest_demo, "extract_file_query_measurements", return_value=benchmark_receipt()
        ):
            try:
                contest_demo.build_report(*fixture)
            except contest_demo.ContestDemoError as error:
                assert "task2" in str(error)
            else:
                raise AssertionError("missing Task 2 mechanism marker was accepted")


def test_task6_and_benchmark_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        fixture = report_fixture(Path(temporary), missing_lab_marker="provenance")
        with mock.patch.object(contest_demo, "clean_source_identity", return_value=COMMIT), mock.patch.object(
            contest_demo, "extract_file_query_measurements", return_value=benchmark_receipt()
        ):
            try:
                contest_demo.build_report(*fixture)
            except contest_demo.ContestDemoError as error:
                assert "provenance" in str(error)
            else:
                raise AssertionError("missing Task 6 marker was accepted")
    with tempfile.TemporaryDirectory() as temporary:
        fixture = report_fixture(Path(temporary))
        with mock.patch.object(contest_demo, "clean_source_identity", return_value=COMMIT), mock.patch.object(
            contest_demo,
            "extract_file_query_measurements",
            side_effect=MeasurementError("forged measurement"),
        ):
            try:
                contest_demo.build_report(*fixture)
            except contest_demo.ContestDemoError as error:
                assert "forged measurement" in str(error)
            else:
                raise AssertionError("benchmark parser failure was masked")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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


def test_repository_wiring_and_fstat_reauthorization() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    runner = (ROOT / "scripts" / "run-contest-demo.sh").read_text(encoding="utf-8")
    syscall = (ROOT / "os" / "syscall.c").read_text(encoding="utf-8")
    agentvfs = (ROOT / "user" / "src" / "agentvfs_ucore.c").read_text(encoding="utf-8")
    trusted_entry = (ROOT / "scripts" / "trusted-python-entry.py").read_text(encoding="utf-8")
    assert "contest-demo:" in makefile and "contest-demo-check:" in makefile
    assert runner.count("scripts/agent_test_runner.py") == 1
    assert runner.count("build_and_run ") == 3
    for case in ("agentfinal_ucore", "agentbench_ucore", "labdemo_ucore"):
        assert case in runner
    assert "host_tools/contest_demo.py identity --root ." in runner
    assert "host_tools/contest_demo.py render" in runner
    assert runner.count("scripts/trusted-python-entry.py") == 2
    assert '"host_tools/contest_demo.py"' in trusted_entry
    assert not any(token in runner for token in ("curl ", "wget ", "http://", "https://"))
    assert "vfs_cred_from_proc(p, &cred);" in syscall
    assert "vfs_inode_authorize(f->ip, &cred, VFS_OP_READ)" in syscall
    assert "status.nlink = f->ip->removed ? 0U : 1U;" in syscall
    assert "fstat(INHERITED_FD, &inherited_status) == -1" in agentvfs
    assert "agentvfs_ucore: fstat_reauthorize=1" in agentvfs


def main() -> int:
    test_report_is_derived_from_verified_logs()
    test_missing_mechanism_marker_fails_closed()
    test_task6_and_benchmark_fail_closed()
    test_source_identity_rejects_dirty_worktrees()
    test_repository_wiring_and_fstat_reauthorization()
    print("test_contest_demo: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
