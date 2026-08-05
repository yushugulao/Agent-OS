#!/usr/bin/env python3
"""Tests for the same-kernel performance evidence contract."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from . import same_kernel_performance as performance
    from .test_contest_demo import build_fixture, report_fixture
except ImportError:
    import same_kernel_performance as performance
    from test_contest_demo import build_fixture, report_fixture


def _protocol(order: tuple[str, str], cache_state: str = "warm") -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": performance.PROTOCOL_KIND,
        "cache_state": cache_state,
        "lane_order": list(order),
        "setup_included": False,
        "quiescence_fence": True,
        "counter_isolation": "global_quiesced",
        "cow_exec_exercised_in_lanes": True,
    }


def _mechanism(seed: int) -> dict[str, object]:
    return {
        "counter_scope": "global",
        "epoch_commits": 2 + seed,
        "epoch_buffers_staged": 8 + seed,
        "physical_writes": 10 + seed,
        "physical_reads": 12 + seed,
        "durable_flushes": 2 + seed,
        "deduplicated_stages": 3 + seed,
        "cow_shared_pages": 4 + seed,
        "cow_copied_pages": 1 + seed,
        "cow_fault_promotions": 1 + seed,
        "exec_cache_hits": 7 + seed,
        "exec_cache_misses": 2 + seed,
        "exec_cache_shared_pages": 5 + seed,
        "exec_cache_evictions": seed,
        "workload_syscalls": 20 + seed,
        "directory_block_probes": 4 + seed,
        "directory_entries_examined": 8 + seed,
        "virtio_notifications": 4 + seed,
        "virtio_submitted_requests": 12 + seed,
        "virtio_write_batch_calls": 1,
        "virtio_batched_write_requests": 3 + seed,
        "virtio_indirect_write_batch_calls": 1,
        "virtio_read_batch_calls": 1,
        "virtio_batched_read_requests": 4 + seed,
        "overwrite_prereads_skipped": 2 + seed,
        "metadata_requests": 8 + seed,
        "metadata_coalesced": 4 + seed,
        "metadata_commits": 2 + seed,
    }


def _report(index: int = 0, protocol: dict[str, object] | None = None) -> dict[str, object]:
    outcome_hash = 0xA617
    report: dict[str, object] = {
        "schema_version": 4,
        "kind": performance.REPORT_KIND,
        "run": {
            "id": f"{index + 1:016x}",
            "commit": "1" * 40,
            "qemu_boots": 1,
            "wall_seconds": 2.5,
        },
        "comparison": {
            "design": "same_kernel_same_guest_same_corpus",
            "execution_actor_pid": 7,
            "timed_scope": "recovery_core_without_70_program_acceptance_chain",
            "corpus_records": 24,
            "lanes": {
                "compat": {
                    "duration_us": 1200 + index,
                    "workload_syscalls": 20,
                    "records_examined": 25,
                    "bytes_read": 2048,
                    "result_items": 1,
                    "outcome_hash": outcome_hash,
                },
                "native": {
                    "duration_us": 300 + index,
                    "workload_syscalls": 7,
                    "records_examined": 2,
                    "bytes_read": 80,
                    "result_items": 1,
                    "outcome_hash": outcome_hash,
                },
            },
        },
        "outcome": {
            "equal": True,
            "outcome_hash": outcome_hash,
            "compat_hash": outcome_hash,
            "native_hash": outcome_hash,
        },
        "mechanisms": {
            "compat": _mechanism(2),
            "native": _mechanism(0),
            "workflow": _mechanism(4),
        },
    }
    if protocol is not None:
        report["measurement_protocol"] = protocol
    return report


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


def _formal_campaign(root: Path) -> dict[str, object]:
    campaign = build_fixture(report_fixture(root))
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "AgentOS Test")
    _git(root, "config", "user.email", "agentos-test@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    for index, relative in enumerate(performance.contest_demo.DEMO_SOURCE_PATHS, 1):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"formal source {index}: {relative}\n".encode("utf-8"))
    _git(root, "add", "--", *performance.contest_demo.DEMO_SOURCE_PATHS)
    _git(root, "commit", "--quiet", "-m", "formal source fixture")
    commit = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    tree = _git(
        root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        commit,
        "--",
        *performance.contest_demo.DEMO_SOURCE_PATHS,
    )
    oids = {}
    for entry in tree.split(b"\0"):
        if entry:
            metadata, raw_path = entry.split(b"\t", 1)
            oids[raw_path.decode("utf-8")] = metadata.split()[2].decode("ascii")
    sources = []
    for relative in performance.contest_demo.DEMO_SOURCE_PATHS:
        oid = oids[relative]
        blob = (root / relative).read_bytes()
        sources.append(
            {
                "path": relative,
                "bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "git_oid": oid,
            }
        )
    campaign["run"]["commit"] = commit
    campaign["build_manifest"]["source_commit"] = commit
    campaign["source_receipt"] = {
        "schema_version": 1,
        "kind": "agentos-showcase-source-receipt",
        "sources": sources,
    }
    return campaign


class SameKernelPerformanceTests(unittest.TestCase):
    def test_balanced_schema6_campaign_expands_to_guest_boot_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = _formal_campaign(root)
            path = root / "summary.json"
            path.write_text(json.dumps(campaign), encoding="utf-8")
            normalized = performance.load_samples(path)

        self.assertEqual(len(normalized), 8)
        group = performance.build_dataset(normalized)["groups"][0]
        self.assertEqual(group["sample_count"], 8)
        self.assertTrue(group["formal_evidence"])
        self.assertTrue(group["headline_eligible"])
        self.assertEqual(
            group["claim_boundaries"]["recovery_core"]["evidence"],
            "controlled_repeated",
        )
        self.assertEqual(
            group["metrics"]["directory_block_probes"][
                "native_minus_compat_ci95"
            ]["estimate"],
            -5.0,
        )
        self.assertIn("runtime_probe", normalized[0]["mechanisms"])
        self.assertEqual(
            group["runtime_probe_mechanisms"]["cow_shared_pages"]["p50"], 6
        )
        self.assertEqual(
            group["metrics"]["virtio_write_batch_calls"]["compat"]["p50"], 3
        )
        self.assertEqual(
            group["metrics"]["virtio_indirect_write_batch_calls"]["native"]["p50"],
            3,
        )
        self.assertEqual(
            group["counter_scopes"],
            {"default": "global", "workload_syscalls": "observer_process"},
        )

    def test_schema6_missing_receipts_are_rejected(self) -> None:
        mutations = {
            "measurement_protocol": lambda report: report.pop(
                "measurement_protocol"
            ),
            "fences": lambda report: report["samples"][0].pop("fences"),
            "raw_pair": lambda report: report["samples"][0]["mechanisms"][
                "compat"
            ]["end_to_end"].pop("raw_pair"),
            "source_receipt": lambda report: report.pop("source_receipt"),
            "build_manifest": lambda report: report.pop("build_manifest"),
            "artifacts": lambda report: report.pop("artifacts"),
            "counter_scopes": lambda report: report["measurement_protocol"].pop(
                "counter_scopes"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                campaign = _formal_campaign(root)
                mutate(campaign)
                path = root / "summary.json"
                path.write_text(json.dumps(campaign), encoding="utf-8")
                with self.assertRaises(performance.SameKernelPerformanceError):
                    performance.load_samples(path)

    def test_schema6_raw_pair_and_artifact_tampering_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = _formal_campaign(root)
            campaign["samples"][0]["mechanisms"]["native"]["end_to_end"][
                "raw_pair"
            ]["after"]["physical_reads"] += 1
            path = root / "summary.json"
            path.write_text(json.dumps(campaign), encoding="utf-8")
            with self.assertRaisesRegex(
                performance.SameKernelPerformanceError, "raw Guest log replay"
            ):
                performance.load_samples(path)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = _formal_campaign(root)
            artifact = next(iter(campaign["artifacts"]))
            (root / artifact).write_bytes(b"tampered")
            path = root / "summary.json"
            path.write_text(json.dumps(campaign), encoding="utf-8")
            with self.assertRaisesRegex(
                performance.SameKernelPerformanceError, "receipt"
            ):
                performance.load_samples(path)

    def test_schema6_latency_must_match_raw_guest_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = _formal_campaign(root)
            campaign["samples"][0]["comparison"]["lanes"]["compat"][
                "core_duration_us"
            ] += 1
            path = root / "summary.json"
            path.write_text(json.dumps(campaign), encoding="utf-8")

            with self.assertRaisesRegex(
                performance.SameKernelPerformanceError, "raw Guest log replay"
            ):
                performance.load_samples(path)

    def test_schema6_replaced_guest_log_is_rejected_even_with_new_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = _formal_campaign(root)
            target = root / "sample-01-qemu.log"
            target.write_bytes((root / "sample-02-qemu.log").read_bytes())
            payload = target.read_bytes()
            campaign["artifacts"][target.name] = {
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            path = root / "summary.json"
            path.write_text(json.dumps(campaign), encoding="utf-8")

            with self.assertRaisesRegex(
                performance.SameKernelPerformanceError, "Guest log replay failed"
            ):
                performance.load_samples(path)

    def test_schema6_source_receipt_is_recomputed_from_commit_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = _formal_campaign(root)
            campaign["source_receipt"]["sources"][0]["sha256"] = "0" * 64
            path = root / "summary.json"
            path.write_text(json.dumps(campaign), encoding="utf-8")

            with self.assertRaisesRegex(
                performance.SameKernelPerformanceError, "commit blob"
            ):
                performance.load_samples(path)

    def test_schema6_dashboard_aggregates_are_rebuilt_from_guest_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = _formal_campaign(root)
            campaign["comparison"]["lanes"]["compat"]["core_duration_us"] = 999999
            path = root / "summary.json"
            path.write_text(json.dumps(campaign), encoding="utf-8")

            with self.assertRaisesRegex(
                performance.SameKernelPerformanceError,
                "comparison.*replayed Guest evidence",
            ):
                performance.load_samples(path)

    def test_schema6_source_paths_must_be_regular_git_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = _formal_campaign(root)
            source = campaign["source_receipt"]["sources"][0]
            _git(
                root,
                "update-index",
                "--cacheinfo",
                "120000",
                source["git_oid"],
                source["path"],
            )
            _git(root, "commit", "--quiet", "-m", "turn source into symlink")
            commit = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
            campaign["run"]["commit"] = commit
            campaign["build_manifest"]["source_commit"] = commit
            path = root / "summary.json"
            path.write_text(json.dumps(campaign), encoding="utf-8")

            with self.assertRaisesRegex(
                performance.SameKernelPerformanceError, "blob set"
            ):
                performance.load_samples(path)

    def test_schema6_artifact_change_during_replay_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = _formal_campaign(root)
            path = root / "summary.json"
            path.write_text(json.dumps(campaign), encoding="utf-8")
            real_verify = performance.contest_demo.verify_showcase
            changed = False

            def verify_and_change(*args, **kwargs):
                nonlocal changed
                result = real_verify(*args, **kwargs)
                if not changed:
                    (root / "showcase-kernel").write_bytes(b"changed kernel\n")
                    changed = True
                return result

            with mock.patch.object(
                performance.contest_demo,
                "verify_showcase",
                side_effect=verify_and_change,
            ), self.assertRaisesRegex(
                performance.SameKernelPerformanceError,
                "changed during formal evidence validation",
            ):
                performance.load_samples(path)

    def test_schema6_replay_uses_hashed_log_bytes_not_a_reopened_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = _formal_campaign(root)
            target = root / "sample-01-qemu.log"
            original = target.read_bytes()
            original_stat = target.stat()
            forged_text = original.decode("utf-8")
            for before, after in (
                ("point=ACK_SETTLED tick_us=540", "point=ACK_SETTLED tick_us=541"),
                ("point=E2E_END tick_us=630", "point=E2E_END tick_us=631"),
                ("seq=4 tick_us=530 role=orchestrator", "seq=4 tick_us=531 role=orchestrator"),
                ("core_duration_us=400", "core_duration_us=401"),
                ("end_to_end_duration_us=530", "end_to_end_duration_us=531"),
                ("end_to_end_finished_us=640", "end_to_end_finished_us=641"),
            ):
                self.assertIn(before, forged_text)
                forged_text = forged_text.replace(before, after, 1)
            forged_path = root / "forged-sample-01.log"
            forged_path.write_text(forged_text, encoding="utf-8")
            forged_sample = performance.contest_demo.verify_showcase(
                forged_path,
                campaign["run"]["id"],
                1,
                "compat_then_native",
            )
            self.assertEqual(
                forged_sample["comparison"]["lanes"]["compat"]["core_duration_us"],
                401,
            )
            campaign["samples"][0] = forged_sample
            path = root / "summary.json"
            path.write_text(json.dumps(campaign), encoding="utf-8")
            real_verify = performance.contest_demo.verify_showcase

            def swap_while_replaying(*args, **kwargs):
                target.write_bytes(forged_text.encode("utf-8"))
                try:
                    return real_verify(*args, **kwargs)
                finally:
                    target.write_bytes(original)
                    os.utime(
                        target,
                        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                    )

            with mock.patch.object(
                performance.contest_demo,
                "verify_showcase",
                side_effect=swap_while_replaying,
            ), self.assertRaisesRegex(
                performance.SameKernelPerformanceError, "raw Guest log replay"
            ):
                performance.load_samples(path)

    def test_schema4_requires_explicit_diagnostic_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "legacy.json"
            path.write_text(json.dumps(_report()), encoding="utf-8")
            with self.assertRaisesRegex(
                performance.SameKernelPerformanceError, "schema 6"
            ):
                performance.load_samples(path)
            sample = performance.load_samples(
                path, allow_uncontrolled_legacy=True
            )[0]
            self.assertFalse(sample["formal_evidence"])
            self.assertEqual(sample["protocol"]["cache_state"], "uncontrolled")

    def test_current_single_run_is_numeric_but_diagnostic(self) -> None:
        sample = performance.normalize_report(_report())
        dataset = performance.build_dataset([sample])
        group = dataset["groups"][0]

        self.assertEqual(
            group["metrics"]["recovery_core_latency_us"]["compat"]["p50"],
            1200,
        )
        self.assertEqual(
            group["metrics"]["physical_writes"]["native"]["p50"], 10
        )
        self.assertIsNone(group["metrics"]["end_to_end_latency_us"])
        self.assertEqual(
            group["claim_boundaries"]["recovery_core"]["evidence"],
            "diagnostic",
        )
        self.assertEqual(
            group["claim_boundaries"]["cow_exec_cache"]["evidence"],
            "workflow_only",
        )
        self.assertFalse(dataset["headline_eligible"])

    def test_legacy_counterbalanced_runs_remain_diagnostic(self) -> None:
        samples = []
        for index in range(8):
            order = ("compat", "native") if index % 2 == 0 else ("native", "compat")
            samples.append(
                performance.normalize_report(_report(index, _protocol(order)))
            )
        group = performance.build_dataset(samples)["groups"][0]

        self.assertEqual(group["lane_order_counts"]["compat_native"], 4)
        self.assertEqual(group["lane_order_counts"]["native_compat"], 4)
        self.assertEqual(
            group["claim_boundaries"]["recovery_core"]["evidence"],
            "diagnostic",
        )
        self.assertEqual(
            group["claim_boundaries"]["io_epoch"]["evidence"],
            "diagnostic",
        )
        self.assertFalse(group["formal_evidence"])
        self.assertFalse(group["headline_eligible"])
        self.assertEqual(
            group["claim_boundaries"]["end_to_end"]["evidence"], "unavailable"
        )

    def test_legacy_cache_claims_are_forced_uncontrolled(self) -> None:
        warm = performance.normalize_report(
            _report(0, _protocol(("compat", "native"), "warm"))
        )
        cold = performance.normalize_report(
            _report(1, _protocol(("native", "compat"), "cold"))
        )
        dataset = performance.build_dataset([warm, cold])

        self.assertEqual(len(dataset["groups"]), 1)
        self.assertEqual(dataset["groups"][0]["cache_state"], "uncontrolled")
        self.assertFalse(dataset["headline_eligible"])

    def test_end_to_end_requires_both_lanes(self) -> None:
        report = _report()
        report["comparison"]["lanes"]["compat"]["end_to_end_duration_us"] = 1800

        with self.assertRaisesRegex(
            performance.SameKernelPerformanceError, "both lanes"
        ):
            performance.normalize_report(report)

    def test_showcase_three_decimal_buffer_ratio_is_accepted(self) -> None:
        report = _report()
        report["mechanisms"]["native"]["epoch_commits"] = 3
        report["mechanisms"]["native"]["epoch_buffers_staged"] = 8
        report["mechanisms"]["native"]["buffers_per_epoch"] = 2.667

        sample = performance.normalize_report(report)

        self.assertEqual(
            sample["mechanisms"]["native"]["buffers_per_epoch"], 2.666667
        )

    def test_outcome_mismatch_is_rejected(self) -> None:
        report = _report()
        report["comparison"]["lanes"]["native"]["outcome_hash"] += 1

        with self.assertRaisesRegex(
            performance.SameKernelPerformanceError, "outcome hashes"
        ):
            performance.normalize_report(report)

    def test_legacy_scope_claim_is_not_trusted(self) -> None:
        protocol = _protocol(("compat", "native"))
        protocol["counter_isolation"] = "scope_bound"

        sample = performance.normalize_report(_report(protocol=protocol))
        self.assertEqual(
            sample["protocol"]["counter_isolation"], "global_unisolated"
        )
        self.assertFalse(sample["formal_evidence"])

    def test_sidecar_must_bind_summary_bytes_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = root / "summary.json"
            summary.write_text(json.dumps(_report()), encoding="utf-8")
            protocol = _protocol(("compat", "native"))
            protocol.update(
                {
                    "run_id": "0000000000000001",
                    "summary_sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
                }
            )
            sidecar = root / "protocol.json"
            sidecar.write_text(json.dumps(protocol), encoding="utf-8")
            sample = performance.load_sample(
                summary, sidecar, allow_uncontrolled_legacy=True
            )
            self.assertFalse(sample["protocol"]["declared"])
            self.assertEqual(sample["protocol"]["cache_state"], "uncontrolled")

            corrupted = copy.deepcopy(protocol)
            corrupted["summary_sha256"] = "0" * 64
            sidecar.write_text(json.dumps(corrupted), encoding="utf-8")
            with self.assertRaisesRegex(
                performance.SameKernelPerformanceError, "summary_sha256"
            ):
                performance.load_sample(
                    summary, sidecar, allow_uncontrolled_legacy=True
                )

    def test_outputs_contain_absolute_numbers_and_attribution(self) -> None:
        dataset = performance.build_dataset(
            [performance.normalize_report(_report())]
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            performance.write_outputs(dataset, output)
            emitted = json.loads(
                (output / "same-kernel-metrics.json").read_text(encoding="utf-8")
            )
            with (output / "same-kernel-metrics.csv").open(
                encoding="utf-8", newline=""
            ) as source:
                rows = list(csv.DictReader(source))

        self.assertEqual(emitted["sample_count"], 1)
        latency = next(row for row in rows if row["metric"] == "recovery_core_latency_us")
        self.assertEqual(latency["compat_p50"], "1200")
        self.assertEqual(latency["native_p50"], "300")
        cow = next(row for row in rows if row["metric"] == "cow_shared_pages")
        self.assertEqual(cow["attribution"], "workflow_p50=8")


if __name__ == "__main__":
    unittest.main()
