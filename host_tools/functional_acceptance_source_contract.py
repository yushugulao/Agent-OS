#!/usr/bin/env python3
"""任务 1-5 功能回执的版本化源码合同；可执行 token 变更必须复审合同版本。"""
from __future__ import annotations

import ast
import hashlib
import json
import re

if __package__:
    from .benchmark_source_contract import (
        _depth_at, _function_tokens, _lex, _locations, _matching,
    )
else:
    from benchmark_source_contract import (
        _depth_at, _function_tokens, _lex, _locations, _matching,
    )


CONTRACT_VERSION = "agentos-functional-acceptance-source-v4"

TASK_SPECS = {
    "task1": ("run_functional_task1", 19, "task1-semantic-v1", "19"),
    "task2": ("run_functional_task2", 33, "task2-semantic-v1", "33"),
    "task3": ("run_functional_task3", 22, "task3-semantic-v2", "22"),
    "task4": ("run_functional_task4", 56, "task4-semantic-v2", "56"),
    "task5": (
        "run_functional_task5",
        28,
        "task5-semantic-v2",
        "TASK5_RECEIPT_VALUES",
    ),
}

# 这是能创建、验证、哈希或发布任务 1-5 证据的完整受审函数闭包；
# 分函数摘要不把空白和注释纳入安全策略。
FUNCTION_GROUPS = {
    "common": (
        "check",
        "hash_bytes",
        "bytes_equal",
        "hash_u64",
        "functional_values_semantic",
        "functional_receipt",
        "print_functional_values",
        "print_launcher_receipt",
        "print_functional_receipt",
        "semantic_token",
        "format_hex16",
        "run_compat_sentinel_probe",
        "run_evaluation",
        "main",
    ),
    "task1": ("run_functional_task1",),
    "task2": (
        "functional_param_uint",
        "functional_param_string",
        "functional_request_init",
        "functional_tool_call",
        "functional_bounded_text_length",
        "functional_schema_param_count",
        "functional_tool_desc",
        "functional_core_schema_hash",
        "functional_catalog_load",
        "functional_response_text_hash",
        "run_functional_task2",
    ),
    "task3": (
        "prepare_functional_task3_tool",
        "check_functional_task3_result",
        "check_functional_task3_record",
        "functional_task3_record_hash",
        "run_functional_task3",
    ),
    "task4": (
        "task4_fixture_code",
        "task4_fixture_name",
        "task4_fixture_text",
        "task4_create_file",
        "task4_set_metadata",
        "task4_prepare_query",
        "task4_hit_matches",
        "task4_query_semantic",
        "task4_delete_metadata",
        "run_functional_task4",
    ),
    "task5": (
        "run_functional_sentinel", "run_functional_waiter",
        "run_functional_task5",
    ),
}

# 任务 3 复用公开直查 helper；以下语义检查约束关键数据流，避免复制整函数摘要。
SEMANTIC_ONLY_FUNCTIONS = frozenset({"run_functional_task3"})


# 摘要覆盖词法归一化后的函数名、参数与函数体；声明由闭合预处理合同和编译器检查，
# 排除函数名前文本以免把前置 #define 误当作签名。
FUNCTION_FINGERPRINTS: dict[str, str] = {
    "check": "1f294c748d60c411d1d4083b9d4d5fd7627051a1db02d8161ec1662b8401e376",
    "hash_bytes": "0b8ff7cd400ce7517ed36f015b2e6dba4b3d940074c121d805cb0f9ba82f6428",
    "bytes_equal": "18bd4533f527e4a19280bda391be71520bc4da68083c20cd7d50abd75e61190e",
    "hash_u64": "f0669b905fb9a26591ca8cf32274635f2911f7e1e6f0f0143eb60efa19c660ca",
    "functional_values_semantic": "cfd436d1c7d59750f5f8d836227bb2a0317dd0386c92a55acf344f3b8383812c",
    "functional_receipt": "5719639cb46752a8a0e7e297574a4dacb0e15360d5fac80fabc9abb55d07da4d",
    "print_functional_values": "a98a2e7ea7cd136447c4c2c8acdf889afdc01f7ff1226b07fb8916cdeab543a5",
    "print_launcher_receipt": "624f1fd7d4a41eefb9f909e1b75905a378ed309d6567717161c1ed1a1b96b92a",
    "print_functional_receipt": "065788e35e4733aaa3bd493fb4bf065a5905c086977b7467656830fd9b024de8",
    "semantic_token": "d05f9f7b644bdfe620a1f4eefaea450ee9ee2bdf09124cc43e2842aaed68459d",
    "format_hex16": "db77c81b5b81582de637b26c2ee3c43e143e83828f686cf59ecb41190f1449bc",
    "run_compat_sentinel_probe": "9512260918f207f54032f97b9d939881a505944fee150244303ce89f17062a4a",
    "run_evaluation": "00a48b4548b7d1e295e95963b1486eb4405bd7fe76d25fe612211bc11f60c7a3",
    "main": "19d496e49e33da3de101d085e6077809e9d20629515b9ddc21968643d0f1aca3",
    "run_functional_task1": "581f4a8b85d6cdf93f13d5c5b0423e1b2ded7af03f17d5bc9c90ddc309f1c7ce",
    "functional_param_uint": "997255a3721e64baccda98889a6bded6f75d87b83f936843eef510fc45f064d7",
    "functional_param_string": "9b7fa5a3e6cd4e9a72509ad8aad0b399cd03e6d25f2e298d32eb99ec445ba7a5",
    "functional_request_init": "23b27ea69b05bf8a6d551ae23b09242ce8dcc17ca98af173406abcf63a1eb54f",
    "functional_tool_call": "fbdbb7bbd997ef208f56d843575d050c870e5834662b7ead0a6068e65719d67a",
    "functional_bounded_text_length": "4dff686abf253b0f6200904dc7f7c75f88f7fc2c7b81bc60ee9c4ea26d15abb6",
    "functional_schema_param_count": "639cc778b539232d0776b1e7d65360eba49fd484455e838158866c0aaa85335c",
    "functional_tool_desc": "c85be3b0e1e1a89a3a6822f16d3b7fec2d9785d28ea5e2d3988bfe5b8f5f5f6d",
    "functional_core_schema_hash": "a0bf7602b3c324d4c380072dddebff16756e6c1160a03930dbd7a20fe0449af6",
    "functional_catalog_load": "d6bac1a9659b81d1f4286a0d5cc3570c65655baa945bf929b2a1963e97d1518a",
    "functional_response_text_hash": "7e4dff88ef90a547c50ea0abce95891a7fda403ae647225575eea34a04611593",
    "run_functional_task2": "a0a9ad5b88e502836a4d55c14f68b4b1e8d254b5097294894447d7f8699ac21f",
    "prepare_functional_task3_tool": "1a4191bb66ba0f1102602113d63e886f178cdfdd677a306e552c657b7b0ddfab",
    "check_functional_task3_result": "48f86e522a6c378a025bc754f4388336d41609c119d0001ed5c2ae485d30fd4c",
    "check_functional_task3_record": "830e016eba4c7373f23a9819804b91594251f7c94896c767a93dbb1486e0815a",
    "functional_task3_record_hash": "a697a491731cf481a29dad0eb7eaea864128bef66a7ab95e5f96ae4c8c9d36f7",
    "task4_fixture_code": "6b777ac34d959e933b4989aa6075c155213fff37a5b2a8e764d7b7a8ba7e07f1",
    "task4_fixture_name": "f65f61996dc467a0a946081fe13bad7048617a6cfd9355dc1d6db0c1bca4577e",
    "task4_fixture_text": "fe5b56cd97570d764cf1645a758096f3cfca332dae544fd8cb54e18623ca7b8b",
    "task4_create_file": "6766b6f1dbffc911cf9cd9fbcb1dba9b57c8ad1c352c2650b2b6d8926c97c786",
    "task4_set_metadata": "4ffc723935fc8459c197095d23416e8c8b8e1b61149d52501af91b13aa622773",
    "task4_prepare_query": "38f9fd5789524b044f0bf8a2adebf6608d85e7ddeb50339e36454f95ca55f919",
    "task4_hit_matches": "d20492081d3d299804831e8ee4a7684c41d6ce45aa912f0b03e0c61a74eb1ee5",
    "task4_query_semantic": "73bf2d612ba98213c13e8509a2301b2d06aabeea561b4c34f1adf4bcc99c055a",
    "task4_delete_metadata": "56024658bc4be4dbcdcbcda69fdcccb5db2511a960f35e0cf78a8b7977ceb653",
    "run_functional_task4": "659552807589f250eaed57140d23e1606f691a6ac2a3eff7efb741e3202841a0",
    "run_functional_sentinel": "cad2eb31ffc3c60fedeb8691b658ec250b864760e94485ed3d43ab8db29b08e1",
    "run_functional_waiter": "8e94f140be6ab5852698675798d88ab78df71353caa56568b9734311aa6f4975",
    "run_functional_task5": "1c106e89c9708c5dee40b5450a11efeb19d807e84779bb085b3511f93d9ffc23",
}


def _definition_tokens(tokens: list[str], name: str) -> list[str]:
    definitions: list[tuple[int, int]] = []
    depth = 0
    for index, token in enumerate(tokens):
        if (
            depth == 0
            and token == name
            and index + 1 < len(tokens)
            and tokens[index + 1] == "("
        ):
            close = _matching(tokens, index + 1, "(", ")")
            if close + 1 < len(tokens) and tokens[close + 1] == "{":
                end = _matching(tokens, close + 1, "{", "}")
                definitions.append((index, end + 1))
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
    if len(definitions) != 1:
        raise ValueError(f"functional helper must have one definition: {name}")
    start, end = definitions[0]
    return tokens[start:end]


def _definition_fingerprint(tokens: list[str], name: str) -> str:
    encoded = json.dumps(
        _definition_tokens(tokens, name),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _top_level_definitions(tokens: list[str]) -> dict[str, list[str]]:
    definitions: dict[str, list[str]] = {}
    depth = 0
    for index, token in enumerate(tokens):
        if (
            depth == 0
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token)
            and index + 1 < len(tokens)
            and tokens[index + 1] == "("
        ):
            close = _matching(tokens, index + 1, "(", ")")
            if close + 1 < len(tokens) and tokens[close + 1] == "{":
                end = _matching(tokens, close + 1, "{", "}")
                if token in definitions:
                    raise ValueError(f"duplicate top-level function: {token}")
                definitions[token] = tokens[close + 2:end]
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
    if depth != 0:
        raise ValueError("functional translation unit braces are unbalanced")
    return definitions


def _normalized_calls(body: list[str]) -> tuple[str, ...]:
    calls: list[str] = []
    keywords = {"for", "if", "sizeof", "switch", "while"}
    for index, token in enumerate(body):
        if (
            token == "("
            and index + 3 < len(body)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", body[index + 1])
            and body[index + 2:index + 4] == [")", "("]
        ):
            calls.append(body[index + 1])
        elif (
            token == "("
            and index + 4 < len(body)
            and body[index + 1] == "*"
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", body[index + 2])
            and body[index + 3:index + 5] == [")", "("]
        ):
            calls.append(f"*{body[index + 2]}")
        elif (
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token)
            and token not in keywords
            and index + 1 < len(body)
            and body[index + 1] == "("
        ):
            calls.append(token)
    return tuple(calls)


def _concatenated_strings(tokens: list[str]) -> tuple[str, ...]:
    strings: list[str] = []
    index = 0
    while index < len(tokens):
        if not tokens[index].startswith('"'):
            index += 1
            continue
        value = ""
        while index < len(tokens) and tokens[index].startswith('"'):
            parsed = ast.literal_eval(tokens[index])
            if not isinstance(parsed, str):
                raise ValueError("functional output contains a non-text literal")
            value += parsed
            index += 1
        strings.append(value)
    return tuple(strings)


def _all_functions() -> tuple[str, ...]:
    names = tuple(name for group in FUNCTION_GROUPS.values() for name in group)
    if len(names) != len(set(names)):
        raise ValueError("functional source contract repeats a helper")
    return names


def _statement_rhs(body: list[str], prefix: tuple[str, ...], label: str) -> tuple[str, ...]:
    positions = _locations(body, prefix)
    if len(positions) != 1:
        raise ValueError(f"{label} must be assigned exactly once")
    start = positions[0] + len(prefix)
    paren = bracket = brace = 0
    for end in range(start, len(body)):
        token = body[end]
        if token == "(":
            paren += 1
        elif token == ")":
            paren -= 1
        elif token == "[":
            bracket += 1
        elif token == "]":
            bracket -= 1
        elif token == "{":
            brace += 1
        elif token == "}":
            brace -= 1
        elif token == ";" and paren == bracket == brace == 0:
            return tuple(body[start:end])
    raise ValueError(f"{label} assignment is unterminated")


def _value_assignments(
    body: list[str], count: int, label: str, *, array: str = "values"
) -> dict[int, tuple[str, ...]]:
    assignments: dict[int, tuple[str, ...]] = {}
    for index in range(len(body) - 5):
        if (
            body[index] == array
            and body[index + 1] == "["
            and body[index + 2].isdigit()
            and body[index + 3:index + 5] == ["]", "="]
        ):
            slot = int(body[index + 2])
            if slot in assignments:
                raise ValueError(f"{label} receipt slot {slot} is assigned twice")
            assignments[slot] = _statement_rhs(
                body,
                tuple(body[index:index + 5]),
                f"{label} receipt slot {slot}",
            )
    if set(assignments) != set(range(count)):
        raise ValueError(f"{label} receipt slots are incomplete or out of range")
    return assignments


def _require_rhs(
    assignments: dict[int, tuple[str, ...]],
    slot: int,
    expected: tuple[str, ...],
    label: str,
) -> None:
    if assignments[slot] != expected:
        raise ValueError(f"{label} receipt slot {slot} lost dynamic provenance")


def _require_call_count(body: list[str], name: str, count: int, label: str) -> None:
    actual = len(_locations(body, (name, "(")))
    if actual != count:
        raise ValueError(f"{label} must call {name} exactly {count} time(s)")


def _require_sequence(body: list[str], sequence: tuple[str, ...], label: str) -> int:
    locations = _locations(body, sequence)
    if len(locations) != 1:
        raise ValueError(f"{label} must occur exactly once")
    return locations[0]


def _forbid_member_assignment(body: list[str], root: str, label: str) -> None:
    for index in range(len(body) - 3):
        if (
            body[index] == root
            and body[index + 1] in {".", "->"}
            and body[index + 3] == "="
        ):
            raise ValueError(f"{label} overwrites a kernel-produced field")


def _require_final_return(
    body: list[str], expression: tuple[str, ...], label: str, *, returns: int = 1
) -> None:
    statement = ("return",) + expression + (";",)
    position = _require_sequence(body, statement, label)
    if body[position:] != list(statement) or body.count("return") != returns or "goto" in body:
        raise ValueError(f"{label} must be the only final return")


def _validate_primitives(tokens: list[str]) -> None:
    check = _function_tokens(tokens, "check")
    _require_sequence(check, ("if", "(", "!", "ok", ")", "{"), "functional failure branch")
    _require_sequence(
        check,
        ("printf", "(", '"agenteval_ucore: check failed: %s\\n"', ",", "message", ")", ";"),
        "functional failure diagnostic",
    )
    _require_sequence(check, ("exit", "(", "1", ")", ";"), "functional failure exit")
    if "return" in check or "goto" in check or len(_locations(check, ("exit", "("))) != 1:
        raise ValueError("functional check can bypass a failed assertion")

    byte_hash = _function_tokens(tokens, "hash_bytes")
    for sequence, label in (
        (("for", "(", "int", "i", "=", "0", ";", "i", "<", "length", ";", "i", "++", ")", "{"), "byte hash loop"),
        (("hash", "^", "=", "bytes", "[", "i", "]", ";"), "byte hash input"),
        (("hash", "*", "=", "FNV_PRIME", ";"), "byte hash multiply"),
    ):
        _require_sequence(byte_hash, sequence, label)
    _require_final_return(byte_hash, ("hash",), "byte hash result")

    integer_hash = _function_tokens(tokens, "hash_u64")
    for sequence, label in (
        (("for", "(", "int", "i", "=", "0", ";", "i", "<", "8", ";", "i", "++", ")", "{"), "integer hash loop"),
        (("hash", "^", "=", "(", "unsigned", "char", ")", "(", "value", "&", "0xff", ")", ";"), "integer hash input"),
        (("hash", "*", "=", "FNV_PRIME", ";"), "integer hash multiply"),
        (("value", ">>", "=", "8", ";"), "integer hash shift"),
    ):
        _require_sequence(integer_hash, sequence, label)
    _require_final_return(integer_hash, ("hash",), "integer hash result")

    equality = _function_tokens(tokens, "bytes_equal")
    _require_sequence(
        equality,
        ("for", "(", "int", "i", "=", "0", ";", "i", "<", "length", ";", "i", "++", ")", "{"),
        "byte equality loop",
    )
    _require_sequence(
        equality,
        ("if", "(", "a", "[", "i", "]", "!=", "b", "[", "i", "]", ")", "return", "0", ";"),
        "byte equality mismatch",
    )
    _require_final_return(equality, ("1",), "byte equality success", returns=2)

    formatter = _function_tokens(tokens, "format_hex16")
    for sequence, label in (
        (("for", "(", "int", "i", "=", "15", ";", "i", ">=", "0", ";", "i", "--", ")", "{"), "hex format loop"),
        (("text", "[", "i", "]", "=", "digits", "[", "value", "&", "0xf", "]", ";"), "hex format digit"),
        (("value", ">>", "=", "4", ";"), "hex format shift"),
        (("text", "[", "16", "]", "=", "0", ";"), "hex format terminator"),
    ):
        _require_sequence(formatter, sequence, label)
    if any(token in formatter for token in ("return", "goto", "exit")):
        raise ValueError("hex receipt formatter has an alternate output path")

    challenge_token = _function_tokens(tokens, "semantic_token")
    for sequence, label in (
        (("hash", "=", "hash_bytes", "(", "hash", ",", "domain", ",", "strlen", "(", "domain", ")", ")", ";"), "request domain"),
        (("hash", "=", "hash_u64", "(", "hash", ",", "AGENTEVAL_CHALLENGE", ")", ";"), "request challenge"),
        (("hash", "=", "hash_u64", "(", "hash", ",", "(", "uint64", ")", "load", ")", ";"), "request load"),
        (("hash", "=", "hash_u64", "(", "hash", ",", "(", "uint64", ")", "pair", ")", ";"), "request pair"),
        (("hash", "=", "hash_u64", "(", "hash", ",", "(", "uint64", ")", "item", ")", ";"), "request item"),
    ):
        _require_sequence(challenge_token, sequence, f"functional {label}")
    _require_final_return(
        challenge_token,
        ("hash", "|", "(", "1ULL", "<<", "63", ")"),
        "functional request token",
    )

    value_printer = _function_tokens(tokens, "print_functional_values")
    _require_sequence(
        value_printer,
        ("for", "(", "int", "i", "=", "0", ";", "i", "<", "count", ";", "i", "++", ")"),
        "functional value loop",
    )
    _require_sequence(
        value_printer,
        (
            "printf", "(", '"%s%lld"', ",", "i", "==", "0", "?", '""', ":", '","', ",",
            "(", "long", "long", ")", "values", "[", "i", "]", ")", ";",
        ),
        "functional value serialization",
    )
    if len(_locations(value_printer, ("printf", "("))) != 1 or any(
        token in value_printer for token in ("return", "goto", "exit")
    ):
        raise ValueError("functional values have an alternate serialization path")


def _validate_hash_and_sink(tokens: list[str]) -> None:
    _validate_primitives(tokens)
    semantic = _function_tokens(tokens, "functional_values_semantic")
    for sequence, label in (
        (("hash", "=", "hash_bytes", "(", "hash", ",", "domain", ",", "strlen", "(", "domain", ")", ")", ";"), "semantic domain"),
        (("hash", "=", "hash_u64", "(", "hash", ",", "AGENTEVAL_CHALLENGE", ")", ";"), "semantic challenge"),
        (("hash", "=", "hash_u64", "(", "hash", ",", "values", "[", "i", "]", ")", ";"), "semantic values"),
        (("return", "hash", ";"), "semantic return"),
    ):
        _require_sequence(semantic, sequence, label)
    _require_final_return(semantic, ("hash",), "semantic hash result")

    receipt = _function_tokens(tokens, "functional_receipt")
    for sequence, label in (
        (("hash", "=", "hash_u64", "(", "hash", ",", "AGENTEVAL_CHALLENGE", ")", ";"), "receipt challenge"),
        (("hash", "=", "hash_bytes", "(", "hash", ",", "task", ",", "strlen", "(", "task", ")", ")", ";"), "receipt task"),
        (("hash", "=", "hash_u64", "(", "hash", ",", "values", "[", "i", "]", ")", ";"), "receipt values"),
        (("hash", "=", "hash_u64", "(", "hash", ",", "semantic", ")", ";"), "receipt semantic"),
        (("return", "hash", ";"), "receipt return"),
    ):
        _require_sequence(receipt, sequence, label)
    _require_final_return(receipt, ("hash",), "functional receipt result")

    printer = _function_tokens(tokens, "print_functional_receipt")
    _require_sequence(
        printer,
        (
            "uint64", "receipt", "=", "functional_receipt", "(", "task", ",",
            "values", ",", "count", ",", "semantic", ")", ";",
        ),
        "functional receipt hash",
    )
    _require_sequence(
        printer,
        ("print_functional_values", "(", "values", ",", "count", ")", ";"),
        "functional receipt value serialization",
    )
    _require_sequence(
        printer,
        (
            "printf", "(", '" semantic=%s receipt=%s status=passed\\n"', ",",
            "semantic_text", ",", "receipt_text", ")", ";",
        ),
        "functional receipt terminal marker",
    )
    if (
        len(_locations(printer, ("printf", "("))) != 2
        or len(_locations(printer, ("format_hex16", "("))) != 3
        or len(_locations(printer, ("functional_receipt", "("))) != 1
        or any(token in printer for token in ("return", "goto", "exit"))
    ):
        raise ValueError("functional receipt printer has an alternate output path")

    launcher = _function_tokens(tokens, "print_launcher_receipt")
    _require_sequence(
        launcher,
        (
            "uint64", "receipt", "=", "functional_receipt", "(", '"launcher"',
            ",", "values", ",", "count", ",", "semantic", ")", ";",
        ),
        "launcher receipt hash",
    )
    _require_sequence(
        launcher,
        ("print_functional_values", "(", "values", ",", "count", ")", ";"),
        "launcher receipt value serialization",
    )
    if (
        len(_locations(launcher, ("printf", "("))) != 2
        or len(_locations(launcher, ("format_hex16", "("))) != 3
        or len(_locations(launcher, ("functional_receipt", "("))) != 1
        or any(token in launcher for token in ("return", "goto", "exit"))
    ):
        raise ValueError("launcher receipt printer has an alternate output path")


def _validate_task_receipts(tokens: list[str]) -> dict[str, dict[int, tuple[str, ...]]]:
    result: dict[str, dict[int, tuple[str, ...]]] = {}
    for task, (function, count, domain, count_token) in TASK_SPECS.items():
        body = _function_tokens(tokens, function)
        assignments = _value_assignments(body, count, task)
        semantic = (
            "semantic", "=", "functional_values_semantic", "(",
            f'"{domain}"', ",", "values", ",", count_token, ")", ";",
        )
        publish = (
            "print_functional_receipt", "(", f'"{task}"', ",", "values", ",",
            count_token, ",", "semantic", ")", ";",
        )
        semantic_at = _require_sequence(body, semantic, f"{task} semantic binding")
        publish_at = _require_sequence(body, publish, f"{task} receipt publication")
        if (
            len(_locations(body, ("semantic", "="))) != 1
            or len(_locations(body, ("functional_values_semantic", "("))) != 1
            or len(_locations(body, ("print_functional_receipt", "("))) != 1
        ):
            raise ValueError(f"{task} semantic or receipt is rewritten")
        if semantic_at >= publish_at or body[publish_at:] != list(publish):
            raise ValueError(f"{task} receipt is not the final successful action")
        if any(token in body for token in ("goto", "return")):
            raise ValueError(f"{task} contains an early control-flow escape")
        result[task] = assignments
    return result


def _validate_task1(tokens: list[str], values: dict[int, tuple[str, ...]]) -> None:
    body = _function_tokens(tokens, "run_functional_task1")
    _require_call_count(body, "agent_info", 1, "Task1")
    for slot, expected in {
        2: ("(", "uint64", ")", "(", "uint", ")", "eval_info", ".", "is_agent"),
        3: ("(", "uint64", ")", "(", "uint", ")", "eval_info", ".", "agent_role"),
        5: ("eval_info", ".", "context_base"),
        6: ("eval_info", ".", "context_size"),
        7: ("header", "->", "magic"),
        8: ("header", "->", "version"),
        9: ("header", "->", "capacity"),
        12: ("header", "->", "user_cache_offset"),
        13: ("header", "->", "user_cache_size"),
        14: ("direct_token",),
        15: ("(", "uint64", ")", "(", "uint", ")", "functional_compat_sentinel_pid"),
        16: ("(", "uint64", ")", "(", "long", "long", ")", "functional_compat_sentinel_status"),
    }.items():
        _require_rhs(values, slot, expected, "Task1")
    _require_sequence(body, ("*", "cache", "=", "direct_token", ";"), "Task1 mapped Context write")
    _require_sequence(
        body,
        ("check", "(", "*", "cache", "==", "direct_token", ","),
        "Task1 mapped Context readback",
    )
    if len(_locations(body, ("&", "eval_info"))) != 1:
        raise ValueError("Task1 Agent info output aliases another writer")
    _forbid_member_assignment(body, "eval_info", "Task1 Agent info")
    _require_sequence(
        body,
        (
            "direct_token", "=", "AGENTEVAL_CHALLENGE", "^", "(", "uint64", ")",
            "(", "uint", ")", "getpid", "(", ")", "^", "eval_info", ".",
            "context_base", ";",
        ),
        "Task1 dynamic mapped Context token",
    )

    main = _function_tokens(tokens, "main")
    for name, count in (("agent_info", 1), ("agent_create", 1), ("agent_create_role", 1), ("waitpid", 2)):
        _require_call_count(main, name, count, "Task1 launcher")
    _require_sequence(
        main,
        ("functional_compat_sentinel_pid", "=", "agent_create", "(", ")", ";"),
        "Task1 compatibility Agent creation",
    )
    _require_sequence(
        main,
        ("pid", "=", "agent_create_role", "(", "AGENT_ROLE_ORCHESTRATOR", ")", ";"),
        "Task1 Orchestrator creation",
    )
    launcher_values = _value_assignments(
        main, 5, "Task1 launcher", array="launcher_values"
    )
    for slot, expected in {
        0: ("(", "uint64", ")", "(", "uint", ")", "getpid", "(", ")"),
        1: ("(", "uint64", ")", "(", "uint", ")", "eval_info", ".", "is_agent"),
        2: ("(", "uint64", ")", "(", "uint", ")", "eval_info", ".", "agent_role"),
        3: ("eval_info", ".", "context_base"),
        4: ("eval_info", ".", "context_size"),
    }.items():
        _require_rhs(launcher_values, slot, expected, "Task1 launcher")
    launcher_semantic = _require_sequence(
        main,
        (
            "launcher_semantic", "=", "functional_values_semantic", "(",
            '"task1-launcher-semantic-v1"', ",", "launcher_values", ",", "5", ")", ";",
        ),
        "Task1 launcher semantic binding",
    )
    launcher_publish = _require_sequence(
        main,
        (
            "print_launcher_receipt", "(", "launcher_values", ",", "5", ",",
            "launcher_semantic", ")", ";",
        ),
        "Task1 launcher receipt publication",
    )
    sentinel_create = _require_sequence(
        main,
        ("functional_compat_sentinel_pid", "=", "agent_create", "(", ")", ";"),
        "Task1 compatibility Agent creation order",
    )
    if not launcher_semantic < launcher_publish < sentinel_create:
        raise ValueError("Task1 launcher receipt is not bound before Agent creation")
    if (
        len(_locations(main, ("launcher_semantic", "="))) != 1
        or len(_locations(main, ("print_launcher_receipt", "("))) != 1
    ):
        raise ValueError("Task1 launcher semantic or receipt is rewritten")
    _require_sequence(
        main,
        (
            "waitpid", "(", "functional_compat_sentinel_pid", ",", "&",
            "functional_compat_sentinel_status", ")", "==",
            "functional_compat_sentinel_pid",
        ),
        "Task1 Sentinel status provenance",
    )


def _validate_task2(tokens: list[str], values: dict[int, tuple[str, ...]]) -> None:
    body = _function_tokens(tokens, "run_functional_task2")
    _require_call_count(body, "functional_catalog_load", 1, "Task2")
    _require_call_count(body, "functional_tool_call", 7, "Task2")
    _forbid_member_assignment(body, "functional_response", "Task2 response")
    for slot, expected in {
        2: ("(", "uint64", ")", "(", "uint", ")", "functional_tool_count"),
        3: ("(", "uint64", ")", "(", "uint", ")", "callable_count"),
        5: ("required_mask",),
        6: ("catalog_hash",),
        7: ("core_schema_hash",),
        8: ("functional_response", ".", "sequence"),
        9: ("functional_response", ".", "value0"),
        10: ("functional_response", ".", "value1"),
        11: ("functional_response", ".", "value2"),
        13: ("functional_response", ".", "sequence"),
        14: ("functional_response", ".", "value0"),
        15: ("functional_response", ".", "value1"),
        16: ("functional_response", ".", "value2"),
        18: ("functional_response", ".", "value0"),
        19: ("functional_response", ".", "value1"),
        20: ("functional_response", ".", "value2"),
        22: ("(", "uint64", ")", "(", "long", "long", ")", "functional_response", ".", "status"),
        25: ("(", "uint64", ")", "(", "long", "long", ")", "functional_response", ".", "status"),
        28: ("(", "uint64", ")", "(", "long", "long", ")", "functional_response", ".", "status"),
        31: ("(", "uint64", ")", "(", "long", "long", ")", "functional_response", ".", "status"),
    }.items():
        _require_rhs(values, slot, expected, "Task2")
    calls = _locations(body, ("functional_tool_call", "("))
    requests = _locations(body, ("functional_request_init", "("))
    phases = (
        (8, 9, 10, 11, 12),
        (13, 14, 15, 16),
        (17, 18, 19, 20),
        (21, 22, 23),
        (24, 25, 26),
        (27, 28, 29),
        (30, 31, 32),
    )
    if not (len(calls) == len(requests) == 7):
        raise ValueError("Task2 request/call phases differ")
    for phase, slots in enumerate(phases):
        upper = requests[phase + 1] if phase + 1 < len(requests) else len(body)
        if not requests[phase] < calls[phase] < upper:
            raise ValueError(f"Task2 phase {phase} request/call order differs")
        for slot in slots:
            position = _require_sequence(
                body,
                ("values", "[", str(slot), "]", "="),
                f"Task2 phase {phase} slot {slot}",
            )
            if not calls[phase] < position < upper:
                raise ValueError(
                    f"Task2 slot {slot} is not captured before response reuse"
                )
    call = _function_tokens(tokens, "functional_tool_call")
    _require_sequence(
        call,
        (
            "check", "(", "tool_call", "(", "&", "functional_request", ",", "&",
            "functional_response", ")", "==", "0", ",", "message", ")", ";",
        ),
        "Task2 production tool syscall",
    )
    catalog = _function_tokens(tokens, "functional_catalog_load")
    _require_call_count(catalog, "tool_list", 2, "Task2 catalog")


def _validate_task3(tokens: list[str], values: dict[int, tuple[str, ...]]) -> None:
    body = _function_tokens(tokens, "run_functional_task3")
    calls = {
        name: _locations(body, (name, "("))
        for name in (
            "agent_run",
            "context_snapshot",
            "context_query",
            "context_rollback",
            "context_clear",
            "context_push",
            "context_direct_active_query",
            "bytes_equal",
        )
    }
    for name, count in {
        "agent_run": 2,
        "context_snapshot": 5,
        "context_query": 2,
        "context_rollback": 1,
        "context_clear": 2,
        "context_push": 1,
        "context_direct_active_query": 2,
        "bytes_equal": 2,
    }.items():
        if len(calls[name]) != count:
            raise ValueError(f"Task3 {name} call count differs")

    # 两次逐项比较必须直接作为循环体中的失败关闭检查，不能只保留调用痕迹。
    for limit, message in (
        ("FUNCTIONAL_TASK3_ROUNDS", "task3 syscall and direct query agree"),
        ("active_after_branch", "task3 post-rollback query agreement"),
    ):
        comparison = (
            "for", "(", "int", "i", "=", "0", ";", "i", "<", limit,
            ";", "i", "++", ")", "check", "(", "bytes_equal", "(",
            "&", "functional_context_records", "[", "i", "]", ",", "&",
            "context_results", "[", "i", "]", ",", "sizeof", "(",
            "functional_context_records", "[", "i", "]", ")", ")", ",",
            f'"{message}"', ")", ";",
        )
        position = _require_sequence(body, comparison, f"Task3 {message}")
        if _depth_at(body, position) != 0:
            raise ValueError(f"Task3 {message} is not on the mandatory path")

    for slot, expected in {
        0: ("FUNCTIONAL_TASK3_ROUNDS",),
        1: ("(", "uint64", ")", "(", "uint", ")", "query_count"),
        2: ("(", "uint64", ")", "(", "uint", ")", "direct_count"),
        3: ("first_sequence",),
        4: ("last_sequence",),
        5: ("tool_semantic",),
        6: ("rollback_sequence",),
        7: ("(", "uint64", ")", "(", "uint", ")", "active_after_rollback"),
        8: ("old_branch",),
        9: ("new_branch",),
        10: ("branch_sequence",),
        11: ("rollback_sequence",),
        12: ("(", "uint64", ")", "(", "uint", ")", "post_query_count"),
        13: ("(", "uint64", ")", "(", "uint", ")", "post_direct_count"),
        14: ("(", "uint64", ")", "(", "uint", ")", "clear_count"),
        15: ("capacity",),
        16: ("(", "uint64", ")", "(", "uint", ")", "fifo_count"),
        17: ("functional_context_header", ".", "dropped_records"),
        18: ("functional_context_header", ".", "oldest_sequence"),
        19: ("functional_context_header", ".", "latest_sequence"),
        20: ("functional_context_header", ".", "eviction_policy"),
        21: ("(", "uint64", ")", "(", "uint", ")", "active_after_branch"),
    }.items():
        _require_rhs(values, slot, expected, "Task3")

    provenance = []
    for sequence, label in (
        (("query_count", "=", "context_query", "(", "first_sequence", ",", "functional_context_records", ",", "AGENT_CONTEXT_MAX_RECORDS", ")", ";"), "initial syscall query"),
        (("direct_count", "=", "context_direct_active_query", "(", "eval_info", ".", "context_base", ",", "first_sequence", ",", "context_results", ",", "EVAL_MAX_LOAD", ")", ";"), "initial mapped query"),
        (("rollback_sequence", "=", "functional_context_records", "[", "2", "]", ".", "sequence", ";"), "rollback selector"),
        (("new_branch", "=", "functional_context_header", ".", "branch_generation", ";"), "rollback generation"),
        (("post_query_count", "=", "context_query", "(", "first_sequence", ",", "functional_context_records", ",", "AGENT_CONTEXT_MAX_RECORDS", ")", ";"), "post-rollback syscall query"),
        (("post_direct_count", "=", "context_direct_active_query", "(", "eval_info", ".", "context_base", ",", "first_sequence", ",", "context_results", ",", "EVAL_MAX_LOAD", ")", ";"), "post-rollback mapped query"),
        (("capacity", "=", "functional_context_header", ".", "capacity", ";"), "FIFO capacity"),
    ):
        position = _require_sequence(body, sequence, f"Task3 {label} provenance")
        if _depth_at(body, position) != 0:
            raise ValueError(f"Task3 {label} is not on the mandatory path")
        provenance.append(position)

    snapshots = calls["context_snapshot"]
    order = (
        calls["context_clear"][0],
        calls["agent_run"][0],
        snapshots[0],
        provenance[0],
        provenance[1],
        calls["bytes_equal"][0],
        calls["context_rollback"][0],
        snapshots[1],
        provenance[3],
        calls["agent_run"][1],
        snapshots[2],
        provenance[4],
        provenance[5],
        calls["bytes_equal"][1],
        calls["context_clear"][1],
        snapshots[3],
        provenance[6],
        calls["context_push"][0],
        snapshots[4],
    )
    if tuple(sorted(order)) != order:
        raise ValueError("Task3 production/query/rollback/FIFO order differs")


def _validate_task4(tokens: list[str], values: dict[int, tuple[str, ...]]) -> None:
    body = _function_tokens(tokens, "run_functional_task4")
    for name, count in (
        ("agent_file_query", 4),
        ("agent_run", 1),
        ("task4_delete_metadata", 4),
        ("task4_set_metadata", 1),
        ("task4_create_file", 1),
    ):
        _require_call_count(body, name, count, "Task4")
    _forbid_member_assignment(body, "file_result", "Task4 query result")
    _forbid_member_assignment(body, "digest_result", "Task4 digest result")
    for slot, expected in {
        4: ("(", "uint64", ")", "(", "uint", ")", "file_result", ".", "total_hits"),
        5: ("(", "uint64", ")", "(", "uint", ")", "file_result", ".", "returned"),
        9: ("(", "uint64", ")", "(", "uint", ")", "file_result", ".", "hits", "[", "0", "]", ".", "fid"),
        11: ("file_result", ".", "hits", "[", "0", "]", ".", "dev"),
        22: ("task4_query_semantic", "(", '"task4-attributes-v2"', ",", "&", "file_query", ",", "&", "file_result", ")"),
        23: ("(", "uint64", ")", "(", "uint", ")", "file_result", ".", "total_hits"),
        35: ("task4_query_semantic", "(", '"task4-summary-v2"', ",", "&", "file_query", ",", "&", "file_result", ")"),
        36: ("digest_op", "->", "request_id"),
        37: ("digest_result", "->", "request_id"),
        38: ("digest_result", "->", "sequence"),
        39: ("(", "uint64", ")", "(", "long", "long", ")", "digest_result", "->", "status"),
        43: ("digest_result", "->", "value2"),
        45: ("(", "uint64", ")", "(", "long", "long", ")", "task4_delete_metadata", "(", "fid_base", ",", "names", "[", "0", "]", ",", "values", "[", "11", "]", ",", "values", "[", "12", "]", ",", "values", "[", "13", "]", ")"),
        46: ("(", "uint64", ")", "(", "uint", ")", "file_result", ".", "total_hits"),
        50: ("task4_query_semantic", "(", '"task4-delete-one-v2"', ",", "&", "file_query", ",", "&", "file_result", ")"),
        51: ("(", "uint64", ")", "(", "long", "long", ")", "task4_delete_metadata", "(", "fid_base", "+", "1", ",", "names", "[", "1", "]", ",", "values", "[", "16", "]", ",", "values", "[", "17", "]", ",", "values", "[", "18", "]", ")"),
        52: ("(", "uint64", ")", "(", "uint", ")", "file_result", ".", "total_hits"),
        55: ("task4_query_semantic", "(", '"task4-delete-all-v2"', ",", "&", "file_query", ",", "&", "file_result", ")"),
    }.items():
        _require_rhs(values, slot, expected, "Task4")
    queries = _locations(body, ("agent_file_query", "("))
    digest_calls = _locations(body, ("agent_run", "("))
    create_call = _require_sequence(
        body, ("task4_create_file", "(", "names", "[", "i", "]", ",", "bodies", "[", "i", "]", ")", ";"),
        "Task4 real fixture creation",
    )
    metadata_call = _require_sequence(
        body, ("task4_set_metadata", "(", "fid_base", "+", "i", ",", "names", "[", "i", "]", ","),
        "Task4 real metadata creation",
    )
    if not (len(queries) == 4 and len(digest_calls) == 1 and create_call < metadata_call < queries[0]):
        raise ValueError("Task4 fixture/query production order differs")

    value_positions = {
        slot: _require_sequence(
            body,
            ("values", "[", str(slot), "]", "="),
            f"Task4 receipt slot {slot} position",
        )
        for slot in range(56)
    }
    phase_bounds = (
        (queries[0], queries[1], range(0, 23)),
        (queries[1], digest_calls[0], range(23, 36)),
        (digest_calls[0], value_positions[45], range(36, 45)),
        (queries[2], value_positions[51], range(46, 51)),
        (queries[3], len(body), range(52, 56)),
    )
    for lower, upper, slots in phase_bounds:
        if any(not lower < value_positions[slot] < upper for slot in slots):
            raise ValueError("Task4 receipt values are captured after result reuse")
    if not (
        value_positions[44] < value_positions[45] < queries[2]
        < value_positions[50] < value_positions[51] < queries[3]
        < value_positions[55]
    ):
        raise ValueError("Task4 delete/query generations are not serialized")
    create = _function_tokens(tokens, "task4_create_file")
    for name, count in (("open", 1), ("write", 1), ("close", 1)):
        _require_call_count(create, name, count, "Task4 fixture")
    setter = _function_tokens(tokens, "task4_set_metadata")
    _require_call_count(setter, "agent_file_meta_set", 1, "Task4 metadata set")
    deleter = _function_tokens(tokens, "task4_delete_metadata")
    _require_call_count(deleter, "agent_file_meta_set", 1, "Task4 metadata delete")


def _validate_task5(tokens: list[str], values: dict[int, tuple[str, ...]]) -> None:
    body = _function_tokens(tokens, "run_functional_task5")
    for name, count in (
        ("agent_create", 1),
        ("agent_wait", 5),
        ("agent_info", 9),
        ("agent_heartbeat_set", 2),
        ("agent_heartbeat_stop", 1),
        ("agent_watch", 1),
        ("agent_route_config", 1),
        ("agent_scope_delegate_fd", 1),
        ("waitpid", 1),
    ):
        _require_call_count(body, name, count, "Task5")
    for root in (
        "functional_info_before", "functional_info_after", "functional_event",
        "waiter_info",
    ):
        _forbid_member_assignment(body, root, "Task5 kernel output")
    for slot, expected in {
        1: ("(", "uint64", ")", "(", "uint", ")", "helper_pid"),
        2: ("(", "uint64", ")", "(", "uint", ")", "message_source"),
        3: ("(", "uint64", ")", "(", "uint", ")", "message_target"),
        4: ("corr_id",),
        5: ("message_event_id",),
        6: ("message_event_tick",),
        7: ("functional_info_before", ".", "wait_sleep_count"),
        8: ("message_sleep_after",),
        9: ("functional_info_before", ".", "wait_wakeup_count"),
        10: ("message_wake_after",),
        11: ("functional_event", ".", "tick"),
        12: ("functional_event", ".", "tick"),
        13: ("functional_info_after", ".", "wait_sleep_count", "-", "heartbeat_sleep_before"),
        14: ("functional_info_after", ".", "wait_wakeup_count", "-", "heartbeat_wake_before"),
        15: ("(", "uint64", ")", "(", "long", "long", ")", "timeout_status"),
        16: ("(", "uint64", ")", "(", "long", "long", ")", "helper_status"),
        19: ("functional_info_before", ".", "current_tick"),
        20: ("functional_info_after", ".", "current_tick"),
        21: ("functional_info_before", ".", "sched_dispatch_count"),
        22: ("functional_info_after", ".", "sched_dispatch_count"),
        23: ("functional_info_before", ".", "sched_vruntime"),
        24: ("functional_info_after", ".", "sched_vruntime"),
        25: ("functional_info_before", ".", "wait_loop_count"),
        26: ("functional_info_after", ".", "wait_loop_count"),
        27: ("functional_info_before", ".", "sched_weight"),
    }.items():
        _require_rhs(values, slot, expected, "Task5")
    for sequence, label in (
        (("message_source", "=", "functional_event", ".", "source_pid", ";"), "message source"),
        (("message_target", "=", "functional_event", ".", "target_pid", ";"), "message target"),
        (("message_event_id", "=", "functional_event", ".", "event_id", ";"), "message event id"),
        (("message_event_tick", "=", "functional_event", ".", "tick", ";"), "message event tick"),
        (("message_sleep_after", "=", "functional_info_after", ".", "wait_sleep_count", ";"), "message sleep counter"),
        (("message_wake_after", "=", "functional_info_after", ".", "wait_wakeup_count", ";"), "message wake counter"),
    ):
        _require_sequence(body, sequence, f"Task5 {label} provenance")
    waiter_create = _require_sequence(
        body,
        ("waiter_tid", "=", "thread_create", "(", "run_functional_waiter", ",", "0", ")", ";"),
        "Task5 waiter callback",
    )
    waiter_release = _require_sequence(
        body, ("write", "(", "gate", "[", "1", "]", ",", "&", "start", ",", "1", ")"),
        "Task5 Sentinel release",
    )
    waiter_join = _require_sequence(
        body, ("waittid", "(", "waiter_tid", ")"), "Task5 waiter join"
    )
    if not waiter_create < waiter_release < waiter_join:
        raise ValueError("Task5 waiter callback/join order differs")
    waits = _locations(body, ("agent_wait", "("))
    heartbeat_sets = _locations(body, ("agent_heartbeat_set", "("))
    heartbeat_stop = _require_sequence(
        body,
        ("agent_heartbeat_stop", "(", ")"),
        "Task5 heartbeat stop order",
    )
    message_capture = _require_sequence(
        body,
        ("message_source", "=", "functional_event", ".", "source_pid", ";"),
        "Task5 delayed message capture order",
    )
    tick_one = _require_sequence(
        body, ("values", "[", "11", "]", "="), "Task5 first heartbeat receipt"
    )
    tick_two = _require_sequence(
        body, ("values", "[", "12", "]", "="), "Task5 second heartbeat receipt"
    )
    timeout = _require_sequence(
        body,
        ("timeout_status", "=", "agent_wait", "(", "&", "functional_event", ",", "3", ")", ";"),
        "Task5 stopped-heartbeat timeout provenance",
    )
    timeout_receipt = _require_sequence(
        body, ("values", "[", "15", "]", "="), "Task5 timeout receipt order"
    )
    if not (
        len(waits) == 5
        and len(heartbeat_sets) == 2
        and message_capture < heartbeat_sets[0] < waits[1]
        < tick_one < heartbeat_sets[1] < waits[2] < tick_two
        < heartbeat_stop < waits[3] < timeout < waits[4] < timeout_receipt
    ):
        raise ValueError("Task5 wait/heartbeat result capture order differs")
    waiter = _function_tokens(tokens, "run_functional_waiter")
    for name, count in (("agent_wait", 1), ("exit", 1)):
        _require_call_count(waiter, name, count, "Task5 waiter")
    _require_sequence(
        waiter,
        (
            "task5_wait_status", "=", "agent_wait", "(", "&",
            "functional_event", ",", "50", ")", ";",
        ),
        "Task5 waiter result",
    )
    sentinel = _function_tokens(tokens, "run_functional_sentinel")
    for name, count in (("agent_info", 3), ("read", 1), ("sleep", 1), ("agent_wake", 1)):
        _require_call_count(sentinel, name, count, "Task5 Sentinel")
    gate_read = _require_sequence(
        sentinel,
        ("read", "(", "gate_fd", ",", "&", "gate", ",", "1", ")"),
        "Task5 Sentinel gate read",
    )
    clock_reads = _locations(
        sentinel, ("agent_info", "(", "&", "sentinel_info", ")")
    )
    deadline = _require_sequence(
        sentinel,
        (
            "wake_tick", "=", "sentinel_info", ".", "current_tick", "+",
            "TASK5_DELAY_TICKS", ";",
        ),
        "Task5 Sentinel tick deadline",
    )
    if not (
        len(clock_reads) == 3
        and clock_reads[0] < gate_read < clock_reads[1] < deadline
        < clock_reads[2]
    ):
        raise ValueError("Task5 Sentinel delay clock is stale")
    _require_sequence(
        sentinel,
        (
            "while", "(", "sentinel_info", ".", "current_tick", "<",
            "wake_tick", ")", ";",
        ),
        "Task5 Sentinel tick wait",
    )


def _validate_execution_control(tokens: list[str]) -> None:
    run = _function_tokens(tokens, "run_evaluation")
    calls = [
        _require_sequence(
            run,
            (f"run_functional_task{number}", "(", ")", ";"),
            f"Task{number} execution",
        )
        for number in range(1, 6)
    ]
    expected_calls = {
        "agent_file_meta_init": 1,
        "agent_info": 1,
        "check": 5,
        "context_clear": 1,
        "exit": 1,
        "printf": 1,
        "run_context_access_experiment": 1,
        "run_file_query_experiment": 1,
        "run_functional_task1": 1,
        "run_functional_task2": 1,
        "run_functional_task3": 1,
        "run_functional_task4": 1,
        "run_functional_task5": 1,
        "run_tool_batch_experiment": 1,
        "wait_for_file_scan": 1,
    }
    actual_calls: dict[str, int] = {}
    for call in _normalized_calls(run):
        actual_calls[call] = actual_calls.get(call, 0) + 1
    if actual_calls != expected_calls:
        raise ValueError("functional execution call inventory differs")

    worker = _require_sequence(
        run,
        ("printf", "(", '"agenteval_ucore: worker passed\\n"', ")", ";"),
        "functional worker terminal marker",
    )
    successful_exit = ("exit", "(", "0", ")", ";")
    exits = _locations(run, successful_exit)
    if (
        calls != sorted(calls)
        or any(_depth_at(run, position) != 0 for position in calls)
        or len(exits) != 1
        or not calls[-1] < worker < exits[0]
        or run[exits[0]:] != list(successful_exit)
    ):
        raise ValueError("Task1-5 execution order or successful exit differs")
    if any(token in run for token in ("goto", "return")):
        raise ValueError("functional execution contains an early control-flow escape")

    expected_evidence_strings = {
        "functional": (
            "agenteval_ucore: functional schema=1 task=%s challenge=%s values=",
        ),
        "launcher": (
            "agenteval_ucore: launcher schema=1 challenge=%s values=",
        ),
        "catalog": ("agenteval_ucore: catalog schema=%d challenge=",),
        "worker": ("agenteval_ucore: worker passed\n",),
    }
    strings = _concatenated_strings(tokens)
    for prefix, expected in expected_evidence_strings.items():
        actual = tuple(
            value for value in strings
            if value.startswith(f"agenteval_ucore: {prefix}")
        )
        if actual != expected:
            raise ValueError(f"functional {prefix} output sink inventory differs")
    definitions = _top_level_definitions(tokens)
    all_calls = [
        call for body in definitions.values() for call in _normalized_calls(body)
    ]
    if any(
        all_calls.count(name) != count
        for name, count in (
            ("print_functional_receipt", 5),
            ("print_launcher_receipt", 1),
            ("functional_receipt", 2),
        )
    ):
        raise ValueError("functional receipt callsite inventory differs")


def validate_functional_acceptance_source_text(text: str) -> None:
    # 先校验词法器会擦除的翻译阶段，阻止续行拼接代码或用替代 token 隐藏指令。
    try:
        text.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(
            "functional source must use the reviewed ASCII alphabet"
        ) from error
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
        raise ValueError("functional source contains a control byte")
    if re.search(r"\\[ \t\v\f]*(?:\r\n|\r|\n)", text):
        raise ValueError("functional source uses a preprocessing line splice")
    if re.search(r"\?\?[=/'()!<>-]|%:", text):
        raise ValueError("functional source uses alternate preprocessing tokens")
    tokens = _lex(text)
    names = _all_functions()
    if not SEMANTIC_ONLY_FUNCTIONS < set(names):
        raise ValueError("functional semantic-only helper inventory differs")
    fingerprinted_names = set(names) - SEMANTIC_ONLY_FUNCTIONS
    if set(FUNCTION_FINGERPRINTS) != fingerprinted_names:
        raise ValueError("functional source fingerprint inventory differs")
    for name in names:
        if name in SEMANTIC_ONLY_FUNCTIONS:
            continue
        actual = _definition_fingerprint(tokens, name)
        if actual != FUNCTION_FINGERPRINTS[name]:
            raise ValueError(f"reviewed functional helper differs: {name}")

    _validate_hash_and_sink(tokens)
    assignments = _validate_task_receipts(tokens)
    _validate_task1(tokens, assignments["task1"])
    _validate_task2(tokens, assignments["task2"])
    _validate_task3(tokens, assignments["task3"])
    _validate_task4(tokens, assignments["task4"])
    _validate_task5(tokens, assignments["task5"])
    _validate_execution_control(tokens)
