#!/usr/bin/env python3
"""Static fail-closed checks for owned mutex and exec namespace lifecycle."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYNC = (ROOT / "os/sync.c").read_text(encoding="utf-8")
SYNC_H = (ROOT / "os/sync.h").read_text(encoding="utf-8")
PROC = (ROOT / "os/proc.c").read_text(encoding="utf-8")
GUEST = (ROOT / "user/src/blocking_semantics_ucore.c").read_text(
    encoding="utf-8"
)
RUNNER = (ROOT / "scripts/run-agent-tests.sh").read_text(encoding="utf-8")
STDIO = (ROOT / "user/lib/stdio.c").read_text(encoding="utf-8")
USER_SYSCALL = (ROOT / "user/lib/syscall.c").read_text(encoding="utf-8")


def source_section(text: str, begin: str, end: str) -> str:
    start = text.find(begin)
    stop = text.find(end, start + len(begin))
    if start < 0 or stop < 0:
        raise ValueError(f"missing section {begin}")
    return text[start:stop]


def validate(sync: str, proc: str) -> None:
    for token in (
        "struct thread *owner;",
        "uint64 owner_generation;",
        "wait_queue_wake_one_thread(&m->waiters)",
        "mutex_release_thread_locks",
        "sync_proc_exec_validate_locked",
        "sync_proc_exec_reset_locked",
        "memset(p->mutex_pool, 0",
        "memset(p->semaphore_pool, 0",
        "memset(p->condvar_pool, 0",
    ):
        if token not in SYNC_H + sync:
            raise ValueError(f"missing synchronization mechanism {token}")
    validate_pos = proc.find("sync_proc_exec_validate_locked")
    commit_pos = proc.find("vfs_proc_exec_commit", validate_pos)
    reset_pos = proc.find("sync_proc_exec_reset_locked", commit_pos)
    publish_pos = proc.find("p->pagetable = image->pagetable", reset_pos)
    if min(validate_pos, commit_pos, reset_pos, publish_pos) < 0 or not (
        validate_pos < commit_pos < reset_pos < publish_pos
    ):
        raise ValueError("exec synchronization reset is outside publication order")
    semaphore_down = source_section(sync, "int semaphore_down(",
                                    "struct condvar *condvar_create")
    cond_wait = source_section(sync, "int cond_wait(", "\n}")
    for body, action, sleep, name in (
        (semaphore_down, "s->count--;",
         "wait_queue_sleep_irq(&s->waiters)", "semaphore"),
        (cond_wait, "mutex_unlock(m)",
         "wait_queue_sleep_irq(&cond->waiters)", "condition"),
    ):
        guard_pos = body.find("enabled = intr_save();")
        action_pos = body.find(action)
        sleep_pos = body.find(sleep)
        if min(guard_pos, action_pos, sleep_pos) < 0 or not (
            guard_pos < action_pos < sleep_pos
        ):
            raise ValueError(f"{name} state change and enqueue are not atomic")
    if ("wait_result == WAIT_QUEUE_WOKEN_INTERRUPTED" not in semaphore_down or
            "wait_queue_wake_one(&s->waiters)" not in semaphore_down or
            "WAIT_QUEUE_WOKEN_INTERRUPTED" not in
            (ROOT / "os/wait.c").read_text(encoding="utf-8") +
            (ROOT / "os/wait.h").read_text(encoding="utf-8")):
        raise ValueError("woken semaphore grant is not handed onward")


def validate_user_spawn_lock(stdio: str, user_syscall: str, runner: str) -> None:
    for token in (
        "int __stdio_process_spawn_prepare(void)",
        "int __stdio_process_spawn_finish(int locked, int result)",
        "return __stdio_process_spawn_finish(locked, result);",
    ):
        if token not in stdio + user_syscall:
            raise ValueError(f"missing libc process-spawn lock mechanism {token}")
    prepare = source_section(
        stdio, "int __stdio_process_spawn_prepare(void)", "\n}"
    )
    finish = source_section(
        stdio, "int __stdio_process_spawn_finish(int locked, int result)", "\n}"
    )
    if "mutex_lock(buffer_lock)" not in prepare:
        raise ValueError("libc spawn prepare does not stabilize stdout")
    for token in (
        "if (result == 0)",
        "buffer_lock = -1;",
        "buffer_lock_enabled = 0;",
        "mutex_unlock(buffer_lock)",
    ):
        if token not in finish:
            raise ValueError(f"libc spawn finish lost {token}")
    wrappers = (
        ("int fork()", "\nvoid exit"),
        ("int agent_create(void)", "\nint agent_create_role"),
        ("int agent_create_role(int role)", "\nint agent_workflow_create"),
        ("int agent_workflow_create(int role)", "\nint agent_workflow_close"),
        ("int agent_worker_create(", "\nint agent_info"),
    )
    for begin, end in wrappers:
        body = source_section(user_syscall, begin, end)
        if ("__stdio_process_spawn_prepare()" not in body or
                "process_spawn_finish(" not in body):
            raise ValueError(f"fork-like wrapper bypasses libc hooks: {begin}")
    if ("iobudget_ucore: lineage_rate_accounting=1 immutable_owner=1" not in
            runner or "Unexpected mutex id" not in runner):
        raise ValueError("iobudget does not reject stale child stdio locks")


validate(SYNC, PROC)
validate_user_spawn_lock(STDIO, USER_SYSCALL, RUNNER)
mutated_proc = PROC.replace("sync_proc_exec_reset_locked(p, &p->threads[0]);", "", 1)
try:
    validate(SYNC, mutated_proc)
except ValueError:
    pass
else:
    raise SystemExit("mutation survived: exec synchronization reset removed")
mutated_sync = SYNC.replace(
    "enabled = intr_save();\n\tif (proc_thread_exit_requested())",
    "enabled = 0;\n\tif (proc_thread_exit_requested())",
    1,
)
try:
    validate(mutated_sync, PROC)
except ValueError:
    pass
else:
    raise SystemExit("mutation survived: semaphore enqueue guard removed")
cond_original = source_section(SYNC, "int cond_wait(", "\n}")
cond_mutated = cond_original.replace("enabled = intr_save();", "enabled = 0;", 1)
mutated_sync = SYNC.replace(cond_original, cond_mutated, 1)
try:
    validate(mutated_sync, PROC)
except ValueError:
    pass
else:
    raise SystemExit("mutation survived: condition enqueue guard removed")
mutated_sync = SYNC.replace(
    "wait_result == WAIT_QUEUE_WOKEN_INTERRUPTED",
    "s->count >= 0",
    1,
)
try:
    validate(mutated_sync, PROC)
except ValueError:
    pass
else:
    raise SystemExit("mutation survived: semaphore grant outcome conflated")

mutated_stdio = STDIO.replace("\t\tbuffer_lock_enabled = 0;", "", 1)
try:
    validate_user_spawn_lock(mutated_stdio, USER_SYSCALL, RUNNER)
except ValueError:
    pass
else:
    raise SystemExit("mutation survived: fork child retained its stdio lock")
mutated_user_syscall = USER_SYSCALL.replace(
    "return process_spawn_finish(locked, syscall(SYS_clone));",
    "return syscall(SYS_clone);",
    1,
)
try:
    validate_user_spawn_lock(STDIO, mutated_user_syscall, RUNNER)
except ValueError:
    pass
else:
    raise SystemExit("mutation survived: fork bypassed libc spawn hooks")

markers = (
    ("owner_slot_reuse=%d generation_safe=1",
     "owner_slot_reuse=16 generation_safe=1"),
    ("process_exit_multilock=1 baton_revoke=1 cond_sem_interrupt_refund=1",) * 2,
    ("exec_sync_reset=1 stale_ids_rejected=1",) * 2,
    ("atomic_wait_publication=%d cond=1 semaphore=1 count_stable=1",
     "atomic_wait_publication=512 cond=1 semaphore=1 count_stable=1"),
)
for guest_marker, runner_marker in markers:
    if guest_marker not in GUEST or runner_marker not in RUNNER:
        raise SystemExit(f"missing dynamic synchronization marker {runner_marker}")

print("[sync-owner] static wiring passed")
