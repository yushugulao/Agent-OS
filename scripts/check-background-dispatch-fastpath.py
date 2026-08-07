#!/usr/bin/env python3
"""将延迟维护限制在低成本的 syscall 返回待处理边之后。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def compact(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", "", text)


def require(text: str, fragment: str, message: str) -> None:
    if fragment not in text:
        raise ValueError(message)


def function_body(text: str, name: str) -> str:
    marker = f"{name}("
    cursor = 0
    while True:
        start = text.find(marker, cursor)
        if start < 0:
            raise ValueError(f"missing function definition: {name}")
        paren = start + len(name)
        depth = 0
        end = paren
        while end < len(text):
            if text[end] == "(":
                depth += 1
            elif text[end] == ")":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        if end + 1 < len(text) and text[end + 1] == "{":
            brace = end + 1
            depth = 1
            finish = brace + 1
            while finish < len(text) and depth:
                if text[finish] == "{":
                    depth += 1
                elif text[finish] == "}":
                    depth -= 1
                finish += 1
            if depth != 0:
                raise ValueError(f"unterminated function definition: {name}")
            return text[brace + 1 : finish - 1]
        cursor = end + 1


def check(root: Path) -> None:
    syscall = compact(root / "os/syscall.c")
    trap = compact(root / "os/trap.c")
    proc = compact(root / "os/proc.c")
    store = compact(root / "os/agent_metadata_store.c")
    scan = compact(root / "os/agent_metadata_scan.c")
    objects = compact(root / "os/agent_metadata_objects.c")
    metadata_internal = compact(root / "os/agent_metadata_internal.h")
    fs = compact(root / "os/fs.c")
    vfs = compact(root / "os/vfs_security.c")
    core = compact(root / "os/agent_core.c")
    background = compact(root / "os/agent_background.c")

    require(
        function_body(background, "agent_background_work_pending"),
        "agent_identity_lease_maintenance_pending()",
        "identity lease renewal escaped the unified syscall-return gate",
    )

    if syscall.count("agent_background_checkpoint();") != 1:
        raise ValueError("syscall path has an unbounded maintenance trigger")
    require(
        syscall,
        "if(agent_background_work_pending()||id==SYS_sched_yield)"
        "agent_background_checkpoint();",
        "syscall return lost its pending-only maintenance safe point",
    )
    if "id!=SYS_agent_performance_snapshot" in syscall:
        raise ValueError("observer syscall policy bypasses the pending edge")
    if "!transaction.fs_epoch_admitted&&fs_epoch_should_commit()" in syscall:
        raise ValueError("unrelated syscalls can still inherit an aged commit")

    require(
        trap,
        "kernel_work_begin_background();agent_background_checkpoint();"
        "kernel_work_end_background();",
        "timer-driven maintenance progress is not bounded as background work",
    )
    require(
        proc,
        "if(t==NULL&&fs_epoch_should_commit()&&fs_epoch_request_begin()==0)",
        "idle writeback fallback is missing",
    )

    mark_dirty = function_body(store, "agent_metadata_store_mark_dirty")
    if mark_dirty.count("agent_background_request();") != 1:
        raise ValueError("ordinary metadata dirties still publish an immediate edge")
    require(
        mark_dirty,
        "if(state==0){agent_meta_reconcile_required=1;intr_restore(enabled);"
        "agent_background_request();return0;}",
        "metadata scope exhaustion lost its reconciliation edge",
    )
    require(
        mark_dirty,
        "if(state->dirty_generation!=state->durable_generation)"
        "state->coalesced_count++;elsestate->due_tick="
        "now+AGENT_META_WRITEBACK_COALESCE_TICKS;",
        "metadata coalescing deadline became sliding or disappeared",
    )
    require(
        mark_dirty,
        "state->request_count++;target=state->dirty_generation;"
        "intr_restore(enabled);returntarget;",
        "coalesced metadata dirties no longer wait for their deadline",
    )

    expedite = function_body(store, "agent_metadata_store_expedite")
    require(
        expedite,
        "state->due_tick=now;if(next_write_tick>now)next_write_tick=now;",
        "urgent metadata writeback no longer advances its deadline",
    )
    if "agent_background_request();" in expedite:
        raise ValueError("metadata expedite bypasses timer-driven dispatch")
    writeback_ready = function_body(store, "agent_file_writeback_ready")
    writeback_due = function_body(store, "agent_file_writeback_due")
    for fragment in ("now<next_write_tick", "now>=state->due_tick"):
        require(
            writeback_due,
            fragment,
            "metadata writeback ready check lost a not-before deadline",
        )
    for fragment in (
        "agent_file_writeback_pending()",
        "now>=agent_meta_persist.retry_tick",
        "agent_file_writeback_due(now)",
    ):
        require(
            writeback_ready,
            fragment,
            "metadata writeback does not distinguish pending from ready(now)",
        )

    store_background = function_body(
        store, "agent_metadata_store_background_maintain"
    )
    if store_background != "agent_file_writeback_maintain();":
        raise ValueError("metadata background completion still self-requeues")

    background_step = function_body(
        store, "agent_meta_persist_background_step_locked"
    )
    require(
        background_step,
        "elseif(step>=0){agent_meta_persist.retry_tick=0;}",
        "metadata step no longer hands deadline policy to its caller",
    )
    writeback_maintain = function_body(store, "agent_file_writeback_maintain")
    require(
        writeback_maintain,
        "step=agent_meta_persist_background_step_locked(owner);"
        "if(agent_meta_persist.phase!=AGENT_META_PERSIST_IDLE)"
        "agent_meta_persist_retry_next_tick();agent_metadata_txn_unlock();",
        "successful metadata persist step can rerun in the same tick",
    )
    for fragment in (
        "if(!bio_background_begin(owner)){if(agent_meta_persist.phase!="
        "AGENT_META_PERSIST_IDLE)agent_meta_persist_retry_next_tick();"
        "elseagent_file_writeback_defer();return;}",
        "if(!agent_metadata_txn_try_external()){if(agent_meta_persist.phase!="
        "AGENT_META_PERSIST_IDLE)agent_meta_persist_retry_next_tick();"
        "elseagent_file_writeback_defer();gotoout_io;}",
    ):
        require(
            writeback_maintain,
            fragment,
            "blocked metadata persist can rerun on every syscall in one tick",
        )

    store_tick = function_body(store, "agent_metadata_store_tick")
    require(
        metadata_internal,
        "voidagent_metadata_store_tick(uint64);",
        "metadata timer scheduler lost its internal declaration",
    )
    require(
        store_tick,
        "if(last_durable_retry_tick!=now){last_durable_retry_tick=now;"
        "retry_durable=1;}",
        "durable retry is not limited to one attempt per tick",
    )
    require(
        store_tick,
        "if(retry_durable)(void)agent_durable_section_retry_pending();",
        "timer tick no longer drives durable retry",
    )
    ready_at = store_tick.find("if(agent_file_writeback_ready(now)){")
    wake_at = store_tick.find("wait_queue_wake_all(&waiters);", ready_at)
    request_at = store_tick.find("agent_background_request();", wake_at)
    if not 0 <= ready_at < wake_at < request_at:
        raise ValueError(
            "timer tick no longer wakes and publishes due metadata writeback"
        )
    if store.count("agent_durable_section_retry_pending();") != 1:
        raise ValueError("durable retry has more than one runtime trigger")
    if store.count("agent_background_request();") != 3:
        raise ValueError("metadata store regained a non-timer continuation edge")

    scan_plan = function_body(scan, "agent_metadata_scan_plan")
    if scan_plan.count("scan.last_step_tick!=now") != 1:
        raise ValueError("metadata scan can plan more than one step per tick")
    for name in ("agent_file_request_scan", "agent_metadata_scan_slot_freed"):
        request = function_body(scan, name)
        require(
            request,
            "if(agent_metadata_scan_plan(now)!=AGENT_METADATA_SCAN_IDLE)"
            "agent_background_request();",
            "metadata scan request ignores its not-before deadline",
        )
    scan_request = function_body(scan, "agent_file_request_scan")
    require(
        scan_request,
        "!scan_ctl.pending||scan_ctl.pending<0",
        "explicit scan request cannot upgrade a deferred resume",
    )

    metadata_background = function_body(objects, "agent_metadata_background_maintain")
    metadata_tick = function_body(objects, "agent_metadata_tick")
    if "agent_metadata_scan_work_pending()" in metadata_background + metadata_tick:
        raise ValueError("scan pending state still hot-requeues unrelated syscalls")
    require(
        metadata_background,
        "plan=agent_metadata_scan_plan(now);"
        "if(plan==AGENT_METADATA_SCAN_IDLE)return;",
        "metadata background path enters scan I/O before the deadline",
    )
    if "agent_background_request();" in metadata_background:
        raise ValueError("metadata scan completion still self-requeues")
    require(
        metadata_tick,
        "agent_metadata_store_tick(now);",
        "timer tick no longer schedules due metadata writeback",
    )
    if metadata_tick.count("agent_metadata_store_tick(now);") != 1:
        raise ValueError("metadata store tick runs more than once per timer tick")
    require(
        metadata_tick,
        "if(agent_metadata_scan_plan(now)!=AGENT_METADATA_SCAN_IDLE)"
        "agent_background_request();",
        "timer tick no longer schedules ready metadata scan work",
    )

    fs_reclaim = function_body(fs, "fs_deferred_reclaim_maintain_owner")
    require(
        fs_reclaim,
        "if(paced){if(fs_deferred_reclaim_next_tick!=0&&"
        "now<fs_deferred_reclaim_next_tick)return0;"
        "fs_deferred_reclaim_next_tick=now+1;}",
        "deferred inode reclaim can run repeatedly in one timer tick",
    )
    if "agent_background_request();" in fs_reclaim:
        raise ValueError("deferred inode reclaim still hot-requeues itself")
    fs_tick = function_body(fs, "fs_deferred_reclaim_tick")
    require(
        fs_tick,
        "fs_deferred_reclaim_count!=0&&"
        "(fs_deferred_reclaim_next_tick==0||"
        "now>=fs_deferred_reclaim_next_tick)",
        "timer tick no longer publishes ready inode reclaim work",
    )

    vfs_reap = function_body(vfs, "vfs_scope_reap_pending")
    require(
        vfs_reap,
        "registry->reap_next_tick=now+1;",
        "workflow teardown is not limited to one phase per timer tick",
    )
    if "agent_background_request();" in vfs_reap:
        raise ValueError("workflow teardown still hot-requeues itself")
    vfs_tick = function_body(vfs, "vfs_scope_reap_tick")
    require(
        vfs_tick,
        "__atomic_load_n(&registry->retiring_count,__ATOMIC_ACQUIRE)!=0&&"
        "(next==0||now>=next)",
        "timer tick no longer publishes ready workflow teardown work",
    )
    core_tick = function_body(core, "agent_core_tick")
    for call in (
        "fs_deferred_reclaim_tick(now);",
        "vfs_scope_reap_tick(now);",
        "agent_metadata_tick(now);",
    ):
        require(core_tick, call, "Agent timer lost a deferred-work scheduler")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (OSError, ValueError) as error:
        print(f"background dispatch fast-path check failed: {error}", file=sys.stderr)
        return 1
    print("background dispatch fast-path check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
