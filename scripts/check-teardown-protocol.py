#!/usr/bin/env python3
"""Validate the workflow teardown phase protocol from kernel sources."""

import argparse
import json
import re
import sys
from pathlib import Path


PHASES = (
    "VFS_SCOPE_RECLAIM_BEGIN",
    "VFS_SCOPE_RECLAIM_FILES",
    "VFS_SCOPE_RECLAIM_METADATA",
    "VFS_SCOPE_RECLAIM_RETIRE",
    "VFS_SCOPE_RECLAIM_DONE",
)
NEW_RECLAIM_APIS = {
    "agent_scope_reclaim_begin",
    "agent_scope_reclaim_metadata_done",
}


class ProtocolError(RuntimeError):
    pass


def sanitize_c(text):
    """Blank comments and literals while preserving offsets and newlines."""
    out = list(text)
    state = "code"
    quote = ""
    i = 0
    while i < len(text):
        char = text[i]
        next_char = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if char == "/" and next_char == "/":
                out[i] = out[i + 1] = " "
                state = "line"
                i += 2
                continue
            if char == "/" and next_char == "*":
                out[i] = out[i + 1] = " "
                state = "block"
                i += 2
                continue
            if char in ('"', "'"):
                quote = char
                out[i] = " "
                state = "literal"
        elif state == "line":
            if char == "\n":
                state = "code"
            else:
                out[i] = " "
        elif state == "block":
            if char == "*" and next_char == "/":
                out[i] = out[i + 1] = " "
                state = "code"
                i += 2
                continue
            if char != "\n":
                out[i] = " "
        else:
            if char == "\\" and next_char:
                if char != "\n":
                    out[i] = " "
                if next_char != "\n":
                    out[i + 1] = " "
                i += 2
                continue
            if char == quote:
                state = "code"
            if char != "\n":
                out[i] = " "
        i += 1
    if state == "block":
        raise ProtocolError("unterminated C block comment")
    return "".join(out)


def normalize(text):
    return re.sub(r"\s+", "", text)


def matching(text, start, opening, closing):
    if start >= len(text) or text[start] != opening:
        raise ProtocolError(f"expected {opening!r} at offset {start}")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    raise ProtocolError(f"unbalanced {opening}{closing} block")


def function_body(source, name):
    clean = sanitize_c(source)
    pattern = re.compile(rf"\b{re.escape(name)}\s*\(")
    for match in pattern.finditer(clean):
        open_paren = clean.find("(", match.start())
        close_paren = matching(clean, open_paren, "(", ")")
        cursor = close_paren + 1
        while cursor < len(clean) and clean[cursor].isspace():
            cursor += 1
        if cursor < len(clean) and clean[cursor] == "{":
            close_brace = matching(clean, cursor, "{", "}")
            return clean[cursor + 1 : close_brace]
    raise ProtocolError(f"missing function definition: {name}")


def parsed_ifs(body, top_level=False):
    result = []
    depth = 0
    index = 0
    while index < len(body):
        char = body[index]
        if char == "{":
            depth += 1
            index += 1
            continue
        if char == "}":
            depth -= 1
            index += 1
            continue
        match = re.match(r"if\b", body[index:])
        if not match or (top_level and depth != 0):
            index += 1
            continue
        cursor = index + match.end()
        while cursor < len(body) and body[cursor].isspace():
            cursor += 1
        if cursor >= len(body) or body[cursor] != "(":
            index += 1
            continue
        close_paren = matching(body, cursor, "(", ")")
        condition = normalize(body[cursor + 1 : close_paren])
        statement_start = close_paren + 1
        while statement_start < len(body) and body[statement_start].isspace():
            statement_start += 1
        if statement_start < len(body) and body[statement_start] == "{":
            statement_end = matching(body, statement_start, "{", "}")
            statement = body[statement_start + 1 : statement_end]
            end = statement_end + 1
        else:
            statement_end = body.find(";", statement_start)
            if statement_end < 0:
                raise ProtocolError(f"unterminated if statement: {condition}")
            statement = body[statement_start : statement_end + 1]
            end = statement_end + 1
        result.append(
            {
                "condition": condition,
                "statement": statement,
                "start": index,
                "end": end,
            }
        )
        index += 1
    return result


def require_if(body, condition, label, top_level=False):
    matches = [
        branch
        for branch in parsed_ifs(body, top_level=top_level)
        if branch["condition"] == condition
    ]
    if len(matches) != 1:
        raise ProtocolError(f"{label} must contain one if ({condition}) guard")
    return matches[0]


def call_positions(body, name):
    return [
        match.start()
        for match in re.finditer(rf"\b{re.escape(name)}\s*\(", body)
    ]


def require_calls(body, name, count, label):
    positions = call_positions(body, name)
    if len(positions) != count:
        raise ProtocolError(
            f"{label} must call {name} exactly {count} time(s), found {len(positions)}"
        )
    return positions


def forbid_calls(body, names, label):
    present = [name for name in names if call_positions(body, name)]
    if present:
        raise ProtocolError(f"{label} crosses phase ownership: {', '.join(present)}")


def phase_blocks(driver):
    blocks = {}
    for phase in PHASES[:-1]:
        branch = require_if(
            driver,
            f"phase=={phase}",
            "teardown driver",
            top_level=True,
        )
        blocks[phase] = branch["statement"]
    return blocks


def validate_begin(objects):
    begin = function_body(objects, "agent_scope_reclaim_begin")
    label = "teardown BEGIN"
    required_cleanup = {
        "catalog": "agent_metadata_catalog_reclaim_scope(scope_id)",
        "dependency table": "memset(&agent_dependencies[i],0,sizeof(agent_dependencies[i]))",
        "dependency generation": "agent_dependency_generation++",
        "action history": "agent_action_history_clear_scope(scope_id)",
        "query cache": "agent_metadata_query_invalidate_locked(scope_id,0)",
        "observability": "agent_observe_scope_reclaim(scope_id)",
        "file state": "agent_file_state_scope_reclaim(scope_id)",
    }
    compact = normalize(begin)
    for owner, contract in required_cleanup.items():
        if compact.count(contract) != 1:
            raise ProtocolError(f"{label} lost {owner} cleanup ownership")
    require_calls(begin, "agent_file_maintain", 1, label)
    require_calls(begin, "agent_metadata_store_mark_dirty", 1, label)
    assignment = "*metadata_target=agent_metadata_store_mark_dirty(scope_id);"
    if compact.count(assignment) != 1:
        raise ProtocolError(f"{label} must capture exactly one dirty generation")
    forbid_calls(
        begin,
        (
            "fs_reclaim_scope_files",
            "agent_scope_reclaim_metadata_done",
            "agent_metadata_store_scope_target_done",
            "agent_file_scope_state_retire",
            "bio_scope_retire",
            "workflow_lifecycle_reclaim",
            "agent_metadata_store_persist",
            "agent_metadata_store_persist_system",
            "sleep",
            "yield",
        ),
        label,
    )


def validate_metadata_poll(objects, store):
    poll = function_body(objects, "agent_scope_reclaim_metadata_done")
    compact = normalize(poll)
    expected = "returnagent_metadata_store_scope_target_done(scope_id,metadata_target);"
    if compact != expected:
        raise ProtocolError("teardown METADATA wrapper may only poll its captured target")

    reached = function_body(store, "agent_file_writeback_scope_reached")
    require_calls(reached, "intr_save", 1, "metadata target snapshot")
    require_calls(reached, "intr_restore", 1, "metadata target snapshot")
    for invariant in (
        "agent_file_writeback_generation_reached(",
        "!settled||state->dirty_generation==state->durable_generation",
        "settled&&agent_file_writeback_scope_busy(scope_id)",
        "reached=0;",
    ):
        if invariant not in normalize(reached):
            raise ProtocolError(f"metadata target snapshot lost invariant: {invariant}")

    target_done = function_body(store, "agent_metadata_store_scope_target_done")
    guard = require_if(
        target_done,
        "!agent_meta_store_failed_closed&&!agent_file_writeback_scope_reached(scope_id,target,1)",
        "metadata target completion",
    )
    if normalize(guard["statement"]) != "return0;":
        raise ProtocolError("metadata target completion must return while unsettled")
    retire = require_calls(
        target_done,
        "agent_file_scope_state_retire",
        1,
        "metadata target completion",
    )[0]
    if retire < guard["end"]:
        raise ProtocolError("metadata scope state retired before its target settled")


def validate_driver(vfs):
    clean = sanitize_c(vfs)
    enum_match = re.search(r"\benum\s+vfs_scope_reclaim_phase\s*\{", clean)
    if not enum_match:
        raise ProtocolError("missing teardown phase enum")
    enum_open = clean.find("{", enum_match.start())
    enum_close = matching(clean, enum_open, "{", "}")
    enum_phases = tuple(re.findall(r"\bVFS_SCOPE_RECLAIM_[A-Z]+\b", clean[enum_open:enum_close]))
    if enum_phases != PHASES:
        raise ProtocolError(f"teardown phase order changed: {enum_phases!r}")

    driver = function_body(vfs, "vfs_scope_reclaim_complete")
    blocks = phase_blocks(driver)
    begin = blocks[PHASES[0]]
    files = blocks[PHASES[1]]
    metadata = blocks[PHASES[2]]
    retire = blocks[PHASES[3]]

    begin_guard = require_if(
        begin,
        "agent_scope_reclaim_begin(scope_id,&target)<0",
        "teardown BEGIN phase",
    )
    if normalize(begin_guard["statement"]) != "return;":
        raise ProtocolError("teardown BEGIN failure must not advance")
    require_calls(begin, "agent_scope_reclaim_begin", 1, "teardown BEGIN phase")
    require_calls(begin, "vfs_scope_reclaim_advance", 1, "teardown BEGIN phase")
    begin_compact = normalize(begin)
    next_contract = (
        "uintnext=preserve_files?VFS_SCOPE_RECLAIM_METADATA:"
        "VFS_SCOPE_RECLAIM_FILES;"
    )
    if next_contract not in begin_compact or (
        "vfs_scope_reclaim_advance(scope_id,lifecycle,phase,next,target)"
        not in begin_compact
    ):
        raise ProtocolError("teardown BEGIN must advance to FILES/METADATA with its target")
    forbid_calls(
        begin,
        (
            "fs_reclaim_scope_files",
            "agent_scope_reclaim_metadata_done",
            "bio_scope_retire",
            "workflow_lifecycle_reclaim",
        ),
        "teardown BEGIN phase",
    )

    fs_call = require_calls(files, "fs_reclaim_scope_files", 1, "teardown FILES phase")[0]
    pending = require_if(
        files, "status==FS_RECLAIM_PENDING", "teardown FILES phase"
    )
    if normalize(pending["statement"]) != "return;":
        raise ProtocolError("teardown FILES must not advance while pending")
    failed = require_if(files, "status<0", "teardown FILES phase")
    failed_compact = normalize(failed["statement"])
    if (
        failed_compact.count("agent_file_request_scan();") != 1
        or not failed_compact.endswith("return;")
        or "vfs_scope_reclaim_advance(" in failed_compact
    ):
        raise ProtocolError("teardown FILES failure must request repair and stay in FILES")
    transition = (
        "vfs_scope_reclaim_advance(scope_id,lifecycle,phase,"
        "VFS_SCOPE_RECLAIM_METADATA,metadata_target)"
    )
    files_compact = normalize(files)
    if files_compact.count(transition) != 1:
        raise ProtocolError("teardown FILES must advance once to METADATA")
    transition_at = files_compact.index(transition)
    if fs_call > pending["start"] or transition_at < len(
        normalize(files[: failed["end"]])
    ):
        raise ProtocolError("teardown FILES transition is ordered before completion guards")
    forbid_calls(
        files,
        (
            "agent_scope_reclaim_begin",
            "agent_scope_reclaim_metadata_done",
            "bio_scope_retire",
            "workflow_lifecycle_reclaim",
        ),
        "teardown FILES phase",
    )

    metadata_guard = require_if(
        metadata,
        "!agent_scope_reclaim_metadata_done(scope_id,metadata_target)",
        "teardown METADATA phase",
    )
    if normalize(metadata_guard["statement"]) != "return;":
        raise ProtocolError("teardown METADATA must not advance before target completion")
    require_calls(
        metadata,
        "agent_scope_reclaim_metadata_done",
        1,
        "teardown METADATA phase",
    )
    require_calls(metadata, "vfs_scope_reclaim_advance", 1, "teardown METADATA phase")
    metadata_compact = normalize(metadata)
    metadata_transition = (
        "vfs_scope_reclaim_advance(scope_id,lifecycle,phase,"
        "VFS_SCOPE_RECLAIM_RETIRE,metadata_target)"
    )
    if metadata_compact.count(metadata_transition) != 1:
        raise ProtocolError("teardown METADATA must advance once to RETIRE")
    if metadata_compact.index(metadata_transition) < len(
        normalize(metadata[: metadata_guard["end"]])
    ):
        raise ProtocolError("teardown METADATA advances before its completion guard")
    forbid_calls(
        metadata,
        (
            "agent_scope_reclaim_begin",
            "fs_reclaim_scope_files",
            "bio_scope_retire",
            "workflow_lifecycle_reclaim",
        ),
        "teardown METADATA phase",
    )

    bio_at = require_calls(retire, "bio_scope_retire", 1, "teardown RETIRE phase")[0]
    require_calls(retire, "vfs_scope_reclaim_advance", 1, "teardown RETIRE phase")
    retire_compact = normalize(retire)
    retire_transition = (
        "vfs_scope_reclaim_advance(scope_id,lifecycle,phase,"
        "VFS_SCOPE_RECLAIM_DONE,metadata_target)"
    )
    if retire_compact.count(retire_transition) != 1:
        raise ProtocolError("teardown RETIRE must advance once to DONE")
    if retire_compact.index(retire_transition) < len(normalize(retire[:bio_at])):
        raise ProtocolError("teardown RETIRE advances before BIO retirement")
    forbid_calls(
        retire,
        (
            "agent_scope_reclaim_begin",
            "fs_reclaim_scope_files",
            "agent_scope_reclaim_metadata_done",
            "workflow_lifecycle_reclaim",
        ),
        "teardown RETIRE phase",
    )

    done_guard = require_if(
        driver,
        "phase!=VFS_SCOPE_RECLAIM_DONE",
        "teardown DONE phase",
        top_level=True,
    )
    if len(call_positions(done_guard["statement"], "panic")) != 1:
        raise ProtocolError("teardown DONE guard must reject an invalid phase")
    reclaim_positions = call_positions(driver, "workflow_lifecycle_reclaim")
    if not reclaim_positions or min(reclaim_positions) < done_guard["end"]:
        raise ProtocolError("workflow lifecycle reclaimed before DONE")

    advance = function_body(vfs, "vfs_scope_reclaim_advance")
    advance_compact = normalize(advance)
    guards = (
        "ref->scope_id!=scope_id",
        "!ref->retiring",
        "workflow_lifecycle_key_equal(ref->lifecycle,lifecycle)",
        "ref->reclaim_phase==expected",
        "workflow_lifecycle_retiring(lifecycle)",
    )
    for guard in guards:
        if guard not in advance_compact:
            raise ProtocolError(f"teardown phase publication lost guard: {guard}")
    target_assignment = "ref->reclaim_metadata_target=metadata_target;"
    phase_assignment = "ref->reclaim_phase=next;"
    if advance_compact.count(target_assignment) != 1 or advance_compact.count(phase_assignment) != 1:
        raise ProtocolError("teardown advance must publish one target and one phase")
    if advance_compact.index("ref->reclaim_phase==expected") > advance_compact.index(target_assignment):
        raise ProtocolError("teardown phase publication occurs before expected-phase check")
    if advance_compact.index(target_assignment) > advance_compact.index(phase_assignment):
        raise ProtocolError("teardown target must be published before its phase")

    for function in ("vfs_scope_create", "vfs_scope_release"):
        body = normalize(function_body(vfs, function))
        if body.count("reclaim_phase=VFS_SCOPE_RECLAIM_BEGIN;") != 1 or body.count(
            "reclaim_metadata_target=0;"
        ) != 1:
            raise ProtocolError(f"{function} must reset teardown phase and target")


def validate_legacy_absent(os_sources):
    legacy = re.compile(r"\bagent_scope_reclaim\s*\(")
    for path, source in os_sources.items():
        if legacy.search(sanitize_c(source)):
            raise ProtocolError(f"legacy all-in-one reclaim entry returned in {path}")


def validate_budget_wiring(budget):
    modules = budget.get("agent_modules", {})
    entries = {entry.get("name"): entry for entry in modules.get("modules", [])}
    metadata = entries.get("metadata_objects")
    if metadata is None:
        raise ProtocolError("metadata_objects is absent from module budgets")
    symbols = set(metadata.get("allowed_global_symbols", []))
    if not NEW_RECLAIM_APIS.issubset(symbols) or "agent_scope_reclaim" in symbols:
        raise ProtocolError("metadata_objects reclaim export budget is stale")
    forbidden = set(modules.get("forbidden_core_authority_symbols", []))
    if not NEW_RECLAIM_APIS.issubset(forbidden):
        raise ProtocolError("core authority boundary omits teardown APIs")
    bridges = {
        bridge.get("name"): bridge for bridge in modules.get("integration_bridges", [])
    }
    vfs = bridges.get("vfs_security")
    if vfs is None:
        raise ProtocolError("vfs_security is absent from integration budgets")
    dependencies = set(vfs.get("allowed_dependencies", []))
    if "metadata_objects" not in dependencies or "metadata_store" in dependencies:
        raise ProtocolError("vfs_security teardown dependency bypasses metadata_objects")


def load_sources(root):
    root = Path(root).resolve()
    paths = {
        "objects": root / "os" / "agent_metadata_objects.c",
        "store": root / "os" / "agent_metadata_store.c",
        "vfs": root / "os" / "vfs_security.c",
        "budget": root / "ci" / "kernel-budgets.json",
    }
    for label, path in paths.items():
        if not path.is_file():
            raise ProtocolError(f"missing {label} input: {path}")
    os_sources = {}
    for pattern in ("*.c", "*.h"):
        for path in sorted((root / "os").rglob(pattern)):
            os_sources[path.relative_to(root).as_posix()] = path.read_text(
                encoding="utf-8"
            )
    try:
        budget = json.loads(paths["budget"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProtocolError(f"invalid kernel budget config: {error}") from error
    return {
        "objects": paths["objects"].read_text(encoding="utf-8"),
        "store": paths["store"].read_text(encoding="utf-8"),
        "vfs": paths["vfs"].read_text(encoding="utf-8"),
        "os_sources": os_sources,
        "budget": budget,
    }


def validate_protocol(sources):
    validate_legacy_absent(sources["os_sources"])
    validate_begin(sources["objects"])
    validate_metadata_poll(sources["objects"], sources["store"])
    validate_driver(sources["vfs"])
    validate_budget_wiring(sources["budget"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    try:
        validate_protocol(load_sources(args.root))
    except ProtocolError as error:
        print(f"[teardown-protocol] failed: {error}", file=sys.stderr)
        return 1
    print("[teardown-protocol] phase and ownership contract: valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
