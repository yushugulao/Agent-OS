#!/usr/bin/env python3
"""检查打开文件的可信授权上下文及其缓存。"""

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
    header = compact((root / "os/open_file_io_lease.h").read_text(encoding="utf-8"))
    source_text = (root / "os/open_file_io_lease.c").read_text(encoding="utf-8")
    source = compact(source_text)
    file_text = (root / "os/file.c").read_text(encoding="utf-8")
    file_source = compact(file_text)
    fs_source = compact((root / "os/fs.c").read_text(encoding="utf-8"))
    edit_text = (root / "os/agent_file_state.c").read_text(encoding="utf-8")
    edit_source = compact(edit_text)

    for field in (
        "structfile*file;", "structinode*inode;", "structproc*subject;",
        "structresource_account_handleaccount;",
        "structworkflow_lifecycle_keylifecycle;",
        "conststructvfs_cred*cred;",
        "uint64edit_authority_generation;", "uint64edit_deadline_tick;",
        "uint64thread_generation;", "uint64syscall_generation;",
        "uintinode_incarnation;", "uintinode_checksum;",
        "uintinode_policy_generation;", "uintinode_exec_size;",
        "uintinode_exec_flags;", "uintinode_exec_generation;",
        "uintinode_exec_role_mask;", "uintinode_exec_layout_version;",
        "uintinode_exec_rw_offset;", "uintinode_exec_profile;",
        "enumvfs_operationoperation;", "ucharvalid;",
    ):
        require(field in header, "typed syscall authority is incomplete")
    require("opaque[" not in header and "token_secret" not in source and
            "token_seal" not in source and "security_stamp" not in source,
            "kernel-private authority still pays a cryptographic sealing tax")
    require("file_generations" not in source and
            "open_file_io_lease_file_init" not in source and
            "open_file_io_lease_file_retire" not in source,
            "pinned file references still carry a duplicate lifetime protocol")
    require("OPEN_FILE_IO_CACHE_CAP64U" in source,
            "bounded authorization cache changed")
    require("structopen_file_io_grant{structfile*file;structinode*inode;"
            "structproc*subject;" in source,
            "authorization cache is not bound to the trusted file object")

    acquire = function(source_text, "open_file_io_lease_acquire")
    validate = function(source_text, "open_file_io_token_validate")
    seed = function(source_text, "open_file_io_lease_seed_authorized")
    grant_match = function(source_text, "open_file_io_grant_matches")
    subject_match = function(source_text, "open_file_io_subject_matches")
    inode_match = function(source_text, "open_file_io_inode_matches")
    file_match = function(source_text, "open_file_io_file_matches")
    edit_match = function(source_text, "open_file_io_edit_matches")
    edit_modify = function(edit_text, "agent_edit_modify_allowed")
    edit_snapshot = function(edit_text, "agent_edit_write_lease_snapshot")
    issue = function(source_text, "open_file_io_token_issue")
    cache_slot = function(source_text, "open_file_io_cache_slot")

    require(
        edit_snapshot.endswith(
            "{returnagent_edit_modify_allowed("
            "ip,0,authority_generation,valid_until_tick);}")
        and edit_snapshot.count("agent_edit_modify_allowed(") == 1,
        "write lease snapshot is not a side-effect-free delegation",
    )
    for fragment in (
        "if(authority_generation)*authority_generation=0;",
        "if(valid_until_tick)*valid_until_tick=0;",
        "enabled=agent_edit_lock();",
        "agent_edit_cleanup_expired_locked(now);",
        "version=file_version_inode_locked(ip,0);",
        "edit=agent_edit_find_locked(ip->vfs_scope_id,0,ip);",
        "edit&&!agent_edit_owner(edit,p)",
        "*valid_until_tick=edit->deadline_tick;",
        "agent_edit_unlock(enabled);",
    ):
        require(
            fragment in edit_modify,
            "write lease snapshot common authorization path is incomplete",
        )
    require(
        "if(action){edit->conflict_count++;" in edit_modify
        and "if(!allowed&&action)agent_edit_audit(" in edit_modify,
        "write lease snapshot common path no longer suppresses audit side effects",
    )

    require(acquire.count("vfs_inode_authorize(") == 1,
            "acquire must have exactly one full VFS authorization")
    require("agent_edit_write_lease_allowed(" in acquire,
            "write acquisition does not capture edit authority")
    require(acquire.index("open_file_io_grant_matches(") <
            acquire.index("vfs_inode_authorize("),
            "full authorization precedes the cache fast path")
    require(acquire.count("open_file_io_grant_matches(") >= 2,
            "slow-path authorization is not revalidated before publication")
    require("slot=open_file_io_cache_slot(file,proc,operation)" in acquire and
            "open_file_io_operation_mask(operation)*0x9e3779b97f4a7c15ULL" in
            cache_slot,
            "cache key does not separate trusted file, subject and operation")

    for fragment in (
        "token->file=grant->file", "token->inode=grant->inode",
        "token->subject=grant->subject", "token->account=grant->account",
        "token->lifecycle=grant->lifecycle", "token->cred=authorized_cred",
        "token->thread_generation=thread->identity_generation",
        "token->syscall_generation=thread->kernel_work_generation",
        "token->inode_incarnation=grant->inode_incarnation",
        "token->inode_checksum=grant->inode_checksum",
        "token->inode_policy_generation=grant->inode_policy_generation",
        "token->inode_exec_generation=grant->inode_exec_generation",
        "token->inode_exec_profile=grant->inode_exec_profile",
        "token->operation=operation", "token->valid=1",
    ):
        require(fragment in issue, "typed syscall authority is not fully issued")

    require("vfs_inode_authorize(" not in validate,
            "filesystem hand-off repeats full VFS authorization")
    require("open_file_io_state.grants" not in validate,
            "in-flight syscall authority still depends on cache residency")
    for fragment in (
        "token->subject==proc", "token->inode==inode",
        "thread->identity_generation==token->thread_generation",
        "thread->kernel_work_generation==token->syscall_generation",
        "open_file_io_file_matches(token->file,inode,operation)",
        "open_file_io_subject_matches(proc,token->account,token->lifecycle,"
        "token->cred)",
        "open_file_io_inode_matches(token->inode_incarnation,"
        "token->inode_checksum,token->inode_policy_generation,"
        "token->inode_exec_size,token->inode_exec_flags,"
        "token->inode_exec_generation,token->inode_exec_role_mask,"
        "token->inode_exec_layout_version,token->inode_exec_rw_offset,"
        "token->inode_exec_profile,inode)",
        "open_file_io_edit_matches(inode,token->cred,operation,"
        "token->edit_authority_generation,token->edit_deadline_tick)",
    ):
        require(fragment in validate,
                "blocking-boundary revocation validation is incomplete")

    for fragment in (
        "proc->teardown_state!=PROC_TEARDOWN_LIVE",
        "resource_account_handle_equal(account,proc->resource_account)",
        "resource_account_active(account)",
        "workflow_lifecycle_key_equal(lifecycle,current_lifecycle)",
        "open_file_io_lifecycle_live(current_lifecycle)",
        "open_file_io_cred_equal(cred,&current_cred)",
    ):
        require(fragment in subject_match, "subject generation checks are incomplete")
    require("file->ref>=1" in file_match and "file->ip==inode" in file_match and
            "file->readable" in file_match and "file->writable" in file_match,
            "pinned file authority checks are incomplete")
    require("incarnation==inode->vfs_incarnation" in inode_match and
            "checksum==inode->vfs_checksum" in inode_match and
            "policy_generation==inode->vfs_policy_generation" in inode_match and
            "exec_generation==inode->exec_generation" in inode_match and
            "exec_profile==inode->vfs_exec_profile" in inode_match,
            "inode security identity is incomplete")
    require("agent_edit_write_lease_snapshot(" in edit_match and
            "authority_generation==current_generation" in edit_match and
            "deadline_tick==current_deadline" in edit_match and
            "authority_generation==0&&deadline_tick==0" in edit_match,
            "edit revocation generation is not checked precisely")
    require("open_file_io_file_matches(" in grant_match and
            "open_file_io_subject_matches(" in grant_match and
            "open_file_io_inode_matches(" in grant_match and
            "open_file_io_edit_matches(" in grant_match,
            "cache hit bypasses a revocable security axis")

    require("open_file_io_cred_equal(authorized_cred,&current_cred)" in seed and
            "agent_edit_write_lease_allowed(" in seed and
            "open_file_io_grant_capture(" in seed and
            "open_file_io_grant_matches(" in seed,
            "open-time authorization proof is not safely published")
    require("open_file_io_lease_file_init" not in file_source and
            "open_file_io_lease_file_retire" not in file_source,
            "filepool still wires the removed duplicate lifetime hooks")
    fileopen = function(file_text, "fileopen")
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
            "staticuint64agent_file_edit_authority_generation;" in edit_source and
            "entry->edit_authority_generation="
            "agent_file_edit_authority_generation;" in edit_source and
             edit_source.count(
                 "edit_authority_generation=agent_file_counter_next("
                 "&agent_file_edit_authority_generation)") >= 3 and
             "*authority_generation=version->edit_authority_generation" in
             edit_modify and
             "agent_file_edit_authority_generation" not in source,
             "edit authority lacks an inode-scoped revocation generation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[1])
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
