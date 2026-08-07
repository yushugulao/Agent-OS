#!/usr/bin/env python3
"""任务 6 配对程序源码回执的回归测试。"""

from __future__ import annotations

import copy
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from . import evaluation_scenario as scenario
    from . import agenteval_measurement_source_contract as measurement_source
    from . import scenario_timing_source_contract
except ImportError:  # 支持从 host_tools/ 目录直接执行。
    import evaluation_scenario as scenario
    import agenteval_measurement_source_contract as measurement_source
    import scenario_timing_source_contract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
).strip()


def _measurement_source_receipt() -> dict[str, object]:
    return {
        "contract_versions": {
            "functional": measurement_source.FUNCTIONAL_CONTRACT_VERSION,
            "functional_compile": measurement_source.FUNCTIONAL_COMPILE_CONTRACT_VERSION,
            "micro": measurement_source.CONTRACT_VERSION,
            "policy": measurement_source.POLICY_INVENTORY_SCHEMA,
            "scenario": scenario_timing_source_contract.CONTRACT_VERSION,
        },
        "formal_boot_count": measurement_source.FORMAL_BOOT_COUNT,
        "policy_inventory": measurement_source.measurement_source_policy_inventory(),
        "schema": measurement_source.RECEIPT_SCHEMA,
        "source_commit": SOURCE_COMMIT,
        "sources": [
            {
                "bytes": len(data),
                "path": path,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for path in measurement_source._receipt_source_paths()
            for data in [(ROOT / path).read_bytes()]
        ],
        "stop_rule": measurement_source.STOP_RULE,
    }


def _rebind_pair(pair: dict[str, object]) -> None:
    unsigned = dict(pair)
    unsigned.pop("sha256", None)
    pair["sha256"] = scenario._binding_sha256(
        unsigned, scenario.PROGRAM_SOURCE_PAIR_DOMAIN
    )


def _rebind_manifest(binding: dict[str, object]) -> None:
    unsigned = dict(binding)
    unsigned.pop("sha256", None)
    binding["sha256"] = scenario._binding_sha256(
        unsigned, scenario.PROGRAM_MANIFEST_BINDING_DOMAIN
    )


def _rebind_receipt(receipt: dict[str, object]) -> None:
    unsigned = dict(receipt)
    unsigned.pop("sha256", None)
    receipt["sha256"] = scenario._binding_sha256(
        unsigned, scenario.PROGRAM_SOURCE_RECEIPT_DOMAIN
    )


def _sample(
    programs: tuple[str, ...],
    plain: dict[str, object],
    agentos: dict[str, object],
) -> dict[str, object]:
    return {
        "binding": {
            "source_commit": SOURCE_COMMIT,
            "program_order": list(programs),
        },
        "targets": {
            "plain": {
                "raw_source_receipt": {
                    "program_source_comparability": plain,
                }
            },
            "agentos": {
                "raw_source_receipt": {
                    "program_source_comparability": agentos,
                }
            },
        },
    }


class Task6SourceComparabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.programs, _ = scenario.read_committed_expected_programs(
            ROOT, SOURCE_COMMIT
        )
        cls.receipt = scenario._program_source_comparability_receipt(
            ROOT, SOURCE_COMMIT, cls.programs
        )
        cls.measurement_receipt = _measurement_source_receipt()

    def test_repository_receipt_covers_all_registered_programs(self) -> None:
        summary = scenario._validate_program_source_comparability_receipt(
            self.receipt, self.programs, SOURCE_COMMIT
        )

        self.assertEqual(len(self.programs), 70)
        self.assertEqual(summary["expected_programs"], 70)
        self.assertEqual(summary["same_source_programs"], 28)
        self.assertEqual(summary["platform_specific_programs"], 42)
        self.assertEqual(summary["receipt_sha256"], self.receipt["sha256"])
        self.assertEqual(
            [pair["program"] for pair in self.receipt["programs"]],
            list(self.programs),
        )
        for pair in self.receipt["programs"]:
            for target in ("agentos", "plain"):
                record = pair[target]
                data = (ROOT / record["path"]).read_bytes()
                self.assertEqual(record["bytes"], len(data))
                self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())
                self.assertRegex(record["git_blob_oid"], r"[0-9a-f]{40}")

    def test_collector_rejects_unresolved_commit_and_worktree_drift(self) -> None:
        with self.assertRaises(scenario.ScenarioEvidenceError):
            scenario._program_source_comparability_receipt(
                ROOT, "a" * 40, self.programs
            )

        with tempfile.TemporaryDirectory(prefix="task6-source-git-") as temporary:
            repo = Path(temporary)
            for relative in scenario.PROGRAM_MANIFEST_PATHS:
                destination = repo / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)
            for relative in scenario._program_source_paths(self.programs):
                destination = repo / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "task6@example.invalid"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Task6 Test"], cwd=repo, check=True
            )
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "fixture"], cwd=repo, check=True
            )
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip()
            scenario._program_source_comparability_receipt(
                repo, commit, self.programs
            )
            changed = repo / scenario._program_source_paths(self.programs)[0]
            changed.write_bytes(changed.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                scenario.ScenarioEvidenceError, "differs from source_commit blob"
            ):
                scenario._program_source_comparability_receipt(
                    repo, commit, self.programs
                )

    def test_portable_snapshot_needs_no_git_and_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task6-source-snapshot-") as temporary:
            snapshot = Path(temporary)
            for record in self.measurement_receipt["sources"]:
                destination = snapshot / record["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / record["path"], destination)
            self.assertFalse((snapshot / ".git").exists())
            programs, _ = scenario.read_snapshot_expected_programs(
                snapshot, SOURCE_COMMIT, self.measurement_receipt
            )
            rebuilt = scenario._program_source_comparability_receipt_from_snapshot(
                snapshot, SOURCE_COMMIT, programs, self.measurement_receipt
            )
            self.assertEqual(rebuilt, self.receipt)
            with self.assertRaises(scenario.ScenarioEvidenceError):
                scenario._program_source_comparability_receipt_from_snapshot(
                    snapshot,
                    SOURCE_COMMIT,
                    programs[:-1],
                    self.measurement_receipt,
                )
            changed = snapshot / scenario._program_source_paths(programs)[0]
            changed.write_bytes(changed.read_bytes() + b"tamper")
            with self.assertRaisesRegex(
                scenario.ScenarioEvidenceError, "measurement-source receipt"
            ):
                scenario._program_source_comparability_receipt_from_snapshot(
                    snapshot, SOURCE_COMMIT, programs, self.measurement_receipt
                )

    def test_validator_rejects_semantic_tampering_after_rebinding(self) -> None:
        forged_relation = copy.deepcopy(self.receipt)
        forged_relation["programs"][0]["relation"] = (
            "platform_specific"
            if forged_relation["programs"][0]["relation"] == "same_source"
            else "same_source"
        )
        _rebind_pair(forged_relation["programs"][0])
        _rebind_receipt(forged_relation)

        forged_path = copy.deepcopy(self.receipt)
        forged_path["programs"][0]["agentos"]["path"] = (
            f"user/src/{self.programs[1]}.c"
        )
        _rebind_pair(forged_path["programs"][0])
        _rebind_receipt(forged_path)

        forged_count = copy.deepcopy(self.receipt)
        forged_count["same_source_programs"] = 0
        forged_count["platform_specific_programs"] = 70
        _rebind_receipt(forged_count)

        forged_manifest = copy.deepcopy(self.receipt)
        forged_manifest["manifest_binding"]["program_order_sha256"] = "f" * 64
        _rebind_manifest(forged_manifest["manifest_binding"])
        _rebind_receipt(forged_manifest)

        for forged in (
            forged_relation,
            forged_path,
            forged_count,
            forged_manifest,
        ):
            with self.subTest(forged=forged):
                with self.assertRaises(scenario.ScenarioEvidenceError):
                    scenario._validate_program_source_comparability_receipt(
                        forged, self.programs, SOURCE_COMMIT
                    )

    def test_summary_rejects_cross_target_and_cross_boot_receipts(self) -> None:
        alternate = copy.deepcopy(self.receipt)
        pair = alternate["programs"][0]
        was_same = pair["relation"] == "same_source"
        pair["plain"]["sha256"] = "f" * 64
        pair["relation"] = "platform_specific"
        _rebind_pair(pair)
        if was_same:
            alternate["same_source_programs"] -= 1
            alternate["platform_specific_programs"] += 1
        _rebind_receipt(alternate)
        scenario._validate_program_source_comparability_receipt(
            alternate, self.programs, SOURCE_COMMIT
        )

        with self.assertRaises(scenario.ScenarioEvidenceError):
            scenario._source_comparability_summary(
                [_sample(self.programs, self.receipt, alternate)]
            )
        with self.assertRaises(scenario.ScenarioEvidenceError):
            scenario._source_comparability_summary(
                [
                    _sample(self.programs, self.receipt, self.receipt),
                    _sample(self.programs, alternate, alternate),
                ]
            )


if __name__ == "__main__":
    unittest.main()
