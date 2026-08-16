#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


class ContractError(RuntimeError):
    pass


def compact(text: str) -> str:
    return " ".join(text.split())


def function_body(source: str, name: str) -> str:
    pattern = re.compile(rf"(?m)^.*\b{re.escape(name)}\s*\(")
    for match in pattern.finditer(source):
        opening = source.find("{", match.end())
        semicolon = source.find(";", match.end(), opening if opening >= 0 else None)
        if opening < 0 or semicolon >= 0:
            continue
        depth = 0
        for index in range(opening, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    return source[opening : index + 1]
    raise ContractError(f"missing function: {name}")


def require(text: str, token: str, context: str) -> None:
    if compact(token) not in compact(text):
        raise ContractError(f"{context} missing: {compact(token)}")


def require_order(text: str, tokens: tuple[str, ...], context: str) -> None:
    normalized = compact(text)
    cursor = 0
    for token in tokens:
        position = normalized.find(compact(token), cursor)
        if position < 0:
            raise ContractError(f"{context} missing/out of order: {compact(token)}")
        cursor = position + len(compact(token))


def validate(sources: dict[str, str]) -> None:
    handler = function_body(sources["handler"], "sys_agent_file_publish")
    require_order(
        handler,
        (
            "copyinstr(p->pagetable, path, request.path, sizeof(path))",
            "snapshot = kalloc_account_page(account, charge_class);",
            "copyin(p->pagetable, snapshot, request.header,",
            "copyin(p->pagetable, snapshot + request.header_size,",
            "fs_agent_file_publish_atomic(path, &cred, snapshot, total);",
            "kfree_account_page(snapshot, account, charge_class);",
        ),
        "immutable charged snapshot",
    )
    helper_at = compact(handler).find(
        "fs_agent_file_publish_atomic(path, &cred, snapshot, total);"
    )
    if "copyin(" in compact(handler)[helper_at:]:
        raise ContractError("publish reads user memory after filesystem mutation starts")
    if "user_range_check" in handler:
        raise ContractError("publish relies on a racy user-range precheck")
    require(handler, "char path[DIRSIZ + 1];", "strict publish name snapshot")
    require(handler, "request.size != sizeof(request)", "request size contract")
    require(handler, "return AGENT_STATUS_BAD_SIZE;", "request size status")

    checkpoint = function_body(sources["fs"], "fs_publish_checkpoint")
    require_order(
        checkpoint,
        (
            "#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE",
            "return fs_durable_barrier_forward();",
            "#else",
            "return fs_forward_checkpoint();",
            "#endif",
        ),
        "profile-aware durable checkpoint",
    )

    publish = function_body(sources["fs"], "fs_agent_file_publish_atomic")
    require(
        publish,
        "vfs_create_request_authorize(cred, VFS_POLICY_WORKFLOW, 0, 1, 0)",
        "write-only workflow authorization",
    )
    require_order(
        publish,
        (
            "ip = ialloc(dp->dev, T_FILE, &charge, &lookup_status);",
            "result = writei(ip, cred, 0, (uint64)snapshot, 0, total);",
            "result = fs_publish_checkpoint();",
            "result = dirlink_publish(dp, key, ip->inum, cred);",
        ),
        "unnamed inode then single namespace attach",
    )
    require(
        publish,
        "if (result == FS_DIRLINK_EXISTS) status = AGENT_STATUS_DUPLICATE;",
        "same-name race mapping",
    )
    uncertain = re.search(
        r"if \(result == FS_LOOKUP_INDETERMINATE \|\|"
        r".*?return AGENT_STATUS_INDETERMINATE;\s*}",
        publish,
        re.S,
    )
    if uncertain is None:
        raise ContractError("missing attach-indeterminate convergence branch")
    for forbidden in ("fs_agent_publish_discard", "removed", "fs_put_removed_checked"):
        if forbidden in uncertain.group(0):
            raise ContractError(
                f"attach-indeterminate branch reclaims possible official inode: {forbidden}"
            )
    require_order(
        uncertain.group(0),
        ("iput(ip);", "iput(dp);", "return AGENT_STATUS_INDETERMINATE;"),
        "attach-indeterminate orphan preservation",
    )

    link = function_body(sources["fs"], "dirlink_impl")
    require_order(
        link,
        (
            "fs_namespace_gate_lock()",
            "writei_charged(dp, &kernel_cred",
            "fs_dentry_index_publish_link(dp, key",
            "publish_commit ? fs_publish_checkpoint()",
            "fs_namespace_gate_unlock();",
        ),
        "namespace-gated attach commit",
    )
    require(link, "result = FS_DIRLINK_EXISTS;", "dirlink duplicate result")
    require(link, "result = FS_DIRLINK_NO_SPACE;", "dirlink capacity result")
    require(
        function_body(sources["fs"], "dirlink"),
        "return dirlink_impl(dp, name, inum, cred, 0);",
        "ordinary dirlink compatibility",
    )
    require(
        function_body(sources["fs"], "dirlink_publish"),
        "return dirlink_impl(dp, name, inum, cred, 1);",
        "publish attach commit",
    )

    discard = function_body(sources["fs"], "fs_agent_publish_discard")
    require_order(
        discard,
        (
            "fs_io_health != FS_IO_HEALTHY",
            "ip->removed = 1;",
            "fs_put_removed_checked(ip);",
            "fs_publish_checkpoint();",
        ),
        "known-unattached reclaim checkpoint",
    )

def mutation_self_test(sources: dict[str, str]) -> None:
    mutations = (
        (
            "handler",
            "snapshot = kalloc_account_page(account, charge_class);",
            "charged snapshot",
        ),
        (
            "fs",
            "static int fs_publish_checkpoint(void)\n"
            "{\n"
            "#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE\n"
            "\treturn fs_durable_barrier_forward();",
            "fault-profile durable flush",
        ),
        (
            "fs",
            "result = dirlink_publish(dp, key, ip->inum, cred);",
            "formal namespace attach",
        ),
        (
            "fs",
            "checkpoint_result = fs_publish_checkpoint();",
            "cleanup checkpoint",
        ),
    )
    for key, token, label in mutations:
        if token not in sources[key]:
            raise ContractError(f"mutation anchor missing: {label}")
        mutated = dict(sources)
        mutated[key] = mutated[key].replace(token, "", 1)
        try:
            validate(mutated)
        except ContractError:
            continue
        raise ContractError(f"mutation escaped atomic publish contract: {label}")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sources = {
        "handler": (root / "os" / "agent_file_state.c").read_text(encoding="utf-8"),
        "fs": (root / "os" / "fs.c").read_text(encoding="utf-8"),
    }
    validate(sources)
    mutation_self_test(sources)
    print("agent-file-publish-atomicity: passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as error:
        print(f"agent-file-publish-atomicity: {error}", file=sys.stderr)
        raise SystemExit(1)
