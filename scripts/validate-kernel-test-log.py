#!/usr/bin/env python3
"""校验已完全排空的内核测试日志中的 profile 专用标记。"""

import argparse
import re
import sys
from pathlib import Path


THREAD_MARKERS = (
    "threadresource_ucore: domain_limit=1",
    "threadresource_ucore: capacity_reject_stable=1",
    "threadresource_ucore: reserved_domain_limit=1",
    "threadresource_ucore: reserved_domain_reuse=1",
    "threadresource_ucore: exit_reuse=1",
    "threadresource_ucore: ordinary_waterline=1",
    "threadresource_ucore: global_thread_limit=1",
    "threadresource_ucore: reserved_global_limit=1",
    "threadresource_ucore: reserved_progress=1",
    "threadresource_ucore: reserved_global_reuse=1",
    "threadresource_ucore: global_reuse=1",
    "threadresource_ucore: domain_fairness=1",
    "threadresource_ucore: parent passed",
)

PROC_REAP_MARKERS = (
    "procreap_ucore: process lifecycle verification",
    "procreap_ucore: child-first=160",
    "procreap_ucore: parent-first=160",
    "procreap_ucore: orphan-resource=136",
    "procreap_ucore: blocked-syscall=384",
    "procreap_ucore: wait-queue cancellation passed",
    "procreap_ucore: detached-wait=8",
    "procreap_ucore: unreaped-parent-isolated=1",
    "procreap_ucore: live-domain-limit=1",
    "procreap_ucore: lineage-bypass-denied=1",
    "procreap_ucore: live-quota-returned=1",
    "procreap_ucore: peer-domain-isolated=1",
    "procreap_ucore: parent passed",
)

PROC_REAP_AGENT_MARKERS = (
    "procreap_agent_ucore: bounded teardown scheduling",
    "procreap_agent_ucore: child-pressure-isolated=1",
    "procreap_agent_ucore: reserved-agent-slot=1",
    "procreap_agent_ucore: adversarial-agent=1",
    "procreap_agent_ucore: parent passed",
)

FILE_MARKERS = (
    "fileresource_ucore: blocking_pin_bounded=1",
    "fileresource_ucore: exit_reuse=1",
    "fileresource_ucore: pipe_rollback=1",
    "fileresource_ucore: domain_limit=1",
    "fileresource_ucore: ordinary_waterline=1",
    "fileresource_ucore: reserved_progress=1",
    "fileresource_ucore: parent passed",
)

PHYSICAL_RESOURCE_MARKERS = (
    "physicalresource_ucore: brk_atomic=1 fork_inherit=1 shrink_refund=1 guard=1",
    "physicalresource_ucore: physical_transfer_rejected=1 mixed_atomic=1",
    "physicalresource_ucore: reserved_promise_lifecycle=1",
    "physicalresource_ucore: reserved_domain_fairness=1",
    "physicalresource_ucore: reserved_domain_refund=1",
    "physicalresource_ucore: domain_isolation=1",
    "physicalresource_ucore: system_reserve=1",
    "physicalresource_ucore: teardown_refund=1",
    "physicalresource_ucore: parent passed",
)

WORKFLOW_TEARDOWN_CAPACITY_SLOT = object()
WORKFLOW_TEARDOWN_CAPACITY_PREFIX = (
    "workflow_teardown_race_ucore: blocked_fdget_cycles="
)
WORKFLOW_TEARDOWN_SEQUENCE = (
    "workflow_teardown_race_ucore: sentinel_public_exec=1 identity_cleared=1 context_unmapped=1 endpoints_revoked=1 scoped_fd_revoked=1 lifecycle_preserved=1 resource_domain_preserved=1 physical_io_charged=1 normal_class=1 argv_build_rollback=1",
    "workflow_teardown_race_ucore: controller_public_exec_rejected=1 post_prepare_rollback=1 identity_preserved=1 context_preserved=1 fd_preserved=1",
    "workflow_teardown_race_ucore: pending_exec_public_image=1",
    "workflow_teardown_race_ucore: pending_exec_public_ready=1 inherited_fd_revoked=1 controller_cancelled=1",
    "workflow_teardown_race_ucore: lifecycle_abi_prefix=1 bad_param_no_write=1 factory_charged=1 self_only_stale=1",
    "workflow_teardown_race_ucore: factory_close=1 final_snapshot=1 public_lineage=1 lifecycle_reclaimed=1",
    "workflow_teardown_race_ucore: natural_exit=1 final_snapshot=1 lifecycle_reclaimed=1",
    "workflow_teardown_race_ucore: spawn_descendants_drained=1 public_exec_escape_blocked=1 nested_multithread_handoff=1 root_control_reached=1",
    WORKFLOW_TEARDOWN_CAPACITY_SLOT,
    "workflow_teardown_race_ucore: blocked_fdget_capacity_crossed=1 file_objects_reclaimed=1",
    "workflow_teardown_race_ucore: lifecycle_id_reused=1 generation_advanced=1 factory_reclaimed=1 natural_reclaimed=1 stale_keys_rejected=1",
    "workflow_teardown_race_ucore: fresh_account=1 io_debt=0 cache=0 inode_reusable=1",
    "workflow_teardown_race_ucore: parent passed",
)
WORKFLOW_TEARDOWN_MARKERS = tuple(
    marker
    for marker in WORKFLOW_TEARDOWN_SEQUENCE
    if marker is not WORKFLOW_TEARDOWN_CAPACITY_SLOT
)

WAIT_ATOMIC_MARKERS = (
    "agentfinal_ucore: context_ro_mapping=1 low_agent_fault=-2 public_unmapped_fault=-2",
    "agentfinal_ucore: context_commit_lane=1 sequence=1..3 hash=1",
    "agentfinal_ucore: context_active_path=1 archive_retained=1 direct_query=1 fifo_suffix=1",
    "agentfinal_ucore: wait_publication_atomic=1 event_wake_none=1 event_no_sleep=1 sibling_wake_none=1 teardown_completed=1",
    "agentfinal_ucore: thread_wait_deadlines finite_infinite=1 distinct_deadlines=1 keyed_timer=1 loop_aggregate=1 slot_reuse=1",
    "agentfinal_ucore: event_baton_identity timeline_waiter=1 event_waiter=1 event_wakeups=1",
    "agentfinal_ucore: event_wake_handoff waiters=1,4,8,15 wakeups=28 herd=0",
    "agentfinal_ucore: passed",
    "agentfinal_ucore: parent passed",
)

CH3_TRACE_MARKERS = (
    "string from task trace test",
    "Test trace OK!",
)

AGENT_CASE_MARKERS = {
	"agentfinal_ucore": (
        "agentfinal_ucore: context_ro_mapping=1 low_agent_fault=-2 public_unmapped_fault=-2",
        "agentfinal_ucore: context_commit_lane=1 sequence=1..3 hash=1",
		"agentfinal_ucore: context_rollback_branch=1 sequence_reuse=0 provenance_bound=1",
        "agentfinal_ucore: context_active_path=1 archive_retained=1 direct_query=1 fifo_suffix=1",
        "agentfinal_ucore: context_rollback_negative nonexistent=1 evicted=1",
        "agentfinal_ucore: fifo oldest=66 latest=193 dropped=65 policy=1",
        "agentfinal_ucore: context_query_cache=1 user_managed=1 kernel_cache_hit=0",
    ),
    "agentloop_ucore": (
        "agentloop_ucore: broadcast_slow_watcher_isolated=1",
        "agentloop_ucore: heartbeat_intrinsic=1 dynamic=1 coalesced=1 stop=1 bounds=1 legacy=1",
    ),
    "agenttoolabi_ucore": (
        "agenttoolabi_ucore: tool_list_contract=1",
        "agenttoolabi_ucore: optional_schema=1 heartbeat_zero_stop=1",
        "agenttoolabi_ucore: schema_generated=1 validated=25",
        "agenttoolabi_ucore: v1_compatible=1",
        "agenttoolabi_ucore: v2_typed_reordered=1",
        "agenttoolabi_ucore: key_capacity=1 llm_response_v1_v2=1 buffer_sentinel=1",
        "agenttoolabi_ucore: strict_negative_matrix=1",
    ),
    "agentsecurity_ucore": (
        "agentsecurity_ucore: legacy_mail_fail_closed=1",
        "agentsecurity_ucore: message_route_lifecycle=1",
        "agentsecurity_ucore: ipc_route_authorization=1",
        "agentsecurity_ucore: target_route_consent=1",
        "agentsecurity_ucore: route_slot_reclaimed=1",
    ),
    "usersafety_ucore": (
        "usersafety_ucore: argv_layout_budget=1024 boundary_accept=1 over_limit_rejected=1 caller_live=1",
    ),
    "agentscope_ucore": (
        "agentscope_ucore: scope_controller_exit_revoke=1 public_lineage=1",
        "agentscope_ucore: lifecycle_reclamation=1",
    ),
    "iobudget_ucore": (
        "iobudget_ucore: fault_exit_armed=1",
        "iobudget_ucore: lineage_rate_accounting=1 immutable_owner=1",
    ),
    "blocking_semantics_ucore": (
        "blocking_semantics_ucore: owner_slot_reuse=16 generation_safe=1",
        "blocking_semantics_ucore: process_exit_multilock=1 baton_revoke=1 cond_sem_interrupt_refund=1",
        "blocking_semantics_ucore: exec_sync_reset=1 stale_ids_rejected=1",
        "blocking_semantics_ucore: atomic_wait_publication=512 cond=1 semaphore=1 count_stable=1",
        "blocking_semantics_ucore: mutex_fifo_waiters=64 dispatch_stable=1",
        "blocking_semantics_ucore: mutex_owner=1 nonowner_rejected=1 recursive_rejected=1 owner_exit_handoff=1",
        "blocking_semantics_ucore: waittid_sleep=1 pipe_wait_queue=1 close_wake_all=1",
    ),
}

SYSCALL_PHASES = (
    (
        "console",
        "SYSCALLFAIR_CONSOLE_BEGIN",
        "SYSCALLFAIR_CONSOLE_PEER",
        "SYSCALLFAIR_CONSOLE_END",
    ),
    (
        "inode",
        "SYSCALLFAIR_INODE_BEGIN",
        "SYSCALLFAIR_INODE_PEER",
        "SYSCALLFAIR_INODE_END",
    ),
    (
        "trunc",
        "SYSCALLFAIR_TRUNC_BEGIN",
        "SYSCALLFAIR_TRUNC_PEER",
        "SYSCALLFAIR_TRUNC_END",
    ),
)

FS_QUOTA_MARKERS = (
    "fsquota_ucore: public_version_churn=1",
    "fsquota_ucore: public_domain_limited=1",
    "fsquota_ucore: post_exit_accounting=1",
    "fsquota_ucore: workflow_reserve=1",
    "fsquota_ucore: workflow_version_reserve=1",
    "fsquota_ucore: content_version_reserve=1",
    "fsquota_ucore: kernel_metadata_reserve=1",
    "fsquota_ucore: pressure_cleanup=1",
)

FS_PERSISTENT_MARKERS = (
    "fspquota_ucore: reboot_charge_persisted=1",
    "fspquota_ucore: deletion_reuse=1",
    "fspquota_ucore: relaunch_charge_persisted=1 launches=2",
    "fspquota_ucore: cleanup_reuse=1",
)

FS_GENERIC_MARKERS = (
    "fsenospc_ucore: inode exhaustion survived",
    "fsenospc_ucore: inode cache exhaustion survived",
    "fsenospc_ucore: block exhaustion survived",
    "fsenospc_ucore: parent passed",
)


class ValidationError(ValueError):
    pass


def ordered_unique(text, markers):
    positions = [text.find(marker) for marker in markers]
    if any(position < 0 for position in positions):
        raise ValidationError(f"missing markers: positions={positions}")
    if positions != sorted(positions):
        raise ValidationError(f"markers out of order: positions={positions}")
    repeated = [marker for marker in markers if text.count(marker) != 1]
    if repeated:
        raise ValidationError(f"markers are not unique: {repeated!r}")
    return positions


def exact_ordered_lines(text, markers):
    lines = text.splitlines()
    positions = []
    for marker in markers:
        hits = [index for index, line in enumerate(lines) if line == marker]
        if len(hits) != 1:
            raise ValidationError(
                f"marker must occur once as a complete line: {marker!r}; hits={hits}"
            )
        positions.append(hits[0])
    if positions != sorted(positions):
        raise ValidationError(f"markers out of order: positions={positions}")
    return positions


def validate_proc_reap(text):
    standard = text.splitlines().count(PROC_REAP_MARKERS[-1])
    adversarial = text.splitlines().count(PROC_REAP_AGENT_MARKERS[-1])
    if (standard, adversarial) == (1, 0):
        return f"standard positions={exact_ordered_lines(text, PROC_REAP_MARKERS)}"
    if (standard, adversarial) == (0, 1):
        return f"adversarial positions={exact_ordered_lines(text, PROC_REAP_AGENT_MARKERS)}"
    raise ValidationError(
        "proc-reap log must contain exactly one standard or adversarial completion"
    )


def ordered_before(text, markers, final_marker):
    final_position = text.find(final_marker)
    positions = [text.find(marker) for marker in markers]
    if final_position < 0 or any(
        position < 0 or position >= final_position for position in positions
    ):
        raise ValidationError(
            f"markers missing before completion: positions={positions}"
        )
    if positions != sorted(positions):
        raise ValidationError(f"markers out of order: positions={positions}")
    return positions


def validate_thread(text):
    positions = ordered_unique(text, THREAD_MARKERS)
    fairness = re.search(
        r"threadresource_ucore: domain_fairness=1 "
        r"hog=(\d+) victim=(\d+) bound=(\d+)",
        text,
    )
    if fairness is None:
        raise ValidationError("missing thread fairness counts")
    hog, victim, bound = map(int, fairness.groups())
    if victim != 512 or bound != 576 or hog > bound:
        raise ValidationError(
            f"thread fairness mismatch: hog={hog} victim={victim} bound={bound}"
        )
    return f"positions={positions} hog={hog} victim={victim} bound={bound}"


def validate_file(text):
    positions = ordered_unique(text, FILE_MARKERS)
    return f"positions={positions}"


def validate_wait_atomic(text):
    positions = exact_ordered_lines(text, WAIT_ATOMIC_MARKERS)
    return f"positions={positions}"


def validate_ch3_trace(text):
    positions = exact_ordered_lines(text, CH3_TRACE_MARKERS)
    return f"positions={positions}"


def validate_agent_case(text, case, context_sync=False):
    marker = f"{case}: parent passed"
    required = (*AGENT_CASE_MARKERS.get(case, ()), marker)
    lines = text.splitlines()
    for expected in required:
        hits = [index for index, line in enumerate(lines) if line == expected]
        if len(hits) != 1:
            raise ValidationError(
                f"Agent case marker must occur once as a complete line: "
                f"{expected!r}; hits={hits}"
            )
    if case == "iobudget_ucore" and "Unexpected mutex id" in text:
        raise ValidationError("iobudget child inherited a stale stdio mutex")
    if context_sync:
        if case != "agentfinal_ucore":
            raise ValidationError("context-sync profile is only valid for agentfinal_ucore")
        context_marker = (
            "agentfinal_ucore: context_sync_atomic=1 append=1 rollback=1 "
            "clear=1 recovery=1"
        )
        if lines.count(context_marker) != 1:
            raise ValidationError("context-sync atomicity marker differs")
        validate_wait_atomic(text)
    return f"case={case} markers={len(required)} context_sync={int(context_sync)}"


def validate_physical_resource(text):
    lines = text.splitlines()
    exact_markers = (
        *PHYSICAL_RESOURCE_MARKERS[:2],
        *PHYSICAL_RESOURCE_MARKERS[4:],
    )
    exact_positions = exact_ordered_lines(text, exact_markers)
    fairness_pattern = re.compile(
        r"physicalresource_ucore: reserved_domain_fairness=1 "
        r"pressure_pages=(\d+) pressure_pipes=(\d+) "
        r"physical_usage=(\d+) physical_limit=(\d+)"
    )
    fairness = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := fairness_pattern.fullmatch(line)) is not None
    ]
    if len(fairness) != 1:
        raise ValidationError(
            f"reserved physical fairness line must be unique: {fairness!r}"
        )
    fairness_position, fairness_match = fairness[0]
    pages, pipes, usage, limit = map(int, fairness_match.groups())
    if not 0 < pages < 256 or not 0 <= pipes <= 5 or usage != limit or limit == 0:
        raise ValidationError(
            "reserved physical pressure did not reach its exact account "
            f"limit: pages={pages} pipes={pipes} usage={usage} limit={limit}"
        )
    promise_pattern = re.compile(
        r"physicalresource_ucore: reserved_promise_lifecycle=1 "
        r"promised=(\d+) limit=(\d+)"
    )
    promise = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := promise_pattern.fullmatch(line)) is not None
    ]
    if len(promise) != 1 or promise[0][1].group(1) != promise[0][1].group(2):
        raise ValidationError("reserved physical promises never filled the pool")
    promise_position = promise[0][0]
    positions = [
        exact_positions[0],
        exact_positions[1],
        promise_position,
        fairness_position,
        *exact_positions[2:],
    ]
    if positions != sorted(positions):
        raise ValidationError(f"markers out of order: positions={positions}")
    raw_pattern = re.compile(
        r"physicalresource_ucore: raw step=(\d+) result=(-?\d+) "
        r"value0=(\d+) value1=(\d+)"
    )
    raw_matches = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := raw_pattern.fullmatch(line)) is not None
    ]
    raw_positions = [index for index, _ in raw_matches]
    raw = [tuple(map(int, match.groups())) for _, match in raw_matches]
    if len(raw) != 30 or [record[0] for record in raw] != list(range(1, 31)):
        raise ValidationError("physical receipt stream is not exact and ordered")
    expected_raw_positions = list(range(positions[0] + 1, positions[1]))
    if raw_positions != expected_raw_positions:
        raise ValidationError(
            "physical receipt block must be contiguous before transfer validation"
        )
    if raw[0][1:] != (0, 2, 0):
        raise ValidationError("physical page kind lost pool-affine identity")
    if any(record[1] != 0 for record in raw[1:5]):
        raise ValidationError("physical transfer setup failed")
    if any(record[1] != -1 for record in raw[5:8]):
        raise ValidationError("pool-affine count mutation was admitted")
    if raw[8][1:] != (0, 1, 1) or raw[9][1:] != (0, 0, 0):
        raise ValidationError("rejected transfer changed account ownership")
    initial = raw[10]
    if initial[1] != 0 or not initial[2] < initial[3]:
        raise ValidationError("invalid initial reserved promise receipt")
    if any(record[1] != 0 for record in raw[11:16]):
        raise ValidationError("reserved promise fill failed")
    if raw[16][1] != 0 or raw[16][2:] != (initial[3], initial[3]):
        raise ValidationError("pending reservation did not fill the promise")
    if raw[17][1] != -1 or raw[18][1:] != (0, 2, 0):
        raise ValidationError("active/closing promise transition is invalid")
    if raw[19][1] != -1 or raw[20][1:] != (0, 3, 0):
        raise ValidationError("closing/draining promise transition is invalid")
    if raw[21][1] != -1 or raw[22][1:] != (0, 0, 3):
        raise ValidationError("pending cancellation released the promise early")
    if raw[23][1] != -1 or raw[24][1:] != (0, 0, 0):
        raise ValidationError("committed usage did not retain then retire the account")
    if raw[25][1:] != (0, initial[2], initial[3]):
        raise ValidationError("draining account did not refund its promise")
    if raw[26][1] != 0 or raw[27][1:] != (0, initial[3], initial[3]):
        raise ValidationError("replacement account did not receive the promise")
    if raw[28][1:] != (0, 0, 0) or raw[29][1:] != (
        0,
        initial[2],
        initial[3],
    ):
        raise ValidationError("replacement cleanup did not restore baseline")
    return (
        f"positions={positions} pages={pages} pipes={pipes} "
        f"usage={usage} receipts={len(raw)}"
    )


def workflow_teardown_expected_lines(domain_file_cap, global_reserved_cap):
    if domain_file_cap <= 6 or global_reserved_cap < domain_file_cap:
        raise ValidationError(
            "invalid expected workflow teardown resource capacities"
        )
    capacity_line = (
        f"{WORKFLOW_TEARDOWN_CAPACITY_PREFIX}{global_reserved_cap + 1} "
        f"domain_cap={domain_file_cap} "
        f"global_reserved_cap={global_reserved_cap}"
    )
    return tuple(
        capacity_line
        if marker is WORKFLOW_TEARDOWN_CAPACITY_SLOT
        else marker
        for marker in WORKFLOW_TEARDOWN_SEQUENCE
    )


def validate_workflow_teardown(text, domain_file_cap, global_reserved_cap):
    expected_lines = workflow_teardown_expected_lines(
        domain_file_cap, global_reserved_cap
    )
    lines = text.splitlines()
    capacity_hits = [
        (index, line)
        for index, line in enumerate(lines)
        if line.startswith(WORKFLOW_TEARDOWN_CAPACITY_PREFIX)
    ]
    if len(capacity_hits) != 1:
        raise ValidationError(
            "capacity marker must occur once as a complete line: "
            f"hits={capacity_hits}"
        )
    _, capacity_line = capacity_hits[0]
    expected_cycles = global_reserved_cap + 1
    expected_capacity_line = next(
        line
        for line in expected_lines
        if line.startswith(WORKFLOW_TEARDOWN_CAPACITY_PREFIX)
    )
    if capacity_line != expected_capacity_line:
        raise ValidationError(
            "workflow teardown capacity mismatch: "
            f"expected {expected_capacity_line!r}, got {capacity_line!r}"
        )
    ordered_positions = exact_ordered_lines(text, expected_lines)
    return (
        f"positions={ordered_positions} cycles={expected_cycles} "
        f"domain_cap={domain_file_cap} "
        f"global_reserved_cap={global_reserved_cap}"
    )


def validate_syscall(text):
    previous_end = -1
    summaries = []
    inode_peer = -1
    inode_end = -1
    trunc_peer = -1
    trunc_end = -1
    for name, begin, peer, end in SYSCALL_PHASES:
        begin_pos, peer_pos, end_pos = ordered_unique(
            text, (begin, peer, end)
        )
        if not (previous_end < begin_pos < peer_pos < end_pos):
            raise ValidationError(
                f"{name} phase order mismatch: "
                f"{begin_pos}/{peer_pos}/{end_pos}"
            )
        previous_end = end_pos
        if name == "inode":
            inode_peer, inode_end = peer_pos, end_pos
        elif name == "trunc":
            trunc_peer, trunc_end = peer_pos, end_pos
        summaries.append(f"{name}={begin_pos}/{peer_pos}/{end_pos}")
    short = "SYSCALLFAIR_INODE_SHORT"
    short_pos = text.find(short)
    if text.count(short) != 1 or not inode_peer < short_pos < inode_end:
        raise ValidationError(f"inode short marker mismatch: {short_pos}")
    fairness = re.findall(
        r"^syscallfair_ucore: truncate_preemptions=(\d+) "
        r"peer_progress=(\d+)$",
        text,
        re.MULTILINE,
    )
    if len(fairness) != 1:
        raise ValidationError(
            "truncate fairness metrics must occur once as a complete line"
        )
    preemptions, peer_progress = map(int, fairness[0])
    metric_line = (
        "syscallfair_ucore: "
        f"truncate_preemptions={preemptions} peer_progress={peer_progress}"
    )
    metric_pos = text.find(metric_line)
    if not trunc_peer < metric_pos < trunc_end:
        raise ValidationError(
            f"truncate fairness metric order mismatch: {metric_pos}"
        )
    if preemptions <= 0 or peer_progress <= 0:
        raise ValidationError(
            "truncate fairness lacked a real scheduling boundary or peer progress"
        )
    passed = "syscallfair_ucore: parent passed"
    passed_pos = text.find(passed)
    if text.count(passed) != 1 or passed_pos <= previous_end:
        raise ValidationError(f"completion marker mismatch: {passed_pos}")
    summaries.append(
        f"truncate_preemptions={preemptions} peer_progress={peer_progress}"
    )
    return " ".join(summaries)


def validate_fs(text, profile, marker):
    if profile == "generic":
        if marker != FS_GENERIC_MARKERS[-1]:
            raise ValidationError("generic filesystem completion marker differs")
        exact_ordered_lines(text, FS_GENERIC_MARKERS)
        return "generic"
    if profile in ("domain", "reserve"):
        required = list(FS_QUOTA_MARKERS)
        if profile == "domain":
            required.append("fsquota_ucore: quota_reuse=1")
        ordered_before(text, required, marker)
        pressure = re.search(
            r"fsquota_ucore: public_domain_limited=1 "
            r"blocks=(\d+) inodes=(\d+)",
            text,
        )
        if pressure is None:
            raise ValidationError("missing quota pressure counts")
        blocks, inodes = map(int, pressure.groups())
        if profile == "domain" and not (
            2 <= blocks <= 16 and 4 <= inodes <= 8
        ):
            raise ValidationError(
                f"domain boundary mismatch: blocks={blocks} inodes={inodes}"
            )
        if profile == "reserve" and not (blocks > 32 and inodes > 12):
            raise ValidationError(
                f"reserve boundary mismatch: blocks={blocks} inodes={inodes}"
            )
        churn = re.search(
            r"fsquota_ucore: public_version_churn=1 cycles=(\d+)", text
        )
        if churn is None or int(churn.group(1)) <= 512:
            raise ValidationError(
                "version churn did not cross the former table capacity"
            )
        return f"{profile} blocks={blocks} inodes={inodes}"
    if profile == "orphan-crash":
        if "fspquota_ucore: crash_orphan_ready=1" not in text:
            raise ValidationError("missing crash-orphan checkpoint")
        return profile
    if profile == "persistent-seed":
        sponsor = re.search(
            r"fspquota_ucore: sponsored_object_charged=1 blocks=(\d+)",
            text,
        )
        seed = re.search(
            r"fspquota_ucore: durable_fixture=1 blocks=(\d+) "
            r"inodes=(\d+) owner_exited=1",
            text,
        )
        if sponsor is None or int(sponsor.group(1)) != 14:
            raise ValidationError(
                "missing sponsored object ownership transfer marker"
            )
        if seed is None:
            raise ValidationError("missing durable quota seed marker")
        if tuple(map(int, seed.groups())) != (18, 8):
            raise ValidationError("durable quota seed limits do not match 18/8")
        return profile
    if profile == "persistent-verify":
        ordered_before(text, FS_PERSISTENT_MARKERS, marker)
        return profile
    raise ValidationError(f"unknown validation profile: {profile}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--profile",
        required=True,
        choices=(
            "proc-reap",
            "thread-resource",
            "file-resource",
            "physical-resource",
            "workflow-teardown-race",
            "wait-atomic",
            "ch3-trace",
            "syscall-fairness",
            "generic",
            "domain",
            "reserve",
            "orphan-crash",
            "persistent-seed",
            "persistent-verify",
        ),
    )
    parser.add_argument("--marker", default="")
    parser.add_argument("--workflow-domain-file-cap", type=int)
    parser.add_argument("--workflow-global-reserved-cap", type=int)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        text = Path(args.log_file).read_text(
            encoding="utf-8", errors="replace"
        )
        if args.profile == "proc-reap":
            summary = validate_proc_reap(text)
        elif args.profile == "thread-resource":
            summary = validate_thread(text)
        elif args.profile == "file-resource":
            summary = validate_file(text)
        elif args.profile == "physical-resource":
            summary = validate_physical_resource(text)
        elif args.profile == "workflow-teardown-race":
            if (
                args.workflow_domain_file_cap is None
                or args.workflow_global_reserved_cap is None
            ):
                raise ValidationError(
                    "workflow teardown profile requires both capacity arguments"
                )
            summary = validate_workflow_teardown(
                text,
                args.workflow_domain_file_cap,
                args.workflow_global_reserved_cap,
            )
        elif args.profile == "wait-atomic":
            summary = validate_wait_atomic(text)
        elif args.profile == "ch3-trace":
            summary = validate_ch3_trace(text)
        elif args.profile == "syscall-fairness":
            summary = validate_syscall(text)
        else:
            if not args.marker:
                raise ValidationError("filesystem profile requires --marker")
            summary = validate_fs(text, args.profile, args.marker)
    except (OSError, ValidationError) as error:
        print(f"[{args.tag}] profile validation failed: {error}", file=sys.stderr)
        return 1
    print(f"[{args.tag}] profile validation passed: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
