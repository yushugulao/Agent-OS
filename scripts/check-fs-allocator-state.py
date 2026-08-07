#!/usr/bin/env python3
"""拒绝持久文件系统分配器状态机的回归。"""

from __future__ import annotations

import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def blank(match: re.Match[str]) -> str:
    return "".join("\n" if char == "\n" else " " for char in match.group())


def matching(text: str, opening: int, left: str, right: str) -> int:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == left:
            depth += 1
        elif text[index] == right:
            depth -= 1
            if depth == 0:
                return index
    return -1


def semantic_text(source: str) -> str:
    text = re.sub(
        r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
        blank,
        source,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"^[ \t]*#if\s+0\b.*?^[ \t]*#endif\b[^\n]*",
        blank,
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    dead = re.compile(r"\bif\s*\(\s*0\s*\)\s*\{")
    while True:
        match = dead.search(text)
        if match is None:
            return text
        opening = text.find("{", match.start())
        closing = matching(text, opening, "{", "}")
        if closing < 0:
            return text
        region = text[match.start() : closing + 1]
        text = text[: match.start()] + blank(re.match(r".*", region, re.DOTALL)) + text[closing + 1 :]


def function_body(source: str, name: str) -> str | None:
    text = semantic_text(source)
    for match in re.finditer(rf"\b{re.escape(name)}\s*\(", text):
        opening = text.find("(", match.start())
        closing = matching(text, opening, "(", ")")
        if closing < 0:
            continue
        brace = re.search(r"\S", text[closing + 1 :])
        if brace is None or brace.group() != "{":
            continue
        start = closing + 1 + brace.start()
        end = matching(text, start, "{", "}")
        if end >= 0:
            return text[start + 1 : end]
    return None


def require_order(
    body: str | None, name: str, patterns: tuple[str, ...], failures: list[str]
) -> None:
    if body is None:
        failures.append(f"missing allocator function {name}")
        return
    cursor = 0
    for pattern in patterns:
        match = re.search(pattern, body[cursor:], re.DOTALL)
        if match is None:
            failures.append(f"{name}: missing or misordered {pattern}")
            return
        cursor += match.end()


def require_call_before_first_return(
    body: str | None, name: str, call: str, failures: list[str]
) -> None:
    if body is None:
        failures.append(f"missing allocator function {name}")
        return
    call_match = re.search(call, body, re.DOTALL)
    first_return = re.search(r"\breturn\b", body)
    if call_match is None or (
        first_return is not None and first_return.start() < call_match.start()
    ):
        failures.append(f"{name}: success/no-op exit can bypass {call}")


def reject_constant_early_exit(
    body: str | None, name: str, failures: list[str]
) -> None:
    if body is None:
        return
    if re.search(
        r"\bif\s*\(\s*(?:1|true)\s*\)\s*(?:\{\s*)?"
        r"(?:return\b|goto\b)",
        body,
        re.DOTALL,
    ):
        failures.append(f"{name}: constant-true early exit bypasses state machine")


def require_forward_helper_shape(
    body: str | None, name: str, raw_call: str, failures: list[str]
) -> None:
    if body is None:
        return
    if re.search(r"\bgoto\b", body):
        failures.append(f"{name}: forward helper may not contain control-flow jumps")
    returns = re.findall(r"\breturn\s+([^;]+);", body, re.DOTALL)
    normalized = {re.sub(r"\s+", "", value) for value in returns}
    if len(returns) != 3 or normalized != {
        "-1",
        "result",
        "fs_durable_barrier_forward()",
    }:
        failures.append(f"{name}: forward helper return shape changed")
    if len(re.findall(raw_call, body)) != 1:
        failures.append(f"{name}: raw allocation-map write count changed")
    if re.search(r"\bresult\s*=\s*0\s*;", body):
        failures.append(f"{name}: forward helper can forge success")


def check_sources(sources: dict[str, str]) -> list[str]:
    failures: list[str] = []
    fs = sources["os/fs.c"]
    fs_h = sources["os/fs.h"]
    fs_epoch = sources["os/fs_epoch.c"]
    fs_epoch_h = sources["os/fs_epoch.h"]
    makefile = sources["Makefile"]
    loader = sources["os/loader.c"]
    syscall = sources["os/syscall.c"]
    test_owner = sources["os/fs_allocator_test.c"]
    runner = sources["scripts/run-fs-allocator-fault-tests.sh"]
    virtio = sources["os/virtio_disk.c"]
    virtio_h = sources["os/virtio.h"]
    bio_h = sources["os/bio.h"]
    proc = sources["os/proc.c"]
    agent_core = sources["os/agent_core.c"]
    client = sources["user/src/fsallocfault_ucore.c"]
    image_tool = sources["scripts/fs-allocator-image.py"]
    evidence = sources["scripts/fs-allocator-evidence.py"]
    host_probe = sources["scripts/host-probe-toolchain.sh"]
    trusted_entry = sources["scripts/trusted-python-entry.py"]
    agent_runner = sources["scripts/agent_test_runner.py"]

    for token in (
        "#define FS_QMAP_STATE_MASK 0xc0000000U",
        "#define FS_QMAP_ALLOCATING_FLAG 0x40000000U",
        "#define FS_QMAP_FREEING_FLAG 0xc0000000U",
    ):
        if token not in fs_h:
            failures.append(f"os/fs.h: missing four-state contract {token}")

    for token, label in (
        ("FS_EPOCH_BUFFER_CAP 48U", "bounded epoch capacity"),
        ("FS_EPOCH_PREPARE", "prepare phase"),
        ("FS_EPOCH_INODE", "inode phase"),
        ("FS_EPOCH_NAMESPACE_DETACH", "namespace detach phase"),
        ("FS_EPOCH_NAMESPACE_ATTACH", "namespace attach phase"),
    ):
        if token not in fs_epoch_h:
            failures.append(f"filesystem epoch missing {label}")
    phase_order = (
        "FS_EPOCH_PREPARE",
        "FS_EPOCH_NAMESPACE_DETACH",
        "FS_EPOCH_INODE",
        "FS_EPOCH_NAMESPACE_ATTACH",
    )
    cursor = 0
    for token in phase_order:
        found = fs_epoch_h.find(token, cursor)
        if found < 0:
            failures.append(f"filesystem epoch phase order missing {token}")
            break
        cursor = found + len(token)
    # 赞助与提交顺序由 test-fs-epoch-sponsor.py 做变异测试；本分配器检查器仅保留
    # 回收所需的生命周期安全门控不变量。
    if "wait_queue_wake_all(&epoch.request_queue);" not in fs_epoch:
        failures.append("filesystem epoch missing lifecycle-safe gate wakeup")
    if "request_handoff" in fs_epoch or "wait_queue_wake_one_thread" in fs_epoch:
        failures.append("filesystem epoch retains a teardown-unsafe thread handoff")
    reserve_phase = function_body(fs_epoch, "fs_epoch_reserve_ordered") or ""
    for token in (
        "FS_EPOCH_NAMESPACE_DETACH",
        "epoch.phase_count[FS_EPOCH_NAMESPACE_ATTACH] != 0",
        "FS_EPOCH_NAMESPACE_ATTACH",
        "epoch.phase_count[FS_EPOCH_NAMESPACE_DETACH] != 0",
    ):
        if token not in reserve_phase:
            failures.append(f"filesystem epoch namespace split missing {token}")
    stage = function_body(fs_epoch, "fs_epoch_stage") or ""
    if not all(
        token in stage
        for token in (
            "entry->phase == FS_EPOCH_NAMESPACE_DETACH",
            "phase == FS_EPOCH_NAMESPACE_ATTACH",
            "entry->phase == FS_EPOCH_NAMESPACE_ATTACH",
            "phase == FS_EPOCH_NAMESPACE_DETACH",
        )
    ):
        failures.append("filesystem epoch stage accepts opposite namespace images")
    fence = function_body(fs_epoch, "fs_epoch_generation_fence") or ""
    for token in (
        "!fs_epoch_dirty_locked()",
        "epoch.active_generation == 0",
        "epoch.owner != owner",
        "*generation = epoch.active_generation",
    ):
        if token not in fence:
            failures.append(f"filesystem reclaim fence missing {token}")
    if "BIO_CACHE_CLEANUP_CAP 8U" not in bio_h:
        failures.append("cleanup cache budget is not exported to deferred reclaim")

    for token, label in (
        ("FS_DEFERRED_RECLAIM_CAP 128U", "bounded reclaim queue"),
        ("FS_DEFERRED_RECLAIM_SYSTEM_RESERVE 16U", "system reclaim reserve"),
        ("FS_DEFERRED_RECLAIM_OWNER_CAP 32U", "per-owner reclaim cap"),
        ("FS_DEFERRED_RECLAIM_UNIQUE_BUFFERS 6U", "cleanup cache budget"),
    ):
        if token not in fs:
            failures.append(f"deferred reclaim missing {label}")
    require_order(
        function_body(fs, "fs_deferred_reclaim_reserve"),
        "deferred reclaim reservation",
        (
            r"fs_deferred_reclaim_capacity_available",
            r"fs_epoch_commit\s*\(",
            r"fs_deferred_reclaim_maintain_owner",
            r"bio_deferred_owner_retain",
            r"bio_deferred_owner_retain_cleanup",
            r"entry.*reserved|\.reserved\s*=\s*1",
            r"reclaim->deferred_reserved\s*=\s*1",
        ),
        failures,
    )
    require_order(
        function_body(fs, "fs_deferred_reclaim_publish"),
        "deferred reclaim publish",
        (
            r"fs_epoch_generation_fence",
            r"entry->reclaim\s*=\s*\*reclaim",
            r"entry->fence_generation\s*=\s*generation",
            r"entry->published\s*=\s*1",
            r"agent_background_request",
        ),
        failures,
    )
    maintain = function_body(fs, "fs_deferred_reclaim_maintain_owner") or ""
    require_order(
        maintain,
        "deferred reclaim ordered settlement",
        (
            r"fs_epoch_generation_committed",
            r"bio_deferred_sponsor_begin",
            r"fs_allocator_gate_lock\s*\(\s*1\s*\)",
            r"fs_deferred_reclaim_stage_",
            r"fs_epoch_commit\s*\(",
            r"fs_deferred_reclaim_advance",
            r"fs_storage_release_many_accounted",
            r"fs_allocator_gate_unlock",
            r"bio_deferred_sponsor_end",
        ),
        failures,
    )
    if "bfree(" in (function_body(fs, "fs_deferred_reclaim_stage_block") or ""):
        failures.append("deferred reclaim restored per-block synchronous free")
    require_order(
        function_body(syscall, "sys_sync"),
        "owner-scoped sync fence",
        (
            r"agent_metadata_quiescence_fence_current",
            r"fs_deferred_reclaim_drain_current",
            r"fs_epoch_commit",
        ),
        failures,
    )
    if "fs_deferred_reclaim_maintain();" not in agent_core:
        failures.append("Agent background maintenance does not advance reclaim")

    request_token = function_body(fs_epoch, "fs_epoch_request_token") or ""
    if not all(
        token in request_token
        for token in ("thread->tid < 0", "thread->identity_generation == 0")
    ):
        failures.append("filesystem epoch lacks permanent idle request token")
    require_order(
        function_body(proc, "scheduler"),
        "idle aged epoch commit",
        (
            r"current_thread\s*=\s*&idle",
            r"t\s*=\s*fetch_task",
            r"t\s*==\s*NULL\s*&&\s*fs_epoch_should_commit",
            r"fs_epoch_request_begin",
            r"fs_epoch_commit",
            r"fs_epoch_request_end",
        ),
        failures,
    )
    if "bio_deferred_polling_current()" not in virtio:
        failures.append("VirtIO runtime path rejects idle deferred polling")

    require_order(
        function_body(fs, "balloc_one"),
        "balloc_one durable fallback",
        (
            r"fs_storage_reserve\s*\(\s*charge\s*,\s*0\s*\)",
            r"bzero\s*\(\s*dev\s*,\s*block\s*\)",
            r"fs_durable_barrier\s*\(\s*\)",
            r"fs_qmap_write\s*\(\s*dev\s*,\s*block\s*,\s*intent\s*\)",
            r"fs_durable_barrier_forward\s*\(\s*\)",
            r"fs_bitmap_write_forward\s*\(\s*dev\s*,\s*block\s*,\s*1\s*\)",
            r"fs_qmap_write_forward\s*\(\s*dev\s*,\s*block\s*,\s*charge->owner\s*\)",
            r"reserved\s*=\s*0\s*;",
        ),
        failures,
    )
    require_order(
        function_body(fs, "balloc"),
        "balloc path selection",
        (
            r"FS_ALLOCATOR_FAULT_TEST_PROFILE",
            r"balloc_one\s*\(",
            r"!fs_epoch_runtime_enabled\s*\(\s*\)\s*\|\|\s*"
            r"fs_epoch_bypass_active\s*\(\s*\)",
            r"balloc_one\s*\(",
            r"balloc_epoch\s*\(",
        ),
        failures,
    )
    if "#define FS_BLOCK_CANDIDATE_BYTES ((FSSIZE + 7U) / 8U)" not in fs:
        failures.append("candidate cache: reservation bitmap is not capacity-sized")
    candidate_cap = re.search(
        r"^\s*#define\s+FS_BLOCK_MAGAZINE_CAP\s+([0-9]+)U\s*$",
        fs,
        re.MULTILINE,
    )
    if candidate_cap is None or not 2 <= int(candidate_cap.group(1)) <= 32:
        failures.append("candidate cache: bounded scan cache must be in [2, 32]")

    refill_body = function_body(fs, "fs_block_candidate_refill")
    require_order(
        refill_body,
        "candidate refill",
        (
            r"fs_block_magazine_find\s*\(",
            r"fs_read_block\s*\(\s*dev\s*,\s*BBLOCK",
            r"fs_block_candidate_is_reserved\s*\(",
            r"fs_block_candidate_set\s*\(",
            r"magazine->blocks\s*\[\s*magazine->count\+\+\s*\]",
            r"fs_block_candidate_transfer\s*\(",
        ),
        failures,
    )
    if refill_body is not None and re.search(
        r"fs_storage_reserve|\bbzero\s*\(|fs_qmap_write|fs_bitmap_write",
        refill_body,
    ):
        failures.append("candidate refill: unused candidates touch durable allocation state")

    # epoch 专用镜像与中止顺序由 test-fs-epoch-sponsor.py 负责；此处仅保留
    # 分配器全局不变量。
    epoch_alloc = function_body(fs, "balloc_epoch") or ""
    if "FS_QMAP_ALLOCATING_FLAG" in epoch_alloc or re.search(
        r"fs_durable_barrier(?:_forward)?\s*\(", epoch_alloc
    ):
        failures.append("epoch allocation: production fast path retains intermediate intent/barrier")
    if len(re.findall(r"fs_storage_reserve\s*\(", epoch_alloc)) != 1:
        failures.append("epoch allocation: quota reservation is not exactly one block")
    legacy_alloc = function_body(fs, "balloc_one") or ""
    if "fs_block_candidate_reclaim" not in legacy_alloc:
        failures.append("balloc_one: synchronous path cannot reclaim cached capacity")
    drain_body = function_body(fs, "fs_block_candidate_drain") or ""
    if "fs_block_candidate_clear" not in drain_body or re.search(
        r"\bbfree\s*\(|fs_storage_release|fs_(?:qmap|bitmap)_write|\bbzero\s*\(",
        drain_body,
    ):
        failures.append("candidate drain: closing an owner mutates unallocated candidates")

    reserve_many_body = function_body(fs, "fs_storage_reserve_many")
    require_order(
        reserve_many_body,
        "fs_storage_reserve_many",
        (
            r"\.amount\s*=\s*amount",
            r"amount\s*>\s*\*free_count\s*\|\|\s*"
            r"\*free_count\s*-\s*amount\s*<\s*reserve",
            r"resource_reserve_many\s*\(\s*account\s*,\s*charge_class\s*,\s*"
            r"&request\s*,\s*1\s*,\s*&reservation\s*\)",
            r"resource_reservation_commit\s*\(\s*&reservation\s*\)",
            r"\*free_count\s*-=\s*amount",
        ),
        failures,
    )
    if reserve_many_body is not None and len(
        re.findall(r"resource_reserve_many\s*\(", reserve_many_body)
    ) != 1:
        failures.append("fs_storage_reserve_many: quota must use one atomic reservation")

    release_many_body = function_body(fs, "fs_storage_release_many_accounted")
    require_order(
        release_many_body,
        "fs_storage_release_many",
        (
            r"\.amount\s*=\s*amount",
            r"amount\s*>\s*total\s*-\s*\*free_count",
            r"\*free_count\s*\+=\s*amount",
            r"resource_release_many\s*\(\s*account\s*,\s*charge_class\s*,\s*"
            r"&request\s*,\s*1\s*\)",
        ),
        failures,
    )

    scrub_inode = function_body(fs, "fs_scrub_mark_inode_blocks")
    require_order(
        scrub_inode,
        "allocation-ahead EOF recovery",
        (
            r"needed\s*=\s*fs_div_round_up\s*\(\s*dip->size\s*,\s*BSIZE\s*\)",
            r"indirect_needed\s*=\s*needed\s*>\s*NDIRECT",
            r"i\s*<\s*indirect_needed",
            r"fs_scrub_mark_block",
            r"brelse\s*\(\s*bp\s*\)",
            r"indirect_needed\s*<\s*NINDIRECT",
            r"fs_scrub_clear_indirect_suffix_forward",
        ),
        failures,
    )
    require_order(
        function_body(fs, "bfree"),
        "bfree",
        (
            r"!allocated\s*&&\s*qstate\s*==\s*FS_OWNER_NONE",
            r"fs_qmap_write_forward\s*\(\s*dev\s*,\s*block\s*,\s*freeing\s*\)",
            r"fs_bitmap_write_forward\s*\(\s*dev\s*,\s*block\s*,\s*0\s*\)",
            r"fs_qmap_write_forward\s*\(\s*dev\s*,\s*block\s*,\s*FS_OWNER_NONE\s*\)",
            r"fs_storage_release\s*\(\s*owner\s*,\s*0\s*\)",
        ),
        failures,
    )
    require_order(
        function_body(fs, "ialloc"),
        "ialloc",
        (
            r"intent\s*=\s*fs_qmap_transition\s*\(\s*FS_QMAP_ALLOCATING_FLAG",
            r"fs_storage_reserve\s*\(\s*charge\s*,\s*1\s*\)",
            r"dip->fs_owner_domain\s*=\s*intent\s*;",
            r"fs_write_inode_block\s*\(\s*bp\s*\)",
            r"fs_durable_barrier_forward\s*\(\s*\)",
            r"FS_ALLOCATOR_FAULT_AFTER\s*\(\s*FSALLOC_OP_IALLOC",
            r"reserved\s*=\s*0\s*;",
        ),
        failures,
    )
    require_order(
        function_body(fs, "inode_remove_detach"),
        "inode_remove_detach",
        (
            r"freeing\s*=\s*fs_qmap_transition\s*\(\s*FS_QMAP_FREEING_FLAG",
            r"ip->fs_owner_domain\s*=\s*freeing\s*;",
            r"iupdate\s*\(\s*ip\s*\)",
            r"FSALLOC_PHASE_INTENT",
            r"ip->type\s*=\s*0\s*;",
            r"ip->fs_owner_domain\s*=\s*freeing\s*;",
            r"iupdate\s*\(\s*ip\s*\)",
            r"FSALLOC_PHASE_OWNER",
            r"ip->fs_owner_domain\s*=\s*FS_OWNER_NONE\s*;",
            r"iupdate\s*\(\s*ip\s*\)",
            r"fs_storage_release\s*\(\s*storage_owner\s*,\s*1\s*\)",
            r"FSALLOC_PHASE_REFUND",
        ),
        failures,
    )
    require_order(
        function_body(fs, "itruncate_detach_partial"),
        "itruncate_detach_partial",
        (
            r"ip->size\s*=\s*size\s*;",
            r"result\s*=\s*iupdate\s*\(\s*ip\s*\)",
            r"fs_durable_barrier_forward\s*\(\s*\)",
            r"entries\s*\[\s*i\s*\]\s*=\s*0\s*;",
            r"fs_write_metadata_block\s*\(\s*bp\s*\)",
            r"fs_durable_barrier_forward\s*\(\s*\)",
        ),
        failures,
    )
    truncate_body = function_body(fs, "itruncate_detach_partial") or ""
    inode_publish = re.search(
        r"ip->size\s*=\s*size\s*;.*?iupdate\s*\(\s*ip\s*\).*?"
        r"fs_durable_barrier_forward\s*\(\s*\)",
        truncate_body,
        re.DOTALL,
    )
    first_clear = re.search(
        r"entries\s*\[[^\]]+\]\s*=\s*0\s*;", truncate_body
    )
    if inode_publish is None or first_clear is None or (
        first_clear.start() < inode_publish.end()
    ):
        failures.append("itruncate_detach_partial: indirect entries clear before durable EOF")

    require_order(
        function_body(fs, "fs_scrub_mark_inode_blocks"),
        "fs_scrub_mark_inode_blocks",
        (
            r"indirect_needed\s*=",
            r"i\s*<\s*indirect_needed",
            r"fs_scrub_mark_block",
            r"fs_scrub_clear_indirect_suffix_forward",
        ),
        failures,
    )
    require_order(
        function_body(fs, "fs_scrub_clear_indirect_suffix_forward"),
        "fs_scrub_clear_indirect_suffix_forward",
        (
            r"entries\s*\[\s*i\s*\]\s*=\s*0\s*;",
            r"fs_write_metadata_block\s*\(\s*bp\s*\)",
            r"fs_durable_barrier_forward\s*\(\s*\)",
        ),
        failures,
    )

    for helper, call in (
        ("fs_qmap_write_forward", r"fs_qmap_write\s*\("),
        ("fs_bitmap_write_forward", r"fs_bitmap_write\s*\("),
        ("fs_scrub_clear_indirect_suffix_forward", r"fs_read_block\s*\("),
    ):
        require_call_before_first_return(
            function_body(fs, helper), helper, call, failures
        )
    require_forward_helper_shape(
        function_body(fs, "fs_qmap_write_forward"),
        "fs_qmap_write_forward",
        r"fs_qmap_write\s*\(",
        failures,
    )
    require_forward_helper_shape(
        function_body(fs, "fs_bitmap_write_forward"),
        "fs_bitmap_write_forward",
        r"fs_bitmap_write\s*\(",
        failures,
    )
    for critical in (
        "balloc",
        "balloc_one",
        "balloc_epoch",
        "fs_block_candidate_refill",
        "fs_block_candidate_drain",
        "bfree",
        "ialloc",
        "inode_remove_detach",
        "bmap",
        "itruncate_detach_partial",
        "fs_qmap_write_forward",
        "fs_bitmap_write_forward",
        "fs_scrub_clear_indirect_suffix_forward",
    ):
        reject_constant_early_exit(function_body(fs, critical), critical, failures)

    for name in ("fs_scrub_mark_block", "fs_mount_scrub", "fs_reap_boot_workflow_objects"):
        body = function_body(fs, name)
        if body is None:
            failures.append(f"missing mount recovery function {name}")
            continue
        if re.search(r"\bfs_(?:qmap|bitmap)_write\s*\(", body):
            failures.append(f"{name}: raw allocation-map write bypasses forward retry")
        if "panic(" in body:
            failures.append(f"{name}: mount recovery may not panic")
    mount = function_body(fs, "fs_mount_scrub") or ""
    retire = function_body(fs, "fs_scrub_retire_inode_forward") or ""
    if not re.search(
        r"dip->type\s*==\s*0\s*&&\s*dip->fs_owner_domain\s*==\s*FS_OWNER_NONE",
        retire,
    ):
        failures.append("fs_mount_scrub: free inode transition is not recovered")
    if "fs_scrub_retire_inode_forward" not in mount or not re.search(
        r"inodes_changed\s*&&\s*fs_durable_barrier_forward", mount
    ):
        failures.append("fs_mount_scrub: inode retirement is not durable before block release")

    profile_contracts = (
        (makefile, "$(filter-out $K/fs_allocator_test.c,$(C_SRCS))", "production object filter"),
        (makefile, "-DFS_ALLOCATOR_FAULT_TEST_PROFILE", "profile define"),
        (loader, "fs_allocator_test_bind_boot_init(p, INIT_PROC);", "sealed loader binding"),
        (syscall, "SYS_fs_allocator_fault_test", "profile syscall"),
        (test_owner, "#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE", "source profile guard"),
        (makefile, "DURABILITY_POWERCUT_TEST_PROFILE := 1", "volatile durability profile"),
        (makefile, "-DDURABILITY_POWERCUT_TEST_PROFILE", "volatile durability define"),
        (virtio_h, "VIRTIO_DURABILITY_TEST_ABI_VERSION", "durability backend ABI"),
        (
            test_owner,
            "FSALLOC_DURABILITY_BACKEND_ABI_VERSION ==",
            "durability ABI equality assertion",
        ),
        (
            makefile,
            "FS_ALLOCATOR_DELETE_BARRIER_MUTANT requires "
            "FS_ALLOCATOR_FAULT_TEST_PROFILE=1",
            "mutant profile containment",
        ),
        (fs, "#ifdef FS_ALLOCATOR_DELETE_BARRIER_MUTANT", "phase barrier negative mutant"),
    )
    for source, token, label in profile_contracts:
        if token not in source:
            failures.append(f"allocator profile missing {label}")
    for source, label in ((client, "user"), (test_owner, "kernel")):
        for token in (
            "backend_instance_id=",
            "abi_version=%u capacity_bytes=%u",
            "raw_write_count=",
            "cached_write_count=",
            "last_acknowledged_sequence=",
        ):
            if token not in source:
                failures.append(f"allocator {label} receipt missing {token}")
    if "parent passed" in client:
        failures.append("allocator client retains a hard-coded pass marker")
    for token, label in (
        ("source \"${SCRIPT_DIR}/evidence-wiring.sh\"", "evidence wiring"),
        ("evidence_append_guest_log", "Guest evidence append"),
        ("build_profile_kernel", "single profile build"),
        (
            "fsalloc_trusted_python scripts/fs-allocator-evidence.py record-case",
            "semantic raw-image evidence verification",
        ),
        ("record-stage", "runtime durability receipt capture"),
        ("record-mutation", "dynamic barrier-deletion mutation"),
        ("verify-archive", "self-contained evidence archive verification"),
        ('cp "${user_elf}" "${user_target}/elf/"', "paired ELF image"),
        (
            'run_case "${tag}" "${marker}" powercut fault',
            "SIGKILL power-cut completion",
        ),
        ("grace=0s", "zero-grace power cut"),
        ("scripts/fs-allocator-evidence.py clean-exec", "controlled child environment"),
        ('PRIVATE_HOME="${TMPDIR_FSALLOC}/home"', "private formal HOME"),
        ("--internal-hermetic-shell", "clean formal shell bootstrap"),
        ("/usr/bin/env -i", "empty formal shell environment"),
        ("unset ASAN_OPTIONS UBSAN_OPTIONS", "untrusted sanitizer option removal"),
        ('"${MAKE_BIN}" build', "attested absolute Make invocation"),
        ('CC="${CROSS_GCC}"', "attested cross compiler invocation"),
        ('LD="${CROSS_LD}"', "attested cross linker invocation"),
        ("materialize-source", "captured source materialization"),
        ('cd "${SOURCE_SNAPSHOT}"', "private source-root build"),
        ("source_boundary before-profile-build", "kernel build preflight"),
        ("source_boundary before-seal", "seal source preflight"),
        ("source_boundary after-archive-verify", "final source recheck"),
    ):
        if token not in runner:
            failures.append(f"allocator runner missing {label}")
    if "Draft dynamic" in runner or "make -B build" in runner:
        failures.append("allocator runner retains draft or per-case kernel build")
    runner_order = (
        "capture-run",
        'TRUSTED_PYTHON_ENTRY="${EVIDENCE_ROOT}/sources/scripts/trusted-python-entry.py"',
        "materialize-source",
        'cd "${SOURCE_SNAPSHOT}"',
        "source_boundary post-materialize",
        "source_boundary before-profile-build",
        "source_boundary before-seal",
        "seal-run",
        "source_boundary after-seal",
        "source_boundary before-pack",
        "pack",
        "source_boundary after-archive-verify",
    )
    cursor = 0
    for token in runner_order:
        found = runner.find(token, cursor)
        if found < 0:
            failures.append(f"allocator runner missing or misordered {token}")
            break
        cursor = found + len(token)
    if re.search(
        r'^[ \t]*"\$\{PYTHON_BIN\}"(?!\s+-I\s+-S\s+-B)',
        runner,
        re.MULTILINE,
    ):
        failures.append("allocator runner contains a non-isolated Python invocation")
    if not re.search(
        r'runner_argv=\(\s*"\$\{PYTHON_BIN\}"\s+-I\s+-S\s+-B\s+'
        r'"\$\{TRUSTED_PYTHON_ENTRY\}"\s+scripts/agent_test_runner\.py',
        runner,
        re.DOTALL,
    ):
        failures.append("allocator QEMU runner is not bound to trusted isolated Python")
    if runner.count('"${MAKE_BIN}" build') != 2:
        failures.append("allocator kernel builds are not both bound to attested Make")

    for token, label in (
        ("CONTROLLED_ENV_PASSTHROUGH", "formal environment allowlist"),
        ("def controlled_environment", "formal environment constructor"),
        ("def clean_exec", "controlled child launcher"),
        ('"PATH": "/usr/bin:/bin"', "fixed formal executable search path"),
        ("def _git_head_source_payloads", "Git HEAD blob verifier"),
        ('"ls-files", "-v", "-z"', "Git index flag verifier"),
        ('"cat-file", "blob"', "Git committed blob reader"),
        ("def attested_tool_path", "attested tool resolver"),
        ("def _normalize_case_build_argv", "canonical user build argv"),
        ("differs from the canonical profile build", "canonical kernel build argv"),
        (
            '"ASAN_OPTIONS": "detect_leaks=0:halt_on_error=1"',
            "fixed ASAN fail-closed policy",
        ),
        ('"UBSAN_OPTIONS": "halt_on_error=1"', "fixed UBSAN fail-closed policy"),
        ("def materialize_source", "source materializer"),
        ("def verify_source_boundary", "source boundary verifier"),
        ('"tree": tree', "Git tree attestation"),
        ("records_after != source_records", "capture double sampling"),
    ):
        if token not in evidence:
            failures.append(f"allocator evidence missing {label}")
    capture_start = evidence.find("def capture_run(")
    capture_end = evidence.find("\ndef ", capture_start + 1)
    capture_body = evidence[capture_start:capture_end]
    if capture_body.count("_require_sources_match_commit") != 2:
        failures.append("capture-run does not double-sample committed source blobs")
    boundary_start = evidence.find("def verify_source_boundary(")
    boundary_end = evidence.find("\ndef ", boundary_start + 1)
    boundary_body = evidence[boundary_start:boundary_end]
    boundary_cursor = 0
    for pattern in (
        r"_load_run\s*\(",
        r"_git_source_state\s*\(",
        r"commit\s*!=\s*run\[",
        r"tree\s*!=\s*run\[",
        r"or\s+status",
        r"_verify_source_tree_against_run\s*\(\s*live_source_root",
        r"_verify_source_tree_against_run\s*\(\s*snapshot_root",
    ):
        match = re.search(pattern, boundary_body[boundary_cursor:], re.DOTALL)
        if match is None:
            failures.append(f"verify_source_boundary: missing or misordered {pattern}")
            break
        boundary_cursor += match.end()
    if "fsalloc_trusted_python scripts/host_probe_toolchain.py" not in host_probe:
        failures.append("host probe setup bypasses trusted Python")
    if host_probe.count("fsalloc_clean_exec") < 2:
        failures.append("host probe compile/run bypasses controlled environment")
    for entrypoint in (
        '"scripts/agent_test_runner.py"',
        '"scripts/check-fs-allocator-state.py"',
        '"scripts/host_probe_toolchain.py"',
        '"scripts/test-fs-allocator-image.py"',
    ):
        if entrypoint not in trusted_entry:
            failures.append(f"trusted Python allowlist missing {entrypoint}")
    if not re.search(
        r"def\s+_powercut_supervisor_command\s*\(.*?return\s*\[\s*sys\.executable,\s*"
        r'"-I",\s*"-S",\s*"-B",',
        agent_runner,
        re.DOTALL,
    ):
        failures.append("powercut supervisor loses isolated Python flags")

    require_order(
        function_body(virtio, "disk_durability_barrier"),
        "disk_durability_barrier overlay commit",
        (
            r"disk_durability_overlay_enter\s*\(\s*\)",
            r"disk_submit\s*\(\s*&disk\.durability_commit_buf\s*,\s*"
            r"VIRTIO_BLK_T_OUT",
            r"durability_stats\.raw_writes\+\+",
            r"disk_submit\s*\(\s*0\s*,\s*VIRTIO_BLK_T_FLUSH",
            r"durability_overlay\s*\[\s*slot\s*\]\.valid\s*=\s*0",
            r"durability_count\s*=\s*0",
            r"durability_stats\.last_acknowledged_sequence\s*=\s*"
            r"after_sequence",
            r"durability_stats\.epoch\+\+",
            r"disk_durability_overlay_leave\s*\(\s*\)",
        ),
        failures,
    )
    submit_start = virtio.find("static int disk_submit(")
    submit_open = virtio.find("{", submit_start)
    submit = function_body(virtio, "disk_submit") or ""
    if (submit_start < 0 or submit_open < 0 or
            "account_transfer" in virtio[submit_start:submit_open]):
        failures.append("disk_submit retains a physical-accounting bypass")
    if not re.search(
        r"if\s*\(\s*submitted\s*\)\s*bio_account_transfer\s*\(", submit
    ):
        failures.append("disk_submit is not the unique physical-accounting boundary")
    if virtio.count("bio_account_transfer(") != 1:
        failures.append("VirtIO physical accounting is aggregated outside submission")
    for token in (
        "BIO_PHYSICAL_STATS_VERSION",
        "int bio_physical_snapshot(struct bio_physical_stats *",
    ):
        if token not in bio_h:
            failures.append(f"global physical I/O ABI missing {token}")
    # 通用批量记账由 test-virtio-disk-wiring.py 和 test-bio-rate-controller.py
    # 检查；本检查器仅负责分配器收据。
    for token in (
        "physical_write_delta != raw_delta",
        "physical_flush_delta != 1",
        "after->physical_failed_transfers !=",
        "verify_operation_io_receipt(&before, &after);",
    ):
        if token not in client:
            failures.append(f"allocator flush receipt missing {token}")
    for token in (
        "bio_physical_snapshot(&physical)",
        "durability.raw_writes - fs_allocator_fault.raw_writes_before !=",
        "physical.writes - fs_allocator_fault.physical_writes_before",
        "durability.successful_flushes - fs_allocator_fault.flushes_before !=",
        "physical.flushes - fs_allocator_fault.physical_flushes_before",
    ):
        if token not in test_owner:
            failures.append(f"allocator crash receipt missing {token}")
    for token in (
        "AGENT_META_STORE_NAMES",
        "_validate_agent_metadata_records",
        "_validate_agent_durable_arena",
        "validate_observation_payload",
        "generation_images",
        "identity_generations",
        "required=require_metadata_cow",
    ):
        if token not in image_tool:
            failures.append(f"metadata COW evidence invariant missing {token}")
    if evidence.count('"--require-metadata-cow"') < 3:
        failures.append("formal allocator evidence does not require metadata COW banks")
    require_order(
        function_body(fs, "fs_create"),
        "fs_create busy rollback",
        (
            r"vfs_create_request_authorize\s*\(",
            r"ialloc\s*\(",
            r"ip->fs_owner_domain\s*=\s*charge.owner\s*;",
            r"vfs_inode_init_label\s*\(\s*ip\s*,\s*cred\s*,\s*policy\s*\)",
            r"fs_io_fail\s*\(\s*FS_FAILURE_METADATA_WRITE_INDETERMINATE\s*\)",
            r"lookup_status\s*=\s*fs_allocator_fault_before\s*\(\s*"
            r"FSALLOC_OP_IALLOC\s*,\s*FSALLOC_PHASE_OWNER\s*,\s*0\s*\)\s*;",
            r"iupdate\s*\(\s*ip\s*\)",
            r"fs_durable_barrier_forward\s*\(\s*\)",
        ),
        failures,
    )
    require_order(
        function_body(virtio, "virtio_disk_rw"),
        "virtio_disk_rw overlay",
        (
            r"disk_durability_overlay_enter\s*\(\s*\)",
            r"disk_durability_overlay_store\s*\(\s*b\s*\)",
            r"disk_durability_overlay_find\s*\(\s*b->blockno\s*\)",
            r"disk_durability_overlay_leave\s*\(\s*\)",
        ),
        failures,
    )
    require_order(
        function_body(virtio, "virtio_disk_durability_test_stats"),
        "virtio_disk_durability_test_stats",
        (
            r"disk_durability_overlay_enter\s*\(\s*\)",
            r"\*stats\s*=\s*disk\.durability_stats",
            r"disk_durability_overlay_leave\s*\(\s*\)",
        ),
        failures,
    )
    require_order(
        function_body(syscall, "sys_fs_allocator_fault_test"),
        "sys_fs_allocator_fault_test",
        (
            r"case\s+FSALLOC_TEST_ARM",
            r"bio_durable_flush\s*\(\s*\)",
            r"fs_allocator_test_arm",
            r"case\s+FSALLOC_TEST_FLUSH",
            r"bio_durable_flush\s*\(\s*\)",
        ),
        failures,
    )
    prepare_tokens = (
        "if (crash_stage == 0)",
        "write_crash_boot_stage('P', 1);",
        'flush_with_receipt("prepare", &after);',
        '"prepared=1\\n"',
    )
    prepare_cursor = 0
    for token in prepare_tokens:
        found = client.find(token, prepare_cursor)
        if found < 0:
            failures.append(
                f"fsallocfault prepare receipt: missing or misordered {token}"
            )
            break
        prepare_cursor = found + len(token)
    return failures


def main() -> int:
    paths = (
        "os/fs.c",
        "os/fs.h",
        "os/fs_epoch.c",
        "os/fs_epoch.h",
        "Makefile",
        "os/loader.c",
        "os/syscall.c",
        "os/fs_allocator_test.c",
        "scripts/run-fs-allocator-fault-tests.sh",
        "os/virtio_disk.c",
        "os/virtio.h",
        "os/bio.h",
        "os/proc.c",
        "os/agent_core.c",
        "user/src/fsallocfault_ucore.c",
        "scripts/fs-allocator-image.py",
        "scripts/fs-allocator-evidence.py",
        "scripts/host-probe-toolchain.sh",
        "scripts/trusted-python-entry.py",
        "scripts/agent_test_runner.py",
    )
    sources = {path: (ROOT / path).read_text(encoding="utf-8") for path in paths}
    failures = check_sources(sources)

    mutations = {
        "dead-live-commit": (
            "os/fs.c",
            "result = fs_qmap_write_forward(dev, block, charge->owner);",
            "if (0) { result = fs_qmap_write_forward(dev, block, charge->owner); }\n\tresult = 0;",
        ),
        "lost-refund": (
            "os/fs.c",
            "fs_storage_release(owner, 0);",
            "(void)owner;",
        ),
        "truncate-hole-order": (
            "os/fs.c",
            "ip->size = size;\n\tresult = iupdate(ip);",
            "entries[first_indirect] = 0;\n\tip->size = size;\n\tresult = iupdate(ip);",
        ),
        "qmap-forward-early-success": (
            "os/fs.c",
            "static int fs_qmap_write_forward(int dev, uint block, uint state)\n{",
            "static int fs_qmap_write_forward(int dev, uint block, uint state)\n{\n\treturn 0;",
        ),
        "bitmap-forward-early-success": (
            "os/fs.c",
            "static int fs_bitmap_write_forward(int dev, uint block, int allocated)\n{",
            "static int fs_bitmap_write_forward(int dev, uint block, int allocated)\n{\n\treturn 0;",
        ),
        "balloc-fast-path-disabled": (
            "os/fs.c",
            "return balloc_epoch(dev, charge, error);",
            "return balloc_one(dev, charge, error);",
        ),
        "candidate-refill-reserves-quota": (
            "os/fs.c",
            "magazine = fs_block_magazine_find(owner, 1);",
            "magazine = fs_block_magazine_find(owner, 1);\n\t(void)fs_storage_reserve(0, 0);",
        ),
        "candidate-refill-zeroes-block": (
            "os/fs.c",
            "fs_block_candidate_set(block);",
            "(void)bzero(dev, block);\n\t\t\t\tfs_block_candidate_set(block);",
        ),
        "candidate-cache-bypass": (
            "os/fs.c",
            "fs_block_candidate_reclaim(b + bi) < 0",
            "0 /* candidate index ignored */",
        ),
        "epoch-intent-restored": (
            "os/fs.c",
            "((uint *)qmap_bp->data)[block % QPB] = charge->owner;",
            "((uint *)qmap_bp->data)[block % QPB] = "
            "FS_QMAP_ALLOCATING_FLAG | charge->owner;",
        ),
        "epoch-intermediate-barrier": (
            "os/fs.c",
            "qmap_changed = 1;",
            "qmap_changed = 1;\n\t(void)fs_durable_barrier();",
        ),
        "epoch-gate-handoff-restored": (
            "os/fs_epoch.c",
            "wait_queue_wake_all(&epoch.request_queue);",
            "wait_queue_wake_one_thread(&epoch.request_queue);",
        ),
        "epoch-namespace-order-swapped": (
            "os/fs_epoch.h",
            "FS_EPOCH_NAMESPACE_DETACH,\n\tFS_EPOCH_INODE,",
            "FS_EPOCH_INODE,\n\tFS_EPOCH_NAMESPACE_DETACH,",
        ),
        "epoch-opposite-namespace-merged": (
            "os/fs_epoch.c",
            "if ((entry->phase == FS_EPOCH_NAMESPACE_DETACH &&\n"
            "\t\t     phase == FS_EPOCH_NAMESPACE_ATTACH) ||\n"
            "\t\t    (entry->phase == FS_EPOCH_NAMESPACE_ATTACH &&\n"
            "\t\t     phase == FS_EPOCH_NAMESPACE_DETACH)) {",
            "if (0) {",
        ),
        "reclaim-fence-allows-empty-epoch": (
            "os/fs_epoch.c",
            "!fs_epoch_dirty_locked() || epoch.active_generation == 0 ||",
            "epoch.active_generation == (uint64)-1 ||",
        ),
        "reclaim-release-before-commit": (
            "os/fs.c",
            "result = fs_epoch_commit();\n\tif (result < 0)\n\t\tgoto out_unlock;\n\tfor (uint i = 0; i < plan_count; i++) {",
            "for (uint i = 0; i < plan_count; i++) {",
        ),
        "sync-skips-owner-reclaim": (
            "os/syscall.c",
            "result = fs_deferred_reclaim_drain_current();",
            "result = 0;",
        ),
        "idle-aged-commit-disabled": (
            "os/proc.c",
            "t == NULL && fs_epoch_should_commit() &&",
            "0 && fs_epoch_should_commit() &&",
        ),
        "bitmap-forward-goto-bypass": (
            "os/fs.c",
            "static int fs_bitmap_write_forward(int dev, uint block, int allocated)\n{\n\tint result;",
            "static int fs_bitmap_write_forward(int dev, uint block, int allocated)\n{\n\tint result;\n\tgoto bypass_bitmap;",
        ),
        "missing-prepare-flush": (
            "user/src/fsallocfault_ucore.c",
            "flush_with_receipt(\"prepare\", &after);",
            "snapshot(&after);",
        ),
        "missing-paired-elf": (
            "scripts/run-fs-allocator-fault-tests.sh",
            'cp "${user_elf}" "${user_target}/elf/"',
            ": # paired ELF copy deleted",
        ),
        "forged-client-pass": (
            "user/src/fsallocfault_ucore.c",
            'puts("fsallocfault_ucore: runtime_verified=1");',
            'puts("fsallocfault_ucore: parent passed");',
        ),
        "missing-real-overlay-flush": (
            "os/virtio_disk.c",
            "result = disk_submit(0, VIRTIO_BLK_T_FLUSH,\n"
            "\t\t\t\t     test_direct, 0);",
            "result = VIRTIO_DISK_OK; /* deleted real FLUSH */",
        ),
        "missing-operation-physical-receipt": (
            "user/src/fsallocfault_ucore.c",
            "verify_operation_io_receipt(&before, &after);",
            "verify_operation_io_receipt_disabled(&before, &after);",
        ),
        "unstable-overlay-stats": (
            "os/virtio_disk.c",
            "if (disk_durability_overlay_enter() != VIRTIO_DISK_OK) {",
            "if (0) {",
        ),
        "raw-mount-write": (
            "os/fs.c",
            "fs_qmap_write_forward(dev, block, expected_owner)",
            "fs_qmap_write(dev, block, expected_owner)",
        ),
        "soft-crash": (
            "scripts/run-fs-allocator-fault-tests.sh",
            'run_case "${tag}" "${marker}" powercut fault',
            'run_case "${tag}" "${marker}" checkpoint fault',
        ),
        "missing-verifier": (
            "scripts/run-fs-allocator-fault-tests.sh",
            "fsalloc_trusted_python scripts/fs-allocator-evidence.py record-case",
            "fsalloc_trusted_python scripts/fs-allocator-evidence.py record-stage",
        ),
        "nonisolated-formal-python": (
            "scripts/run-fs-allocator-fault-tests.sh",
            '"${PYTHON_BIN}" -I -S -B "${TRUSTED_PYTHON_ENTRY}"',
            '"${PYTHON_BIN}" "${TRUSTED_PYTHON_ENTRY}"',
        ),
        "lost-clean-environment": (
            "scripts/run-fs-allocator-fault-tests.sh",
            "scripts/fs-allocator-evidence.py clean-exec --",
            "scripts/fs-allocator-evidence.py verify --",
        ),
        "live-tree-build": (
            "scripts/run-fs-allocator-fault-tests.sh",
            'cd "${SOURCE_SNAPSHOT}"',
            'cd "${LIVE_SOURCE_ROOT}"',
        ),
        "missing-final-source-recheck": (
            "scripts/run-fs-allocator-fault-tests.sh",
            "source_boundary after-archive-verify",
            ": # final source recheck deleted",
        ),
        "nonisolated-powercut-supervisor": (
            "scripts/agent_test_runner.py",
            'sys.executable,\n        "-I",\n        "-S",\n        "-B",',
            "sys.executable,",
        ),
        "missing-hermetic-shell": (
            "scripts/run-fs-allocator-fault-tests.sh",
            "/usr/bin/env -i",
            "/usr/bin/env",
        ),
        "unbound-make-tool": (
            "scripts/run-fs-allocator-fault-tests.sh",
            '"${MAKE_BIN}" build',
            "make build",
        ),
        "missing-head-blob-sample": (
            "scripts/fs-allocator-evidence.py",
            "_require_sources_match_commit(source_root, git, commit, source_payloads)",
            "_require_guest_source_inventory(source_root, \"captured\")",
        ),
    }
    for name, (path, old, new) in mutations.items():
        mutated = dict(sources)
        if old not in mutated[path]:
            failures.append(f"mutation fixture {name} no longer matches")
            continue
        mutated[path] = mutated[path].replace(old, new, 1)
        if not check_sources(mutated):
            failures.append(f"allocator checker missed {name} mutation")

    if failures:
        for failure in failures:
            print(f"fs-allocator-state: {failure}", file=sys.stderr)
        return 1
    print("fs-allocator-state: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
