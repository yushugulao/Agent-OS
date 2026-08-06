#!/usr/bin/env python3
"""Focused regressions for the source-bound, single-Guest contest showcase."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import contest_demo


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "0123456789abcdef"
NONCE = int(RUN_ID, 16)
COMMIT = "a" * 40
SOURCE_BYTES = b"bound source\n"
SOURCE_SAMPLE = (
    ("source.c", len(SOURCE_BYTES), hashlib.sha256(SOURCE_BYTES).hexdigest(), "b" * 40),
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


COUNTERS = contest_demo.MECHANISM_COUNTERS
STORAGE = contest_demo._STORAGE_COUNTERS


def _add(before: dict[str, int], **changes: int) -> dict[str, int]:
    return {name: before[name] + changes.get(name, 0) for name in COUNTERS}


def _counter_base(value: int) -> dict[str, int]:
    counters = {name: value + offset for offset, name in enumerate(COUNTERS)}
    counters.update(
        metadata_dirty=value,
        metadata_durable=value,
        metadata_requests=value + 2,
        metadata_coalesced=value + 1,
        metadata_commits=value,
    )
    return counters


def _mechanism_line(
    mode: str,
    scope: str,
    observer_pid: int,
    before_tick: int,
    after_tick: int,
    lifecycle_id: int,
    generation: int,
    before: dict[str, int],
    after: dict[str, int],
) -> str:
    pairs = " ".join(
        f"before_{name}={before[name]} after_{name}={after[name]}"
        for name in COUNTERS
    )
    return (
        f"agentos:demo schema=2 nonce={NONCE} kind=mechanism mode={mode} "
        f"scope={scope} observer_pid={observer_pid} before_tick={before_tick} "
        f"after_tick={after_tick} observer_lifecycle_id={lifecycle_id} "
        f"observer_lifecycle_generation={generation} counter_scope=global {pairs}"
        " before_metadata_pending=0 after_metadata_pending=0"
    )


def _fence_line(
    mode: str,
    sequence: int,
    point: str,
    tick_us: int,
    observer_tick: int,
    values: dict[str, int],
    observer_pid: int = 2,
    lifecycle_id: int = 77,
    generation: int = 3,
) -> str:
    storage = " ".join(f"{name}={values[name]}" for name in STORAGE)
    return (
        f"agentos:demo schema=2 nonce={NONCE} kind=fence mode={mode} "
        f"seq={sequence} point={point} tick_us={tick_us} attempts=3 "
        f"stable_rounds=2 observer_pid={observer_pid} "
        f"observer_tick={observer_tick} observer_lifecycle_id={lifecycle_id} "
        f"observer_lifecycle_generation={generation} counter_scope=global "
        f"{storage} metadata_dirty={values['metadata_dirty']} "
        f"metadata_durable={values['metadata_durable']} "
        f"metadata_requests={values['metadata_requests']} "
        f"metadata_coalesced={values['metadata_coalesced']} "
        f"metadata_commits={values['metadata_commits']} metadata_pending=0"
    )


def _lane_records(mode: str, position: int, sample_id: int) -> list[str]:
    outcome = contest_demo._expected_outcome_hash()
    wall = 100 + position * 1000
    observer = 1000 + position * 100 + sample_id * 10
    base_value = 10000 + position * 1000 + sample_id * 100
    start = _counter_base(base_value)
    seeded = _add(
        start,
        epoch_commits=3,
        epoch_buffers_staged=12,
        physical_writes=18,
        physical_reads=20,
        durable_flushes=3,
        deduplicated_stages=4,
        virtio_notifications=8,
        virtio_submitted_requests=16,
        virtio_write_batch_calls=2,
        virtio_batched_write_requests=6,
        virtio_indirect_write_batch_calls=2,
        virtio_read_batch_calls=2,
        virtio_batched_read_requests=6,
    )
    if mode == "compat":
        core = _add(
            seeded,
            epoch_commits=1,
            epoch_buffers_staged=4,
            physical_writes=8,
            physical_reads=10,
            durable_flushes=1,
            workload_syscalls=12,
            directory_block_probes=6,
            directory_entries_examined=18,
            virtio_notifications=6,
            virtio_submitted_requests=8,
            virtio_write_batch_calls=1,
            virtio_batched_write_requests=3,
            virtio_indirect_write_batch_calls=1,
            virtio_read_batch_calls=1,
            virtio_batched_read_requests=2,
            metadata_requests=4,
            metadata_coalesced=1,
            metadata_commits=2,
            overwrite_prereads_skipped=0,
        )
        core_duration, records, bytes_read = 400, 25, 2048
        discovery_role = "orchestrator"
    else:
        core = _add(
            seeded,
            epoch_commits=1,
            epoch_buffers_staged=3,
            physical_writes=4,
            physical_reads=2,
            durable_flushes=1,
            deduplicated_stages=2,
            workload_syscalls=7,
            directory_block_probes=1,
            directory_entries_examined=2,
            virtio_notifications=2,
            virtio_submitted_requests=8,
            virtio_write_batch_calls=1,
            virtio_batched_write_requests=4,
            virtio_indirect_write_batch_calls=1,
            virtio_read_batch_calls=1,
            virtio_batched_read_requests=4,
            metadata_requests=4,
            metadata_coalesced=3,
            metadata_commits=1,
            overwrite_prereads_skipped=4,
        )
        core_duration, records, bytes_read = 80, 2, 0
        discovery_role = "sentinel"
    workload_syscalls = core["workload_syscalls"] - seeded["workload_syscalls"]
    settled = _add(
        core,
        epoch_commits=1,
        epoch_buffers_staged=2,
        physical_writes=2,
        physical_reads=1,
        durable_flushes=1,
        deduplicated_stages=1,
        virtio_notifications=1,
        virtio_submitted_requests=2,
    )
    finished = _add(
        settled,
        epoch_commits=2,
        epoch_buffers_staged=8,
        physical_writes=12,
        physical_reads=8,
        durable_flushes=2,
        deduplicated_stages=3,
        virtio_notifications=4,
        virtio_submitted_requests=8,
        virtio_read_batch_calls=1,
        virtio_batched_read_requests=4,
    )
    event_ticks = (wall + 30, wall + 50, wall + 70, wall + 30 + core_duration)
    e2e_started = wall + 10
    e2e_finished = event_ticks[-1] + 110
    rows = [
        _fence_line(mode, 1, "E2E_START", wall, observer, start),
        _fence_line(mode, 2, "CORE_START", wall + 20, observer + 10, seeded),
        _fence_line(
            mode,
            3,
            "ACK_SETTLED",
            event_ticks[-1] + 10,
            observer + 30,
            settled,
        ),
        _fence_line(
            mode,
            4,
            "E2E_END",
            event_ticks[-1] + 100,
            observer + 40,
            finished,
        ),
        f"agentos:demo schema=2 nonce={NONCE} kind=event mode={mode} seq=1 tick_us={event_ticks[0]} role=orchestrator event=INCIDENT value0=0 value1=0",
        f"agentos:demo schema=2 nonce={NONCE} kind=event mode={mode} seq=2 tick_us={event_ticks[1]} role={discovery_role} event=DISCOVERED value0={records} value1={bytes_read}",
        f"agentos:demo schema=2 nonce={NONCE} kind=event mode={mode} seq=3 tick_us={event_ticks[2]} role=recovery event=RECOVERY_COMMITTED value0={workload_syscalls} value1=1",
        f"agentos:demo schema=2 nonce={NONCE} kind=event mode={mode} seq=4 tick_us={event_ticks[3]} role=orchestrator event=RECOVERED value0=1 value1=0",
        f"agentos:demo schema=2 nonce={NONCE} kind=metric mode={mode} actor_pid=2 core_duration_us={core_duration} end_to_end_duration_us={e2e_finished - e2e_started} end_to_end_started_us={e2e_started} end_to_end_finished_us={e2e_finished} workload_syscalls={workload_syscalls} records_examined={records} bytes_read={bytes_read} result_items=1 outcome_hash={outcome}",
        _mechanism_line(
            mode, "core", 2, observer + 10, observer + 20, 77, 3, seeded, core
        ),
        _mechanism_line(
            mode, "end_to_end", 2, observer, observer + 40, 77, 3, start, finished
        ),
    ]
    return rows


def showcase_log(sample_id: int = 1, order: str = "compat_then_native") -> str:
    outcome = contest_demo._expected_outcome_hash()
    modes = ("compat", "native") if order == "compat_then_native" else ("native", "compat")
    lines = [
        f"agentos:demo schema=2 nonce={NONCE} kind=run sample={sample_id} order={order}"
    ]
    for position, mode in enumerate(modes):
        lines.extend(_lane_records(mode, position, sample_id))

    workflow_before = _counter_base(50000)
    workflow_after = _add(
        workflow_before,
        epoch_commits=2,
        epoch_buffers_staged=6,
        physical_writes=4,
        physical_reads=6,
        durable_flushes=2,
        deduplicated_stages=5,
        cow_shared_pages=18,
        cow_copied_pages=3,
        cow_fault_promotions=3,
        exec_cache_hits=7,
        exec_cache_misses=1,
        exec_cache_shared_pages=9,
    )
    probe_before = _counter_base(70000)
    probe_after = _add(
        probe_before,
        epoch_commits=1,
        epoch_buffers_staged=2,
        physical_writes=2,
        physical_reads=3,
        durable_flushes=1,
        cow_shared_pages=6,
        cow_copied_pages=3,
        cow_fault_promotions=3,
        exec_cache_hits=2,
        exec_cache_misses=1,
        exec_cache_shared_pages=4,
    )
    lines.extend(
        [
            f"agentos:demo schema=2 nonce={NONCE} kind=oracle project=lab-gene-x workflow=nightly-regression run=RUN-042 stage=align reason=memory_limit final_status=recovered execution_order={order} corpus=24 outcome_hash={outcome} compat_hash={outcome} native_hash={outcome}",
            f"agentos:demo schema=2 nonce={NONCE} kind=trace seq=1 tick_us=5000 role=orchestrator event=INCIDENT value0=0 value1=0",
            f"agentos:demo schema=2 nonce={NONCE} kind=trace seq=2 tick_us=5100 role=sentinel event=DISCOVERED value0=1 value1=1",
            f"agentos:demo schema=2 nonce={NONCE} kind=trace seq=3 tick_us=5200 role=investigator event=HANDOFF value0=4 value1=1",
            f"agentos:demo schema=2 nonce={NONCE} kind=trace seq=4 tick_us=5300 role=recovery event=RECOVERY_COMMITTED value0=1 value1=1",
            f"agentos:demo schema=2 nonce={NONCE} kind=trace seq=5 tick_us=5500 role=orchestrator event=RECOVERED value0=1 value1=0",
            f"agentos:demo schema=2 nonce={NONCE} kind=runtime mode=native agents=3 duration_us=500 tool_calls=10 dispatches=20 wait_sleeps=3 wait_wakeups=3 records_examined=3 denied_actions=1 duplicate_actions=1 recovery_side_effects=1",
            _mechanism_line(
                "workflow", "end_to_end", 2, 9000, 9100, 77, 3,
                workflow_before, workflow_after,
            ),
            _fence_line(
                "runtime_probe", 1, "PROBE_START", 6000, 10000,
                probe_before, observer_pid=1, lifecycle_id=0, generation=0,
            ),
            _fence_line(
                "runtime_probe", 2, "PROBE_END", 6100, 10010,
                probe_after, observer_pid=1, lifecycle_id=0, generation=0,
            ),
            _mechanism_line(
                "runtime_probe", "end_to_end", 1, 10000, 10010, 0, 0,
                probe_before, probe_after,
            ),
            "labdemo_ucore: startup_barrier ready=3 released=3 chain_receipts=3",
            "labdemo_ucore: global_audit=1 records=19 agents=3 context=1 event=1 sched=1 prefetch=1",
            "labdemo_ucore: unified_timeline records=24 context=1 event=1 sched=1 prefetch=1",
            "labdemo_ucore: provenance_graph edges=9 message=1 prefetch=1",
            "labdemo_ucore: passed",
            "labdemo_ucore: parent passed",
        ]
    )
    return "\n".join(lines) + "\n"


def report_fixture(root: Path):
    logs = [
        write(
            root / f"sample-{sample:02d}-qemu.log",
            showcase_log(
                sample,
                "compat_then_native" if sample % 2 else "native_then_compat",
            ),
        )
        for sample in range(1, 9)
    ]
    artifacts = [
        write(root / "showcase-kernel", "kernel\n"),
    ]
    for sample in range(1, 9):
        artifacts.extend(
            [
                write(root / f"sample-{sample:02d}-fs.img", f"filesystem {sample}\n"),
                write(root / f"sample-{sample:02d}-labdemo.elf", f"guest elf {sample}\n"),
            ]
        )
    return root, logs, RUN_ID, COMMIT, 12.5, artifacts


def build_fixture(fixture):
    with mock.patch.object(
        contest_demo, "clean_source_identity", return_value=COMMIT
    ), mock.patch.object(
        contest_demo, "_measurement_source_sample", return_value=SOURCE_SAMPLE
    ) as sampler:
        report = contest_demo.build_report(*fixture)
    assert sampler.call_count == 2
    assert sampler.call_args_list[0].kwargs["snapshot_root"] != fixture[0]
    return report


def test_showcase_parser_and_product_outputs() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        report = build_fixture(report_fixture(root))
        comparison = report["comparison"]
        assert report["schema_version"] == 6
        assert report["run"]["qemu_boots"] == 8
        assert report["build_manifest"]["source_commit"] == COMMIT
        assert report["build_manifest"]["kernel_artifact"] == "showcase-kernel"
        assert len(report["build_manifest"]["samples"]) == 8
        assert len(report["artifacts"]) == 25
        assert comparison["design"] == "same_kernel_same_guest_same_corpus"
        assert comparison["lanes"]["compat"]["core_duration_us"] == 400
        assert comparison["lanes"]["native"]["core_duration_us"] == 80
        assert comparison["ratios"]["compat_over_native_core_duration"] == 5.0
        assert comparison["ratios"]["compat_over_native_workload_syscalls"] == 1.714
        assert comparison["order_balance"] == {
            "compat_then_native": 4,
            "native_then_compat": 4,
        }
        core_effect = comparison["paired_effects"]["core_duration_us"]
        assert core_effect["native_minus_compat"] == {
            "estimate": -320,
            "low": -320,
            "high": -320,
        }
        assert core_effect["native_change_pct"] == {
            "estimate": -80,
            "low": -80,
            "high": -80,
        }
        assert core_effect["direction"] == "lower"
        native_core = report["mechanisms"]["native"]["core"]
        assert native_core["directory_block_probes"] == 1
        assert native_core["virtio_notifications"] == 2
        assert native_core["virtio_submitted_requests"] == 8
        assert native_core["virtio_read_batch_calls"] == 1
        assert native_core["virtio_batched_read_requests"] == 4
        assert native_core["virtio_write_batch_calls"] == 1
        assert native_core["virtio_indirect_write_batch_calls"] == 1
        assert native_core["physical_reads"] == 2
        assert native_core["overwrite_prereads_skipped"] == 4
        assert native_core["metadata_coalescing_rate_pct"] == 75
        assert native_core["raw_cycle_ordering"] == {
            "ordered_samples": 8,
            "minimum_gap": 10,
            "median_gap": 10,
            "unit": "raw_cycle_order_token",
        }
        assert report["mechanism_effects"]["core"][
            "directory_block_probes"
        ]["native_minus_compat"]["estimate"] == -5
        assert report["measurement_protocol"]["qemu_jobs"] == 1
        assert report["measurement_protocol"]["counter_scopes"] == {
            "default": "global",
            "workload_syscalls": "observer_process",
        }
        assert report["mechanism_effects"]["core"]["workload_syscalls"][
            "counter_scope"
        ] == "observer_process"
        assert report["measurement_protocol"]["host_concurrency"] == (
            "one_isolated_qemu_at_a_time"
        )
        assert report["outcome"]["equal"] is True
        assert [row["offset_us"] for row in report["timeline"]] == [0, 100, 200, 300, 500]

        output = root / "output"
        output.mkdir()
        for obsolete in contest_demo.REMOVED_PUBLISHED_FILES:
            (output / obsolete).write_text("stale\n", encoding="utf-8")
        contest_demo.publish(report, output)
        assert json.loads((output / "summary.json").read_text("utf-8")) == report
        assert all(not (output / name).exists() for name in contest_demo.REMOVED_PUBLISHED_FILES)
        page = (output / "index.html").read_text("utf-8")
        assert "8 boot 中位数" in page
        assert "400 us" in page and "80 us" in page and "5.00x" in page
        assert "6 / 3" in page and "2 / 1" in page
        assert "Native - Compat 均值 [95% CI]" in page
        assert "Metadata 合并率 p50" in page
        assert "VirtIO 读批量 p50" in page
        assert "VirtIO 写批量 p50" in page and "间接描述符批次" in page
        assert "observer_process" in page
        assert "8 boot 原始配对" in page
        assert "-320 us [-320 us，-320 us]" in page
        assert "多 Agent 恢复时间线" in page and "结果一致性" in page
        assert "passed" not in page and "部分就绪" not in page and "通过" not in page
        assert "http://" not in page and "https://" not in page
        markdown = (output / "report.md").read_text("utf-8")
        assert "Native - Compat 均值 [95% CI]" in markdown
        assert "物理读 / 写 / flush" in markdown
        assert "写 batch / request / indirect" in markdown
        assert "observer_process" in markdown
        assert "8 boot 原始配对" in markdown
        assert "-320 us [-320 us，-320 us]" in markdown
        assert "passed" not in markdown and "通过" not in markdown
        embedded = re.search(
            r'<script type="application/json" id="agentos-live-data">(.*?)</script>',
            page,
        )
        assert embedded is not None
        assert json.loads(embedded.group(1)) == report


def _increment_first(pattern: str, text: str) -> str:
    return re.sub(
        pattern,
        lambda match: match.group(1) + str(int(match.group(2)) + 1),
        text,
        count=1,
    )


def _zero_first_counter_delta(text: str, name: str) -> str:
    return re.sub(
        rf"(kind=mechanism mode=compat scope=core[^\n]*before_{name}=)([0-9]+)"
        rf"( after_{name}=)([0-9]+)",
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(3)}{match.group(2)}"
        ),
        text,
        count=1,
    )


def _stop_first_mechanism_clock(text: str) -> str:
    return re.sub(
        r"(kind=mechanism mode=compat scope=core[^\n]*before_tick=)([0-9]+)"
        r"( after_tick=)([0-9]+)",
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(3)}{match.group(2)}"
        ),
        text,
        count=1,
    )


def _metadata_coalescing_overflow(text: str) -> str:
    pattern = (
        r"(kind=mechanism mode=compat scope=core[^\n]*before_metadata_requests=)"
        r"([0-9]+)( after_metadata_requests=)([0-9]+)"
        r"( before_metadata_coalesced=)([0-9]+)"
        r"( after_metadata_coalesced=)([0-9]+)"
    )

    def replace(match: re.Match[str]) -> str:
        request_delta = int(match.group(4)) - int(match.group(2))
        invalid_after = int(match.group(6)) + request_delta + 1
        return "".join(
            (
                match.group(1), match.group(2), match.group(3), match.group(4),
                match.group(5), match.group(6), match.group(7), str(invalid_after),
            )
        )

    return re.sub(pattern, replace, text, count=1)


def test_schema2_records_fail_closed() -> None:
    mutations = {
        "wrong nonce": lambda text: text.replace(
            f"nonce={NONCE}", f"nonce={NONCE + 1}", 1
        ),
        "missing event": lambda text: "\n".join(
            line
            for line in text.splitlines()
            if "mode=native seq=2" not in line
        )
        + "\n",
        "metric mismatch": lambda text: text.replace(
            "core_duration_us=400", "core_duration_us=401", 1
        ),
        "workload syscall snapshot mismatch": lambda text: text.replace(
            "workload_syscalls=12 records_examined=25",
            "workload_syscalls=13 records_examined=25",
            1,
        ),
        "actor mismatch": lambda text: text.replace(
            "mode=native actor_pid=2", "mode=native actor_pid=3", 1
        ),
        "oracle mismatch": lambda text: text.replace(
            f"native_hash={contest_demo._expected_outcome_hash()}", "native_hash=1", 1
        ),
        "unknown record": lambda text: text
        + f"agentos:demo schema=2 nonce={NONCE} kind=claim passed=1\n",
        "partial mechanism set": lambda text: "\n".join(
            line
            for line in text.splitlines()
            if "kind=mechanism mode=workflow" not in line
        )
        + "\n",
        "nonmonotonic trace": lambda text: text.replace(
            "seq=3 tick_us=5200 role=investigator",
            "seq=3 tick_us=5050 role=investigator",
            1,
        ),
        "unblocked wait path": lambda text: text.replace(
            "wait_sleeps=3 wait_wakeups=3",
            "wait_sleeps=2 wait_wakeups=3",
            1,
        ),
        "pending metadata": lambda text: text.replace(
            "metadata_pending=0", "metadata_pending=1", 1
        ),
        "observer lifecycle drift": lambda text: text.replace(
            "observer_lifecycle_generation=3 counter_scope=global",
            "observer_lifecycle_generation=4 counter_scope=global",
            1,
        ),
        "bootstrap claims workflow lifecycle": lambda text: re.sub(
            r"(kind=fence mode=runtime_probe[^\n]*observer_lifecycle_id=)0",
            r"\g<1>9",
            text,
            count=1,
        ),
        "raw counter mismatch": lambda text: _increment_first(
            r"(kind=mechanism mode=compat scope=end_to_end[^\n]*after_physical_writes=)([0-9]+)",
            text,
        ),
        "stopped raw cycle clock": _stop_first_mechanism_clock,
        "read batch call without requests": lambda text: _zero_first_counter_delta(
            text, "virtio_batched_read_requests"
        ),
        "metadata coalescing exceeds requests": _metadata_coalescing_overflow,
        "zero exec reuse": lambda text: re.sub(
            r"(kind=mechanism mode=runtime_probe scope=end_to_end[^\n]*before_exec_cache_hits=)([0-9]+) after_exec_cache_hits=([0-9]+)",
            lambda match: f"{match.group(1)}{match.group(2)} after_exec_cache_hits={match.group(2)}",
            text,
            count=1,
        ),
    }
    for label, mutate in mutations.items():
        with tempfile.TemporaryDirectory() as temporary:
            log = write(Path(temporary) / "showcase.log", mutate(showcase_log()))
            try:
                contest_demo.verify_showcase(log, RUN_ID)
            except contest_demo.ContestDemoError:
                continue
            raise AssertionError(f"{label} was accepted")


def test_mechanism_snapshot_deltas_are_exposed_as_numbers() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        log = write(Path(temporary) / "showcase.log", showcase_log())
        report = contest_demo.verify_showcase(log, RUN_ID)
    assert report["mechanisms"]["compat"]["core"]["buffers_per_epoch"] == 4.0
    assert report["mechanisms"]["native"]["core"]["physical_writes"] == 4
    assert report["mechanisms"]["workflow"]["end_to_end"]["cow_shared_pages"] == 18
    assert report["mechanisms"]["workflow"]["end_to_end"]["exec_cache_hits"] == 7
    assert report["mechanisms"]["compat"]["core"]["raw_pair"]["before"]


def test_legacy_workflow_receipts_fail_closed() -> None:
    for marker in ("startup_barrier", "global_audit=1", "unified_timeline", "provenance_graph"):
        with tempfile.TemporaryDirectory() as temporary:
            content = "\n".join(
                line for line in showcase_log().splitlines() if marker not in line
            ) + "\n"
            log = write(Path(temporary) / "showcase.log", content)
            try:
                contest_demo.verify_showcase(log, RUN_ID)
            except contest_demo.ContestDemoError:
                continue
            raise AssertionError(f"missing {marker} was accepted")


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


def test_report_rejects_source_drift() -> None:
    changed = (("source.c", 1, "c" * 64, "d" * 40),)
    with tempfile.TemporaryDirectory() as temporary:
        fixture = report_fixture(Path(temporary))
        with mock.patch.object(
            contest_demo, "clean_source_identity", return_value=COMMIT
        ), mock.patch.object(
            contest_demo,
            "_measurement_source_sample",
            side_effect=(SOURCE_SAMPLE, changed),
        ):
            try:
                contest_demo.build_report(*fixture)
            except contest_demo.ContestDemoError as error:
                assert "changed" in str(error)
            else:
                raise AssertionError("source drift was accepted")


def test_report_rejects_artifact_basename_collisions() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        fixture = list(report_fixture(root))
        fixture[5] = fixture[5] + [
            write(root / "other" / "showcase-kernel", "different kernel\n")
        ]
        with mock.patch.object(
            contest_demo, "clean_source_identity", return_value=COMMIT
        ), mock.patch.object(
            contest_demo, "_measurement_source_sample", return_value=SOURCE_SAMPLE
        ):
            try:
                contest_demo.build_report(*fixture)
            except contest_demo.ContestDemoError as error:
                assert "basenames" in str(error)
            else:
                raise AssertionError("artifact basename collision was accepted")


def test_repository_wiring_is_balanced_and_nonce_bound() -> None:
    runner = (ROOT / "scripts" / "run-contest-demo.sh").read_text(encoding="utf-8")
    demo = (ROOT / "host_tools" / "contest_demo.py").read_text(encoding="utf-8")
    manifest = (ROOT / "user" / "include" / "exec_policy_manifest.h").read_text(
        encoding="utf-8"
    )
    guest = (ROOT / "user" / "src" / "labdemo_ucore.c").read_text(
        encoding="utf-8"
    )
    trusted = (ROOT / "scripts" / "trusted-python-entry.py").read_text(encoding="utf-8")
    assert runner.count("scripts/agent_test_runner.py") == 1
    assert "agenteval_ucore" not in runner and "--evaluation-log" not in runner
    assert "LABDEMO_RUN_NONCE=0x${run_id}ULL" in runner
    assert "LABDEMO_SAMPLE_ID=${sample}" in runner
    assert "LABDEMO_NATIVE_FIRST=${native_first}" in runner
    assert "sample-${sample_tag}-qemu.log" in runner
    assert 'CH_TESTS="labdemo_ucore labdemo_execprobe_ucore"' in runner
    assert "CONTEST_DEMO_QEMU_JOBS:-1" in runner
    assert "QEMU_JOBS != 1" in runner
    assert "CAMPAIGN_SAMPLES < 8" in runner
    assert "CAMPAIGN_SAMPLES > 64" in runner
    assert runner.count('CH_TESTS="labdemo_ucore labdemo_execprobe_ucore"') == 2
    assert runner.count("INIT_PROC=labdemo_ucore") == 2
    assert "--lab-log" in runner
    assert runner.count("scripts/trusted-python-entry.py") == 2
    assert '"host_tools/contest_demo.py"' in trusted
    assert not any(token in runner for token in ("curl ", "wget ", "http://", "https://"))
    assert "same_kernel_same_guest_same_corpus" in demo
    assert "incident_to_verified_durable_outcome" in demo
    assert "control_ops" not in demo and "control_ops" not in guest
    assert '"programs": 70' not in demo
    assert "agentos-live-data" in demo
    for source in (
        "os/performance_stats.c", "os/performance_stats.h", "os/fs.c",
        "os/virtio_disk.c", "os/agent_metadata_store.c",
    ):
        assert f'"{source}"' in demo
    assert 'X("labdemo_execprobe_ucore", "ldexecprobe"' in manifest
    assert "EXEC_MANIFEST_F_SEALED, 0, 0, EXEC_MANIFEST_VFS_PROFILE_NONE" in manifest
    assert 'exec("ldexecprobe", argv)' in guest
    assert len("ldexecprobe") <= 14


def main() -> int:
    test_showcase_parser_and_product_outputs()
    test_schema2_records_fail_closed()
    test_mechanism_snapshot_deltas_are_exposed_as_numbers()
    test_legacy_workflow_receipts_fail_closed()
    test_shared_guest_failure_classifier_is_stage_aware()
    test_source_identity_rejects_dirty_worktrees()
    test_report_rejects_source_drift()
    test_report_rejects_artifact_basename_collisions()
    test_repository_wiring_is_balanced_and_nonce_bound()
    print("test_contest_demo: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
