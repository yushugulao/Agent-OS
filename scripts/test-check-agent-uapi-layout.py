#!/usr/bin/env python3
"""冻结 Agent UAPI 布局契约的负向测试。"""

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-agent-uapi-layout.py")
ROOT = SCRIPT.parent.parent
SPEC = importlib.util.spec_from_file_location("agent_uapi_layout", SCRIPT)
agent_uapi_layout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent_uapi_layout)


class AgentUapiLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = agent_uapi_layout.load_golden(
            ROOT / "ci" / "agent-uapi-layout.json"
        )

    def test_golden_contract_has_expected_coverage(self):
        self.assertEqual(len(self.golden), 248)
        self.assertEqual(
            self.golden["agent_uapi_layout_value_ledger_version"], 3
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_offset_info_file_scan_deferred"],
            577,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_offset_info_file_scan_failures"],
            585,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_offset_info_metadata_journal_txns"],
            593,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_offset_info_metadata_full_cow_blocks"],
            617,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_lifecycle_info_resource_account_valid"
            ],
            49,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_lifecycle_info_resource_account_generation"
            ],
            57,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_size_performance_snapshot"],
            256,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_performance_snapshot_exec_cache_evictions"
            ],
            129,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_performance_snapshot_virtio_batched_read_requests"
            ],
            209,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_performance_snapshot_overwrite_prereads_skipped"
            ],
            225,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_size_workflow_fence_request"],
            56,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_size_workflow_fence_receipt"],
            320,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_offset_workflow_fence_request_reserved"],
            13,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_workflow_fence_receipt_resource_used"
            ],
            97,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_workflow_fence_receipt_credit_digest"
            ],
            193,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_workflow_fence_receipt_evidence_root"
            ],
            289,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_workflow_fence_receipt_evidence_last_sequence"
            ],
            73,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_value_workflow_fence_version"],
            1,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_size_file_live_watch"],
            368,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_offset_file_live_watch_query"],
            41,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_value_event_file_query"],
            10,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_value_file_meta_f_unsupported_mask"],
            6,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_value_observe_recovery_compat_tombstone"
            ],
            1,
        )

    def test_retired_syscall_numbers_remain_unassigned(self):
        id_paths = (
            ROOT / "os" / "syscall_ids.h",
            ROOT / "user" / "lib" / "syscall_ids.h",
            ROOT / "user" / "lib" / "arch" / "riscv" / "syscall_ids.h.in",
        )
        for path in id_paths:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("file_prefetch", source)
            self.assertNotRegex(source, r"\b(?:SYS_|__NR_)\w+\s+52[67]\b")
            self.assertRegex(source, r"\b(?:SYS_|__NR_)agent_sched_config\s+525\b")
            self.assertRegex(source, r"\b(?:SYS_|__NR_)agent_span_trace_snapshot\s+528\b")
            self.assertRegex(
                source,
                r"\b(?:SYS_|__NR_)agent_observe_recovery\s+550\b",
            )

        dispatch = (ROOT / "os" / "syscall.c").read_text(encoding="utf-8")
        recovery_case = dispatch[
            dispatch.index("case SYS_agent_observe_recovery:") :
            dispatch.index("break;", dispatch.index("case SYS_agent_observe_recovery:"))
        ]
        self.assertIn("AGENT_STATUS_BAD_PARAM", recovery_case)
        self.assertNotIn("sys_agent_observe_recovery(", recovery_case)

        for path in (
            ROOT / "os" / "syscall.c",
            ROOT / "os" / "syscall_counter.h",
            ROOT / "user" / "lib" / "syscall.c",
            ROOT / "user" / "include" / "agent.h",
        ):
            self.assertNotIn(
                "file_prefetch", path.read_text(encoding="utf-8")
            )

    def test_ledger_v3_accounts_restored_v8_kinds(self):
        for relative in ("os/agent.h", "user/include/agent.h"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("#define AGENT_LEDGER_VERSION           3", source)
            self.assertIn("#define AGENT_AUDIT_KIND_PREFETCH      5", source)
            self.assertIn("uint64 other_records;", source)
        query = (ROOT / "os/agent_observe_audit_query.c").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "summary.other_records = view.admission_drops +\n"
            "\t\tview.kind_counts[AGENT_AUDIT_KIND_PREFETCH];",
            query,
        )

    def test_compatibility_tombstones_are_explicit(self):
        agent_uapi_layout.validate_compatibility_tombstones(ROOT)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "os").mkdir()
            (root / "user" / "include").mkdir(parents=True)
            for relative in (
                "agent_observe_abi.h",
                "os/agent.h",
                "user/include/agent.h",
            ):
                source = (ROOT / relative).read_text(encoding="utf-8")
                (root / relative).write_text(source, encoding="utf-8")
            path = root / "agent_observe_abi.h"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "#define AGENT_OBSERVE_RECOVERY_COMPAT_TOMBSTONE 1U",
                    "/* tombstone removed */",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                agent_uapi_layout.LayoutError, "explicit ABI tombstone"
            ):
                agent_uapi_layout.validate_compatibility_tombstones(root)

    def test_size_drift_is_rejected(self):
        actual = copy.deepcopy(self.golden)
        actual["agent_uapi_layout_size_request_v2"] += 8
        with self.assertRaisesRegex(
            agent_uapi_layout.LayoutError, "frozen ABI drift"
        ):
            agent_uapi_layout.compare_golden(actual, self.golden)

    def test_missing_offset_is_rejected(self):
        actual = copy.deepcopy(self.golden)
        del actual["agent_uapi_layout_offset_request_v2_params"]
        with self.assertRaisesRegex(
            agent_uapi_layout.LayoutError, "frozen ABI drift"
        ):
            agent_uapi_layout.compare_golden(actual, self.golden)

    def test_workflow_fence_header_owns_its_dependencies(self):
        source = (ROOT / "agent_workflow_fence_abi.h").read_text(
            encoding="utf-8"
        )
        self.assertIn('#include "agent_lifecycle_abi.h"', source)
        self.assertIn("sizeof(struct agent_workflow_fence_request) == 56", source)
        self.assertIn("sizeof(struct agent_workflow_fence_receipt) == 320", source)

    def schema_fixture(self, key_literal):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "os").mkdir()
        abi = (ROOT / "agent_tool_abi.h").read_text(encoding="utf-8")
        protocol = (ROOT / "os" / "agent_tool_protocol.c").read_text(
            encoding="utf-8"
        )
        abi = abi.replace('X(REPLY_SUMMARY, "reply_summary")', key_literal)
        (root / "agent_tool_abi.h").write_text(abi, encoding="utf-8")
        (root / "os" / "agent_tool_protocol.c").write_text(
            protocol, encoding="utf-8"
        )
        return temporary, root

    def test_tool_schema_registry_is_complete_and_encodable(self):
        agent_uapi_layout.validate_tool_protocol_schema(ROOT)
        temporary, root = self.schema_fixture(
            'X(REPLY_SUMMARY, "123456789012345")'
        )
        with temporary:
            agent_uapi_layout.validate_tool_protocol_schema(root)

    def test_tool_schema_rejects_a_16_character_key(self):
        temporary, root = self.schema_fixture(
            'X(REPLY_SUMMARY, "1234567890123456")'
        )
        with temporary, self.assertRaisesRegex(
            agent_uapi_layout.LayoutError, "not NUL-terminated"
        ):
            agent_uapi_layout.validate_tool_protocol_schema(root)

    def test_tool_schema_requires_the_compile_time_capacity_guard(self):
        temporary, root = self.schema_fixture(
            'X(REPLY_SUMMARY, "reply_summary")'
        )
        with temporary:
            path = root / "agent_tool_abi.h"
            source = path.read_text(encoding="utf-8").replace(
                "AGENT_PARAM_KEY_REGISTRY(AGENT_PARAM_KEY_ASSERT)",
                "/* capacity guard removed */",
            )
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(
                agent_uapi_layout.LayoutError, "compile-time capacity guard"
            ):
                agent_uapi_layout.validate_tool_protocol_schema(root)

    def test_tool_schema_rejects_a_registry_bypass(self):
        temporary, root = self.schema_fixture(
            'X(REPLY_SUMMARY, "reply_summary")'
        )
        with temporary:
            path = root / "os" / "agent_tool_protocol.c"
            source = path.read_text(encoding="utf-8").replace(
                "R(REPLY_SUMMARY,", 'R("reply_summary",'
            )
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(
                agent_uapi_layout.LayoutError, "bypasses"
            ):
                agent_uapi_layout.validate_tool_protocol_schema(root)

    def test_tool_schema_rejects_a_non_monotonic_csr_index(self):
        temporary, root = self.schema_fixture(
            'X(REPLY_SUMMARY, "reply_summary")'
        )
        with temporary:
            path = root / "os" / "agent_tool_protocol.c"
            source = path.read_text(encoding="utf-8").replace(
                "0, 3, 3, 3, 4, 4, 4,", "0, 3, 2, 3, 4, 4, 4,"
            )
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(
                agent_uapi_layout.LayoutError, "offsets do not cover"
            ):
                agent_uapi_layout.validate_tool_protocol_schema(root)


if __name__ == "__main__":
    unittest.main()
