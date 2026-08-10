#!/usr/bin/env python3
"""Validate the unified Agent workflow-fence protocol from source.

The fence deliberately reuses ``SYS_agent_run``.  This checker freezes the
cross-module protocol that makes that reuse fail closed and retry safe; it is
not a general C parser.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class ContractError(RuntimeError):
    pass


SOURCE_PATHS = {
    "abi": "agent_workflow_fence_abi.h",
    "kernel_agent_h": "os/agent.h",
    "user_agent_h": "user/include/agent.h",
    "core": "os/agent_core.c",
    "proc": "os/proc.c",
    "agent_lifecycle": "os/agent_lifecycle.c",
    "fence": "os/agent_workflow_fence.c",
    "lifecycle": "os/workflow_lifecycle.c",
    "vfs_security": "os/vfs_security.c",
    "credit": "os/workflow_credit_domain.c",
    "evidence": "os/agent_evidence_ring.c",
    "metadata_objects": "os/agent_metadata_objects.c",
    "metadata_catalog": "os/agent_metadata_catalog.c",
    "live_query_events": "os/agent_live_query_events.c",
    "user_syscall": "user/lib/syscall.c",
}


def sanitize_c(text: str) -> str:
    """Blank comments and literals while retaining offsets and newlines."""

    output = list(text)
    state = "code"
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if char == "/" and following == "/":
                output[index] = output[index + 1] = " "
                state = "line"
                index += 2
                continue
            if char == "/" and following == "*":
                output[index] = output[index + 1] = " "
                state = "block"
                index += 2
                continue
            if char in ('"', "'"):
                quote = char
                output[index] = " "
                state = "literal"
        elif state == "line":
            if char == "\n":
                state = "code"
            else:
                output[index] = " "
        elif state == "block":
            if char == "*" and following == "/":
                output[index] = output[index + 1] = " "
                state = "code"
                index += 2
                continue
            if char != "\n":
                output[index] = " "
        else:
            if char == "\\" and following:
                if char != "\n":
                    output[index] = " "
                if following != "\n":
                    output[index + 1] = " "
                index += 2
                continue
            if char == quote:
                state = "code"
            if char != "\n":
                output[index] = " "
        index += 1
    if state == "block":
        raise ContractError("unterminated C block comment")
    return "".join(output)


def compact(text: str) -> str:
    return re.sub(r"\s+", "", sanitize_c(text))


def matching(text: str, start: int, opening: str, closing: str) -> int:
    if start >= len(text) or text[start] != opening:
        raise ContractError(f"expected {opening!r} at offset {start}")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    raise ContractError(f"unbalanced {opening}{closing} block")


def function_body(source: str, name: str) -> str:
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
    raise ContractError(f"missing function definition: {name}")


def struct_body(source: str, name: str) -> str:
    clean = sanitize_c(source)
    match = re.search(rf"\bstruct\s+{re.escape(name)}\s*\{{", clean)
    if match is None:
        raise ContractError(f"missing struct definition: {name}")
    opening = clean.find("{", match.start())
    closing = matching(clean, opening, "{", "}")
    return clean[opening + 1 : closing]


def require(source: str, token: str, message: str) -> int:
    position = source.find(token)
    if position < 0:
        raise ContractError(message)
    return position


def reject(source: str, token: str, message: str) -> None:
    if token in source:
        raise ContractError(message)


def require_once(source: str, token: str, message: str) -> int:
    count = source.count(token)
    if count != 1:
        raise ContractError(f"{message} (found {count})")
    return source.index(token)


def require_order(source: str, tokens: tuple[str, ...], message: str) -> None:
    positions = [require(source, token, message) for token in tokens]
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise ContractError(message)


def define_shift(source: str, name: str) -> int:
    match = re.search(
        rf"^\s*#define\s+{re.escape(name)}\s+\(1(?:U|ULL)\s*<<\s*(\d+)\)\s*$",
        source,
        re.MULTILINE,
    )
    if match is None:
        raise ContractError(f"workflow fence ABI lacks exact {name} bit")
    return int(match.group(1))


def validate_abi(sources: dict[str, str]) -> None:
    abi = sources["abi"]
    request = re.sub(r"\s+", "", struct_body(abi, "agent_workflow_fence_request"))
    account_key = re.sub(
        r"\s+", "", struct_body(abi, "agent_workflow_credit_account_key")
    )
    receipt = re.sub(r"\s+", "", struct_body(abi, "agent_workflow_fence_receipt"))

    if not re.search(
        r"^\s*#define\s+AGENT_WORKFLOW_FENCE_VERSION\s+1U\s*$",
        abi,
        re.MULTILINE,
    ):
        raise ContractError("workflow fence ABI version is not frozen at v1")
    if define_shift(abi, "AGENT_RUN_F_FENCE") != 0:
        raise ContractError("workflow fence dispatch flag is not bit zero")
    receipt_bits = {
        define_shift(abi, "AGENT_WORKFLOW_FENCE_RECEIPT_F_PARTIAL_COVERAGE"),
        define_shift(abi, "AGENT_WORKFLOW_FENCE_RECEIPT_F_CREDIT_EXACT"),
        define_shift(abi, "AGENT_WORKFLOW_FENCE_RECEIPT_F_EVIDENCE_SEALED"),
        define_shift(abi, "AGENT_WORKFLOW_FENCE_RECEIPT_F_METADATA_VOLATILE"),
    }
    if receipt_bits != {0, 1, 2, 3}:
        raise ContractError("workflow fence receipt flags overlap or drift")
    for name, value in (
        ("AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE", 32),
        ("AGENT_WORKFLOW_FENCE_ROOT_SIZE", 32),
        ("AGENT_WORKFLOW_FENCE_RESOURCE_KINDS", 8),
    ):
        if not re.search(
            rf"^\s*#define\s+{name}\s+{value}U\s*$", abi, re.MULTILINE
        ):
            raise ContractError(f"workflow fence ABI {name} drifted")

    request_fields = (
        "unsignedintversion;",
        "unsignedintstruct_size;",
        "unsignedintflags;",
        "unsignedintreserved;",
        "unsignedcharchallenge[AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE];",
        "unsignedlonglongrequest_id;",
    )
    require_order(request, request_fields, "workflow fence request fields drifted")
    require_order(
        account_key,
        (
            "unsignedintslot;",
            "unsignedintreserved;",
            "unsignedlonglonggeneration;",
        ),
        "workflow credit account key fields drifted",
    )
    receipt_fields = (
        "unsignedintversion;",
        "unsignedintstruct_size;",
        "intstatus;",
        "unsignedintflags;",
        "structagent_workflow_lifecycle_keykey;",
        "unsignedlonglongrequest_id;",
        "unsignedlonglongfence_sequence;",
        "unsignedlonglongmetadata_generation;",
        "unsignedlonglongcredit_epoch;",
        "unsignedlonglongresource_used[AGENT_WORKFLOW_FENCE_RESOURCE_KINDS];",
        "structagent_workflow_credit_account_keycredit_exec_account;",
        "structagent_workflow_credit_account_keycredit_storage_account;",
        "unsignedcharcredit_digest[AGENT_WORKFLOW_FENCE_ROOT_SIZE];",
        "unsignedcharchallenge[AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE];",
        "unsignedcharprevious_root[AGENT_WORKFLOW_FENCE_ROOT_SIZE];",
        "unsignedcharevidence_root[AGENT_WORKFLOW_FENCE_ROOT_SIZE];",
    )
    require_order(
        receipt,
        receipt_fields,
        "workflow fence receipt exact-credit fields drifted",
    )
    for field in receipt_fields:
        require(receipt, field, f"workflow fence receipt omits {field}")
    abi_compact = compact(abi)
    require(
        abi_compact,
        "_Static_assert(sizeof(structagent_workflow_fence_request)==56,",
        "workflow fence request size is not frozen",
    )
    require(
        abi_compact,
        "_Static_assert(sizeof(structagent_workflow_credit_account_key)==16,",
        "workflow credit account key size is not frozen",
    )
    require(
        abi_compact,
        "_Static_assert(__builtin_offsetof(structagent_workflow_fence_receipt,credit_digest)==192,",
        "workflow fence credit digest offset is not frozen",
    )
    require(
        abi_compact,
        "_Static_assert(__builtin_offsetof(structagent_workflow_fence_receipt,challenge)==224,",
        "workflow fence challenge offset is not frozen",
    )
    require(
        abi_compact,
        "_Static_assert(sizeof(structagent_workflow_fence_receipt)==320,",
        "workflow fence receipt size is not frozen",
    )

    require(
        re.sub(r"\s+", "", sources["kernel_agent_h"]),
        "#include\"../agent_workflow_fence_abi.h\"",
        "kernel Agent UAPI does not include the shared workflow fence ABI",
    )
    require(
        re.sub(r"\s+", "", sources["user_agent_h"]),
        "#include\"../../agent_workflow_fence_abi.h\"",
        "user Agent UAPI does not include the shared workflow fence ABI",
    )
    require(
        compact(sources["user_agent_h"]),
        "intagent_workflow_fence(conststructagent_workflow_fence_request*request,structagent_workflow_fence_receipt*receipt);",
        "user Agent UAPI omits the workflow fence wrapper declaration",
    )


def validate_dispatch(core: str) -> None:
    body = compact(function_body(core, "sys_agent_run"))
    count_check = require_once(
        body,
        "if(count<0||count>AGENT_BATCH_MAX)return-1;",
        "agent_run count bounds are not fail closed",
    )
    fence_branch = require_once(
        body,
        "if(count==0&&flags==AGENT_RUN_F_FENCE){",
        "workflow fence is not selected by the exact count/flag pair",
    )
    flags_check = require_once(
        body,
        "if(flags!=0)return-1;",
        "unknown agent_run flags are not rejected",
    )
    zero_noop = require_once(
        body,
        "if(count==0)return0;",
        "legacy zero-count agent_run behavior drifted",
    )
    if not count_check < fence_branch < flags_check < zero_noop:
        raise ContractError("agent_run count/flags matrix is ordered unsafely")
    reject(
        body,
        "flags&AGENT_RUN_F_FENCE",
        "workflow fence accepts flag combinations instead of an exact flag",
    )

    anonymous = require_once(
        body,
        "if(opsaddr==0&&resultsaddr!=0)return-1;",
        "anonymous workflow fence can request a non-retryable receipt",
    )
    range_read = require(
        body,
        "user_range_check(p->pagetable,opsaddr,sizeof(request),PTE_R)",
        "workflow fence request is not prevalidated for read",
    )
    copy_request = require(
        body,
        "copyin(p->pagetable,(char*)&request,opsaddr,sizeof(request))",
        "workflow fence request is not copied at its exact ABI size",
    )
    range_write = require(
        body,
        "user_range_check(p->pagetable,resultsaddr,sizeof(receipt),PTE_W)",
        "workflow fence receipt is not prevalidated for write",
    )
    execute = require_once(
        body,
        "status=agent_workflow_fence_execute(p,request_ptr,receipt_ptr);",
        "workflow fence dispatch does not execute exactly once",
    )
    copy_receipt = require(
        body,
        "copyout(p->pagetable,resultsaddr,(char*)&receipt,sizeof(receipt))<0",
        "workflow fence receipt is not copied at its exact ABI size",
    )
    delivered = require_once(
        body,
        "agent_workflow_fence_receipt_delivered(vfs_proc_lifecycle(p),request.request_id);",
        "workflow fence receipt delivery is not acknowledged exactly once",
    )
    if not fence_branch < anonymous < range_read < copy_request < range_write < execute < copy_receipt < flags_check:
        raise ContractError(
            "workflow fence user ranges, execution, and copyout are ordered unsafely"
        )
    require(
        body,
        "if(receipt_ptr!=0&&copyout",
        "workflow fence copies an unrequested receipt",
    )
    require(
        body,
        "copyout(p->pagetable,resultsaddr,(char*)&receipt,sizeof(receipt))<0)return-1;",
        "workflow fence copyout failure is not reported after execution",
    )
    reject(
        body,
        "status==AGENT_STATUS_OK&&request_ptr!=0&&receipt_ptr!=0",
        "named workflow fence without a receipt is not acknowledged",
    )
    delivery_guard = require_once(
        body,
        "if(status==AGENT_STATUS_OK&&request_ptr!=0)agent_workflow_fence_receipt_delivered(vfs_proc_lifecycle(p),request.request_id);",
        "workflow fence delivery ACK is not success and named-request guarded",
    )
    if delivery_guard <= copy_receipt or delivery_guard != delivered - len(
        "if(status==AGENT_STATUS_OK&&request_ptr!=0)"
    ):
        raise ContractError(
            "workflow fence receipt is acknowledged before successful copyout"
        )


def validate_bootstrap_controller_adoption(
    kernel_agent_h: str, core: str, proc: str, lifecycle: str
) -> None:
    adoption = compact(
        function_body(core, "agent_bootstrap_scope_controller_bind")
    )
    require(
        adoption,
        "if(parent==0||parent->is_agent||!parent->resource_domain_admin||"
        "!exec_policy_process_bootstrap(parent)||"
        "role!=AGENT_ROLE_ORCHESTRATOR)return0;",
        "bootstrap workflow controller adoption is not authority restricted",
    )
    require(
        adoption,
        "if(child==0||!child->is_agent||child->agent_role!=role||"
        "child->agent_control_id!=control_id||"
        "child->agent_controller_id!=0||child->vfs_scope_controller||"
        "control_id==0||"
        "!parent->workflow_lifecycle_charged||"
        "!child->workflow_lifecycle_charged||"
        "parent->vfs_scope_id<VFS_SCOPE_FIRST_DYNAMIC||"
        "parent->vfs_scope_id>=FS_OWNER_SCOPE_FLAG||"
        "parent->vfs_scope_id!=child->vfs_scope_id||"
        "parent->vfs_scope_id!=parent->storage_principal_id||"
        "child->vfs_scope_id!=child->storage_principal_id)return-1;",
        "bootstrap workflow controller adoption is not inherited-scope fail closed",
    )
    require(
        adoption,
        "if(!workflow_lifecycle_key_valid(parent_lifecycle)||"
        "!workflow_lifecycle_key_valid(child_lifecycle)||"
        "!workflow_lifecycle_key_equal(parent_lifecycle,child_lifecycle)||"
        "workflow_lifecycle_scope(child_lifecycle,&lifecycle_scope)<0||"
        "lifecycle_scope!=child->vfs_scope_id)return-1;",
        "bootstrap workflow controller adoption is not full-lifecycle bound",
    )
    require(
        adoption,
        "returnvfs_scope_bind_controller(child->vfs_scope_id,child_lifecycle,"
        "control_id)<0?-1:1;",
        "bootstrap workflow controller adoption does not use the atomic binder",
    )
    reject(
        adoption,
        "child->vfs_scope_controller=",
        "bootstrap controller adoption changes scope ownership",
    )

    require(
        compact(kernel_agent_h),
        "intagent_bootstrap_scope_controller_bind("
        "conststructproc*parent,conststructproc*child,introle,"
        "uint64control_id);",
        "bootstrap workflow controller binder is not exported to publication",
    )

    make_role = compact(function_body(core, "agent_make_role"))
    require(
        make_role,
        "if(p->vfs_scope_controller&&vfs_scope_bind_controller("
        "p->vfs_scope_id,vfs_proc_lifecycle(p),p->agent_control_id)<0)"
        "gotofail;",
        "fresh workflow controller binding changed",
    )
    reject(
        make_role,
        "agent_bootstrap_scope_controller_bind(",
        "bootstrap controller is bound before final process publication",
    )
    reject(
        make_role,
        "p->vfs_scope_controller=",
        "agent role admission rewrites scope ownership",
    )

    fork = compact(function_body(proc, "fork_common"))
    adoption_call = (
        "if(make_agent&&admission==PROC_ADMIT_AGENT&&"
        "scope_mode==VFS_SPAWN_SCOPE_INHERIT&&"
        "agent_bootstrap_scope_controller_bind("
        "p,np,agent_role,np->agent_control_id)<0){"
        "intr_restore(publish_enabled);freeproc(np);gotofail;}"
    )
    require_once(
        fork,
        adoption_call,
        "bootstrap controller publication is not exact and fail closed",
    )
    require_order(
        fork,
        (
            "publish_enabled=intr_save();",
            "agent_lifecycle_spawn_publish_locked(p,np)<0",
            "if(proc_child_bind(p,np)<0)",
            adoption_call,
            "*(nt->trapframe)=*(t->trapframe);",
            "nt->state=RUNNABLE;",
            "add_task(nt);",
        ),
        "bootstrap controller bind is not the last fallible pre-runnable publication",
    )
    after_adoption = fork[fork.index(adoption_call) + len(adoption_call) :]
    require_order(
        after_adoption,
        (
            "*(nt->trapframe)=*(t->trapframe);",
            "nt->state=RUNNABLE;",
            "add_task(nt);",
            "proc_vm_snapshot_end(p);",
            "intr_restore(publish_enabled);",
            "returnnp->pid;",
        ),
        "bootstrap controller bind is not followed by infallible publication",
    )
    return_position = require(
        after_adoption,
        "returnnp->pid;",
        "bootstrap controller bind does not complete process publication",
    )
    for forbidden in ("gotofail;", "return-1;", "freeproc(np);"):
        reject(
            after_adoption[:return_position],
            forbidden,
            "bootstrap controller bind is followed by a recoverable failure",
        )

    exec_public = compact(
        function_body(core, "agent_core_exec_public_commit")
    )
    require(
        exec_public,
        "if(p->vfs_scope_controller||workflow_lifecycle_controller_matches("
        "vfs_proc_lifecycle(p),p->vfs_scope_id,p->agent_control_id)||"
        "!agent_lifecycle_context_lane_quiescent(p))return-1;",
        "adopted workflow controller can shed its identity through public exec",
    )

    bind = compact(
        function_body(lifecycle, "workflow_lifecycle_bind_controller")
    )
    require_order(
        bind,
        (
            "if(control_id==0)return-1;",
            "enabled=intr_save();",
            "record=workflow_lifecycle_find_locked(key);",
            "record->controller_control_id==0||"
            "record->controller_control_id==control_id",
            "record->controller_control_id=control_id;",
            "intr_restore(enabled);",
        ),
        "workflow lifecycle controller binding is not atomic and one-time",
    )


def validate_bootstrap_controller_replacement(
    lifecycle: str, vfs_security: str, agent_lifecycle: str
) -> None:
    lookup = compact(
        function_body(lifecycle, "workflow_lifecycle_find_locked")
    )
    require_order(
        lookup,
        (
            "if(!workflow_lifecycle_key_valid(key)||"
            "key.id>WORKFLOW_LIFECYCLE_CAP)return0;",
            "slot=key.id-1;",
            "record=&workflow_lifecycles[slot];",
            "if(!record->used||record->generation!=key.generation)return0;",
            "returnrecord;",
        ),
        "workflow lifecycle lookup is not full id/generation bound",
    )

    unbind = compact(
        function_body(lifecycle, "workflow_lifecycle_unbind_controller")
    )
    exact_clear = (
        "if(!record->closing&&record->controller_control_id==control_id){"
        "record->controller_control_id=0;result=1;}"
    )
    require_once(
        unbind,
        exact_clear,
        "workflow lifecycle controller unbind clears without an exact open match",
    )
    require_order(
        unbind,
        (
            "if(control_id==0)return-1;",
            "enabled=intr_save();",
            "record=workflow_lifecycle_find_locked(key);",
            "if(record!=0&&record->scope_id==scope_id&&record->members>0){",
            "result=0;",
            exact_clear,
            "intr_restore(enabled);",
            "returnresult;",
        ),
        "workflow lifecycle controller unbind is not full-key/scope/identity bound",
    )
    require_once(
        unbind,
        "record->controller_control_id=0;",
        "workflow lifecycle controller unbind has a second clear path",
    )

    scope_unbind = compact(
        function_body(vfs_security, "vfs_scope_unbind_controller")
    )
    require_order(
        scope_unbind,
        (
            "if(scope_id<VFS_SCOPE_FIRST_DYNAMIC||control_id==0)return-1;",
            "enabled=intr_save();",
            "ref=vfs_scope_find_locked(scope_id);",
            "if(ref!=0&&!ref->retiring&&"
            "workflow_lifecycle_key_equal(ref->lifecycle,lifecycle))",
            "result=workflow_lifecycle_unbind_controller("
            "lifecycle,scope_id,control_id);",
            "intr_restore(enabled);",
            "returnresult;",
        ),
        "VFS controller unbind is not exact lifecycle/scope/identity bound",
    )
    require_once(
        scope_unbind,
        "workflow_lifecycle_unbind_controller(lifecycle,scope_id,control_id);",
        "VFS controller unbind does not delegate exactly once",
    )

    departure = compact(
        function_body(
            agent_lifecycle, "agent_lifecycle_controller_departing_locked"
        )
    )
    branch_token = "if(!departure.scope_controller){"
    branch_at = require(
        departure,
        branch_token,
        "borrowed controller unbind is not confined to non-scope controllers",
    )
    branch_open = branch_at + len(branch_token) - 1
    branch_close = matching(departure, branch_open, "{", "}")
    borrowed = departure[branch_open + 1 : branch_close]
    require_once(
        departure,
        "vfs_scope_unbind_controller(",
        "borrowed controller unbind is not confined to one departure path",
    )
    require_once(
        borrowed,
        "vfs_scope_unbind_controller(",
        "borrowed controller unbind escaped the non-scope-controller path",
    )
    require_once(
        borrowed,
        "proc_request_controller_exit("
        "departure.lifecycle,departure.control_id,AGENT_STATUS_CANCELLED);"
        "if(finish&&vfs_scope_unbind_controller("
        "departure.scope_id,departure.lifecycle,departure.control_id)<0)"
        "panic();returndeparture.control_id;",
        "borrowed controller unbind is not finish-only after child retirement",
    )


def validate_request_and_cache(fence: str) -> None:
    receipt_init = compact(
        function_body(fence, "agent_workflow_fence_receipt_init")
    )
    require_order(
        receipt_init,
        (
            "if(receipt==0)return;",
            "memset(receipt,0,sizeof(*receipt));",
            "receipt->version=AGENT_WORKFLOW_FENCE_VERSION;",
            "receipt->struct_size=sizeof(*receipt);",
            "receipt->status=status;",
        ),
        "workflow fence receipt, including reserved account fields, is not zero initialized",
    )

    validate = compact(function_body(fence, "agent_workflow_fence_request_validate"))
    require(
        validate,
        "if(request==0)returnAGENT_STATUS_OK;",
        "workflow fence no-request form is not explicit",
    )
    for predicate, status, label in (
        ("request->version!=AGENT_WORKFLOW_FENCE_VERSION", "AGENT_STATUS_BAD_VERSION", "version"),
        ("request->struct_size!=sizeof(*request)", "AGENT_STATUS_BAD_SIZE", "size"),
    ):
        require(
            validate,
            f"if({predicate})return{status};",
            f"workflow fence request {label} is not rejected precisely",
        )
    require(
        validate,
        "if(request->flags!=0||request->reserved!=0||request->request_id==0)returnAGENT_STATUS_BAD_PARAM;",
        "workflow fence request flags/reserved/request_id are not fail closed",
    )
    require(
        validate,
        "returnAGENT_STATUS_OK;",
        "valid workflow fence request is not accepted",
    )

    equal = compact(function_body(fence, "agent_workflow_fence_challenge_equal"))
    require(
        equal,
        "for(uinti=0;i<AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE;i++)if(a[i]!=b[i])return0;return1;",
        "workflow fence challenge comparison does not cover every byte",
    )

    lookup = compact(function_body(fence, "agent_workflow_fence_cache_lookup"))
    require(
        lookup,
        "if(request==0)return0;",
        "workflow fence cache incorrectly handles anonymous requests",
    )
    for token, message in (
        (
            "!workflow_lifecycle_key_equal(cache->key,key)",
            "workflow fence retry cache is not lifecycle-generation keyed",
        ),
        (
            "request->request_id==cache->request_id",
            "workflow fence retry cache does not match an exact request_id",
        ),
        (
            "!agent_workflow_fence_challenge_equal(request->challenge,cache->receipt.challenge)",
            "same-id workflow fence retry does not bind its challenge",
        ),
        (
            "result=AGENT_STATUS_CONFLICT;",
            "same request_id with a different challenge is not a conflict",
        ),
        (
            "*receipt=cache->receipt;result=1;",
            "exact workflow fence retry does not replay the cached receipt",
        ),
        (
            "request->request_id<cache->request_id",
            "workflow fence cache lacks monotonic stale detection",
        ),
        (
            "result=AGENT_STATUS_STALE;",
            "lower workflow fence request_id is not stale",
        ),
        (
            "elseif(!cache->delivered){result=AGENT_STATUS_RETRY;}",
            "a higher workflow fence request can evict an undelivered receipt",
        ),
    ):
        require(lookup, token, message)
    require_order(
        lookup,
        (
            "request->request_id==cache->request_id",
            "!agent_workflow_fence_challenge_equal",
            "result=AGENT_STATUS_CONFLICT;",
            "*receipt=cache->receipt;result=1;",
            "request->request_id<cache->request_id",
            "result=AGENT_STATUS_STALE;",
            "elseif(!cache->delivered){result=AGENT_STATUS_RETRY;}",
        ),
        "workflow fence retry/conflict/stale decisions are ordered incorrectly",
    )
    require_order(
        lookup,
        ("enabled=intr_save();", "cache=&agent_workflow_fence_cache", "intr_restore(enabled);"),
        "workflow fence cache lookup is not serialized",
    )

    publish = compact(function_body(fence, "agent_workflow_fence_cache_publish"))
    require(
        publish,
        "if(request_id==0||receipt==0||key.id==0||key.id>WORKFLOW_LIFECYCLE_CAP)return;",
        "workflow fence cache publishes anonymous or invalid receipts",
    )
    require_order(
        publish,
        (
            "enabled=intr_save();",
            "cache->key=key;",
            "cache->request_id=request_id;",
            "cache->receipt=*receipt;",
            "cache->valid=1;",
            "cache->delivered=0;",
            "intr_restore(enabled);",
        ),
        "workflow fence cache publication is not atomic and complete",
    )

    delivered = compact(
        function_body(fence, "agent_workflow_fence_receipt_delivered")
    )
    require_order(
        delivered,
        (
            "if(request_id==0||key.id==0||key.id>WORKFLOW_LIFECYCLE_CAP)return;",
            "enabled=intr_save();",
            "cache=&agent_workflow_fence_cache[key.id-1];",
            "cache->valid&&workflow_lifecycle_key_equal(cache->key,key)&&cache->request_id==request_id",
            "cache->delivered=1;",
            "intr_restore(enabled);",
        ),
        "workflow fence delivery ACK is not full-key/request-id serialized",
    )


def validate_fence_execution(fence: str) -> None:
    commit = compact(
        function_body(fence, "agent_workflow_fence_commit_cached")
    )
    require_order(
        commit,
        (
            "enabled=intr_save();",
            "workflow_lifecycle_fence_end(key,fence_sequence,1)",
            "agent_workflow_fence_cache_publish(key,request_id,receipt);",
            "intr_restore(enabled);",
        ),
        "workflow fence sequence and retry cache are not atomically published",
    )
    if commit.count("intr_save()") != 1 or commit.count(
        "intr_restore(enabled)"
    ) != 1:
        raise ContractError(
            "workflow fence atomic commit has a split interrupt boundary"
        )

    body = compact(function_body(fence, "agent_workflow_fence_execute"))
    require(
        body,
        "if(agent_metadata_quiescence_fence_snapshot_current(&metadata_generation)<0||metadata_generation==0){status=AGENT_STATUS_RETRY;gotoabort_fence;}",
        "workflow fence does not fail closed on an unavailable metadata cut",
    )
    for obsolete in (
        "agent_metadata_quiescence_fence_current()",
        "agent_metadata_catalog_generation()",
        "agent_metadata_catalog_generation_snapshot(",
    ):
        reject(
            body,
            obsolete,
            "workflow fence bypasses the integrated metadata cut",
        )
    require(
        body,
        "if(!workflow_lifecycle_controller_matches(key,p->vfs_scope_id,p->agent_control_id)){status=AGENT_STATUS_DENIED;gotofail;}",
        "workflow fence does not require the lifecycle controller and scope",
    )
    require_order(
        body,
        (
            "status=agent_workflow_fence_request_validate(request);",
            "if(p==0||!p->is_agent||!agent_identity_has_cap(p,AGENT_CAP_ORCHESTRATE))",
            "key=vfs_proc_lifecycle(p);",
            "workflow_lifecycle_controller_matches(key,p->vfs_scope_id,p->agent_control_id)",
            "cache_status=agent_workflow_fence_cache_lookup(key,request,receipt);",
            "workflow_lifecycle_fence_begin(key,&fence_sequence)",
            "metadata_generation=0;",
            "agent_metadata_quiescence_fence_snapshot_current(&metadata_generation)",
            "fs_deferred_reclaim_drain_current()",
            "fs_epoch_commit()",
            "workflow_credit_domain_fence(key,p->resource_account,storage_account,&credit)",
            "agent_workflow_fence_credit_digest(&credit,credit_digest);",
            "agent_evidence_seal(key,fence_sequence,challenge,metadata_generation,credit.epoch,credit_digest,&evidence)",
            "agent_workflow_fence_commit_cached(key,fence_sequence,request_id,&completed);",
        ),
        "workflow fence gate/flush/seal/commit/publication order changed",
    )
    if body.count("agent_metadata_quiescence_fence_snapshot_current(") != 1:
        raise ContractError("workflow fence takes more than one metadata cut")
    if body.count("workflow_credit_domain_fence(") != 1:
        raise ContractError("workflow fence takes more than one exact credit snapshot")
    require(
        body,
        "if(receipt!=0)*receipt=completed;returnAGENT_STATUS_OK;",
        "workflow fence does not return the committed receipt",
    )
    require(
        body,
        "status=AGENT_STATUS_DENIED;gotofail;",
        "workflow fence lacks an orchestrator-capability denial",
    )
    require(
        body,
        "if(cache_status==1)returnAGENT_STATUS_OK;",
        "exact workflow fence retry re-enters the fence",
    )
    require(
        body,
        "if(cache_status<0){status=cache_status;gotofail;}",
        "workflow fence cache conflict/stale status is not preserved",
    )
    require(
        body,
        "if(request!=0)memmove(challenge,request->challenge,sizeof(challenge));",
        "workflow fence challenge is not copied byte-for-byte",
    )
    for token, message in (
        ("completed.fence_sequence=fence_sequence;", "receipt sequence is not the committed fence sequence"),
        ("completed.metadata_generation=metadata_generation;", "receipt metadata generation is not the sealed generation"),
        ("completed.credit_epoch=credit.epoch;", "receipt credit epoch is not the sealed epoch"),
        ("completed.credit_exec_account.slot=credit.account[WORKFLOW_CREDIT_EXEC].handle.slot;", "receipt omits the exact exec account slot"),
        ("completed.credit_exec_account.generation=credit.account[WORKFLOW_CREDIT_EXEC].handle.generation;", "receipt omits the exact exec account generation"),
        ("completed.credit_storage_account.slot=credit.account[WORKFLOW_CREDIT_STORAGE].handle.slot;", "receipt omits the exact storage account slot"),
        ("completed.credit_storage_account.generation=credit.account[WORKFLOW_CREDIT_STORAGE].handle.generation;", "receipt omits the exact storage account generation"),
        ("memmove(completed.credit_digest,credit_digest,sizeof(completed.credit_digest));", "receipt omits the evidence-bound credit digest"),
        ("memmove(completed.challenge,challenge,sizeof(completed.challenge));", "receipt challenge is not the sealed challenge"),
        ("if(evidence.fence_sequence!=fence_sequence)panic", "evidence result is not checked against the gate sequence"),
    ):
        require(body, token, message)
    require_order(
        body,
        (
            "for(uintkind=0;kind<RESOURCE_KIND_COUNT;kind++)completed.resource_used[kind]=credit.used[kind];",
            "completed.credit_exec_account.slot=credit.account[WORKFLOW_CREDIT_EXEC].handle.slot;",
            "completed.credit_exec_account.generation=credit.account[WORKFLOW_CREDIT_EXEC].handle.generation;",
            "completed.credit_storage_account.slot=credit.account[WORKFLOW_CREDIT_STORAGE].handle.slot;",
            "completed.credit_storage_account.generation=credit.account[WORKFLOW_CREDIT_STORAGE].handle.generation;",
            "memmove(completed.credit_digest,credit_digest,sizeof(completed.credit_digest));",
        ),
        "workflow fence receipt does not project the exact hashed credit snapshot",
    )
    flags_at = require(body, "completed.flags=", "workflow fence receipt flags are not initialized")
    commit_at = require(
        body,
        "agent_workflow_fence_commit_cached(key,fence_sequence,request_id,&completed);",
        "workflow fence success does not atomically commit and cache",
    )
    flag_block = body[flags_at:commit_at]
    receipt_flags = (
        "AGENT_WORKFLOW_FENCE_RECEIPT_F_PARTIAL_COVERAGE",
        "AGENT_WORKFLOW_FENCE_RECEIPT_F_CREDIT_EXACT",
        "AGENT_WORKFLOW_FENCE_RECEIPT_F_EVIDENCE_SEALED",
        "AGENT_WORKFLOW_FENCE_RECEIPT_F_METADATA_VOLATILE",
    )
    for flag in receipt_flags:
        require(flag_block, flag, f"workflow fence receipt omits {flag}")
    flag_assignment = re.search(r"completed\.flags=([^;]+);", flag_block)
    if flag_assignment is None or sorted(flag_assignment.group(1).split("|")) != sorted(
        receipt_flags
    ):
        raise ContractError("workflow fence receipt flags are not the exact v1 set")

    begin_at = body.index("workflow_lifecycle_fence_begin(key,&fence_sequence)")
    between = body[begin_at:commit_at]
    require(
        between,
        "workflow_lifecycle_fence_begin(key,&fence_sequence)<0){status=AGENT_STATUS_RETRY;gotofail;}",
        "workflow fence begin failure does not stay outside the abort path",
    )
    if between.count("gotofail;") != 1:
        raise ContractError(
            "a post-gate workflow fence failure bypasses the abort path"
        )
    require(
        body[commit_at:],
        "abort_fence:if(workflow_lifecycle_fence_end(key,fence_sequence,0)<0)panic",
        "workflow fence failures do not abort without advancing",
    )
    reject(
        body,
        "workflow_lifecycle_fence_end(key,fence_sequence,1)",
        "workflow fence bypasses atomic sequence/cache publication",
    )
    reject(
        body,
        "agent_workflow_fence_cache_publish(key,request_id,&completed)",
        "workflow fence bypasses atomic sequence/cache publication",
    )


def validate_lifecycle(lifecycle: str) -> None:
    require(
        compact(lifecycle),
        "uintdeparting_operations;",
        "workflow lifecycle record omits departure operations",
    )
    create = compact(function_body(lifecycle, "workflow_lifecycle_create"))
    require(
        create,
        "record->departing_operations=0;",
        "new workflow lifecycle inherits departure operations",
    )

    controller = compact(
        function_body(lifecycle, "workflow_lifecycle_controller_matches")
    )
    require_order(
        controller,
        (
            "if(control_id==0)return0;",
            "enabled=intr_save();",
            "record=workflow_lifecycle_find_locked(key);",
            "record!=0&&record->scope_id==scope_id&&record->controller_control_id==control_id&&record->members>0",
            "intr_restore(enabled);",
            "returnresult;",
        ),
        "workflow lifecycle controller match is not full-key/scope/identity bound",
    )

    join = compact(function_body(lifecycle, "workflow_lifecycle_join"))
    require_order(
        join,
        (
            "record=workflow_lifecycle_find_locked(key);",
            "record!=0&&!record->closing&&!record->fence_gate&&record->members!=(uint)-1",
            "record->members++;",
        ),
        "workflow lifecycle join can cross a closed fence gate",
    )

    operation_enter = compact(
        function_body(lifecycle, "workflow_lifecycle_operation_enter")
    )
    for token, message in (
        ("!record->fence_gate", "ordinary workflow operations can enter a closed fence gate"),
        ("record->active_operations++", "workflow operation gate does not track entrants"),
    ):
        require(operation_enter, token, message)

    departure_enter = compact(
        function_body(lifecycle, "workflow_lifecycle_departure_enter")
    )
    require_order(
        departure_enter,
        (
            "record=workflow_lifecycle_find_locked(key);",
            "record!=0&&record->members>0&&!record->fence_gate&&record->departing_operations!=(uint)-1",
            "record->departing_operations++;",
        ),
        "workflow departure can cross a fence or is not counted",
    )
    reject(
        departure_enter,
        "!record->closing",
        "workflow departure is illegally blocked after close",
    )
    departure_leave = compact(
        function_body(lifecycle, "workflow_lifecycle_departure_leave")
    )
    require(
        departure_leave,
        "if(record==0||record->departing_operations==0)panic",
        "workflow departure leave does not fail closed on underflow",
    )
    require(
        departure_leave,
        "record->departing_operations--;",
        "workflow departure leave does not settle its counter",
    )

    close = compact(function_body(lifecycle, "workflow_lifecycle_close"))
    close_authority = (
        "if((!trusted&&(!workflow_lifecycle_key_equal(key,expected)||"
        "control_id==0||record->controller_control_id==0||"
        "record->controller_control_id!=control_id))||"
        "record->members==0)break;"
    )
    require_once(
        close,
        close_authority,
        "workflow close authority no longer separates trusted and exact-owner paths",
    )
    if close.count("record->controller_control_id") != 2:
        raise ContractError(
            "trusted workflow close gained a global controller requirement"
        )
    require_order(
        close,
        (
            close_authority,
            "if(record->fence_gate){result=1;break;}",
            "record->closing=1;",
            "*closed=key;",
        ),
        "workflow close can race an active fence",
    )

    close_owned = compact(
        function_body(lifecycle, "workflow_lifecycle_close_owned")
    )
    if close_owned != (
        "returnworkflow_lifecycle_close("
        "scope_id,expected,control_id,0,closed);"
    ):
        raise ContractError(
            "owned workflow close is not exact-key/controller authorized"
        )
    close_trusted = compact(
        function_body(lifecycle, "workflow_lifecycle_close_trusted")
    )
    if close_trusted != (
        "returnworkflow_lifecycle_close("
        "scope_id,workflow_lifecycle_none(),0,1,closed);"
    ):
        raise ContractError(
            "trusted workflow close is not explicitly controllerless"
        )

    begin = compact(function_body(lifecycle, "workflow_lifecycle_fence_begin"))
    require_order(
        begin,
        (
            "record!=0&&!record->closing&&record->members>0",
            "!record->fence_gate",
            "record->fence_gate=1;",
            "record->active_operations==0",
            "record->departing_operations==0",
            "*fence_sequence=record->fence_sequence+1;",
            "if(result<0)record->fence_gate=0;",
        ),
        "workflow lifecycle fence does not close, drain, and roll back atomically",
    )

    end = compact(function_body(lifecycle, "workflow_lifecycle_fence_end"))
    for token, message in (
        ("record->fence_gate", "workflow lifecycle fence end accepts an open gate"),
        ("record->active_operations==0", "workflow lifecycle fence commits with active operations"),
        ("record->departing_operations==0", "workflow lifecycle fence commits with departing operations"),
        ("fence_sequence==record->fence_sequence+1", "workflow lifecycle fence accepts a non-next sequence"),
        ("if(committed)record->fence_sequence=fence_sequence;", "workflow lifecycle abort advances the sequence"),
        ("record->fence_gate=0;", "workflow lifecycle fence end leaves the gate closed"),
    ):
        require(end, token, message)
    reject(
        end,
        "if(!committed)record->fence_sequence=fence_sequence;",
        "workflow lifecycle abort advances the sequence",
    )

    reclaim = compact(function_body(lifecycle, "workflow_lifecycle_reclaim"))
    require_order(
        reclaim,
        (
            "record->active_operations==0",
            "record->departing_operations==0",
            "!record->fence_gate",
            "record->departing_operations=0;",
        ),
        "workflow lifecycle can reclaim with unsettled departures",
    )


def validate_credit(credit: str) -> None:
    fence = compact(function_body(credit, "workflow_credit_domain_fence"))
    require_order(
        fence,
        (
            "resource_credit_snapshot_pair_trim(exec_account,storage_account,out)",
            "for(uintkind=0;kind<RESOURCE_KIND_COUNT;kind++)",
            "if(out->pending[kind]!=0)return-1;",
            "out->key=key;",
        ),
        "workflow credit fence is not an exact pending-free snapshot",
    )


def validate_metadata_cut(
    metadata_objects: str, metadata_catalog: str, live_query_events: str
) -> None:
    current = compact(
        function_body(
            metadata_objects,
            "agent_metadata_quiescence_fence_snapshot_current",
        )
    )
    require_order(
        current,
        (
            "if(metadata_generation==0)return-1;",
            "*metadata_generation=0;",
            "if(p==0||!p->is_agent)return-1;",
            "scope_id=agent_identity_proc_scope(p);",
            "if(!agent_scope_valid(scope_id))return-1;",
            "lifecycle=vfs_proc_lifecycle(p);",
            "if(!workflow_lifecycle_key_valid(lifecycle)||!agent_metadata_txn_lock(1))return-1;",
            "result=agent_live_query_fence_drain(lifecycle,scope_id);",
            "if(result==AGENT_STATUS_OK)result=agent_metadata_catalog_fence_generation(scope_id,lifecycle,metadata_generation);",
            "agent_metadata_txn_unlock();",
            "returnresult;",
        ),
        "workflow fence metadata cut is not one volatile lifecycle transaction",
    )
    reject(
        current,
        "agent_metadata_catalog_generation_snapshot(",
        "workflow fence metadata cut bypasses the volatile transaction",
    )
    if (
        current.count("agent_metadata_txn_lock(1)") != 1
        or current.count("agent_metadata_txn_unlock()") != 1
        or current.count("agent_live_query_fence_drain(") != 1
        or current.count("agent_metadata_catalog_fence_generation(") != 1
    ):
        raise ContractError(
            "workflow fence metadata cut has a split or repeated volatile transaction"
        )

    drain = compact(
        function_body(live_query_events, "agent_live_query_fence_drain")
    )
    require_order(
        drain,
        (
            "if(!agent_live_query_domain_valid(key,scope_id)||!agent_metadata_txn_owned(0))returnAGENT_STATUS_RETRY;",
            "for(uintslot=0;slot<AGENT_LIVE_QUERY_TOMBSTONE_CAP;slot++)",
            "intenabled=intr_save();pending=agent_live_query_tombstones[slot].used&&agent_live_query_domain_equal(agent_live_query_tombstones[slot].key,agent_live_query_tombstones[slot].scope_id,key,scope_id);",
            "intr_restore(enabled);if(!pending)continue;if(agent_live_query_tombstone_process(slot)<0)retry=1;",
            "for(uintslot=0;slot<AGENT_FILE_META_MAX;slot++)",
            "intenabled=intr_save();pending=agent_live_query_content_pending[slot].used&&agent_live_query_domain_equal(agent_live_query_content_pending[slot].receipt.lifecycle,agent_live_query_content_pending[slot].receipt.scope_id,key,scope_id);",
            "intr_restore(enabled);if(!pending)continue;if(agent_live_query_content_process(slot)<0)retry=1;",
            "for(structproc*target=pool;target<&pool[NPROC];target++)",
            "intenabled=intr_save();structagent_live_query_proc_resync*state=&agent_live_query_proc_resync[target-pool];",
            "pending=agent_live_query_proc_resync_valid(state,target)&&(scope_id==VFS_SCOPE_SYSTEM||agent_live_query_domain_equal(state->key,state->scope_id,key,scope_id));",
            "intr_restore(enabled);if(pending)(void)agent_live_query_proc_resync_flush(target);",
            "if(agent_live_query_domain_generation_locked(key,scope_id)!=0||agent_live_query_proc_resync_pending_domain(key,scope_id))retry=1;intr_restore(enabled);}returnretry?AGENT_STATUS_RETRY:AGENT_STATUS_OK;",
        ),
        "workflow fence metadata cut does not fully drain lifecycle live-query work",
    )
    if drain.count("intr_save()") != 4 or drain.count("intr_restore(enabled)") != 4:
        raise ContractError(
            "workflow fence live-query drain does not use short serialized snapshots"
        )
    for work in (
        "agent_live_query_tombstone_process(slot)",
        "agent_live_query_content_process(slot)",
        "agent_live_query_proc_resync_flush(target)",
    ):
        work_at = require(
            drain,
            work,
            "workflow fence live-query drain omits required queued work",
        )
        restore_at = drain.rfind("intr_restore(enabled);", 0, work_at)
        save_at = drain.rfind("intr_save()", 0, work_at)
        if restore_at < save_at:
            raise ContractError(
                "workflow fence live-query drain performs catalog/event work with IRQs disabled"
            )

    pending_generation = compact(
        function_body(
            live_query_events, "agent_live_query_domain_generation_locked"
        )
    )
    require_order(
        pending_generation,
        (
            "generation=agent_live_query_global_resync_generation;",
            "for(uintslot=0;slot<AGENT_LIVE_QUERY_DOMAIN_CAP;slot++)",
            "state->scope_id==VFS_SCOPE_SYSTEM||agent_live_query_domain_equal(state->key,state->scope_id,key,scope_id)",
            "state->generation>generation",
            "generation=state->generation;",
            "returngeneration;",
        ),
        "workflow fence ignores pending SYSTEM or lifecycle live-query state",
    )

    generation = compact(
        function_body(metadata_catalog, "agent_metadata_catalog_fence_generation")
    )
    require_order(
        generation,
        (
            "agent_catalog_require_txn();",
            "if(generation==0)return-1;",
            "*generation=0;",
            "if(!agent_scope_valid(scope_id)||!workflow_lifecycle_key_valid(lifecycle)||agent_catalog_mutation_owner!=0||agent_catalog_active_edit!=0||vfs_scope_lifecycle(scope_id,&current)<0||!workflow_lifecycle_key_equal(current,lifecycle))returnAGENT_CATALOG_CONFLICT;",
            "scope_generation=agent_file_state_scope_generation(scope_id);",
            "system_generation=agent_file_state_scope_generation(VFS_SCOPE_SYSTEM);",
            "value=agent_catalog_hash_bytes(value,&lifecycle,sizeof(lifecycle));",
            "value=agent_catalog_hash_bytes(value,&scope_id,sizeof(scope_id));",
            "value=agent_catalog_hash_bytes(value,&agent_catalog_generation,sizeof(agent_catalog_generation));",
            "value=agent_catalog_hash_bytes(value,&scope_generation,sizeof(scope_generation));",
            "value=agent_catalog_hash_bytes(value,&system_generation,sizeof(system_generation));",
            "*generation=value==0?AGENT_CATALOG_PLAN_HASH:value;",
            "return0;",
        ),
        "metadata generation is not lifecycle, scope, catalog, and SYSTEM bound",
    )


def validate_evidence(evidence: str) -> None:
    prepare = compact(
        function_body(evidence, "agent_evidence_prepare_seal_stable")
    )
    for token, message in (
        ("agent_evidence_hash_u64(&hash,workflow_fence_sequence);", "evidence root omits the workflow fence sequence"),
        ("agent_evidence_hash_u64(&hash,metadata_generation);", "evidence root omits the metadata generation"),
        ("agent_evidence_hash_u64(&hash,credit_epoch);", "evidence root omits the credit epoch"),
        ("agent_sha256_update(&hash,credit_digest,AGENT_SHA256_DIGEST_SIZE);", "evidence root omits the exact credit digest"),
        ("plan->result.fence_sequence=workflow_fence_sequence;", "evidence receipt sequence is not the hashed sequence"),
        ("plan->result.metadata_generation=metadata_generation;", "evidence receipt metadata generation is not hashed"),
        ("plan->result.credit_epoch=credit_epoch;", "evidence receipt credit epoch is not hashed"),
    ):
        require(prepare, token, message)
    public = compact(function_body(evidence, "agent_evidence_seal"))
    require(
        public,
        "if(challenge==0||credit_digest==0||out==0||",
        "public evidence seal accepts a missing credit binding",
    )
    require(
        public,
        "agent_evidence_seal_stable(state,workflow_fence_sequence,challenge,metadata_generation,credit_epoch,credit_digest,1,out)",
        "public evidence seal does not forward one fence binding unchanged",
    )


def validate_credit_digest(fence: str) -> None:
    require(
        fence,
        'static const char domain[] = "AgentOS workflow credit exact v1";',
        "workflow credit digest lacks canonical domain separation",
    )
    for name, width in (
        ("agent_workflow_fence_hash_u32", 4),
        ("agent_workflow_fence_hash_u64", 8),
    ):
        encoder = compact(function_body(fence, name))
        require(
            encoder,
            f"ucharencoded[{width}];",
            f"workflow credit {width * 8}-bit encoder width drifted",
        )
        require(
            encoder,
            "for(uinti=0;i<sizeof(encoded);i++)encoded[i]=(uchar)(value>>(i*8U));",
            "workflow credit digest integers are not canonical little-endian",
        )
        require(
            encoder,
            "agent_sha256_update(hash,encoded,sizeof(encoded));",
            "workflow credit digest encoder does not hash every encoded byte",
        )

    digest = compact(
        function_body(fence, "agent_workflow_fence_credit_digest")
    )
    require_order(
        digest,
        (
            "agent_sha256_init(&hash);",
            "agent_sha256_update(&hash,domain,sizeof(domain)-1U);",
            "agent_workflow_fence_hash_u32(&hash,credit->key.id);",
            "agent_workflow_fence_hash_u64(&hash,credit->key.generation);",
            "agent_workflow_fence_hash_u64(&hash,credit->epoch);",
            "for(uintrole=0;role<WORKFLOW_CREDIT_ACCOUNT_COUNT;role++){",
            "agent_workflow_fence_hash_u32(&hash,credit->account[role].handle.slot);",
            "agent_workflow_fence_hash_u64(&hash,credit->account[role].handle.generation);",
            "for(uintkind=0;kind<RESOURCE_KIND_COUNT;kind++)",
            "agent_workflow_fence_hash_u64(&hash,credit->used[kind]);",
            "agent_sha256_final(&hash,digest);",
        ),
        "workflow credit digest no longer canonically binds key/epoch/handles/used",
    )


def validate_user_wrapper(user_syscall: str) -> None:
    wrapper = compact(function_body(user_syscall, "agent_workflow_fence"))
    expected = (
        "returnsyscall(SYS_agent_run,request,receipt,0,AGENT_RUN_F_FENCE);"
    )
    if wrapper != expected:
        raise ContractError(
            "user workflow fence wrapper does not exactly reuse SYS_agent_run"
        )


def load_sources(root: Path) -> dict[str, str]:
    return {
        name: (root / relative).read_text(encoding="utf-8")
        for name, relative in SOURCE_PATHS.items()
    }


def validate_sources(sources: dict[str, str]) -> None:
    validate_abi(sources)
    validate_dispatch(sources["core"])
    validate_bootstrap_controller_adoption(
        sources["kernel_agent_h"], sources["core"], sources["proc"],
        sources["lifecycle"]
    )
    validate_bootstrap_controller_replacement(
        sources["lifecycle"], sources["vfs_security"],
        sources["agent_lifecycle"]
    )
    validate_request_and_cache(sources["fence"])
    validate_fence_execution(sources["fence"])
    validate_lifecycle(sources["lifecycle"])
    validate_credit(sources["credit"])
    validate_metadata_cut(
        sources["metadata_objects"],
        sources["metadata_catalog"],
        sources["live_query_events"],
    )
    validate_evidence(sources["evidence"])
    validate_credit_digest(sources["fence"])
    validate_user_wrapper(sources["user_syscall"])


def check(root: Path) -> None:
    validate_sources(load_sources(root))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (ContractError, OSError) as error:
        print(f"workflow fence check failed: {error}", file=sys.stderr)
        return 1
    print("workflow fence check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
