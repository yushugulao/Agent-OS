#!/usr/bin/env python3
"""Mutation contracts for filesystem epoch I/O sponsorship."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FS_EPOCH = (ROOT / "os/fs_epoch.c").read_text(encoding="utf-8")
FS_EPOCH_H = (ROOT / "os/fs_epoch.h").read_text(encoding="utf-8")
BIO = (ROOT / "os/bio.c").read_text(encoding="utf-8")
FS = (ROOT / "os/fs.c").read_text(encoding="utf-8")
SYSCALL = (ROOT / "os/syscall.c").read_text(encoding="utf-8")


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
        depth = 0
        closing = -1
        for index in range(opening, len(source)):
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing < 0:
            raise ContractError(f"unterminated declaration {name}")
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
                        return source[brace + 1 : index]
            raise ContractError(f"unterminated function {name}")
        search = closing + 1


def compact(value: str) -> str:
    return " ".join(value.split())


def ordered(body: str, *tokens: str) -> None:
    cursor = 0
    for token in tokens:
        position = body.find(token, cursor)
        if position < 0:
            raise ContractError(f"ordering contract missing {tokens}")
        cursor = position + len(token)


def replace_in_function(source: str, name: str, old: str, new: str) -> str:
    body = function_body(source, name)
    if old not in body:
        raise ContractError(f"mutation anchor drift: {name}: {old}")
    return source.replace(body, body.replace(old, new, 1), 1)


def validate(fs_epoch: str, fs_epoch_h: str, bio: str, syscall: str) -> None:
    if not all(token in fs_epoch for token in (
        "uint sponsor_class;", "uint64 sponsor_request_id;", "uint forward_only;"
    )):
        raise ContractError("epoch does not retain class and irreversible state")
    if fs_epoch.count("epoch.forward_only = 1;") != 1:
        raise ContractError("irreversible state is not tied to one device publication")
    if fs_epoch.count("epoch.forward_only = 0;") != 2:
        raise ContractError("irreversible state is reset outside start or success")

    start = compact(function_body(fs_epoch, "fs_epoch_start_locked"))
    ordered(
        start,
        "bio_deferred_owner_retain_current( owner, &sponsor_class, &sponsor_request_id)",
        "sponsor_request_id = 0",
        "bio_deferred_owner_retain_cleanup( owner, &sponsor_class)",
        "epoch.sponsor_class = sponsor_class",
        "epoch.sponsor_request_id = sponsor_request_id",
        "epoch.forward_only = 0",
    )
    if "bio_deferred_owner_retain(owner" in start:
        raise ContractError("epoch uses the ambiguous compatibility retain entry")
    for special_case in (
        "FS_OWNER_SYSTEM",
        "FS_OWNER_PUBLIC",
        "FS_OWNER_IS_SCOPE",
        "IO_POLICY_CLASS_",
    ):
        if special_case in start:
            raise ContractError("epoch sponsorship is selected by role or path")

    acquire = compact(function_body(fs_epoch, "fs_epoch_acquire_entry"))
    ordered(
        acquire,
        "result == VIRTIO_DISK_ERR_BUSY",
        "if (!epoch.forward_only)",
        "fs_epoch_forward_wait()",
        "continue",
    )

    write = compact(function_body(fs_epoch, "fs_epoch_write_phase"))
    ordered(
        write,
        "virtio_disk_write_batch(batch, batch_count)",
        "result == VIRTIO_DISK_ERR_BUSY",
        "if (!epoch.forward_only)",
        "fs_epoch_forward_wait()",
        "epoch.forward_only = 1",
        "if (result < 0)",
    )

    flush = compact(function_body(fs_epoch, "fs_epoch_flush_forward"))
    ordered(
        flush,
        "result != VIRTIO_DISK_ERR_BUSY",
        "if (!epoch.forward_only)",
        "fs_epoch_forward_wait()",
    )

    forward = compact(function_body(fs_epoch, "fs_epoch_forward_wait"))
    if forward.count("bio_request_settle_quiescent_cleanup()") != 1:
        raise ContractError("forward-only BUSY wait does not settle exactly once")
    if fs_epoch.count("bio_request_settle_quiescent_cleanup()") != 1:
        raise ContractError("epoch settles debt outside irreversible BUSY recovery")

    commit = compact(function_body(fs_epoch, "fs_epoch_commit"))
    ordered(
        commit,
        "commit_owner = epoch.owner",
        "sponsor_class = epoch.sponsor_class",
        "sponsor_request_id = epoch.sponsor_request_id",
        "bio_deferred_sponsor_begin( commit_owner, sponsor_class, sponsor_request_id)",
        "fs_epoch_write_phase",
        "fs_epoch_flush_forward",
        "bio_deferred_sponsor_end()",
        "bunpin(epoch.entries[i].buf)",
        "epoch.sponsor_request_id = 0",
        "epoch.forward_only = 0",
        "bio_deferred_owner_release(commit_owner)",
    )
    if "bio_request_settle_quiescent_cleanup" in commit:
        raise ContractError("successful commit settles the outer request inside the gate")

    fail = compact(function_body(fs_epoch, "fs_epoch_commit_fail"))
    sponsored_fail = compact(
        function_body(fs_epoch, "fs_epoch_commit_sponsored_fail")
    )
    if "bio_deferred_owner_release" in fail + sponsored_fail:
        raise ContractError("retryable commit failure drops the retained owner")
    ordered(sponsored_fail, "bio_deferred_sponsor_end()", "fs_epoch_commit_fail(result)")

    sponsor = compact(function_body(bio, "bio_deferred_sponsor_begin"))
    ordered(
        sponsor,
        "origin_request_id != 0",
        "thread->io_request_id == origin_request_id",
        "thread->io_request_owner == owner",
        "thread->io_request_class == io_class",
        "effective_class = io_class",
        "reuse_request_lease = 1",
        "effective_class = IO_POLICY_CLASS_BACKGROUND",
    )
    transfer = compact(function_body(bio, "bio_account_transfer"))
    ordered(
        transfer,
        "bio_deferred_sponsor_current()",
        "io_policy.deferred.reuse_request_lease",
        "thread->io_request_id == io_policy.deferred.origin_request_id",
        "thread->io_request_owner == io_policy.deferred.owner",
        "thread->io_request_class == io_policy.deferred.io_class",
        "transfers = &thread->io_request_transfers",
    )

    finish = compact(function_body(syscall, "syscall_transaction_finish"))
    ordered(
        finish,
        "fs_epoch_request_end()",
        "syscall_transaction_end_io(transaction)",
    )
    end_io = compact(function_body(syscall, "syscall_transaction_end_io"))
    if "bio_request_end_current(1)" not in end_io:
        raise ContractError("syscall I/O lease no longer settles at its finalizer")
    if "Request debt remains with the outer I/O lease" not in fs_epoch_h:
        raise ContractError("public epoch contract does not assign debt settlement")
    reclaim = compact(function_body(FS, "fs_deferred_reclaim_maintain_owner"))
    if "bio_deferred_sponsor_begin(sponsor_owner, sponsor_class, 0)" not in reclaim:
        raise ContractError("asynchronous reclaim can borrow a coincidental request")


validate(FS_EPOCH, FS_EPOCH_H, BIO, SYSCALL)

MUTATIONS = (
    (
        replace_in_function(
            FS_EPOCH,
            "fs_epoch_start_locked",
            "&sponsor_request_id) < 0",
            "0) < 0",
        ),
        BIO,
        SYSCALL,
        "exact class retain removed",
    ),
    (
        replace_in_function(
            FS_EPOCH,
            "fs_epoch_start_locked",
            "epoch.sponsor_request_id = sponsor_request_id;",
            "epoch.sponsor_request_id = 0;",
        ),
        BIO,
        SYSCALL,
        "origin request identity discarded",
    ),
    (
        replace_in_function(
            FS_EPOCH,
            "fs_epoch_start_locked",
            "epoch.sponsor_class = sponsor_class;",
            "epoch.sponsor_class = IO_POLICY_CLASS_BACKGROUND;",
        ),
        BIO,
        SYSCALL,
        "captured class demoted",
    ),
    (
        replace_in_function(
            FS_EPOCH,
            "fs_epoch_acquire_entry",
            "if (!epoch.forward_only)\n\t\t\t\treturn result;",
            "",
        ),
        BIO,
        SYSCALL,
        "reversible acquire waits forward",
    ),
    (
        replace_in_function(
            FS_EPOCH,
            "fs_epoch_write_phase",
            "if (!epoch.forward_only)\n\t\t\t\treturn result;",
            "",
        ),
        BIO,
        SYSCALL,
        "reversible write waits forward",
    ),
    (
        replace_in_function(
            FS_EPOCH,
            "fs_epoch_flush_forward",
            "if (!epoch.forward_only)\n\t\t\treturn result;",
            "",
        ),
        BIO,
        SYSCALL,
        "reversible flush waits forward",
    ),
    (
        replace_in_function(
            FS_EPOCH,
            "fs_epoch_write_phase",
            "epoch.forward_only = 1;",
            "",
        ),
        BIO,
        SYSCALL,
        "irreversible publication not recorded",
    ),
    (
        replace_in_function(
            FS_EPOCH,
            "fs_epoch_note_data",
            "epoch.data_notices++;",
            "epoch.data_notices++;\n\t\tepoch.forward_only = 1;",
        ),
        BIO,
        SYSCALL,
        "staged data treated as published",
    ),
    (
        replace_in_function(
            FS_EPOCH,
            "fs_epoch_commit",
            "bio_deferred_sponsor_end();",
            "bio_request_settle_quiescent_cleanup();\n\tbio_deferred_sponsor_end();",
        ),
        BIO,
        SYSCALL,
        "successful commit settles debt in gate",
    ),
    (
        replace_in_function(
            FS_EPOCH, "fs_epoch_commit", "bio_deferred_sponsor_end();", ""
        ),
        BIO,
        SYSCALL,
        "sponsor context leaked",
    ),
    (
        replace_in_function(
            FS_EPOCH,
            "fs_epoch_commit",
            "bio_deferred_owner_release(commit_owner);",
            "",
        ),
        BIO,
        SYSCALL,
        "retained owner leaked",
    ),
    (
        replace_in_function(
            FS_EPOCH,
            "fs_epoch_commit",
            "epoch.forward_only = 0;",
            "",
        ),
        BIO,
        SYSCALL,
        "successful epoch keeps irreversible state",
    ),
    (
        replace_in_function(
            FS_EPOCH,
            "fs_epoch_commit_sponsored_fail",
            "bio_deferred_sponsor_end();",
            "bio_deferred_owner_release(epoch.owner);",
        ),
        BIO,
        SYSCALL,
        "failure drops retry owner",
    ),
    (
        FS_EPOCH.replace(
            "bio_request_settle_quiescent_cleanup()",
            "bio_request_checkpoint_quiescent().state",
            1,
        ),
        BIO,
        SYSCALL,
        "forward wait no longer cleanup-safe",
    ),
    (
        FS_EPOCH,
        replace_in_function(
            BIO,
            "bio_deferred_sponsor_begin",
            "thread->io_request_id == origin_request_id",
            "1",
        ),
        SYSCALL,
        "stale same-class request reused",
    ),
    (
        FS_EPOCH,
        replace_in_function(
            BIO,
            "bio_deferred_sponsor_begin",
            "thread->io_request_class == io_class",
            "1",
        ),
        SYSCALL,
        "sponsor class match removed",
    ),
    (
        FS_EPOCH,
        replace_in_function(
            BIO,
            "bio_deferred_sponsor_begin",
            "effective_class = IO_POLICY_CLASS_BACKGROUND;",
            "effective_class = io_class;",
        ),
        SYSCALL,
        "deferred executor keeps foreground class",
    ),
    (
        FS_EPOCH,
        BIO,
        replace_in_function(
            SYSCALL,
            "syscall_transaction_finish",
            "fs_epoch_request_end();",
            "syscall_transaction_end_io(transaction);",
        ),
        "outer debt settles before epoch gate release",
    ),
)

for mutated_epoch, mutated_bio, mutated_syscall, label in MUTATIONS:
    try:
        validate(mutated_epoch, FS_EPOCH_H, mutated_bio, mutated_syscall)
    except ContractError:
        continue
    raise SystemExit(f"fs epoch sponsor mutation survived: {label}")

print(f"[fs-epoch-sponsor] {len(MUTATIONS)} mutations passed")
