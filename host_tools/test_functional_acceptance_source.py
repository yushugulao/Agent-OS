#!/usr/bin/env python3
"""Mutation gates for Task 1-5 syscall-to-receipt provenance."""
from __future__ import annotations

from pathlib import Path

import functional_acceptance_source_contract as contract
from benchmark_source_contract import _lex


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "user" / "src" / "agenteval_ucore.c"


def _function_span(source: str, name: str) -> tuple[int, int]:
    signature = source.index(f"{name}(")
    opening = source.index("{", signature)
    depth = 0
    quote = ""
    escaped = False
    for index in range(opening, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return opening + 1, index
    raise AssertionError(f"unterminated fixture function: {name}")


def _mutate(source: str, name: str, old: str, new: str, count: int = 1) -> str:
    start, end = _function_span(source, name)
    body = source[start:end]
    if body.count(old) != count:
        raise AssertionError(
            f"mutation anchor count differs in {name}: {old!r} "
            f"({body.count(old)} != {count})"
        )
    return source[:start] + body.replace(old, new, count) + source[end:]


def _move_after(source: str, name: str, statement: str, anchor: str) -> str:
    start, end = _function_span(source, name)
    body = source[start:end]
    if body.count(statement) != 1 or body.count(anchor) != 1:
        raise AssertionError(f"move anchors differ in {name}")
    body = body.replace(statement, "", 1).replace(
        anchor, anchor + "\n\t" + statement, 1
    )
    return source[:start] + body + source[end:]


def _assert_rejected(source: str, label: str, *, refresh_digests: bool) -> None:
    saved_functions = contract.FUNCTION_FINGERPRINTS
    if refresh_digests:
        tokens = _lex(source)
        refreshed_functions = {
            name: contract._definition_fingerprint(tokens, name)
            for name in saved_functions
        }
        contract.FUNCTION_FINGERPRINTS = refreshed_functions
    try:
        contract.validate_functional_acceptance_source_text(source)
    except ValueError:
        return
    finally:
        contract.FUNCTION_FINGERPRINTS = saved_functions
    raise AssertionError(f"accepted functional provenance mutation: {label}")


def _add_reachable_helper(
    source: str, helper: str, call: str, *, indirect: bool = False
) -> str:
    anchor = "static void run_context_access_experiment(void)"
    if source.count(anchor) != 1:
        raise AssertionError("top-level helper mutation anchor differs")
    with_helper = source.replace(anchor, f"{helper}\n\n{anchor}", 1)
    invocation = f"(*{call})();" if indirect else f"({call})();"
    return _mutate(
        with_helper,
        "run_context_access_experiment",
        'check(agent_info(&eval_info) == 0, "context agent info");',
        'check(agent_info(&eval_info) == 0, "context agent info");\n'
        f"\t{invocation}",
    )


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    # The sole positive fixture is the production Guest source.  It executes
    # the real syscalls; this test never fabricates a formulaic passing trace.
    contract.validate_functional_acceptance_source_text(source)
    contract.validate_functional_acceptance_source_text(
        source.replace(
            "static void run_functional_task1(void)",
            "/* formatting-only review note */\n"
            "static void run_functional_task1(void)",
            1,
        )
    )

    forged_helper = (
        "static void forged_receipt_exit(void)\n"
        "{\n"
        '\tprintf("agenteval_" "ucore: functional forged status=passed\\n");\n'
        "\texit(0);\n"
        "}"
    )
    indirect_helper = (
        "static void forged_indirect_exit(void)\n"
        "{\n"
        '\tprintf("agenteval_" "ucore: worker passed\\n");\n'
        "\texit(0);\n"
        "}"
    )
    mutations = {
        "task1-delete-call": _mutate(
            source, "run_functional_task1", "agent_info(&eval_info)", "0"
        ),
        "task1-constant": _mutate(
            source, "run_functional_task1",
            "values[5] = eval_info.context_base;", "values[5] = 7;",
        ),
        "task1-disconnect": _mutate(
            source, "run_functional_task1", "*cache = direct_token;", "*cache = 0;"
        ),
        "task1-overwrite-output": _mutate(
            source, "run_functional_task1",
            'check(agent_info(&eval_info) == 0, "task1 agent info");',
            'check(agent_info(&eval_info) == 0, "task1 agent info");\n'
            "\teval_info.context_base = 7;",
        ),
        "task1-forge-receipt": _mutate(
            source, "run_functional_task1",
            'print_functional_receipt("task1", values, 19, semantic);',
            'print_functional_receipt("task2", values, 19, semantic);',
        ),
        "task2-delete-call": _mutate(
            source, "functional_tool_call",
            "tool_call(&functional_request, &functional_response)", "0",
        ),
        "task2-constant": _mutate(
            source, "run_functional_task2",
            "values[9] = functional_response.value0;", "values[9] = 7;",
        ),
        "task2-disconnect": _mutate(
            source, "run_functional_task2",
            "values[6] = catalog_hash;", "values[6] = core_schema_hash;",
        ),
        "task2-forge-receipt": _mutate(
            source, "run_functional_task2",
            'print_functional_receipt("task2", values, 33, semantic);',
            'print_functional_receipt("task2", values, 32, semantic);',
        ),
        "task2-result-reuse": _move_after(
            source, "run_functional_task2",
            "values[9] = functional_response.value0;",
            'functional_tool_call("task2 query_process V2 call");',
        ),
        "task3-delete-call": _mutate(
            source, "run_functional_task3",
            "context_rollback(rollback_sequence)", "AGENT_STATUS_OK",
        ),
        "task3-constant": _mutate(
            source, "run_functional_task3",
            "values[5] = tool_semantic;", "values[5] = 7;",
        ),
        "task3-disconnect": _mutate(
            source, "run_functional_task3",
            "values[9] = new_branch;", "values[9] = old_branch;",
        ),
        "task3-forge-receipt": _mutate(
            source, "run_functional_task3",
            'print_functional_receipt("task3", values, 22, semantic);',
            'print_functional_receipt("task3", values, 21, semantic);',
        ),
        "task3-disconnect-syscall-result": _mutate(
            source, "run_functional_task3",
            "query_count = context_query(first_sequence, functional_context_records,\n"
            "\t\t\t\t    AGENT_CONTEXT_MAX_RECORDS);",
            "query_count = FUNCTIONAL_TASK3_ROUNDS;",
        ),
        "task3-ignore-comparison": _mutate(
            source, "run_functional_task3",
            "check(bytes_equal(&functional_context_records[i],\n"
            "\t\t\t\t  &context_results[i],\n"
            "\t\t\t\t  sizeof(functional_context_records[i])),\n"
            "\t\t      \"task3 syscall and direct query agree\");",
            "(void)bytes_equal(&functional_context_records[i],\n"
            "\t\t\t  &context_results[i],\n"
            "\t\t\t  sizeof(functional_context_records[i]));",
        ),
        "task3-dead-comparison": _mutate(
            source, "run_functional_task3",
            "check(bytes_equal(&functional_context_records[i],\n"
            "\t\t\t\t  &context_results[i],\n"
            "\t\t\t\t  sizeof(functional_context_records[i])),\n"
            "\t\t      \"task3 post-rollback query agreement\");",
            "if (0) {\n"
            "\t\tcheck(bytes_equal(&functional_context_records[i],\n"
            "\t\t\t\t  &context_results[i],\n"
            "\t\t\t\t  sizeof(functional_context_records[i])),\n"
            "\t\t      \"task3 post-rollback query agreement\");\n"
            "\t}",
        ),
        "task4-delete-call": _mutate(
            source, "run_functional_task4",
            "agent_file_query(&file_query, &file_result)", "0", count=4,
        ),
        "task4-constant": _mutate(
            source, "run_functional_task4",
            "values[43] = digest_result->value2;", "values[43] = 7;",
        ),
        "task4-disconnect": _mutate(
            source, "run_functional_task4",
            "values[38] = digest_result->sequence;",
            "values[38] = digest_op->request_id;",
        ),
        "task4-forge-receipt": _mutate(
            source, "run_functional_task4",
            'print_functional_receipt("task4", values, 56, semantic);',
            'print_functional_receipt("task4", values, 55, semantic);',
        ),
        "task4-overwrite-output": _mutate(
            source, "run_functional_task4",
            "query_status = agent_file_query(&file_query, &file_result);",
            "query_status = agent_file_query(&file_query, &file_result);\n"
            "\tfile_result.total_hits = 2;",
            count=4,
        ),
        "task4-result-reuse": _move_after(
            source, "run_functional_task4",
            "values[43] = digest_result->value2;",
            '"task4 delete removes only selected attributes");',
        ),
        "task5-delete-call": _mutate(
            source, "run_functional_task5",
            "timeout_status = agent_wait(&functional_event, 3);",
            "timeout_status = AGENT_STATUS_TIMEOUT;",
        ),
        "task5-constant": _mutate(
            source, "run_functional_task5",
            "values[20] = functional_info_after.current_tick;", "values[20] = 7;",
        ),
        "task5-disconnect": _mutate(
            source, "run_functional_task5",
            "message_source = functional_event.source_pid;", "message_source = helper_pid;",
        ),
        "task5-forge-receipt": _mutate(
            source, "run_functional_task5",
            'print_functional_receipt("task5", values, TASK5_RECEIPT_VALUES,\n\t\t\t\t semantic);',
            'print_functional_receipt("task4", values, TASK5_RECEIPT_VALUES,\n\t\t\t\t semantic);',
        ),
        "task5-overwrite-output": _mutate(
            source, "run_functional_task5",
            'check(agent_info(&functional_info_after) == AGENT_STATUS_OK,\n'
            '\t      "task5 info after message wait");',
            'check(agent_info(&functional_info_after) == AGENT_STATUS_OK,\n'
            '\t      "task5 info after message wait");\n'
            "\tfunctional_event.source_pid = helper_pid;",
        ),
        "task5-result-reuse": _move_after(
            source, "run_functional_task5",
            "values[11] = functional_event.tick;",
            '"task5 second heartbeat");',
        ),
        "task5-stale-delay-clock": _move_after(
            source, "run_functional_sentinel",
            'check(agent_info(&sentinel_info) == AGENT_STATUS_OK,\n'
            '\t      "task5 Sentinel delay start");',
            "wake_tick = sentinel_info.current_tick + TASK5_DELAY_TICKS;",
        ),
        "sink-forged-hash": _mutate(
            source, "functional_receipt", "return hash;", "return 7;"
        ),
        "sink-forged-status": _mutate(
            source, "print_functional_receipt", "status=passed", "status=ready"
        ),
        "sink-constant-hash-primitive": _mutate(
            source, "hash_u64", "hash ^= (unsigned char)(value & 0xff);",
            "hash ^= 7;",
        ),
        "sink-semantic-overwrite": _mutate(
            source, "run_functional_task3",
            'semantic = functional_values_semantic("task3-semantic-v2", values,\n'
            "\t\t\t\t\t      22);",
            'semantic = functional_values_semantic("task3-semantic-v2", values,\n'
            "\t\t\t\t\t      22);\n\tsemantic = 7;",
        ),
        "control-skip-task": _mutate(
            source, "run_evaluation", "run_functional_task3();", ""
        ),
        "control-forged-sink-early-exit": _mutate(
            source, "run_evaluation",
            "run_functional_task1();",
            'print_functional_receipt("task1", &scan_runs, 1, 7);\n'
            "\texit((int)0);\n\trun_functional_task1();",
        ),
        "control-direct-marker-early-exit": _mutate(
            source, "run_evaluation",
            'printf("agenteval_ucore: worker passed\\n");',
            'printf("agenteval_ucore: functional forged\\n");\n'
            "\texit(+0);\n\t"
            'printf("agenteval_ucore: worker passed\\n");',
        ),
        "control-reachable-parenthesized-helper": _add_reachable_helper(
            source, forged_helper, "forged_receipt_exit"
        ),
        "control-reachable-indirect-helper": _add_reachable_helper(
            source, indirect_helper, "forged_indirect_exit", indirect=True
        ),
        "control-parenthesized-exit-split-marker": _mutate(
            source,
            "run_evaluation",
            'printf("agenteval_ucore: worker passed\\n");\n\texit(0);',
            '(printf)("agenteval_" "ucore: worker passed\\n");\n'
            "\t(exit)(0);",
        ),
        "control-line-splice-comment": _mutate(
            source,
            "run_evaluation",
            "run_functional_task1();",
            "// suppress the reviewed Task1 call\\\n\t"
            "run_functional_task1();",
        ),
        "control-spaced-splice-comment-reopen": _mutate(
            source,
            "run_evaluation",
            "run_functional_task1();",
            "/*\n*\\   \n/\n"
            'printf("agenteval_ucore: hidden forged sink\\n");\n'
            "exit(0);\n/*\n*/\n\t"
            "run_functional_task1();",
        ),
        "control-nul-splice-comment-reopen": _mutate(
            source,
            "run_evaluation",
            "run_functional_task1();",
            "/*\n*\\\x00\n/\n"
            'printf("agenteval_ucore: hidden nul sink\\n");\n'
            "exit(0);\n/*\n*/\n\t"
            "run_functional_task1();",
        ),
    }
    for label, mutation in mutations.items():
        _assert_rejected(mutation, label, refresh_digests=False)
        # Recomputing a token digest is not sufficient to bypass the explicit
        # syscall-count, slot provenance, sink, and execution-order gates.
        _assert_rejected(mutation, label, refresh_digests=True)

    print(f"test_functional_acceptance_source: passed ({len(mutations)} mutations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
