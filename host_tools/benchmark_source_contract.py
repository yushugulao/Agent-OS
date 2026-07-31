#!/usr/bin/env python3
"""Fail closed unless the file-query receipt has measured provenance."""
from __future__ import annotations

import re
from pathlib import Path

BENCHMARK_SOURCE = Path(__file__).resolve().parents[1] / "user" / "src" / "agentbench_ucore.c"
MARKER_PREFIX = "agentbench_ucore: file_query_benchmark "
FIELD_SPEC = (
    ("schema", "2", None),
    ("unit", "us", None),
    ("load", "%d", "file_query_receipt.load"),
    ("traversal_ops", "%d", "file_query_receipt.traversal_ops"),
    ("traversal_records", "%d", "file_query_receipt.traversal_records"),
    ("traversal_duration_us", "%d", "file_query_receipt.traversal_duration_us"),
    ("cold_index_ops", "%d", "file_query_receipt.cold_index_ops"),
    ("cold_index_records", "%d", "file_query_receipt.cold_index_records"),
    ("cold_index_duration_us", "%d", "file_query_receipt.cold_index_duration_us"),
    ("cold_rebuild_records", "%d", "file_query_receipt.cold_rebuild_records"),
    ("cold_rebuild_included", "1", None),
    ("warm_index_ops", "%d", "file_query_receipt.warm_index_ops"),
    ("warm_index_records", "%d", "file_query_receipt.warm_index_records"),
    ("warm_index_duration_us", "%d", "file_query_receipt.warm_index_duration_us"),
    ("status", "measured", None),
)
FIELD_ORDER = tuple(field for field, _, _ in FIELD_SPEC)
FIELD_BINDINGS = {
    field: (("argument", expression) if expression else ("literal", value))
    for field, value, expression in FIELD_SPEC
}
_QUERY_RESULT = ("bench_scratch", ".", "file_query_result")

_TOKEN = re.compile(
    r"(?P<space>\s+)|"
    r"(?P<comment>//[^\r\n]*|/\*.*?\*/)|"
    r'(?P<string>"(?:\\.|[^"\\])*")|'
    r"(?P<char>'(?:\\.|[^'\\])*')|"
    r"(?P<identifier>[A-Za-z_][A-Za-z0-9_]*)|"
    r"(?P<number>0[xX][0-9A-Fa-f]+(?:ULL|LLU|UL|LU|LL|U|L)?|"
    r"[0-9]+(?:ULL|LLU|UL|LU|LL|U|L)?)|"
    r"(?P<operator>==|!=|<=|>=|\+\+|--|&&|\|\||<<|>>|->|[{}()\[\].,;:?~!%^&|*+\-/=<>#])",
    re.S,
)


def _extract_call(text: str, start: int) -> str:
    depth, quote, escaped = 0, "", False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:index]
    raise ValueError("benchmark printf call is unterminated")


def _split_arguments(call: str) -> list[str]:
    values, start, depth, quote, escaped = [], 0, 0, "", False
    for index, char in enumerate(call):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            values.append(call[start:index].strip())
            start = index + 1
    values.append(call[start:].strip())
    return values


def _compact(value: str) -> str:
    return "".join(_lex(value))


def _lex(text: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            raise ValueError(f"benchmark source contains an unsupported token at byte {position}")
        position = match.end()
        if match.lastgroup not in {"space", "comment"}:
            tokens.append(match.group())
    return tokens


def _matching(tokens: list[str], start: int, opening: str, closing: str) -> int:
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index] == opening:
            depth += 1
        elif tokens[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"unbalanced {opening}{closing} in benchmark source")


def _function_tokens(tokens: list[str], name: str) -> list[str]:
    bodies: list[list[str]] = []
    for index, token in enumerate(tokens):
        if token != name or index + 1 >= len(tokens) or tokens[index + 1] != "(":
            continue
        close = _matching(tokens, index + 1, "(", ")")
        if close + 1 < len(tokens) and tokens[close + 1] == "{":
            end = _matching(tokens, close + 1, "{", "}")
            bodies.append(tokens[close + 2:end])
    if len(bodies) != 1:
        raise ValueError(f"benchmark helper must have one definition: {name}")
    return bodies[0]


def _locations(tokens: list[str], sequence: tuple[str, ...]) -> list[int]:
    width = len(sequence)
    return [
        index
        for index in range(len(tokens) - width + 1)
        if tuple(tokens[index:index + width]) == sequence
    ]


def _require_once(tokens: list[str], sequence: tuple[str, ...], label: str) -> int:
    found = _locations(tokens, sequence)
    if len(found) != 1:
        raise ValueError(f"{label} must occur exactly once")
    return found[0]


def _depth_at(tokens: list[str], position: int) -> int:
    depth = 0
    for token in tokens[:position]:
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
    return depth


def _require_top_level(tokens: list[str], sequence: tuple[str, ...], label: str) -> int:
    position = _require_once(tokens, sequence, label)
    if _depth_at(tokens, position) != 0:
        raise ValueError(f"{label} must execute unconditionally")
    return position


def _forbid_control_escape(tokens: list[str], name: str, allow_final_return: bool) -> None:
    forbidden = {"goto", "break", "continue"}
    if any(token in forbidden for token in tokens):
        raise ValueError(f"{name} contains an early control-flow escape")
    returns = [index for index, token in enumerate(tokens) if token == "return"]
    if not allow_final_return and returns:
        raise ValueError(f"{name} contains an early return")
    if allow_final_return and len(returns) != 1:
        raise ValueError(f"{name} must have one final return")


def _validate_clock(tokens: list[str]) -> None:
    now = _function_tokens(tokens, "now_us")
    _require_top_level(
        now,
        ("check", "(", "sys_get_time", "(", "&", "now", ",", "0", ")", "==", "0", ","),
        "microsecond clock syscall",
    )
    _require_top_level(
        now,
        ("return", "now", ".", "sec", "*", "1000000ULL", "+", "now", ".", "usec", ";"),
        "microsecond clock value",
    )
    _forbid_control_escape(now, "now_us", allow_final_return=True)

    elapsed = _function_tokens(tokens, "raw_elapsed_us")
    _require_top_level(elapsed, ("delta", "=", "end", "-", "start", ";"), "raw duration delta")
    _require_top_level(elapsed, ("return", "(", "int", ")", "delta", ";"), "raw duration return")
    if "?" in elapsed or _locations(elapsed, ("return", "1", ";")):
        raise ValueError("raw duration must not floor or synthesize a value")
    _forbid_control_escape(elapsed, "raw_elapsed_us", allow_final_return=True)


def _validate_timed_loop(
    tokens: list[str], name: str, bound: str, result: tuple[str, ...]
) -> None:
    body = _function_tokens(tokens, name)
    if any(item in body for item in ("now_ms", "get_mtime", "elapsed")):
        raise ValueError(f"{name} uses a quantized or floored clock")
    _forbid_control_escape(body, name, allow_final_return=True)
    start_at = _require_top_level(
        body, ("start", "=", "now_us", "(", ")", ";"),
        f"{name} start timestamp",
    )
    duration = (
        "return", "raw_elapsed_us", "(", "start", ",", "now_us", "(", ")", ")", ";"
    )
    duration_at = _require_top_level(
        body,
        duration,
        f"{name} raw duration",
    )
    loop = ("for", "(", "int", "i", "=", "0", ";", "i", "<", bound, ";", "i", "++", ")", "{")
    loop_at = _require_top_level(body, loop, f"{name} operation loop")
    loop_open = loop_at + len(loop) - 1
    loop_close = _matching(body, loop_open, "{", "}")
    call = ("agent_file_query", "(", "&", "bench_file_query_arg", ",") + result + (")",)
    call_at = _require_once(body, call, f"{name} kernel query")
    if not loop_open < call_at < loop_close or _depth_at(body, call_at) != 1:
        raise ValueError(f"{name} kernel query is not in the measured operation loop")
    if not start_at < loop_at < loop_close < duration_at:
        raise ValueError(f"{name} timestamps do not enclose the measured operation loop")
    if body[duration_at:] != list(duration):
        raise ValueError(f"{name} raw duration must be the final statement")


def _validate_cold_query(tokens: list[str]) -> None:
    name = "bench_file_query_cold_us"
    body = _function_tokens(tokens, name)
    if any(item in body for item in ("now_ms", "get_mtime", "elapsed", "for", "while")):
        raise ValueError(f"{name} must measure exactly one query with the raw clock")
    _forbid_control_escape(body, name, allow_final_return=True)
    start_at = _require_top_level(
        body, ("start", "=", "now_us", "(", ")", ";"),
        f"{name} start timestamp",
    )
    query_at = _require_top_level(
        body,
        ("agent_file_query", "(", "&", "bench_file_query_arg", ",", "result", ")"),
        f"{name} kernel query",
    )
    duration = (
        "return", "raw_elapsed_us", "(", "start", ",", "now_us", "(", ")", ")", ";"
    )
    duration_at = _require_top_level(
        body,
        duration,
        f"{name} raw duration",
    )
    if not start_at < query_at < duration_at:
        raise ValueError(f"{name} timestamps do not enclose the measured kernel query")
    if body[duration_at:] != list(duration):
        raise ValueError(f"{name} raw duration must be the final statement")


_RECEIPT_ASSIGNMENTS = {
    "schema": ("FILE_QUERY_MEASUREMENT_SCHEMA",),
    "traversal_ops": ("FILE_OPS",),
    "traversal_duration_us": ("bench_file_query_traversal_us", "(", "AGENT_FILE_QUERY_SCAN", ")"),
    "load": _QUERY_RESULT + (".", "candidate_records"),
    "traversal_records": _QUERY_RESULT + (".", "scanned_records"),
    "traversal_plan": _QUERY_RESULT + (".", "plan"),
    "cold_index_ops": ("1",),
    "cold_index_duration_us": ("bench_file_query_cold_us", "(", "AGENT_FILE_QUERY_USE_INDEX", ",",
                               "AGENT_FILE_QUERY_MAX_HITS", ",", "&") + _QUERY_RESULT + (")",),
    "cold_index_records": _QUERY_RESULT + (".", "scanned_records"),
    "cold_rebuild_records": _QUERY_RESULT + (".", "index_rebuild_records"),
    "warm_index_ops": ("FILE_OPS",),
    "warm_index_duration_us": ("bench_file_query_warm_us", "(", "FILE_OPS", ",", "&") + _QUERY_RESULT + (")",),
    "warm_index_records": _QUERY_RESULT + (".", "scanned_records"),
    "warm_index_plan": _QUERY_RESULT + (".", "plan"),
    "warm_index_candidates": _QUERY_RESULT + (".", "candidate_records"),
    "warm_index_reason": _QUERY_RESULT + (".", "plan_reason"),
    "warm_index_cache_hit": ("(", "receipt", ".", "warm_index_reason", "&",
                             "AGENT_FILE_QUERY_REASON_CACHE_HIT", ")", "!=", "0"),
}


def _validate_receipt_builder(tokens: list[str]) -> None:
    body = _function_tokens(tokens, "measure_file_query_paths")
    _forbid_control_escape(body, "measure_file_query_paths", allow_final_return=True)
    if body[-3:] != ["return", "receipt", ";"]:
        raise ValueError("measurement receipt must be returned only after every check")
    assignments = {}
    for field, expression in _RECEIPT_ASSIGNMENTS.items():
        prefix = ("receipt", ".", field, "=")
        if len(_locations(body, prefix)) != 1:
            raise ValueError(f"receipt field {field} must be assigned exactly once")
        assignments[field] = _require_top_level(
            body, prefix + expression + (";",), f"receipt field {field} provenance"
        )
    clear = (("memset", "(", "&") + _QUERY_RESULT + (",", "0", ",", "sizeof", "(")
             + _QUERY_RESULT + (")", ")", ";"))
    clears = _locations(body, clear)
    traversal = ("traversal_duration_us", "load", "traversal_records", "traversal_plan")
    cold = ("cold_index_ops", "cold_index_duration_us", "cold_index_records", "cold_rebuild_records")
    warm = ("warm_index_ops", "warm_index_duration_us", "warm_index_records", "warm_index_plan",
            "warm_index_candidates", "warm_index_reason", "warm_index_cache_hit")
    if (len(clears) != 2 or not max(assignments[field] for field in traversal) < clears[0]
            < min(assignments[field] for field in cold)
            or not max(assignments[field] for field in cold) < clears[1]
            < min(assignments[field] for field in warm)
            or not assignments["traversal_duration_us"] < min(assignments[field] for field in traversal[1:])
            or not assignments["cold_index_duration_us"] < min(assignments[field] for field in cold[2:])
            or not assignments["warm_index_duration_us"] < min(assignments[field] for field in warm[2:])):
        raise ValueError("kernel query results must be snapshotted before scratch reuse")
    for index in _locations(body, _QUERY_RESULT):
        after = index + len(_QUERY_RESULT)
        if after + 2 < len(body) and body[after] == "." and body[after + 2] == "=":
            raise ValueError("kernel query scratch output must not be overwritten")
    required_checks = (
        ("check", "(", "receipt", ".", "cold_rebuild_records", ">", "0", ","),
        ("check", "(", "!", "receipt", ".", "warm_index_cache_hit", ","),
        ("check", "(", "receipt", ".", "load", ">", "0", "&&", "receipt", ".", "load", "<=", "receipt", ".", "traversal_records", ","),
        ("check", "(", "receipt", ".", "warm_index_candidates", ">", "0", "&&", "receipt", ".", "warm_index_candidates", "<=", "receipt", ".", "warm_index_records", ","),
        ("check", "(", "receipt", ".", "cold_index_records", "==", "receipt", ".", "warm_index_records", ","),
        ("check", "(") + _QUERY_RESULT + (".", "index_rebuild_records", "==", "0", ","),
    )
    for sequence in required_checks:
        _require_top_level(body, sequence, "file-query receipt invariant")


def _validate_marker(text: str, tokens: list[str]) -> None:
    if text.count(MARKER_PREFIX) != 1:
        raise ValueError("benchmark source must contain one file-query marker")
    marker_at = text.index(MARKER_PREFIX)
    call_at = text.rfind("printf(", 0, marker_at)
    if call_at < 0:
        raise ValueError("benchmark marker printf is missing")
    arguments = _split_arguments(_extract_call(text, call_at + len("printf")))
    format_text = "".join(re.findall(r'"((?:\\.|[^"\\])*)"', arguments[0]))
    expected_format = MARKER_PREFIX + " ".join(
        f"{field}={value}" for field, value, _ in FIELD_SPEC
    ) + r"\n"
    if format_text != expected_format:
        raise ValueError("benchmark marker fields, literals, units, or order differ from schema")
    expected_arguments = [(field, expression) for field, _, expression in FIELD_SPEC if expression]
    if len(arguments) != len(expected_arguments) + 1:
        raise ValueError("benchmark printf value count differs from schema")
    for (field, expected), actual in zip(expected_arguments, arguments[1:]):
        if _compact(actual) != _compact(expected):
            raise ValueError(f"benchmark field {field} is not bound to {expected}")

    run = _function_tokens(tokens, "run_agent_bench")
    _forbid_control_escape(run, "run_agent_bench", allow_final_return=False)
    _require_top_level(run, ("exit", "(", "0", ")", ";"), "final successful exit")
    if run[-5:] != ["exit", "(", "0", ")", ";"] or "_exit" in run:
        raise ValueError("run_agent_bench may only exit after publishing the receipt")
    _require_top_level(
        run,
        (
            "const", "struct", "file_query_measurement_receipt", "file_query_receipt",
            "=", "measure_file_query_paths", "(", ")", ";",
        ),
        "immutable measurement receipt",
    )
    if len(_locations(run, ("measure_file_query_paths", "(", ")"))) != 1:
        raise ValueError("measurement receipt must be collected exactly once")
    marker_tokens = [
        index for index, token in enumerate(run)
        if token.startswith('"agentbench_ucore: file_query_benchmark ')
    ]
    if len(marker_tokens) != 1 or _depth_at(run, marker_tokens[0]) != 0:
        raise ValueError("benchmark marker must be emitted unconditionally")
    for index in range(len(run) - 3):
        if (
            run[index] == "file_query_receipt"
            and run[index + 1] == "."
            and run[index + 3] == "="
        ):
            raise ValueError("immutable measurement receipt is overwritten")
    if _locations(run, ("&", "file_query_receipt")):
        raise ValueError("immutable measurement receipt address must not escape")

def validate_benchmark_source(path: Path = BENCHMARK_SOURCE) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"benchmark source is unavailable: {error}") from error
    tokens = _lex(text)
    if len(_locations(tokens, ("#", "define", "FILE_OPS", "64"))) != 1:
        raise ValueError("FILE_OPS must be the reviewed 64-operation workload")
    _validate_clock(tokens)
    _validate_timed_loop(
        tokens, "bench_file_query_traversal_us", "FILE_OPS",
        ("&",) + _QUERY_RESULT,
    )
    _validate_cold_query(tokens)
    _validate_timed_loop(
        tokens, "bench_file_query_warm_us", "operations", ("result",)
    )
    _validate_receipt_builder(tokens)
    _validate_marker(text, tokens)
