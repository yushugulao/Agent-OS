#!/usr/bin/env python3
"""Cross-source and fail-closed tests for the Nexus autonomy contract."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_nexus_contract as contract


ROOT = Path(__file__).resolve().parents[1]
GUEST_SOURCE = ROOT / "user" / "src" / "agentnexus_ucore.c"
PROTOCOL_HEADER = ROOT / "user" / "include" / "agent_nexus_protocol.h"


def _c_string(source: str, name: str) -> str:
    match = re.search(
        rf"static const char\s+{re.escape(name)}\[\]\s*=\s*"
        rf"(?P<body>(?:\s*\"(?:\\.|[^\"\\])*\")+)\s*;",
        source,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing C string {name}")
    tokens = re.findall(r'"(?:\\.|[^"\\])*"', match.group("body"))
    return "".join(ast.literal_eval(token) for token in tokens)


def _header_macro(source: str, name: str) -> str:
    match = re.search(
        rf"^#define\s+{re.escape(name)}(?:\s+\\\n\s*)?\s+([^\n]+)$",
        source,
        re.MULTILINE,
    )
    if match is None:
        raise AssertionError(f"missing {name}")
    value = match.group(1).strip()
    if value.endswith("U") and value[:-1].isdigit():
        return value[:-1]
    return ast.literal_eval(value)


def _pair_sha256(user: str, assistant: str) -> str:
    return hashlib.sha256(
        user.encode("utf-8") + b"\0" + assistant.encode("utf-8")
    ).hexdigest()


def _request(
    *,
    history: bool = False,
    prior_turns: tuple[tuple[str, str], ...] = (),
) -> dict[str, object]:
    messages: list[dict[str, object]] = []
    context_turns: list[dict[str, object]] = []
    sequence = 1
    for index, (user, assistant) in enumerate(prior_turns, 1):
        messages.extend(
            (
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            )
        )
        context_turns.append(
            {
                "turn_id": index,
                "request_id": index + 10,
                "user_sequence": sequence,
                "final_sequence": sequence + 1,
                "sha256": _pair_sha256(user, assistant),
            }
        )
        sequence += 2
    messages.extend(
        [
        {"role": "user", "content": "an arbitrary current goal"},
        {
            "role": "user",
            "content": contract.CONTROL_CONTEXT_PREFIX + "bounded observations",
        },
        ]
    )
    if history:
        messages.extend(
            [
                {
                    "role": "assistant",
                    "tool_use": {
                        "corr_id": 7,
                        "tool": "search_files",
                        "arguments": {"query": "symbol"},
                    },
                },
                {
                    "role": "tool",
                    "tool_corr_id": 7,
                    "content": '{"status":0}',
                    "is_error": False,
                },
            ]
        )
    return {
        "corr_id": 9,
        "contract_version": contract.CONTRACT_VERSION,
        "policy_sha256": contract.SYSTEM_POLICY_SHA256,
        "tool_catalog_sha256": contract.TOOL_CATALOG_SHA256,
        "context_path": {
            "version": contract.CONTEXT_PATH_VERSION,
            "branch_generation": 3,
            "visible_head_sequence": sequence,
            "current_user_sequence": sequence,
            "turns": context_turns,
        },
        "max_tokens": 512,
        "system": contract.SYSTEM_PROMPT,
        "messages": messages,
        "tools": copy.deepcopy(list(contract.TOOLS)),
    }


class CrossSourceTests(unittest.TestCase):
    def test_guest_literals_and_protocol_macros_match_host_anchor(self) -> None:
        guest = GUEST_SOURCE.read_text(encoding="utf-8")
        header = PROTOCOL_HEADER.read_text(encoding="utf-8")
        guest_system = _c_string(guest, "live_system_prompt")
        guest_tools_json = _c_string(guest, "live_tools_json")

        self.assertEqual(guest_system, contract.SYSTEM_PROMPT)
        self.assertEqual(json.loads(guest_tools_json), list(contract.TOOLS))
        self.assertEqual(
            hashlib.sha256(guest_system.encode()).hexdigest(),
            contract.SYSTEM_POLICY_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(guest_tools_json.encode()).hexdigest(),
            contract.TOOL_CATALOG_SHA256,
        )
        self.assertEqual(
            int(_header_macro(header, "AGENT_NEXUS_AUTONOMY_CONTRACT_VERSION")),
            contract.CONTRACT_VERSION,
        )
        self.assertEqual(
            _header_macro(header, "AGENT_NEXUS_SYSTEM_POLICY_SHA256"),
            contract.SYSTEM_POLICY_SHA256,
        )
        self.assertEqual(
            _header_macro(header, "AGENT_NEXUS_TOOL_CATALOG_SHA256"),
            contract.TOOL_CATALOG_SHA256,
        )

    def test_system_policy_and_tools_are_generic_and_read_only(self) -> None:
        prompt = contract.SYSTEM_PROMPT
        self.assertEqual(contract.CONTRACT_VERSION, 4)
        for text in (
            "Solve the user's current task directly and in the requested language",
            "come from the active AgentOS Context path",
            "reassess when the user changes direction",
            "current Host workspace supplied to this session",
            "search before reading when the location is unknown",
            "System inspection describes only the current Guest runtime",
            "return exactly one tool call with no prose",
            "Do not invent unseen facts, narrate the harness, or list the tool sequence",
            "Keep the final answer within 2048 UTF-8 bytes",
        ):
            self.assertIn(text, prompt)
        for legacy in (
            "kernel engineering",
            "build_source_snapshot",
            "citation",
            "draft_report",
            "read_artifact",
            "proof",
        ):
            self.assertNotIn(legacy, prompt)
        tools = {str(tool["name"]): tool for tool in contract.TOOLS}
        self.assertEqual(set(tools), {"search_files", "read_file", "inspect_system"})
        self.assertIn("current Host workspace", tools["search_files"]["description"])
        self.assertIn("empty query lists files", tools["search_files"]["description"])
        self.assertIn("at most 8 matches", tools["search_files"]["description"])
        self.assertIn("Read-only", tools["read_file"]["description"])
        self.assertIn("current Guest runtime", tools["inspect_system"]["description"])
        self.assertEqual(
            tools["search_files"]["input_schema"],
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 0,
                        "maxLength": 95,
                        "pattern": r"^[^\u0000]*$",
                    },
                    "path_prefix": {
                        "type": "string",
                        "maxLength": 111,
                        "pattern": r"^[^\u0000]*$",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            tools["read_file"]["input_schema"],
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 255,
                        "pattern": r"^[^\u0000]*$",
                    },
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 4294967295,
                    },
                    "max_lines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 64,
                    },
                },
                "required": ["path", "start_line", "max_lines"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            tools["inspect_system"]["input_schema"],
            {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["status", "processes", "context"],
                    }
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
        )


class ContractValidationTests(unittest.TestCase):
    def assert_rejected(self, request: dict[str, object]) -> None:
        with self.assertRaises(contract.NexusContractError):
            contract.validate_request_contract(request)

    def test_exact_three_tool_request_and_context_history_are_accepted(self) -> None:
        request = _request(
            history=True,
            prior_turns=(("first question", "first answer"), ("继续", "后续回答")),
        )
        contract.validate_request_contract(request)
        self.assertEqual(
            [tool["name"] for tool in request["tools"]],
            ["search_files", "read_file", "inspect_system"],
        )

    def test_system_one_character_mutation_is_rejected(self) -> None:
        request = _request()
        request["system"] = str(request["system"])[:-1] + "?"
        self.assert_rejected(request)

    def test_tool_catalog_mutations_are_rejected(self) -> None:
        mutations = []
        request = _request()
        request["tools"][0]["name"] = "search_paths"
        mutations.append(request)
        request = _request()
        request["tools"][1]["description"] += "?"
        mutations.append(request)
        request = _request()
        request["tools"][2]["input_schema"]["properties"]["operation"]["enum"].reverse()
        mutations.append(request)
        request = _request()
        request["tools"][0], request["tools"][1] = (
            request["tools"][1],
            request["tools"][0],
        )
        mutations.append(request)
        request = _request()
        request["tools"].append(copy.deepcopy(request["tools"][0]))
        mutations.append(request)
        request = _request()
        del request["tools"][-1]
        mutations.append(request)
        request = _request()
        request["tools"][2]["name"] = request["tools"][0]["name"]
        mutations.append(request)
        for mutation in mutations:
            with self.subTest(tools=mutation["tools"]):
                self.assert_rejected(mutation)

    def test_forced_choice_temperature_and_stop_are_rejected(self) -> None:
        for key, value in (
            ("tool_choice", {"tool": "search_files"}),
            ("temperature", 0),
            ("stop", "done"),
        ):
            request = _request()
            request[key] = value
            with self.subTest(field=key):
                self.assert_rejected(request)

    def test_contract_proof_mutations_and_missing_fields_are_rejected(self) -> None:
        for key, value in (
            ("contract_version", contract.CONTRACT_VERSION + 1),
            ("policy_sha256", "0" * 64),
            ("tool_catalog_sha256", "f" * 64),
        ):
            request = _request()
            request[key] = value
            self.assert_rejected(request)
            del request[key]
            self.assert_rejected(request)

    def test_context_path_fields_bounds_and_order_are_rejected(self) -> None:
        mutations: list[dict[str, object]] = []
        for key, value in (
            ("version", 2),
            ("branch_generation", 0),
            ("visible_head_sequence", True),
            ("current_user_sequence", 1 << 64),
        ):
            request = _request()
            request["context_path"][key] = value
            mutations.append(request)

        request = _request()
        del request["context_path"]["turns"]
        mutations.append(request)
        request = _request()
        request["context_path"]["extra"] = 1
        mutations.append(request)
        request = _request()
        request["context_path"]["visible_head_sequence"] = 1
        request["context_path"]["current_user_sequence"] = 2
        mutations.append(request)
        request = _request(prior_turns=(("one", "answer one"),) * 2)
        request["context_path"]["turns"][1]["turn_id"] = 1
        mutations.append(request)
        request = _request(prior_turns=(("one", "answer one"),) * 2)
        request["context_path"]["turns"][1]["user_sequence"] = 2
        mutations.append(request)
        request = _request(
            prior_turns=(("one", "answer one"), ("two", "answer two"))
        )
        extra = copy.deepcopy(request["context_path"]["turns"][-1])
        extra.update(
            {
                "turn_id": 3,
                "request_id": 13,
                "user_sequence": 5,
                "final_sequence": 6,
            }
        )
        request["context_path"]["turns"].append(extra)
        mutations.append(request)

        for mutation in mutations:
            with self.subTest(context_path=mutation.get("context_path")):
                self.assert_rejected(mutation)

    def test_context_path_digest_and_message_binding_are_rejected_when_tampered(self) -> None:
        mutations: list[dict[str, object]] = []
        request = _request(prior_turns=(("question", "answer"),))
        request["context_path"]["turns"][0]["sha256"] = "A" * 64
        mutations.append(request)
        request = _request(prior_turns=(("question", "answer"),))
        request["context_path"]["turns"][0]["sha256"] = "0" * 64
        mutations.append(request)
        request = _request(prior_turns=(("question", "answer"),))
        request["messages"][1]["content"] = "altered answer"
        mutations.append(request)
        request = _request(prior_turns=(("question", "answer"),))
        request["messages"][0]["role"] = "system"
        mutations.append(request)
        request = _request(prior_turns=(("question", "answer"),))
        request["messages"][0]["content"] = "question\0suffix"
        request["context_path"]["turns"][0]["sha256"] = _pair_sha256(
            "question\0suffix", "answer"
        )
        mutations.append(request)
        for mutation in mutations:
            self.assert_rejected(mutation)

    def test_non_contract_message_shapes_are_rejected(self) -> None:
        invalid_messages = (
            [
                {"role": "user", "content": "goal"},
                {"role": "system", "content": "extra"},
                {"role": "user", "content": contract.CONTROL_CONTEXT_PREFIX + "x"},
            ],
            [
                {"role": "user", "content": "goal"},
                {"role": "user", "content": contract.CONTROL_CONTEXT_PREFIX + "x"},
                {"role": "assistant", "content": "plain answer"},
            ],
            [
                {"role": "user", "content": "earlier turn summary"},
                {"role": "user", "content": "current goal"},
                {"role": "user", "content": contract.CONTROL_CONTEXT_PREFIX + "x"},
            ],
            [
                {"role": "user", "content": "goal"},
                {"role": "user", "content": "unlabelled context"},
            ],
        )
        for messages in invalid_messages:
            request = _request()
            request["messages"] = messages
            with self.subTest(messages=messages):
                self.assert_rejected(request)

    def test_invalid_history_paths_are_rejected(self) -> None:
        request = _request(history=True)
        request["messages"].pop()
        self.assert_rejected(request)

        request = _request(history=True)
        request["messages"][3]["tool_corr_id"] = 8
        self.assert_rejected(request)

        request = _request(history=True)
        request["messages"].extend(copy.deepcopy(request["messages"][2:]))
        self.assert_rejected(request)

        request = _request(history=True)
        request["messages"][2]["tool_use"]["tool"] = "unknown_tool"
        self.assert_rejected(request)

    def test_model_type_and_unknown_request_field_are_rejected(self) -> None:
        request = _request()
        request["model"] = 17
        self.assert_rejected(request)
        request = _request()
        request["summary"] = "previous turn"
        self.assert_rejected(request)

    def test_strip_removes_only_proofs_without_mutating_input(self) -> None:
        request = _request(history=True)
        original = copy.deepcopy(request)
        stripped = contract.strip_internal_contract_fields(request)
        self.assertEqual(request, original)
        self.assertEqual(
            set(request).difference(stripped), contract.INTERNAL_CONTRACT_FIELDS
        )
        self.assertEqual(
            {key: value for key, value in request.items() if key in stripped},
            stripped,
        )
        self.assertIsNot(stripped["messages"], request["messages"])
        bad = _request()
        bad["system"] += "!"
        with self.assertRaises(contract.NexusContractError):
            contract.strip_internal_contract_fields(bad)


if __name__ == "__main__":
    unittest.main()
