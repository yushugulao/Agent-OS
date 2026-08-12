#!/usr/bin/env python3
"""Mutation tests for the stable factory lifecycle view."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "user" / "src" / "workflow_teardown_race_ucore.c"


class ContractError(AssertionError):
    pass


def compact(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def function(source: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*\([^;]*?\)\s*\{{", source, re.S)
    if match is None:
        raise ContractError(f"missing function: {name}")
    start = match.end() - 1
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
    raise ContractError(f"unterminated function: {name}")


def require(source: str, needle: str, message: str) -> None:
    if needle not in source:
        raise ContractError(message)


def validate(source: str) -> None:
    stable_view = compact(function(source, "factory_lifecycle_view_equal"))
    v3_tail = compact(function(source, "lifecycle_v3_tail_well_formed"))
    sized_prefix = compact(function(source, "check_lifecycle_sized_prefix"))
    sized_prefix_errors = compact(
        function(source, "check_lifecycle_sized_prefix_errors")
    )
    factory_view = compact(function(source, "check_factory_lifecycle_view"))
    foreign_compare = compact(
        function(source, "check_factory_self_only_foreign_compare")
    )

    require(
        stable_view,
        "returnbytes_equal(left,right,AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE);",
        "factory lifecycle comparison includes live v3 scheduler telemetry",
    )
    for needle, message in (
        (
            "info->scheduler_mode==AGENT_WORKFLOW_SCHED_MODE_EEVDF||"
            "info->scheduler_mode==AGENT_WORKFLOW_SCHED_MODE_FALLBACK",
            "v3 tail does not validate its scheduler mode",
        ),
        (
            "info->scheduler_weight==1024U",
            "v3 tail does not validate its frozen scheduler weight",
        ),
        (
            "info->scheduler_wakeup_samples==wakeup_samples",
            "v3 tail does not validate its final wake histogram",
        ),
    ):
        require(v3_tail, needle, message)
    require(
        sized_prefix,
        "memset(&oversized,0xa5,sizeof(oversized));",
        "oversized lifecycle probe is not poisoned before copyout",
    )
    require(
        sized_prefix,
        "lifecycle_v3_tail_well_formed(&oversized.info)",
        "oversized lifecycle copy does not prove the v3 tail was written",
    )
    require(
        sized_prefix_errors,
        "memset(&bad_parameter,0xa5,sizeof(bad_parameter));",
        "out-of-range STALE lifecycle probe is not poisoned before copyout",
    )
    require(
        sized_prefix_errors,
        "lifecycle_v3_tail_well_formed(&bad_parameter)",
        "out-of-range STALE lifecycle copy does not prove the v3 tail was written",
    )
    require(
        factory_view,
        "factory_lifecycle_view_equal(&info,&factory_lifecycle_baseline)",
        "factory baseline does not use the frozen lifecycle view",
    )
    require(
        foreign_compare,
        "memset(&compared,0xa5,sizeof(compared));",
        "foreign STALE lifecycle probe is not poisoned before copyout",
    )
    require(
        foreign_compare,
        "lifecycle_v3_tail_well_formed(&compared)",
        "foreign STALE lifecycle copy does not prove the v3 tail was written",
    )
    require(
        foreign_compare,
        "factory_lifecycle_view_equal(&before,&compared)",
        "foreign lifecycle comparison includes live scheduler telemetry",
    )
    require(
        foreign_compare,
        "factory_lifecycle_view_equal(&before,&after)",
        "post-foreign lifecycle comparison includes live scheduler telemetry",
    )


class WorkflowTeardownLifecycleViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE.read_text(encoding="utf-8")

    def rejected(self, source: str) -> None:
        with self.assertRaises(ContractError):
            validate(source)

    def test_repository_view_contract_is_valid(self) -> None:
        validate(self.source)

    def test_rejects_live_scheduler_tail_in_factory_view(self) -> None:
        old = "AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE"
        self.assertIn(old, self.source, "factory lifecycle prefix anchor drifted")
        self.rejected(
            self.source.replace(
                old, "sizeof(struct agent_workflow_lifecycle_info)", 1
            )
        )

    def test_rejects_full_stale_snapshot_comparison(self) -> None:
        old = "factory_lifecycle_view_equal(&before, &compared)"
        self.assertIn(old, self.source, "stale comparison anchor drifted")
        self.rejected(
            self.source.replace(
                old, "bytes_equal(&before, &compared, sizeof(before))", 1
            )
        )

    def test_rejects_full_post_lookup_comparison(self) -> None:
        old = "factory_lifecycle_view_equal(&before, &after)"
        self.assertIn(old, self.source, "post-lookup comparison anchor drifted")
        self.rejected(
            self.source.replace(
                old, "bytes_equal(&before, &after, sizeof(before))", 1
            )
        )

    def test_rejects_oversized_copy_without_v3_tail_proof(self) -> None:
        old = "lifecycle_v3_tail_well_formed(&oversized.info) &&"
        self.assertIn(old, self.source, "oversized v3 tail anchor drifted")
        self.rejected(self.source.replace(old, "1 &&", 1))

    def test_rejects_capacity_stale_copy_without_v3_tail_proof(self) -> None:
        old = "lifecycle_v3_tail_well_formed(&bad_parameter),"
        self.assertIn(old, self.source, "capacity STALE v3 tail anchor drifted")
        self.rejected(self.source.replace(old, "1,", 1))

    def test_rejects_foreign_stale_copy_without_v3_tail_proof(self) -> None:
        old = "lifecycle_v3_tail_well_formed(&compared),"
        self.assertIn(old, self.source, "foreign STALE v3 tail anchor drifted")
        self.rejected(self.source.replace(old, "1,", 1))

    def test_rejects_v3_tail_without_final_histogram_shape(self) -> None:
        old = "info->scheduler_wakeup_samples == wakeup_samples;"
        self.assertIn(old, self.source, "v3 histogram anchor drifted")
        self.rejected(self.source.replace(old, "1;", 1))

    def test_rejects_unpoisoned_oversized_copy_probe(self) -> None:
        old = "memset(&oversized, 0xa5, sizeof(oversized));"
        self.assertIn(old, self.source, "oversized poison anchor drifted")
        self.rejected(self.source.replace(old, old.replace("0xa5", "0"), 1))

    def test_rejects_unpoisoned_foreign_stale_probe(self) -> None:
        old = "memset(&compared, 0xa5, sizeof(compared));"
        self.assertIn(old, self.source, "foreign STALE poison anchor drifted")
        self.rejected(self.source.replace(old, old.replace("0xa5", "0"), 1))


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    raise SystemExit(0 if result.wasSuccessful() else 1)
