#!/usr/bin/env python3
"""持有并原子推进 Observe 恢复启动控制状态。"""

from __future__ import annotations

import argparse
import os
import re
import stat
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from . import plain_ucore_fs_extract as ucore_fs
else:
    import plain_ucore_fs_extract as ucore_fs


PHASE_FILE = "obsphase"
PHASE_MAGIC = 0x4F425350
PHASE_STATE_BYTES = 168
IDENTITY_STRUCT = struct.Struct("<IIIIQQQQQQQQ")
EMPTY_SLOT = bytes(PHASE_STATE_BYTES)
STATE_NAMES = ("empty", "phase0", "phase1", "phase2")

IDENTITY_LINE = re.compile(
    r"^agentobsreboot_ucore: (?P<tag>phase1_identity|phase2_successor) "
    r"scope=(?P<scope>[0-9]+) agent=(?P<agent>[0-9]+) "
    r"lifecycle_id=(?P<lifecycle_id>[0-9]+) "
    r"lifecycle_generation=(?P<lifecycle_generation>[0-9]+) "
    r"max_sequence=(?P<max_sequence>[0-9]+) "
    r"max_span_id=(?P<max_span_id>[0-9]+) "
    r"max_event_id=(?P<max_event_id>[0-9]+) "
    r"actor_control_id=(?P<actor_control_id>[0-9]+) "
    r"receipt_sequence=(?P<receipt_sequence>[0-9]+) "
    r"receipt_record_hash=(?P<receipt_record_hash>[0-9]+) "
    r"receipt_id=(?P<receipt_id>[0-9]+)$"
)
CUT_LINE = re.compile(
    r"^agentobsreboot_ucore: (?P<tag>lease_cut_alloc|lease_cut_successor) "
    r"audit=(?P<audit>[0-9]+) span=(?P<span>[0-9]+) "
    r"event=(?P<event>[0-9]+) control=(?P<control>[0-9]+) "
    r"agent=(?P<agent>[0-9]+) lifecycle_slot=(?P<lifecycle_slot>[0-9]+) "
    r"lifecycle_generation=(?P<lifecycle_generation>[0-9]+)$"
)


class PhaseControlError(ValueError):
    """镜像或观测到的转换违反阶段控制 ABI。"""


@dataclass(frozen=True)
class EvidenceIdentity:
    scope_id: int = 0
    agent_id: int = 0
    lifecycle_id: int = 0
    lifecycle_reserved: int = 0
    lifecycle_generation: int = 0
    max_sequence: int = 0
    max_span_id: int = 0
    max_event_id: int = 0
    actor_control_id: int = 0
    receipt_sequence: int = 0
    receipt_record_hash: int = 0
    receipt_id: int = 0

    def pack(self) -> bytes:
        return IDENTITY_STRUCT.pack(
            self.scope_id,
            self.agent_id,
            self.lifecycle_id,
            self.lifecycle_reserved,
            self.lifecycle_generation,
            self.max_sequence,
            self.max_span_id,
            self.max_event_id,
            self.actor_control_id,
            self.receipt_sequence,
            self.receipt_record_hash,
            self.receipt_id,
        )


@dataclass(frozen=True)
class PhaseState:
    phase: int
    evidence: EvidenceIdentity = EvidenceIdentity()
    successor: EvidenceIdentity = EvidenceIdentity()

    def pack(self) -> bytes:
        return (
            struct.pack("<II", PHASE_MAGIC, self.phase)
            + self.evidence.pack()
            + self.successor.pack()
        )


ZERO_IDENTITY = EvidenceIdentity()


def _read_all(fd: int, size: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(fd, min(1024 * 1024, remaining))
        if not chunk:
            raise PhaseControlError("filesystem image changed during read")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _open_regular(path: Path, flags: int) -> tuple[int, os.stat_result]:
    open_flags = flags | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, open_flags)
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            raise PhaseControlError("phase-control target is not a regular file")
        if status.st_size <= 0:
            raise PhaseControlError("filesystem image is empty")
        return fd, status
    except BaseException:
        os.close(fd)
        raise


def _phase_extent(image: bytes) -> tuple[int, bytes]:
    superblock = ucore_fs.read_superblock(image)
    if superblock.magic != ucore_fs.FSMAGIC_AGENT_PRINCIPAL:
        raise PhaseControlError("phase control requires the current Agent filesystem format")
    matches = [
        inum
        for inum, name in ucore_fs.root_entries(image, superblock)
        if name == PHASE_FILE
    ]
    if len(matches) != 1:
        raise PhaseControlError(
            f"expected exactly one {PHASE_FILE!r} root entry, found {len(matches)}"
        )
    phase_inum = matches[0]
    inode = ucore_fs.read_inode(image, superblock, phase_inum)
    if inode.type != ucore_fs.T_FILE or inode.size != PHASE_STATE_BYTES:
        raise PhaseControlError(
            "phase slot has the wrong inode type or size "
            f"(actual type={inode.type}, size={inode.size}; "
            f"expected type={ucore_fs.T_FILE}, size={PHASE_STATE_BYTES})"
        )
    expected_security = (
        inode.vfs_flags == ucore_fs.VFS_LABEL_F_PROTECTED
        and inode.vfs_policy == ucore_fs.VFS_POLICY_WORKFLOW
        and inode.vfs_scope_id == ucore_fs.VFS_SCOPE_SYSTEM
        and inode.vfs_exec_profile == ucore_fs.VFS_EXEC_PROFILE_NONE
        and inode.fs_owner_domain == ucore_fs.FS_OWNER_SYSTEM
        and inode.fs_owner_version == ucore_fs.FS_OWNER_VERSION
        and inode.exec_flags == 0
        and inode.exec_generation == 0
        and inode.exec_role_mask == 0
        and inode.exec_layout_version == 0
        and inode.exec_rw_offset == 0
    )
    if not expected_security:
        raise PhaseControlError(
            "phase slot is not an exact non-executable SYSTEM/WORKFLOW data object"
        )
    if inode.addrs[0] == 0 or any(inode.addrs[1:]):
        raise PhaseControlError("phase slot must occupy one dedicated direct block")
    if (
        superblock.datastart is None
        or inode.addrs[0] < superblock.datastart
        or inode.addrs[0] >= superblock.size
    ):
        raise PhaseControlError("phase slot points outside the data-block arena")
    phase_block = inode.addrs[0]
    bitmap_offset = superblock.bmapstart * ucore_fs.BSIZE + phase_block // 8
    if image[bitmap_offset] & (1 << (phase_block % 8)) == 0:
        raise PhaseControlError("phase slot block is not allocated in the bitmap")
    if superblock.qmapstart is None:
        raise PhaseControlError("phase slot image has no storage-owner map")
    owner_offset = superblock.qmapstart * ucore_fs.BSIZE + phase_block * 4
    if ucore_fs.u32(image, owner_offset) != ucore_fs.FS_OWNER_SYSTEM:
        raise PhaseControlError("phase slot block is not SYSTEM-owned")
    for inum in range(1, superblock.ninodes):
        inode_offset = (
            (inum // superblock.ipb + superblock.inodestart) * ucore_fs.BSIZE
            + (inum % superblock.ipb) * superblock.dinode_size
        )
        if ucore_fs.u16(image, inode_offset) == 0:
            continue
        candidate = ucore_fs.read_inode(image, superblock, inum)
        if inum == phase_inum:
            continue
        if phase_block in candidate.addrs:
            raise PhaseControlError("phase slot block aliases another inode mapping")
        indirect_block = candidate.addrs[ucore_fs.NDIRECT]
        if indirect_block == 0:
            continue
        indirect = ucore_fs.block(image, indirect_block)
        if any(
            ucore_fs.u32(indirect, index * 4) == phase_block
            for index in range(ucore_fs.NINDIRECT)
        ):
            raise PhaseControlError("phase slot block aliases indirect file data")
    payload = ucore_fs.read_file(image, inode)
    if len(payload) != PHASE_STATE_BYTES:
        raise PhaseControlError("phase slot is sparse or truncated")
    return inode.addrs[0] * ucore_fs.BSIZE, payload


def _unpack_identity(payload: bytes, offset: int) -> EvidenceIdentity:
    return EvidenceIdentity(*IDENTITY_STRUCT.unpack_from(payload, offset))


def _identity_valid(identity: EvidenceIdentity, receipts: bool) -> bool:
    core = (
        identity.scope_id,
        identity.agent_id,
        identity.lifecycle_id,
        identity.lifecycle_generation,
        identity.max_sequence,
        identity.max_span_id,
        identity.max_event_id,
        identity.actor_control_id,
    )
    receipt = (
        identity.receipt_sequence,
        identity.receipt_record_hash,
        identity.receipt_id,
    )
    return (
        identity.lifecycle_reserved == 0
        and ucore_fs.VFS_SCOPE_FIRST_DYNAMIC
        <= identity.scope_id
        < ucore_fs.FS_OWNER_SCOPE_FLAG
        and all(value > 0 for value in core)
        and (all(value > 0 for value in receipt) if receipts else not any(receipt))
    )


def _successor_monotonic(old: EvidenceIdentity, new: EvidenceIdentity) -> bool:
    return (
        new.agent_id > old.agent_id
        and new.max_sequence > old.max_sequence
        and new.max_span_id > old.max_span_id
        and new.max_event_id > old.max_event_id
        and new.actor_control_id > old.actor_control_id
        and (
            new.lifecycle_id != old.lifecycle_id
            or new.lifecycle_generation > old.lifecycle_generation
        )
    )


def _decode_state(payload: bytes) -> tuple[str, PhaseState | None]:
    if payload == EMPTY_SLOT:
        return "empty", None
    if len(payload) != PHASE_STATE_BYTES:
        raise PhaseControlError("phase slot has a partial ABI payload")
    magic, phase = struct.unpack_from("<II", payload)
    if magic != PHASE_MAGIC or phase > 2:
        raise PhaseControlError("phase slot contains an unknown state header")
    state = PhaseState(
        phase,
        _unpack_identity(payload, 8),
        _unpack_identity(payload, 8 + IDENTITY_STRUCT.size),
    )
    if state.pack() != payload:
        raise PhaseControlError("phase slot is not a canonical ABI state")
    if phase == 0:
        valid = state.evidence == ZERO_IDENTITY and state.successor == ZERO_IDENTITY
    elif phase == 1:
        valid = _identity_valid(state.evidence, True) and \
            state.successor == ZERO_IDENTITY
    else:
        valid = (
            _identity_valid(state.evidence, True)
            and _identity_valid(state.successor, False)
            and _successor_monotonic(state.evidence, state.successor)
        )
    if not valid:
        raise PhaseControlError(f"phase{phase} violates identity invariants")
    return f"phase{phase}", state


def _read_image(path: Path) -> tuple[bytes, os.stat_result]:
    fd, status = _open_regular(path, os.O_RDONLY)
    try:
        return _read_all(fd, status.st_size), status
    finally:
        os.close(fd)


def read_state(image_path: Path) -> str:
    image, _ = _read_image(image_path)
    _, payload = _phase_extent(image)
    name, _ = _decode_state(payload)
    return name


def _read_log(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except UnicodeError as error:
        raise PhaseControlError(f"Guest log is not strict UTF-8: {path}") from error


def _parse_identity_log(
    path: Path, tag: str, completion_marker: str
) -> tuple[EvidenceIdentity, int]:
    lines = _read_log(path)
    candidates = [(index, line) for index, line in enumerate(lines) if tag in line]
    matches = [
        (index, match)
        for index, line in candidates
        if (match := IDENTITY_LINE.fullmatch(line)) is not None
        and match.group("tag") == tag
    ]
    completions = [index for index, line in enumerate(lines) if line == completion_marker]
    if len(candidates) != 1 or len(matches) != 1 or len(completions) != 1:
        raise PhaseControlError(f"Guest log lacks one exact {tag!r} transition line")
    index, match = matches[0]
    if index >= completions[0]:
        raise PhaseControlError(f"Guest {tag!r} identity follows its completion marker")
    identity = EvidenceIdentity(
        scope_id=int(match.group("scope")),
        agent_id=int(match.group("agent")),
        lifecycle_id=int(match.group("lifecycle_id")),
        lifecycle_generation=int(match.group("lifecycle_generation")),
        max_sequence=int(match.group("max_sequence")),
        max_span_id=int(match.group("max_span_id")),
        max_event_id=int(match.group("max_event_id")),
        actor_control_id=int(match.group("actor_control_id")),
        receipt_sequence=int(match.group("receipt_sequence")),
        receipt_record_hash=int(match.group("receipt_record_hash")),
        receipt_id=int(match.group("receipt_id")),
    )
    return identity, index


def _parse_cut_log(path: Path, tag: str) -> tuple[dict[str, int], int]:
    lines = _read_log(path)
    candidates = [
        (index, line)
        for index, line in enumerate(lines)
        if "agentobsreboot_ucore: lease_cut_" in line
    ]
    matches = [
        (index, match)
        for index, line in candidates
        if (match := CUT_LINE.fullmatch(line)) is not None
        and match.group("tag") == tag
    ]
    if len(candidates) != 1 or len(matches) != 1:
        raise PhaseControlError(f"Guest log lacks one exact {tag!r} allocation line")
    index, match = matches[0]
    return (
        {name: int(value) for name, value in match.groupdict().items() if name != "tag"},
        index,
    )


def _validate_cut_successor(old: dict[str, int], new: dict[str, int]) -> None:
    for key in ("audit", "span", "event", "control", "agent"):
        if new[key] <= old[key]:
            raise PhaseControlError(f"identity allocator {key} did not advance")
    if (
        new["lifecycle_slot"] != old["lifecycle_slot"]
        or new["lifecycle_generation"] <= old["lifecycle_generation"]
    ):
        raise PhaseControlError("lifecycle allocator did not advance its stable slot")


def _atomic_replace_payload(
    image_path: Path,
    image: bytes,
    status: os.stat_result,
    offset: int,
    payload: bytes,
) -> None:
    updated = bytearray(image)
    updated[offset : offset + len(payload)] = payload
    temporary = image_path.with_name(
        f".{image_path.name}.phase-control-{os.getpid()}"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(temporary, flags, stat.S_IMODE(status.st_mode))
    try:
        view = memoryview(updated)
        written = 0
        while written < len(view):
            count = os.write(fd, view[written:])
            if count <= 0:
                raise PhaseControlError("short write while replacing phase image")
            written += count
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        temporary.unlink(missing_ok=True)
        raise
    os.close(fd)
    current_image, current = _read_image(image_path)
    if (
        current.st_dev != status.st_dev
        or current.st_ino != status.st_ino
        or current_image != image
    ):
        temporary.unlink(missing_ok=True)
        raise PhaseControlError("phase-control image changed before atomic replace")
    os.replace(temporary, image_path)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(image_path.parent, directory_flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def advance_state(
    image_path: Path,
    source: str,
    target: str,
    guest_log: Path | None,
    cut_log: Path | None,
) -> None:
    allowed = {("empty", "phase0"), ("phase0", "phase1"), ("phase1", "phase2")}
    if (source, target) not in allowed:
        raise PhaseControlError(f"invalid phase transition {source!r} -> {target!r}")
    image, status = _read_image(image_path)
    offset, payload = _phase_extent(image)
    actual, state = _decode_state(payload)
    if actual != source:
        raise PhaseControlError(f"phase slot is {actual!r}, expected {source!r}")
    if target == "phase0":
        if guest_log is not None or cut_log is not None:
            raise PhaseControlError("empty -> phase0 does not consume identity logs")
        successor = PhaseState(0)
    elif target == "phase1":
        if guest_log is None or cut_log is None:
            raise PhaseControlError("phase1 requires boot0 and boot1 Guest logs")
        evidence, evidence_index = _parse_identity_log(
            guest_log,
            "phase1_identity",
            "agentobsreboot_ucore: boot1_checkpoint_ready=1",
        )
        old_cut, _ = _parse_cut_log(cut_log, "lease_cut_alloc")
        new_cut, cut_index = _parse_cut_log(guest_log, "lease_cut_successor")
        _validate_cut_successor(old_cut, new_cut)
        if cut_index >= evidence_index or not _identity_valid(evidence, True):
            raise PhaseControlError("phase1 evidence is not a valid post-cut identity")
        successor = PhaseState(1, evidence)
    else:
        if guest_log is None or cut_log is not None or state is None:
            raise PhaseControlError("phase2 requires only the boot2 Guest log")
        identity, _ = _parse_identity_log(
            guest_log,
            "phase2_successor",
            "agentobsreboot_ucore: boot2_reap_replicated=1",
        )
        if not _identity_valid(identity, False) or not _successor_monotonic(
            state.evidence, identity
        ):
            raise PhaseControlError("phase2 successor is not monotonic")
        successor = PhaseState(2, state.evidence, identity)
    successor_payload = successor.pack()
    if len(successor_payload) != PHASE_STATE_BYTES:
        raise PhaseControlError("phase-control ABI encoder drifted")
    _atomic_replace_payload(image_path, image, status, offset, successor_payload)
    verified_image, _ = _read_image(image_path)
    verified_offset, verified_payload = _phase_extent(verified_image)
    verified_name, _ = _decode_state(verified_payload)
    if (
        verified_offset != offset
        or verified_payload != successor_payload
        or verified_name != target
    ):
        raise PhaseControlError("atomic phase transition failed exact reread")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify")
    verify.add_argument("--image", type=Path, required=True)
    verify.add_argument("--expect", choices=STATE_NAMES, required=True)

    advance = commands.add_parser("advance")
    advance.add_argument("--image", type=Path, required=True)
    advance.add_argument("--from", dest="source", choices=STATE_NAMES, required=True)
    advance.add_argument("--to", dest="target", choices=STATE_NAMES, required=True)
    advance.add_argument("--guest-log", type=Path)
    advance.add_argument("--cut-log", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "verify":
            actual = read_state(args.image)
            if actual != args.expect:
                raise PhaseControlError(
                    f"phase slot is {actual!r}, expected {args.expect!r}"
                )
        else:
            advance_state(
                args.image, args.source, args.target, args.guest_log, args.cut_log
            )
    except (OSError, ValueError) as error:
        print(f"[observe-phase-control] {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
