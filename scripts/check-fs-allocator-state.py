#!/usr/bin/env python3
"""Reject regressions in the durable filesystem allocator state machine."""

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
    makefile = sources["Makefile"]
    loader = sources["os/loader.c"]
    syscall = sources["os/syscall.c"]
    test_owner = sources["os/fs_allocator_test.c"]
    runner = sources["scripts/run-fs-allocator-fault-tests.sh"]
    virtio = sources["os/virtio_disk.c"]
    virtio_h = sources["os/virtio.h"]
    client = sources["user/src/fsallocfault_ucore.c"]

    for token in (
        "#define FS_QMAP_STATE_MASK 0xc0000000U",
        "#define FS_QMAP_ALLOCATING_FLAG 0x40000000U",
        "#define FS_QMAP_FREEING_FLAG 0xc0000000U",
    ):
        if token not in fs_h:
            failures.append(f"os/fs.h: missing four-state contract {token}")

    require_order(
        function_body(fs, "balloc"),
        "balloc",
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
    balloc_body = function_body(fs, "balloc") or ""
    reserve = re.search(r"fs_storage_reserve\s*\(\s*charge\s*,\s*0\s*\)", balloc_body)
    valid_guard = re.search(
        r"if\s*\(\s*charge\s*==\s*0\s*\|\|\s*"
        r"fs_allocator_gate_lock\s*\(\s*0\s*\)\s*<\s*0\s*\)\s*"
        r"return\s+0\s*;",
        balloc_body,
        re.DOTALL,
    )
    if reserve is None or valid_guard is None or valid_guard.end() > reserve.start():
        failures.append("balloc: missing exact invalid-input/gate guard")
    elif len(re.findall(r"\breturn\b", balloc_body[: reserve.start()])) != 1:
        failures.append("balloc: unexpected early return before quota reservation")
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
            r"fs_write_metadata_block\s*\(\s*bp\s*\)",
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
        function_body(fs, "bmap"),
        "bmap indirect publish",
        (
            r"a\s*\[\s*bn\s*\]\s*=\s*candidate\s*;",
            r"fs_write_metadata_block\s*\(\s*bp\s*\)",
            r"brelse\s*\(\s*bp\s*\)",
            r"fs_durable_barrier_forward\s*\(\s*\)",
            r"addr\s*=\s*candidate\s*;",
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
        ("record-case", "semantic raw-image evidence verification"),
        ("record-stage", "runtime durability receipt capture"),
        ("record-mutation", "dynamic barrier-deletion mutation"),
        ("verify-archive", "self-contained evidence archive verification"),
        (
            'run_case "${tag}" "${marker}" powercut fault',
            "SIGKILL power-cut completion",
        ),
        ("grace=0s", "zero-grace power cut"),
    ):
        if token not in runner:
            failures.append(f"allocator runner missing {label}")
    if "Draft dynamic" in runner or "make -B build" in runner:
        failures.append("allocator runner retains draft or per-case kernel build")

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
        "Makefile",
        "os/loader.c",
        "os/syscall.c",
        "os/fs_allocator_test.c",
        "scripts/run-fs-allocator-fault-tests.sh",
        "os/virtio_disk.c",
        "os/virtio.h",
        "user/src/fsallocfault_ucore.c",
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
        "lost-indirect-publish-barrier": (
            "os/fs.c",
            "result = fs_durable_barrier_forward();\n\t\t\t\t\tif (result < 0)\n\t\t\t\t\t\treturn bmap_error(error, result);\n\t\t\t\t\taddr = candidate;",
            "addr = candidate;",
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
        "balloc-valid-input-early-success": (
            "os/fs.c",
            "static uint balloc(uint dev, const struct fs_storage_charge *charge, int *error)\n{",
            "static uint balloc(uint dev, const struct fs_storage_charge *charge, int *error)\n{\n\tif (charge != 0) return 0;",
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
        "forged-client-pass": (
            "user/src/fsallocfault_ucore.c",
            'puts("fsallocfault_ucore: runtime_verified=1");',
            'puts("fsallocfault_ucore: parent passed");',
        ),
        "missing-real-overlay-flush": (
            "os/virtio_disk.c",
            "result = disk_submit(0, VIRTIO_BLK_T_FLUSH,\n"
            "\t\t\t\t     test_direct, 0, 0);",
            "result = VIRTIO_DISK_OK; /* deleted real FLUSH */",
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
            "scripts/fs-allocator-evidence.py record-case",
            "scripts/fs-allocator-evidence.py record-stage",
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
