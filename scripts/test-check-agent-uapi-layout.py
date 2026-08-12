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
        self.assertEqual(len(self.golden), 560)
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
            self.golden["agent_uapi_layout_size_execution_contract_node"],
            168,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_execution_contract_node_schema_digest"
            ],
            57,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_size_request_v3"], 200
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_offset_request_v3_contract"], 73
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_request_v3_source_control_id"
            ],
            185,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_offset_request_v3_source_pid"],
            193,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_request_v3_source_reserved"
            ],
            197,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_size_response_v3"], 280
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_size_provenance_manifest"], 32
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_size_task_sqe"], 128
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_offset_task_sqe_schema_digest"],
            97,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_size_task_cqe"], 128
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_task_channel_enter_result_last_accepted_request_id"
            ],
            97,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_value_task_channel_setup_syscall"],
            563,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_value_task_resource_utf8_max"],
            63,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_value_workflow_lifecycle_info_v2_size"
            ],
            64,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_size_file_publish_request"],
            64,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_file_publish_request_payload_size"
            ],
            45,
        )
        self.assertEqual(
            self.golden[
                "agent_uapi_layout_offset_file_publish_request_reserved_tail"
            ],
            49,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_value_file_publish_syscall"],
            566,
        )
        self.assertEqual(
            self.golden["agent_uapi_layout_value_file_publish_max_bytes"],
            4096,
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
            for relative in ("os/agent.h", "user/include/agent.h"):
                source = (ROOT / relative).read_text(encoding="utf-8")
                (root / relative).write_text(source, encoding="utf-8")
            path = root / "user" / "include" / "agent.h"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "#define AGENT_FILE_META_F_UNSUPPORTED_MASK ",
                    "/* tombstone removed */",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                agent_uapi_layout.LayoutError, "not explicitly unsupported"
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
        source = (ROOT / "include" / "agent_workflow_fence_abi.h").read_text(
            encoding="utf-8"
        )
        self.assertIn('#include "agent_lifecycle_abi.h"', source)
        self.assertIn("sizeof(struct agent_workflow_fence_request) == 56", source)
        self.assertIn("sizeof(struct agent_workflow_fence_receipt) == 320", source)

    def feature_fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "os").mkdir()
        (root / "include").mkdir()
        (root / "user" / "include").mkdir(parents=True)
        for relative in (
            "include/agent_execution_contract_abi.h",
            "include/agent_file_publish_abi.h",
            "include/agent_lifecycle_abi.h",
            "include/agent_provenance_abi.h",
            "include/agent_resource_abi.h",
            "include/agent_task_channel_abi.h",
            "include/agent_tool_abi.h",
            "os/agent.h",
            "user/include/agent.h",
        ):
            path = root / relative
            path.write_text(
                (ROOT / relative).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        return temporary, root

    def test_new_feature_constants_and_lifecycle_prefix_are_frozen(self):
        agent_uapi_layout.validate_feature_abi_constants(ROOT)
        self.assertEqual(
            set(agent_uapi_layout.SHARED_HEADER_PROBES),
            {
                "workflow-fence",
                "execution-contract",
                "provenance",
                "file-publish",
                "task-channel",
            },
        )
        temporary, root = self.feature_fixture()
        with temporary:
            path = root / "include" / "agent_task_channel_abi.h"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "#define AGENT_TASK_CHANNEL_CAPACITY      16U",
                    "#define AGENT_TASK_CHANNEL_CAPACITY      32U",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                agent_uapi_layout.LayoutError, "frozen value 16"
            ):
                agent_uapi_layout.validate_feature_abi_constants(root)

            path.write_text(
                (ROOT / "include" / "agent_task_channel_abi.h")
                .read_text(encoding="utf-8")
                .replace(
                    "#define AGENT_TASK_RESOURCE_UTF8_MAX      63U",
                    "#define AGENT_TASK_RESOURCE_UTF8_MAX      64U",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                agent_uapi_layout.LayoutError, "frozen value 63"
            ):
                agent_uapi_layout.validate_feature_abi_constants(root)

    def test_syscall_reservations_reject_partial_task_wiring(self):
        agent_uapi_layout.validate_agent_syscall_numbers(ROOT)
        temporary = tempfile.TemporaryDirectory()
        with temporary:
            root = Path(temporary.name)
            paths = (
                "os/syscall_ids.h",
                "user/lib/syscall_ids.h",
                "user/lib/arch/riscv/syscall_ids.h.in",
                "os/agent.h",
                "user/include/agent.h",
            )
            for relative in paths:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    (ROOT / relative).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            path = root / "user/lib/syscall_ids.h"
            source = path.read_text(encoding="utf-8")
            definition = "#define SYS_agent_task_channel_enter 564\n"
            self.assertIn(definition, source)
            path.write_text(
                source.replace(definition, "", 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                agent_uapi_layout.LayoutError, "atomically mirror"
            ):
                agent_uapi_layout.validate_agent_syscall_numbers(root)

            publish_definition = "#define SYS_agent_file_publish 566\n"
            self.assertIn(publish_definition, source)
            path.write_text(
                source.replace(publish_definition, "", 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                agent_uapi_layout.LayoutError, "atomically mirror"
            ):
                agent_uapi_layout.validate_agent_syscall_numbers(root)

    def schema_fixture(self, key_literal):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "os").mkdir()
        (root / "include").mkdir()
        (root / "user" / "include").mkdir(parents=True)
        abi = (ROOT / "include" / "agent_tool_abi.h").read_text(
            encoding="utf-8"
        )
        protocol = (ROOT / "os" / "agent_tool_protocol.c").read_text(
            encoding="utf-8"
        )
        abi = abi.replace('X(REPLY_SUMMARY, "reply_summary")', key_literal)
        (root / "include" / "agent_tool_abi.h").write_text(
            abi, encoding="utf-8"
        )
        (root / "os" / "agent_tool_protocol.c").write_text(
            protocol, encoding="utf-8"
        )
        for relative in (
            "include/agent_provenance_abi.h",
            "os/agent.h",
            "user/include/agent.h",
        ):
            (root / relative).write_text(
                (ROOT / relative).read_text(encoding="utf-8"),
                encoding="utf-8",
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
            path = root / "include" / "agent_tool_abi.h"
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

    def test_tool_security_registry_requires_one_row_per_tool(self):
        temporary, root = self.schema_fixture(
            'X(REPLY_SUMMARY, "reply_summary")'
        )
        with temporary:
            path = root / "os" / "agent_tool_protocol.c"
            source = path.read_text(encoding="utf-8").replace(
                "\tX(0, AGENT_PROVENANCE_ALL, PROV_DERIVED, 0) \\\n",
                "",
                1,
            )
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(
                agent_uapi_layout.LayoutError, "not one-to-one"
            ):
                agent_uapi_layout.validate_tool_protocol_schema(root)

    def test_tool_security_registry_rejects_unowned_numeric_masks(self):
        temporary, root = self.schema_fixture(
            'X(REPLY_SUMMARY, "reply_summary")'
        )
        with temporary:
            path = root / "os" / "agent_tool_protocol.c"
            source = path.read_text(encoding="utf-8").replace(
                "X(0, AGENT_PROVENANCE_ALL, PROV_DERIVED, 0)",
                "X(1ULL, AGENT_PROVENANCE_ALL, PROV_DERIVED, 0)",
                1,
            )
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(
                agent_uapi_layout.LayoutError, "unowned numeric mask"
            ):
                agent_uapi_layout.validate_tool_protocol_schema(root)

    def test_tool_manifest_requires_common_id_binding(self):
        temporary, root = self.schema_fixture(
            'X(REPLY_SUMMARY, "reply_summary")'
        )
        with temporary:
            path = root / "os" / "agent_tool_protocol.c"
            source = path.read_text(encoding="utf-8").replace(
                "&agent_tool_security[tool_id - 1]",
                "&agent_tool_security[tool_id]",
                1,
            )
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(
                agent_uapi_layout.LayoutError, "common tool id"
            ):
                agent_uapi_layout.validate_tool_protocol_schema(root)


if __name__ == "__main__":
    unittest.main()
