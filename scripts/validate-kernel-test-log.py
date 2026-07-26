#!/usr/bin/env python3
"""Validate profile-specific markers in a fully drained kernel test log."""

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

FILE_MARKERS = (
    "fileresource_ucore: blocking_pin_bounded=1",
    "fileresource_ucore: exit_reuse=1",
    "fileresource_ucore: pipe_rollback=1",
    "fileresource_ucore: domain_limit=1",
    "fileresource_ucore: ordinary_waterline=1",
    "fileresource_ucore: reserved_progress=1",
    "fileresource_ucore: parent passed",
)

WORKFLOW_TEARDOWN_MARKERS = (
    "workflow_teardown_race_ucore: lifecycle_abi_prefix=1 bad_param_no_write=1 factory_charged=1 self_only_stale=1",
    "workflow_teardown_race_ucore: factory_close=1 final_snapshot=1 context_waiter=1 metadata_waiter=1 public_lineage=1 lifecycle_reclaimed=1",
    "workflow_teardown_race_ucore: io_retire=1 debt_observed=1 cache_observed=1 dual_scope_progress=1",
    "workflow_teardown_race_ucore: natural_exit=1 final_snapshot=1 context_waiter=1 metadata_waiter=1 lifecycle_reclaimed=1",
    "workflow_teardown_race_ucore: blocked_fdget_capacity_crossed=1 file_objects_reclaimed=1",
    "workflow_teardown_race_ucore: lifecycle_id_reused=1 generation_advanced=1 factory_reclaimed=1 natural_reclaimed=1 stale_keys_rejected=1",
    "workflow_teardown_race_ucore: fresh_account=1 io_debt=0 cache=0 inode_reusable=1",
    "workflow_teardown_race_ucore: parent passed",
)
WORKFLOW_TEARDOWN_CAPACITY_PREFIX = (
    "workflow_teardown_race_ucore: blocked_fdget_cycles="
)

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


def validate_workflow_teardown(text, domain_file_cap, global_reserved_cap):
    if domain_file_cap <= 6 or global_reserved_cap < domain_file_cap:
        raise ValidationError(
            "invalid expected workflow teardown resource capacities"
        )
    lines = text.splitlines()
    positions = []
    for marker in WORKFLOW_TEARDOWN_MARKERS:
        hits = [index for index, line in enumerate(lines) if line == marker]
        if len(hits) != 1:
            raise ValidationError(
                "marker must occur once as a complete line: "
                f"{marker!r}; hits={hits}"
            )
        positions.append(hits[0])
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
    capacity_position, capacity_line = capacity_hits[0]
    expected_cycles = global_reserved_cap + 1
    expected_capacity_line = (
        f"{WORKFLOW_TEARDOWN_CAPACITY_PREFIX}{expected_cycles} "
        f"domain_cap={domain_file_cap} "
        f"global_reserved_cap={global_reserved_cap}"
    )
    if capacity_line != expected_capacity_line:
        raise ValidationError(
            "workflow teardown capacity mismatch: "
            f"expected {expected_capacity_line!r}, got {capacity_line!r}"
        )
    ordered_positions = (
        positions[:4] + [capacity_position] + positions[4:]
    )
    if ordered_positions != sorted(ordered_positions):
        raise ValidationError(
            f"markers out of order: {ordered_positions}"
        )
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
        summaries.append(f"{name}={begin_pos}/{peer_pos}/{end_pos}")
    short = "SYSCALLFAIR_INODE_SHORT"
    short_pos = text.find(short)
    if text.count(short) != 1 or not inode_peer < short_pos < inode_end:
        raise ValidationError(f"inode short marker mismatch: {short_pos}")
    passed = "syscallfair_ucore: parent passed"
    passed_pos = text.find(passed)
    if text.count(passed) != 1 or passed_pos <= previous_end:
        raise ValidationError(f"completion marker mismatch: {passed_pos}")
    return " ".join(summaries)


def validate_fs(text, profile, marker):
    if profile == "generic":
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
            "workflow-teardown-race",
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
            summary = "completion marker verified"
        elif args.profile == "thread-resource":
            summary = validate_thread(text)
        elif args.profile == "file-resource":
            summary = validate_file(text)
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
