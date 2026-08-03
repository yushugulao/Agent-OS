#!/usr/bin/env python3
"""Mutation guards for thread-bound background I/O context."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIO = (ROOT / "os/bio.c").read_text(encoding="utf-8")
BIO_H = (ROOT / "os/bio.h").read_text(encoding="utf-8")
FS = (ROOT / "os/fs.c").read_text(encoding="utf-8")
IOBUDGET = (ROOT / "user/src/iobudget_ucore.c").read_text(encoding="utf-8")


class ContractError(RuntimeError):
    pass


def function_body(source: str, name: str) -> str:
    marker = f"{name}("
    search = 0
    while True:
        start = source.find(marker, search)
        if start < 0:
            raise ContractError(f"missing function {name}")
        opening = source.find("(", start)
        parens = 0
        closing = -1
        for index in range(opening, len(source)):
            if source[index] == "(":
                parens += 1
            elif source[index] == ")":
                parens -= 1
                if parens == 0:
                    closing = index
                    break
        if closing < 0:
            raise ContractError(f"unterminated signature {name}")
        brace = source.find("{", closing)
        semicolon = source.find(";", closing)
        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            depth = 0
            for index in range(brace, len(source)):
                if source[index] == "{":
                    depth += 1
                elif source[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return source[brace + 1:index]
            raise ContractError(f"unterminated body {name}")
        search = closing + 1


def compact(text: str) -> str:
    return " ".join(text.split())


def replace_in_function(source: str, name: str, old: str, new: str) -> str:
    body = function_body(source, name)
    if old not in body:
        raise ContractError(f"mutation anchor drift: {name}: {old}")
    return source.replace(body, body.replace(old, new, 1), 1)


def validate(bio: str, bio_h: str, fs: str) -> None:
    context = bio[bio.index("struct io_background_context {"):
                  bio.index("};", bio.index("struct io_background_context {") + 1)]
    for token in (
        "struct thread *executor;",
        "uint64 executor_generation;",
        "int boot_executor;",
        "int cache_wait_pending;",
        "uint64 cache_wait_sequence;",
    ):
        if token not in context:
            raise ContractError(f"background context missing {token}")

    current = compact(function_body(bio, "bio_background_current"))
    for token in (
        "io_policy.background.active",
        "io_policy.background.executor == 0",
        "io_policy.background.executor != thread",
        "io_policy.background.executor_generation != thread->identity_generation",
        "io_policy.background.boot_executor",
        "!io_policy.runtime_ready",
        "io_policy.background.executor_generation == 0",
        "io_policy.runtime_ready",
        "io_policy.background.executor_generation != 0",
    ):
        if token not in current:
            raise ContractError(f"current-executor predicate missing {token}")

    begin = compact(function_body(bio, "bio_background_begin"))
    for token in (
        "io_policy.background.active || executor == 0",
        "!io_policy.runtime_ready && executor->identity_generation != 0",
        "executor->state != RUNNING || executor->tid < 0",
        "executor->process == 0 || executor->identity_generation == 0",
        "executor->bio_buffer_holds != 0",
        "executor->bio_fs_atomic_depth != 0",
        "io_policy.background.executor = executor;",
        "io_policy.background.executor_generation = executor->identity_generation;",
        "io_policy.background.boot_executor = !io_policy.runtime_ready;",
    ):
        if token not in begin:
            raise ContractError(f"background begin missing {token}")

    end = function_body(bio, "bio_background_end")
    if "if (!bio_background_current())" not in end:
        raise ContractError("background end is not restricted to its executor")

    retained = function_body(bio, "bio_cache_owner_retained")
    if "io_policy.background.active" not in retained:
        raise ContractError("active cleanup cache floor is not retained globally")
    # Direct active state is only lifetime state. Every ambient caller path
    # must go through the executor-and-generation predicate.
    if bio.count("io_policy.background.active") != 7:
        raise ContractError("ambient background-active access escaped its allowlist")

    debt_wait = compact(function_body(bio, "io_wait_for_debt"))
    for token in (
        "closing_background = cleanup && io_class == IO_POLICY_CLASS_BACKGROUND",
        "bio_background_current() && state->active_requests != 0",
        "state->owner == io_policy.background.owner",
        "(state->retiring || state->quiesced) && !closing_background",
    ):
        if token not in debt_wait:
            raise ContractError(f"closing background debt settlement missing {token}")

    checkpoint = compact(function_body(bio, "bio_request_checkpoint_mode"))
    for token in (
        "if (cleanup && quiescent)",
        "io_wait_for_debt( state, IO_POLICY_CLASS_BACKGROUND, 1)",
        "io_wait_for_device_debt( owner, IO_POLICY_CLASS_BACKGROUND, 1)",
        "bio_background_wait_for_cache_progress()",
        "if (state == 0) panic(\"background I/O owner vanished\")",
        "return bio_checkpoint_make(BIO_CHECKPOINT_READY)",
    ):
        if token not in checkpoint:
            raise ContractError(f"forward cleanup settlement missing {token}")
    if "if (quiescent) panic(\"background quiescent checkpoint holds buffer\")" not in checkpoint:
        raise ContractError("background quiescent checkpoint can retain a buffer")

    settle = compact(function_body(bio, "bio_request_settle_quiescent_cleanup"))
    if ("bio_request_checkpoint_mode(1, 1)" not in settle or
            "if (result.state == BIO_CHECKPOINT_DEFERRED) panic(" not in settle):
        raise ContractError("forward-only public API can expose DEFERRED")

    release_closed = compact(function_body(bio, "bio_cache_release_closed_owner"))
    if ("if (bio_cache_owner_retained(owner)) return" not in release_closed or
            "bcache.buf[i].cache_owner == owner" not in release_closed or
            "bcache.buf[i].refcnt == 0" not in release_closed):
        raise ContractError("closed-owner cache partition release is incomplete")
    for name in ("bio_scope_quiesce", "bio_scope_retire", "bio_background_end"):
        release = compact(function_body(bio, name))
        if "bio_cache_release_closed_owner(owner)" not in release:
            raise ContractError(f"{name} can strand or destroy cleanup cache state")
    background_end = compact(function_body(bio, "bio_background_end"))
    if "if (state == 0) panic(\"background I/O owner vanished at end\")" not in background_end:
        raise ContractError("background end can silently leak a vanished owner")
    if background_end.index("memset(&io_policy.background") > background_end.index(
            "bio_cache_release_closed_owner(owner)"):
        raise ContractError("background cache floor is still active during final release")
    if background_end.index("bio_cache_release_closed_owner(owner)") > \
            background_end.index("io_active_request_release(state)"):
        raise ContractError("background owner is unpinned before cache settlement")

    cache_wait = compact(function_body(bio, "bio_background_wait_for_cache_progress"))
    for token in (
        "cache_wait_sequence == cache_progress_sequence",
        "wait_queue_sleep_irq_uninterruptible(&cache_waiters)",
        "cache_wait_pending = 0",
    ):
        if token not in cache_wait:
            raise ContractError(f"cache progress wait missing {token}")
    if "bio_cache_note_progress()" not in function_body(bio, "brelse"):
        raise ContractError("buffer holder release cannot wake forward cleanup")
    if "bio_cache_note_progress()" not in function_body(bio, "bunpin"):
        raise ContractError("buffer unpin cannot wake forward cleanup")
    if "bio_cache_note_progress()" not in function_body(bio, "bio_cache_invalidate"):
        raise ContractError("cache invalidation cannot wake forward cleanup")

    required_current_counts = {
        "bio_request_checkpoint_mode": 1,
        "bio_fs_atomic_enter": 1,
        "bio_fs_atomic_leave": 1,
        "bio_background_active": 1,
        "bio_background_end": 1,
        "bio_current_owner": 1,
        "bio_current_class": 1,
        "bio_account_transfer": 1,
        "bio_current_cache_owner": 1,
        "bio_cache_holder_token": 1,
        "bget": 5,
        "bclaim": 2,
    }
    for name, expected in required_current_counts.items():
        actual = function_body(bio, name).count("bio_background_current()")
        if actual != expected:
            raise ContractError(
                f"{name} has {actual} executor checks, expected {expected}"
            )

    bget = compact(function_body(bio, "bget"))
    if ("b = bio_cache_hash_find(dev, blockno); if (b != 0) { "
            "if (b->background_reserved) panic(\"reserved buffer in hash\")"
            not in bget):
        raise ContractError(
            "reserved buffers remain reachable through exact-key hits"
        )
    reserve = compact(function_body(bio, "bio_background_reserve_buffers"))
    unlink = reserve.find("bio_cache_hash_remove(candidate);")
    hide = reserve.find("candidate->background_reserved = 1;")
    if unlink < 0 or hide < 0 or unlink > hide:
        raise ContractError(
            "background reservation remains linked in the exact-key index"
        )
    reserved = (
        "if (b->background_reserved) { "
        "if (bio_background_current() && b->cache_owner == owner && "
        "reserved_candidate == 0) reserved_candidate = b; continue; }"
    )
    if reserved not in bget:
        raise ContractError(
            "foreground callers can consume a background-reserved buffer"
        )
    if bget.count("*result = VIRTIO_DISK_ERR_BUSY;") != 3:
        raise ContractError("background cache contention is not a retry result")
    if bget.count("intr_restore(enabled); return 0;") != 3:
        raise ContractError("background cache retry returns with IRQs disabled")
    for panic_text in (
        "background hit busy buffer",
        "background buffer-cache reservation invariant",
        "background buffer-cache controller admission",
    ):
        if panic_text in bget:
            raise ContractError(f"ordinary background contention can panic: {panic_text}")
    for name in ("bread", "bread_device"):
        read = compact(function_body(bio, name))
        if "b = bget(dev, blockno, &result); if (b == 0) return result;" not in read:
            raise ContractError(f"{name} does not propagate cache retry")

    for token in (
        "enum bio_checkpoint_state",
        "struct bio_checkpoint_result {",
        "BIO_CHECKPOINT_READY",
        "BIO_CHECKPOINT_DEFERRED",
        "BIO_CHECKPOINT_INTERRUPTED",
        "bio_checkpoint_should_stop(struct bio_checkpoint_result result)",
    ):
        if token not in compact(bio_h):
            raise ContractError(f"typed checkpoint contract missing {token}")
    if bio_h.count("BIO_MUST_CHECK;") < 4:
        raise ContractError("checkpoint results are not must-check")

    ialloc = compact(function_body(fs, "ialloc"))
    for token in (
        "start_cursor = fs_storage.inode_alloc_cursor;",
        "bio_checkpoint_should_stop(checkpoint)",
        "checkpoint.state == BIO_CHECKPOINT_DEFERRED ? VIRTIO_DISK_ERR_BUSY : -1",
    ):
        if token not in ialloc:
            raise ContractError(f"ialloc checkpoint missing {token}")
    if ialloc.count("fs_storage.inode_alloc_cursor = inum + 1;") != 2:
        raise ContractError("ialloc does not publish success and scan cursors")
    if "checkpoint < 0" in ialloc:
        raise ContractError("ialloc conflates deferred work with interruption")

    balloc = compact(function_body(fs, "balloc"))
    for token in (
        "fs_storage.block_alloc_cursor = b + limit;",
        "bio_checkpoint_should_stop(checkpoint)",
        "checkpoint.state == BIO_CHECKPOINT_DEFERRED ? VIRTIO_DISK_ERR_BUSY : -1",
    ):
        if token not in balloc:
            raise ContractError(f"balloc checkpoint missing {token}")

    preallocate = compact(function_body(fs, "fs_preallocate_inode"))
    for token in (
        "start_block = MIN(blocks, (ip->size + BSIZE - 1) / BSIZE);",
        "bio_checkpoint_should_stop(checkpoint)",
        "checkpoint.state == BIO_CHECKPOINT_DEFERRED ? VIRTIO_DISK_ERR_BUSY : -1",
    ):
        if token not in preallocate:
            raise ContractError(f"preallocation continuation missing {token}")

    forward = compact(function_body(fs, "fs_forward_checkpoint"))
    if "bio_request_settle_quiescent_cleanup() == 0" not in forward:
        raise ContractError("forward checkpoint still exposes resumable deferral")
    if "BIO_CHECKPOINT_DEFERRED" in forward or "VIRTIO_DISK_ERR_BUSY" in forward:
        raise ContractError("forward-only allocator publication can return BUSY")

    bzero = compact(function_body(fs, "bzero"))
    for token in (
        "result = fs_read_block(dev, bno, &bp);",
        "result = bclaim(bp);",
        "return result;",
    ):
        if token not in bzero:
            raise ContractError(f"bzero loses retry status: {token}")


def validate_iobudget_probe(source: str) -> None:
    workflow = compact(function_body(source, "run_workflow"))
    ordered = (
        'create_file("wfhot", block_data, sizeof(block_data))',
        'check(read(fd, &value, 1) == 1, "preheat workflow hot block")',
        'io_policy_info(&initial) == 0, "read workflow I/O state"',
        "report.ready = 1",
        'read_exact(command_fd, &command, sizeof(command), "read workflow probe")',
        'io_policy_info(&pressured) == 0',
        'check(read(fd, &value, 1) == 1 && value == \'W\'',
        'io_policy_info(&after_read) == 0',
    )
    positions = [workflow.index(token) for token in ordered]
    if positions != sorted(positions):
        raise ContractError("I/O cache-floor probe ordering drifted")
    for token in (
        "initial.cache_resident != 0",
        "initial.cache_floor == IO_CACHE_WORKFLOW_FLOOR",
        "initial.cache_cap == IO_CACHE_WORKFLOW_CAP",
        "initial.cache_resident < initial.cache_floor ? "
        "initial.cache_resident : initial.cache_floor",
        "pressured.owner == initial.owner",
        "pressured.cache_floor == initial.cache_floor",
        "pressured.cache_cap == initial.cache_cap",
        "pressured.cache_resident >= protected_resident",
        "pressured.cache_resident <= pressured.cache_cap",
    ):
        if token not in workflow:
            raise ContractError(f"I/O cache-floor probe missing {token}")
    for aggregate_counter in (
        "pressured.physical_reads",
        "pressured.cache_hits",
        "after_read.physical_reads",
        "after_read.cache_hits",
    ):
        if aggregate_counter in workflow:
            raise ContractError(
                "owner-wide I/O counter used as a per-read receipt: "
                f"{aggregate_counter}"
            )


validate(BIO, BIO_H, FS)
validate_iobudget_probe(IOBUDGET)

MUTATIONS = (
    (BIO.replace("struct thread *executor;", "", 1), BIO_H, FS,
     "executor field removed"),
    (BIO.replace("uint64 executor_generation;", "", 1), BIO_H, FS,
     "executor generation removed"),
    (BIO.replace("io_policy.background.executor != thread", "0", 1), BIO_H, FS,
     "executor identity bypassed"),
    (BIO.replace(
        "io_policy.background.executor_generation !=\n\t\t    thread->identity_generation",
        "0", 1), BIO_H, FS, "executor generation bypassed"),
    (BIO.replace(
        "io_policy.background.executor_generation =\n\t\texecutor->identity_generation;",
        "", 1), BIO_H, FS, "executor generation publication removed"),
    (replace_in_function(
        BIO, "bio_current_owner", "bio_background_current()",
        "io_policy.background.active"), BIO_H, FS, "owner lookup made ambient"),
    (BIO.replace("if (b->background_reserved) {",
                 "if (0 && b->background_reserved) {", 1), BIO_H, FS,
     "reserved-buffer isolation disabled"),
    (replace_in_function(
        BIO, "bio_background_reserve_buffers",
        "bio_cache_hash_remove(candidate);", ""), BIO_H, FS,
     "reserved-buffer exact-hit isolation disabled"),
    (BIO.replace("*result = VIRTIO_DISK_ERR_BUSY;", "", 1), BIO_H, FS,
     "background cache retry removed"),
    (BIO, BIO_H.replace("struct bio_checkpoint_result {", "struct checkpoint {", 1),
     FS, "checkpoint result type removed"),
    (BIO, BIO_H, replace_in_function(
        FS, "ialloc", "bio_checkpoint_should_stop(checkpoint)", "0"),
     "inode allocator deferral ignored"),
    (BIO, BIO_H, replace_in_function(
        FS, "ialloc", "fs_storage.inode_alloc_cursor = inum + 1;", ""),
     "inode continuation cursor removed"),
    (BIO, BIO_H, replace_in_function(
        FS, "fs_forward_checkpoint",
        "bio_request_settle_quiescent_cleanup() == 0", "0"),
     "forward settlement bypassed"),
    (replace_in_function(
        BIO, "io_wait_for_debt", "!closing_background", "1"), BIO_H, FS,
     "closing background debt settlement removed"),
    (replace_in_function(
        BIO, "bio_request_checkpoint_mode", "if (cleanup && quiescent)",
        "if (0)"), BIO_H, FS, "forward cleanup wait removed"),
    (replace_in_function(
        BIO, "bio_cache_release_closed_owner",
        "if (bio_cache_owner_retained(owner))", "if (0)"),
     BIO_H, FS, "active cleanup cache partition invalidated"),
    (replace_in_function(
        BIO, "bio_background_end", "bio_cache_release_closed_owner(owner);", ""),
     BIO_H, FS, "closed cleanup cache partition leaked"),
    (replace_in_function(
        BIO, "bio_request_checkpoint_mode",
        "bio_background_wait_for_cache_progress()", "0"),
     BIO_H, FS, "cache progress wait removed"),
    (replace_in_function(
        BIO, "brelse", "bio_cache_note_progress();", ""),
     BIO_H, FS, "holder release wake removed"),
)
for mutated_bio, mutated_header, mutated_fs, label in MUTATIONS:
    try:
        validate(mutated_bio, mutated_header, mutated_fs)
    except ContractError:
        continue
    raise SystemExit(f"mutation survived: {label}")

IOBUDGET_MUTATIONS = (
    (
        'check(io_policy_info(&pressured) == 0,\n'
        '\t      "snapshot pressured workflow cache state");\n'
        "\tcheck(read(fd, &value, 1) == 1 && value == 'W',\n"
        '\t      "read protected workflow hot block");',
        "check(read(fd, &value, 1) == 1 && value == 'W',\n"
        '\t      "read protected workflow hot block");\n'
        "\tcheck(io_policy_info(&pressured) == 0,\n"
        '\t      "snapshot pressured workflow cache state");',
        "cache snapshot moved after the probe read",
    ),
    ("pressured.owner == initial.owner", "1",
     "cache owner binding removed"),
    ("pressured.cache_floor == initial.cache_floor", "1",
     "cache floor identity removed"),
    ("pressured.cache_cap == initial.cache_cap", "1",
     "cache cap identity removed"),
    ("pressured.cache_resident >= protected_resident", "1",
     "cache floor comparison removed"),
    ("initial.cache_resident : initial.cache_floor", "initial.cache_resident",
     "cache floor clamp removed"),
)
for old, new, label in IOBUDGET_MUTATIONS:
    try:
        validate_iobudget_probe(
            replace_in_function(IOBUDGET, "run_workflow", old, new)
        )
    except (ContractError, ValueError):
        continue
    raise SystemExit(f"mutation survived: {label}")

print(
    f"[bio-background-context] wiring and "
    f"{len(MUTATIONS) + len(IOBUDGET_MUTATIONS)} mutations passed"
)
