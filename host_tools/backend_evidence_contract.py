#!/usr/bin/env python3
"""后端证据角色的严格源码与 Guest 日志合同。"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

try:
    from .benchmark_source_contract import (
        _compact,
        _depth_at,
        _extract_call,
        _function_tokens,
        _lex,
        _locations,
        _require_once,
        _require_top_level,
        _split_arguments,
    )
except ImportError:
    from benchmark_source_contract import (
        _compact,
        _depth_at,
        _extract_call,
        _function_tokens,
        _lex,
        _locations,
        _require_once,
        _require_top_level,
        _split_arguments,
    )


class ContractError(RuntimeError):
    pass


CONTRACTS = {
    "plain": {
        "cases": 7,
        "log_prefix": "rp_backend: evidence_role=",
        "log_pattern": re.compile(
            r"rp_backend: evidence_role=demo_reference "
            r"catalog_generation=demo_expected cases=(?P<cases>[1-9][0-9]*) "
            r"status=reference_ready"
        ),
    },
    "agentos": {
        "cases": 8,
        "source_reads": 8,
        "kernel_checks": 4,
        "log_prefix": "rp_backend: evidence_generation=",
        "log_pattern": re.compile(
            r"rp_backend: evidence_generation=runtime "
            r"runtime_cases=(?P<cases>[1-9][0-9]*) "
            r"source_reads=(?P<source_reads>[1-9][0-9]*) "
            r"kernel_checks=(?P<kernel_checks>[1-9][0-9]*) "
            r"context_sequence=(?P<context>[1-9][0-9]*) "
            r"query_returned=(?P<query>[1-9][0-9]*) "
            r"query_used_index=(?P<used_index>[01]) status=verified"
        ),
    },
}

PLAIN_REFERENCE_CASES = (
    "plain-ucore",
    "retry-recovery",
    "user-context",
    "user-fsmeta",
    "user-recovery",
    "user-event",
    "user-audit",
)

AGENT_RUNTIME_SPECS = (
    ("workflow-contract", "rp_wfio", "execution_plan"),
    ("retry-state", "rp_retry_plan", "retry_stage"),
    ("kernel-context", "rp_agentos_kernel", "context_snapshot"),
    ("kernel-file-query", "rp_agentos_query", "metadata_source"),
    ("kernel-recovery", "rp_agentos_recovery", "kernel_tool"),
    ("kernel-event", "rp_agentos_timeline", "event_delivery"),
    ("kernel-audit", "rp_agentos_audit", "audit_source"),
    ("kernel-edit", "rp_agentos_conflict", "holder_write"),
)


def _read_regular(path: Path, label: str) -> str:
    if not path.is_file() or path.is_symlink():
        raise ContractError(f"{label} is missing or unsafe: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ContractError(f"{label} is not readable UTF-8: {error}") from error


def _contract(action) -> None:
    try:
        action()
    except ValueError as error:
        raise ContractError(str(error)) from error


def _validate_main_returns(body: list[str], label: str) -> None:
    returns = [index for index, token in enumerate(body) if token == "return"]
    if not returns or body[-3:] != ["return", "0", ";"]:
        raise ValueError(f"{label} must publish evidence before its sole successful return")
    successful = 0
    for index in returns:
        statement = tuple(body[index:index + 3])
        if statement == ("return", "0", ";"):
            successful += 1
        elif statement != ("return", "1", ";"):
            raise ValueError(f"{label} has a non-fail-closed return")
    if successful != 1 or "goto" in body:
        raise ValueError(f"{label} may not bypass evidence collection")


def _validate_plain_source(text: str) -> None:
    forbidden = (
        "runner_case=",
        "result=passed",
        "input_check=pass",
        "artifact_check=pass",
        "ticks=",
        "runner_passed=",
        "passed_cases=",
    )
    present = [item for item in forbidden if item in text]
    if present:
        raise ValueError(
            "plain demo reference contains forged runtime/performance fields: "
            + ", ".join(present)
        )
    for case in PLAIN_REFERENCE_CASES:
        if text.count(f'"reference_case={case};expected_input=') != 1:
            raise ValueError(f"plain reference case is missing or duplicated: {case}")
    file_envelope = (
        '"evidence_file_role=demo_reference\\n"',
        '"evidence_file_generation=demo_expected\\n"',
        '"evidence_file_status=reference_ready\\n"',
    )
    required = (
        '"reference_cases=7\\n"',
        '"runtime_cases=0\\n"',
        '"runtime_pass_rows=0\\n"',
        '"performance_samples=0\\n"',
        '"reference_case_rows=7\\n"',
        '"reference_report_rows=7\\n"',
    )
    compact = _compact(text)
    for fragment in file_envelope:
        if compact.count(_compact(fragment)) != 1:
            raise ValueError(
                f"plain reference file envelope must appear exactly once: {fragment}"
            )
    for fragment in required:
        if compact.count(_compact(fragment)) < 1:
            raise ValueError(f"plain reference contract fragment is missing: {fragment}")
    marker_fragment = (
        'printf("rp_backend: evidence_role=demo_reference '
        'catalog_generation=demo_expected cases=7 status=reference_ready\\n");'
    )
    if compact.count(_compact(marker_fragment)) != 1:
        raise ValueError("plain reference marker source differs")
    tokens = _lex(text)
    main = _function_tokens(tokens, "main")
    _validate_main_returns(main, "plain backend main")
    marker = [
        index for index, token in enumerate(main)
        if token.startswith('"rp_backend: evidence_role=demo_reference ')
    ]
    if len(marker) != 1 or _depth_at(main, marker[0]) != 0:
        raise ValueError("plain reference marker must be emitted unconditionally")


def _validate_runtime_case_reader(tokens: list[str]) -> None:
    body = _function_tokens(tokens, "append_backend_runtime_case")
    read_call = (
        "rp_evidence_measure_file_field", "(", "spec", "->", "source", ",",
        "spec", "->", "key", ",", "spec", "->", "value", ",", "&",
        "measured", ")",
    )
    read_at = _require_once(body, read_call, "runtime source field read")
    source_at = _require_once(
        body, ("backend_runtime_source_reads", "++", ";"),
        "successful runtime source-read accounting",
    )
    fold_at = _require_once(
        body,
        ("fold_backend_runtime_source", "(", "spec", ",", "&", "measured", ")", ";"),
        "runtime source digest binding",
    )
    case_at = _require_once(
        body, ("backend_runtime_cases", "++", ";"),
        "successful runtime case accounting",
    )
    if not read_at < source_at < fold_at < case_at:
        raise ValueError("runtime source evidence is accounted before it is verified")
    for index in range(len(body) - 3):
        if body[index] == "measured" and body[index + 1] in {".", "->"} and body[index + 3] == "=":
            raise ValueError("runtime source measurement output is overwritten")
    if "goto" in body:
        raise ValueError("runtime source reader contains a bypass")


def _validate_kernel_checks(tokens: list[str]) -> None:
    edit = _function_tokens(tokens, "run_kernel_edit_check")
    for call in (
        ("agent_file_edit_begin", "("),
        ("agent_file_edit_state", "("),
        ("write", "(", "fd", ",", '"A"', ",", "1", ")"),
        ("agent_file_edit_commit", "("),
    ):
        _require_once(edit, call, "kernel edit transaction operation")
    if "goto" in edit:
        raise ValueError("kernel edit check contains a bypass")

    body = _function_tokens(tokens, "run_kernel_backend_check")
    calls = (
        ("agent_run", "(", "&", "backend_op", ",", "&", "backend_result", ",", "1", ",", "0", ")"),
        ("context_snapshot", "(", "&", "backend_header", ",", "backend_records", ",", "8", ")"),
        ("agent_file_query", "(", "&", "backend_query", ",", "&", "backend_query_result", ")"),
        ("run_kernel_edit_check", "(", ")"),
    )
    call_positions = [_require_once(body, call, "runtime kernel check") for call in calls]
    increments = _locations(body, ("backend_runtime_kernel_checks", "++", ";"))
    if len(increments) != len(calls):
        raise ValueError("runtime kernel-check accounting is not one-to-one")
    for index, (call_at, increment_at) in enumerate(zip(call_positions, increments)):
        next_call = call_positions[index + 1] if index + 1 < len(call_positions) else len(body)
        if not call_at < increment_at < next_call:
            raise ValueError("runtime kernel check is accounted before successful validation")
    if _locations(body, ("backend_runtime_kernel_checks", "=")) or "goto" in body:
        raise ValueError("runtime kernel-check count can be forged")


_BACKEND_RECEIPT_FIELDS = {
    "runtime_cases": ("backend_runtime_cases",),
    "source_reads": ("backend_runtime_source_reads",),
    "kernel_checks": ("backend_runtime_kernel_checks",),
    "context_sequence": ("(", "int", ")", "backend_header", ".", "latest_sequence"),
    "query_returned": ("backend_query_result", ".", "returned"),
    "query_scanned": ("backend_query_result", ".", "scanned_records"),
    "query_used_index": ("backend_query_result", ".", "used_index"),
    "echo_request_id": ("(", "int", ")", "backend_op", ".", "request_id"),
    "echo_status": ("backend_result", ".", "status"),
}


def _validate_backend_receipt(tokens: list[str]) -> None:
    body = _function_tokens(tokens, "make_backend_runtime_receipt")
    if body[-3:] != ["return", "receipt", ";"] or body.count("return") != 1 or "goto" in body:
        raise ValueError("backend runtime receipt has an early control-flow escape")
    for field, expression in _BACKEND_RECEIPT_FIELDS.items():
        prefix = ("receipt", ".", field, "=")
        if len(_locations(body, prefix)) != 1:
            raise ValueError(f"backend receipt field {field} must be assigned exactly once")
        _require_top_level(
            body, prefix + expression + (";",),
            f"backend receipt field {field} provenance",
        )


def _validate_agent_main(text: str, tokens: list[str]) -> None:
    main = _function_tokens(tokens, "main")
    _validate_main_returns(main, "AgentOS backend main")
    _require_top_level(
        main,
        (
            "const", "struct", "backend_runtime_receipt", "backend_receipt",
            "=", "make_backend_runtime_receipt", "(", ")", ";",
        ),
        "immutable backend runtime receipt",
    )
    _require_top_level(
        main,
        ("append_backend_runtime_case", "(", "&", "backend_runtime_specs", "[", "i", "]", ")"),
        "runtime source-case loop",
    )
    for global_name in (
        "backend_runtime_cases",
        "backend_runtime_source_reads",
        "backend_runtime_kernel_checks",
    ):
        if _locations(main, (global_name, "=")) or _locations(main, (global_name, "++")):
            raise ValueError(f"AgentOS main forges {global_name}")
    for index in range(len(main) - 3):
        if main[index] == "backend_receipt" and main[index + 1] == "." and main[index + 3] == "=":
            raise ValueError("immutable backend runtime receipt is overwritten")
    if _locations(main, ("&", "backend_receipt")):
        raise ValueError("immutable backend runtime receipt address escapes")

    prefix = "rp_backend: evidence_generation=runtime "
    if text.count(prefix) != 1:
        raise ValueError("AgentOS backend must contain one runtime marker")
    marker_at = text.index(prefix)
    call_at = text.rfind("printf(", 0, marker_at)
    if call_at < 0:
        raise ValueError("AgentOS runtime marker printf is missing")
    arguments = _split_arguments(_extract_call(text, call_at + len("printf")))
    expected_format = (
        '"rp_backend: evidence_generation=runtime runtime_cases=%d '
        'source_reads=%d kernel_checks=%d context_sequence=%d query_returned=%d '
        'query_used_index=%d status=verified\\n"'
    )
    expected_arguments = (
        "backend_receipt.runtime_cases",
        "backend_receipt.source_reads",
        "backend_receipt.kernel_checks",
        "backend_receipt.context_sequence",
        "backend_receipt.query_returned",
        "backend_receipt.query_used_index",
    )
    if _compact(arguments[0]) != _compact(expected_format) or len(arguments) != 7:
        raise ValueError("AgentOS runtime marker schema differs")
    for actual, expected in zip(arguments[1:], expected_arguments):
        if _compact(actual) != _compact(expected):
            raise ValueError(f"AgentOS runtime marker is not bound to {expected}")
    marker_tokens = [
        index for index, token in enumerate(main)
        if token.startswith('"rp_backend: evidence_generation=runtime ')
    ]
    if len(marker_tokens) != 1 or _depth_at(main, marker_tokens[0]) != 0:
        raise ValueError("AgentOS runtime marker must be emitted unconditionally")


def _validate_agent_source(text: str) -> None:
    tokens = _lex(text)
    compact = _compact(text)
    for case_name, source, key in AGENT_RUNTIME_SPECS:
        spec_prefix = _compact(f'{{"{case_name}", "{source}", "{key}",')
        if compact.count(spec_prefix) != 1:
            raise ValueError(f"runtime source spec is missing or duplicated: {case_name}")
    counter_increments = {
        "backend_runtime_cases": 1,
        "backend_runtime_source_reads": 1,
        "backend_runtime_kernel_checks": 4,
    }
    for counter, expected in counter_increments.items():
        if _locations(tokens, (counter, "=")) or len(
            _locations(tokens, (counter, "++", ";"))
        ) != expected:
            raise ValueError(f"runtime counter {counter} can be forged")
    for output in ("backend_result", "backend_header", "backend_query_result"):
        for index in range(len(tokens) - 3):
            if tokens[index] == output and tokens[index + 1] == "." and tokens[index + 3] == "=":
                raise ValueError(f"runtime kernel output {output} is overwritten")
    _validate_runtime_case_reader(tokens)
    _validate_kernel_checks(tokens)
    _validate_backend_receipt(tokens)
    _validate_agent_main(text, tokens)


def validate_source(target: str, path: Path) -> None:
    text = _read_regular(path, f"{target} backend source")
    _contract(lambda: _validate_plain_source(text) if target == "plain" else _validate_agent_source(text))


def parse_log(target: str, path: Path) -> dict[str, int]:
    contract = CONTRACTS[target]
    lines = _read_regular(path, f"{target} Guest log").splitlines()
    candidates = [line for line in lines if line.startswith(contract["log_prefix"])]
    if len(candidates) != 1:
        raise ContractError(
            f"{target} Guest log must contain exactly one backend evidence marker"
        )
    match = contract["log_pattern"].fullmatch(candidates[0])
    if match is None:
        raise ContractError(f"{target} Guest backend evidence marker is malformed")
    values = {key: int(value) for key, value in match.groupdict().items()}
    if values["cases"] != contract["cases"]:
        raise ContractError(
            f"{target} Guest backend cases differ: {values['cases']} != {contract['cases']}"
        )
    if target == "agentos" and (
        values["source_reads"] != contract["source_reads"]
        or values["kernel_checks"] != contract["kernel_checks"]
        or values["used_index"] != 1
    ):
        raise ContractError("AgentOS backend receipt counters differ from the contract")
    return values


def summary(target: str, cases: int | None = None) -> str:
    expected = int(CONTRACTS[target]["cases"])
    if cases is not None and cases != expected:
        raise ContractError(f"{target} summary cases differ from the contract")
    if target == "plain":
        return f"plain backend reference: expected_cases={expected} runtime_cases=0"
    return f"AgentOS backend runtime: cases={expected} source_reads={expected} kernel_checks=4"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("verify-source", "verify-log"))
    parser.add_argument("--target", choices=tuple(CONTRACTS), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    try:
        validate_source(args.target, args.source)
        values = None
        if args.mode == "verify-log":
            if args.log is None:
                raise ContractError("verify-log requires --log")
            values = parse_log(args.target, args.log)
        elif args.log is not None:
            raise ContractError("verify-source does not accept --log")
        print(summary(args.target, None if values is None else values["cases"]))
    except ContractError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
