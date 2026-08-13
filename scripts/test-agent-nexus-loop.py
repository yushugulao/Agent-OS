#!/usr/bin/env python3
"""Static contracts for the autonomous AgentOS Nexus Guest loop."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
GUEST = (ROOT / "user/src/agentnexus_ucore.c").read_text(encoding="utf-8")
PROTOCOL = (ROOT / "user/include/agent_nexus_protocol.h").read_text(encoding="utf-8")
SOURCE_API = (ROOT / "user/include/agent_nexus_source.h").read_text(encoding="utf-8")
SOURCE_LIB = (ROOT / "user/lib/agent_nexus_source.c").read_text(encoding="utf-8")
NEXUS_LIB = (ROOT / "user/lib/agent_nexus.c").read_text(encoding="utf-8")
USER_MAKE = (ROOT / "user/Makefile").read_text(encoding="utf-8")
ROOT_MAKE = (ROOT / "Makefile").read_text(encoding="utf-8")
HOST = (ROOT / "host_tools/agentos_relayd.py").read_text(encoding="utf-8")
PROVIDER = (ROOT / "host_tools/guest_llm_relay.py").read_text(encoding="utf-8")


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


def require_order(source: str, needles: tuple[str, ...], message: str) -> None:
    cursor = -1
    for needle in needles:
        cursor = source.find(needle, cursor + 1)
        if cursor < 0:
            raise ContractError(f"{message}: missing/out of order {needle!r}")


def c_string(source: str, name: str) -> str:
    match = re.search(
        rf"static const char\s+{re.escape(name)}\[\]\s*=\s*(?P<body>(?:\s*\"(?:\\.|[^\"\\])*\")+)\s*;",
        source,
        re.S,
    )
    if match is None:
        raise ContractError(f"missing C string {name}")
    return "".join(
        ast.literal_eval(token)
        for token in re.findall(r'"(?:\\.|[^"\\])*"', match.group("body"))
    )


class AgentNexusAutonomyTests(unittest.TestCase):
    def test_live_and_replay_use_the_same_model_token_contract(self) -> None:
        require(
            ROOT_MAKE,
            "AGENTOS_NEXUS_MAX_OUTPUT_TOKENS ?= 114514",
            "Nexus model token contract is not explicit",
        )
        self.assertEqual(
            ROOT_MAKE.count(
                "--max-output-tokens "
                "$(call shell_quote,$(AGENTOS_NEXUS_MAX_OUTPUT_TOKENS))"
            ),
            2,
            "live and strict replay do not share the same model token contract",
        )
        for variable, value, option in (
            ("AGENTOS_NEXUS_HTTP_TIMEOUT", "600", "--http-timeout"),
            (
                "AGENTOS_NEXUS_MAX_HTTP_RESPONSE_BYTES",
                "8388608",
                "--max-http-response-bytes",
            ),
        ):
            require(
                ROOT_MAKE,
                f"{variable} ?= {value}",
                f"Nexus {variable} contract is not explicit",
            )
            self.assertEqual(
                ROOT_MAKE.count(
                    f"{option} $(call shell_quote,$({variable}))"
                ),
                2,
                f"live and strict replay do not share {variable}",
            )

    def test_no_fixed_demo_content_or_orchestrator_remains(self) -> None:
        for forbidden in (
            "agentnexus_seed.h",
            "AGENTNEXUS_SEED_",
            "3.118",
            "13.452",
            "33.477",
            "live_state_tool_choice",
            "live_state_arguments_match",
            "nexus_canonical_objective",
            "nexus_final_canonical",
            "canonical evidence block",
            "Search exactly once first",
            "historical_not_this_boot",
            "outer_path_erases_gain",
            "delegate_task",
            "tool_search",
            "#if 0",
        ):
            forbid(GUEST, forbidden, "fixed Live Query product logic returned")

    def test_generation_and_final_wire_budgets_are_separate(self) -> None:
        for contract in (
            "#define LIVE_MAX_JSON 16384U",
            "#define LIVE_MAX_GOAL 2048U",
            "#define LIVE_MAX_TOKENS 114514U",
            "#define LIVE_MAX_FINAL_TEXT 2048U",
            "#define LIVE_MAX_WIRE_STRING 3072U",
            "#define LIVE_MAX_MODEL_REQUEST 15360U",
            "#define LIVE_MAX_ROUNDS 16U",
            '"max_user_bytes"',
            '"max_final_bytes"',
        ):
            require(GUEST, contract, "Nexus negotiated budget drifted")
        hello = function_body(GUEST, "live_parse_hello_v2")
        request = function_body(GUEST, "live_build_autonomous_request_v2")
        validate = function_body(GUEST, "live_validate_decision")
        require(hello, "number > LIVE_MAX_TOKENS",
                "generation budget is not bounded by 114514")
        require(hello, "hello->max_tokens = (uint)number;",
                "HELLO silently changes an accepted generation budget")
        forbid(hello, "65536", "retired generation ceiling remains")
        forbid(hello, "number > LIVE_MAX_TOKENS ?",
               "generation budget is silently clamped")
        require(request, "live_builder_u64(&builder, hello->max_tokens);",
                "MODEL_REQUEST does not preserve the negotiated generation budget")
        require(validate, "hello->max_final_bytes",
                "final text is not bounded by its separate wire budget")
        require(hello, "number > LIVE_MAX_GOAL", "user budget is not bounded")
        require(hello, "number > LIVE_MAX_FINAL_TEXT", "final budget is not bounded")
        require(HOST, "max_user_bytes", "Host does not negotiate user bytes")
        require(HOST, "max_final_bytes", "Host does not negotiate final bytes")
        require(HOST, "NEXUS_MAX_MODEL_REQUEST_BYTES = 15360",
                "Host and Guest request caps drifted")

    def test_v2_provider_wait_outlives_the_host_600_second_deadline(self) -> None:
        require(GUEST, "#define LIVE_WAIT_TICKS 9000",
                "legacy provider wait changed with the Nexus V2 timeout")
        require(GUEST, "#define LIVE_V2_WAIT_TICKS 66000",
                "Nexus V2 wait no longer includes deadline slack")
        self.assertGreater(66000, 600 * 100,
                           "Guest V2 wait must outlive the Host provider deadline")

    def test_model_sees_five_general_tools_without_forced_choice(self) -> None:
        tools = json.loads(c_string(GUEST, "live_tools_json"))
        self.assertEqual(
            [tool["name"] for tool in tools],
            [
                "source_search",
                "source_read",
                "inspect_runtime",
                "draft_report",
                "read_artifact",
            ],
        )
        for tool in tools:
            self.assertFalse(tool["input_schema"]["additionalProperties"])
        request = function_body(GUEST, "live_build_autonomous_request_v2")
        prompt = c_string(GUEST, "live_system_prompt")
        require(prompt, "tools may be repeated and reordered", "prompt lost autonomy")
        require(prompt, "You independently decide whether, which, and how often",
                "model is not the independent planner")
        require(prompt, "when you decide it is relevant",
                "source capability is presented as a mandatory stage")
        forbid(prompt, "Use source_search and source_read for claims",
               "neutral policy still mandates a fixed tool choice")
        require(request, "live_tools_json", "general tool set is not advertised")
        forbid(request, "tool_choice", "Guest still forces a provider tool choice")
        forbid(request, "next=", "Guest still embeds a scripted next stage")

    def test_arbitrary_utf8_goal_and_direct_final_are_supported(self) -> None:
        parse_input = function_body(GUEST, "live_parse_v2_input")
        require(parse_input, "sizeof(input->content)", "goal parser is not bounded")
        request = function_body(GUEST, "live_build_autonomous_request_v2")
        require(request, "live_text_utf8_bounded", "goal UTF-8 validation is missing")
        execute = function_body(GUEST, "nexus_execute_open_decision")
        require_order(
            execute,
            (
                "decision.type == LIVE_DECISION_FINAL",
                "nexus_root_terminal_after_cleanup(",
                "strcpy(final_answer, decision.final_text)",
                "return 1",
            ),
            "direct final is exposed before the terminal cleanup barrier",
        )
        forbid(execute, "publish_decision_handle", "final still requires publication")
        self.assertTrue("分析 AgentOS 内核模块的改进".encode("utf-8"))

    def test_root_task_is_visible_before_even_a_direct_final_request(self) -> None:
        workflow = function_body(GUEST, "live_workflow_v2")
        relay = function_body(GUEST, "live_relay_loop_v2")
        execute = function_body(GUEST, "nexus_execute_open_decision")
        require_order(
            workflow,
            (
                "nexus_result_write_fd = result_fd",
                "nexus_root_start(&tool_result",
                "nexus_root_ready(",
                "while (decision_rounds < command.max_rounds",
                "live_llm_call(",
            ),
            "root TASK lifecycle starts after the provider request",
        )
        require_order(
            relay,
            (
                '"interactive turn telemetry"',
                "live_v2_read_root_ready(",
                "while (decision_rounds < hello->max_rounds",
                "live_build_request_v2(",
            ),
            "Relay does not drain root events before MODEL_REQUEST",
        )
        root_drain = function_body(GUEST, "live_v2_read_root_ready")
        for binding in ("turn_id", "request_id", "corr_id", "event_count != 3"):
            require(root_drain, binding, f"root prelude omits {binding} binding")
        forbid(execute, "nexus_root_start(",
               "provider final can create a root lifecycle after the response")

    def test_sideband_preserves_full_decision_and_binds_every_dimension(self) -> None:
        send = function_body(GUEST, "live_sideband_send")
        receive = function_body(GUEST, "live_sideband_receive")
        relay = function_body(GUEST, "live_relay_loop_v2")
        for field in ("turn_id", "request_id", "corr_id"):
            require(send, f"writer.header.{field}", f"sideband omits {field}")
        require(send, "writer.decision = decision", "sideband omits the exact decision")
        require(send, "live_sideband_digest_parts", "sideband is not digested")
        for binding in (
            "header.turn_id != turn_id",
            "header.request_id != request_id",
            "header.corr_id != corr_id",
            "decision->corr_id != corr_id",
            "live_bytes_equal(digest, header.digest",
            'strcmp(marker, expected)',
        ):
            require(receive, binding, "sideband binding is incomplete")
        writer = function_body(GUEST, "live_sideband_writer_worker")
        require_order(writer, ("&writer->header", "writer->decision"),
                      "sideband no longer streams bound header then exact decision")
        require(relay, "live_sideband_send(", "Relay does not send full decisions")
        require(relay, "sideband_tid > 0", "valid decision can fall back to compact args")
        forbid(function_body(GUEST, "live_make_compact"), "decision->arguments",
               "compact kernel event still transports/truncates arguments")

    def test_every_host_delivered_tool_rejection_preserves_exact_history(self) -> None:
        relay = function_body(GUEST, "live_relay_loop_v2")
        rejected = function_body(GUEST, "live_history_append_rejected_call")
        require_order(
            relay,
            (
                "validation_error = live_validate_decision(",
                "validation_error == 0)",
                "live_history_append(history, &history_count,",
                "else if (decision.type == LIVE_DECISION_TOOL)",
                "live_history_append_rejected_call(",
                "&decision",
            ),
            "a Host-delivered tool rejection can be normalized or omitted",
        )
        require(rejected, "live_history_append(history, count, decision, result)",
                "rejected call does not retain exact delivered tool and arguments")
        require(rejected, "AGENT_STATUS_BAD_PARAM",
                "rejected call lacks structured tool-error feedback")
        require(rejected, "&live_rejected_result_workspace",
                "rejected-call scratch returned to the bounded user stack")
        forbid(rejected, "\n\tstruct live_tool_result_wire result;",
               "rejected-call projection consumes an automatic 3 KiB frame")
        for stack_contract in (
            "STACK_USAGE_APPLICATION_SRCS",
            "-fstack-usage",
            "-fcallgraph-info=su",
            "scripts/check-user-stack-usage.py",
        ):
            require(USER_MAKE, stack_contract,
                    "RISC-V rejected-call path is not stack-profiled")
        forbid(GUEST, "live_history_append_validation_error",
               "a delivered tool call can still be rewritten to empty arguments")
        request = function_body(GUEST, "live_build_autonomous_request_v2")
        require(request, "builder.length <= LIVE_MAX_MODEL_REQUEST",
                "a rejected call can make the next request exceed the Host cap")

        tools = {tool["name"]: tool for tool in json.loads(c_string(GUEST, "live_tools_json"))}
        cases = (
            ("source_search", "query", "界" * 32, 95, 96),
            ("draft_report", "content", "界" * 1000, 2800, 3000),
            ("draft_report", "title", "界" * 43, 128, 129),
        )
        for tool, field, value, schema_limit, byte_length in cases:
            with self.subTest(tool=tool, field=field):
                schema = tools[tool]["input_schema"]["properties"][field]
                self.assertLessEqual(len(value), schema["maxLength"])
                self.assertEqual(schema["maxLength"], schema_limit)
                self.assertEqual(len(value.encode("utf-8")), byte_length)
                self.assertGreater(len(value.encode("utf-8")), schema_limit)
        for tool, field in (("source_read", "start_line"),
                            ("read_artifact", "handle")):
            with self.subTest(tool=tool, field=field):
                schema = tools[tool]["input_schema"]["properties"][field]
                self.assertNotIn("maximum", schema)
                self.assertGreater(1 << 32, 0xFFFFFFFF)

    def test_large_relay_scratch_stays_off_the_riscv_call_path(self) -> None:
        parse = function_body(GUEST, "live_parse_decision_v2")
        wait = function_body(GUEST, "live_wait_tool_result_cancelable")
        receive = function_body(GUEST, "live_v2_receive_model")
        dispatch = function_body(GUEST, "nexus_dispatch_task")

        require(parse, "live_json_string(\n\t\t\t\t&parser, 0, LIVE_MAX_GOAL + 1)",
                "provider error diagnostic is retained instead of bounded validation")
        forbid(parse, "\n\tchar ignored[LIVE_MAX_GOAL + 1];",
               "provider error discard buffer consumes a 2 KiB frame")
        for body, label in ((wait, "cancel wait"), (receive, "model receive")):
            require(body, "&live_transient_input_workspace",
                    f"{label} scratch returned to the Relay stack")
            forbid(body, "\n\tstruct live_v2_input input;",
                   f"{label} consumes a 2 KiB automatic frame")
        for record in (
            "static struct nexus_kernel_telemetry worker_snapshot;",
            "static struct nexus_worker_result_binding worker_result;",
        ):
            require(dispatch, record,
                    "serial Coordinator dispatch scratch returned to the stack")
        for stack_contract in (
            "user-stack-check:",
            "-fstack-usage",
            "-fcallgraph-info=su",
            "$(PYTHON_CMD) $(USER_STACK_CHECKER)",
        ):
            require(USER_MAKE, stack_contract,
                    "actual RISC-V frame/call-path checker is not wired")

    def test_rx_decision_snapshot_isolated_from_the_next_host_frame(self) -> None:
        pump = function_body(GUEST, "live_rx_pump")
        take = function_body(GUEST, "live_rx_take")
        relay = function_body(GUEST, "live_relay_loop_v2")
        require(GUEST, "union live_rx_frame_storage",
                "RX parse scratch no longer overlays the bounded frame buffer")
        require(GUEST, "live_rx_decision_workspace",
                "RX pump has no private decision scratch")
        require(pump, "&live_rx_decision_workspace",
                "RX parser writes the Relay main decision directly")
        require_order(
            take,
            (
                "*decision = live_rx_decision_workspace",
                "live_rx_mailbox.state = LIVE_RX_EMPTY",
                "mutex_unlock(live_rx_mutex)",
            ),
            "RX may overwrite a decision before the Relay snapshots it",
        )
        require(relay, "&live_decision_workspace",
                "Relay main lacks a stable exact-decision snapshot")

    def test_failed_tool_result_never_claims_success_provenance(self) -> None:
        runtime = function_body(GUEST, "live_result_runtime")
        require(runtime,
                "wire->status == AGENT_STATUS_OK && wire->provenance_labels == 0",
                "failed tool feedback receives fabricated success provenance")
        require_order(
            runtime,
            (
                "wire->status == AGENT_STATUS_OK",
                "wire->provenance_labels = AGENT_PROVENANCE_AGENT_DERIVED",
            ),
            "success provenance is not guarded by the result status",
        )

    def test_stale_handle_uses_general_exact_rejection_path(self) -> None:
        relay = function_body(GUEST, "live_relay_loop_v2")
        rejected = function_body(GUEST, "live_history_append_rejected_call")
        require_order(
            relay,
            (
                'validation_error = "stale_report_handle"',
                "else if (decision.type == LIVE_DECISION_TOOL)",
                "live_history_append_rejected_call(",
                "&decision",
            ),
            "stale artifact rejection rewrites Host-bound model arguments",
        )
        require(rejected, "live_history_append(history, count, decision, result)",
                "semantic rejection does not retain the exact delivered decision")
        require(rejected, "AGENT_STATUS_BAD_PARAM",
                "semantic rejection is not returned as structured tool feedback")
        forbid(rejected, "normalized",
               "semantic rejection replaces the delivered assistant call")

    def test_provider_error_retryability_cannot_deadlock_sideband(self) -> None:
        compact = function_body(GUEST, "live_make_compact")
        relay = function_body(GUEST, "live_relay_loop_v2")
        execute = function_body(GUEST, "nexus_execute_open_decision")
        workflow = function_body(GUEST, "live_workflow_v2")
        root_terminal = function_body(GUEST, "nexus_root_terminal_summary")
        require(GUEST, "decision->retryable = retryable;", "MODEL_ERROR loses retryability")
        require(compact, '"nexus-E|N|"', "MODEL_ERROR incorrectly requests sideband")
        require(compact, '"provider_retryable"', "retryable error marker missing")
        require(compact, '"provider_fatal"', "fatal error marker missing")
        require(relay, "!decision.retryable", "fatal model errors do not terminate")
        require(relay, "previous_error_code", "retryable provider error is not replanned")
        require_order(
            execute,
            (
                '!strcmp(code + 1, "provider_fatal")',
                "nexus_root_terminal(",
                "AGENT_STATUS_IO_ERROR",
                "return 3",
                "return 0",
            ),
            "fatal and retryable provider errors share a nonterminal result",
        )
        require(workflow, "decision_status == 3",
                "Coordinator requests another round after a fatal provider error")
        require(root_terminal, 'status == AGENT_STATUS_CANCELLED ? "cancelled" : "failed"',
                "fatal provider error does not produce a failed root terminal")
        require_order(
            relay,
            (
                "live_wait_tool_result_cancelable(",
                "decision.type == LIVE_DECISION_ERROR &&",
                "turn_error = 1",
                "turn_done = 1",
                "live_v2_emit_turn_complete(",
            ),
            "TURN_COMPLETE can precede the fatal root terminal result",
        )
        require(relay, "for (;;) {",
                "fatal provider error closes the session instead of allowing a next turn")

    def test_history_eviction_preserves_current_tool_evidence_first(self) -> None:
        request = function_body(GUEST, "live_build_autonomous_request_v2")
        append = function_body(GUEST, "live_history_append")
        require(GUEST, "#define LIVE_HISTORY_TURNS 4U",
                "analysis cannot retain four reordered/repeated tool results")
        require(append, "if (*count == LIVE_HISTORY_TURNS)",
                "bounded history does not evict only when full")
        require_order(
            append,
            (
                "for (uint i = 1; i < LIVE_HISTORY_TURNS; i++)",
                "history[i - 1] = history[i]",
                "history[index].decision = *decision",
                "history[index].result.status = result->status",
                "result->model_projection",
            ),
            "history eviction does not preserve newest complete tool pairs",
        )
        require_order(
            request,
            (
                "for (uint first_history = 0",
                "live_builder_history_turn",
                "builder.length <= hello->max_payload",
            ),
            "current-turn history eviction is not bounded",
        )
        require(request, "*retained_out = history_count - first_history",
                "request does not report how many newest pairs fit")
        require(request, "*dropped_out = first_history",
                "request does not report oldest-pair eviction")
        require(request, "first_history++", "history cannot eventually be bounded")
        require(request,
                "first_history <= (history_count > 0 ? history_count - 1 : 0)",
                "the latest delivered and settled tool pair can be dropped")

    def test_json_escape_budget_matches_host_before_model_request(self) -> None:
        request = function_body(GUEST, "live_build_autonomous_request_v2")
        validator = function_body(GUEST, "live_validate_decision")
        escaped = function_body(GUEST, "live_json_string_content_bounded")
        require(request, "live_json_string_content_bounded(goal, hello->max_user_bytes)",
                "a 2048-byte control-heavy goal can overflow the first request")
        for bound in (
            "AGENT_NEXUS_SOURCE_QUERY_SIZE - 1",
            "AGENT_NEXUS_SOURCE_PREFIX_SIZE - 1",
            "live_json_string_content_bounded(first_text, 2800)",
            "live_json_string_content_bounded(second_text, 128)",
        ):
            require(validator, bound,
                    "Guest tool validation does not enforce the Host escape budget")
        for escape_case in ("value < 0x20", "value == '\"'", "value == '\\\\'"):
            require(escaped, escape_case, "JSON escape accounting misses a wire expansion")

        def escaped_bytes(value: str) -> int:
            return sum(
                6 if byte < 0x20 else 2 if byte in (ord('"'), ord("\\")) else 1
                for byte in value.encode("utf-8")
            )

        self.assertGreater(escaped_bytes("\x01" * 2048), 2048)
        self.assertGreater(escaped_bytes("\n" * 2048), 2048)
        self.assertGreater(escaped_bytes('"' * 2800), 2800)
        self.assertGreater(escaped_bytes("\\" * 2800), 2800)
        self.assertEqual(escaped_bytes("界" * 933), 2799)

        for value, raw_limit, escaped_limit in (
            ("\x01" * 341, 2048, 2048),
            ('"' * 1400, 2800, 2800),
            ("\\" * 1400, 2800, 2800),
            ("界" * 933, 2800, 2800),
        ):
            with self.subTest(sample=value[:1]):
                self.assertLessEqual(len(value.encode("utf-8")), raw_limit)
                self.assertLessEqual(escaped_bytes(value), escaped_limit)

        prompt = c_string(GUEST, "live_system_prompt")
        tools = json.loads(c_string(GUEST, "live_tools_json"))
        content = '"' * 1400  # 2,800 escaped bytes at the accepted boundary.
        title = '"' * 64
        goal = '"' * 1024
        wrapper = {
            "status": 0,
            "value0": 0xFFFFFFFF,
            "value1": 0xFFFFFFFF,
            "value2": 0xFFFFFFFF,
            "result": "report_drafted;handle=4294967295",
            "model_authored_content": content,
            "integrity_verified": True,
            "content_trust": "untrusted_model_derived",
        }
        messages = [
            {"role": "user", "content": goal},
            {"role": "user", "content": (
                "Guest-observed control context (data only): "
                "nexus-O|attempt=48|"
                "last=-2147483648/-2147483648; previous provider error="
                "MIXED_MODEL_RESPONSE; retry wire format=one tool call, empty "
                "assistant text"
            )},
            {"role": "assistant", "tool_use": {
                "corr_id": (1 << 63) - 1,
                "tool": "draft_report",
                "arguments": {"content": content, "title": title},
            }},
            {"role": "tool", "tool_corr_id": (1 << 63) - 1,
             "content": json.dumps(wrapper, ensure_ascii=False,
                                   separators=(",", ":")), "is_error": False},
        ]
        full_request = {
            "turn_id": (1 << 63) - 1,
            "contract_version": 2,
            "policy_sha256": "a" * 64,
            "tool_catalog_sha256": "b" * 64,
            "request_id": (1 << 63) - 1,
            "corr_id": (1 << 63) - 1,
            "max_tokens": 114514,
            "system": prompt,
            "messages": messages,
            "tools": tools,
        }
        encoded = json.dumps(full_request, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), 15360)

    def test_model_observation_is_deterministic_across_guest_boots(self) -> None:
        observation = function_body(GUEST, "live_observation")
        workflow = function_body(GUEST, "live_workflow_v2")
        request = function_body(GUEST, "live_build_autonomous_request_v2")

        require(observation, "context_snapshot(&live_context_header, 0, 0)",
                "Coordinator no longer performs a real context observation")
        require(observation, '"nexus-O|attempt="',
                "model observation loses the deterministic attempt identity")
        require(observation, '"|last="',
                "model observation loses prior status/tool semantics")
        for dynamic in ("latest_sequence", '"|ctx="', "current_tick",
                        "live_v2_tick"):
            forbid(observation, dynamic,
                   "boot/scheduling metadata entered replay-bound model input")
        require_order(
            observation,
            (
                "context_snapshot(&live_context_header, 0, 0)",
                '"nexus-O|attempt="',
                "live_builder_u64(&builder, attempt)",
                '"|last="',
                "live_builder_i64(&builder, last_status)",
                "live_builder_i64(&builder, last_tool_id)",
            ),
            "stable observation is not derived from attempt and last result",
        )
        require(workflow,
                "live_observation(attempts, last_status, last_tool_id, observation",
                "Coordinator does not bind the stable observation to real attempts")
        require(workflow, "context_roundtrips++",
                "real context observation telemetry was removed")
        require(request, "Guest-observed control context (data only): ",
                "stable observation is no longer sent through the model contract")

    def test_retry_guidance_is_error_specific_and_format_only(self) -> None:
        request = function_body(GUEST, "live_build_autonomous_request_v2")
        require_order(
            request,
            (
                '"; previous provider error="',
                '"MIXED_MODEL_RESPONSE"',
                '"MULTIPLE_TOOL_CALLS"',
                '"; retry wire format=one tool call"',
                '", empty assistant text"',
            ),
            "provider response-shape retries lack deterministic wire guidance",
        )
        require(request, "if (previous_host_error != 0)",
                "retry guidance can enter an ordinary request")
        forbid(c_string(GUEST, "live_system_prompt"), "retry wire format=",
               "transient response-shape guidance leaked into ordinary requests")

    def test_task_event_routing_is_complete_for_root_and_artifacts(self) -> None:
        root_start = function_body(GUEST, "nexus_root_start")
        root_terminal = function_body(GUEST, "nexus_root_terminal_summary")
        dispatch = function_body(GUEST, "nexus_dispatch_task")
        self.assertGreaterEqual(root_start.count(
            "source_pid = nexus_coordinator_identity.pid"), 3)
        self.assertGreaterEqual(root_start.count(
            "target_pid = nexus_coordinator_identity.pid"), 3)
        require(root_terminal, "source_pid = nexus_coordinator_identity.pid",
                "root terminal lacks Coordinator source routing")
        require(root_terminal, "target_pid = nexus_coordinator_identity.pid",
                "root terminal lacks Coordinator target routing")
        require_order(
            dispatch,
            (
                '"artifact_published", "completed"',
                "wire->source_pid = target_pid",
                "wire->target_pid = nexus_coordinator_identity.pid",
                "wire->artifact_handle",
            ),
            "artifact producer event is not routed worker to Coordinator",
        )

    def test_cross_turn_summary_is_disabled_until_transcript_is_authenticated(self) -> None:
        request = function_body(GUEST, "live_build_autonomous_request_v2")
        forbid(GUEST, "live_v2_store_summary",
               "unauthenticated assistant prose survives across turns")
        forbid(GUEST, "live_v2_summary",
               "cross-turn summary storage is still compiled")
        forbid(request, "first_summary", "request still accepts prior summaries")

    def test_stable_neutral_policy_and_catalog_are_digest_bound(self) -> None:
        prompt = c_string(GUEST, "live_system_prompt")
        tools = c_string(GUEST, "live_tools_json")
        policy_sha = hashlib.sha256(prompt.encode()).hexdigest()
        tools_sha = hashlib.sha256(tools.encode()).hexdigest()
        require(PROTOCOL, policy_sha, "policy digest constant drifted")
        require(PROTOCOL, tools_sha, "tool catalog digest constant drifted")
        request = function_body(GUEST, "live_build_autonomous_request_v2")
        for binding in ("contract_version", "policy_sha256", "tool_catalog_sha256"):
            require(request, binding, f"MODEL_REQUEST omits {binding}")
        require(GUEST, "live_autonomy_contract_valid()",
                "Guest does not verify its compiled contract bytes at boot")

    def test_source_tools_are_real_snapshot_reads_with_integrity(self) -> None:
        search = function_body(GUEST, "nexus_build_source_search_payload")
        read = function_body(GUEST, "nexus_build_source_read_payload")
        require(search, "agent_nexus_source_search(", "source_search is not real")
        require(read, "agent_nexus_source_read(", "source_read is not real")
        for evidence in (
            "build_source_snapshot",
            "content_untrusted=1",
            "manifest_sha256=",
            "full_sha256",
            "chunk_sha256",
            "citation",
        ):
            require(GUEST, evidence, "source provenance projection is incomplete")
        require(search, "emitted_matches != search.match_count",
                "bounded search does not disclose truncation")
        require(search, "body_builder.capacity", "search sizing is not capacity-aware")
        require(read, "status != AGENT_NEXUS_SOURCE_BAD_PARAM",
                "source corruption is retried as a buffer issue")
        require(GUEST, "agent_nexus_source_init() == AGENT_NEXUS_SOURCE_OK",
                "corpus is not verified before the model runs")
        require(SOURCE_API, "content_untrusted", "source API omits trust marking")
        require(SOURCE_LIB, "manifest_sha256", "source library omits manifest binding")

    def test_source_search_not_found_is_typed_and_reinjected_exactly(self) -> None:
        dispatch = function_body(GUEST, "nexus_dispatch_task")
        failure = dispatch.split("if (status != AGENT_STATUS_OK) {", 1)[1].split(
            "if (audit_failed) {", 1
        )[0]
        history_append = function_body(GUEST, "live_history_append")
        history_json = function_body(GUEST, "live_build_history_result_json")
        tool_event = function_body(GUEST, "live_v2_emit_tool_event")

        require_order(
            failure,
            (
                "task_type == AGENT_NEXUS_TASK_SOURCE_SEARCH",
                "status == AGENT_STATUS_NOT_FOUND",
                '"source_search_no_matches;replan_allowed=1"',
                '"task_failed;replan_allowed=1"',
                "NEXUS_DISPATCH_RETURN(status, 0)",
            ),
            "source_search NOT_FOUND is indistinguishable from an internal failure",
        )
        self.assertEqual(
            GUEST.count('"source_search_no_matches;replan_allowed=1"'),
            1,
            "typed no-match feedback escaped the narrow source_search branch",
        )
        require(
            history_append,
            "strcpy(history[index].result.result, result->result)",
            "model history does not retain the exact typed no-match result",
        )
        require(
            history_json,
            "live_builder_json_string(&result_builder, result)",
            "model history normalizes typed no-match feedback",
        )
        require(
            tool_event,
            "live_builder_json_string(&builder, result->result)",
            "public TOOL_EVENT normalizes typed no-match feedback",
        )

    def test_final_has_no_goal_keyword_or_semantic_citation_gate(self) -> None:
        execute = function_body(GUEST, "nexus_execute_open_decision")
        require(execute, "strcpy(final_answer, decision.final_text)",
                "provider-authored final is not preserved")
        for semantic_gate in (
            "nexus_goal_requires_source",
            "final_missing_verified_citation",
            "source_search_requires_source_read",
            "grounding_incomplete",
        ):
            forbid(GUEST, semantic_gate,
                   "product runtime still parses task/final semantics")
        prompt = c_string(GUEST, "live_system_prompt")
        require(prompt, "If you make a source-backed claim, cite an exact citation token",
                "neutral evidence instruction is missing")

    def test_source_evidence_is_observable_without_source_body(self) -> None:
        evidence = function_body(GUEST, "nexus_emit_source_evidence")
        emit = function_body(GUEST, "nexus_v2_emit_evidence_event")
        dispatch = function_body(GUEST, "nexus_dispatch_task")
        require(dispatch, "nexus_emit_source_evidence(",
                "verified source metadata is not sent to the live event stream")
        for field in (
            "build_source_snapshot",
            "corpus_revision",
            "manifest_sha256",
            "source_id",
            "path",
            "start_line",
            "end_line",
            "citation",
            "full_sha256",
            "chunk_sha256",
            "artifact_sha256",
            "projection_sha256",
        ):
            require(evidence + emit, field, f"source evidence event omits {field}")
        require(evidence, '"source_read"', "source metadata has no typed event")
        require(evidence, "live_digest_text(projection",
                "event is not bound to the exact model projection")
        require(evidence, "strlen(artifact_sha256) != LIVE_SHA_HEX_SIZE",
                "event does not validate the artifact payload digest")
        require(dispatch, "result->artifact_sha256",
                "source evidence is not bound to the read artifact payload")
        require(evidence, "LIVE_V2_RESULT_EVIDENCE", "evidence uses TASK_EVENT")
        require(emit, '"EVIDENCE_EVENT"', "dedicated frame is not streamed")
        forbid(emit, "source data", "source body leaks into observer telemetry")
        require(evidence, "nexus_projection_field", "read evidence is not structured")
        history = function_body(GUEST, "live_build_history_result_json")
        require(history, 'source_evidence', "next request cannot bind read evidence")
        require(history, 'discovery_projection', "search hint trust type is missing")
        require(history, 'runtime_observation', "runtime trust type is missing")
        require(history, 'model_authored_content', "model content trust type is missing")
        history_turn = function_body(GUEST, "live_builder_history_turn")
        require(history_turn, 'tool_corr_id', "history projection lacks source corr binding")
        tool_event = function_body(GUEST, "live_v2_emit_tool_event")
        require(tool_event, 'projection_sha256', "TOOL_EVENT lacks projection binding")
        require(tool_event, 'result_sha256', "TOOL_EVENT lacks history-result binding")

    def test_history_digest_uses_host_canonical_json_escapes(self) -> None:
        encoder = function_body(GUEST, "live_builder_json_string")
        for value, escape in (
            (r"value == '\b'", r'live_builder_text(builder, "\\b")'),
            (r"value == '\t'", r'live_builder_text(builder, "\\t")'),
            (r"value == '\n'", r'live_builder_text(builder, "\\n")'),
            (r"value == '\f'", r'live_builder_text(builder, "\\f")'),
            (r"value == '\r'", r'live_builder_text(builder, "\\r")'),
        ):
            require(encoder, value, "canonical JSON control branch is missing")
            require(encoder, escape, "canonical JSON short escape is missing")
        require_order(
            encoder,
            (r"value == '\n'", "value < 0x20", r'"\\u00"'),
            "canonical newline must not use the generic Unicode escape",
        )

    def test_specialists_execute_general_tasks_and_emit_task_events(self) -> None:
        specialist = function_body(GUEST, "nexus_specialist_loop")
        dispatch = function_body(GUEST, "nexus_dispatch_task")
        for task in (
            "AGENT_NEXUS_TASK_INSPECT_RUNTIME",
            "AGENT_NEXUS_TASK_INSPECT_PROCESSES",
            "AGENT_NEXUS_TASK_INSPECT_CONTEXT",
            "AGENT_NEXUS_TASK_SOURCE_SEARCH",
            "AGENT_NEXUS_TASK_SOURCE_READ",
            "AGENT_NEXUS_TASK_DRAFT_REPORT",
        ):
            require(PROTOCOL, task, f"task ABI does not define {task}")
            require(NEXUS_LIB, task, f"task ABI rejects {task}")
        for task in (
            "AGENT_NEXUS_TASK_SOURCE_SEARCH",
            "AGENT_NEXUS_TASK_SOURCE_READ",
            "AGENT_NEXUS_TASK_DRAFT_REPORT",
        ):
            require(specialist, task, f"specialist cannot execute {task}")
        require(specialist, "nexus_system_operation_name(task.status)",
                "System does not execute the typed operation task")
        require(dispatch, "nexus_system_operation_name(task_type)",
                "Coordinator does not route typed System operations")
        for event in ("assigned", "accepted", "progress", "completed", "cancelled"):
            require(dispatch, f'"{event}"', f"TASK_EVENT {event} is missing")
        require(dispatch, "agent_nexus_task_transition_validate",
                "specialist replies are not state-machine validated")
        require(dispatch, "assigned.deadline_tick", "worker deadline is missing")

    def test_task_artifacts_bind_task_parent_and_producer_identity(self) -> None:
        specialist = function_body(GUEST, "nexus_specialist_loop")
        dispatch = function_body(GUEST, "nexus_dispatch_task")
        for binding in (
            "capsule_header.task_id != task_id",
            "capsule_header.parent_task_id != task.parent_task_id",
            "capsule_header.producer",
            "nexus_coordinator_identity",
        ):
            require(specialist, binding,
                    "specialist capsule is not bound to its TASK message")
        for binding in (
            "artifact.task_id != task_id",
            "artifact.parent_task_id != root_task",
            "nexus_actor_matches_identity(&artifact.producer, target)",
        ):
            require(dispatch, binding,
                    "Coordinator accepts a result from another task or producer")

    def test_system_and_research_results_are_coordinator_brokered(self) -> None:
        system = function_body(GUEST, "nexus_open_system_task")
        search = function_body(GUEST, "nexus_open_source_search_task")
        read = function_body(GUEST, "nexus_open_source_read_task")
        report = function_body(GUEST, "nexus_open_report_task")
        materialize = function_body(GUEST, "nexus_materialize_brokered")
        replay = function_body(GUEST, "nexus_replay_and_materialize_worker_result")
        dispatch = function_body(GUEST, "nexus_dispatch_task")
        owned = function_body(NEXUS_LIB, "nexus_owned_manifest_valid")
        brokered = function_body(NEXUS_LIB, "nexus_brokered_manifest_valid")
        capabilities = function_body(NEXUS_LIB, "agent_nexus_product_capabilities")

        for worker in (system, search, read):
            require(worker, "nexus_worker_result_progress(",
                    "read-only specialist does not bind its computed payload")
            forbid(worker, "nexus_publish_specialist_result(",
                   "System or Research still requires ARTIFACT_WRITE")
            forbid(worker, "nexus_publish_owned(",
                   "System or Research directly writes an artifact")
        require(report, "nexus_publish_specialist_result(",
                "Analyst lost its owned report materialization")
        for contract in (
            "NEXUS_ARTIFACT_THREAD_MATERIALIZE_BROKERED",
            "AGENT_NEXUS_ARTIFACT_F_BROKERED",
            "manifest->producer",
            "nexus_coordinator_identity, &manifest->materializer",
            "manifest->owner = manifest->materializer",
            "AGENT_NEXUS_ARTIFACT_READ_COORDINATOR",
        ):
            require(materialize, contract,
                    "Coordinator broker manifest is not identity bound")
        require(replay, "nexus_build_source_search_payload(",
                "Coordinator does not replay source_search")
        require(replay, "nexus_build_source_read_payload(",
                "Coordinator does not replay source_read")
        require(replay, "nexus_build_system_payload(",
                "Coordinator does not reconstruct System output")
        require(replay, "nexus_materialize_brokered(",
                "verified payload is not broker materialized")
        for relationship in (
            "artifact.flags != (AGENT_NEXUS_ARTIFACT_F_BROKERED",
            "&artifact.owner, &nexus_coordinator_identity",
            "&artifact.materializer, &nexus_coordinator_identity",
        ):
            require(dispatch, relationship,
                    "Coordinator does not verify broker ownership")
        for role in ("AGENT_NEXUS_ROLE_SYSTEM", "AGENT_NEXUS_ROLE_RESEARCH"):
            forbid(owned, role, "read-only worker retained an owned manifest path")
            require(brokered, role, "broker validator rejects a read-only producer")
        system_caps = capabilities.split("case AGENT_NEXUS_ROLE_SYSTEM:", 1)[1].split(
            "case AGENT_NEXUS_ROLE_RESEARCH:", 1
        )[0]
        research_caps = capabilities.split("case AGENT_NEXUS_ROLE_RESEARCH:", 1)[1].split(
            "case AGENT_NEXUS_ROLE_ANALYST:", 1
        )[0]
        for caps in (system_caps, research_caps):
            forbid(caps, "AGENT_CAP_ARTIFACT_WRITE",
                   "read-only worker gained ARTIFACT_WRITE")
        forbid(system_caps, "AGENT_CAP_CONTENT_READ",
               "System regained a content-read capsule path")
        kernel_policy = (ROOT / "os/agent_identity.c").read_text(encoding="utf-8")
        sentinel_policy = kernel_policy.split("{ AGENT_ROLE_SENTINEL,", 1)[1].split(
            "{ AGENT_ROLE_INVESTIGATOR,", 1
        )[0]
        require(sentinel_policy, "AGENT_CAP_PROCESS_READ",
                "kernel Sentinel cannot inspect runtime process state")
        for forbidden_capability in (
            "AGENT_CAP_CONTENT_READ",
            "AGENT_CAP_ARTIFACT_WRITE",
        ):
            forbid(sentinel_policy, forbidden_capability,
                   "kernel Sentinel privilege silently widened")

    def test_worker_result_binding_is_exact_unique_and_full_digest(self) -> None:
        emit = function_body(GUEST, "nexus_worker_result_progress")
        accept = function_body(GUEST, "nexus_accept_worker_result_metric")
        replay = function_body(GUEST, "nexus_replay_and_materialize_worker_result")
        dispatch = function_body(GUEST, "nexus_dispatch_task")

        require(emit, "LIVE_SHA_SIZE / 4", "worker sends less than full SHA-256")
        require(emit, "NEXUS_METRIC_RESULT_DIGEST0 + i",
                "digest words are not typed")
        require(emit, "NEXUS_SYSTEM_RESULT_FIELD_COUNT",
                "System value words are not fully bound")
        require(emit, "NEXUS_RESEARCH_RESULT_FIELD_COUNT",
                "Research digest field set is not explicit")
        require(accept, "binding->seen_mask & bit",
                "duplicate result metrics are accepted")
        require(accept, "binding->invalid = 1",
                "malformed result binding does not fail closed")
        require(replay, "binding->seen_mask != expected_mask",
                "missing or extra result metrics are accepted")
        require(replay, "payload_size != binding->payload_size",
                "worker payload length is not compared")
        require(replay,
                "live_bytes_equal(digest, binding->payload_sha256, sizeof(digest))",
                "Coordinator does not compare the full payload digest")
        require(dispatch, "nexus_accept_worker_result_metric(",
                "Coordinator ignores typed worker bindings")
        require_order(
            dispatch,
            (
                "metric_code >= NEXUS_RESULT_METRIC_FIRST",
                "if (inline_value != 0)",
                "worker_result.invalid = 1",
                "nexus_accept_worker_result_metric(",
            ),
            "result metrics ignore non-canonical high bits",
        )
        forged_value0 = 147 | (1 << 16)
        self.assertNotEqual(forged_value0 >> 16, 0,
                            "high-bit mutation did not exercise canonicality")
        require_order(
            dispatch,
            (
                "if (status != AGENT_STATUS_OK)",
                "nexus_replay_and_materialize_worker_result(",
            ),
            "a failed worker result is replayed or loses its business status",
        )

    def test_worker_result_is_provisional_until_artifact_verification(self) -> None:
        dispatch = function_body(GUEST, "nexus_dispatch_task")
        provisional = dispatch.find("A worker RESULT is provisional")
        verification = dispatch.find(
            "nexus_replay_and_materialize_worker_result(", provisional
        )

        self.assertGreaterEqual(provisional, 0)
        self.assertGreater(verification, provisional)
        self.assertNotIn(
            '"completed"',
            dispatch[provisional:verification],
            "worker RESULT is exposed before Coordinator verification",
        )
        require_order(
            dispatch,
            (
                "A worker RESULT is provisional",
                "nexus_replay_and_materialize_worker_result(",
                "nexus_read_artifact(result_handle, expected_kind",
                "if (verification_status != AGENT_STATUS_OK)",
                '"result_verification_failed"',
                "nexus_emit_source_evidence(",
                'corr_id, task_id, root_task, "completed", "completed"',
                '"publish verified completion TASK_EVENT"',
                '"artifact_published", "completed"',
            ),
            "child terminal is visible before payload settlement",
        )
        require(dispatch, '"result_verification_failed"',
                "verification failure has no bounded Task diagnosis")
        self.assertEqual(
            dispatch.count('"publish verified completion TASK_EVENT"'), 1,
            "verified success publishes more than one child completion",
        )

    def test_post_assigned_failures_settle_one_child_before_tool_error(self) -> None:
        dispatch = function_body(GUEST, "nexus_dispatch_task")

        for marker in (
            '"audit_verification_failed"',
            '"result_verification_failed"',
            '"result_payload_bounds_failed"',
            '"prior_report_cleanup_failed"',
            '"result_hint_bounds_failed"',
            '"model_projection_bounds_failed"',
            '"source_evidence_publish_failed"',
        ):
            marker_at = dispatch.find(marker)
            terminal_at = dispatch.rfind(
                "nexus_publish_worker_terminal(", 0, marker_at
            )
            returned_at = dispatch.find("NEXUS_DISPATCH_RETURN(", marker_at)
            self.assertGreaterEqual(marker_at, 0, f"missing failure branch {marker}")
            self.assertGreaterEqual(terminal_at, 0,
                                    f"{marker} leaves the child nonterminal")
            self.assertGreater(returned_at, marker_at,
                               f"{marker} settles TOOL before child failure")
        require_order(
            dispatch,
            (
                "if (cancel_ack)",
                "user_cancel_requested ? AGENT_STATUS_CANCELLED",
                "deadline_cancel ?",
                '"task_cancelled;reason=user_interrupt;terminal_ack=1"',
                "deadline_cancel ?",
                '"task_failed;reason=deadline;replan_allowed=1"',
                '"task_cancelled;reason=internal_audit;replan_allowed=1"',
                "NEXUS_DISPATCH_RETURN(result->status, 0)",
                "if (status != AGENT_STATUS_OK)",
                "NEXUS_DISPATCH_RETURN(status, 0)",
                "if (audit_failed)",
            ),
            "cancelled/failed worker terminal is overwritten by audit failure",
        )
        cancel_block = dispatch.split("if (cancel_ack)", 1)[1].split(
            "if (status != AGENT_STATUS_OK)", 1
        )[0]
        forbid(cancel_block, "nexus_publish_worker_terminal(",
               "cancel acknowledgement publishes a duplicate child terminal")
        require(dispatch, "int user_cancel_requested = 0",
                "internal quiesce is not distinguished from a Host cancel")
        require(dispatch, "user_cancel_requested = 1",
                "authenticated Host cancel does not record its cause")
        require_order(
            dispatch,
            (
                "nexus_return_status == AGENT_STATUS_CANCELLED &&",
                "user_cancel_requested",
                "LIVE_RESULT_F_CANCEL_DERIVED",
            ),
            "internal cleanup failure is suppressed as a Host-derived cancel",
        )
        status_branch = dispatch.split("if (cancel_ack)", 1)[1].split(
            "if (status != AGENT_STATUS_OK)", 1
        )[0]
        self.assertLess(
            status_branch.find("user_cancel_requested ? AGENT_STATUS_CANCELLED"),
            status_branch.find("deadline_cancel ? AGENT_STATUS_TIMEOUT"),
            "deadline wins a simultaneous authenticated Host cancellation",
        )
        require_order(
            dispatch,
            (
                "nexus_emit_source_evidence(",
                'corr_id, task_id, root_task, "completed", "completed"',
                '"artifact_published", "completed"',
            ),
            "source evidence is staged after the successful child terminal",
        )
        forbid(dispatch, "live_check(nexus_emit_source_evidence(",
               "source evidence failure still crashes the Guest")

    def test_system_uses_authenticated_inline_metadata_not_a_capsule_read(self) -> None:
        specialist = function_body(GUEST, "nexus_specialist_loop")
        dispatch = function_body(GUEST, "nexus_dispatch_task")
        operation = function_body(GUEST, "nexus_system_operation_id")
        system_branch = specialist.find("if (role == AGENT_ROLE_SENTINEL)")
        capsule_branch = specialist.find("nexus_read_artifact_for_role(", system_branch)

        self.assertGreaterEqual(system_branch, 0)
        self.assertGreater(capsule_branch, system_branch)
        for exact, task_type in (
            ('"system_status"', "AGENT_NEXUS_TASK_INSPECT_RUNTIME"),
            ('"processes"', "AGENT_NEXUS_TASK_INSPECT_PROCESSES"),
            ('"context"', "AGENT_NEXUS_TASK_INSPECT_CONTEXT"),
        ):
            require_order(operation, (exact, f"return {task_type}"),
                          "model runtime enum is not mapped exactly")
        require(specialist,
                "task.flags == (AGENT_NEXUS_TASK_F_HAS_INPUT |",
                "System inline control binding is not typed")
        require(specialist, "nexus_system_operation_name(task.status)",
                "System does not validate the inline operation enum")
        require(specialist, "uint64 control_id = task.value0 |",
                "System does not reconstruct the full control id")
        require(specialist, "((uint64)task.value1 << 32)",
                "System truncates the high control-id half")
        require(specialist, "agent_nexus_identity_bind_control(control_id)",
                "System inline task is not bound to its kernel control identity")
        require(dispatch, "(uint)target->control_id : capsule_handle",
                "Coordinator does not send the low System control-id half")
        require(dispatch, "(uint)(target->control_id >> 32) : 0",
                "Coordinator does not send the high System control-id half")
        require(dispatch, "assigned.status = task_type",
                "Coordinator does not carry the typed System operation")
        require(dispatch, "nexus_system_operation_id(objective) != task_type",
                "Coordinator does not bind the model operation to the task type")
        require_order(
            specialist,
            (
                "if (role == AGENT_ROLE_SENTINEL)",
                "else if ((task.flags & AGENT_NEXUS_TASK_F_HAS_INPUT) == 0",
                "nexus_read_artifact_for_role(",
            ),
            "System still enters the CONTENT_READ capsule path",
        )

    def test_system_inline_control_u64_boundaries_and_accept_order(self) -> None:
        validator = function_body(NEXUS_LIB, "agent_nexus_task_validate")
        specialist = function_body(GUEST, "nexus_specialist_loop")

        require(validator, "nexus_system_task_type(task->status)",
                "System ASSIGN lacks a dedicated typed validator")
        require(validator,
                "task->flags != (AGENT_NEXUS_TASK_F_HAS_INPUT |",
                "System ASSIGN does not require both typed halves")
        require(validator, "(task->value0 == 0 && task->value1 == 0)",
                "System accepts a zero control id")
        for control_id in (1, 0xFFFFFFFF, 0x100000000, 0xFFFFFFFFFFFFFFFF):
            low = control_id & 0xFFFFFFFF
            high = (control_id >> 32) & 0xFFFFFFFF
            self.assertNotEqual((low, high), (0, 0))
            self.assertEqual(low | (high << 32), control_id)
        system_branch = specialist.find("if (role == AGENT_ROLE_SENTINEL)")
        binding = specialist.find("agent_nexus_identity_bind_control(control_id)", system_branch)
        accepted = specialist.find('"specialist validated TASK_ACCEPT"', binding)
        self.assertGreaterEqual(system_branch, 0)
        self.assertGreater(binding, system_branch)
        self.assertGreater(accepted, binding,
                           "System authenticates malformed inline metadata after ACCEPT")

    def test_worker_telemetry_is_wide_and_cannot_override_tool_status(self) -> None:
        snapshot = function_body(GUEST, "nexus_worker_snapshot_progress")
        specialist = function_body(GUEST, "nexus_specialist_loop")
        dispatch = function_body(GUEST, "nexus_dispatch_task")
        audit = function_body(GUEST, "nexus_audit_drain")
        workflow = function_body(GUEST, "live_workflow_v2")

        require(snapshot, "values[7] = snapshot.sched_vruntime",
                "scheduler vruntime is omitted")
        require(snapshot, "values[i] > 0xffffffffffffULL",
                "snapshot fields are still constrained to 16 bits")
        require(snapshot, "(uint)(values[i] >> 32) << 16",
                "snapshot high bits are not transmitted")
        forbid(snapshot, "snapshot.sched_vruntime > 0xffffULL",
               "absolute vruntime permanently overflows telemetry")
        require(specialist, "(void)nexus_worker_snapshot_progress(",
                "telemetry failure still participates in business status")
        require_order(
            specialist,
            (
                "status = nexus_open_source_read_task(",
                "(void)nexus_worker_snapshot_progress(",
                "status == AGENT_STATUS_OK ? AGENT_NEXUS_TASK_RESULT",
            ),
            "telemetry can replace the actual specialist status",
        )
        require(dispatch, "NEXUS_SNAPSHOT_METRIC_FIRST",
                "Coordinator cannot reconstruct wide snapshot fields")
        require(dispatch, "(uint64)inline_value << 32",
                "Coordinator truncates wide snapshot fields")
        require(dispatch, "(void)nexus_publish_kernel_telemetry(&worker_snapshot)",
                "worker snapshot publication can terminate business execution")
        require(audit, "(void)nexus_publish_kernel_telemetry(&projected)",
                "audit telemetry publication can become an audit failure")
        require(workflow, "(void)nexus_emit_self_snapshot(",
                "Coordinator telemetry publication can terminate a turn")
        forbid(dispatch, "live_check(nexus_publish_kernel_telemetry",
               "telemetry write failure still kills the Coordinator")
        require_order(
            dispatch,
            (
                "(void)nexus_publish_kernel_telemetry(&worker_snapshot)",
                "if (status != AGENT_STATUS_OK)",
                "NEXUS_DISPATCH_RETURN(status, 0)",
                "nexus_replay_and_materialize_worker_result(",
                'corr_id, task_id, root_task, "completed", "completed"',
                '"artifact_published", "completed"',
            ),
            "telemetry failure can replace NOT_FOUND or suppress a verified artifact",
        )
        status_api = (ROOT / "include/agent_tool_abi.h").read_text(encoding="utf-8")
        require(status_api, "#define AGENT_STATUS_NOT_FOUND    -5",
                "NOT_FOUND wire status drifted")

    def test_runtime_operation_is_model_selected_and_scope_honest(self) -> None:
        tools = {tool["name"]: tool for tool in json.loads(c_string(GUEST, "live_tools_json"))}
        operation = tools["inspect_runtime"]["input_schema"]["properties"]["operation"]
        self.assertEqual(operation["enum"], ["system_status", "processes", "context"])
        self.assertEqual(
            set(tools["inspect_runtime"]["input_schema"]["properties"]),
            {"operation"},
            "runtime schema advertises an ignored selector",
        )
        runtime = (
            function_body(GUEST, "nexus_system_operation_id")
            + function_body(GUEST, "nexus_build_system_payload")
            + function_body(GUEST, "nexus_open_system_task")
        )
        for operation_name in operation["enum"]:
            require(runtime, f'"{operation_name}"', "runtime operation is ignored")
        require(runtime, "scope=this_boot_guest_runtime", "runtime scope is misleading")
        forbid(runtime, "build_source_snapshot", "runtime and source scope are conflated")

    def test_draft_report_is_model_authored_exact_content(self) -> None:
        report = function_body(GUEST, "nexus_open_report_task")
        require(report, "capsule->objective", "report does not use model content")
        require(report, "capsule->objective_length", "report size is not exact")
        forbid(report, "live_builder_text", "Analyst worker adds canned report text")
        require(PROTOCOL, "char objective[2801]", "report capsule is too small")
        require(PROTOCOL, "AGENT_NEXUS_ARTIFACT_MAX     3072U",
                "report artifact budget drifted")

    def test_unattested_publication_and_approval_are_not_nexus_capabilities(self) -> None:
        for forbidden in (
            '"publish_report"',
            '"APPROVAL_REQUEST"',
            '"APPROVAL_RESULT"',
            "live_consume_approval",
            "live_v2_emit_approval_request",
            "nexus_publish_report_effect",
            "NEXUS_PUBLISH",
        ):
            forbid(GUEST, forbidden,
                   "Nexus exposes an externally unverifiable publication path")
        prompt = c_string(GUEST, "live_system_prompt")
        require(prompt, "neither tool publishes or performs an external effect",
                "report tools are not described as effect-free")

    def test_cancel_round_limit_controls_and_protocol_v2_survive(self) -> None:
        require(GUEST, '#define LIVE_PREFIX_V2 "@AGENTOS/2 "', "V2 framing vanished")
        relay = function_body(GUEST, "live_relay_loop_v2")
        workflow = function_body(GUEST, "live_workflow_v2")
        for command in ("tools", "context", "status", "reset", "agents", "tasks", "artifacts"):
            require(GUEST, f'"{command}"', f"/{command} control vanished")
        require(relay, '"nexus-C|user_interrupt"', "cancel does not wake Coordinator")
        require(relay, "LIVE_ROUND_ACK_LIMIT", "round limit is not terminal")
        require(workflow, '"round_limit"', "Coordinator loses the round-limit cause")
        require(workflow, "command.max_rounds <= LIVE_MAX_ROUNDS", "round bound is unchecked")
        require(GUEST, "nexus_shutdown_specialists", "session close leaks specialists")

    def test_tool_execution_cancel_has_one_rx_reader_and_worker_quiescence(self) -> None:
        relay = function_body(GUEST, "live_wait_tool_result_cancelable")
        relay_loop = function_body(GUEST, "live_relay_loop_v2")
        rx = function_body(GUEST, "live_rx_pump")
        cancel = function_body(GUEST, "nexus_cancel_pump")
        dispatch = function_body(GUEST, "nexus_dispatch_task")
        execute = function_body(GUEST, "nexus_execute_open_decision")
        workflow = function_body(GUEST, "live_workflow_v2")
        self.assertEqual(GUEST.count("live_read_line(0,"), 2,
                         "serial input gained another physical reader")
        require(rx, "live_v2_read_frame", "RX pump is not sole framed reader")
        require(relay, "live_rx_take", "tool wait cannot observe Host CANCEL")
        require(relay, "live_send_cancel", "Relay does not forward tool CANCEL")
        require(cancel, "agent_wait_cancel(nexus_cancel_active_pid",
                "Coordinator cannot interrupt the active specialist wait")
        require(dispatch, "cancel_not_quiescent;session_blocked=1",
                "unacknowledged cancellation can reuse/cleanup a live namespace")
        require(dispatch, "cancel_ack", "cleanup does not require terminal CANCEL ack")
        require(dispatch, "nexus_next_artifact_slot++",
                "late writers can alias a new task namespace")
        self.assertEqual(dispatch.count('received.kind == AGENT_NEXUS_TASK_CANCEL'), 3,
                         "cancel path has a second terminal event branch")
        require_order(
            dispatch,
            (
                '"publish assigned TASK_EVENT"',
                "if (nexus_audit_drain() < 0)",
                "while (!terminal)",
            ),
            "post-assignment audit failure can abandon a live worker",
        )
        require(dispatch, "observed > 384",
                "observation exhaustion does not force cancellation")
        require(dispatch, "worker_not_quiescent;session_blocked=1",
                "unquiescent worker has no terminal indeterminate event")
        require_order(
            dispatch,
            (
                '"publish indeterminate terminal TASK_EVENT"',
                "nexus_artifact_cleanup_failed = 1",
                "return AGENT_STATUS_IO_ERROR",
            ),
            "unquiescent namespace is cleaned or returned before terminal telemetry",
        )
        require_order(
            execute,
            (
                '"task_cancelled;reason=user_interrupt;terminal_ack=1"',
                "nexus_root_terminal_after_cleanup(",
                "AGENT_STATUS_CANCELLED",
                "LIVE_RESULT_F_CANCEL_DERIVED",
                "return 3",
            ),
            "active-worker cancellation bypasses the cleanup barrier",
        )
        require_order(
            dispatch,
            (
                "nexus_return_status == AGENT_STATUS_CANCELLED",
                "LIVE_RESULT_F_CANCEL_DERIVED",
            ),
            "transient cleanup failure erases the worker cancellation settlement",
        )
        require(execute, "tool_result->internal_flags = internal_flags",
                "turn cleanup failure erases the cancellation-derived flag")
        require_order(
            relay_loop,
            (
                "LIVE_RESULT_F_CANCEL_DERIVED",
                "live_v2_emit_tool_event(",
            ),
            "cancel-derived cleanup failure can duplicate the task-ledger settlement",
        )
        require(workflow, "decision_status == 2",
                "Coordinator requests another model round after worker cancellation")
        require_order(
            relay_loop,
            (
                "live_wait_tool_result_cancelable(",
                "turn_cancelled = 1",
                "live_v2_emit_turn_complete(",
            ),
            "TURN_COMPLETE can precede child/root cancellation events",
        )

    def test_completed_direct_tool_cancel_settles_then_cancels_root(self) -> None:
        relay = function_body(GUEST, "live_relay_loop_v2")
        ack = function_body(GUEST, "live_send_round_ack")
        workflow = function_body(GUEST, "live_workflow_v2")
        require(GUEST, "#define LIVE_ROUND_ACK_MAGIC",
                "completed direct tools have no cancellation handshake")
        for binding in ("turn_id", "request_id", "corr_id"):
            require(ack, f"ack.{binding} = {binding}",
                    "round acknowledgement is not exact-model-call bound")
        require_order(
            relay,
            (
                "live_v2_emit_tool_event(",
                "live_send_round_ack(",
                "live_v2_read_tool_result(",
                "live_result_session_blocked(&tool_result)",
                "AGENT_STATUS_CANCELLED",
                "turn_cancelled = 1",
            ),
            "a completed read_artifact can be cancelled before its TOOL settlement",
        )
        require_order(
            workflow,
            (
                '"interactive structured result reinjection"',
                "round_ack.magic == LIVE_ROUND_ACK_MAGIC",
                "round_ack.action == LIVE_ROUND_ACK_CANCEL",
                "nexus_root_terminal_after_cleanup(",
                '"post-result root terminal acknowledgement"',
            ),
            "Coordinator cannot acknowledge cancellation after a direct result",
        )
        require(relay, "A terminal result wins a simultaneous late cancel",
                "direct final and cancellation have no deterministic winner")

    def test_session_block_is_immediately_terminal_and_close_only(self) -> None:
        execute = function_body(GUEST, "nexus_execute_open_decision")
        relay = function_body(GUEST, "live_relay_loop_v2")
        blocked = function_body(GUEST, "live_result_session_blocked")
        workflow = function_body(GUEST, "live_workflow_v2")
        require_order(
            execute,
            (
                "status == AGENT_STATUS_IO_ERROR && nexus_artifact_cleanup_failed",
                "nexus_root_terminal(",
                "AGENT_STATUS_IO_ERROR",
                "return 3",
            ),
            "indeterminate cleanup failure permits another model round",
        )
        for marker in (
            "cancel_not_quiescent;session_blocked=1",
            "artifact_cleanup_failed;session_blocked=1",
        ):
            require(blocked, marker, "Relay misses a blocked-session result")
        require_order(
            relay,
            (
                "live_result_session_blocked(&tool_result)",
                "turn_error = 1",
                "turn_done = 1",
                "close_after_turn = 1",
                "live_v2_emit_turn_complete(",
                "live_v2_finish_session(",
            ),
            "blocked session can replan, reset, or emit a final answer",
        )
        require(workflow, "blocked Nexus session only accepts close",
                "Coordinator accepts commands after a fail-closed terminal")
        forbid(workflow, "live_check(!nexus_artifact_cleanup_failed",
               "Coordinator dies before the Relay close handshake")

    def test_terminal_cleanup_is_a_precommit_barrier_for_every_outcome(self) -> None:
        clear = function_body(GUEST, "nexus_clear_work_identity")
        terminal = function_body(GUEST, "nexus_root_terminal_after_cleanup")
        execute = function_body(GUEST, "nexus_execute_open_decision")
        workflow = function_body(GUEST, "live_workflow_v2")
        relay = function_body(GUEST, "live_relay_loop_v2")

        def validate_barrier(body: str) -> None:
            require_order(
                body,
                (
                    "nexus_clear_work_identity()",
                    "cleanup_status < 0 || nexus_artifact_cleanup_failed",
                    '"artifact_cleanup_failed;session_blocked=1"',
                    "nexus_root_terminal_summary(",
                    "return -1",
                    "nexus_root_terminal(",
                ),
                "cleanup is not resolved before the unique root terminal",
            )

        validate_barrier(terminal)
        for removed in (
            "nexus_clear_work_identity()",
            "cleanup_status < 0 || nexus_artifact_cleanup_failed",
            '"artifact_cleanup_failed;session_blocked=1"',
            "nexus_root_terminal_summary(",
        ):
            with self.assertRaises(ContractError):
                validate_barrier(terminal.replace(removed, "", 1))
        require_order(
            clear,
            (
                "nexus_remove_ephemeral_artifact(nexus_report_handle)",
                "nexus_artifact_cleanup_failed = 1",
                "return status",
            ),
            "report unlink failure is not propagated to the barrier",
        )
        for terminal_path in (
            '"nexus-C|"',
            '"provider_fatal"',
            "decision.type == LIVE_DECISION_FINAL",
            '"task_cancelled;reason=user_interrupt;terminal_ack=1"',
        ):
            start = execute.find(terminal_path)
            self.assertGreaterEqual(start, 0, f"missing terminal path {terminal_path}")
            require(execute[start:], "nexus_root_terminal_after_cleanup(",
                    f"terminal path {terminal_path} bypasses cleanup")
        require_order(
            execute,
            (
                "decision.type == LIVE_DECISION_FINAL",
                "nexus_root_terminal_after_cleanup(",
                "strcpy(final_answer, decision.final_text)",
            ),
            "final content can escape before cleanup succeeds",
        )
        require_order(
            workflow,
            (
                "round_ack.action != LIVE_ROUND_ACK_CONTINUE",
                "nexus_root_terminal_after_cleanup(",
                '"post-result root terminal acknowledgement"',
            ),
            "cancel/limit terminal acknowledgement bypasses cleanup",
        )
        require(relay, "live_result_session_blocked(&tool_result)",
                "Relay cannot observe a terminal cleanup failure")
        require_order(
            relay,
            (
                "live_result_session_blocked(&tool_result)",
                "turn_error = 1",
                "close_after_turn = 1",
                "live_v2_emit_turn_complete(",
                "live_v2_finish_session(",
            ),
            "cleanup failure can publish a completed final or accept another turn",
        )

    def test_round_ack_is_typed_bound_and_limit_wins_late_cancel(self) -> None:
        relay = function_body(GUEST, "live_relay_loop_v2")
        sender = function_body(GUEST, "live_send_round_ack")
        workflow = function_body(GUEST, "live_workflow_v2")
        for action in (
            "LIVE_ROUND_ACK_CONTINUE",
            "LIVE_ROUND_ACK_CANCEL",
            "LIVE_ROUND_ACK_LIMIT",
        ):
            require(relay + workflow, action, f"missing typed ACK action {action}")
        require(sender, "ack.action = action", "ACK still encodes cancellation as a boolean")
        forbid(relay, "corr_id, cancel_status > 0",
               "Relay still sends the old 0/1 ACK values")
        require_order(
            relay,
            (
                "decision_rounds == hello->max_rounds",
                "retryable_errors == hello->max_retries",
                "LIVE_ROUND_ACK_LIMIT",
                "cancel_status > 0 ? LIVE_ROUND_ACK_CANCEL",
            ),
            "a simultaneous exhausted-budget CANCEL can arm two terminal causes",
        )
        require(workflow, "round_ack.action == LIVE_ROUND_ACK_LIMIT",
                "Coordinator does not validate the bound limit action")
        require(workflow, "decision_rounds == command.max_rounds",
                "Coordinator accepts LIMIT below the decision cap")
        require(workflow, "retryable_errors == command.max_retries",
                "Coordinator accepts LIMIT on a nonterminal round")

    def test_retryable_provider_errors_have_an_independent_bounded_budget(self) -> None:
        hello = function_body(GUEST, "live_parse_hello_v2")
        relay = function_body(GUEST, "live_relay_loop_v2")
        execute = function_body(GUEST, "nexus_execute_open_decision")
        workflow = function_body(GUEST, "live_workflow_v2")
        for contract in (
            "#define LIVE_MAX_RETRYABLE_ERRORS 32U",
            '"max_retries"',
            "uint max_retries;",
        ):
            require(GUEST, contract, "retry budget is absent from the Guest ABI")
        require(hello, "number > LIVE_MAX_RETRYABLE_ERRORS",
                "HELLO accepts an unbounded retry budget")
        require(hello, "number == 0", "HELLO accepts a zero retry budget")
        require(GUEST, "seen == 511U", "HELLO max_retries is not mandatory")
        require(relay, "command.max_retries = hello->max_retries",
                "Relay does not propagate the negotiated retry cap")
        for body, rounds, retries in (
            (relay, "hello->max_rounds", "hello->max_retries"),
            (workflow, "command.max_rounds", "command.max_retries"),
        ):
            require(body, f"decision_rounds < {rounds}",
                    "decision budget does not guard model attempts")
            require(body, f"retryable_errors < {retries}",
                    "retry budget does not guard model attempts")
            require(body, f"attempts <= {rounds} + {retries}",
                    "combined attempt ceiling is not asserted")
        require_order(
            execute,
            ('!strcmp(code + 1, "provider_retryable")', "return 4",
             '!strcmp(code + 1, "provider_fatal")', "return 3"),
            "retryable and fatal provider outcomes are not distinct",
        )
        require_order(
            workflow,
            ("nexus_compact_is_delivered_decision(event.payload)",
             "decision_rounds++", "decision_status = nexus_execute_open_decision(",
             "decision_status == 4", "retryable_errors++",
             "decision_status == 0 || decision_status == 4"),
            "Coordinator does not account decisions and retries before ACK",
        )
        require_order(
            relay,
            ("live_v2_receive_model(", "receive_status == 0",
             "decision.type != LIVE_DECISION_ERROR", "decision_rounds++",
             "live_validate_decision(", "!decision.retryable", "break;",
             "decision.type == LIVE_DECISION_ERROR", "retryable_errors++"),
            "Relay does not count retryable errors separately from model decisions",
        )
        require(workflow, "decision_status == 1 || decision_status == 2 ||",
                "terminal decision statuses no longer stop the loop")
        forbid(workflow, "decision_status == 4)\n\t\t\t\tbreak",
               "retryable provider errors terminate instead of replanning")

    def test_delivered_response_counts_before_every_execution_terminal_edge(self) -> None:
        relay = function_body(GUEST, "live_relay_loop_v2")
        workflow = function_body(GUEST, "live_workflow_v2")
        execute = function_body(GUEST, "nexus_execute_open_decision")
        classify = function_body(GUEST, "nexus_compact_is_delivered_decision")
        require_order(
            relay,
            ("receive_status == 0", "decision.type != LIVE_DECISION_ERROR",
             "decision_rounds++", "live_validate_decision(",
             'validation_error = "bad_args"',
             "live_wait_tool_result_cancelable(",
             "live_result_session_blocked(&tool_result)",
             "decision.type == LIVE_DECISION_FINAL"),
            "Relay execution/cancellation can precede delivered-response accounting",
        )
        require_order(
            workflow,
            ("nexus_compact_is_delivered_decision(event.payload)",
             "decision_rounds++", "nexus_execute_open_decision("),
            "Coordinator executes a delivered response before counting it",
        )
        self.assertEqual(relay.count("decision_rounds++"), 1,
                         "Relay can double-count a delivered response")
        self.assertEqual(workflow.count("decision_rounds++"), 1,
                         "Coordinator can double-count a delivered response")
        require_order(
            classify,
            ('"nexus-B|"', '"nexus-E|T|"', "return 1",
             '"nexus-E|N|"', '"provider_retryable"', '"provider_fatal"',
             "return 1", "return 0"),
            "compact response/error marker accounting truth table drifted",
        )
        require_order(
            execute,
            ("decision.type == LIVE_DECISION_FINAL",
             "nexus_root_terminal_after_cleanup(", "return 3", "return 1",
             "status == AGENT_STATUS_IO_ERROR && nexus_artifact_cleanup_failed",
             "return 3", "status == AGENT_STATUS_CANCELLED",
             "nexus_root_terminal_after_cleanup(", "return 3", "return 2"),
            "final cleanup, tool cleanup, and active tool cancel terminal traces drifted",
        )

    def test_turn_complete_carries_exact_decision_retry_attempt_counts(self) -> None:
        relay = function_body(GUEST, "live_relay_loop_v2")
        complete = function_body(GUEST, "live_v2_emit_turn_complete")
        self.assertEqual(
            re.findall(r'\\"([a-z_]+)\\"', complete),
            ["turn_id", "request_id", "status", "rounds", "retries",
             "attempts", "answer"],
            "Guest TURN_COMPLETE budget-proof fields or order drifted",
        )
        require_order(
            complete,
            ("live_builder_u64(&builder, rounds)",
             "live_builder_u64(&builder, retries)",
             "live_builder_u64(&builder, attempts)", "if (answer != 0)"),
            "TURN_COMPLETE derives or emits counts after optional content",
        )
        require_order(
            relay,
            ("attempts++", "decision.type != LIVE_DECISION_ERROR",
             "decision_rounds++", "retryable_errors++",
             "live_v2_emit_turn_complete(",
             "decision_rounds, retryable_errors, attempts"),
            "Relay does not publish its exact terminal budget counters",
        )

    def test_protocol_layout_and_artifact_roles_match_the_open_loop(self) -> None:
        for obsolete in (
            "AGENT_NEXUS_TASK_SYSTEM_SNAPSHOT",
            "AGENT_NEXUS_TASK_LOCAL_RESEARCH",
            "AGENT_NEXUS_TASK_COMPOSE_REPORT",
            "AGENT_NEXUS_ARTIFACT_SEED",
        ):
            forbid(PROTOCOL + NEXUS_LIB, obsolete, "old demo ABI remains")
        for current in (
            "AGENT_NEXUS_ARTIFACT_TOOL_INPUT",
            "AGENT_NEXUS_ARTIFACT_SYSTEM_RESULT",
            "AGENT_NEXUS_ARTIFACT_RESEARCH_RESULT",
            "AGENT_NEXUS_ARTIFACT_REPORT",
        ):
            require(NEXUS_LIB + PROTOCOL, current, f"artifact ABI omits {current}")
        require(NEXUS_LIB, "AGENT_FILE_PUBLISH_MAX_BYTES",
                "artifact does not fit atomic publish snapshot")

    def test_persistent_session_artifacts_are_unique_and_transients_are_hidden(self) -> None:
        dispatch = function_body(GUEST, "nexus_dispatch_task")
        read = function_body(GUEST, "nexus_read_product_artifact")
        clear = function_body(GUEST, "nexus_clear_work_identity")
        workflow = function_body(GUEST, "live_workflow_v2")
        control = function_body(GUEST, "live_v2_control_execute")
        self.assertEqual(
            int(re.search(r"AGENT_NEXUS_ARTIFACT_SLOTS\s+(\d+)U", PROTOCOL).group(1)),
            65535,
        )
        require(dispatch, "nexus_next_artifact_slot++",
                "task artifacts reuse a handle within one lifecycle")
        forbid(dispatch, "nexus_next_artifact_slot =",
               "persistent-session artifact cursor is reset per turn")
        require(dispatch, "nexus_cleanup_task_artifacts",
                "tool paths do not converge through cleanup")
        require(dispatch, "persistent_result ? result_handle : 0",
                "transient evidence handle escapes into model or Context data")
        require(dispatch, "wire->artifact_handle = persistent_result ? result_handle : 0",
                "transient handle escapes into TASK_EVENT")
        require(read, "handle != nexus_report_handle",
                "non-report or stale handles can be dereferenced")
        require(read, "nexus_artifact_owner_matches",
                "report handle is not current-turn bound")
        require_order(
            dispatch,
            ("nexus_report_handle != 0", "nexus_remove_ephemeral_artifact(",
             "nexus_report_handle = result_handle"),
            "a replacement draft leaves the previous report readable",
        )
        require(clear, "nexus_remove_ephemeral_artifact(nexus_report_handle)",
                "turn/reset cleanup leaves a current report behind")
        terminal = function_body(GUEST, "nexus_root_terminal_after_cleanup")
        require(terminal, "nexus_clear_work_identity()",
                "turn terminal path lacks an unpublished report cleanup barrier")
        require(control, "nexus_clear_work_identity()",
                "reset does not clear an unpublished report")

    def test_task_events_stream_without_old_batch_truncation(self) -> None:
        add = function_body(GUEST, "nexus_add_task_event")
        forbid(GUEST, "NEXUS_TASK_EVENTS_MAX", "TASK_EVENT stream still has a batch cap")
        forbid(add, "return 0", "TASK_EVENT can be silently dropped")
        require(add, "nexus_event_count++", "TASK_EVENT sequence is not counted")

    def test_task_event_summary_never_copies_model_authored_text(self) -> None:
        dispatch = function_body(GUEST, "nexus_dispatch_task")
        summary = function_body(GUEST, "nexus_task_summary")
        forbid(dispatch, "nexus_copy_text(wire->summary, sizeof(wire->summary), objective)",
               "multiline or split UTF-8 model text escapes into TASK_EVENT summary")
        require(dispatch, "nexus_task_summary(",
                "assigned TASK_EVENT does not use metadata-only summary")
        for metadata in (
            '"task_type="',
            '";objective_bytes="',
            '";objective_sha256_prefix="',
            "live_sha256(objective, objective_bytes, digest)",
        ):
            require(summary, metadata, "task summary lacks bounded ASCII metadata")
        forbid(summary, "nexus_copy_text",
               "task summary can byte-truncate Unicode/control input")
        hostile_report = ("分析\t内容\n" * 40).encode("utf-8")
        self.assertGreater(len(hostile_report), 256)

    def test_runtime_context_address_is_not_packed_as_a_count(self) -> None:
        runtime = function_body(GUEST, "nexus_build_system_payload")
        require(runtime, '"\\ncontext_base="', "Context address is unlabeled")
        require(runtime, '"\\ncontext_size="', "Context size is unlabeled")
        require(runtime, '"\\nvolatile_fields_omitted=call_count"',
                "volatile Context call count is not disclosed as omitted")
        forbid(runtime, '"\\ncall_count="',
               "volatile Context call count entered model history")
        forbid(runtime, "metrics->process_count = (uint)response.value0",
               "large Context base is truncated into packed business metrics")

    def test_runtime_projection_omits_cross_boot_counters_for_all_operations(self) -> None:
        runtime = function_body(GUEST, "nexus_build_system_payload")
        for stable in ('"\\nprocess_count="', '"\\nagent_count="',
                       '"\\ncontext_base="', '"\\ncontext_size="'):
            require(runtime, stable, "stable runtime structure is absent")
        for volatile in ("uptime_tick", "runnable_count", "call_count"):
            require(runtime, f'"\\nvolatile_fields_omitted={volatile}"',
                    f"{volatile} omission is not explicit")
            forbid(runtime, f'"\\n{volatile}="',
                   f"{volatile} still enters replay-bound model history")

    def test_source_snapshot_is_bounded_literal_and_runtime_mutation_denied(self) -> None:
        prompt = c_string(GUEST, "live_system_prompt")
        workflow = function_body(GUEST, "live_workflow")
        require(prompt, "one literal substring", "source search semantics are hidden")
        for denial in (
            'open("nxsrcmeta", O_WRONLY)',
            'open("nxsrcmeta", O_WRONLY | O_TRUNC)',
            'unlink("nxsrcmeta")',
        ):
            require(workflow, denial, "source snapshot mutation denial is untested")


if __name__ == "__main__":
    unittest.main()
