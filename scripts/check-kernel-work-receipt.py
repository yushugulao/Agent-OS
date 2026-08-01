#!/usr/bin/env python3
"""Check attributable syscall work receipts and timer isolation."""

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


def reject(text: str, fragment: str, message: str) -> None:
    if fragment in text:
        raise ValueError(message)


def require_count(text: str, fragment: str, count: int, message: str) -> None:
    if text.count(fragment) != count:
        raise ValueError(message)


RECEIPT_OWNED_FIELDS = {
    "kernel_syscall_preemptions_start": 2,
    "kernel_last_syscall_preemptions": 2,
    "kernel_receipt_generation": 2,
    "kernel_receipt_completion_timer_epoch": 2,
    "kernel_receipt_syscall_id": 2,
    "kernel_work_target_syscall_id": 3,
    "kernel_work_publish_receipt": 3,
}


def field_write_count(text: str, field: str) -> int:
    suffix = (
        rf"(?:->|\.){re.escape(field)}"
        rf"(?:<<=|>>=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|\+\+|--|=(?!=))"
    )
    prefix = rf"(?:\+\+|--)[A-Za-z_][A-Za-z0-9_]*(?:->|\.){re.escape(field)}"
    return len(re.findall(suffix, text)) + len(re.findall(prefix, text))


def check(root: Path) -> None:
    abi = compact(root / "kernel_work_abi.h")
    header = compact(root / "os/kernel_work.h")
    implementation = compact(root / "os/kernel_work.c")
    proc = compact(root / "os/proc.h")
    trap = compact(root / "os/trap.c")
    syscall = compact(root / "os/syscall.c")
    kernel_ids = compact(root / "os/syscall_ids.h")
    user_ids = compact(root / "user/lib/syscall_ids.h")
    arch_ids = compact(root / "user/lib/arch/riscv/syscall_ids.h.in")
    unistd = compact(root / "user/include/unistd.h")
    user_syscall = compact(root / "user/lib/syscall.c")
    workload = compact(root / "user/src/agentfs_ucore.c")
    os_sources = {
        path.name: compact(path) for path in sorted((root / "os").glob("*.c"))
    }

    for fragment, label in (
        ("#defineKERNEL_WORK_RECEIPT_ABI_VERSION1U", "ABI version"),
        ("#defineKERNEL_WORK_RECEIPT_OWNER_THREAD1U", "owner kind"),
        ("#defineKERNEL_WORK_RECEIPT_KIND_SYSCALL1U", "receipt kind"),
        ("unsignedlonglonggeneration;", "generation"),
        ("unsignedlonglongowner_generation;", "stable owner generation"),
        ("unsignedlonglongpreemptions;", "preemption count"),
        ("unsignedlonglongcompletion_timer_epoch;", "completion epoch"),
        ("unsignedlonglongobserved_timer_epoch;", "observation epoch"),
        ("intowner_tid;", "owner tid"),
        ("intowner_pid;", "owner pid"),
        ("unsignedintowner_kind;", "owner class"),
        ("unsignedintkind;", "receipt class"),
        ("intsyscall_id;", "target syscall"),
        ("sizeof(structkernel_work_receipt)==72", "fixed layout"),
    ):
        require(abi, fragment, f"kernel work receipt lacks {label}")

    for fragment in (
        "uint64kernel_receipt_generation;",
        "uint64kernel_receipt_completion_timer_epoch;",
        "intkernel_receipt_syscall_id;",
        "intkernel_work_target_syscall_id;",
        "uintkernel_work_publish_receipt;",
    ):
        require(proc, fragment, "thread state lacks attributable receipt source")

    require(
        implementation,
        "t->kernel_syscall_preemptions_start=0;"
        "t->kernel_last_syscall_preemptions=0;"
        "t->kernel_receipt_generation=0;"
        "t->kernel_receipt_completion_timer_epoch=0;"
        "t->kernel_receipt_syscall_id=-1;"
        "t->kernel_work_target_syscall_id=-1;"
        "t->kernel_work_publish_receipt=0;",
        "receipt reset does not clear the complete canonical state",
    )
    for field, expected in RECEIPT_OWNED_FIELDS.items():
        if field_write_count(implementation, field) != expected:
            raise ValueError(
                f"kernel work canonical owner has unexpected {field} writes"
            )
        for name, source in os_sources.items():
            if name != "kernel_work.c" and field_write_count(source, field):
                raise ValueError(
                    f"{field} is written outside the kernel work canonical owner"
                )

    require(
        implementation,
        "if(t->kernel_work_depth==0){t->kernel_syscall_preemptions_start="
        "t->kernel_work_redispatches;t->kernel_work_target_syscall_id=syscall_id;"
        "t->kernel_work_publish_receipt=publish_receipt;}t->kernel_work_depth++;",
        "outermost begin does not exclusively capture receipt ownership",
    )
    require(
        implementation,
        "kernel_work_begin_scope(syscall_id,"
        "syscall_class==KERNEL_WORK_SYSCALL_PUBLISH);",
        "syscall class is not bound at the outer begin",
    )
    require(
        implementation,
        "voidkernel_work_begin_background(void){kernel_work_begin_scope(-1,0);}",
        "background begin can publish a syscall receipt",
    )
    require(
        implementation,
        "outer=t->kernel_work_depth==1;if(terminal||outer){"
        "(void)kernel_work_checkpoint_mode(0,cleanup);"
        "if(t->kernel_work_publish_receipt)kernel_work_publish_receipt(t);}",
        "receipt publication is not limited to outer completion",
    )
    require_count(
        implementation,
        "kernel_work_publish_receipt(t);",
        1,
        "receipt has more than one publication path",
    )
    require(
        implementation,
        "if(terminal)t->kernel_work_depth=0;elset->kernel_work_depth--;"
        "if(terminal||outer){t->kernel_work_target_syscall_id=-1;"
        "t->kernel_work_publish_receipt=0;}",
        "terminal completion does not reset depth and in-flight ownership",
    )
    require(
        implementation,
        "kernel_work_receipt_next_generation++;"
        "t->kernel_last_syscall_preemptions=t->kernel_work_redispatches-"
        "t->kernel_syscall_preemptions_start;"
        "t->kernel_receipt_generation=kernel_work_receipt_next_generation;"
        "t->kernel_receipt_completion_timer_epoch=kernel_work_timer_epoch;"
        "t->kernel_receipt_syscall_id=t->kernel_work_target_syscall_id;",
        "published receipt is not atomically sourced from completed syscall work",
    )
    require(
        implementation,
        "if(kernel_work_timer_epoch==(uint64)-1)"
        "panic(\"kernelworktimerepochexhausted\");"
        "kernel_work_timer_epoch++;",
        "timer epoch is not a checked monotonic counter",
    )
    for fragment in (
        "receipt->generation=t->kernel_receipt_generation;",
        "receipt->owner_generation=t->identity_generation;",
        "receipt->preemptions=t->kernel_last_syscall_preemptions;",
        "receipt->completion_timer_epoch="
        "t->kernel_receipt_completion_timer_epoch;",
        "receipt->observed_timer_epoch=kernel_work_timer_epoch;",
        "receipt->owner_tid=t->tid;",
        "receipt->owner_pid=t->process!=0?t->process->pid:-1;",
        "receipt->syscall_id=t->kernel_receipt_syscall_id;",
    ):
        require(
            implementation,
            fragment,
            "snapshot is not sourced from immutable receipt and trusted owner state",
        )

    require(
        trap,
        "caseSupervisorTimer:kernel_work_timer_advance();set_next_timer();",
        "timer trap does not advance the trusted epoch",
    )
    require(
        trap,
        "kernel_work_begin_background();agent_background_checkpoint();"
        "kernel_work_end_background();",
        "timer maintenance does not use the non-publishing background scope",
    )
    reject(
        trap,
        "KERNEL_WORK_SYSCALL_PUBLISH",
        "timer maintenance can publish a syscall receipt",
    )

    require(
        syscall,
        "kernel_work_begin_syscall(id,syscall_kernel_work_class(id));",
        "dispatcher does not bind the target syscall to its work scope",
    )
    require(
        syscall,
        "uint64sys_kernel_work_last_preemptions(void){"
        "returnkernel_work_last_preemptions(curr_thread());}",
        "legacy getter bypasses the immutable receipt source",
    )
    require(
        syscall,
        "caseSYS_kernel_work_last_preemptions:"
        "caseSYS_kernel_work_receipt_snapshot:"
        "caseSYS_agent_resource_snapshot:"
        "returnKERNEL_WORK_SYSCALL_OBSERVER;",
        "receipt observers are not a non-publishing syscall class",
    )
    require(
        syscall,
        "kernel_work_receipt_snapshot(curr_thread(),&receipt)<0",
        "snapshot syscall bypasses the canonical receipt source",
    )
    require(
        syscall,
        "caseSYS_kernel_work_receipt_snapshot:"
        "ret=sys_kernel_work_receipt_snapshot(args[0],args[1]);break;",
        "receipt snapshot syscall is not dispatched",
    )

    for text, fragment in (
        (kernel_ids, "#defineSYS_kernel_work_receipt_snapshot558"),
        (user_ids, "#defineSYS_kernel_work_receipt_snapshot558"),
        (arch_ids, "#define__NR_kernel_work_receipt_snapshot558"),
    ):
        require(text, fragment, "kernel work receipt syscall IDs diverge")
    require(
        unistd,
        "intkernel_work_receipt_snapshot(structkernel_work_receipt*receipt);",
        "user receipt API is missing",
    )
    require(
        user_syscall,
        "returnsyscall(SYS_kernel_work_receipt_snapshot,receipt,sizeof(*receipt));",
        "user receipt wrapper does not pass the versioned structure size",
    )

    reject(workload, "rdtime", "agentfs still depends on user-mode rdtime")
    for fragment, label in (
        (
            "query_receipt_before.generation>query_receipt_prior.generation",
            "monotonic target generation",
        ),
        (
            "query_receipt_before.syscall_id==SYS_agent_file_query",
            "target syscall identity",
        ),
        ("query_receipt_before.preemptions>0", "target preemptions"),
        (
            "query_receipt_after.observed_timer_epoch>"
            "query_receipt_before.observed_timer_epoch",
            "cross-observation timer progress",
        ),
        (
            "query_receipt_after.generation==query_receipt_before.generation",
            "immutable receipt generation",
        ),
        (
            "query_receipt_after.completion_timer_epoch=="
            "query_receipt_before.completion_timer_epoch",
            "immutable completion epoch",
        ),
    ):
        require(workload, fragment, f"agentfs lacks {label} assertion")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(Path(args.root))
    except (OSError, ValueError) as error:
        print(f"kernel work receipt check failed: {error}", file=sys.stderr)
        return 1
    print("kernel work attributable receipt check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
