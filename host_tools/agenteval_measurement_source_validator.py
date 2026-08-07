#!/usr/bin/env python3
"""Token-level validators for AgentOS headline measurement sources."""
from __future__ import annotations

import re
from pathlib import Path

if __package__:
    from .benchmark_source_contract import (
        _depth_at, _function_tokens, _lex, _locations, _matching,
        _require_once, _require_top_level,
    )
    from .functional_acceptance_source_contract import (
        CONTRACT_VERSION as FUNCTIONAL_CONTRACT_VERSION,
        validate_functional_acceptance_source_text,
    )
    from .agenteval_measurement_source_policy import SOURCE_RELATIVE
else:
    from benchmark_source_contract import (
        _depth_at, _function_tokens, _lex, _locations, _matching,
        _require_once, _require_top_level,
    )
    from functional_acceptance_source_contract import (
        CONTRACT_VERSION as FUNCTIONAL_CONTRACT_VERSION,
        validate_functional_acceptance_source_text,
    )
    from agenteval_measurement_source_policy import SOURCE_RELATIVE


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / SOURCE_RELATIVE
CONTRACT_VERSION = "agenteval-measurement-source-v11"
PRINT_FORMAT = (
    r'"agenteval_ucore: sample schema=2 experiment=%s load=%d pair=%d '
    r'variant=%s order=%s cache=%s operations=%d dataset_size=%d '
    r'work_units=%llu records_examined=%llu result_items=%llu duration_us=%llu '
    r'index_rebuild_records=%llu result_cache_hits=%llu '
    r'workload_fingerprint=%s result_fingerprint=%s status=measured\n"'
)
START = ("start", "=", "now_us", "(", ")", ";")
DURATION = (
    "measurement", "->", "duration_us", "=", "elapsed_us", "(",
    "start", ",", "now_us", "(", ")", ")", ";",
)
POSTPROCESSING_CALLS = (
    "check",
    "finalize_agent_file_variant",
    "finalize_path_file_variant",
    "check_path_index_equivalence",
    "finalize_tool_variant",
    "finalize_context_variant",
    "hash_file_semantics",
    "hash_tool_results",
    "hash_context_results",
    "print_sample",
    "validate_context_mirror",
)

# C preprocessing happens before the token-level measurement checks below.  If
# an unreviewed directive were allowed, a macro could make the source spell
# ``now_us()`` while the compiler executes a different clock (or similarly
# redirect a production operation).  Keep the complete preprocessing surface
# of this deliberately small measurement program closed and reviewable.
_APPROVED_DIRECTIVE_TEXT = """\
#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <agenteval_seed.h>
#define EVAL_SCHEMA 1
#define EVAL_PAIRS 7
#define EVAL_LOADS 3
#define EVAL_MAX_LOAD 96
#define EVAL_FILE_QUERIES 16
#define EVAL_PATH_LOADS 4
#define EVAL_PATH_MAX_QUERIES 8
#define EVAL_UNION_LOADS 5
#define EVAL_FILE_RECORD_MAGIC 0x4147464958545552ULL
#define EVAL_FILE_RECORD_SCHEMA 1
#define EVAL_CONTEXT_SELECTOR 0x4147415396722031ULL
#define FUNCTIONAL_TASK3_ROUNDS 6
#define FUNCTIONAL_TOOL_CATALOG_SCHEMA 1
#define TASK4_FUNCTIONAL_FID_BASE 10000
#define TASK4_FUNCTIONAL_FID_STRIDE 4
#define TASK5_DELAY_TICKS 8
#define TASK5_TICK_MSEC 10
#define TASK5_MAX_WAIT_LOOPS 3
#define TASK5_RECEIPT_VALUES 28
#define REVISIT_IDENTITIES 4
#define REVISIT_VISITS 5
#define REVISIT_CONCURRENCY_LEVELS 3
#define REVISIT_ROUNDS 16
#define REVISIT_COMMAND_MAGIC 0x4149524551563031ULL
#define REVISIT_REPLY_MAGIC 0x4149524552503031ULL
#define REVISIT_COMMAND_VISIT 1
#define REVISIT_COMMAND_QUERY 2
#define REVISIT_COMMAND_STOP 3
#define FNV_OFFSET 1469598103934665603ULL
#define FNV_PRIME 1099511628211ULL
#define scan_file_observations (capture.file.scan)
#define index_file_observations (capture.file.index)
#define path_file_observations (capture.file.scan)
#define prepared_file_queries (capture.file.queries)
#define prepared_file_targets (capture.file.targets)
#define tool_ops (capture.tool.ops)
#define scalar_tool_results (capture.tool.scalar_results)
#define batch_tool_results (capture.tool.batch_results)
#define tool_results (capture.tool.scalar_results)
#define syscall_context_results (capture.context.syscall_records)
#define direct_context_results (capture.context.direct_records)
#define syscall_context_query_results (capture.context.syscall_query_results)
#define direct_context_query_results (capture.context.direct_query_results)
#define context_results (capture.context.syscall_records)
"""
APPROVED_PREPROCESSOR_DIRECTIVES = tuple(
    tuple(_lex(line)) for line in _APPROVED_DIRECTIVE_TEXT.splitlines()
)


def _validate_preprocessor_contract(text: str, tokens: list[str]) -> None:
    try:
        text.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(
            "Agent evaluation source must use the reviewed ASCII alphabet"
        ) from error
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
        raise ValueError("Agent evaluation source contains a control byte")
    if re.search(r"\\[ \t\v\f]*(?:\r\n|\r|\n)", text):
        raise ValueError("Agent evaluation source uses a pre-tokenization line splice")
    if (
        _locations(tokens, ("%", ":"))
        or _locations(tokens, ("?", "?", "="))
    ):
        raise ValueError("Agent evaluation source uses an alternate directive token")

    directives = []
    for line in text.splitlines():
        if re.match(r"^[ \t]*#", line):
            directives.append(tuple(_lex(line)))
    if tokens.count("#") != len(directives):
        raise ValueError("Agent evaluation preprocessing directive is obscured")
    if tuple(directives) != APPROVED_PREPROCESSOR_DIRECTIVES:
        raise ValueError("Agent evaluation preprocessing contract differs")


def _split_arguments(tokens: list[str]) -> list[tuple[str, ...]]:
    values: list[tuple[str, ...]] = []
    start = 0
    depth = 0
    pairs = {"(": ")", "[": "]", "{": "}"}
    for index, token in enumerate(tokens):
        if token in pairs:
            depth += 1
        elif token in pairs.values():
            depth -= 1
        elif token == "," and depth == 0:
            values.append(tuple(tokens[start:index]))
            start = index + 1
    values.append(tuple(tokens[start:]))
    return values


def _call_arguments(body: list[str], position: int, name: str) -> list[tuple[str, ...]]:
    if body[position:position + 2] != [name, "("]:
        raise ValueError(f"{name} call position is invalid")
    closing = _matching(body, position + 1, "(", ")")
    return _split_arguments(body[position + 2:closing])


def _measured_loop(
    tokens: list[str], name: str, loop: tuple[str, ...]
) -> tuple[list[str], int, int, int, int]:
    body = _function_tokens(tokens, name)
    start = _require_top_level(body, START, f"{name} start timestamp")
    duration = _require_top_level(body, DURATION, f"{name} raw duration")
    assignments = _locations(body, ("measurement", "->", "duration_us", "="))
    if assignments != [duration]:
        raise ValueError(f"{name} duration must have one measured assignment")
    references = _locations(body, ("measurement", "->", "duration_us"))
    if references != [duration]:
        raise ValueError(f"{name} duration is rewritten or used in arithmetic")
    start_references = sum(token == "start" for token in body)
    if start_references != 3:
        raise ValueError(f"{name} start timestamp is rewritten or substituted")
    loops = [
        position for position in _locations(body, loop)
        if _depth_at(body, position) == 0 and start < position < duration
    ]
    if len(loops) != 1:
        raise ValueError(f"{name} must have one measured production loop")
    loop_at = loops[0]
    loop_open = loop_at + len(loop) - 1
    loop_close = _matching(body, loop_open, "{", "}")
    if not start < loop_at < loop_close < duration:
        raise ValueError(f"{name} timestamps do not enclose its production loop")
    return body, start, loop_open, loop_close, duration


def _inside_loop(
    body: list[str], loop_open: int, loop_close: int,
    operation: tuple[str, ...], label: str,
) -> int:
    position = _require_once(body, operation, label)
    if not loop_open < position < loop_close or _depth_at(body, position) < 1:
        raise ValueError(f"{label} is outside the measured production loop")
    return position


def _forbid_timed_postprocessing(body: list[str], name: str) -> None:
    for call in POSTPROCESSING_CALLS:
        if _locations(body, (call, "(")):
            raise ValueError(f"{name} performs {call} during timed capture")


def _validate_deferred_pair(
    tokens: list[str], run_name: str, time_name: str,
    finalize_name: str | tuple[str, ...],
    time_argument_count: int,
    variants: tuple[
        tuple[str, tuple[tuple[int, tuple[str, ...]], ...]],
        tuple[str, tuple[tuple[int, tuple[str, ...]], ...]],
    ],
    marker_bindings: tuple[tuple[str, str, str], tuple[str, str, str]],
    base_depth: int = 1,
) -> None:
    body = _function_tokens(tokens, run_name)
    timed = _locations(body, (time_name, "("))
    finalize_names = (
        (finalize_name,) if isinstance(finalize_name, str) else finalize_name
    )
    finalized = sorted(
        position
        for name in finalize_names
        for position in _locations(body, (name, "("))
    )
    if len(timed) != 6 or len(finalized) != 4:
        raise ValueError(
            f"{run_name} must time two independent warmup/AB/BA captures "
            "and defer both finalizers"
        )

    warm_timed = [
        position for position in timed
        if _depth_at(body, position) == base_depth
    ]
    warm_finalized = [
        position for position in finalized
        if _depth_at(body, position) == base_depth
    ]
    pair_timed = [
        position for position in timed
        if _depth_at(body, position) == base_depth + 2
    ]
    pair_finalized = [
        position for position in finalized
        if _depth_at(body, position) == base_depth + 1
    ]
    if not (
        len(warm_timed) == len(warm_finalized) == 2
        and len(pair_timed) == 4
        and len(pair_finalized) == 2
        and warm_timed[0] < warm_timed[1] < warm_finalized[0] < warm_finalized[1]
        and max(pair_timed) < pair_finalized[0] < pair_finalized[1]
    ):
        raise ValueError(
            f"{run_name} must finish both timed variants before either finalizer"
        )

    for first, second in (
        (warm_timed[0], warm_timed[1]),
        (pair_timed[0], pair_timed[1]),
        (pair_timed[2], pair_timed[3]),
    ):
        for call in POSTPROCESSING_CALLS:
            if any(first < position < second
                   for position in _locations(body, (call, "("))):
                raise ValueError(
                    f"{run_name} performs {call} between paired timed windows"
                )

    def timed_identity(position: int) -> str:
        arguments = _call_arguments(body, position, time_name)
        if len(arguments) != time_argument_count:
            raise ValueError(f"{run_name} timed call argument count changed")
        matches = [
            identity for identity, constraints in variants
            if all(arguments[index] == expected for index, expected in constraints)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{run_name} timed call is not bound to one real variant capture"
            )
        return matches[0]

    variant_counts = {identity: 0 for identity, _ in variants}
    for position in timed:
        variant_counts[timed_identity(position)] += 1
    if set(variant_counts.values()) != {3}:
        raise ValueError(f"{run_name} does not balance its two capture banks")

    condition = (
        "if", "(", "pair_runs_ab", "(", "pair", ")", ")", "{",
    )
    condition_positions = _locations(body, condition)
    if len(condition_positions) != 2:
        raise ValueError(f"{run_name} must use the real pair_runs_ab branch twice")

    def branches(position: int) -> tuple[tuple[int, int], tuple[int, int]]:
        then_open = position + len(condition) - 1
        then_close = _matching(body, then_open, "{", "}")
        if body[then_close + 1:then_close + 3] != ["else", "{"]:
            raise ValueError(f"{run_name} AB branch lacks its BA alternative")
        else_open = then_close + 2
        else_close = _matching(body, else_open, "{", "}")
        return (then_open, then_close), (else_open, else_close)

    timing_condition: int | None = None
    marker_condition: int | None = None
    timing_ranges: tuple[tuple[int, int], tuple[int, int]] | None = None
    marker_ranges: tuple[tuple[int, int], tuple[int, int]] | None = None
    for position in condition_positions:
        ranges = branches(position)
        has_timing = any(
            start < call < end for start, end in ranges for call in timed
        )
        marker_calls = _locations(body, ("print_sample", "("))
        has_marker = any(
            start < call < end for start, end in ranges for call in marker_calls
        )
        if has_timing and not has_marker:
            timing_condition, timing_ranges = position, ranges
        elif has_marker and not has_timing:
            marker_condition, marker_ranges = position, ranges
        else:
            raise ValueError(f"{run_name} mixes timing and marker order branches")
    if timing_ranges is None or marker_ranges is None:
        raise ValueError(f"{run_name} lacks separate timing and marker branches")

    first_name, second_name = variants[0][0], variants[1][0]

    def identities_in_range(bounds: tuple[int, int]) -> list[str]:
        start, end = bounds
        return [
            timed_identity(position) for position in timed
            if start < position < end
        ]

    if (
        identities_in_range(timing_ranges[0]) != [first_name, second_name]
        or identities_in_range(timing_ranges[1]) != [second_name, first_name]
    ):
        raise ValueError(f"{run_name} real AB/BA timed call order changed")

    marker_calls = _locations(body, ("print_sample", "("))
    if len(marker_calls) != 4:
        raise ValueError(f"{run_name} must print exactly two markers per AB/BA branch")
    marker_by_literal = {
        literal: (identity, measurement)
        for identity, literal, measurement in marker_bindings
    }

    def marker_identity(position: int) -> str:
        arguments = _call_arguments(body, position, "print_sample")
        if len(arguments) != 10 or arguments[4] != ("order",):
            raise ValueError(f"{run_name} marker call shape changed")
        literal = arguments[3]
        if len(literal) != 1 or literal[0] not in marker_by_literal:
            raise ValueError(f"{run_name} marker variant label is not canonical")
        identity, measurement = marker_by_literal[literal[0]]
        if arguments[9] != ("&", measurement):
            raise ValueError(f"{run_name} marker is not bound to its measurement")
        return identity

    def markers_in_range(bounds: tuple[int, int]) -> list[str]:
        start, end = bounds
        return [
            marker_identity(position) for position in marker_calls
            if start < position < end
        ]

    if (
        markers_in_range(marker_ranges[0]) != [first_name, second_name]
        or markers_in_range(marker_ranges[1]) != [second_name, first_name]
        or timing_condition is None
        or marker_condition is None
        or timing_condition >= marker_condition
        or pair_finalized[-1] >= marker_condition
    ):
        raise ValueError(f"{run_name} marker order differs from real AB/BA execution")
    order_binding = (
        "const", "char", "*", "order", "=", "pair_runs_ab", "(", "pair", ")",
        "?", '"AB"', ":", '"BA"', ";",
    )
    if len(_locations(body, order_binding)) != 1:
        raise ValueError(f"{run_name} order label is not bound to pair_runs_ab")
    if body.count("order") != 5 or body.count("pair_runs_ab") != 3:
        raise ValueError(f"{run_name} AB/BA order binding is rewritten")


def _validate_clock(tokens: list[str]) -> None:
    now = _function_tokens(tokens, "now_us")
    _require_top_level(
        now,
        ("check", "(", "sys_get_time", "(", "&", "now", ",", "0", ")", "==", "0", ","),
        "Agent evaluation microsecond clock syscall",
    )
    _require_top_level(
        now,
        ("return", "now", ".", "sec", "*", "1000000ULL", "+", "now", ".", "usec", ";"),
        "Agent evaluation microsecond clock value",
    )
    if sum(token == "now" for token in now) != 4:
        raise ValueError("Agent evaluation clock sample is rewritten")
    elapsed = _function_tokens(tokens, "elapsed_us")
    _require_top_level(
        elapsed,
        ("check", "(", "end", ">=", "start", ","),
        "Agent evaluation monotonic duration guard",
    )
    _require_top_level(
        elapsed, ("return", "end", "-", "start", ";"),
        "Agent evaluation raw clock difference",
    )
    if (
        sum(token == "start" for token in elapsed) != 2
        or sum(token == "end" for token in elapsed) != 2
    ):
        raise ValueError("Agent evaluation elapsed inputs are rewritten")


def _validate_file_query(tokens: list[str]) -> None:
    for declaration, label in (
        (("static", "const", "int", "eval_loads", "[", "EVAL_LOADS", "]",
          "=", "{", "24", ",", "64", ",", "96", "}", ";"),
         "metadata table load declaration"),
        (("static", "const", "int", "eval_path_loads", "[",
          "EVAL_PATH_LOADS", "]", "=", "{", "8", ",", "24", ",",
          "48", ",", "96", "}", ";"), "contest path load declaration"),
        (("static", "const", "int", "eval_path_operations", "[",
          "EVAL_PATH_LOADS", "]", "=", "{", "8", ",", "6", ",",
          "4", ",", "1", "}", ";"), "contest operation declaration"),
        (("static", "const", "int", "eval_union_loads", "[",
          "EVAL_UNION_LOADS", "]", "=", "{", "8", ",", "24", ",",
          "48", ",", "64", ",", "96", "}", ";"),
         "incremental union load declaration"),
    ):
        _require_once(tokens, declaration, label)

    for declaration, label in (
        (("static", "int", "ambient_file_records", ";"),
         "ambient metadata census state"),
        (("static", "int", "expected_visible_file_records", ";"),
         "visible metadata census state"),
    ):
        _require_once(tokens, declaration, label)

    census = _function_tokens(tokens, "census_visible_file_records")
    for operation, label in (
        (("file_query", ".", "flags", "=", "AGENT_FILE_QUERY_SCAN", ";"),
         "forced metadata census selector"),
        (("returned", "=", "agent_file_query", "(", "&", "file_query", ",",
          "&", "file_result", ")", ";"),
         "metadata census production syscall"),
        (("file_result", ".", "scanned_records", "==",
          "AGENT_FILE_META_MAX"), "metadata census full-table proof"),
        (("return", "file_result", ".", "candidate_records", ";"),
         "metadata census observed cardinality"),
    ):
        _require_once(census, operation, label)

    contest_loop = (
        "for", "(", "int", "operation", "=", "0", ";", "operation", "<",
        "operations", ";", "operation", "++", ")", "{",
    )
    body, start, loop_open, loop_close, _ = _measured_loop(
        tokens, "time_file_contest_variant", contest_loop
    )
    selector = _require_top_level(
        body,
        (
            "set_file_query_flags", "(", "operations", ",", "path_walk", "?",
            "0", ":", "AGENT_FILE_QUERY_USE_INDEX", ")", ";",
        ),
        "contest path/index selector",
    )
    if selector >= start:
        raise ValueError("contest path/index selector must precede timing")
    path_branch = _inside_loop(
        body, loop_open, loop_close,
        ("if", "(", "path_walk", ")", "{"),
        "contest path/index branch",
    )
    path_open = path_branch + 4
    path_close = _matching(body, path_open, "{", "}")
    if body[path_close + 1:path_close + 3] != ["else", "{"]:
        raise ValueError("contest indexed query is not the path branch alternative")
    index_open = path_close + 2
    index_close = _matching(body, index_open, "{", "}")
    file_loop = (
        "for", "(", "int", "item", "=", "0", ";", "item", "<", "load",
        ";", "item", "++", ")", "{",
    )
    file_loop_at = _require_once(body, file_loop, "complete N-file traversal loop")
    file_open = file_loop_at + len(file_loop) - 1
    file_close = _matching(body, file_open, "{", "}")
    if not path_open < file_loop_at < file_close < path_close:
        raise ValueError("N-file traversal is outside the measured path variant")
    required_path_operations = (
        (("observation", "->", "scanned_records", "++", ";"),
         "path record accounting"),
        (("open", "(", "name", ",", "O_RDONLY", ")"), "path open"),
        (("read", "(", "fd", ",", "&", "record", ",", "sizeof", "(",
          "record", ")", ")"), "path read"),
        (("fstat", "(", "fd", ",", "&", "status", ")"), "path fstat"),
        (("close", "(", "fd", ")"), "path close"),
        (("eval_file_record_valid", "(", "&", "record", ",", "item", ")"),
         "challenge record validation"),
        (("eval_file_record_matches_query", "(", "&", "record", ",", "&",
          "prepared_file_queries", "[", "operation", "]", ")"),
         "path predicate evaluation"),
    )
    for operation, label in required_path_operations:
        position = _require_once(body, operation, label)
        if not file_open < position < file_close:
            raise ValueError(f"{label} is outside the complete N-file loop")
    if any(token in {"break", "continue", "return"}
           for token in body[file_open + 1:file_close]):
        raise ValueError("N-file traversal can terminate before checking every path")
    index_call = _require_once(
        body,
        ("agent_file_query", "(", "&", "prepared_file_queries", "[",
         "operation", "]", ",", "&", "file_result", ")"),
        "contest indexed production syscall",
    )
    if not index_open < index_call < index_close:
        raise ValueError("indexed syscall is outside the contest index variant")
    _forbid_timed_postprocessing(body, "time_file_contest_variant")

    table_loop = contest_loop
    table, table_start, table_open, table_close, _ = _measured_loop(
        tokens, "time_file_table_variant", table_loop
    )
    table_selector = _require_top_level(
        table,
        ("set_file_query_flags", "(", "operations", ",", "use_index", "?",
         "AGENT_FILE_QUERY_USE_INDEX", ":", "AGENT_FILE_QUERY_SCAN", ")", ";"),
        "metadata table scan/index selector",
    )
    if table_selector >= table_start:
        raise ValueError("metadata table selector must precede timing")
    _inside_loop(
        table, table_open, table_close,
        ("agent_file_query", "(", "&", "prepared_file_queries", "[",
         "operation", "]", ",", "&", "file_result", ")"),
        "metadata table production syscall",
    )
    _forbid_timed_postprocessing(table, "time_file_table_variant")

    _validate_deferred_pair(
        tokens, "run_file_query_path_index", "time_file_contest_variant",
        ("finalize_path_file_variant", "finalize_agent_file_variant"), 5,
        (
            ("path", ((2, ("1",)), (3, ("path_file_observations",)),
                      (4, ("&", "path")))),
            ("index", ((2, ("0",)), (3, ("index_file_observations",)),
                       (4, ("&", "index")))),
        ),
        (("path", '"path_walk"', "path"), ("index", '"index"', "index")),
        base_depth=0,
    )
    _validate_deferred_pair(
        tokens, "run_file_query_table_ablation", "time_file_table_variant",
        "finalize_agent_file_variant", 4,
        (
            ("scan", ((1, ("0",)), (2, ("scan_file_observations",)),
                      (3, ("&", "scan")))),
            ("index", ((1, ("1",)), (2, ("index_file_observations",)),
                       (3, ("&", "index")))),
        ),
        (("scan", '"scan"', "scan"), ("index", '"index"', "index")),
        base_depth=0,
    )

    for run_name in ("run_file_query_path_index", "run_file_query_table_ablation"):
        run = _function_tokens(tokens, run_name)
        if len(_locations(run, ("prepare_file_query_workload", "("))) != 2:
            raise ValueError(f"{run_name} does not share one prepared query workload")
        if len(_locations(run, ("rebuild_file_index_diagnostic", "("))) != 1:
            raise ValueError(f"{run_name} lacks its pre-sample readiness diagnostic")

    for finalize_name in (
        "finalize_agent_file_variant", "finalize_path_file_variant"
    ):
        finalize = _function_tokens(tokens, finalize_name)
        initialize = (
            "measurement", "->", "records_examined", "=", "0", ";",
        )
        accumulate = (
            "measurement", "->", "records_examined", "+", "=",
            "observation", "->", "candidate_records", ";",
        )
        if (
            len(_locations(finalize, initialize)) != 1
            or len(_locations(finalize, accumulate)) != 1
            or len(_locations(
                finalize, ("measurement", "->", "records_examined", "=")
            )) != 1
            or len(_locations(
                finalize, ("measurement", "->", "records_examined", "+", "=")
            )) != 1
        ):
            raise ValueError(
                f"{finalize_name} must derive examined records from observations"
            )

    finalize_agent = _function_tokens(tokens, "finalize_agent_file_variant")
    for operation, label in (
        (("observation", "->", "candidate_records", "==",
          "expected_visible_file_records"),
         "per-query visible metadata census"),
        (("uint64", ")", "(", "uint", ")",
          "expected_visible_file_records", "*", "(", "uint64", ")", "(",
          "uint", ")", "operations"),
         "aggregate visible metadata census"),
    ):
        _require_once(finalize_agent, operation, label)

    dispatcher = _function_tokens(tokens, "run_file_query_experiment")
    union_loop = (
        "for", "(", "int", "i", "=", "0", ";", "i", "<",
        "EVAL_UNION_LOADS", ";", "i", "++", ")", "{",
    )
    union_at = _require_once(dispatcher, union_loop, "incremental union load loop")
    union_open = union_at + len(union_loop) - 1
    union_close = _matching(dispatcher, union_open, "{", "}")
    ambient = _require_once(
        dispatcher,
        ("ambient_file_records", "=", "census_visible_file_records", "(",
         ")", ";"),
        "pre-fixture ambient metadata census",
    )
    if ambient >= union_at:
        raise ValueError("ambient metadata census must precede fixture loads")
    for operation, label in (
        (("before_seed", "=", "census_visible_file_records", "(", ")", ";"),
         "pre-seed metadata census"),
        (("seed_file_metadata", "(", "seeded", ",", "load", ")", ";"),
         "incremental fixture seed"),
        (("after_seed", "=", "census_visible_file_records", "(", ")", ";"),
         "post-seed metadata census"),
        (("after_seed", "-", "before_seed", "==", "load", "-", "seeded"),
         "exact fixture census delta"),
        (("expected_visible_file_records", "=", "after_seed", ";"),
         "timed visible census binding"),
        (("seeded", "=", "load", ";"), "incremental seed frontier"),
        (("run_file_query_path_index", "(", "load", ",", "path_operations", ")", ";"),
         "contest file experiment dispatch"),
        (("run_file_query_table_ablation", "(", "load", ")", ";"),
         "metadata table ablation dispatch"),
    ):
        position = _require_once(dispatcher, operation, label)
        if not union_open < position < union_close:
            raise ValueError(f"{label} is outside the union load loop")


def _validate_tool_batch(tokens: list[str]) -> None:
    loop = ("while", "(", "completed", "<", "load", ")", "{")
    body, _, loop_open, loop_close, _ = _measured_loop(
        tokens, "time_tool_variant", loop
    )
    selector = _inside_loop(
        body, loop_open, loop_close,
        ("count", "=", "batch", "?", "load", "-", "completed", ":", "1", ";"),
        "tool scalar/batch selector",
    )
    operation = _inside_loop(
        body, loop_open, loop_close,
        (
            "agent_run", "(", "&", "tool_ops", "[", "completed", "]", ",",
            "&", "results", "[", "completed", "]", ",", "count", ",", "0", ")",
        ),
        "tool production syscall",
    )
    if selector >= operation:
        raise ValueError("tool variant selection must precede the measured syscall")
    _forbid_timed_postprocessing(body, "time_tool_variant")
    _validate_deferred_pair(
        tokens, "run_tool_batch_experiment", "time_tool_variant",
        "finalize_tool_variant",
        4,
        (
            ("scalar", ((1, ("0",)), (2, ("scalar_tool_results",)),
                        (3, ("&", "scalar")))),
            ("batch", ((1, ("1",)), (2, ("batch_tool_results",)),
                       (3, ("&", "batch")))),
        ),
        (("scalar", '"scalar"', "scalar"), ("batch", '"batch"', "batch")),
    )


def _validate_context_access(tokens: list[str]) -> None:
    loop = ("for", "(", "int", "i", "=", "0", ";", "i", "<", "load", ";", "i", "++", ")", "{")
    body, _, loop_open, loop_close, _ = _measured_loop(
        tokens, "time_context_variant", loop
    )
    branch_sequence = ("if", "(", "direct", ")", "{")
    branch = _inside_loop(
        body, loop_open, loop_close, branch_sequence,
        "context direct/syscall selector",
    )
    direct = _inside_loop(
        body, loop_open, loop_close,
        (
            "query_results", "[", "i", "]", "=",
            "context_direct_active_query", "(",
            "eval_info", ".", "context_base", ",", "target_sequence", ",",
            "&", "results", "[", "i", "]", ",", "1", ")", ";",
        ),
        "mapped context production query",
    )
    syscall = _inside_loop(
        body, loop_open, loop_close,
        (
            "context_query", "(", "target_sequence", ",", "&", "results",
            "[", "i", "]", ",", "1", ")",
        ),
        "context production syscall",
    )
    direct_open = branch + len(branch_sequence) - 1
    direct_close = _matching(body, direct_open, "{", "}")
    if body[direct_close + 1:direct_close + 3] != ["else", "{"]:
        raise ValueError("context syscall variant must be the direct branch else path")
    syscall_open = direct_close + 2
    syscall_close = _matching(body, syscall_open, "{", "}")
    if not direct_open < direct < direct_close:
        raise ValueError("mapped context query is outside the direct variant")
    if not syscall_open < syscall < syscall_close:
        raise ValueError("context syscall is outside the syscall variant")
    _forbid_timed_postprocessing(body, "time_context_variant")
    _validate_deferred_pair(
        tokens, "run_context_access_experiment", "time_context_variant",
        "finalize_context_variant",
        6,
        (
            ("syscall", (
                (1, ("0",)), (3, ("syscall_context_results",)),
                (4, ("syscall_context_query_results",)),
                (5, ("&", "syscall_query")),
            )),
            ("direct", (
                (1, ("1",)), (3, ("direct_context_results",)),
                (4, ("direct_context_query_results",)),
                (5, ("&", "direct")),
            )),
        ),
        (("syscall", '"syscall"', "syscall_query"),
         ("direct", '"direct"', "direct")),
    )


def _validate_revisit_evaluation(tokens: list[str]) -> None:
    _require_once(
        tokens,
        (
            "static", "const", "int", "revisit_sequence", "[",
            "REVISIT_VISITS", "]", "=", "{", "0", ",", "1", ",",
            "2", ",", "3", ",", "0", "}", ";",
        ),
        "revisit A/B/C/D/A sequence",
    )
    _require_once(
        tokens,
        (
            "static", "const", "int", "revisit_concurrency_levels", "[",
            "REVISIT_CONCURRENCY_LEVELS", "]", "=", "{", "1", ",",
            "2", ",", "4", "}", ";",
        ),
        "revisit concurrency levels",
    )
    main = _function_tokens(tokens, "main")
    revisit_call = _require_top_level(
        main, ("run_revisit_evaluation", "(", ")", ";"),
        "revisit evaluation launcher call",
    )
    worker_create = _require_top_level(
        main,
        (
            "pid", "=", "agent_create_role", "(",
            "AGENT_ROLE_ORCHESTRATOR", ")", ";",
        ),
        "headline evaluation worker creation",
    )
    wait_call = _require_top_level(
        main, ("waitpid", "(", "pid", ",", "&", "status", ")"),
        "headline evaluation wait",
    )
    if not worker_create < wait_call < revisit_call:
        raise ValueError("revisit evaluation must run after headline timing")

    run = _function_tokens(tokens, "run_revisit_evaluation")
    start = _require_top_level(
        run, ("revisit_start_workers", "(", ")", ";"),
        "revisit workflow startup",
    )
    visits = _require_top_level(
        run, ("run_revisit_visits", "(", ")", ";"),
        "revisit observation execution",
    )
    stop = _require_top_level(
        run, ("revisit_stop_workers", "(", ")", ";"),
        "revisit workflow shutdown",
    )
    concurrency_calls = _locations(run, ("run_revisit_concurrency", "("))
    if (
        len(concurrency_calls) != 1
        or not start < visits < concurrency_calls[0] < stop
    ):
        raise ValueError("revisit visit/concurrency execution order differs")

    startup = _function_tokens(tokens, "revisit_start_workers")
    _require_once(
        startup,
        (
            "pid", "=", "identity", "==", "0", "?",
            "agent_create_role", "(", "AGENT_ROLE_ORCHESTRATOR", ")", ":",
            "agent_workflow_create", "(", "AGENT_ROLE_ORCHESTRATOR", ")", ";",
        ),
        "revisit inherited/fresh lifecycle admission",
    )
    if len(_locations(startup, ("agent_scope_delegate_fd", "("))) != 2:
        raise ValueError("revisit pipe endpoints are not explicitly delegated")

    observe = _function_tokens(tokens, "revisit_context_observe")
    if (
        len(_locations(observe, ("context_snapshot", "("))) != 1
        or len(_locations(observe, ("contamination", "++", ";"))) != 1
    ):
        raise ValueError("revisit isolation is not derived from Context records")
    classify = _function_tokens(tokens, "revisit_context_record_identity")
    if (
        len(_locations(classify, ("record", "->", "request_id", "==", "expected"))) != 1
        or len(_locations(classify, ("revisit_context_token", "(", "identity", ")"))) != 1
    ):
        raise ValueError("revisit contamination is not bound to peer identity tokens")
    seed = _function_tokens(tokens, "revisit_seed_context")
    if (
        len(_locations(seed, ("context_push", "("))) != 1
        or len(_locations(seed, ("record", ".", "request_id", "=", "token", ";"))) != 1
    ):
        raise ValueError("revisit context seed is not challenge-bound production work")
    unique = _function_tokens(tokens, "revisit_worker_unique")
    for identity_field in (
        ("candidate", "->", "agent_id", "==", "worker", "->", "agent_id"),
        (
            "candidate", "->", "lifecycle_generation", "==", "worker", "->",
            "lifecycle_generation",
        ),
    ):
        if len(_locations(unique, identity_field)) != 1:
            raise ValueError("revisit correctness lost distinct workflow identity")

    visit_body = _function_tokens(tokens, "run_revisit_visits")
    for binding, label in (
        (
            (
                "observed_correct", "=", "revisit_visits", "[", "visit", "]",
                ".", "correct", "&&", "unique", ";",
            ),
            "revisit correct observation",
        ),
        (
            (
                "observed_fallback", "=", "revisit_visits", "[", "visit",
                "]", ".", "fallback", "||", "!", "unique", ";",
            ),
            "revisit fallback observation",
        ),
        (("correct", "+", "=", "observed_correct", ";"), "revisit correct total"),
        (
            ("contamination", "+", "=", "revisit_visits", "[", "visit", "]", ".", "contamination", ";"),
            "revisit contamination total",
        ),
        (("fallback", "+", "=", "observed_fallback", ";"), "revisit fallback total"),
    ):
        _require_once(visit_body, binding, label)

    timed = _function_tokens(tokens, "run_revisit_concurrency")
    timed_start = _require_top_level(
        timed, ("start_us", "=", "now_us", "(", ")", ";"),
        "revisit concurrency start timestamp",
    )
    timed_end = _require_top_level(
        timed, ("end_us", "=", "now_us", "(", ")", ";"),
        "revisit concurrency end timestamp",
    )
    duration = _require_top_level(
        timed,
        (
            "duration_us", "=", "elapsed_us", "(", "start_us", ",",
            "end_us", ")", ";",
        ),
        "revisit concurrency raw duration",
    )
    if not timed_start < timed_end < duration:
        raise ValueError("revisit concurrency timestamps do not enclose requests")
    writes = _locations(timed, ("revisit_write_exact", "("))
    reads = _locations(timed, ("revisit_read_exact", "("))
    if (
        len(writes) != 1 or len(reads) != 1
        or not timed_start < writes[0] < reads[0] < timed_end
    ):
        raise ValueError("revisit concurrency timing does not enclose real IPC")
    worker = _function_tokens(tokens, "run_revisit_worker")
    worker_start = _locations(worker, ("reply", ".", "started_us", "=", "now_us", "(", ")", ";"))
    worker_observe = _locations(worker, ("revisit_context_observe", "("))
    worker_complete = _locations(worker, ("reply", ".", "completed_us", "=", "now_us", "(", ")", ";"))
    if (
        len(worker_start) != 2 or len(worker_observe) != 1
        or len(worker_complete) != 1
        or not worker_start[1] < worker_observe[0] < worker_complete[0]
    ):
        raise ValueError("revisit worker service timestamps differ")
    for field, source in (
        ("submitted_us", ("revisit_perf_sent_us", "[", "index", "]")),
        ("started_us", ("reply", "->", "started_us")),
        ("completed_us", ("reply", "->", "completed_us")),
        ("received_us", ("revisit_perf_received_us", "[", "index", "]")),
    ):
        _require_once(
            timed, ("sample", "->", field, "=", *source, ";"),
            f"revisit {field} provenance",
        )
    for binding, label in (
        (
            ("sample", "->", "wait_us", "=", "elapsed_us", "(",
             "sample", "->", "submitted_us", ",", "sample", "->", "started_us", ")", ";"),
            "revisit wait time",
        ),
        (
            ("sample", "->", "service_us", "=", "elapsed_us", "(",
             "sample", "->", "started_us", ",", "sample", "->", "completed_us", ")", ";"),
            "revisit service time",
        ),
        (
            ("sample", "->", "turnaround_us", "=", "elapsed_us", "(",
             "revisit_perf_sent_us", "[", "index", "]", ",",
             "reply", "->", "completed_us", ")", ";"),
            "revisit turnaround time",
        ),
        (
            (
                "throughput_milli_rps", "=", "(", "uint64", ")", "requests",
                "*", "1000000000ULL", "/", "duration_us", ";",
            ),
            "revisit throughput derivation",
        ),
        (
            (
                "goodput_milli_rps", "=", "(", "uint64", ")", "(", "uint", ")",
                "isolated", "*", "1000000000ULL", "/", "duration_us", ";",
            ),
            "revisit goodput derivation",
        ),
        (
            (
                "avg_milli_us", "=", "turnaround_sum_us", "*", "1000ULL", "/", "(",
                "uint64", ")", "requests", ";",
            ),
            "revisit turnaround average",
        ),
    ):
        position = _require_once(timed, binding, label)
        if position <= duration:
            raise ValueError(f"{label} is computed before real timing completes")
    for metric, values in (
        ("", "revisit_sorted_turnaround_us"),
        ("wait_", "revisit_sorted_wait_us"),
        ("service_", "revisit_sorted_service_us"),
    ):
        for percentile in (50, 90, 99):
            _require_once(
                timed,
                (
                    f"{metric}p{percentile}_us", "=", "revisit_nearest_rank", "(",
                    values, ",", "requests", ",", str(percentile), ")", ";",
                ),
                f"revisit {metric}p{percentile}",
            )
    print_positions = _locations(timed, ("printf", "("))
    if len(print_positions) != 2 or any(position <= duration for position in print_positions):
        raise ValueError("revisit output contaminates its timed interval")


def _validate_duration_output(tokens: list[str]) -> None:
    body = _function_tokens(tokens, "print_sample")
    calls = _locations(body, ("printf", "("))
    if len(calls) != 1 or _depth_at(body, calls[0]) != 0:
        raise ValueError("print_sample must contain one unconditional marker printf")
    arguments = _call_arguments(body, calls[0], "printf")
    direct_duration = (
        "(", "unsigned", "long", "long", ")", "measurement", "->",
        "duration_us",
    )
    if (
        len(arguments) != 17
        or arguments[0] != (PRINT_FORMAT,)
        or arguments[12] != direct_duration
        or len(_locations(body, ("measurement", "->", "duration_us"))) != 1
    ):
        raise ValueError(
            "print_sample must directly serialize measured duration_us in schema 2"
        )
    for name in (
        "finalize_agent_file_variant",
        "finalize_path_file_variant",
        "finalize_tool_variant",
        "finalize_context_variant",
    ):
        if "duration_us" in _function_tokens(tokens, name):
            raise ValueError(f"{name} rewrites or computes with measured duration")


def validate_source_text(text: str) -> None:
    tokens = _lex(text)
    _validate_preprocessor_contract(text, tokens)
    _validate_clock(tokens)
    _validate_file_query(tokens)
    _validate_tool_batch(tokens)
    _validate_context_access(tokens)
    _validate_revisit_evaluation(tokens)
    _validate_duration_output(tokens)
    validate_functional_acceptance_source_text(text)


def validate_source(path: Path = SOURCE) -> None:
    validate_source_text(path.read_text(encoding="utf-8"))
