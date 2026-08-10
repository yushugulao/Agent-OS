#!/usr/bin/env python3
"""谓词复查与等待队列发布的变异防护。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IPC = (ROOT / "os/agent_ipc.c").read_text(encoding="utf-8")
PROC = (ROOT / "os/proc.c").read_text(encoding="utf-8")
WAIT_TEST = (ROOT / "os/wait_atomic_test.c").read_text(encoding="utf-8")
WAIT_HEADER = (ROOT / "os/wait_atomic_test.h").read_text(encoding="utf-8")
WAIT_QUEUE = (ROOT / "os/wait.c").read_text(encoding="utf-8")
WAIT_QUEUE_HEADER = (ROOT / "os/wait.h").read_text(encoding="utf-8")
CORE = (ROOT / "os/agent_core.c").read_text(encoding="utf-8")
IDENTITY = (ROOT / "os/agent_identity.c").read_text(encoding="utf-8")
SYSCALL = (ROOT / "os/syscall.c").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
GUEST = (ROOT / "user/src/agentfinal_ucore.c").read_text(encoding="utf-8")
RUNNER = (ROOT / "scripts/run-agent-tests.sh").read_text(encoding="utf-8")
LOG_VALIDATOR = (ROOT / "scripts/validate-kernel-test-log.py").read_text(
    encoding="utf-8"
)

MARKER = (
    "agentfinal_ucore: wait_publication_atomic=1 event_wake_none=1 "
    "event_no_sleep=1 sibling_wake_none=1 teardown_completed=1"
)
DEADLINE_MARKER = (
    "agentfinal_ucore: thread_wait_deadlines finite_infinite=1 "
    "distinct_deadlines=1 keyed_timer=1 loop_aggregate=1 slot_reuse=1"
)
HANDOFF_MARKER = (
    "agentfinal_ucore: event_wake_handoff waiters=1,4,8,15 "
    "wakeups=28 herd=0"
)
BATON_MARKER = (
    "agentfinal_ucore: event_baton_identity timeline_waiter=1 "
    "event_waiter=1 event_wakeups=1"
)


def function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for offset in range(brace, len(source)):
        if source[offset] == "{":
            depth += 1
        elif source[offset] == "}":
            depth -= 1
            if depth == 0:
                return source[start : offset + 1]
    raise ValueError(f"unterminated function: {signature}")


def has_top_level_token(source: str, start: int, end: int, token: str) -> bool:
    depth = source[:start].count("{") - source[:start].count("}")
    base_depth = depth
    offset = start
    while offset < end:
        if source.startswith(token, offset) and depth == base_depth:
            return True
        if source[offset] == "{":
            depth += 1
        elif source[offset] == "}":
            depth -= 1
        offset += 1
    return False


def mutate_function(source: str, signature: str, old: str, new: str) -> str:
    body = function_body(source, signature)
    if body.count(old) != 1:
        raise AssertionError(f"mutation anchor drift: {old}")
    return source.replace(body, body.replace(old, new, 1), 1)


def validate_wait_pointer_api(wait_source: str, wait_header: str) -> None:
    for forbidden in (
        "struct wait_queue_wake_identity",
        "wait_queue_wake_one_identity",
    ):
        if forbidden in wait_header or forbidden in wait_source:
            raise ValueError(f"obsolete copied wake identity remains: {forbidden}")
    if "struct thread *wait_queue_wake_one_thread(struct wait_queue *)" not in wait_header:
        raise ValueError("wake-one pointer API declaration missing")
    wake = function_body(wait_source, "struct thread *wait_queue_wake_one_thread(")
    if "wait_queue_wake(q, 0, 0, 1, &woken)" not in wake or "return woken;" not in wake:
        raise ValueError("wake-one pointer is not returned from the atomic dequeue")


def validate(ipc: str, proc: str, wait_test: str) -> None:
    inject = function_body(wait_test, "wait_atomic_test_agent_wait(")
    inject_guard = inject.index("enabled = intr_save()")
    empty = inject.index("p->agent_event_waiters.head == 0", inject_guard)
    armed = inject.index("wait_atomic_test_begin(", empty)
    publish = inject.index("agent_ipc_wait_test_publish(p)", armed)
    complete = inject.index("wait_atomic_test_complete(", publish)
    inject_restore = inject.index("intr_restore(enabled);", complete)
    if not inject_guard < empty < armed < publish < complete < inject_restore:
        raise ValueError("agent wait injection receipt is not source-backed")
    publisher = function_body(ipc, "agent_ipc_queue_intrinsic_timer_locked(")
    if "agent_ipc_queue_event_locked(" not in publisher or (
        "AGENT_EVENT_INTRINSIC_COALESCED" not in publisher
    ):
        raise ValueError("agent wait profile publisher is not a narrow IPC callback")
    profile_publisher = function_body(ipc, "agent_ipc_wait_test_publish(")
    if "agent_ipc_queue_intrinsic_timer_locked" not in profile_publisher or (
        '"wait=atomic-injected"' not in profile_publisher
    ):
        raise ValueError("profile publisher escaped the narrow timer boundary")
    queue = function_body(ipc, "agent_ipc_queue_event_locked(")
    queue_publish = queue.index("target->agent_event_count_queued++;")
    queue_wake = queue.index("agent_ipc_handoff_event_locked(target);", queue_publish)
    if queue_wake < queue_publish:
        raise ValueError("agent event publication does not execute wake")

    consumable = function_body(ipc, "agent_ipc_event_consumable_locked(")
    consumable_lock = consumable.index("if (intr_get())")
    consumable_reserved = consumable.index("AGENT_EVENT_ACCOUNT_RESERVED")
    consumable_reserved_return = consumable.index("return 0;", consumable_reserved)
    consumable_cancel = consumable.index(
        "p->agent_wait_cancel_pending != 0", consumable_reserved_return
    )
    consumable_pending = consumable.index(
        "p->agent_wait_cancel_pending == AGENT_WAIT_CANCEL_PENDING",
        consumable_cancel,
    )
    consumable_queue = consumable.index(
        "p->agent_event_count_queued > 0", consumable_pending
    )
    if not (
        consumable_lock
        < consumable_reserved
        < consumable_reserved_return
        < consumable_cancel
        < consumable_pending
        < consumable_queue
    ):
        raise ValueError("event handoff predicate lost reservation ordering")

    if "agent_ipc_event_wake_inflight_locked" in ipc:
        raise ValueError("event baton returned to WAITING-state inference")
    if "agent_ipc_event_batons[NPROC]" in ipc:
        raise ValueError("event baton returned to a process-sized side table")
    owner_lookup = function_body(ipc, "agent_ipc_event_baton_owner_locked(")
    for token in (
        "for (int tid = 0; tid < NTHREAD; tid++)",
        "if (!t->agent_event_baton)",
        "t->process != p",
        "t->tid != tid",
        "t->identity_generation == 0",
        "panic(\"Agent event baton duplicate\")",
    ):
        if token not in owner_lookup:
            raise ValueError(f"explicit event baton identity missing: {token}")
    for forbidden in (
        "agent_loop_state",
        "wait_channel",
        "wait_interrupted",
        "RUNNABLE",
        "RUNNING",
    ):
        if forbidden in owner_lookup:
            raise ValueError("event baton identity depends on inferred wait state")

    handoff = function_body(ipc, "static int\nagent_ipc_handoff_event_locked(")
    empty_fast = handoff.index("p->agent_event_waiters.head == 0")
    predicate = handoff.index("agent_ipc_event_consumable_locked(p)", empty_fast)
    active_gate = handoff.index("agent_ipc_event_baton_active_locked(p)", predicate)
    wake_one = handoff.index(
        "wait_queue_wake_one_thread(&p->agent_event_waiters)",
        active_gate,
    )
    selected_identity = handoff.index(
        "selected->identity_generation == 0", wake_one
    )
    selected_owner = handoff.index(
        "selected != &p->threads[selected->tid]", selected_identity
    )
    selected_baton = handoff.index("selected->agent_event_baton = 1", selected_owner)
    account = handoff.index("p->agent_wait_wakeup_count += woken", selected_baton)
    if not (
        empty_fast < predicate < active_gate < wake_one < selected_identity
        < selected_owner < selected_baton < account
    ):
        raise ValueError("event wake-one handoff or accounting order changed")
    if (
        "wait_queue_wake_all" in handoff
        or "wait_queue_wake_one(&p->agent_event_waiters)" in handoff
        or "agent_wait_wakeup_count++" in handoff
    ):
        raise ValueError("event handoff returned to broadcast or call accounting")

    broadcast = function_body(ipc, "agent_ipc_broadcast_event_teardown_locked(")
    broadcast_clear = broadcast.index("agent_ipc_event_baton_clear_locked(p)")
    broadcast_wake = broadcast.index(
        "wait_queue_wake_all(&p->agent_event_waiters)", broadcast_clear
    )
    broadcast_account = broadcast.index(
        "p->agent_wait_wakeup_count += woken", broadcast_wake
    )
    if not broadcast_clear < broadcast_wake < broadcast_account:
        raise ValueError("teardown broadcast does not account actual wakes")

    reserve = function_body(ipc, "agent_ipc_wait_reserve_locked(")
    owner = reserve.index("agent_ipc_event_baton_owned_locked(p, t)")
    active_gate = reserve.index("agent_ipc_event_baton_active_locked(p)", owner)
    owner_gate = reserve.index("!owns_baton", active_gate)
    owner_return = reserve.index("return 0;", owner_gate)
    head_count = reserve.index("p->agent_event_count_queued > 0", owner_return)
    head_reserved = reserve.index(
        "cold->event_accounting[p->agent_event_head] &", head_count
    )
    head_marker = reserve.index(
        "AGENT_EVENT_ACCOUNT_RESERVED) != 0", head_reserved
    )
    head_return = reserve.index("return 0;", head_marker)
    cancel = reserve.index("p->agent_wait_cancel_pending != 0", head_return)
    cancel_reserved = reserve.index(
        "p->agent_wait_cancel_pending == AGENT_WAIT_CANCEL_RESERVED", cancel
    )
    cancel_return = reserve.index("return 0;", cancel_reserved)
    cancel_span = reserve.index(
        "reservation->span_owner = p->agent_wait_cancel_span_owner", cancel_return
    )
    cancel_source = reserve.index(
        "reservation->source_control = p->agent_wait_cancel_source_control",
        cancel_span,
    )
    cancel_principal = reserve.index(
        "p->agent_wait_cancel_audit_principal", cancel_source
    )
    cancel_mark = reserve.index(
        "p->agent_wait_cancel_pending = AGENT_WAIT_CANCEL_RESERVED",
        cancel_principal,
    )
    cancel_slot = reserve.index("reservation->slot = AGENT_WAIT_CANCEL_SLOT", cancel_mark)
    cancel_cookie = reserve.index(
        "reservation->cookie = event->event_id", cancel_slot
    )
    cancel_baton = reserve.index(
        "agent_ipc_event_baton_release_locked(p, t)", cancel_cookie
    )
    queue_empty = reserve.index("p->agent_event_count_queued <= 0", cancel_baton)
    event_valid = reserve.index("cold->events[slot].event_id == 0", queue_empty)
    event_copy = reserve.index("*event = cold->events[slot]", event_valid)
    event_span = reserve.index(
        "reservation->span_owner = cold->event_span_owner[slot]", event_copy
    )
    event_source = reserve.index(
        "reservation->source_control = cold->event_source_control[slot]",
        event_span,
    )
    event_principal = reserve.index(
        "reservation->audit_principal = cold->event_audit_principal[slot]",
        event_source,
    )
    queue_mark = reserve.index(
        "cold->event_accounting[slot] |= AGENT_EVENT_ACCOUNT_RESERVED",
        event_principal,
    )
    event_slot = reserve.index("reservation->slot = slot", queue_mark)
    event_cookie = reserve.index(
        "reservation->cookie = event->event_id", event_slot
    )
    event_baton = reserve.index(
        "agent_ipc_event_baton_release_locked(p, t)", event_cookie
    )
    if not (
        owner
        < active_gate
        < owner_gate
        < owner_return
        < head_count
        < head_reserved
        < head_marker
        < head_return
        < cancel
        < cancel_reserved
        < cancel_return
        < cancel_span
        < cancel_source
        < cancel_principal
        < cancel_mark
        < cancel_slot
        < cancel_cookie
        < cancel_baton
        < queue_empty
        < event_valid
        < event_copy
        < event_span
        < event_source
        < event_principal
        < queue_mark
        < event_slot
        < event_cookie
        < event_baton
    ):
        raise ValueError("wait reservation lost exclusion, priority, or cookie")
    for forbidden in (
        "agent_event_count_queued--;",
        "agent_external_event_count_queued--;",
        "agent_ipc_count_queued--;",
        "agent_attributed_event_count_queued--;",
        "agent_ipc_event_account_refund(",
    ):
        if forbidden in reserve:
            raise ValueError("wait reservation refunded queue accounting")

    refund = function_body(ipc, "agent_ipc_event_account_refund(")
    refund_kind = refund.index("(accounting & kind) == 0")
    refund_guard = refund.index("*count <= 0", refund_kind)
    refund_commit = refund.index("(*count)--;", refund_guard)
    if not refund_kind < refund_guard < refund_commit:
        raise ValueError("event accounting refund lost its checked decrement")

    finish = function_body(ipc, "agent_ipc_wait_finish(")
    slot = finish.index("int slot = reservation->slot")
    cancel_branch = finish.index("slot == AGENT_WAIT_CANCEL_SLOT", slot)
    cancel_state = finish.index(
        "p->agent_wait_cancel_pending != AGENT_WAIT_CANCEL_RESERVED",
        cancel_branch,
    )
    cancel_cookie = finish.index(
        "p->agent_wait_cancel_event_id != reservation->cookie", cancel_state
    )
    event_bounds = finish.index("slot < 0 || slot >= AGENT_EVENT_QUEUE_CAP", cancel_cookie)
    event_count = finish.index("p->agent_event_count_queued <= 0", event_bounds)
    event_head = finish.index("p->agent_event_head != slot", event_count)
    event_cookie = finish.index(
        "cold->events[slot].event_id != reservation->cookie", event_head
    )
    event_reserved = finish.index("AGENT_EVENT_ACCOUNT_RESERVED) == 0", event_cookie)
    abort = finish.index("if (!commit)", event_reserved)
    cancel_abort = finish.index(
        "p->agent_wait_cancel_pending = AGENT_WAIT_CANCEL_PENDING", abort
    )
    event_abort = finish.index(
        "~AGENT_EVENT_ACCOUNT_RESERVED", cancel_abort
    )
    abort_wake = finish.index("agent_ipc_handoff_event_locked(p);", event_abort)
    cancel_commit = finish.index("p->agent_wait_cancel_pending = 0", abort_wake)
    queue_commit = finish.index("p->agent_event_count_queued--;", cancel_commit)
    queue_refund = finish.index(
        "&p->agent_external_event_count_queued", queue_commit
    )
    ipc_refund = finish.index("&p->agent_ipc_count_queued", queue_refund)
    attributed_refund = finish.index(
        "&p->agent_attributed_event_count_queued", ipc_refund
    )
    final_wake = finish.index("agent_ipc_handoff_event_locked(p);", attributed_refund)
    if not (
        slot
        < cancel_branch
        < cancel_state
        < cancel_cookie
        < event_bounds
        < event_count
        < event_head
        < event_cookie
        < event_reserved
        < abort
        < cancel_abort
        < event_abort
        < abort_wake
        < cancel_commit
        < queue_commit
        < queue_refund
        < ipc_refund
        < attributed_refund
        < final_wake
    ):
        raise ValueError("wait finish is not exact-cookie abort-or-commit")
    abort_region = finish[abort:cancel_commit]
    for forbidden in (
        "agent_event_count_queued--;",
        "agent_ipc_event_account_refund(",
        "p->agent_wait_cancel_pending = 0",
    ):
        if forbidden in abort_region:
            raise ValueError("wait abort consumes or refunds its reservation")
    if finish.count("agent_ipc_event_account_refund(") != 3 or (
        finish.count("agent_ipc_handoff_event_locked(p);") != 2
    ):
        raise ValueError("wait abort/commit cannot hand off the retained queue")

    wait = function_body(ipc, "int sys_agent_wait(")
    loop = wait.index("for (;;)")
    injection = wait.index("wait_atomic_test_agent_wait(", loop)
    guard = wait.index("\n\t\tenabled = intr_save();", loop)
    baton_owner = wait.index(
        "owns_baton = agent_ipc_event_baton_owned_locked(p, t)", guard
    )
    reservation = wait.index("agent_ipc_wait_reserve_locked(p, t,", baton_owner)
    cancel_status = wait.index(
        "reservation.slot == AGENT_WAIT_CANCEL_SLOT", reservation
    )
    recheck_empty = wait.index(
        "owns_baton && status == AGENT_STATUS_NOT_FOUND", cancel_status
    )
    recheck_release = wait.index(
        "agent_ipc_event_baton_release_locked(p, t)", recheck_empty
    )
    recheck_handoff = wait.index(
        "agent_ipc_handoff_event_locked(p);", recheck_release
    )
    heartbeat = wait.index("agent_ipc_queue_heartbeat_if_due", cancel_status)
    timeout = wait.index("if (timeout_ticks >= 0", heartbeat)
    waiting = wait.index("t->agent_loop_state = AGENT_LOOP_WAITING", timeout)
    sleep = wait.index("wait_queue_sleep_key_irq(", waiting)
    key = wait.index("t->identity_generation", sleep)
    direct_recheck = wait.index("goto recheck_locked;", sleep)
    failed_wake = wait.index("if (wait_status != WAIT_QUEUE_OK)", direct_recheck)
    failed_release = wait.index(
        "agent_ipc_event_baton_release_locked(p, t)", failed_wake
    )
    failed_live = wait.index("proc_teardown_live(p)", failed_release)
    failed_handoff = wait.index(
        "agent_ipc_handoff_event_locked(p);", failed_live
    )
    if not (
        injection
        < guard
        < baton_owner
        < reservation
        < cancel_status
        < recheck_empty
        < recheck_release
        < recheck_handoff
        < heartbeat
        < timeout
        < waiting
        < sleep
        < direct_recheck
        < failed_wake
        < failed_release
        < failed_live
        < failed_handoff
    ):
        raise ValueError("agent wait final predicate order is not atomic")
    if key < sleep:
        raise ValueError("agent wait is not keyed by thread generation")
    if has_top_level_token(wait, guard, sleep, "intr_restore(enabled);"):
        raise ValueError("agent wait reopens interrupts before queue publication")
    if "wait_queue_sleep(&p->agent_event_waiters)" in wait:
        raise ValueError("agent wait uses an unlocked queue publication API")
    if "t->agent_loop_state = AGENT_LOOP_RUNNING;" in wait[sleep:direct_recheck]:
        raise ValueError("selected event waiter drops its handoff before recheck")

    ipc_teardown = function_body(ipc, "agent_ipc_proc_teardown(")
    teardown_guard = ipc_teardown.index("enabled = intr_save()")
    teardown_broadcast = ipc_teardown.index(
        "agent_ipc_broadcast_event_teardown_locked(p)", teardown_guard
    )
    teardown_reset = ipc_teardown.index("agent_ipc_proc_reset(p)", teardown_broadcast)
    teardown_restore = ipc_teardown.index("intr_restore(enabled)", teardown_reset)
    if not teardown_guard < teardown_broadcast < teardown_reset < teardown_restore:
        raise ValueError("Agent teardown lost IRQ-atomic event broadcast")

    lane = wait.index("agent_lifecycle_context_lane_enter(p)", sleep)
    lane_abort = wait.index("agent_ipc_wait_finish(p, &reservation, 0)", lane)
    provenance = wait.index("agent_provenance_merge_current(", lane_abort)
    provenance_labels = wait.index(
        "p, reservation.provenance_labels", provenance
    )
    provenance_abort = wait.index(
        "agent_ipc_wait_finish(p, &reservation, 0)", lane_abort + 1
    )
    provenance_leave = wait.index(
        "agent_lifecycle_context_lane_leave(p)", provenance_abort
    )
    copyout = wait.index("copyout(p->pagetable, eventaddr", provenance_leave)
    copyout_abort = wait.index(
        "agent_ipc_wait_finish(p, &reservation, 0)", provenance_abort + 1
    )
    copyout_leave = wait.index(
        "agent_lifecycle_context_lane_leave(p)", copyout_abort
    )
    span_guard = wait.index(
        "event.span_id != 0 && reservation.span_owner != 0", copyout_leave
    )
    span_owner = wait.index(
        "p->agent_current_span_owner = reservation.span_owner", span_guard
    )
    source_control = wait.index(
        "p->agent_current_cause_control = reservation.source_control != 0",
        span_owner,
    )
    source_control_value = wait.index(
        "reservation.source_control :", source_control
    )
    audit = wait.index("agent_observe_record_event(", copyout_abort)
    audit_private = wait.index(
        "reservation.span_owner, reservation.audit_principal", audit
    )
    context = wait.index("agent_context_append_system_causal(", audit)
    commit = wait.index("agent_ipc_wait_finish(p, &reservation, 1)", context)
    leave = wait.index("agent_lifecycle_context_lane_leave(p)", commit)
    if not (
        lane
        < lane_abort
        < provenance
        < provenance_labels
        < provenance_abort
        < provenance_leave
        < copyout
        < copyout_abort
        < copyout_leave
        < span_guard
        < span_owner
        < source_control
        < source_control_value
        < audit
        < audit_private
        < context
        < commit
        < leave
    ):
        raise ValueError("wait reserve/copyout/context commit order changed")
    if wait.count("agent_ipc_wait_finish(p, &reservation, 0)") != 3 or (
        wait.count("agent_ipc_wait_finish(p, &reservation, 1)") != 1
    ):
        raise ValueError("wait reservation lacks exact abort/commit exits")

    teardown = function_body(proc, "void exit(int code)")
    loop = teardown.index("for (;;)", teardown.index("mutex_release_thread_locks"))
    interrupt = teardown.index("proc_interrupt_siblings(p, t);", loop)
    test_guard = teardown.index("test_enabled = intr_save();", interrupt)
    test_empty = teardown.index("p->thread_exit_waiters.head == 0", test_guard)
    injection = teardown.index("wait_atomic_test_begin(", test_empty)
    test_quiescent = teardown.index(
        "while (!proc_siblings_quiescent(p, t))", injection
    )
    test_yield = teardown.index("yield();", test_quiescent)
    test_complete = teardown.index("wait_atomic_test_complete(", test_yield)
    guard = teardown.index("\n\t\tenabled = intr_save();", test_complete)
    final = teardown.index("if (proc_siblings_quiescent(p, t))", guard)
    sleep = teardown.index("wait_queue_sleep_irq(&p->thread_exit_waiters)", final)
    if not (
        loop
        < interrupt
        < test_guard
        < test_empty
        < injection
        < test_quiescent
        < test_yield
        < test_complete
        < guard
        < final
        < sleep
    ):
        raise ValueError("teardown final sibling check is outside atomic wait")
    between = teardown[final:sleep]
    close = between.find("intr_restore(enabled);")
    branch = between.find("break;")
    if close < 0 or branch < 0 or close > branch:
        raise ValueError("teardown quiescent branch does not restore interrupts")
    if "wait_queue_sleep(&p->thread_exit_waiters)" in teardown:
        raise ValueError("teardown uses an unlocked queue publication API")
    if has_top_level_token(teardown, guard, sleep, "intr_restore(enabled);"):
        raise ValueError("teardown reopens interrupts before queue publication")

    finish = function_body(proc, "static void scheduler_finish_dying_thread(")
    exited = finish.index("t->state = EXITED;")
    keyed = finish.index(
        "wait_queue_wake_key_all(&p->thread_exit_waiters,",
        exited,
    )
    zero = finish.index(
        "wait_queue_wake_key_all(&p->thread_exit_waiters, 0);",
        keyed,
    )
    if not exited < keyed < zero:
        raise ValueError("last sibling does not publish EXITED before wake")


def validate_image_install_transition(ipc: str, proc: str) -> None:
    validator = function_body(proc, "proc_image_install_state_valid_locked(")
    compact = "".join(validator.split())
    for token in (
        "if(intr_get()||main->process!=p||main->on_run_queue)",
        "if(mode==PROC_IMAGE_INSTALL_LIVE_EXEC)",
        "main!=curr_thread()||main->state!=RUNNING||main->tid!=0||",
        "main->identity_generation==0",
        "elseif(mode==PROC_IMAGE_INSTALL_BOOTSTRAP)",
        "main==curr_thread()||main->state!=T_UNUSED||main->tid!=-1||",
        "main->identity_generation!=0",
        "for(inttid=1;tid<NTHREAD;tid++)",
        "t->process!=p||t->on_run_queue",
        "t->state!=T_UNUSED&&t->state!=EXITED",
        "t->state!=T_UNUSED||t->tid!=-1||t->identity_generation!=0",
    ):
        if token not in compact:
            raise ValueError(f"image install state validation missing: {token}")
    if compact.count("mode==PROC_IMAGE_INSTALL_LIVE_EXEC") != 2 or (
        compact.count("mode==PROC_IMAGE_INSTALL_BOOTSTRAP") != 1
    ):
        raise ValueError("image install modes no longer select exact state contracts")

    transition = function_body(ipc, "agent_ipc_thread_runtime_transition(")
    for token in (
        "AGENT_THREAD_RUNTIME_ACTIVATE",
        "AGENT_THREAD_RUNTIME_RELEASE",
        "t->identity_generation == 0",
    ):
        if token not in transition:
            raise ValueError(f"thread runtime transition contract missing: {token}")
    for forbidden in (
        "AGENT_THREAD_RUNTIME_EXEC_RESET",
        "AGENT_THREAD_RUNTIME_IMAGE_INSTALL",
    ):
        if forbidden in transition or forbidden in proc:
            raise ValueError("image installation returned to the thread transition API")

    reset = function_body(ipc, "agent_ipc_process_image_install_locked(")
    reset_guard = reset.index("intr_get()")
    reset_scan = reset.index("for (int tid = 0; tid < NTHREAD; tid++)", reset_guard)
    reset_clear = reset.index("agent_ipc_thread_state_clear_locked(", reset_scan)
    reset_refresh = reset.index("agent_identity_loop_refresh_locked(p);", reset_clear)
    if not reset_guard < reset_scan < reset_clear < reset_refresh:
        raise ValueError("locked process image reset lost its full thread settlement")
    if "intr_save()" in reset:
        raise ValueError("locked process image reset acquired a second publication guard")

    install = function_body(proc, "int proc_install_user_image(")
    for token in (
        "mode != PROC_IMAGE_INSTALL_BOOTSTRAP",
        "mode != PROC_IMAGE_INSTALL_LIVE_EXEC",
        "live_exec = mode == PROC_IMAGE_INSTALL_LIVE_EXEC",
    ):
        if token not in install:
            raise ValueError(f"image install mode gate missing: {token}")
    guard = install.index("enabled = intr_save();")
    state = install.index("proc_image_install_state_valid_locked(p, mode)", guard)
    sync_validate = install.index("sync_proc_exec_validate_locked(", state)
    vfs_validate = install.index("vfs_proc_exec_validate_locked(", sync_validate)
    reset_call = install.index("agent_process_image_install_locked(p);", vfs_validate)
    sync_reset = install.index("sync_proc_exec_reset_locked(", reset_call)
    publish = install.index("p->pagetable = image->pagetable;", sync_reset)
    restore = install.index("intr_restore(enabled);", publish)
    if not (
        guard
        < state
        < sync_validate
        < vfs_validate
        < reset_call
        < sync_reset
        < publish
        < restore
    ):
        raise ValueError("image install validation/reset left its publication boundary")
    commits = [
        offset
        for offset in range(len(install))
        if install.startswith("vfs_proc_exec_commit(p, &transition)", offset)
    ]
    if len(commits) != 2 or any(offset < vfs_validate or offset > reset_call for offset in commits):
        raise ValueError("process image reset does not follow every credential commit")
    if has_top_level_token(install, guard, reset_call, "intr_restore(enabled);"):
        raise ValueError("image install reopens interrupts before process reset")


def validate_thread_deadlines(
    ipc: str, proc: str, core: str, identity: str, syscall: str
) -> None:
    if "p->agent_wait_deadline" in ipc or "target->agent_wait_deadline" in ipc:
        raise ValueError("event deadline ownership returned to proc")
    wait = function_body(ipc, "int sys_agent_wait(")
    for token in (
        "t->agent_wait_deadline_valid = 1;",
        "wait_queue_sleep_key_irq(",
        "t->identity_generation",
        "goto out;",
    ):
        if token not in wait:
            raise ValueError(f"per-thread wait contract missing: {token}")
    tick = function_body(ipc, "agent_ipc_tick_proc(")
    for token in (
        "for (int tid = 0; tid < NTHREAD; tid++)",
        "t->state != SLEEPING",
        "t->wait_channel != &p->agent_event_waiters",
        "t->wait_key != t->identity_generation",
        "wait_queue_wake_key_all(&p->agent_event_waiters,",
    ):
        if token not in tick:
            raise ValueError(f"keyed timer scan missing: {token}")
    if "p->agent_wait_wakeup_count += woken;" not in tick or (
        "p->agent_wait_wakeup_count++;" in tick
    ):
        raise ValueError("keyed timer does not account actual woken threads")
    if "agent_wait_deadline_valid = 0" in tick:
        raise ValueError("timer consumed thread-owned deadline")
    cancel = function_body(ipc, "int sys_agent_wait_cancel(")
    if "agent_wait_deadline" in cancel:
        raise ValueError("cancel producer clears waiter deadline")
    for token in ("AGENT_THREAD_RUNTIME_ACTIVATE", "AGENT_THREAD_RUNTIME_RELEASE"):
        if token not in proc and token not in syscall:
            raise ValueError(f"thread lifecycle settlement missing: {token}")
    validate_image_install_transition(ipc, proc)
    if "agent_ipc_thread_sched_snapshot(t, &ipc);" not in core or (
        "p->agent_wait_deadline" in core
    ):
        raise ValueError("scheduler still consumes proc deadline")
    refresh = function_body(identity, "agent_identity_loop_refresh_locked(")
    if not (
        refresh.index("running ? AGENT_LOOP_RUNNING")
        < refresh.index("waiting ? AGENT_LOOP_WAITING")
        < refresh.index("idle ? AGENT_LOOP_IDLE")
    ):
        raise ValueError("loop aggregate priority changed")


def validate_watch_syscalls(ipc: str, core: str) -> None:
    helper = function_body(ipc, "agent_ipc_watch_update(")
    ordered = (
        "if (!p->is_agent)",
        "agent_identity_has_cap(p, AGENT_CAP_WATCH)",
        "event_type < AGENT_EVENT_NONE",
        "copyinstr(p->pagetable, filter, filteraddr, sizeof(filter))",
        "agent_lifecycle_context_lane_enter(p)",
        "agent_ipc_watch_set(p, event_type, filter)",
        "agent_ipc_watch_clear(p, event_type, filter)",
        "agent_identity_thread_loop_set(curr_thread(), AGENT_LOOP_IDLE)",
        "agent_context_append_system(",
        "agent_lifecycle_context_lane_leave(p)",
        "return result;",
    )
    positions = [helper.index(token) for token in ordered]
    if positions != sorted(positions):
        raise ValueError("watch syscall validation or context-lane order changed")
    compact = "".join(helper.split())
    for token in (
        "charfilter[AGENT_WATCH_FILTER_SIZE]={0};",
        "returnAGENT_STATUS_DENIED;",
        "event_type>AGENT_EVENT_MAX",
        "returnAGENT_STATUS_BAD_PARAM;",
        "result=AGENT_STATUS_NO_SPACE;gotoout;",
        'filter,remove?"unwatch":"watch",AGENT_STATUS_OK,remove?result:event_type,0,0);',
    ):
        if token not in compact:
            raise ValueError(f"watch syscall contract missing: {token}")
    watch = "".join(function_body(ipc, "int sys_agent_watch(").split())
    unwatch = "".join(function_body(ipc, "int sys_agent_unwatch(").split())
    if "agent_ipc_watch_update(event_type,filteraddr,0)" not in watch or (
        "agent_ipc_watch_update(event_type,filteraddr,1)" not in unwatch
    ):
        raise ValueError("watch syscall wrappers select the wrong operation")
    if "agent_ipc_watch_set(p, op->arg0, op->payload)" not in core or (
        "agent_ipc_watch_update(" in core
    ):
        raise ValueError("batch watch ABI was coupled to syscall preparation")


def require_profile_guard(source: str, token: str, label: str) -> None:
    if source.count(token) != 1:
        raise ValueError(f"{label} must have one owner")
    position = source.index(token)
    guard = source.rfind("#ifdef WAIT_ATOMIC_TEST_PROFILE", 0, position)
    close = source.find("#endif", guard)
    if guard < 0 or close < position:
        raise ValueError(f"{label} escaped WAIT_ATOMIC_TEST_PROFILE")


def remove_profile_guard(source: str, token: str) -> str:
    position = source.index(token)
    guard = source.rfind("#ifdef WAIT_ATOMIC_TEST_PROFILE", 0, position)
    close = source.find("#endif", position)
    if guard < 0 or close < 0:
        raise AssertionError(f"profile guard mutation anchor drift: {token}")
    guard_end = source.index("\n", guard) + 1
    close_end = source.index("\n", close) + 1
    return source[:guard] + source[guard_end:close] + source[close_end:]


def validate_profile_isolation(
    ipc: str,
    wait_test: str,
    wait_header: str,
    makefile: str,
) -> None:
    require_profile_guard(
        ipc, "int agent_ipc_wait_test_publish(", "wait profile IPC publisher"
    )
    require_profile_guard(
        ipc, "wait_status = wait_atomic_test_agent_wait(p);", "wait profile caller"
    )
    require_profile_guard(
        wait_test, "wait_atomic_test_agent_wait(struct proc *p)", "wait test owner"
    )
    require_profile_guard(
        wait_header, "int wait_atomic_test_agent_wait(struct proc *p);", "wait test API"
    )
    require_profile_guard(
        wait_header, "int agent_ipc_wait_test_publish(struct proc *p);", "IPC test API"
    )
    for token in (
        "CFLAGS += -MD",
        "$(C_OBJS): $(BUILDDIR)/$K/%.o : $K/%.c",
        "$(C_OBJS) $(AS_OBJS): $(KSTACK_BUILD_CONFIG)",
        "-include $(HEADER_DEP)",
    ):
        if token not in makefile:
            raise ValueError(f"profile-aware dependency generation missing: {token}")
    object_rule = next(
        line
        for line in makefile.splitlines()
        if line.startswith("$(C_OBJS): $(BUILDDIR)/$K/%.o")
    )
    if ".d" in object_rule or "$(CC) -MM" in makefile:
        raise ValueError("dependency generation is detached from the real compile")


def validate_runner_profile_image(runner: str, makefile: str) -> None:
    flags = (
        'CONTEXT_SYNC_USER_CFLAGS="-DAGENT_CONTEXT_SYNC_TEST_PROFILE '
        '-DWAIT_ATOMIC_TEST_PROFILE"'
    )
    if runner.count(flags) != 1:
        raise ValueError("Context/wait profile user flags have no single source")
    start = runner.index("build_user_image() {")
    end = runner.index("\nrun_case() {", start)
    builder = runner[start:end]
    make_image = builder.index(
        '"${MAKE_TOOL}" "${MAKE_JOB_ARGS[@]}" -rR -f Makefile nfs/fs.img'
    )
    pass_flags = builder.index(
        'USER_EXTRA_CFLAGS="${user_extra_cflags}"', make_image
    )
    if pass_flags < make_image or "make user" in builder:
        raise ValueError("profile user image is not built by one flag-preserving make")
    profile = runner.index('build_user_image "${CONTEXT_SYNC_USER_CFLAGS}"')
    profile_case = runner.index(
        'run_case agentfinal_ucore "agentfinal_ucore: parent passed" "" 1',
        profile,
    )
    normal = runner.index("\nbuild_user_image\n", profile_case)
    if not profile < profile_case < normal:
        raise ValueError("profile and production user images are not isolated")
    for token in (
        "$(F)/fs.img: user .FORCE",
        "USER_EXTRA_CFLAGS='$(USER_EXTRA_CFLAGS)'",
    ):
        if token not in makefile:
            raise ValueError(f"root user-image flag propagation missing: {token}")


def validate_deadline_guest(guest: str) -> None:
    body = function_body(guest, "static void check_thread_wait_deadlines(")
    preserved = body.index('"infinite waiter preserved"')
    watch = body.index(
        'agent_watch(AGENT_EVENT_MESSAGE, "thread-deadline-release")',
        preserved,
    )
    payload = body.index(
        'strcpy(event.payload, "thread-deadline-release")', watch
    )
    wake = body.index("check(agent_wake(getpid(), &event) == 0", payload)
    join = body.index("waittid(tids[1])", wake)
    status = body.index(
        "wait_deadline_status[1] == AGENT_STATUS_OK", join
    )
    unwatch = body.index(
        "agent_unwatch(AGENT_EVENT_MESSAGE,", status
    )
    unwatch_status = body.index(") == 1,", unwatch)
    next_phase = body.index(
        "WAIT_ATOMIC_TEST_DEADLINE_IDLE_FIRST", unwatch_status
    )
    if not (
        preserved < watch < payload < wake < join < status < unwatch
        < unwatch_status < next_phase
    ):
        raise ValueError("infinite waiter release is not explicitly routed and settled")
    if body.count('"thread-deadline-release"') != 3 or (
        '"unwatch infinite waiter release"' not in body
    ):
        raise ValueError("infinite waiter route filter or cleanup drifted")


def validate_handoff_guest(guest: str) -> None:
    worker = function_body(guest, "static void wait_handoff_worker(")
    wait = worker.index("agent_wait(&event, -1)")
    done = worker.index("wait_handoff_done[slot] = 1", wait)
    if done < wait:
        raise ValueError("handoff worker publishes completion before wait")

    timeline_worker = function_body(guest, "static void timeline_handoff_worker(")
    timeline_wait = timeline_worker.index(
        "agent_timeline_wait(&timeline_handoff_filter, -1)"
    )
    timeline_done = timeline_worker.index("timeline_handoff_done = 1", timeline_wait)
    if timeline_done < timeline_wait:
        raise ValueError("timeline waiter publishes completion before wait")

    phase = function_body(guest, "check_event_wake_handoff_phase(int count)")
    before = phase.index("check(agent_info(&wait_handoff_before) == 0")
    create = phase.index("thread_create(wait_handoff_worker", before)
    sleeping = phase.index(
        "wait_handoff_until_sleeping(wait_handoff_before.wait_sleep_count + count)",
        create,
    )
    publish = phase.index(
        "check(agent_wake(getpid(), &wait_handoff_event) == 0", sleeping
    )
    completion = phase.index("wait_handoff_until(count, i + 1)", publish)
    first = phase.index(
        "wait_handoff_before.wait_wakeup_count + 1", completion
    )
    join = phase.index("waittid(wait_handoff_tids[i])", first)
    exact = phase.index(
        "wait_handoff_before.wait_wakeup_count + count", join
    )
    if not before < create < sleeping < publish < completion < first < join < exact:
        raise ValueError("dynamic event handoff evidence order changed")

    campaign = function_body(guest, "check_event_wake_handoff(void)")
    for token in (
        "{1, 4, 8, WAIT_HANDOFF_MAX}",
        'agent_watch(AGENT_EVENT_MESSAGE, "wait-handoff")',
        "check_event_wake_handoff_phase(counts[i])",
        "check_event_timeline_waiter_isolation()",
        'agent_unwatch(AGENT_EVENT_MESSAGE, "wait-handoff")',
        HANDOFF_MARKER,
    ):
        if token not in campaign:
            raise ValueError(f"dynamic event handoff campaign missing: {token}")

    mixed = function_body(guest, "check_event_timeline_waiter_isolation(void)")
    before = mixed.index("check(agent_info(&before) == 0")
    audit_filter = mixed.index("AGENT_TIMELINE_SOURCE_MASK_AUDIT", before)
    enqueue_filter = mixed.index("AGENT_AUDIT_KIND_EVENT_ENQUEUE", audit_filter)
    target_filter = mixed.index("timeline_handoff_filter.target_pid = getpid()", enqueue_filter)
    event_filter = mixed.index("AGENT_EVENT_MESSAGE", target_filter)
    timeline_create = mixed.index("thread_create(timeline_handoff_worker", event_filter)
    event_create = mixed.index("thread_create(wait_handoff_worker", timeline_create)
    both_sleep = mixed.index("wait_handoff_until_mixed_sleeping(", event_create)
    future_tick = mixed.index("wait_handoff_until_tick(", both_sleep)
    publish = mixed.index("check(agent_wake(getpid(), &wait_handoff_event) == 0", future_tick)
    event_done = mixed.index("wait_handoff_until(1, 1)", publish)
    timeline_done = mixed.index("timeline_handoff_done", event_done)
    event_account = mixed.index(
        "after.wait_wakeup_count == before.wait_wakeup_count + 1", timeline_done
    )
    timeline_account = mixed.index(
        "after.timeline_wait_wakeup_count ==", event_account
    )
    marker = mixed.index(BATON_MARKER, timeline_account)
    if not (
        before < audit_filter < enqueue_filter < target_filter < event_filter
        < timeline_create < event_create
        < both_sleep < future_tick < publish < event_done < timeline_done
        < event_account < timeline_account < marker
    ):
        raise ValueError("timeline/event baton isolation evidence order changed")


validate_wait_pointer_api(WAIT_QUEUE, WAIT_QUEUE_HEADER)
validate(IPC, PROC, WAIT_TEST)
validate_thread_deadlines(IPC, PROC, CORE, IDENTITY, SYSCALL)
validate_watch_syscalls(IPC, CORE)
validate_profile_isolation(
    IPC,
    WAIT_TEST,
    WAIT_HEADER,
    MAKEFILE,
)
validate_runner_profile_image(RUNNER, MAKEFILE)
validate_deadline_guest(GUEST)
validate_handoff_guest(GUEST)

mutations = (
    (mutate_function(IPC, "int sys_agent_wait(", "\t\tenabled = intr_save();", ""), PROC, WAIT_TEST),
    (mutate_function(IPC, "int sys_agent_wait(", "wait_queue_sleep_key_irq(", "wait_queue_sleep_irq("), PROC, WAIT_TEST),
    (mutate_function(IPC, "int sys_agent_wait(", "\t\tp->agent_wait_loop_count++;", "\t\tintr_restore(enabled);\n\t\tp->agent_wait_loop_count++;"), PROC, WAIT_TEST),
    (IPC, mutate_function(PROC, "void exit(int code)", "#endif\n\t\tenabled = intr_save();", "#endif"), WAIT_TEST),
    (IPC, mutate_function(PROC, "void exit(int code)", "wait_queue_sleep_irq(&p->thread_exit_waiters)", "wait_queue_sleep(&p->thread_exit_waiters)"), WAIT_TEST),
    (IPC, mutate_function(PROC, "void exit(int code)", "\t\tif (proc_siblings_quiescent(p, t)) {", "\t\tintr_restore(enabled);\n\t\tif (proc_siblings_quiescent(p, t)) {"), WAIT_TEST),
    (IPC, mutate_function(PROC, "static void scheduler_finish_dying_thread(", "\t\twait_queue_wake_key_all(&p->thread_exit_waiters, 0);", ""), WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_queue_event_locked(", "\tagent_ipc_handoff_event_locked(target);", ""), PROC, WAIT_TEST),
    (mutate_function(IPC, "static int\nagent_ipc_handoff_event_locked(", "wait_queue_wake_one_thread(&p->agent_event_waiters)", "wait_queue_wake_one(&p->agent_event_waiters)"), PROC, WAIT_TEST),
    (mutate_function(IPC, "static int\nagent_ipc_handoff_event_locked(", "\tp->agent_wait_wakeup_count += woken;", "\tp->agent_wait_wakeup_count++;"), PROC, WAIT_TEST),
    (mutate_function(IPC, "static int\nagent_ipc_handoff_event_locked(", "p->agent_event_waiters.head == 0 ||\n\t    ", ""), PROC, WAIT_TEST),
    (mutate_function(IPC, "static int\nagent_ipc_handoff_event_locked(", " ||\n\t    agent_ipc_event_baton_active_locked(p)", ""), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_event_consumable_locked(", "\t\treturn 0;\n\tif (p->agent_wait_cancel_pending != 0)", "\tif (p->agent_wait_cancel_pending != 0)"), PROC, WAIT_TEST),
    (mutate_function(IPC, "int sys_agent_wait(", "\t\t\tif (agent_ipc_event_baton_release_locked(p, t) &&\n\t\t\t    proc_teardown_live(p))\n\t\t\t\tagent_ipc_handoff_event_locked(p);", ""), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_proc_teardown(", "\tagent_ipc_broadcast_event_teardown_locked(p);", ""), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_wait_reserve_locked(", "AGENT_EVENT_ACCOUNT_RESERVED) != 0", "0) != 0"), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_wait_reserve_locked(", "\t\tif (p->agent_wait_cancel_pending == AGENT_WAIT_CANCEL_RESERVED)\n\t\t\treturn 0;", "\t\tif (0)\n\t\t\treturn 0;"), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_wait_reserve_locked(", "\t\treservation->source_control = p->agent_wait_cancel_source_control;", "\t\treservation->source_control = 0;"), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_wait_reserve_locked(", "\tcold->event_accounting[slot] |= AGENT_EVENT_ACCOUNT_RESERVED;", ""), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_wait_reserve_locked(", "\treservation->audit_principal = cold->event_audit_principal[slot];", "\treservation->audit_principal = 0;"), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_wait_reserve_locked(", "\treservation->slot = slot;", "\tp->agent_event_count_queued--;\n\treservation->slot = slot;"), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_wait_finish(", "p->agent_wait_cancel_pending != AGENT_WAIT_CANCEL_RESERVED", "0"), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_wait_finish(", "\t\t    p->agent_wait_cancel_event_id != reservation->cookie)", "\t\t    0)"), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_wait_finish(", "\t\t    p->agent_event_head != slot ||\n", ""), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_wait_finish(", "\t\t    cold->events[slot].event_id != reservation->cookie ||", ""), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_wait_finish(", "\tif (!commit) {", "\tif (commit) {"), PROC, WAIT_TEST),
    (mutate_function(IPC, "agent_ipc_event_account_refund(", "\t(*count)--;", ""), PROC, WAIT_TEST),
    (mutate_function(IPC, "int sys_agent_wait(", "reservation.source_control != 0 ?", "0 ?"), PROC, WAIT_TEST),
    (mutate_function(IPC, "int sys_agent_wait(", "reservation.span_owner, reservation.audit_principal", "reservation.span_owner, 0"), PROC, WAIT_TEST),
    (mutate_function(IPC, "int sys_agent_wait(", "\t\tagent_ipc_wait_finish(p, &reservation, 1);", ""), PROC, WAIT_TEST),
    (IPC, mutate_function(PROC, "void exit(int code)", "\t\t\t\twhile (!proc_siblings_quiescent(p, t))\n\t\t\t\t\tyield();", "\t\t\t\twhile (0 && !proc_siblings_quiescent(p, t))\n\t\t\t\t\tyield();"), WAIT_TEST),
    (IPC, PROC, mutate_function(WAIT_TEST, "wait_atomic_test_agent_wait(", "\tint enabled = intr_save(),", "\tint enabled = 0,")),
)
for mutated_ipc, mutated_proc, mutated_wait_test in mutations:
    try:
        validate(mutated_ipc, mutated_proc, mutated_wait_test)
    except (ValueError, IndexError):
        pass
    else:
        raise SystemExit("wait-publication mutation survived")

profile_mutations = (
    (
        remove_profile_guard(IPC, "int agent_ipc_wait_test_publish("),
        WAIT_TEST,
        WAIT_HEADER,
    ),
    (
        remove_profile_guard(IPC, "wait_status = wait_atomic_test_agent_wait(p);"),
        WAIT_TEST,
        WAIT_HEADER,
    ),
    (
        IPC,
        remove_profile_guard(WAIT_TEST, "wait_atomic_test_agent_wait(struct proc *p)"),
        WAIT_HEADER,
    ),
    (
        IPC,
        WAIT_TEST,
        remove_profile_guard(
            WAIT_HEADER, "int wait_atomic_test_agent_wait(struct proc *p);"
        ),
    ),
)
for mutated_ipc, mutated_wait_test, mutated_wait_header in profile_mutations:
    try:
        validate_profile_isolation(
            mutated_ipc,
            mutated_wait_test,
            mutated_wait_header,
            MAKEFILE,
        )
    except (ValueError, IndexError):
        pass
    else:
        raise SystemExit("wait profile guard mutation survived")

makefile_mutations = (
    MAKEFILE.replace("CFLAGS += -MD\n", "", 1),
    MAKEFILE.replace(
        "$(C_OBJS): $(BUILDDIR)/$K/%.o : $K/%.c",
        "$(C_OBJS): $(BUILDDIR)/$K/%.o : $K/%.c $(BUILDDIR)/$K/%.d",
        1,
    ),
    MAKEFILE + "\n\t$(CC) -MM $<\n",
    MAKEFILE.replace("$(C_OBJS) $(AS_OBJS): $(KSTACK_BUILD_CONFIG)\n", "", 1),
    MAKEFILE.replace("-include $(HEADER_DEP)\n", "", 1),
)
for mutated_makefile in makefile_mutations:
    try:
        validate_profile_isolation(
            IPC,
            WAIT_TEST,
            WAIT_HEADER,
            mutated_makefile,
        )
    except (ValueError, IndexError, StopIteration):
        pass
    else:
        raise SystemExit("profile dependency mutation survived")

runner_profile_mutations = (
    RUNNER.replace('USER_EXTRA_CFLAGS="${user_extra_cflags}"', "", 1),
    RUNNER.replace(
        'build_user_image "${CONTEXT_SYNC_USER_CFLAGS}"',
        "build_user_image",
        1,
    ),
)
for mutated_runner in runner_profile_mutations:
    try:
        validate_runner_profile_image(mutated_runner, MAKEFILE)
    except (ValueError, IndexError):
        pass
    else:
        raise SystemExit("profile user-image mutation survived")

try:
    validate_runner_profile_image(
        RUNNER,
        MAKEFILE.replace("$(F)/fs.img: user .FORCE", "$(F)/fs.img: .FORCE", 1),
    )
except (ValueError, IndexError):
    pass
else:
    raise SystemExit("profile user dependency mutation survived")

deadline_guest_mutations = (
    GUEST.replace(
        '\tcheck(agent_watch(AGENT_EVENT_MESSAGE, "thread-deadline-release") == 0,\n'
        '\t      "watch infinite waiter release");\n',
        "",
        1,
    ),
    GUEST.replace(
        'agent_watch(AGENT_EVENT_MESSAGE, "thread-deadline-release")',
        'agent_watch(AGENT_EVENT_MESSAGE, "unmatched-release")',
        1,
    ),
    GUEST.replace(
        'strcpy(event.payload, "thread-deadline-release");',
        'strcpy(event.payload, "unmatched-release");',
        1,
    ),
    mutate_function(
        GUEST,
        "static void check_thread_wait_deadlines(",
        "check(agent_wake(getpid(), &event) == 0",
        "check(agent_wake(getpid(), &event) == AGENT_STATUS_NOT_FOUND",
    ),
    GUEST.replace(
        "\tcheck(agent_unwatch(AGENT_EVENT_MESSAGE,\n"
        '\t\t\t    "thread-deadline-release") == 1,\n'
        '\t      "unwatch infinite waiter release");\n',
        "",
        1,
    ),
    GUEST.replace(
        '"thread-deadline-release") == 1,',
        '"thread-deadline-release") == 0,',
        1,
    ),
)
for mutated_guest in deadline_guest_mutations:
    try:
        validate_deadline_guest(mutated_guest)
    except (ValueError, IndexError):
        pass
    else:
        raise SystemExit("deadline guest route mutation survived")

handoff_guest_mutations = (
    GUEST.replace(
        "wait_handoff_until_sleeping(wait_handoff_before.wait_sleep_count + count);",
        "wait_handoff_until_sleeping(wait_handoff_before.wait_sleep_count + 1);",
        1,
    ),
    GUEST.replace(
        "wait_handoff_before.wait_wakeup_count + 1,",
        "wait_handoff_before.wait_wakeup_count + count,",
        1,
    ),
    GUEST.replace(
        "wait_handoff_before.wait_wakeup_count + count,",
        "wait_handoff_before.wait_wakeup_count + 1,",
        1,
    ),
    GUEST.replace("{1, 4, 8, WAIT_HANDOFF_MAX}", "{1}", 1),
)
for mutated_guest in handoff_guest_mutations:
    try:
        validate_handoff_guest(mutated_guest)
    except (ValueError, IndexError):
        pass
    else:
        raise SystemExit("event handoff guest mutation survived")

deadline_mutations = (
    (mutate_function(IPC, "agent_ipc_tick_proc(", "for (int tid = 0; tid < NTHREAD; tid++)", "for (int tid = 0; tid < 1; tid++)"), PROC, CORE, IDENTITY, SYSCALL),
    (mutate_function(IPC, "agent_ipc_tick_proc(", "wait_queue_wake_key_all(&p->agent_event_waiters,", "wait_queue_wake_all(&p->agent_event_waiters); /* "), PROC, CORE, IDENTITY, SYSCALL),
    (mutate_function(IPC, "agent_ipc_tick_proc(", "\t\twoken = wait_queue_wake_key_all", "\t\tt->agent_wait_deadline_valid = 0;\n\t\twoken = wait_queue_wake_key_all"), PROC, CORE, IDENTITY, SYSCALL),
    (mutate_function(IPC, "int sys_agent_wait_cancel(", "\t\t\tagent_ipc_handoff_event_locked(target);", "\t\t\ttarget->agent_wait_deadline_valid = 0;\n\t\t\tagent_ipc_handoff_event_locked(target);"), PROC, CORE, IDENTITY, SYSCALL),
    (
        IPC,
        mutate_function(
            PROC,
            "proc_image_install_state_valid_locked(",
            "intr_get() || main->process != p",
            "main->process != p",
        ),
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (
        IPC,
        mutate_function(
            PROC,
            "proc_image_install_state_valid_locked(",
            "main != curr_thread()",
            "main == curr_thread()",
        ),
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (
        IPC,
        mutate_function(
            PROC,
            "proc_image_install_state_valid_locked(",
            "main->state != RUNNING",
            "main->state != T_USED",
        ),
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (
        IPC,
        mutate_function(
            PROC,
            "proc_image_install_state_valid_locked(",
            "main->identity_generation == 0",
            "main->identity_generation != 0",
        ),
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (
        IPC,
        mutate_function(
            PROC,
            "proc_image_install_state_valid_locked(",
            "mode == PROC_IMAGE_INSTALL_BOOTSTRAP",
            "mode == PROC_IMAGE_INSTALL_LIVE_EXEC",
        ),
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (
        IPC,
        mutate_function(
            PROC,
            "proc_image_install_state_valid_locked(",
            "main->state != T_UNUSED",
            "main->state != T_USED",
        ),
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (
        IPC,
        mutate_function(
            PROC,
            "proc_image_install_state_valid_locked(",
            "main->tid != -1",
            "main->tid != 0",
        ),
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (
        IPC,
        mutate_function(
            PROC,
            "proc_image_install_state_valid_locked(",
            "main->identity_generation != 0",
            "main->identity_generation == 0",
        ),
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (
        IPC,
        mutate_function(
            PROC,
            "proc_image_install_state_valid_locked(",
            "for (int tid = 1; tid < NTHREAD; tid++)",
            "for (int tid = 1; tid < 1; tid++)",
        ),
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (
        mutate_function(
            IPC,
            "agent_ipc_process_image_install_locked(",
            "intr_get()",
            "0",
        ),
        PROC,
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (
        mutate_function(
            IPC,
            "agent_ipc_process_image_install_locked(",
            "for (int tid = 0; tid < NTHREAD; tid++)",
            "for (int tid = 0; tid < 1; tid++)",
        ),
        PROC,
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (
        IPC,
        mutate_function(
            PROC,
            "int proc_install_user_image(",
            "\tagent_process_image_install_locked(p);",
            "",
        ),
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (
        IPC,
        mutate_function(
            PROC,
            "int proc_install_user_image(",
            "\tagent_process_image_install_locked(p);\n"
            "\tsync_proc_exec_reset_locked(p, &p->threads[0]);",
            "\tsync_proc_exec_reset_locked(p, &p->threads[0]);\n"
            "\tagent_process_image_install_locked(p);",
        ),
        CORE,
        IDENTITY,
        SYSCALL,
    ),
    (IPC, PROC, CORE.replace("agent_ipc_thread_sched_snapshot(t, &ipc);", ""), IDENTITY, SYSCALL),
)
for sources in deadline_mutations:
    try:
        validate_thread_deadlines(*sources)
    except (ValueError, IndexError):
        pass
    else:
        raise SystemExit("thread-deadline mutation survived")

watch_mutations = {
    "capability": mutate_function(IPC, "agent_ipc_watch_update(",
                                  "\tif (!agent_identity_has_cap(p, AGENT_CAP_WATCH)) return AGENT_STATUS_DENIED;\n", ""),
    "event-boundary": mutate_function(IPC, "agent_ipc_watch_update(",
                                      "event_type > AGENT_EVENT_MAX", "event_type >= AGENT_EVENT_MAX"),
    "audit-action": mutate_function(IPC, "agent_ipc_watch_update(",
                                    'remove ? "unwatch" : "watch"', 'remove ? "watch" : "unwatch"'),
    "audit-value": mutate_function(IPC, "agent_ipc_watch_update(",
                                   "remove ? result : event_type", "event_type"),
}
for mutation, mutated_ipc in watch_mutations.items():
    try:
        validate_watch_syscalls(mutated_ipc, CORE)
    except (ValueError, IndexError):
        pass
    else:
        raise SystemExit(f"watch syscall mutation survived: {mutation}")

for token in (
    "C_SRCS := $(filter-out $K/wait_atomic_test.c,$(C_SRCS))",
    "CFLAGS += -DWAIT_ATOMIC_TEST_PROFILE",
    "WAIT_ATOMIC_TEST_PROFILE=$(WAIT_ATOMIC_TEST_PROFILE)",
):
    if token not in MAKEFILE:
        raise SystemExit(f"missing test-profile isolation: {token}")
if MARKER not in GUEST or MARKER not in RUNNER:
    raise SystemExit("wait-publication exact dynamic marker is not enforced")
if DEADLINE_MARKER not in GUEST or DEADLINE_MARKER not in RUNNER:
    raise SystemExit("thread-deadline exact dynamic marker is not enforced")
if HANDOFF_MARKER not in GUEST or HANDOFF_MARKER not in RUNNER:
    raise SystemExit("event wake-one handoff marker is not enforced")
if (
    BATON_MARKER not in GUEST
    or BATON_MARKER not in RUNNER
    or BATON_MARKER not in LOG_VALIDATOR
):
    raise SystemExit("event identity baton marker is not enforced")
for token in (
    "wait_atomic_test_query(WAIT_ATOMIC_TEST_AGENT_WAIT",
    "before.event_queue_count == 0",
    "receipt.flags == WAIT_ATOMIC_TEST_AGENT_FLAGS",
    "wait_atomic_test_query(WAIT_ATOMIC_TEST_TEARDOWN",
    "receipt.flags == WAIT_ATOMIC_TEST_TEARDOWN_FLAGS",
    "receipt.flags == WAIT_ATOMIC_TEST_DEADLINE_FLAGS",
    "WAIT_ATOMIC_TEST_DEADLINE_FINITE_INFINITE",
    "WAIT_ATOMIC_TEST_DEADLINE_LONG_ONLY",
    "snapshot.reused_threads == 2",
):
    if token not in GUEST:
        raise SystemExit(f"wait-publication receipt is not enforced: {token}")

print("[wait-atomic] predicate recheck, injection and mutation wiring passed")
