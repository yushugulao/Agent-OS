#!/usr/bin/env python3
"""根据内核源码校验工作流拆除阶段协议。"""

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
    """在保留偏移与换行的同时清空注释和字面量。"""
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


def split_top_level_or(condition):
    terms = []
    depth = 0
    start = 0
    index = 0
    while index < len(condition):
        char = condition[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ProtocolError("unbalanced exec validation predicate")
        elif condition.startswith("||", index) and depth == 0:
            terms.append(condition[start:index])
            start = index + 2
            index += 1
        index += 1
    if depth != 0:
        raise ProtocolError("unbalanced exec validation predicate")
    terms.append(condition[start:])
    return terms


def require_if_predicates(body, predicates, label, top_level=False):
    expected = tuple(predicates)
    matches = []
    for branch in parsed_ifs(body, top_level=top_level):
        terms = split_top_level_or(branch["condition"])
        if len(terms) == len(expected) and set(terms) == set(expected):
            matches.append(branch)
    if len(matches) != 1:
        raise ProtocolError(
            f"{label} must contain one if with independent predicates "
            f"{expected!r}"
        )
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


def require_call_sequence(body, names, label):
    positions = [require_calls(body, name, 1, label)[0] for name in names]
    if positions != sorted(positions):
        raise ProtocolError(f"{label} call order changed")
    allowed = set(names) | {"if"}
    actual = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", body))
    unexpected = sorted(actual - allowed)
    if unexpected:
        raise ProtocolError(
            f"{label} has unowned calls: {', '.join(unexpected)}"
        )


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


def validate_begin(objects, actions):
    begin = function_body(objects, "agent_scope_reclaim_begin")
    label = "teardown BEGIN"
    required_cleanup = {
        "catalog": "agent_metadata_catalog_reclaim_scope(scope_id)",
        "action/dependency owner": "agent_metadata_actions_reclaim_scope(scope_id)",
        "query cache": "agent_metadata_query_invalidate_locked(scope_id,0)",
        "observability": "agent_observe_scope_reclaim(scope_id)",
        "file state": "agent_file_state_scope_reclaim(scope_id)",
    }
    compact = normalize(begin)
    for owner, contract in required_cleanup.items():
        if compact.count(contract) != 1:
            raise ProtocolError(f"{label} lost {owner} cleanup ownership")

    owner_cleanup = function_body(actions, "agent_metadata_actions_reclaim_scope")
    owner_compact = normalize(owner_cleanup)
    owned_contracts = {
        "dependency table": "memset(&agent_dependencies[i],0,sizeof(agent_dependencies[i]))",
        "dependency generation": "agent_metadata_actions_generation_advance()",
        "action history": "agent_metadata_actions_clear_history(scope_id)",
    }
    for owner, contract in owned_contracts.items():
        if owner_compact.count(contract) != 1:
            raise ProtocolError(f"metadata actions lost {owner} cleanup ownership")
    require_calls(begin, "agent_metadata_note_catalog_changes", 1, label)
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
    for contract in (
        "workflow_lifecycle_key_valid(lifecycle)",
        "vfs_scope_lifecycle(scope_id,&current)<0",
        "!workflow_lifecycle_key_equal(current,lifecycle)",
        "returnagent_metadata_store_scope_target_done(scope_id,metadata_target);",
    ):
        if compact.count(contract) != 1:
            raise ProtocolError(
                "teardown METADATA wrapper lost its lifecycle-keyed poll contract"
            )
    for forbidden in ("mark_dirty", "persist(", "sleep(", "yield("):
        if forbidden in compact:
            raise ProtocolError(
                "teardown METADATA wrapper may only validate and poll its target"
            )

    retire = function_body(store, "agent_file_scope_state_retire")
    retire_compact = normalize(retire)
    intr_save_at = require_calls(
        retire, "intr_save", 1, "metadata target retirement"
    )[0]
    intr_restore_at = require_calls(
        retire, "intr_restore", 1, "metadata target retirement"
    )[0]
    pending = require_if(
        retire,
        "agent_meta_store_failed_closed||"
        "agent_durable_section_scope_pending(scope_id)",
        "metadata target retirement",
    )
    if normalize(pending["statement"]) != "gotoout;":
        raise ProtocolError("metadata target retirement must stop while dirty")
    settled = require_if(
        retire,
        "!agent_file_writeback_generation_reached("
        "state->replicated_generation,target)||"
        "state->dirty_generation!=state->durable_generation||"
        "state->dirty_generation!=state->replicated_generation||"
        "agent_file_writeback_scope_busy(scope_id)",
        "metadata target retirement",
    )
    if normalize(settled["statement"]) != "gotoout;":
        raise ProtocolError("metadata target retirement must stop before settlement")
    retire_call = "memset(state,0,sizeof(*state));"
    if retire_compact.count(retire_call) != 1:
        raise ProtocolError("metadata target retirement must clear one settled slot")
    retire_at = require_calls(
        retire, "memset", 1, "metadata target retirement"
    )[0]
    lookup_at = require_calls(
        retire, "agent_file_scope_state_locked", 1,
        "metadata target retirement",
    )[0]
    absent = require_if(retire, "state==0", "metadata target retirement")
    if normalize(absent["statement"]) != "retired=target==0;gotoout;":
        raise ProtocolError(
            "metadata target retirement may accept an absent slot only for target zero"
        )
    if not (
        intr_save_at < pending["end"] < lookup_at < absent["end"]
        < settled["end"] < retire_at < intr_restore_at
    ):
        raise ProtocolError("metadata scope state retired before its target settled")

    target_done = normalize(
        function_body(store, "agent_metadata_store_scope_target_done")
    )
    expected_done = (
        "intretired;"
        "if(!agent_metadata_txn_lock(0))return0;"
        "retired=agent_file_scope_state_retire(scope_id,target);"
        "agent_metadata_txn_unlock();"
        "returnretired;"
    )
    if target_done != expected_done:
        raise ProtocolError(
            "metadata target completion must atomically check and retire under its transaction"
        )

    maintain = normalize(
        function_body(store, "agent_metadata_store_background_maintain")
    )
    expected_maintain = "agent_file_writeback_maintain();"
    if maintain != expected_maintain:
        raise ProtocolError(
            "metadata background maintenance must not hot-requeue pending state"
        )
    tick = normalize(function_body(store, "agent_metadata_store_tick"))
    ready = tick.find("if(agent_file_writeback_ready(now)){")
    wake = tick.find("wait_queue_wake_all(&waiters);", ready)
    request = tick.find("agent_background_request();", wake)
    if (
        "agent_durable_section_retry_pending();" not in tick
        or not 0 <= ready < wake < request
    ):
        raise ProtocolError(
            "metadata timer no longer wakes and publishes due durable work"
        )


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
        "agent_scope_reclaim_begin(scope_id,lifecycle,&target)<0",
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
        "!agent_scope_reclaim_metadata_done(scope_id,lifecycle,metadata_target)",
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

    lookup = function_body(vfs, "vfs_scope_find_locked")
    lookup_compact = normalize(lookup)
    lookup_contract = (
        "vfs_scope_registry_init_locked();",
        "link=registry->hash_heads[vfs_scope_hash(scope_id)];",
        "if(visited>=VFS_SCOPE_LIFECYCLE_CAP)panic();",
        "ref=&registry->refs[vfs_scope_slot(link)];",
        "if(!ref->used)panic();",
        "if(ref->scope_id==scope_id)returnref;",
        "link=ref->hash_next;",
    )
    for invariant in lookup_contract:
        if lookup_compact.count(invariant) != 1:
            raise ProtocolError(
                f"workflow scope hash lookup lost invariant: {invariant}"
            )
    if lookup_compact.count("for(") != 1 or "registry->refs[visited]" in lookup_compact:
        raise ProtocolError("workflow scope lookup must follow one bounded hash chain")

    advance = function_body(vfs, "vfs_scope_reclaim_advance")
    advance_compact = normalize(advance)
    guards = (
        "structvfs_scope_ref*ref=vfs_scope_find_locked(scope_id);",
        "ref!=0",
        "ref->retiring",
        "workflow_lifecycle_key_equal(ref->lifecycle,lifecycle)",
        "ref->reclaim_phase==expected",
        "workflow_lifecycle_retiring(lifecycle)",
    )
    for guard in guards:
        if guard not in advance_compact:
            raise ProtocolError(f"teardown phase publication lost guard: {guard}")
    if advance_compact.count("vfs_scope_find_locked(scope_id)") != 1 or re.search(
        r"\b(?:for|while)\s*\(", advance
    ):
        raise ProtocolError(
            "teardown phase publication must use one direct hashed scope match"
        )
    target_assignment = "ref->reclaim_metadata_target=metadata_target;"
    phase_assignment = "ref->reclaim_phase=next;"
    if advance_compact.count(target_assignment) != 1 or advance_compact.count(phase_assignment) != 1:
        raise ProtocolError("teardown advance must publish one target and one phase")
    if advance_compact.index("ref->reclaim_phase==expected") > advance_compact.index(target_assignment):
        raise ProtocolError("teardown phase publication occurs before expected-phase check")
    if advance_compact.index(target_assignment) > advance_compact.index(phase_assignment):
        raise ProtocolError("teardown target must be published before its phase")

    insert = normalize(function_body(vfs, "vfs_scope_registry_insert_locked"))
    create = normalize(function_body(vfs, "vfs_scope_create"))
    release = normalize(function_body(vfs, "vfs_scope_release"))
    if (
        insert.count("memset(ref,0,sizeof(*ref));") != 1
        or insert.count("ref->reclaim_phase=VFS_SCOPE_RECLAIM_BEGIN;") != 1
        or insert.index("memset(ref,0,sizeof(*ref));")
        > insert.index("ref->reclaim_phase=VFS_SCOPE_RECLAIM_BEGIN;")
        or create.count(
            "vfs_scope_registry_insert_locked(scope_id,created,storage)"
        ) != 1
    ):
        raise ProtocolError(
            "vfs_scope_create must initialize phase and target in its registry insertion"
        )
    if (
        release.count("matched->reclaim_phase=VFS_SCOPE_RECLAIM_BEGIN;") != 1
        or release.count("matched->reclaim_metadata_target=0;") != 1
        or release.index("matched->reclaim_phase=VFS_SCOPE_RECLAIM_BEGIN;")
        > release.index("matched->reclaim_metadata_target=0;")
    ):
        raise ProtocolError("vfs_scope_release must reset teardown phase and target")


def validate_reaper_liveness(vfs, objects, background, agent_core):
    release = function_body(vfs, "vfs_scope_release")
    release_compact = normalize(release)
    if release_compact.count("agent_background_request();") != 1:
        raise ProtocolError(
            "last workflow reference must publish exactly one maintenance edge"
        )
    close_at = release_compact.find("fs_storage_scope_account_close(storage);")
    quiesce_at = release_compact.find("bio_scope_quiesce(scope_id);")
    request_at = release_compact.find("agent_background_request();")
    if min(close_at, quiesce_at, request_at) < 0 or not (
        close_at < quiesce_at < request_at
    ):
        raise ProtocolError(
            "last workflow reference must quiesce storage and I/O before reaping"
        )
    release_branches = [
        branch
        for branch in parsed_ifs(release)
        if branch["condition"] == "last>0"
        and "agent_background_request();" in normalize(branch["statement"])
    ]
    if len(release_branches) != 1:
        raise ProtocolError(
            "last workflow reference maintenance edge escaped its common boundary"
        )

    reap = function_body(vfs, "vfs_scope_reap_pending")
    reap_compact = normalize(reap)
    require_calls(reap, "vfs_scope_reclaim_complete", 2, "workflow reaper")
    if call_positions(reap, "agent_background_request"):
        raise ProtocolError(
            "workflow reaper must leave continuation publication to the timer"
        )
    for invariant in (
        "now<registry->reap_next_tick",
        "registry->reap_next_tick=now+1;",
        "if(registry->retiring_count==0)registry->reap_next_tick=0;",
    ):
        if invariant not in reap_compact:
            raise ProtocolError(
                "workflow reaper lost its one-bounded-phase-per-tick deadline"
            )
    if re.search(r"\b(?:while|goto)\b", reap):
        raise ProtocolError("workflow reaper must not busy-loop")
    for forbidden in (
        "agent_background_maintain",
        "agent_background_checkpoint",
        "vfs_scope_reap_pending",
    ):
        if call_positions(reap, forbidden):
            raise ProtocolError("workflow reaper must return after one bounded pass")

    tick = function_body(vfs, "vfs_scope_reap_tick")
    tick_compact = normalize(tick)
    for invariant in (
        "__atomic_load_n(&registry->retiring_count,__ATOMIC_ACQUIRE)!=0",
        "now>=next",
        "agent_background_request();",
    ):
        if invariant not in tick_compact:
            raise ProtocolError(
                "workflow reaper timer lost pending, deadline, or publication"
            )
    if re.search(r"\b(?:for|while|goto)\b", tick):
        raise ProtocolError("workflow reaper timer must remain O(1)")

    metadata_maintain = function_body(
        objects, "agent_metadata_background_maintain"
    )
    reap_calls = require_calls(
        metadata_maintain,
        "vfs_scope_reap_pending",
        1,
        "metadata background coordinator",
    )
    store_calls = require_calls(
        metadata_maintain,
        "agent_metadata_store_background_maintain",
        1,
        "metadata background coordinator",
    )
    if reap_calls[0] > store_calls[0]:
        raise ProtocolError(
            "metadata background must preserve a reaper edge across same-pass commit"
        )

    request = normalize(function_body(background, "agent_background_request"))
    take = normalize(function_body(background, "agent_background_take"))
    if request != (
        "__atomic_store_n(&agent_background_pending,1,__ATOMIC_RELEASE);"
    ) or take != (
        "return__atomic_exchange_n(&agent_background_pending,0,"
        "__ATOMIC_ACQ_REL);"
    ):
        raise ProtocolError(
            "background edge latch must atomically merge concurrent requests"
        )

    checkpoint = function_body(agent_core, "agent_background_checkpoint")
    take_calls = require_calls(
        checkpoint, "agent_background_take", 1, "background checkpoint"
    )
    maintain_calls = require_calls(
        checkpoint, "agent_background_maintain", 1, "background checkpoint"
    )
    if take_calls[0] > maintain_calls[0] or re.search(
        r"\b(?:while|goto)\b", checkpoint
    ):
        raise ProtocolError(
            "background checkpoint must consume once before one maintenance pass"
        )


def validate_trapframe_lifecycle(proc):
    clean = sanitize_c(proc)
    if re.search(
        r"\btrapframe\s*\[\s*NPROC\s*\]\s*\[\s*NTHREAD\s*\]", clean
    ):
        raise ProtocolError("global NPROC x NTHREAD trapframe pool returned")

    acquire = function_body(proc, "thread_trapframe_acquire")
    require_calls(acquire, "kalloc_account_page", 1, "trapframe acquire")
    if call_positions(acquire, "kalloc"):
        raise ProtocolError("trapframe acquire bypasses page accounting")

    release = function_body(proc, "thread_trapframe_release")
    require_calls(release, "kfree_account_page", 1, "trapframe release")
    if call_positions(release, "kfree"):
        raise ProtocolError("trapframe release bypasses page accounting")

    user_release = normalize(function_body(proc, "thread_user_vm_release"))
    unmap = "uvmunmap(pt,get_thread_trapframe_va(t->tid),1,0);"
    free = "thread_trapframe_release(t);"
    if user_release.count(unmap) != 1 or user_release.count(free) != 1:
        raise ProtocolError("thread teardown lost one trapframe unmap/free")
    if user_release.index(unmap) > user_release.index(free):
        raise ProtocolError("thread teardown frees trapframe before unmapping it")

    reset = normalize(function_body(proc, "proc_reset_thread_slot"))
    trapframe_release = "thread_trapframe_release(t);"
    thread_release = "proc_thread_resource_release(t);"
    if reset.count(trapframe_release) != 1 or reset.count(thread_release) != 1:
        raise ProtocolError("thread slot reset lost trapframe/account release")
    if reset.index(trapframe_release) > reset.index(thread_release):
        raise ProtocolError("thread account released before its trapframe page")

    install = normalize(function_body(proc, "proc_install_user_image"))
    image_check = "proc_user_image_trapframe_valid(p,image)"
    prepare = "vfs_proc_exec_prepare(p,image,live_exec,&transition)"
    commit = "vfs_proc_exec_commit(p,&transition)"
    if image_check not in install or prepare not in install or commit not in install:
        raise ProtocolError("exec replacement lost trapframe image validation")
    if install.index(image_check) > install.index(prepare) or install.index(
        image_check
    ) > install.index(commit):
        raise ProtocolError("exec publishes credentials/VM before trapframe validation")
    image_validator = normalize(
        function_body(proc, "proc_user_image_trapframe_valid")
    )
    for invariant in (
        "walk(image->pagetable,TRAPFRAME,0)",
        "PTE2PA(*pte)!=(uint64)p->threads[0].trapframe",
        "PTE_V|PTE_R|PTE_W",
        "PTE_U|PTE_X",
    ):
        if invariant not in image_validator:
            raise ProtocolError(
                f"trapframe image validator lost invariant: {invariant}"
            )
    old_unmap = (
        "uvmunmap(old_pagetable,get_thread_trapframe_va(tid),1,0);"
    )
    sibling_reset = "proc_reset_thread_slot(slot);"
    if old_unmap not in install or sibling_reset not in install:
        raise ProtocolError("exec replacement lost sibling trapframe teardown")
    if install.index(old_unmap) > install.index(sibling_reset):
        raise ProtocolError("exec frees sibling trapframe before old VM unmap")

    dying = function_body(proc, "scheduler_finish_dying_thread")
    dying_guard = require_if(
        dying, "t->trapframe!=0", "scheduler trapframe handoff"
    )
    if len(call_positions(dying_guard["statement"], "panic")) != 1:
        raise ProtocolError("scheduler handoff lost trapframe quiescence guard")
    thread_release_at = normalize(dying).find(thread_release)
    if thread_release_at < 0:
        raise ProtocolError("scheduler handoff lost thread account release")
    if len(normalize(dying[: dying_guard["end"]])) > thread_release_at:
        raise ProtocolError("scheduler releases thread account with live trapframe")


def validate_exec_publication(proc):
    install = function_body(proc, "proc_install_user_image")
    compact = normalize(install)
    label = "PUBLIC exec publication"

    validate_at = require_calls(
        compact, "vfs_proc_exec_validate_locked", 1, label
    )[0]
    image_state_at = require_calls(
        compact, "proc_image_install_state_valid_locked", 1, label
    )[0]
    sync_validate_at = require_calls(
        compact, "sync_proc_exec_validate_locked", 1, label
    )[0]
    identity_at = require_calls(
        compact, "agent_exec_public_identity_commit", 1, label
    )[0]
    detach_at = require_calls(
        compact, "proc_detach_vfs_scope_fds_locked", 1, label
    )[0]
    commit_positions = require_calls(compact, "vfs_proc_exec_commit", 2, label)
    image_reset_at = require_calls(
        compact, "agent_process_image_install_locked", 1, label
    )[0]
    forbid_calls(compact, ("agent_thread_runtime_transition",), label)
    sync_reset_at = require_calls(
        compact, "sync_proc_exec_reset_locked", 1, label
    )[0]
    vm_swap = "p->pagetable=image->pagetable;"
    if compact.count(vm_swap) != 1:
        raise ProtocolError(f"{label} must swap the VM exactly once")
    publication_order = (
        max(image_state_at, validate_at, sync_validate_at),
        identity_at,
        detach_at,
        max(commit_positions),
        image_reset_at,
        sync_reset_at,
        compact.index(vm_swap),
    )
    if publication_order != tuple(sorted(publication_order)):
        raise ProtocolError(
            f"{label} order must be validate, identity, FDs, VFS, runtime, sync, VM"
        )

    prepare_failure = require_if(
        install,
        "!proc_user_image_trapframe_valid(p,image)||"
        "vfs_proc_exec_prepare(p,image,live_exec,&transition)<0",
        "exec prepare",
    )
    alias_failure = require_if(
        install,
        "agent_alias_exec_context(p,image->pagetable)<0",
        "exec Context alias failure",
    )
    validate_failure = require_if_predicates(
        install,
        (
            "!proc_teardown_live(p)",
            "!proc_image_install_state_valid_locked(p,mode)",
            "sync_proc_exec_validate_locked(p,&p->threads[0])<0",
            "vfs_proc_exec_validate_locked(p,&transition)<0",
        ),
        "exec locked validation failure",
        top_level=True,
    )
    guard_prefix = normalize(install[: validate_failure["start"]])
    if not guard_prefix.endswith("enabled=intr_save();"):
        raise ProtocolError(
            "exec locked validation predicates must share the IRQ publication boundary"
        )
    reserved = require_if(
        install, "transition.lifecycle_reserved", "exec reserved lifecycle"
    )
    reserved_failure = require_if(
        reserved["statement"],
        "vfs_proc_exec_commit(p,&transition)<0",
        "exec reserved lifecycle commit failure",
    )
    identity_failure = require_if(
        install,
        "transition.identity_policy==VFS_EXEC_IDENTITY_PUBLIC&&"
        "agent_exec_public_identity_commit(p)<0",
        "exec PUBLIC identity failure",
    )
    abort = "vfs_proc_exec_abort(&transition);"
    for failure_label, failure in (
        ("exec Context alias failure", alias_failure),
        ("exec locked validation failure", validate_failure),
        ("exec reserved lifecycle commit failure", reserved_failure),
        ("exec PUBLIC identity failure", identity_failure),
    ):
        statement = normalize(failure["statement"])
        failed_return = "return-1;"
        if (
            statement.count(abort) != 1
            or statement.count(failed_return) != 1
            or statement.index(abort) > statement.index(failed_return)
        ):
            raise ProtocolError(
                f"{failure_label} must abort prepared exec before returning"
            )

    vm_swap_match = re.search(
        r"\bp\s*->\s*pagetable\s*=\s*image\s*->\s*pagetable\s*;", install
    )
    if vm_swap_match is None:
        raise ProtocolError(f"{label} lost VM swap")
    prepublication = install[prepare_failure["end"] : vm_swap_match.start()]
    if len(re.findall(r"\breturn\b", prepublication)) != 4:
        raise ProtocolError(
            "exec pre-publication failure paths changed without rollback contract"
        )
    require_calls(prepublication, "vfs_proc_exec_abort", 4, "exec pre-publication")


def validate_legacy_mail_fail_closed(syscall):
    label = "retired legacy mail syscalls"
    expected = {
        "sys_mailread": "(void)buf;(void)len;return-1;",
        "sys_mailwrite": "(void)pid;(void)buf;(void)len;return-1;",
    }
    for name, exact in expected.items():
        body = function_body(syscall, name)
        compact = normalize(body)
        if compact != exact:
            raise ProtocolError(
                f"{label}: {name} must only discard arguments and return -1"
            )


def validate_proc_prepare_invariant(agent_core):
    label = "RECYCLED_CLEAN process prepare"
    prepare = function_body(agent_core, "agent_core_proc_prepare")
    guard = require_if_predicates(
        prepare,
        (
            "p==0",
            "!proc_teardown_live(p)",
            "p->is_agent",
            "p->agent_control_id!=0",
            "p->agent_controller_id!=0",
            "!agent_context_is_empty(p)",
        ),
        label,
        top_level=True,
    )
    require_calls(guard["statement"], "panic", 1, label)
    publish = require_calls(prepare, "agent_ipc_proc_prepare", 1, label)[0]
    if guard["end"] >= publish:
        raise ProtocolError(f"{label} must validate before IPC prepare")
    forbid_calls(
        prepare,
        (
            "agent_core_clear_metadata",
            "agent_core_proc_state_reset",
            "agent_context_proc_reset",
            "agent_observe_proc_reset",
            "agent_ipc_proc_reset",
            "agent_identity_proc_reset",
        ),
        label,
    )
    if re.search(
        r"\bp\s*->\s*(?:agent_|context_|heartbeat_|resource_quota)"
        r"[A-Za-z0-9_]*\s*(?:=(?!=)|\+=|-=|\*=|/=|&=|\|=|\^=|\+\+|--)",
        prepare,
    ):
        raise ProtocolError(f"{label} must not rewrite recycled process state")
    require_call_sequence(
        prepare,
        (
            "proc_teardown_live",
            "agent_context_is_empty",
            "panic",
            "agent_ipc_proc_prepare",
        ),
        label,
    )


def validate_ipc_lifecycle(agent_ipc):
    require_call_sequence(
        function_body(agent_ipc, "agent_ipc_proc_prepare"),
        ("intr_save", "agent_ipc_event_baton_clear_locked", "intr_restore"),
        "IPC process prepare",
    )
    require_call_sequence(
        function_body(agent_ipc, "agent_ipc_exec_public"),
        ("agent_ipc_remove_source", "agent_ipc_proc_reset"),
        "IPC PUBLIC exec",
    )
    require_call_sequence(
        function_body(agent_ipc, "agent_ipc_proc_teardown"),
        (
            "agent_ipc_remove_source",
            "intr_save",
            "agent_ipc_broadcast_event_teardown_locked",
            "agent_ipc_proc_reset",
            "intr_restore",
        ),
        "IPC process teardown",
    )


def validate_legacy_absent(os_sources):
    legacy = re.compile(r"\bagent_scope_reclaim\s*\(")
    for path, source in os_sources.items():
        if legacy.search(sanitize_c(source)):
            raise ProtocolError(f"legacy all-in-one reclaim entry returned in {path}")


def validate_terminal_teardown(proc):
    teardown = function_body(proc, "proc_teardown_run")
    compact = normalize(teardown)
    gate = "fs_epoch_request_end();"
    file_settle = "fileclose_batch_settle(&close_batch)"
    settle = "bio_request_end_current_cleanup()"
    work = "kernel_work_end_cleanup();"
    clear = "vfs_proc_terminal_clear(p);"
    release = "vfs_proc_lifecycle_release(p);"
    for token, label in (
        (gate, "filesystem gate release"),
        (file_settle, "deferred file settlement"),
        (settle, "terminal I/O settlement"),
        (work, "terminal work settlement"),
        (clear, "terminal identity clear"),
        (release, "lifecycle release"),
    ):
        if compact.count(token) != 1:
            raise ProtocolError(f"process teardown needs one {label}")
    positions = [
        compact.index(token)
        for token in (gate, file_settle, settle, work, clear, release)
    ]
    if positions != sorted(positions):
        raise ProtocolError(
            "terminal teardown must release FS gate, settle files/BIO/work, then release lifecycle"
        )
    if "fileclose_batch_add(&close_batch,files[i])" not in compact or (
        "fileclose(files[i])" in compact
    ):
        raise ProtocolError("process teardown bypasses batched file settlement")
    terminal_blocks = [
        normalize(branch["statement"])
        for branch in parsed_ifs(teardown)
        if branch["condition"] == "terminal_current"
    ]
    settlement_blocks = [body for body in terminal_blocks if gate in body]
    if len(settlement_blocks) != 1 or not all(
        token in settlement_blocks[0]
        for token in (gate, file_settle, settle, work)
    ):
        raise ProtocolError("terminal settlement is not one guarded ownership transfer")

    progress = normalize(function_body(proc, "proc_teardown_file_progress"))
    progress_tokens = (
        "fs_epoch_request_end();",
        "fileclose_batch_settle(batch)",
        "kernel_work_checkpoint_cleanup(KERNEL_WORK_OPERATION_UNITS)",
        "fs_epoch_request_begin()",
    )
    if any(token not in progress for token in progress_tokens) or [
        progress.index(token) for token in progress_tokens
    ] != sorted(progress.index(token) for token in progress_tokens):
        raise ProtocolError(
            "teardown progress must release the FS gate, settle and yield, then reacquire"
        )
    if compact.count("proc_teardown_file_progress(&close_batch)") < 2:
        raise ProtocolError(
            "teardown does not incrementally settle or retry file cleanup"
        )

    exit_body = normalize(function_body(proc, "exit"))
    if gate in exit_body:
        raise ProtocolError("exit releases the filesystem gate twice")
    begin = "fs_epoch_request_begin()"
    io_begin = "bio_request_begin_current_cleanup()"
    run = "proc_teardown_run(p,t,1)"
    if not all(token in exit_body for token in (begin, io_begin, run)) or not (
        exit_body.index(begin) < exit_body.index(io_begin) < exit_body.index(run)
    ):
        raise ProtocolError("exit teardown admission order is incomplete")


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
    if (
        "background" not in dependencies
        or "metadata_objects" not in dependencies
        or "metadata_store" in dependencies
    ):
        raise ProtocolError("vfs_security teardown dependency bypasses metadata_objects")


def load_sources(root):
    root = Path(root).resolve()
    paths = {
        "objects": root / "os" / "agent_metadata_objects.c",
        "actions": root / "os" / "agent_metadata_actions.c",
        "store": root / "os" / "agent_metadata_store.c",
        "vfs": root / "os" / "vfs_security.c",
        "proc": root / "os" / "proc.c",
        "agent_core": root / "os" / "agent_core.c",
        "background": root / "os" / "agent_background.c",
        "agent_ipc": root / "os" / "agent_ipc.c",
        "syscall": root / "os" / "syscall.c",
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
        "actions": paths["actions"].read_text(encoding="utf-8"),
        "store": paths["store"].read_text(encoding="utf-8"),
        "vfs": paths["vfs"].read_text(encoding="utf-8"),
        "proc": paths["proc"].read_text(encoding="utf-8"),
        "agent_core": paths["agent_core"].read_text(encoding="utf-8"),
        "background": paths["background"].read_text(encoding="utf-8"),
        "agent_ipc": paths["agent_ipc"].read_text(encoding="utf-8"),
        "syscall": paths["syscall"].read_text(encoding="utf-8"),
        "os_sources": os_sources,
        "budget": budget,
    }


def validate_protocol(sources):
    validate_legacy_absent(sources["os_sources"])
    validate_begin(sources["objects"], sources["actions"])
    validate_metadata_poll(sources["objects"], sources["store"])
    validate_driver(sources["vfs"])
    validate_reaper_liveness(
        sources["vfs"], sources["objects"], sources["background"],
        sources["agent_core"]
    )
    validate_trapframe_lifecycle(sources["proc"])
    validate_exec_publication(sources["proc"])
    validate_terminal_teardown(sources["proc"])
    validate_proc_prepare_invariant(sources["agent_core"])
    validate_ipc_lifecycle(sources["agent_ipc"])
    validate_legacy_mail_fail_closed(sources["syscall"])
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
