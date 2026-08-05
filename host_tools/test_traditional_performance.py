#!/usr/bin/env python3
"""Contract tests for the traditional-interface performance campaign."""

from __future__ import annotations

import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import traditional_performance as performance


NONCE = 0x123456789ABCDEF0
SAMPLE = 7
KNOWN_HASHES = (
    2167027917143403410,
    9224707053542613849,
    5601746541466351107,
    6782995567322458513,
    2765224305126396520,
)
KNOWN_AGGREGATE = 8613519748272980348


def metric(workload: str, target: str = "agentos", duration: int = 12_345) -> dict[str, object]:
    profile = performance.PROFILES[workload]
    value: dict[str, object] = {
        "nonce": NONCE,
        "sample": SAMPLE,
        "target": target,
        "workload": workload,
        "duration_us": duration,
        "duration_ticks": 12,
        "ops": profile["ops"],
        "outcome_hash": 0,
        "barrier_kind": profile["barrier_kind"][target],
    }
    for name in performance.COUNTERS:
        count = profile.get(name, 0)
        value[name] = count[target] if isinstance(count, dict) else count
    for name in performance.MECHANISM_COUNTERS:
        value[name] = 0
    if target == "agentos" and workload == "cache_read_4k":
        value["file_auth_full"] = value["read_calls"]
    if target == "agentos" and workload == "tiny_write_fsync":
        value["file_auth_full"] = 1
        value["file_auth_lease_hits"] = value["write_calls"] - 1
    value["outcome_hash"] = performance._expected_outcome(value, NONCE)
    return value


def metric_line(value: dict[str, object]) -> str:
    fields = [
        "agentos:tradperf schema=1",
        f"nonce={value['nonce']}", f"sample={value['sample']}",
        f"target={value['target']}", f"workload={value['workload']}",
        f"duration_us={value['duration_us']}",
        f"duration_ticks={value['duration_ticks']}", f"ops={value['ops']}",
        f"outcome_hash={value['outcome_hash']}",
    ]
    fields.extend(f"{name}={value[name]}" for name in
                  performance.COUNTERS + performance.MECHANISM_COUNTERS)
    fields.append(f"barrier_kind={value['barrier_kind']}")
    return " ".join(fields)


def guest_log(target: str = "agentos", slot: int = 1) -> bytes:
    metrics = [metric(workload, target) for workload in performance.WORKLOADS]
    aggregate = performance._aggregate_hash(NONCE, metrics)
    lines = [
        "uCore boot",
        f"agentos:tradperf schema=1 nonce={NONCE} sample={SAMPLE} "
        f"target={target} order_slot={slot} phase=begin tick_unit=ms",
        *[metric_line(value) for value in metrics],
        f"agentos:tradperf schema=1 nonce={NONCE} sample={SAMPLE} "
        f"target={target} phase=end aggregate_hash={aggregate}",
        "tradperf: complete",
    ]
    return ("\n".join(lines) + "\n").encode()


def parsed_pair() -> dict[str, object]:
    return {
        "agentos": performance.parse_guest(
            guest_log("agentos", 1), target="agentos", sample=SAMPLE,
            nonce=NONCE, order_slot=1,
        ),
        "baseline": performance.parse_guest(
            guest_log("baseline", 2), target="baseline", sample=SAMPLE,
            nonce=NONCE, order_slot=2,
        ),
    }


class TraditionalPerformanceTests(unittest.TestCase):
    def test_hash_vectors_are_host_recomputed(self) -> None:
        metrics = [metric(workload) for workload in performance.WORKLOADS]
        self.assertEqual(tuple(row["outcome_hash"] for row in metrics), KNOWN_HASHES)
        self.assertEqual(performance._aggregate_hash(NONCE, metrics), KNOWN_AGGREGATE)

    def test_strict_guest_protocol(self) -> None:
        parsed = performance.parse_guest(
            guest_log(), target="agentos", sample=SAMPLE,
            nonce=NONCE, order_slot=1,
        )
        self.assertEqual([row["workload"] for row in parsed["metrics"]], list(performance.WORKLOADS))
        self.assertEqual(parsed["aggregate_hash"], KNOWN_AGGREGATE)

    def test_forged_guest_hash_is_rejected(self) -> None:
        payload = guest_log().replace(
            f"outcome_hash={KNOWN_HASHES[0]}".encode(), b"outcome_hash=1", 1
        )
        with self.assertRaises(performance.TraditionalPerformanceError):
            performance.parse_guest(payload, target="agentos", sample=SAMPLE,
                                    nonce=NONCE, order_slot=1)

    def test_missing_and_malformed_protocol_are_rejected(self) -> None:
        payload = guest_log().replace(metric_line(metric("open_close")).encode() + b"\n", b"", 1)
        with self.assertRaises(performance.TraditionalPerformanceError):
            performance.parse_guest(payload, target="agentos", sample=SAMPLE,
                                    nonce=NONCE, order_slot=1)
        malformed = guest_log() + b"agentos:tradperf forged\n"
        with self.assertRaises(performance.TraditionalPerformanceError):
            performance.parse_guest(malformed, target="agentos", sample=SAMPLE,
                                    nonce=NONCE, order_slot=1)

    def test_wrong_workload_and_ab_slot_are_rejected(self) -> None:
        lines = guest_log().decode().splitlines()
        lines[2], lines[3] = lines[3], lines[2]
        with self.assertRaises(performance.TraditionalPerformanceError):
            performance.parse_guest(("\n".join(lines) + "\n").encode(),
                                    target="agentos", sample=SAMPLE,
                                    nonce=NONCE, order_slot=1)
        with self.assertRaises(performance.TraditionalPerformanceError):
            performance.parse_guest(guest_log(), target="agentos", sample=SAMPLE,
                                    nonce=NONCE, order_slot=2)

    def test_profile_counters_and_clock_relation_are_not_trusted(self) -> None:
        payload = guest_log().replace(b"read_calls=256", b"read_calls=255", 1)
        with self.assertRaises(performance.TraditionalPerformanceError):
            performance.parse_guest(payload, target="agentos", sample=SAMPLE,
                                    nonce=NONCE, order_slot=1)
        payload = guest_log().replace(b"duration_us=12345", b"duration_us=99999", 1)
        with self.assertRaises(performance.TraditionalPerformanceError):
            performance.parse_guest(payload, target="agentos", sample=SAMPLE,
                                    nonce=NONCE, order_slot=1)
        forged = guest_log().replace(
            b"duration_us=12345 duration_ticks=12",
            b"duration_us=1 duration_ticks=31", 1,
        )
        with self.assertRaises(performance.TraditionalPerformanceError):
            performance.parse_guest(forged, target="agentos", sample=SAMPLE,
                                    nonce=NONCE, order_slot=1)

    def test_non_equivalent_target_is_rejected(self) -> None:
        pair = parsed_pair()
        pair["baseline"]["metrics"][0]["ops"] += 1
        with self.assertRaises(performance.TraditionalPerformanceError):
            performance._validate_pair(pair)

    def test_artifact_mutation_and_missing_artifact_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact"
            path.write_bytes(b"immutable evidence")
            snapshot = performance._snapshot(path, "fixture", capture=True)
            path.write_bytes(b"changed evidence")
            with self.assertRaises(performance.TraditionalPerformanceError):
                performance._assert_stable(snapshot, "fixture")
            with self.assertRaises(performance.TraditionalPerformanceError):
                performance._snapshot(Path(directory) / "missing", "missing")

    def test_duplicate_json_key_is_rejected(self) -> None:
        with self.assertRaises(performance.TraditionalPerformanceError):
            performance._json_bytes(b'{"a":1,"a":2}', "fixture")
        with self.assertRaises(performance.TraditionalPerformanceError):
            performance._json_bytes(b'{"a":NaN,"b":Infinity}', "fixture")
        with self.assertRaises(ValueError):
            performance._canonical_json({"a": float("nan")})

    def test_csv_keeps_every_raw_pair_and_dashboard_uses_numbers(self) -> None:
        pairs = []
        for index in range(1, 9):
            pair = parsed_pair()
            pair["sample"] = index
            for target in performance.TARGETS:
                pair[target]["sample"] = index
            pairs.append(pair)
        summary_input = [{"agentos": pair["agentos"], "baseline": pair["baseline"]} for pair in pairs]
        report = {
            "campaign": {"pairs": 8, "boots": 16, "build_manifest_sha256": "a" * 64},
            "source": {"commit": "b" * 40},
            "workloads": performance._summarize(summary_input),
            "pairs": pairs,
        }
        csv_rows = list(csv.DictReader(io.StringIO(performance._csv(report).decode())))
        self.assertEqual(len(csv_rows), 8 * len(performance.WORKLOADS))
        self.assertEqual(csv_rows[0]["agentos_duration_us"], "12345")
        dashboard = performance._html(report).decode()
        self.assertIn("12345 us", dashboard)
        self.assertNotIn("通过", dashboard)
        self.assertNotIn("pass", dashboard.lower())


if __name__ == "__main__":
    unittest.main()
