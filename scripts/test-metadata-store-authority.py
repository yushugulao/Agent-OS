#!/usr/bin/env python3
"""Mutation-test metadata job identity and physical delta shadows."""

from __future__ import annotations

import re
from pathlib import Path


STORE = Path(__file__).resolve().parents[1] / "os" / "agent_metadata_store.c"


class ContractError(RuntimeError):
    pass


def compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def body(source: str, signature: str) -> str:
    start = source.find(signature)
    while start >= 0:
        opening = source.find("{", start)
        semicolon = source.find(";", start)
        if opening >= 0 and (semicolon < 0 or opening < semicolon):
            break
        start = source.find(signature, start + len(signature))
    if start < 0:
        raise ContractError(f"missing function definition: {signature}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return compact(source[opening + 1 : index])
    raise ContractError(f"unterminated function: {signature}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def validate(source: str) -> None:
    all_source = compact(source)
    busy = body(source, "agent_file_writeback_scope_busy(")
    require(
        "agent_meta_persist.phase!=AGENT_META_PERSIST_IDLE" in busy
        and "agent_meta_persist.scope_id==scope_id" in busy,
        "busy identity is not bound to the immutable persist scope",
    )
    require(
        "agent_meta_persist.owner" not in busy and "FS_OWNER_" not in busy,
        "mutable I/O sponsorship controls teardown busy identity",
    )
    require(
        "agent_meta_bank_delta_valid[AGENT_META_STORE_BANKS]" in all_source,
        "physical delta validity is not separate from logical authority",
    )

    delta_invalidate = body(source, "agent_meta_bank_delta_invalidate(")
    require(
        "agent_meta_bank_delta_valid[bank]=0" in delta_invalidate
        and "agent_meta_bank_write_limit[bank]=0" in delta_invalidate,
        "delta invalidation keeps a skippable physical range",
    )
    shadow_invalidate = body(source, "agent_meta_bank_shadow_invalidate(")
    require(
        "agent_meta_bank_delta_invalidate(bank)" in shadow_invalidate
        and "agent_meta_bank_shadow_valid[bank]=0" in shadow_invalidate,
        "logical invalidation leaves physical delta authority behind",
    )
    force_full = body(source, "agent_meta_persist_target_locked(")
    require(
        "if(force_full){memset(agent_meta_persist.dirty_blocks,0xff" in force_full
        and "agent_meta_bank_delta_invalidate(agent_meta_persist.target_bank)"
        in force_full,
        "full rewrite does not revoke delta skipping",
    )

    prepare = body(source, "agent_meta_persist_prepare_blocks(")
    dirty_gate = (
        "if(!agent_meta_bank_shadow_valid[target_bank]||"
        "!agent_meta_bank_delta_valid[target_bank]||"
        "agent_meta_bank_write_limit[target_bank]<end||memcmp("
    )
    require(dirty_gate in prepare, "payload skipping accepts an unverified delta")

    install = body(source, "agent_meta_bank_shadow_install(")
    require(
        "agent_meta_bank_shadow_valid[target_bank]=1" in install
        and "agent_meta_bank_delta_valid[target_bank]=physical_delta_valid"
        in install
        and "physical_delta_valid?write_limit:0" in install,
        "logical and physical shadow publication are not distinct",
    )

    select = body(source, "agent_meta_store_select(")
    require(
        "*selected_migrated=migration[selected]" in select,
        "selected physical format is not returned to publication",
    )
    load = body(source, "agent_file_load_snapshot(")
    require(
        "agent_meta_bank_shadow_install(store,selected_bank,"
        "agent_meta_persist_segment_end(store_bytes-1),"
        "!selected_migrated&&selected_cursor.slots_used==0)" in load,
        "migrated v5 authority is published as a skippable v7 delta",
    )
    require(
        all_source.count(
            "agent_meta_persist.write_limit,1);"
        ) == 2,
        "a bank gains delta authority without both verified write paths",
    )
    persist_step = body(source, "agent_meta_persist_step_locked(")
    device_error = body(source, "agent_meta_persist_device_error(")
    require(
        "if(result==VIRTIO_DISK_ERR_BUSY||"
        "result==VIRTIO_DISK_ERR_TIMEOUT)" in device_error
        and "returnAGENT_META_PERSIST_DEFERRED" in device_error,
        "retryable device pressure does not preserve the inducing owner",
    )
    require(
        "if(n<0&&agent_meta_persist.error_cause=="
        "AGENT_METADATA_PERSIST_NONE)result="
        "agent_meta_persist_device_error(n)" in persist_step
        and "returnresult" in persist_step,
        "common I/O completion swallows an already classified retry",
    )
    start = body(source, "agent_meta_persist_start_locked(")
    require(
        "agent_meta_persist.error_cause=AGENT_METADATA_PERSIST_NONE" in start
        and "if(agent_meta_store_generation==~0ULL){"
        "agent_meta_persist.error_cause=AGENT_METADATA_PERSIST_DURABILITY;"
        "return-1;}" in start
        and "if(!agent_meta_store_io_enter())"
        "returnAGENT_META_PERSIST_DEFERRED" in start,
        "persist admission does not distinguish exhaustion from lane pressure",
    )
    persist = body(source, "agent_file_persist(")
    require(
        "if(start_status<0){failure_cause="
        "agent_meta_persist.error_cause==AGENT_METADATA_PERSIST_NONE?"
        "AGENT_METADATA_PERSIST_IO:agent_meta_persist.error_cause" in persist,
        "persist start discards the classified failure cause",
    )
    maintain = body(source, "agent_file_writeback_maintain(")
    start_at = maintain.find(
        "step=agent_meta_persist_start_locked(owner)"
    )
    continue_at = maintain.find(
        "if(step>=0)step=agent_meta_persist_background_step_locked(owner)"
    )
    classify_at = maintain.find(
        "if(step==AGENT_META_PERSIST_DEFERRED&&"
    )
    require(
        "if(agent_meta_persist.phase==AGENT_META_PERSIST_IDLE)" in maintain
        and 0 <= start_at < continue_at < classify_at,
        "background persist does not inspect start status before stepping",
    )
    background_step = body(source, "agent_meta_persist_background_step_locked(")
    require(
        "if(step<0&&step!=AGENT_META_PERSIST_DEFERRED)" in background_step
        and "if(agent_meta_persist_note_failure_locked()==0)"
        "step=AGENT_META_DRAIN_RETRY;" in background_step,
        "recoverable mirror repair is reported as a fatal drain failure",
    )
    drain = body(source, "agent_meta_persist_drain_owner(")
    require(
        "#defineAGENT_META_OWNER_DRAIN_STEP_BUDGET"
        "AGENT_META_REPLICATED_STEP_LIMIT" in all_source
        and "for(uintsteps=0;steps<step_budget;)" in drain
        and "batch_limit=MIN(AGENT_META_SUBMIT_DRAIN_BUDGET,"
        "step_budget-steps)" in drain
        and "while(progressed<batch_limit)" in drain,
        "metadata owner drain lacks an explicit step budget",
    )
    require(
        "agent_meta_persist_background_step_locked(owner)" in drain
        and "progressed++;steps++;" in drain
        and "kernel_work_checkpoint_cleanup(MIN("
        "KERNEL_WORK_BUDGET_UNITS,"
        "progressed*KERNEL_WORK_OPERATION_UNITS))!=0" in drain
        and "if(agent_meta_persist.phase==AGENT_META_PERSIST_IDLE){"
        "result=0;break;}" in drain,
        "metadata drain lacks bounded batching or a fairness checkpoint",
    )
    require(
        "if(step<0){if(step==AGENT_META_PERSIST_DEFERRED||"
        "step==AGENT_META_DRAIN_RETRY){"
        "if(agent_meta_persist.phase!=AGENT_META_PERSIST_IDLE)"
        "agent_meta_persist_retry_next_tick();}else{result=-1;}break;}" in drain,
        "metadata drain does not preserve the repair retry state",
    )
    require(
        "if(job_id!=0&&job_id!=agent_meta_persist.job_id){"
        "agent_metadata_txn_unlock();break;}"
        "job_id=agent_meta_persist.job_id;" in drain
        and "batch_limit=1;" in drain,
        "metadata drain can cross a replacement job or expand a borrowed lease",
    )
    require(
        "if(agent_meta_persist.phase!=AGENT_META_PERSIST_IDLE)"
        "agent_meta_persist_retry_next_tick();" in maintain,
        "background persist does not defer its continuation to the next tick",
    )
    submit = body(source, "agent_metadata_store_submit_wait_locked(")
    retry_branch = "if(drain_status==AGENT_META_DRAIN_RETRY){"
    retry_at = submit.find(retry_branch)
    retry_wait = submit.find("gotowait;", retry_at)
    require(
        "#defineAGENT_META_SUBMIT_DRAIN_BUDGET"
        "(4U*AGENT_META_STORE_BANKS)" in all_source
        and "AGENT_META_SUBMIT_DRAIN_BUDGET<"
        "AGENT_META_OWNER_DRAIN_STEP_BUDGET" in all_source
        and "drain_attempts" not in submit
        and submit.count("agent_meta_persist_drain_owner(") == 1
        and "agent_meta_persist_drain_owner(owner,"
        "AGENT_META_SUBMIT_DRAIN_BUDGET)" in submit
        and retry_at >= 0
        and retry_wait > retry_at,
        "metadata submit does not use one bounded foreground assist",
    )
    retry_body = submit[retry_at:retry_wait]
    require(
        "serving_ticket++;" not in retry_body
        and "return0;" not in retry_body
        and "agent_background_request();" not in retry_body,
        "retrying FIFO head is released or returned to a user-space hot loop",
    )
    require(
        "agent_meta_persist_abort_locked" not in retry_body
        and "agent_meta_persist_release_locked" not in retry_body,
        "retry exhaustion destroys the inducing owner continuation",
    )
    background = body(source, "agent_metadata_store_background_maintain(")
    tick = body(source, "agent_metadata_store_tick(")
    ready_at = tick.find("if(agent_file_writeback_ready(now)){")
    wake_at = tick.find("wait_queue_wake_all(&waiters);", ready_at)
    request_at = tick.find("agent_background_request();", wake_at)
    require(
        background == "agent_file_writeback_maintain();"
        and 0 <= ready_at < wake_at < request_at,
        "metadata continuation is not published exclusively by its timer deadline",
    )
    read_at = persist_step.find("n=readi_device(ip,&kernel_cred,0,(uint64)&verified_header")
    error_at = persist_step.find(
        "if(n<0){iput(ip);returnagent_meta_persist_device_error(n);}", read_at
    )
    short_at = persist_step.find("if(n!=(int)sizeof(store->header)", read_at)
    require(
        0 <= read_at < error_at < short_at,
        "header read errors are collapsed into short-read corruption",
    )


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ContractError(f"mutation anchor drifted: {old}")
    return source.replace(old, new, 1)


class SubmitDrainModel:
    """FIFO 队首按到期边续跑固定批次，不退回用户态热循环。"""

    def __init__(self) -> None:
        self.serving_ticket = 1
        self.continuation = (7, "mirror", 41)
        self.deadline_armed = 0
        self.served: list[int] = []
        self.waits = 0
        self.checkpoints = 0
        self.charged_steps = 0

    def submit(self, ticket: int, rounds: list[list[str]]) -> tuple[str, int]:
        require(ticket == self.serving_ticket, "submit model violated FIFO order")
        attempts = 0
        for outcomes in rounds:
            require(0 < len(outcomes) <= 8, "submit model exceeded batch limit")
            attempts += 1
            self.checkpoints += 1
            self.charged_steps += len(outcomes)
            outcome = outcomes[-1]
            if outcome == "complete":
                self.continuation = None
                break
            require(outcome == "retry", "submit model received unknown outcome")
            self.deadline_armed += 1
            self.waits += 1
        else:
            raise ContractError("submit model returned a live FIFO head")
        self.served.append(ticket)
        self.serving_ticket += 1
        return "ready", attempts


def validate_submit_retry_model() -> None:
    model = SubmitDrainModel()
    result, attempts = model.submit(
        1,
        [["step"] * 7 + ["retry"], ["step"] * 6 + ["complete"]],
    )
    require(result == "ready" and attempts == 2, "recovered work did not resume")
    require(model.continuation is None, "completed continuation remains pinned")
    require(
        model.served == [1] and model.serving_ticket == 2 and model.waits == 1,
        "retrying FIFO head escaped before its deadline continuation",
    )
    require(
        model.deadline_armed == 1
        and model.checkpoints == 2
        and model.charged_steps == 15,
        "batch work or its tail was not charged exactly once",
    )


def main() -> int:
    source = STORE.read_text(encoding="utf-8")
    validate(source)
    mutations = (
        (
            "owner controls busy identity",
            "\t       agent_meta_persist.scope_id == scope_id;",
            "\t       agent_meta_persist.owner == scope_id;",
        ),
        (
            "drop physical delta gate",
            "\t\t    !agent_meta_bank_delta_valid[target_bank] ||\n",
            "",
        ),
        (
            "trust migrated bank bytes",
            "\t\t!selected_migrated && selected_cursor.slots_used == 0);",
            "\t\t1);",
        ),
        (
            "retain delta on full rewrite",
            "\tagent_meta_bank_delta_invalidate(agent_meta_persist.target_bank);",
            "",
        ),
        (
            "forget selected migration",
            "\t*selected_migrated = migration[selected];",
            "\t*selected_migrated = 0;",
        ),
        (
            "logical invalidation retains delta",
            "\tagent_meta_bank_delta_invalidate(bank);\n"
            "\tagent_meta_bank_shadow_valid[bank] = 0;",
            "\tagent_meta_bank_shadow_valid[bank] = 0;",
        ),
        (
            "classify header error as corruption",
            "\t\tif (n < 0) {\n"
            "\t\t\tiput(ip);\n"
            "\t\t\treturn agent_meta_persist_device_error(n);\n"
            "\t\t}\n",
            "",
        ),
        (
            "repair on timeout pressure",
            "\tif (result == VIRTIO_DISK_ERR_BUSY ||\n"
            "\t    result == VIRTIO_DISK_ERR_TIMEOUT) {",
            "\tif (result == VIRTIO_DISK_ERR_BUSY) {",
        ),
        (
            "swallow preclassified flush retry",
            "\tif (n < 0 && agent_meta_persist.error_cause ==\n"
            "\t\t\t     AGENT_METADATA_PERSIST_NONE)\n"
            "\t\tresult = agent_meta_persist_device_error(n);",
            "\tif (n < 0)\n"
            "\t\treturn agent_meta_persist.error_cause ==\n"
            "\t\t\t       AGENT_METADATA_PERSIST_NONE ?\n"
            "\t\t       agent_meta_persist_device_error(n) : -1;",
        ),
        (
            "classify metadata lane pressure as I/O",
            "\tif (!agent_meta_store_io_enter())\n"
            "\t\treturn AGENT_META_PERSIST_DEFERRED;",
            "\tif (!agent_meta_store_io_enter())\n\t\treturn -1;",
        ),
        (
            "discard persist start failure cause",
            "\t\t\t\tfailure_cause = agent_meta_persist.error_cause ==\n"
            "\t\t\t\t\t\tAGENT_METADATA_PERSIST_NONE ?\n"
            "\t\t\t\t\t\tAGENT_METADATA_PERSIST_IO :\n"
            "\t\t\t\t\t\tagent_meta_persist.error_cause;",
            "\t\t\t\tfailure_cause = AGENT_METADATA_PERSIST_IO;",
        ),
        (
            "misclassify generation exhaustion",
            "\t\tagent_meta_persist.error_cause = AGENT_METADATA_PERSIST_DURABILITY;",
            "\t\tagent_meta_persist.error_cause = AGENT_METADATA_PERSIST_IO;",
        ),
        (
            "background persist hides start status",
            "\tif (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE)\n"
            "\t\tstep = agent_meta_persist_start_locked(owner);\n"
            "\tif (step >= 0)\n"
            "\t\tstep = agent_meta_persist_background_step_locked(owner);",
            "\tstep = agent_meta_persist_background_step_locked(owner);",
        ),
        (
            "repair retry becomes fatal",
            "\t\tif (agent_meta_persist_note_failure_locked() == 0)\n"
            "\t\t\tstep = AGENT_META_DRAIN_RETRY;",
            "\t\t(void)agent_meta_persist_note_failure_locked();",
        ),
        (
            "drain discards repair retry",
            "\t\t\t\tif (step == AGENT_META_PERSIST_DEFERRED ||\n"
            "\t\t\t\t    step == AGENT_META_DRAIN_RETRY) {\n",
            "\t\t\t\tif (step == AGENT_META_PERSIST_DEFERRED) {\n",
        ),
        (
            "submit drain loses retry budget",
            "\t\t\tdrain_status = agent_meta_persist_drain_owner(\n"
            "\t\t\t\towner, AGENT_META_SUBMIT_DRAIN_BUDGET);",
            "\t\t\tdrain_status = agent_meta_persist_drain_owner(\n"
            "\t\t\t\towner, AGENT_META_OWNER_DRAIN_STEP_BUDGET);",
        ),
        (
            "drain loses batch fairness",
            "\t\tif (started_background &&\n"
            "\t\t    kernel_work_checkpoint_cleanup(MIN(KERNEL_WORK_BUDGET_UNITS,\n"
            "\t\t\tprogressed * KERNEL_WORK_OPERATION_UNITS)) != 0)\n"
            "\t\t\tbreak;",
            "\t\tif (started_background && 0)\n\t\t\tbreak;",
        ),
        (
            "drain loses locked completion",
            "\t\t\tif (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE) {\n"
            "\t\t\t\tresult = 0;",
            "\t\t\tif (0) {\n\t\t\t\tresult = 0;",
        ),
        (
            "drain drops retry deadline",
            "\t\t\t\t\tif (agent_meta_persist.phase != AGENT_META_PERSIST_IDLE)\n"
            "\t\t\t\t\t\tagent_meta_persist_retry_next_tick();\n",
            "",
        ),
        (
            "drain crosses replacement job",
            "\t\t\tif (job_id != 0 && job_id != agent_meta_persist.job_id) {",
            "\t\t\tif (0) {",
        ),
        (
            "drain expands borrowed lease",
            "\t\t\tbatch_limit = 1;",
            "\t\t\tbatch_limit = AGENT_META_SUBMIT_DRAIN_BUDGET;",
        ),
        (
            "submit retry releases FIFO head",
            "\t\t\t\t/* 队首在内核等待续跑边，不能用用户态重试抢占一个 tick。 */\n"
            "\t\t\t\tgoto wait;",
            "\t\t\t\tserving_ticket++;\n\t\t\t\tgoto wait;",
        ),
        (
            "submit retry returns to userspace",
            "\t\t\t\t/* 队首在内核等待续跑边，不能用用户态重试抢占一个 tick。 */\n"
            "\t\t\t\tgoto wait;",
            "\t\t\t\treturn 0;",
        ),
        (
            "timer drops the FIFO deadline wake",
            "\t\twait_queue_wake_all(&waiters);\n"
            "\t\tagent_background_request();",
            "\t\tagent_background_request();",
        ),
        (
            "timer drops the background continuation",
            "\t\t/* 到期边同时唤醒 FIFO 队首并合并后台任务。 */\n"
            "\t\twait_queue_wake_all(&waiters);\n"
            "\t\tagent_background_request();",
            "\t\twait_queue_wake_all(&waiters);",
        ),
    )
    for label, old, new in mutations:
        candidate = replace_once(source, old, new)
        try:
            validate(candidate)
        except ContractError:
            continue
        raise ContractError(f"mutation survived: {label}")
    validate_submit_retry_model()
    print(f"metadata store authority contract: ok ({len(mutations)}/{len(mutations)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
