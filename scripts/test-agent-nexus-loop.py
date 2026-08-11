#!/usr/bin/env python3
"""Static security and integration contracts for the AgentOS Nexus Guest."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
GUEST = (ROOT / "user/src/agentnexus_ucore.c").read_text(encoding="utf-8")
LIB = (ROOT / "user/lib/agent_nexus.c").read_text(encoding="utf-8")
API = (ROOT / "user/include/agent_nexus.h").read_text(encoding="utf-8")
PROTOCOL = (ROOT / "user/include/agent_nexus_protocol.h").read_text(encoding="utf-8")
SEED = (ROOT / "user/include/agentnexus_seed.h").read_text(encoding="utf-8")
MANIFEST = (ROOT / "user/include/exec_policy_manifest.h").read_text(encoding="utf-8")
IDENTITY = (ROOT / "os/agent_identity.c").read_text(encoding="utf-8")
CORE = (ROOT / "os/agent_core.c").read_text(encoding="utf-8")
SECURITY = (ROOT / "user/src/agentsecurity_ucore.c").read_text(encoding="utf-8")
HOST = (ROOT / "host_tools/agentos_relayd.py").read_text(encoding="utf-8")
OBSERVER = (ROOT / "host_tools/agentos_observe.py").read_text(encoding="utf-8")


class ContractError(AssertionError):
    pass


def require(source: str, needle: str, message: str) -> None:
    if needle not in source:
        raise ContractError(f"{message}: missing {needle!r}")


def forbid(source: str, needle: str, message: str) -> None:
    if needle in source:
        raise ContractError(f"{message}: found forbidden {needle!r}")


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*\([^;]*?\)\s*\{{", source, re.S)
    if match is None:
        raise ContractError(f"missing function {name}")
    start = match.end() - 1
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
    raise ContractError(f"unterminated function {name}")


def python_function(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            if segment is not None:
                return segment
    raise ContractError(f"missing Python function {name}")


def require_order(source: str, needles: tuple[str, ...], message: str) -> None:
    cursor = -1
    for needle in needles:
        cursor = source.find(needle, cursor + 1)
        if cursor < 0:
            raise ContractError(f"{message}: missing or out of order {needle!r}")


def enum_values(source: str, prefix: str) -> dict[str, int]:
    return {
        name: int(value)
        for name, value in re.findall(rf"\b({re.escape(prefix)}[A-Z0-9_]+)\s*=\s*([0-9]+)", source)
    }


class AgentNexusLoopTests(unittest.TestCase):
    def test_guest_keeps_only_the_protocol_v2_runtime(self) -> None:
        require(GUEST, '#define LIVE_PREFIX_V2 "@AGENTOS/2 "', "Nexus V2 prefix is missing")
        require(
            function_body(GUEST, "live_open_session"),
            "live_parse_hello_v2(",
            "Nexus HELLO no longer uses the V2 parser",
        )
        require(
            function_body(GUEST, "live_relay_loop"),
            "live_relay_loop_v2(",
            "Nexus relay no longer enters the persistent V2 loop",
        )
        require(
            function_body(GUEST, "live_workflow"),
            "live_workflow_v2(",
            "Nexus workflow no longer enters the persistent V2 loop",
        )
        for needle in (
            '"@AGENTOS/1 "',
            "live_parse_hello(",
            "live_parse_decision(",
            "live_build_request(",
            "live_receive_decision(",
            "live_execute_decision(",
            "live_observer_worker(",
        ):
            forbid(GUEST, needle, "retired Nexus V1/dead runtime returned")

    def test_seed_provenance_and_exec_profile_are_versioned(self) -> None:
        for needle in (
            '#define AGENTNEXUS_SEED_VERSION 3U',
            '#define AGENTNEXUS_SEED_CASE_NAME "nexus_case"',
            '#define AGENTNEXUS_SEED_MEAS_NAME "nexus_meas"',
            '#define AGENTNEXUS_SEED_STATE_NAME "nexus_state"',
            '"schema=agentos.nexus.case.v2\\n"',
            '"source_contract=agentos.nexus.workflow.v1\\n"',
            '"seed_revision=3\\n"',
            '"schema=agentos.nexus.measurement.v2\\n"',
            '"source_revision=2b14fb1f74b9bd093e6de939a16554620835699e\\n"',
            '"source_pipeline=watch>query>delegate>plan>govern>publish>audit\\n"',
            '"source_roles=coordinator,system,research,analyst\\n"',
            '"nexus_derived_project=lab-gene-x\\n"',
            '"nexus_derived_workflow=nightly-regression\\n"',
            '"nexus_derived_run_id=RUN-042\\n"',
            '"nexus_derived_incident=align_memory_limit\\n"',
            '"source_manifest=one_shot_metrics/data/20260811/manifest.json\\n"',
            '"source_table=one_shot_metrics/data/20260811/tables/contest_paired.csv\\n"',
            '"benchmark=file_query_core_path_paired\\n"',
            '"records=96\\n"',
            '"traversal_us=34712.5\\n"',
            '"indexed_us=13293.5\\n"',
            '"paired_ratio_median=3.118\\n"',
            '"wins=16/16\\n"',
            '"nexus_derived_checks=16/16\\n"',
            '"nexus_derived_checks_basis=wins\\n"',
            '"nexus_derived_claim=published_snapshot\\n"',
            '"nexus_derived_measurement_scope=historical_not_this_boot\\n"',
            '"claim=this_boot_runtime_observation\\n"',
            '"published_benchmark=false\\n"',
        ):
            require(SEED, needle, "tracked Nexus capsule contract changed")
        forbid(
            SEED,
            '"schema=agentos.nexus.measurement.v1\\n"',
            "retired Nexus measurement schema returned",
        )
        forbid(SEED, '"ratio=', "ambiguous unpaired ratio field returned")
        for needle in (
            "docs/",
            "source_path=",
            "source_lines=",
            "source_results=",
            "source_results_lines=",
        ):
            forbid(SEED, needle, "Nexus seed regained document or line-number coupling")
        require(
            GUEST,
            '"canonical paired measurement dataset"',
            "Nexus measurement metadata lost its canonical dataset identity",
        )
        forbid(
            GUEST,
            "4-boot ABBA measurement",
            "retired Nexus measurement metadata returned",
        )
        self.assertGreaterEqual(SEED.count("sizeof(AGENTNEXUS_SEED_"), 6)

        row = re.search(
            r'X\("agentnexus_ucore",\s*"agentnexus_ucore",(?P<body>.*?)\)\s*\\',
            MANIFEST,
            re.S,
        )
        self.assertIsNotNone(row, "agentnexus exec manifest row is missing")
        body = row.group("body") if row is not None else ""
        self.assertIn("EXEC_MANIFEST_F_BOOT_SEALED", body)
        expected_roles = {
            "EXEC_MANIFEST_ROLE_ORCHESTRATOR",
            "EXEC_MANIFEST_ROLE_SENTINEL",
            "EXEC_MANIFEST_ROLE_INVESTIGATOR",
            "EXEC_MANIFEST_ROLE_ARTIFACT",
        }
        self.assertEqual(
            set(re.findall(r"EXEC_MANIFEST_ROLE_(?!BIT\b)[A-Z]+", body)),
            expected_roles,
            "Nexus may create only its four long-lived business Agent roles",
        )
        self.assertIn("EXEC_MANIFEST_VFS_PROFILE_WORKFLOW", body)

        sentinel = re.search(
            r"\{\s*AGENT_ROLE_SENTINEL,\s*(?P<caps>.*?)\s*,\s*0,\s*70\s*\}",
            IDENTITY,
            re.S,
        )
        self.assertIsNotNone(sentinel, "Sentinel kernel role policy is missing")
        sentinel_caps = sentinel.group("caps") if sentinel is not None else ""
        expected_caps = {
            "AGENT_CAP_META_READ",
            "AGENT_CAP_PROCESS_READ",
            "AGENT_CAP_MESSAGE_SEND",
            "AGENT_CAP_WATCH",
            "AGENT_CAP_AUDIT_WRITE",
        }
        self.assertEqual(
            set(re.findall(r"AGENT_CAP_[A-Z_]+", sentinel_caps)),
            expected_caps,
            "Nexus must not widen the global System/Sentinel role policy",
        )
        expected_caps_body = function_body(SECURITY, "expected_caps")
        sentinel_branch = expected_caps_body[
            expected_caps_body.index("if (role == AGENT_ROLE_SENTINEL)") :
            expected_caps_body.index("if (role == AGENT_ROLE_INVESTIGATOR)")
        ]
        self.assertEqual(
            set(re.findall(r"AGENT_CAP_[A-Z_]+", sentinel_branch)),
            expected_caps,
            "security role oracle drifted from the Sentinel kernel policy",
        )
        capability_map = function_body(CORE, "agent_cap_for_action")
        for action in ('"query_process"', '"get_system_status"'):
            require(
                capability_map,
                action,
                "System process-read action is absent from capability_check",
            )
        process_branch = capability_map[
            capability_map.index('"query_process"') :
            capability_map.index('if (strncmp(action, "query"')
        ]
        require(
            process_branch,
            "AGENT_CAP_PROCESS_READ",
            "System status capability_check maps to the wrong capability",
        )
        forbid(
            process_branch,
            "AGENT_CAP_ACTION_WRITE",
            "System status capability_check accidentally grants a write",
        )
        sentinel_runtime = function_body(SECURITY, "run_sentinel")
        for needle in (
            '"query_process"',
            '"get_system_status"',
            '"sentinel digest denied"',
        ):
            require(
                sentinel_runtime,
                needle,
                "security runtime lacks the narrow System policy expectation",
            )

    def test_typed_task_wire_has_canonical_state_and_runtime_checks(self) -> None:
        kinds = enum_values(PROTOCOL, "AGENT_NEXUS_TASK_")
        self.assertEqual(
            {key: kinds[key] for key in (
                "AGENT_NEXUS_TASK_ASSIGN",
                "AGENT_NEXUS_TASK_ACCEPT",
                "AGENT_NEXUS_TASK_PROGRESS",
                "AGENT_NEXUS_TASK_RESULT",
                "AGENT_NEXUS_TASK_FAILED",
                "AGENT_NEXUS_TASK_CANCEL",
            )},
            {
                "AGENT_NEXUS_TASK_ASSIGN": 1,
                "AGENT_NEXUS_TASK_ACCEPT": 2,
                "AGENT_NEXUS_TASK_PROGRESS": 3,
                "AGENT_NEXUS_TASK_RESULT": 4,
                "AGENT_NEXUS_TASK_FAILED": 5,
                "AGENT_NEXUS_TASK_CANCEL": 6,
            },
        )
        require(PROTOCOL, "AGENT_NEXUS_TASK_WIRE_SIZE    44U", "TASK wire extent is not frozen")
        require(PROTOCOL, "AGENT_NEXUS_TASK_B64_SIZE     59U", "TASK base64url extent is not frozen")
        require(PROTOCOL, "AGENT_NEXUS_TASK_TEXT_SIZE    62U", "TASK MESSAGE extent is not frozen")
        require(PROTOCOL, "AGENT_NEXUS_TASK_TEXT_SIZE < AGENT_EVENT_PAYLOAD_SIZE", "TASK does not prove MESSAGE fit")

        validate = function_body(LIB, "agent_nexus_task_validate")
        for pair in (
            ("AGENT_NEXUS_TASK_ASSIGN", "AGENT_NEXUS_TASK_STATE_ASSIGNED"),
            ("AGENT_NEXUS_TASK_ACCEPT", "AGENT_NEXUS_TASK_STATE_ACCEPTED"),
            ("AGENT_NEXUS_TASK_PROGRESS", "AGENT_NEXUS_TASK_STATE_WAITING"),
            ("AGENT_NEXUS_TASK_RESULT", "AGENT_NEXUS_TASK_STATE_COMPLETED"),
            ("AGENT_NEXUS_TASK_FAILED", "AGENT_NEXUS_TASK_STATE_FAILED"),
            ("AGENT_NEXUS_TASK_CANCEL", "AGENT_NEXUS_TASK_STATE_CANCELLED"),
        ):
            for needle in pair:
                require(validate, needle, "TASK kind/state validation weakened")
        require(validate, "task->lifecycle_generation == 0", "TASK permits a null lifecycle generation")
        require(validate, "task->deadline_tick == 0", "TASK permits an unbounded deadline")
        require(validate, "task->flags & ~AGENT_NEXUS_TASK_F_KNOWN_MASK", "TASK permits unknown flags")

        runtime = function_body(LIB, "agent_nexus_task_validate_runtime")
        for needle in (
            "task->lifecycle_id != expected_lifecycle->id",
            "task->lifecycle_generation != expected_lifecycle->generation",
            "task->deadline_tick - current_tick",
        ):
            require(runtime, needle, "TASK runtime binding weakened")
        transition = function_body(LIB, "agent_nexus_task_transition_validate")
        for needle in (
            "previous->lifecycle_generation != next->lifecycle_generation",
            "previous->parent_task_id != next->parent_task_id",
            "previous->deadline_tick != next->deadline_tick",
            "previous->kind == AGENT_NEXUS_TASK_RESULT",
            "previous->kind == AGENT_NEXUS_TASK_FAILED",
            "previous->kind == AGENT_NEXUS_TASK_CANCEL",
        ):
            require(transition, needle, "TASK transition permits identity or terminal reuse")

        decode = function_body(LIB, "agent_nexus_task_decode_n")
        require_order(
            decode,
            (
                "nexus_base64_decode(",
                "AGENT_NEXUS_TASK_MAGIC",
                "AGENT_NEXUS_TASK_VERSION",
                "agent_nexus_task_validate(task)",
                "agent_nexus_task_encode(task, canonical)",
                "nexus_bytes_equal(text, canonical, AGENT_NEXUS_TASK_TEXT_SIZE)",
            ),
            "TASK decode must reject noncanonical encodings",
        )
        send = function_body(LIB, "agent_nexus_task_send")
        require_order(
            send,
            (
                "agent_nexus_task_encode(task, message)",
                'strcpy(arguments[0].key, "target_pid")',
                'strcpy(arguments[1].key, "message")',
                'agent_nexus_tool_call("send_message", task_id',
            ),
            "TASK must travel as typed V2 MESSAGE with task_id correlation",
        )

    def test_role_filtered_tool_calls_remain_typed_v2(self) -> None:
        for pattern in (
            r"AGENT_TOOL_QUERY_PROCESS,\s*NX_COORD \| NX_SYSTEM,\s*AGENT_CAP_PROCESS_READ",
            r"AGENT_TOOL_READ_FILE_SUMMARY,\s*NX_COORD \| NX_RESEARCH \| NX_ANALYST,\s*AGENT_CAP_CONTENT_READ",
            r"AGENT_TOOL_WRITE_REPORT,\s*NX_COORD \| NX_ANALYST,\s*AGENT_CAP_ARTIFACT_WRITE",
            r"AGENT_TOOL_LLM_REQUEST,\s*NX_COORD,\s*AGENT_CAP_MESSAGE_SEND",
            r"AGENT_TOOL_LLM_RESPONSE,\s*NX_RELAY,\s*AGENT_CAP_LLM_RELAY",
        ):
            self.assertRegex(LIB, re.compile(pattern, re.S))
        discover = function_body(LIB, "agent_nexus_tools_discover")
        for needle in (
            "tool_list(nexus_tool_catalog, AGENT_TOOL_COUNT)",
            "AGENT_CALL_VERSION_V2",
            "sizeof(nexus_tool_catalog[i])",
            "descriptor->tool_id <= 0",
            "strnlen(descriptor->name, sizeof(descriptor->name))",
        ):
            require(discover, needle, "kernel tool discovery is not validated")

        views = function_body(LIB, "agent_nexus_tool_views_for_role_class")
        for needle in (
            "AGENT_NEXUS_TOOL_ROLE(product_role)",
            "spec->product_role_mask & role_bit",
            "spec->required_capabilities",
            "agent_nexus_product_capabilities(product_role)",
            "descriptor->flags & AGENT_TOOL_F_CALLABLE",
        ):
            require(views, needle, "role-visible tool catalog is not filtered")

        call = function_body(LIB, "agent_nexus_tool_call")
        for needle in (
            "tool->flags & AGENT_TOOL_F_CALLABLE",
            "argument_count > tool->param_count",
            "request.version = AGENT_CALL_VERSION_V2",
            "request.tool_id = tool->tool_id",
            "strcpy(request.tool_name, tool->name)",
            "AGENT_PARAM_UINT64",
            "tool_call(&request, response)",
            "response->request_id != request_id",
            "response->tool_id != tool->tool_id",
        ):
            require(call, needle, "typed V2 request/response binding weakened")
        schema = function_body(LIB, "nexus_schema_arguments_valid")
        self.assertRegex(
            schema,
            re.compile(r"arguments\[argument_index\]\.type\s*==\s*AGENT_PARAM_UINT64"),
            "V2 schema loses uint64 typing",
        )
        self.assertRegex(
            schema,
            re.compile(r"arguments\[argument_index\]\.type\s*==\s*AGENT_PARAM_STRING"),
            "V2 schema loses string typing",
        )
        require(schema, "if (!matched && !optional)", "V2 schema loses required/optional ordering")
        require(schema, "return argument_index == argument_count", "V2 schema accepts extra arguments")
        call_as = function_body(LIB, "agent_nexus_tool_call_as")
        require(call_as, "spec->product_role_mask", "role call bypasses the role mask")
        require(call_as, "spec->required_capabilities", "role call bypasses capability filtering")
        require(call_as, "agent_nexus_tool_call(", "role call bypasses typed V2")

    def test_artifact_read_revalidates_full_payload_manifest_and_lifecycle(self) -> None:
        handle = function_body(LIB, "agent_nexus_artifact_handle_validate")
        require(handle, "agent_nexus_artifact_handle_make(lifecycle_generation", "artifact handle ignores lifecycle generation")
        require(handle, "expected != handle", "artifact handle accepts a stale generation")

        manifest = function_body(LIB, "agent_nexus_artifact_manifest_validate")
        for needle in (
            "manifest->lifecycle.id == 0",
            "manifest->lifecycle.generation == 0",
            "manifest->flags & ~AGENT_NEXUS_ARTIFACT_F_KNOWN_MASK",
            "!nexus_actor_shape_valid(&manifest->producer)",
            "!nexus_actor_shape_valid(&manifest->owner)",
            "!nexus_actor_shape_valid(&manifest->materializer)",
            "manifest->provenance_labels & ~AGENT_PROVENANCE_ALL",
            "manifest->permission_mask & ~AGENT_NEXUS_ARTIFACT_READ_ALL",
            "agent_nexus_artifact_handle_validate(",
        ):
            require(manifest, needle, "artifact manifest validation weakened")

        store = function_body(LIB, "nexus_artifact_store")
        require_order(
            store,
            (
                "agent_nexus_sha256(payload, payload_size, stored->payload_sha256)",
                "memset(stored->manifest_sha256, 0",
                "agent_nexus_sha256(&digest_header, sizeof(digest_header)",
                "agent_file_edit_begin(",
                "existing = open(path, O_RDONLY)",
                "pending_header.magic = 0",
                "nexus_write_all(fd, &pending_header, sizeof(pending_header))",
                "nexus_write_all(fd, payload, payload_size)",
                "fsync(fd)",
                "fd = open(path, O_WRONLY)",
                "nexus_write_all(fd, stored, sizeof(*stored))",
                "fsync(fd)",
                "agent_file_edit_commit(",
            ),
            "artifact publication is not fully hashed, publish-once, and header-last",
        )
        forbid(store, "link(", "artifact publication depends on unsupported VFS link")
        forbid(store, "unlink(", "artifact publication depends on unsupported VFS unlink")

        read = function_body(LIB, "agent_nexus_artifact_read_verify")
        for needle in (
            "agent_workflow_lifecycle_info(&lifecycle, expected_lifecycle)",
            "header->handle_generation != AGENT_NEXUS_ARTIFACT_GENERATION(handle)",
            "header->handle_slot != AGENT_NEXUS_ARTIFACT_SLOT(handle)",
            "header->lifecycle_id != expected_lifecycle->id",
            "header->lifecycle_generation != expected_lifecycle->generation",
            "header->payload_size > capacity",
            "!agent_nexus_artifact_manifest_validate(&manifest)",
            "header->kind != expected_kind",
            "header->permission_mask & agent_nexus_product_permission(",
            "nexus_read_all(fd, payload, header->payload_size)",
            "tail = read(fd, &extra, 1)",
            "agent_nexus_sha256(payload, header->payload_size, digest)",
            "header->payload_sha256",
            "memset(digest_header.manifest_sha256, 0",
            "agent_nexus_sha256(&digest_header, sizeof(digest_header), digest)",
            "header->manifest_sha256",
        ):
            require(read, needle, "artifact read accepts stale, partial or tampered content")

        broker = function_body(LIB, "nexus_brokered_manifest_valid")
        require(broker, "manifest->materializer.product_role !=", "broker is not coordinator-bound")
        require(broker, "AGENT_NEXUS_ROLE_COORDINATOR", "broker materializer is not the Coordinator")
        require(broker, "manifest->producer.product_role == AGENT_NEXUS_ROLE_SYSTEM", "System producer identity is lost")
        require(broker, "manifest->producer.product_role == AGENT_NEXUS_ROLE_RESEARCH", "Research producer identity is lost")
        broker_guest = function_body(GUEST, "nexus_publish_brokered")
        require(broker_guest, "manifest->producer", "brokered artifact loses logical worker producer")
        require(broker_guest, "manifest->materializer", "brokered artifact loses Coordinator materializer")
        require(
            broker_guest,
            "manifest->owner = manifest->materializer",
            "brokered artifact owner is not the Coordinator materializer",
        )

    def test_measurement_projection_and_report_provenance_are_source_bound(self) -> None:
        measurement = function_body(GUEST, "nexus_measurement_valid")
        for key in (
            "source_manifest=",
            "source_table=",
            "records=",
            "traversal_us=",
            "indexed_us=",
            "paired_ratio_median=",
            "wins=",
            "nexus_derived_checks=",
            "nexus_derived_checks_basis=",
            "nexus_derived_claim=",
            "nexus_derived_measurement_scope=",
        ):
            require(measurement, f'"{key}"', "measurement parser lost a canonical source field")

        for name in (
            "nexus_measurement_event_summary",
            "nexus_measurement_compact_event_summary",
        ):
            projection = function_body(GUEST, name)
            for key in (
                "source_manifest",
                "source_table",
                "records",
                "traversal_us",
                "indexed_us",
                "paired_ratio_median",
                "wins",
                "nexus_derived_checks",
            ):
                require(projection, f'"{key}"', "Research TASK summary lost bounded evidence")
            forbid(projection, '"source_revision"', "Research TASK summary exceeds its bounded purpose")
            forbid(projection, '"nexus_derived_claim"', "Research TASK summary exceeds its bounded purpose")
            require(projection, "builder.length <= 256", "Research TASK summary is not wire bounded")

        prepare = function_body(GUEST, "live_prepare_workspace")
        require_order(
            prepare,
            (
                "AGENTNEXUS_SEED_MEAS_BODY",
                "AGENT_PROVENANCE_UNTRUSTED_FILE_DATA",
                "AGENTNEXUS_SEED_MEAS_BODY",
            ),
            "measurement seed is not published with file-data provenance",
        )
        task_capsule = function_body(GUEST, "nexus_publish_task_capsule")
        for needle in (
            "AGENT_NEXUS_SOURCE_MODEL",
            "AGENT_PROVENANCE_AGENT_DERIVED",
            "AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT",
            "AGENT_PROVENANCE_CROSS_AGENT_DATA",
        ):
            require(
                task_capsule,
                needle,
                "model-selected TASK capsule loses its untrusted provenance",
            )
        forbid(
            task_capsule,
            "AGENT_NEXUS_SOURCE_USER",
            "model-selected TASK objective is mislabeled as direct user control",
        )
        forbid(
            task_capsule,
            "AGENT_PROVENANCE_TRUSTED_USER_CONTROL",
            "model-selected TASK objective is mislabeled trusted",
        )
        materialize = function_body(GUEST, "nexus_materialize_worker_result")
        for needle in (
            "result_provenance |= AGENT_PROVENANCE_KERNEL_FACT",
            '";sched_dispatch_count="',
            '";sched_budget="',
            '";sched_budget_used="',
            '";sched_vruntime="',
            "result_provenance |= source_header.provenance_labels",
            "task_id, parent_task_id, result_provenance",
        ):
            require(materialize, needle, "worker artifact drops source or kernel provenance")

        stable_system = function_body(GUEST, "nexus_system_stable_summary")
        for key in (
            '"source"',
            '"claim"',
            '"process_count"',
            '"context_count"',
            '"file_bytes"',
            '"sched_budget"',
        ):
            require(stable_system, key, "stable System model projection drops a verified fact")
        for volatile in (
            '"sched_dispatch_count"',
            '"sched_budget_used"',
            '"sched_vruntime"',
            '"digest"',
        ):
            forbid(stable_system, volatile, "stable System model projection includes a boot-volatile fact")

        report_event = function_body(GUEST, "nexus_report_event_summary")
        for key in (
            '"system_digest"',
            '"research_digest"',
            '"sched_budget"',
            '"paired_ratio_median"',
        ):
            require(report_event, key, "report event drops real artifact evidence")
        report_model = function_body(GUEST, "nexus_report_model_summary")
        for needle in (
            "nexus_system_stable_summary(",
            "nexus_measurement_compact_event_summary(",
            '"system_handle"',
            '"research_handle"',
            '"source_revision"',
        ):
            require(report_model, needle, "report model projection is not source-bound and stable")
        for volatile in (
            '"system_digest"',
            '"research_digest"',
            '"sched_dispatch_count"',
            '"sched_budget_used"',
            '"sched_vruntime"',
        ):
            forbid(report_model, volatile, "report model projection includes a boot-volatile fact")

        delegate_projection = function_body(GUEST, "nexus_delegate_task")
        for needle in ("nexus_system_model_summary", "nexus_report_model_summary("):
            require(delegate_projection, needle, "delegation returns a volatile artifact projection to the model")
        read_projection = function_body(GUEST, "nexus_read_product_artifact")
        for needle in ("nexus_system_stable_summary(", "nexus_report_model_summary("):
            require(read_projection, needle, "artifact read returns a volatile projection to the model")

        analyst = function_body(GUEST, "nexus_analyst_task")
        require_order(
            analyst,
            (
                "report_provenance = NEXUS_PROVENANCE_WORKER",
                "system_header.provenance_labels",
                "research_header.provenance_labels",
                "report_provenance,",
            ),
            "Analyst report does not union both verified artifact provenance sets",
        )

        history = re.search(
            r"struct\s+live_history_turn\s*\{(?P<body>.*?)\};",
            GUEST,
            re.S,
        )
        self.assertIsNotNone(history, "Nexus history turn structure is missing")
        history_body = history.group("body") if history is not None else ""
        forbid(
            history_body,
            "struct live_tool_result_wire",
            "bounded conversation history embeds the transient TASK_EVENT batch",
        )
        forbid(
            history_body,
            "nexus_events",
            "bounded conversation history retains transient TASK_EVENT records",
        )

    def test_four_roles_use_real_task_transport_and_nonbusy_workers(self) -> None:
        specialist = function_body(GUEST, "nexus_specialist_loop")
        for needle in (
            "agent_nexus_tools_discover()",
            'agent_watch(AGENT_EVENT_MESSAGE, "N1:")',
            "agent_wait(&event, 0x7fffffff)",
            "event.source_pid != coordinator_pid",
            "agent_nexus_task_decode(event.payload, &task)",
            "agent_nexus_task_validate_runtime(",
            "AGENT_NEXUS_TASK_ACCEPT",
            "AGENT_NEXUS_TASK_RESULT",
            "AGENT_NEXUS_TASK_FAILED",
            "AGENT_NEXUS_TASK_CANCEL",
            "AGENT_ROLE_SENTINEL",
            "AGENT_ROLE_INVESTIGATOR",
            "AGENT_ROLE_ARTIFACT",
        ):
            require(specialist, needle, "specialist loop is scripted output rather than typed TASK execution")
        pause = function_body(GUEST, "nexus_worker_nonbusy_pause")
        require_order(
            pause,
            (
                "AGENT_NEXUS_TASK_STATE_WAITING",
                "agent_wait(&event, 20)",
                "AGENT_NEXUS_TASK_STATE_RUNNING",
            ),
            "worker wait/resume is not a real nonbusy kernel wait",
        )
        worker_snapshot = function_body(GUEST, "nexus_worker_snapshot_progress")
        for needle in (
            "snapshot.wait_sleep_delta > 0xffULL",
            "snapshot.wait_wakeup_delta > 0xffULL",
            "codes[1] = NEXUS_METRIC_PACK_FILE_SCHED |",
            "((uint)snapshot.wait_sleep_delta << 16)",
            "((uint)snapshot.wait_wakeup_delta << 24)",
        ):
            require(worker_snapshot, needle, "worker snapshot does not carry verified wait deltas")
        delegate_metrics = function_body(GUEST, "nexus_delegate_task")
        require_order(
            delegate_metrics,
            (
                "NEXUS_METRIC_PACK_RESUME",
                "NEXUS_METRIC_PACK_BUSINESS",
                "worker_context_sequence = inline_value",
                "NEXUS_METRIC_PACK_FILE_SCHED",
                "worker_snapshot.wait_sleep_delta = inline_value & 0xffU",
                "worker_snapshot.wait_wakeup_delta = inline_value >> 8",
            ),
            "Coordinator final snapshot does not use final Context and verified wait deltas",
        )

        workflow = function_body(GUEST, "live_workflow")
        for role in ("AGENT_ROLE_SENTINEL", "AGENT_ROLE_INVESTIGATOR", "AGENT_ROLE_ARTIFACT"):
            require(workflow, f"agent_create_role({role})", "workflow does not create all business specialists")
        for pid in ("nexus_system_pid", "nexus_research_pid", "nexus_analyst_pid"):
            require(workflow, pid, "workflow does not retain an independent specialist PID")
        require(workflow, "agent_workflow_lifecycle_info(", "workflow does not bind Nexus state to its lifecycle")
        require(workflow, "nexus_identity_lookup(", "workflow does not obtain kernel-backed identities")

    def test_failed_research_replans_and_publish_denial_precedes_effect(self) -> None:
        specialist = function_body(GUEST, "nexus_specialist_loop")
        system_path = specialist[
            specialist.index("role == AGENT_ROLE_SENTINEL") :
            specialist.index("} else if ((task.flags & AGENT_NEXUS_TASK_F_HAS_INPUT)")
        ]
        for needle in (
            "task.status == AGENT_NEXUS_TASK_SYSTEM_SNAPSHOT",
            "task.flags != AGENT_NEXUS_TASK_F_HAS_RESULT",
            "task.value0 != 0",
            "task.value1",
            "agent_nexus_artifact_handle_validate(",
        ):
            require(
                system_path,
                needle,
                "System no-input TASK does not validate its frozen opcode/result handle",
            )
        forbid(
            system_path,
            "nexus_read_artifact_for_role(",
            "System no-input TASK still opens a VFS task capsule",
        )
        role_read = function_body(GUEST, "nexus_read_artifact_for_role")
        for needle in (
            "NEXUS_ARTIFACT_THREAD_READ_ROLE",
            "call.lifecycle = nexus_lifecycle",
            "call.reader_role = reader_role",
            "nexus_artifact_thread_run(&call)",
        ):
            require(role_read, needle, "worker capsule read bypasses lifecycle/role validation")
        artifact_worker = function_body(GUEST, "nexus_artifact_thread_worker")
        require(
            artifact_worker,
            "agent_nexus_artifact_read(",
            "worker capsule read does not reach the verified artifact API",
        )
        require_order(
            specialist,
            (
                "task.flags & AGENT_NEXUS_TASK_F_HAS_INPUT",
                "nexus_read_artifact_for_role(",
                "task.value0",
                "capsule.task_type != (uint)task.status",
                "capsule.objective_length == 0",
                "capsule.objective[capsule.objective_length] != 0",
                "capsule.target.control_id == 0",
                "capsule.target.pid != (uint)getpid()",
                "capsule.target.agent_id != (uint)info.agent_id",
                "capsule.target.kernel_role != (uint)role",
                "capsule.target.product_role != nexus_product_role(role)",
                "agent_nexus_identity_bind_control(",
            ),
            "worker does not authenticate and validate the TASK capsule before dispatch",
        )
        research = function_body(GUEST, "nexus_research_task")
        require_order(
            research,
            (
                "capsule->input_handle == 0",
                "nexus_read_artifact(capsule->input_handle",
                "AGENT_NEXUS_ARTIFACT_SEED",
                "nexus_measurement_valid",
                '"query_file"',
            ),
            "Research does not verify its task capsule before using the source handle",
        )
        delegate = function_body(GUEST, "nexus_delegate_task")
        for needle in (
            "capsule_handle = 0",
            "if (role_code == 's')",
            "result_handle = agent_nexus_artifact_handle_make(",
            "assigned.flags = role_code == 's' ? AGENT_NEXUS_TASK_F_HAS_RESULT",
            "assigned.value0 = capsule_handle",
            "assigned.value1 = role_code == 's' ? result_handle : 0",
        ):
            require(
                delegate,
                needle,
                "Coordinator no longer assigns System one result slot without a capsule",
            )
        require(delegate, "nexus_next_child_task++", "replan can reuse a failed task identity")
        require(delegate, "nexus_task_send(", "delegation bypasses the bounded TASK send path")
        task_send = function_body(GUEST, "nexus_task_send")
        require(task_send, "thread_create(nexus_task_send_thread_worker", "TASK send is not stack isolated")
        task_send_worker = function_body(GUEST, "nexus_task_send_thread_worker")
        require(
            task_send_worker,
            "agent_nexus_task_send(",
            "TASK send worker bypasses typed N1 over kernel MESSAGE",
        )
        require(
            task_send_worker,
            "exit(0);",
            "TASK send thread can return through a null user-thread trampoline",
        )
        task_reply = function_body(GUEST, "nexus_task_reply")
        require_order(
            task_reply,
            (
                "for (uint retry = 0; retry < 64; retry++)",
                "nexus_task_send(",
                "send_status != AGENT_STATUS_NO_SPACE",
                "nexus_current_tick() >= assigned->deadline_tick",
                "sched_yield()",
                "return AGENT_STATUS_NO_SPACE",
            ),
            "worker TASK replies have no deadline-bounded queue backpressure",
        )
        require(
            artifact_worker,
            "exit(0);",
            "artifact thread can return through a null user-thread trampoline",
        )
        require(delegate, "AGENT_NEXUS_TASK_FAILED", "delegation does not surface worker failure")
        require(delegate, "nexus_tasks_failed++", "failed task is not recorded for replanning")

        validate = function_body(GUEST, "live_validate_decision")
        require(validate, 'strcmp(decision->tool, "publish_report") != 0', "publish_report is implicitly approved")
        approval = function_body(GUEST, "live_v2_receive_approval")
        for needle in (
            "decision.tool_id != pending->tool_id",
            "decision.issued_tick != pending->issued_tick",
            "decision.expires_tick != pending->expires_tick",
            "strcmp(decision.digest, pending->digest)",
            "strcmp(decision.nonce, pending->nonce)",
            "info.current_tick >= pending->expires_tick",
        ):
            require(approval, needle, "approval decision is not bound to the exact pending call")
        execute = function_body(GUEST, "nexus_execute_decision")
        denied = execute.find("AGENT_STATUS_DENIED")
        effect = execute.find("nexus_publish_report_effect")
        self.assertGreaterEqual(denied, 0, "publish denial is not represented as a tool result")
        self.assertGreater(effect, denied, "publication effect can occur before approval denial")
        for needle in (
            "live_consume_approval(",
            '"not_approved"',
            '"approval_invalid"',
            "tool_result->value0 = 0",
            "tool_result->value1 = 0",
            "tool_result->value2 = 0",
        ):
            require(execute, needle, "publication approval is not exact and zero-effect on denial")

        register = function_body(GUEST, "nexus_register_report_artifact")
        for needle in (
            "AGENTNEXUS_SEED_PROJECT",
            "AGENTNEXUS_SEED_WORKFLOW",
            "AGENTNEXUS_SEED_RUN_ID",
            'strcpy(meta.stage, "nexus-report")',
        ):
            require(register, needle, "report metadata selector identity is not stable")
        publish = function_body(GUEST, "nexus_publish_report_effect")
        require(
            publish,
            '"project=lab-gene-x;stage=nexus-report;run_id=RUN-042"',
            "approved report publish does not select the registered artifact",
        )
        forbid(
            publish,
            '"project=agentos;',
            "approved report publish uses a project that cannot match registration",
        )
        require(publish, '"artifact_update"', "approved path does not execute the kernel update")

    def test_observer_kernel_sources_are_guest_only_and_semantically_distinct(self) -> None:
        observer = function_body(GUEST, "nexus_observer_worker")
        for needle in (
            "agent_info(",
            "agent_timeline_read(",
            "nexus_audit_drain()",
        ):
            require(observer, needle, "observer is not backed by kernel audit and self snapshots")
        audit_drain = function_body(GUEST, "nexus_audit_drain")
        require_order(
            audit_drain,
            (
                "mutex_lock(nexus_audit_mutex)",
                "filter.start_sequence = nexus_audit_cursor + 1",
                "agent_audit_query(",
                "nexus_project_audit_record(",
                "nexus_publish_kernel_telemetry(",
                "nexus_audit_cursor = nexus_audit_records[i].sequence",
                "} while (count == (int)(sizeof(nexus_audit_records)",
                "mutex_unlock(nexus_audit_mutex)",
            ),
            "shared audit drain is not serialized, raw-ordered, or page-complete",
        )
        audit_project = function_body(GUEST, "nexus_project_audit_record")
        for needle in (
            "AGENT_AUDIT_KIND_EVENT_ENQUEUE",
            "AGENT_AUDIT_KIND_EVENT_CONSUME",
            "AGENT_EVENT_MESSAGE",
            "source->workflow_lifecycle_id != nexus_lifecycle.id",
            "!nexus_business_pid(source->source_pid)",
            "!nexus_business_pid(source->target_pid)",
            "projected->record_sequence = source->sequence",
            "projected->value1 = source->value1",
        ):
            require(audit_project, needle, "audit projection accepts synthetic or out-of-scope records")
        delegate_audit = function_body(GUEST, "nexus_delegate_task")
        require_order(
            delegate_audit,
            (
                "nexus_task_send(target_pid, task_id, &assigned",
                "nexus_audit_drain()",
                "agent_wait(&message",
                "nexus_audit_drain()",
            ),
            "Coordinator does not synchronously drain both TASK enqueue and consume evidence",
        )
        snapshot = function_body(GUEST, "nexus_capture_self_snapshot")
        for needle in (
            "after.capability_mask == 0",
            "record.actor_control_id = control_id",
            "record.capability_mask = after.capability_mask",
        ):
            require(snapshot, needle, "worker snapshot lacks kernel-backed control/capability identity")
        emitter_start = GUEST.index("static int nexus_v2_emit_kernel_telemetry(")
        emitter_end = GUEST.index("static void nexus_telemetry_pump(", emitter_start)
        emitter = GUEST[emitter_start:emitter_end]
        for needle in (
            r'\"source\":\"kernel_audit\"',
            r'\"record_sequence\":',
            r'\"actor_control_id\":',
            r'\"source_pid\":',
            r'\"target_pid\":',
            r'\"value1\":',
            r'\"fresh\":true',
            r'\"source\":\"kernel_snapshot\"',
            r'\"capability_mask\":',
            r'\"wait_sleep_delta\":',
            r'\"wait_wakeup_delta\":',
            r'\"sched_dispatch_count\":',
            r'\"sched_vruntime\":',
            r'\"fresh\":false',
        ):
            require(emitter, needle, "kernel observer serializer omits a required typed field")
        forbid(emitter, '"context_seq":record.sequence', "audit sequence is mislabeled as Context sequence")

        guest_telemetry = python_function(HOST, "_guest_telemetry")
        require(guest_telemetry, 'allowed_sources.update(("kernel_audit", "kernel_snapshot"))', "Host does not profile-gate kernel sources")
        require(guest_telemetry, "self._validate_kernel_telemetry(payload, source)", "Host bypasses typed kernel telemetry validation")
        kernel_validation = python_function(HOST, "_validate_kernel_telemetry")
        require(kernel_validation, 'source == "kernel_audit"', "Host does not validate fresh audit shape")
        require(kernel_validation, "KERNEL_SNAPSHOT_REQUIRED_FIELDS.issubset(fields)", "Host does not validate snapshot fields")
        require(kernel_validation, 'payload.get("event") != "kernel_snapshot"', "Host does not validate snapshot shape")
        for needle in (
            'payload.get("actor_control_id"), "actor_control_id", minimum=1',
            'payload.get("capability_mask"), "capability_mask", minimum=1',
            "self._bind_kernel_identity(",
        ):
            require(kernel_validation, needle, "Host does not bind snapshot control/capability identity")
        snapshot_fields = HOST[
            HOST.index("KERNEL_SNAPSHOT_REQUIRED_FIELDS") :
            HOST.index("KERNEL_SNAPSHOT_OPTIONAL_FIELDS")
        ]
        for field in ('"actor_control_id"', '"capability_mask"'):
            require(snapshot_fields, field, "Host snapshot schema treats required identity evidence as optional")
        telemetry = python_function(HOST, "_telemetry")
        require(telemetry, 'source in ("kernel_audit", "kernel_snapshot") and not guest_origin', "Host can spoof a kernel source")
        require(telemetry, 'source = "host"', "Host spoof does not downgrade to host source")
        fields = HOST[HOST.index("OBSERVER_TELEMETRY_FIELDS"):HOST.index("def _positive_u64")]
        for secret in ('"raw"', '"summary"', '"content"', '"objective"'):
            forbid(fields, secret, "observer allowlist exposes business content")
        capabilities = python_function(OBSERVER, "_capabilities")
        require(capabilities, 'return f"caps=0x{value:x}"', "observer hides the capability snapshot")
        render = python_function(OBSERVER, "render_event")
        require(render, "_capabilities(event)", "default observer table omits capabilities")

        pump = function_body(GUEST, "nexus_telemetry_pump")
        require_order(
            pump,
            (
                "for (;;)",
                "live_read_all(pump->fd",
                "break;",
                "nexus_v2_emit_kernel_telemetry(",
            ),
            "telemetry pump does not consume the writer EOF before returning",
        )
        forbid(
            pump,
            "nexus_relay_pump_stop",
            "telemetry pump can discard a record read immediately before shutdown",
        )
        require(
            pump,
            "exit(0);",
            "telemetry pump can return through a null user-thread trampoline",
        )
        require(
            observer,
            "exit(1);",
            "observer publisher failure can return through a null user-thread trampoline",
        )
        require(
            observer,
            "exit(0);",
            "observer shutdown can return through a null user-thread trampoline",
        )
        require_order(
            observer,
            (
                "while (!live_observer_stop)",
                "nexus_audit_drain()",
                "if (nexus_audit_drain() < 0)",
                "nexus_observer_status = 1",
            ),
            "observer does not invoke the shared final audit drain after stop",
        )
        workflow_v2 = function_body(GUEST, "live_workflow_v2")
        require_order(
            workflow_v2,
            (
                "live_observer_stop = 1",
                "waittid(observer_tid)",
                "close(telemetry_write_fd)",
                "live_write_all(result_fd",
            ),
            "Coordinator acknowledges close before the observer joins and its writer reaches EOF",
        )
        finish = function_body(GUEST, "live_v2_finish_session")
        require_order(
            finish,
            (
                "live_read_all(result_fd",
                "waittid(telemetry_tid)",
                "close(telemetry_fd)",
                "mutex_lock(nexus_relay_tx_mutex)",
                '"SESSION_CLOSED"',
            ),
            "SESSION_CLOSED can race the observer writer or Relay EOF drain",
        )


if __name__ == "__main__":
    unittest.main()
