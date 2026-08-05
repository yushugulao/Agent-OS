#!/usr/bin/env python3
"""Check the inode grant cache and syscall-scoped open-file token."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class ContractError(RuntimeError):
    pass


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def function(text: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\([^;]*?\)\s*\{{", text, re.S)
    if not match:
        raise ContractError(f"missing function {name}")
    depth = 1
    pos = match.end()
    while pos < len(text) and depth:
        depth += (text[pos] == "{") - (text[pos] == "}")
        pos += 1
    if depth:
        raise ContractError(f"unterminated function {name}")
    return compact(text[match.start():pos])


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def check(root: Path) -> None:
    header = compact((root / "os/open_file_io_lease.h").read_text())
    source_text = (root / "os/open_file_io_lease.c").read_text()
    source = compact(source_text)
    file_source = compact((root / "os/file.c").read_text())
    fs_source = compact((root / "os/fs.c").read_text())
    edit_source = compact((root / "os/agent_file_state.c").read_text())

    require("structopen_file_io_token{uint64opaque[4];};" in header,
            "I/O token must stay four words")
    require("uintfile_generations[FILEPOOLSIZE];" in source,
            "file-slot ABA generation is missing")
    require("subject_generations" not in source,
            "duplicated process-generation table returned")
    require("OPEN_FILE_IO_CACHE_CAP64U" in source,
            "bounded grant cache changed")
    require("structopen_file_io_grant{structinode*inode;structproc*subject;" in source,
            "authorization grant is still bound to one open-file instance")
    require("generationexhausted" not in source and
            source.count("memset(open_file_io_state.grants,0,") >= 1,
            "generation rollover is not fail-closed")

    init = function(source_text, "open_file_io_lease_file_init")
    retire = function(source_text, "open_file_io_lease_file_retire")
    for body in (init, retire):
        require("open_file_io_next32(" in body,
                "file-slot generation is not advanced")
        require("for(" not in body and "while(" not in body,
                "file lifetime hook is no longer O(1)")

    acquire = function(source_text, "open_file_io_lease_acquire")
    validate = function(source_text, "open_file_io_token_validate")
    seed = function(source_text, "open_file_io_lease_seed_authorized")
    grant_match = function(source_text, "open_file_io_grant_matches_locked")
    inode_match = function(source_text, "open_file_io_inode_matches")
    file_match = function(source_text, "open_file_io_file_matches_locked")
    issue = function(source_text, "open_file_io_token_issue_locked")
    cache_slot_fn = function(source_text, "open_file_io_cache_slot")
    grant_stamp = function(source_text, "open_file_io_grant_stamp_locked")
    require(acquire.count("vfs_inode_authorize(") == 1,
            "acquire must have exactly one full VFS authorization")
    require("agent_edit_write_lease_allowed(" in acquire,
            "workflow edit expiry is not captured")
    require(acquire.index("open_file_io_grant_matches_locked(") <
            acquire.index("vfs_inode_authorize("),
            "full authorization precedes the cache fast path")
    require("cache_slot=open_file_io_cache_slot(file->ip,proc,operation)" in acquire,
            "grant cache is still keyed by short-lived file slots")
    require("open_file_io_operation_mask(operation)*0x9e3779b97f4a7c15ULL" in
            cache_slot_fn,
            "read and write grants collide in the same cache slot")
    require("file_generations[file_slot]" in acquire and
            "edit_authority_generation" in acquire,
            "slow-path publication lacks generation revalidation")
    require("grant->file" not in grant_match,
            "reusable authorization grant retains an open-file pointer")
    for fragment in (
        "grant->inode_dev==inode->dev",
        "grant->inode_inum==inode->inum",
        "grant->inode_incarnation==inode->vfs_incarnation",
        "grant->inode_policy_generation==inode->vfs_policy_generation",
    ):
        require(fragment in inode_match,
                "reusable grant lacks stable inode security identity")
    require("generation==open_file_io_state.file_generations[slot]" in file_match,
            "syscall token does not reject file-slot ABA")
    require("token->opaque[0]=((uint64)file_slot<<32)|(uint)operation" in issue and
            "token->opaque[1]=file_generation" in issue,
            "syscall token does not carry its file-slot generation")
    require("token->opaque[2]=grant->edit_deadline_tick" in issue and
            "grant->security_stamp" in issue,
            "syscall token does not carry its self-authenticating stamp")
    require("open_file_io_token_seal_locked(token,current.security_stamp,thread)" in validate and
            "open_file_io_grant_capture_locked(&current,inode,proc,operation,edit_authority_generation,edit_deadline_tick)" in validate and
            "open_file_io_grant_matches_locked(&current,inode,proc,operation)" in validate and
            "file=&filepool[file_slot]" in validate and
            "open_file_io_file_matches_locked(" in validate,
            "filesystem token validation is incomplete")
    require("kernel_receipt_generation" in source and
            "vfs_inode_authorize(" not in validate,
            "filesystem hand-off repeats full VFS authorization")
    require("open_file_io_state.grants" not in validate and
            "grant->sequence" not in source,
            "syscall token still depends on grant-cache residency")
    for fragment in (
        "grant->account.generation", "grant->lifecycle.generation",
        "grant->cred.capabilities", "grant->edit_authority_generation",
        "grant->inode_incarnation", "grant->inode_policy_generation",
        "grant->inode_checksum", "grant->allowed",
    ):
        require(fragment in grant_stamp,
                "self-authenticating stamp omits revocable security state")
    require("open_file_io_cred_equal(authorized_cred,&current_cred)" in seed and
            "open_file_io_file_matches_locked(" in seed and
            "agent_edit_write_lease_allowed(" in seed and
            "open_file_io_grant_capture_locked(" in seed,
            "open-time authorization proof is not safely published")
    require("cache_slot=open_file_io_cache_slot(file->ip,proc,operation)" in seed,
            "open seed is keyed by short-lived file slots")

    require("open_file_io_lease_file_init(f);" in file_source and
            "open_file_io_lease_file_retire(f);" in file_source,
            "filepool lifetime is not wired")
    fileopen = function((root / "os/file.c").read_text(), "fileopen")
    require(fileopen.count("open_file_io_lease_seed_authorized(") == 2 and
            fileopen.index("vfs_inode_authorize(") <
            fileopen.index("open_file_io_lease_seed_authorized("),
            "open does not publish its completed read/write authorization")
    require("readi_lease(" in file_source and "writei_lease(" in file_source,
            "traditional read/write does not use the lease path")
    require("open_file_io_token_validate(lease,ip,VFS_OP_READ)" in fs_source and
            "open_file_io_token_validate(lease,ip,VFS_OP_WRITE)" in fs_source,
            "filesystem does not verify the kernel token")
    require("uint64edit_authority_generation;" in edit_source and
            edit_source.count(
                "agent_file_counter_next(&version->edit_authority_generation)") >= 2 and
            "agent_file_counter_next(&version_entry->edit_authority_generation)" in
            edit_source and
            "agent_edit_write_lease_snapshot(" in edit_source,
            "edit authority lacks an inode-scoped revocation generation")
    require("open_file_io_lease_edit_changed" not in source and
            "open_file_io_lease_edit_changed" not in edit_source and
            "grant->edit_authority_generation!=0" in grant_match,
            "global edit invalidation still affects unrelated I/O")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"open-file I/O lease check failed: {error}", file=sys.stderr)
        return 1
    print("open-file I/O lease check: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
