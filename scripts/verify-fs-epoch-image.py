#!/usr/bin/env python3
"""校验 fs_epoch 断电回归的原始镜像结果。"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


BLOCK_SIZE = 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
BATCH_BLOCKS = 8
BATCH_PATH = "fsepoch_batch"
CREATED_PATH = "fsepoch_new"
STATE_PATH = "fsepoch_state"
CASES = ("dirty", "inflight", "durable")
SNAPSHOT_FORMAT = "agentos-fs-allocator-v2"
SNAPSHOT_GENERATOR = {"name": "fs-allocator-image.py", "version": "2"}
ROOT_INODE = 1
INFLIGHT_WINDOW_MISS = 3
TARGET_PATHS = frozenset((BATCH_PATH, CREATED_PATH, STATE_PATH))


class VerificationError(ValueError):
    pass


class InflightWindowMiss(VerificationError):
    def __init__(self, result: dict[str, object]):
        super().__init__("power cut missed the partial payload window")
        self.result = result


def pattern(size: int, seed: int) -> bytes:
    return bytes(
        (seed + index * 37 + (index // BLOCK_SIZE) * 17) & 0xFF
        for index in range(size)
    )


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


BATCH_OLD = pattern(BATCH_BLOCKS * BLOCK_SIZE, 0x31)
BATCH_NEW = pattern(BATCH_BLOCKS * BLOCK_SIZE, 0xA7)
CREATED_NEW = pattern(BLOCK_SIZE, 0x5C)
BATCH_OLD_HASH = digest(BATCH_OLD)
BATCH_NEW_HASH = digest(BATCH_NEW)
CREATED_NEW_HASH = digest(CREATED_NEW)


def load_snapshot(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"cannot read snapshot {path}: {error}") from error
    if not isinstance(value, dict):
        raise VerificationError(f"snapshot {path} is not an object")
    for key in (
        "format",
        "generator",
        "image",
        "geometry",
        "state_sha256",
        "allocated_blocks",
        "owned_blocks",
        "qmap_entries",
        "inode_owner_entries",
        "inode_raw_sha256",
        "inode_incarnations",
        "inodes",
        "reachable_inodes",
        "reachable_blocks",
        "orphan_inodes",
        "orphan_blocks",
        "allocated_unowned",
        "owner_without_bitmap",
        "root_names",
        "payload_sha256",
        "inode_blocks",
        "block_sha256",
        "nonzero_data_block_sha256",
        "canonical_violations",
    ):
        if key not in value:
            raise VerificationError(f"snapshot {path} is missing {key}")
    require_snapshot_envelope(value, str(path))
    return value


def require_snapshot_envelope(snapshot: dict[str, object], label: str) -> None:
    if snapshot.get("format") != SNAPSHOT_FORMAT:
        raise VerificationError(f"{label}: snapshot format mismatch")
    if snapshot.get("generator") != SNAPSHOT_GENERATOR:
        raise VerificationError(f"{label}: snapshot generator mismatch")
    geometry = snapshot.get("geometry")
    image = snapshot.get("image")
    if not isinstance(geometry, dict) or not isinstance(image, dict):
        raise VerificationError(f"{label}: snapshot envelope is malformed")
    size = geometry.get("size")
    byte_count = image.get("bytes")
    image_hash = image.get("sha256")
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or byte_count <= 0
        or byte_count > MAX_IMAGE_BYTES
        or byte_count != size * BLOCK_SIZE
        or not isinstance(image_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", image_hash) is None
    ):
        raise VerificationError(f"{label}: snapshot image provenance is invalid")
    semantic = {
        key: value
        for key, value in snapshot.items()
        if key not in {"image", "generator", "state_sha256"}
    }
    calculated = digest(
        json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    )
    if snapshot.get("state_sha256") != calculated:
        raise VerificationError(f"{label}: snapshot semantic hash mismatch")


def table(snapshot: dict[str, object], key: str, label: str) -> dict[str, object]:
    value = snapshot.get(key)
    if not isinstance(value, dict):
        raise VerificationError(f"{label}: {key} is not an object")
    return value


def integer_set(snapshot: dict[str, object], key: str, label: str) -> set[int]:
    value = snapshot.get(key)
    if not isinstance(value, list):
        raise VerificationError(f"{label}: {key} is not an array")
    try:
        return {int(item) for item in value}
    except (TypeError, ValueError) as error:
        raise VerificationError(f"{label}: {key} is malformed") from error


def names(snapshot: dict[str, object]) -> dict[str, int]:
    value = snapshot["root_names"]
    if not isinstance(value, dict):
        raise VerificationError("root_names is not an object")
    return {str(name): int(inum) for name, inum in value.items()}


def require_names(
    snapshot: dict[str, object], expected: dict[str, int], label: str
) -> None:
    actual = names(snapshot)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        replaced = sorted(
            name for name in set(actual) & set(expected) if actual[name] != expected[name]
        )
        raise VerificationError(
            f"{label}: namespace mismatch missing={missing} extra={extra} "
            f"replaced={replaced}"
        )


def payload_hash(snapshot: dict[str, object], path: str) -> str:
    table = snapshot["payload_sha256"]
    root = names(snapshot)
    if path not in root or not isinstance(table, dict):
        raise VerificationError(f"missing payload for {path}")
    value = table.get(str(root[path]))
    if not isinstance(value, str):
        raise VerificationError(f"invalid payload hash for {path}")
    return value


def require_payload(
    snapshot: dict[str, object], path: str, expected: str, label: str
) -> None:
    actual = payload_hash(snapshot, path)
    if actual != expected:
        raise VerificationError(
            f"{label}: {path} payload {actual} does not match {expected}"
        )


def require_absent(snapshot: dict[str, object], path: str, label: str) -> None:
    if path in names(snapshot):
        raise VerificationError(f"{label}: {path} is unexpectedly reachable")


def require_canonical(snapshot: dict[str, object], label: str) -> None:
    violations = snapshot["canonical_violations"]
    if not isinstance(violations, list) or violations:
        raise VerificationError(f"{label}: filesystem is not canonical: {violations}")


def require_state(snapshot: dict[str, object], stage: bytes, label: str) -> None:
    require_payload(snapshot, STATE_PATH, digest(stage), label)


def block_counts(
    before: dict[str, object], fault: dict[str, object]
) -> tuple[int, int]:
    before_names = names(before)
    fault_names = names(fault)
    if before_names.get(BATCH_PATH) != fault_names.get(BATCH_PATH):
        raise VerificationError("inflight: batch inode identity changed")
    inum = str(before_names[BATCH_PATH])
    before_blocks_table = before["inode_blocks"]
    fault_blocks_table = fault["inode_blocks"]
    fault_hashes = fault["block_sha256"]
    if not all(
        isinstance(value, dict)
        for value in (before_blocks_table, fault_blocks_table, fault_hashes)
    ):
        raise VerificationError("inflight: malformed block tables")
    before_blocks = list(before_blocks_table.get(inum, []))
    fault_blocks = list(fault_blocks_table.get(inum, []))
    if before_blocks != fault_blocks or len(fault_blocks) != BATCH_BLOCKS:
        raise VerificationError("inflight: stable batch mapping changed")
    old_count = 0
    new_count = 0
    for index, block in enumerate(fault_blocks):
        actual = fault_hashes.get(str(block))
        old_hash = digest(
            BATCH_OLD[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE]
        )
        new_hash = digest(
            BATCH_NEW[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE]
        )
        if actual == old_hash:
            old_count += 1
        elif actual == new_hash:
            new_count += 1
        else:
            raise VerificationError(
                f"inflight: block {index} is neither complete old nor complete new"
            )
    return old_count, new_count


def blocks_for_inodes(
    snapshot: dict[str, object], inums: set[int], label: str
) -> set[int]:
    inode_blocks = table(snapshot, "inode_blocks", label)
    result: set[int] = set()
    for inum in inums:
        value = inode_blocks.get(str(inum), [])
        if not isinstance(value, list):
            raise VerificationError(f"{label}: inode {inum} block list is malformed")
        try:
            result.update(int(block) for block in value)
        except (TypeError, ValueError) as error:
            raise VerificationError(
                f"{label}: inode {inum} block list is malformed"
            ) from error
    return result


def require_non_target_objects(
    before: dict[str, object], candidate: dict[str, object], label: str
) -> None:
    before_names = names(before)
    candidate_names = names(candidate)
    for path, inum in before_names.items():
        if path in TARGET_PATHS:
            continue
        if candidate_names.get(path) != inum:
            raise VerificationError(f"{label}: non-target object {path} was replaced")
        if payload_hash(candidate, path) != payload_hash(before, path):
            raise VerificationError(f"{label}: non-target payload {path} changed")
        for key in ("inode_blocks", "inodes", "inode_raw_sha256"):
            before_table = table(before, key, "before")
            candidate_table = table(candidate, key, label)
            if before_table.get(str(inum)) != candidate_table.get(str(inum)):
                raise VerificationError(
                    f"{label}: non-target inode state for {path} changed"
                )


def require_global_preservation(
    snapshots: tuple[tuple[str, dict[str, object]], ...]
) -> None:
    before = snapshots[0][1]
    if any(snapshot.get("geometry") != before.get("geometry") for _, snapshot in snapshots):
        raise VerificationError("filesystem geometry changed across the campaign")

    mutable_inums = {ROOT_INODE}
    for label, snapshot in snapshots:
        root = names(snapshot)
        mutable_inums.update(
            root[path] for path in TARGET_PATHS if path in root
        )
    mutable_blocks: set[int] = set()
    for label, snapshot in snapshots:
        mutable_blocks.update(blocks_for_inodes(snapshot, mutable_inums, label))

    before_raw = table(before, "inode_raw_sha256", "before")
    before_incarnations = table(before, "inode_incarnations", "before")
    for label, snapshot in snapshots[1:]:
        require_non_target_objects(before, snapshot, label)
        raw = table(snapshot, "inode_raw_sha256", label)
        incarnations = table(snapshot, "inode_incarnations", label)
        if set(raw) != set(before_raw) or set(incarnations) != set(before_incarnations):
            raise VerificationError(f"{label}: inode table inventory changed")
        for key, value in before_raw.items():
            if int(key) not in mutable_inums and raw.get(key) != value:
                raise VerificationError(f"{label}: unrelated inode {key} changed")
        for key, value in before_incarnations.items():
            if int(key) not in mutable_inums and incarnations.get(key) != value:
                raise VerificationError(
                    f"{label}: unrelated inode incarnation {key} changed"
                )

        for key in ("block_sha256", "nonzero_data_block_sha256"):
            reference_blocks = table(before, key, "before")
            candidate_blocks = table(snapshot, key, label)
            for block in set(reference_blocks) | set(candidate_blocks):
                if int(block) in mutable_blocks:
                    continue
                if reference_blocks.get(block) != candidate_blocks.get(block):
                    raise VerificationError(
                        f"{label}: non-target block {block} changed"
                    )

        before_allocated = integer_set(before, "allocated_blocks", "before")
        candidate_allocated = integer_set(snapshot, "allocated_blocks", label)
        if (before_allocated ^ candidate_allocated) - mutable_blocks:
            raise VerificationError(f"{label}: unrelated allocation state changed")
        for key in ("owned_blocks", "qmap_entries"):
            reference = table(before, key, "before")
            candidate = table(snapshot, key, label)
            for block in set(reference) | set(candidate):
                if int(block) not in mutable_blocks and reference.get(block) != candidate.get(block):
                    raise VerificationError(
                        f"{label}: unrelated block owner {block} changed"
                    )
        reference_owners = table(before, "inode_owner_entries", "before")
        candidate_owners = table(snapshot, "inode_owner_entries", label)
        for inum in set(reference_owners) | set(candidate_owners):
            if int(inum) not in mutable_inums and reference_owners.get(inum) != candidate_owners.get(inum):
                raise VerificationError(
                    f"{label}: unrelated inode owner {inum} changed"
                )
        for key in (
            "orphan_inodes",
            "orphan_blocks",
            "allocated_unowned",
            "owner_without_bitmap",
        ):
            if snapshot.get(key) != before.get(key):
                raise VerificationError(f"{label}: {key} changed")


def baseline_namespace(before: dict[str, object]) -> dict[str, int]:
    root = names(before)
    if BATCH_PATH not in root or STATE_PATH not in root or CREATED_PATH in root:
        raise VerificationError("before: target namespace is malformed")
    return root


def completed_namespace(
    before: dict[str, object], completed: dict[str, object], label: str
) -> dict[str, int]:
    root = baseline_namespace(before)
    actual = names(completed)
    if CREATED_PATH not in actual:
        raise VerificationError(f"{label}: committed object is absent")
    expected = dict(root)
    expected[CREATED_PATH] = actual[CREATED_PATH]
    require_names(completed, expected, label)
    return expected


def probe_inflight(
    before: dict[str, object], fault: dict[str, object]
) -> dict[str, object]:
    for label, snapshot in (("before", before), ("fault", fault)):
        require_snapshot_envelope(snapshot, label)
        require_canonical(snapshot, label)
    if before.get("geometry") != fault.get("geometry"):
        raise VerificationError("inflight probe changed filesystem geometry")
    root = baseline_namespace(before)
    require_payload(before, BATCH_PATH, BATCH_OLD_HASH, "before")
    require_absent(before, CREATED_PATH, "before")
    require_state(before, b"P", "before")
    require_state(fault, b"R", "fault")
    require_non_target_objects(before, fault, "fault")

    fault_names = names(fault)
    if CREATED_PATH in fault_names:
        expected = dict(root)
        expected[CREATED_PATH] = fault_names[CREATED_PATH]
        require_names(fault, expected, "fault")
        require_payload(fault, BATCH_PATH, BATCH_NEW_HASH, "fault")
        require_payload(fault, CREATED_PATH, CREATED_NEW_HASH, "fault")
    else:
        require_names(fault, root, "fault")
    old_blocks, new_blocks = block_counts(before, fault)
    result = {
        "format": "agentos-fs-epoch-inflight-probe-v1",
        "selected": old_blocks != 0 and new_blocks != 0 and CREATED_PATH not in fault_names,
        "fault_old_blocks": old_blocks,
        "fault_new_blocks": new_blocks,
        "namespace_published": CREATED_PATH in fault_names,
    }
    if not result["selected"]:
        raise InflightWindowMiss(result)
    return result


def verify(
    case: str,
    before: dict[str, object],
    fault: dict[str, object],
    retry: dict[str, object],
    final: dict[str, object],
    *,
    calibration_attempt: int | None = None,
    calibration_delay: str | None = None,
) -> dict[str, object]:
    if case not in CASES:
        raise VerificationError(f"unsupported case {case!r}")
    snapshots = (
        ("before", before),
        ("fault", fault),
        ("retry", retry),
        ("final", final),
    )
    for label, snapshot in snapshots:
        require_snapshot_envelope(snapshot, label)
        require_canonical(snapshot, label)
    require_global_preservation(snapshots)
    base_names = baseline_namespace(before)
    completed_names = completed_namespace(before, retry, "retry")
    require_names(final, completed_names, "final")
    for path in (BATCH_PATH, STATE_PATH):
        if names(retry)[path] != base_names[path]:
            raise VerificationError(f"retry: {path} inode identity changed")
    for label, snapshot in (("fault", fault), ("retry", retry), ("final", final)):
        for path in (BATCH_PATH, STATE_PATH):
            if names(snapshot).get(path) != base_names[path]:
                raise VerificationError(f"{label}: {path} inode identity changed")
    require_payload(before, BATCH_PATH, BATCH_OLD_HASH, "before")
    require_absent(before, CREATED_PATH, "before")
    require_state(before, b"P", "before")
    require_state(fault, b"R", "fault")

    old_blocks = 0
    new_blocks = 0
    if case == "dirty":
        require_names(fault, base_names, "fault")
        require_payload(fault, BATCH_PATH, BATCH_OLD_HASH, "fault")
        require_absent(fault, CREATED_PATH, "fault")
        old_blocks = BATCH_BLOCKS
    elif case == "inflight":
        require_names(fault, base_names, "fault")
        require_absent(fault, CREATED_PATH, "fault")
        old_blocks, new_blocks = block_counts(before, fault)
        if old_blocks == 0 or new_blocks == 0:
            raise VerificationError(
                "inflight: delayed cut did not land inside the payload flush"
            )
    else:
        require_names(fault, completed_names, "fault")
        require_payload(fault, BATCH_PATH, BATCH_NEW_HASH, "fault")
        require_payload(fault, CREATED_PATH, CREATED_NEW_HASH, "fault")
        new_blocks = BATCH_BLOCKS

    for label, snapshot in (("retry", retry), ("final", final)):
        require_payload(snapshot, BATCH_PATH, BATCH_NEW_HASH, label)
        require_payload(snapshot, CREATED_PATH, CREATED_NEW_HASH, label)
        require_state(snapshot, b"D", label)
    if names(retry) != names(final):
        raise VerificationError("final verification boot changed the namespace")
    for path in (BATCH_PATH, CREATED_PATH, STATE_PATH):
        if payload_hash(retry, path) != payload_hash(final, path):
            raise VerificationError(f"final verification boot changed {path}")

    result: dict[str, object] = {
        "format": "agentos-fs-epoch-powercut-v1",
        "case": case,
        "batch_blocks": BATCH_BLOCKS,
        "fault_old_blocks": old_blocks,
        "fault_new_blocks": new_blocks,
        "fault_canonical_violations": len(fault["canonical_violations"]),
        "same_epoch_eio_retry": "not-covered-no-authorized-normal-io-fault-hook",
        "retry_state_sha256": retry.get("state_sha256"),
        "final_state_sha256": final.get("state_sha256"),
    }
    if case == "inflight":
        if calibration_attempt is not None:
            if calibration_attempt <= 0:
                raise VerificationError("inflight calibration attempt is invalid")
            result["calibration_attempt"] = calibration_attempt
        if calibration_delay is not None:
            result["calibration_delay"] = calibration_delay
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=CASES)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--fault", required=True, type=Path)
    parser.add_argument("--retry", type=Path)
    parser.add_argument("--final", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--probe-inflight", action="store_true")
    parser.add_argument("--calibration-attempt", type=int)
    parser.add_argument("--calibration-delay")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        before = load_snapshot(args.before)
        fault = load_snapshot(args.fault)
        if args.probe_inflight:
            if args.case != "inflight" or args.retry is not None or args.final is not None:
                raise VerificationError("inflight probe accepts only before and fault")
            result = probe_inflight(before, fault)
        else:
            if args.retry is None or args.final is None:
                raise VerificationError("full verification requires retry and final")
            result = verify(
                args.case,
                before,
                fault,
                load_snapshot(args.retry),
                load_snapshot(args.final),
                calibration_attempt=args.calibration_attempt,
                calibration_delay=args.calibration_delay,
            )
    except InflightWindowMiss as error:
        rendered = json.dumps(error.result, indent=2, sort_keys=True) + "\n"
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            args.output.write_text(rendered, encoding="utf-8")
        return INFLIGHT_WINDOW_MISS
    except VerificationError as error:
        print(f"fs-epoch-image: {error}", file=sys.stderr)
        return 1
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
