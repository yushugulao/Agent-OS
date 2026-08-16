#!/usr/bin/env python3
"""单 Guest 竞赛演示的专项回归测试。"""

from __future__ import annotations

import csv
import json
import re
import tempfile
from pathlib import Path

import contest_demo


NONCE = int("0123456789abcdef", 16)
DISCOVERY_QUERIES = 4


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


COUNTERS = contest_demo.MECHANISM_COUNTERS
STORAGE = contest_demo._STORAGE_COUNTERS


def _add(before: dict[str, int], **changes: int) -> dict[str, int]:
    return {name: before[name] + changes.get(name, 0) for name in COUNTERS}


def _counter_base(value: int) -> dict[str, int]:
    return {name: value + offset for offset, name in enumerate(COUNTERS)}


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
        f"{storage}"
    )


def _lane_records(
    mode: str, position: int, sample_id: int, discovery_queries: int
) -> list[str]:
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
            overwrite_prereads_skipped=0,
        )
        core_duration, records, bytes_read = (
            400,
            contest_demo.CORPUS_SIZE * discovery_queries + 1,
            2048,
        )
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
            overwrite_prereads_skipped=4,
        )
        if discovery_queries == 1:
            core_duration = 390
            records = contest_demo.CORPUS_SIZE + 1
            bytes_read = 2048
        else:
            core_duration, records, bytes_read = 80, discovery_queries + 1, 0
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
    if mode == "native":
        selected_path = "traversal" if discovery_queries == 1 else "indexed"
        query_state = "cold" if discovery_queries == 1 else "ready"
        build_count = 0 if discovery_queries == 1 else 1
        batch_calls = 0 if discovery_queries == 1 else 6
        registered_items = 0 if discovery_queries == 1 else 96
        reuse_hits = 0 if discovery_queries == 1 else discovery_queries
        cold_build_us = 0 if discovery_queries == 1 else 40
        warm_query_us = 0 if discovery_queries == 1 else 8
        rows.append(
            f"agentos:demo schema=2 nonce={NONCE} kind=catalog mode=native "
            f"expected_discovery_queries={discovery_queries} "
            f"selected_path={selected_path} query_state={query_state} "
            f"discovery_query_count={discovery_queries} "
            f"validation_query_count=1 total_query_count={discovery_queries + 1} "
            f"build_count={build_count} batch_calls={batch_calls} "
            f"registered_items={registered_items} reuse_hits={reuse_hits} "
            f"cold_build_us={cold_build_us} aggregate_query_us=20 "
            f"warm_query_us={warm_query_us}"
        )
    return rows


def showcase_log(
    sample_id: int = 1,
    order: str = "compat_then_native",
    discovery_queries: int = DISCOVERY_QUERIES,
) -> str:
    outcome = contest_demo._expected_outcome_hash()
    modes = ("compat", "native") if order == "compat_then_native" else ("native", "compat")
    lines = [
        f"agentos:demo schema=2 nonce={NONCE} kind=run sample={sample_id} order={order}"
    ]
    for position, mode in enumerate(modes):
        lines.extend(_lane_records(mode, position, sample_id, discovery_queries))

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
            f"agentos:demo schema=2 nonce={NONCE} kind=oracle project=lab-gene-x workflow=nightly-regression run=RUN-042 stage=align reason=memory_limit final_status=recovered execution_order={order} corpus={contest_demo.CORPUS_SIZE} outcome_hash={outcome} compat_hash={outcome} native_hash={outcome}",
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
                probe_before, observer_pid=1, lifecycle_id=1, generation=1,
            ),
            _fence_line(
                "runtime_probe", 2, "PROBE_END", 6100, 10010,
                probe_after, observer_pid=1, lifecycle_id=1, generation=1,
            ),
            _mechanism_line(
                "runtime_probe", "end_to_end", 1, 10000, 10010, 1, 1,
                probe_before, probe_after,
            ),
            "labdemo_ucore: startup_barrier ready=3 released=3 chain_receipts=3",
            "labdemo_ucore: global_audit=1 records=19 agents=3 context=1 event=1 sched=1 message=1",
            "labdemo_ucore: unified_timeline records=24 context=1 event=1 sched=1 message=1",
            "labdemo_ucore: provenance_graph edges=9 message=1 context=1",
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
    return logs, 12.5


def build_fixture(fixture):
    return contest_demo.build_report(*fixture)


def test_showcase_parser_and_product_outputs() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        report = build_fixture(report_fixture(root))
        comparison = report["comparison"]
        assert report["schema_version"] == 2
        assert report["campaign"]["qemu_boots"] == 8
        assert (
            comparison["workload"]
            == "same_kernel_same_guest_same_repeated_file_query"
        )
        assert comparison["adaptive_selection"] == "indexed"
        assert comparison["paths"] == {
            "traversal": "directory_traversal",
            "indexed": "indexed_reused_path",
        }
        assert comparison["medians"]["traversal"]["core_duration_us"] == 400
        assert comparison["medians"]["indexed"]["core_duration_us"] == 80
        assert comparison["medians"]["traversal"]["records_examined"] == 385
        assert comparison["medians"]["indexed"]["records_examined"] == 5
        assert comparison["ratios"]["traversal_over_indexed_core_duration"] == 5.0
        assert comparison["ratios"]["traversal_over_indexed_records_examined"] == 77.0
        assert comparison["order_balance"] == {
            "traversal_then_indexed": 4,
            "indexed_then_traversal": 4,
        }
        assert comparison["paired_regression"] == {
            "sample_count": 8,
            "indexed_faster_samples": 8,
            "indexed_faster_majority": True,
            "median_indexed_minus_traversal_core_us": -320,
            "indexed_end_to_end_faster_samples": 8,
            "indexed_end_to_end_faster_majority": True,
            "median_indexed_minus_traversal_end_to_end_us": -320,
            "indexed_reduced_records_in_all_samples": True,
        }
        assert report["catalog_reuse"] == {
            "expected_discovery_queries": 4,
            "selected_path": "indexed",
            "query_state": "ready",
            "discovery_query_count": 4,
            "validation_query_count": 1,
            "total_query_count": 5,
            "build_count": 1,
            "batch_calls": 6,
            "registered_items": 96,
            "reuse_hits": 4,
            "medians": {
                "cold_build_us": 40,
                "aggregate_query_us": 20,
                "warm_query_us": 8,
            },
        }
        assert report["outcome"]["equal"] is True

        output = root / "output"
        contest_demo.publish(report, output)
        assert json.loads((output / "summary.json").read_text("utf-8")) == report
        assert sorted(path.name for path in output.iterdir()) == [
            "measurements.csv",
            "report.md",
            "summary.json",
        ]
        rows = list(
            csv.DictReader((output / "measurements.csv").read_text("utf-8").splitlines())
        )
        assert len(rows) == 8
        assert rows[0]["traversal_core_duration_us"] == "400"
        assert rows[0]["indexed_core_duration_us"] == "80"
        assert rows[0]["traversal_records_examined"] == "385"
        assert rows[0]["indexed_records_examined"] == "5"
        assert rows[0]["indexed_minus_traversal_core_us"] == "-320"
        assert rows[0]["indexed_minus_traversal_end_to_end_us"] == "-320"
        markdown = (output / "report.md").read_text("utf-8")
        assert "directory traversal" in markdown
        assert "indexed/reused path" in markdown
        assert "Indexed/reused core (us)" in markdown
        assert "traversal_then_indexed_reused" in markdown
        assert "400" in markdown and "80" in markdown
        assert "`8/8` paired boots" in markdown
        assert "-320 us" in markdown
        assert "Raw paired measurements" in markdown
        assert "original QEMU logs" in markdown


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
            "workload_syscalls=12 records_examined="
            f"{contest_demo.CORPUS_SIZE * DISCOVERY_QUERIES + 1}",
            "workload_syscalls=13 records_examined="
            f"{contest_demo.CORPUS_SIZE * DISCOVERY_QUERIES + 1}",
            1,
        ),
        "actor mismatch": lambda text: text.replace(
            "mode=native actor_pid=2", "mode=native actor_pid=3", 1
        ),
        "oracle mismatch": lambda text: text.replace(
            f"native_hash={contest_demo._expected_outcome_hash()}", "native_hash=1", 1
        ),
        "catalog reuse mismatch": lambda text: text.replace(
            f"reuse_hits={DISCOVERY_QUERIES} cold_build_us=40",
            "reuse_hits=0 cold_build_us=40",
            1,
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
        "nonmonotonic fence counter": lambda text: re.sub(
            r"(kind=fence mode=compat seq=4[^\n]* physical_reads=)([0-9]+)",
            r"\g<1>0",
            text,
            count=1,
        ),
        "observer lifecycle drift": lambda text: text.replace(
            "observer_lifecycle_generation=3 counter_scope=global",
            "observer_lifecycle_generation=4 counter_scope=global",
            1,
        ),
        "runtime probe lacks lifecycle": lambda text: re.sub(
            r"(kind=fence mode=runtime_probe[^\n]*observer_lifecycle_id=)1",
            r"\g<1>0",
            text,
            count=1,
        ),
        "runtime probe lifecycle drift": lambda text: re.sub(
            r"(kind=fence mode=runtime_probe[^\n]*observer_lifecycle_generation=)1",
            r"\g<1>2",
            text,
            count=1,
        ),
        "runtime probe uses orchestrator": lambda text: re.sub(
            r"(kind=(?:fence|mechanism) mode=runtime_probe[^\n]*observer_pid=)1",
            r"\g<1>2",
            text,
        ),
        "raw counter mismatch": lambda text: _increment_first(
            r"(kind=mechanism mode=compat scope=end_to_end[^\n]*after_physical_writes=)([0-9]+)",
            text,
        ),
        "stopped raw cycle clock": _stop_first_mechanism_clock,
        "read batch call without requests": lambda text: _zero_first_counter_delta(
            text, "virtio_batched_read_requests"
        ),
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
                contest_demo.verify_showcase(log, NONCE)
            except contest_demo.ContestDemoError:
                continue
            raise AssertionError(f"{label} was accepted")


def test_mechanism_snapshot_deltas_are_exposed_as_numbers() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        log = write(Path(temporary) / "showcase.log", showcase_log())
        report = contest_demo.verify_showcase(log, NONCE)
    probe = report["mechanisms"]["runtime_probe"]["end_to_end"]
    assert report["mechanisms"]["traversal"]["core"]["buffers_per_epoch"] == 4.0
    assert report["mechanisms"]["indexed"]["core"]["physical_writes"] == 4
    assert report["mechanisms"]["workflow"]["end_to_end"]["cow_shared_pages"] == 18
    assert report["mechanisms"]["workflow"]["end_to_end"]["exec_cache_hits"] == 7
    assert report["mechanisms"]["traversal"]["core"]["raw_pair"]["before"]
    assert probe["observer_lifecycle_id"] == 1
    assert probe["observer_lifecycle_generation"] == 1
    assert probe["observer_pid"] != report["lanes"]["traversal"]["actor_pid"]


def test_legacy_workflow_receipts_fail_closed() -> None:
    for marker in ("startup_barrier", "global_audit=1", "unified_timeline", "provenance_graph"):
        with tempfile.TemporaryDirectory() as temporary:
            content = "\n".join(
                line for line in showcase_log().splitlines() if marker not in line
            ) + "\n"
            log = write(Path(temporary) / "showcase.log", content)
            try:
                contest_demo.verify_showcase(log, NONCE)
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


def test_campaign_rejects_mixed_guest_nonces() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        logs, elapsed = report_fixture(root)
        replacement = NONCE + 1
        logs[1].write_text(
            logs[1].read_text("utf-8").replace(
                f"nonce={NONCE}", f"nonce={replacement}"
            ),
            encoding="utf-8",
        )
        try:
            contest_demo.build_report(logs, elapsed)
        except contest_demo.ContestDemoError:
            pass
        else:
            raise AssertionError("mixed Guest nonces were accepted")


def test_paired_performance_regression_is_rejected() -> None:
    samples = []
    for sample_id, order in (
        (1, "compat_then_native"),
        (2, "native_then_compat"),
        (3, "compat_then_native"),
        (4, "native_then_compat"),
    ):
        sample = contest_demo.verify_showcase(
            Path("unused"),
            NONCE,
            sample_id,
            order,
            guest_bytes=showcase_log(sample_id, order).encode("utf-8"),
        )
        sample["lanes"]["indexed"]["core_duration_us"] = 500
        samples.append(sample)
    try:
        contest_demo.campaign_aggregates(samples)
    except contest_demo.ContestDemoError as error:
        assert "majority" in str(error)
    else:
        raise AssertionError("indexed-path performance regression was accepted")


def test_single_use_campaign_selects_traversal_without_catalog() -> None:
    samples = []
    for sample_id, order in (
        (1, "compat_then_native"),
        (2, "native_then_compat"),
        (3, "compat_then_native"),
        (4, "native_then_compat"),
    ):
        sample = contest_demo.verify_showcase(
            Path("unused"),
            NONCE,
            sample_id,
            order,
            guest_bytes=showcase_log(sample_id, order, 1).encode("utf-8"),
        )
        samples.append(sample)
    report = contest_demo.campaign_aggregates(samples)
    assert report["comparison"]["adaptive_selection"] == "traversal"
    assert report["comparison"]["paths"]["indexed"] == (
        "adaptive_directory_traversal"
    )
    assert report["comparison"]["paired_regression"][
        "indexed_reduced_records_in_all_samples"
    ] is False
    assert report["catalog_reuse"]["build_count"] == 0
    assert report["catalog_reuse"]["batch_calls"] == 0
    assert report["catalog_reuse"]["registered_items"] == 0
    assert report["catalog_reuse"]["reuse_hits"] == 0
    report["campaign"] = {"qemu_boots": 4}
    markdown = contest_demo.render_markdown(report)
    assert "kept the Catalog cold" in markdown
    assert "Adaptive traversal core (us)" in markdown
    assert "traversal_then_adaptive_traversal" in markdown
    assert "indexed" not in markdown.lower()


def main() -> int:
    test_showcase_parser_and_product_outputs()
    test_schema2_records_fail_closed()
    test_mechanism_snapshot_deltas_are_exposed_as_numbers()
    test_legacy_workflow_receipts_fail_closed()
    test_shared_guest_failure_classifier_is_stage_aware()
    test_campaign_rejects_mixed_guest_nonces()
    test_paired_performance_regression_is_rejected()
    test_single_use_campaign_selects_traversal_without_catalog()
    print("test_contest_demo: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
