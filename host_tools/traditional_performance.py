#!/usr/bin/env python3
"""Build-bound, paired performance evidence for traditional uCore interfaces."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

try:
    from . import contest_demo
    from . import plain_ucore_fs_extract as fs_extract
    from .committed_source_identity import committed_source_path_sample
    from .evidence_delivery_contract import DeliveryContractError
except ImportError:
    import contest_demo
    import plain_ucore_fs_extract as fs_extract
    from committed_source_identity import committed_source_path_sample
    from evidence_delivery_contract import DeliveryContractError


KIND = "agentos-traditional-performance-campaign"
MANIFEST_KIND = "agentos-traditional-performance-build-manifest"
SCHEMA_VERSION = 1
ATTESTATION_FORMAT = "agentos-qemu-execution-attestation-v2"
EVIDENCE_SCOPE = "local_e3_unsigned"
TARGETS = ("agentos", "baseline")
WORKLOADS = (
    "cache_read_4k",
    "open_close",
    "tiny_write_fsync",
    "fork_wait",
    "warm_exec",
)
COUNTERS = (
    "open_calls",
    "close_calls",
    "read_calls",
    "write_calls",
    "bytes_read",
    "bytes_written",
    "fork_calls",
    "exec_calls",
    "wait_calls",
    "durability_barriers",
)
MECHANISM_COUNTERS = (
    "file_auth_full",
    "file_auth_lease_hits",
    "file_auth_revalidations",
)
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
UINT = r"(?:0|[1-9][0-9]*)"
UINT64_MAX = (1 << 64) - 1
FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
MAX_JSON_BYTES = 8 << 20
MAX_LOG_BYTES = 32 << 20
MAX_FS_BYTES = 256 << 20

# This is the causal surface of the experiment, not a hand-picked benchmark
# binary alone.  Every listed worktree byte must equal its immutable Git blob.
SOURCE_PATHS = (
    "Makefile",
    "nfs/Makefile",
    "user/Makefile",
    "evaluation_guest/traditional_perf.c",
    "evaluation_guest/traditional_execprobe.c",
    "user/lib/syscall.c",
    "user/lib/syscall_ids.h",
    "user/include/exec_policy_manifest.h",
    "baseline_ucore/Makefile",
    "baseline_ucore/nfs/Makefile",
    "baseline_ucore/user/Makefile",
    "baseline_ucore/user/lib/syscall.c",
    "baseline_ucore/user/lib/syscall_ids.h",
    "os/syscall.c",
    "os/fs.c",
    "os/fs.h",
    "os/file.c",
    "os/proc.c",
    "os/vm.c",
    "os/bio.c",
    "os/fs_epoch.c",
    "os/virtio_disk.c",
    "os/agent_metadata_store.c",
    "os/performance_stats.c",
    "baseline_ucore/os/syscall.c",
    "baseline_ucore/os/fs.c",
    "baseline_ucore/os/file.c",
    "baseline_ucore/os/proc.c",
    "baseline_ucore/os/vm.c",
    "baseline_ucore/os/bio.c",
    "baseline_ucore/os/virtio_disk.c",
    "scripts/run-traditional-performance.sh",
    "scripts/agent_test_runner.py",
    "scripts/guest_failure_classifier.py",
    "scripts/trusted-python-entry.py",
    "host_tools/traditional_performance.py",
    "host_tools/contest_demo.py",
    "host_tools/committed_source_identity.py",
    "host_tools/plain_ucore_fs_extract.py",
    "host_tools/evidence_delivery_contract.py",
    "host_tools/safe_host_paths.py",
)

BEGIN_RE = re.compile(
    rf"agentos:tradperf schema=1 nonce=({UINT}) sample=({UINT}) "
    rf"target=(agentos|baseline) order_slot=([12]) phase=begin tick_unit=ms\Z"
)
METRIC_RE = re.compile(
    rf"agentos:tradperf schema=1 nonce=({UINT}) sample=({UINT}) "
    rf"target=(agentos|baseline) workload=([a-z0-9_]+) "
    rf"duration_us=({UINT}) duration_ticks=({UINT}) ops=({UINT}) "
    rf"outcome_hash=({UINT}) open_calls=({UINT}) close_calls=({UINT}) "
    rf"read_calls=({UINT}) write_calls=({UINT}) bytes_read=({UINT}) "
    rf"bytes_written=({UINT}) fork_calls=({UINT}) exec_calls=({UINT}) "
    rf"wait_calls=({UINT}) durability_barriers=({UINT}) "
    rf"file_auth_full=({UINT}) file_auth_lease_hits=({UINT}) "
    rf"file_auth_revalidations=({UINT}) "
    rf"barrier_kind=(none|fsync|sync_write_completion)\Z"
)
END_RE = re.compile(
    rf"agentos:tradperf schema=1 nonce=({UINT}) sample=({UINT}) "
    rf"target=(agentos|baseline) phase=end aggregate_hash=({UINT})\Z"
)

PROFILES = {
    "cache_read_4k": {
        "ops": 256, "read_calls": 256, "bytes_read": 1_048_576,
        "barrier_kind": {"agentos": "none", "baseline": "none"},
    },
    "open_close": {
        "ops": 256, "open_calls": 256, "close_calls": 256,
        "barrier_kind": {"agentos": "none", "baseline": "none"},
    },
    "tiny_write_fsync": {
        "ops": 128, "write_calls": 128, "bytes_written": 2048,
        "durability_barriers": {"agentos": 8, "baseline": 128},
        "barrier_kind": {"agentos": "fsync", "baseline": "sync_write_completion"},
    },
    "fork_wait": {
        "ops": 32, "fork_calls": 32, "wait_calls": 32,
        "barrier_kind": {"agentos": "none", "baseline": "none"},
    },
    "warm_exec": {
        "ops": 16, "fork_calls": 16, "exec_calls": 16, "wait_calls": 16,
        "barrier_kind": {"agentos": "none", "baseline": "none"},
    },
}


class TraditionalPerformanceError(RuntimeError):
    """Raised when measurement evidence is incomplete or mutable."""


def _exact(value: object, keys: Iterable[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise TraditionalPerformanceError(f"{label} has an invalid shape")
    return value


def _uint(value: object, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TraditionalPerformanceError(f"{label} must be an integer")
    if value < (1 if positive else 0) or value > UINT64_MAX:
        raise TraditionalPerformanceError(f"{label} is outside uint64")
    return value


def _text(value: object, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or (pattern and not pattern.fullmatch(value)):
        raise TraditionalPerformanceError(f"{label} is invalid")
    return value


def _json_bytes(payload: bytes, label: str) -> Any:
    if not payload or len(payload) > MAX_JSON_BYTES:
        raise TraditionalPerformanceError(f"{label} has an invalid size")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise TraditionalPerformanceError(f"{label} has a duplicate key")
            result[key] = value
        return result

    def constant(_value: str) -> None:
        raise TraditionalPerformanceError(f"{label} contains a non-finite number")

    try:
        return json.loads(payload.decode("utf-8", errors="strict"),
                          object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TraditionalPerformanceError(f"{label} is not strict JSON") from error


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def _stat_key(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _snapshot(path: Path, label: str, *, capture: bool = False,
              maximum: int = MAX_FS_BYTES) -> dict[str, Any]:
    try:
        if path.is_symlink():
            raise TraditionalPerformanceError(f"{label} must not be a symlink")
        before = path.stat(follow_symlinks=False)
        if not path.is_file() or before.st_size <= 0 or before.st_size > maximum:
            raise TraditionalPerformanceError(f"{label} is missing or outside its byte budget")
        digest = hashlib.sha256()
        chunks: list[bytes] | None = [] if capture else None
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if _stat_key(opened) != _stat_key(before):
                raise TraditionalPerformanceError(f"{label} changed while opening")
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
                if chunks is not None:
                    chunks.append(chunk)
            after_read = os.fstat(handle.fileno())
        after = path.stat(follow_symlinks=False)
    except OSError as error:
        raise TraditionalPerformanceError(f"cannot read {label}: {error}") from error
    identity = _stat_key(before)
    if _stat_key(after_read) != identity or _stat_key(after) != identity:
        raise TraditionalPerformanceError(f"{label} changed while hashing")
    return {
        "path": path.resolve(),
        "bytes": before.st_size,
        "sha256": digest.hexdigest(),
        "identity": identity,
        "payload": b"".join(chunks) if chunks is not None else None,
    }


def _receipt(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {"bytes": snapshot["bytes"], "sha256": snapshot["sha256"]}


def _assert_stable(snapshot: dict[str, Any], label: str) -> None:
    current = _snapshot(snapshot["path"], label)
    if any(current[key] != snapshot[key] for key in ("bytes", "sha256", "identity")):
        raise TraditionalPerformanceError(f"{label} changed during validation")


def _resolve_tool(requested: str, label: str) -> Path:
    candidate = shutil.which(requested)
    if candidate is None and Path(requested).is_absolute():
        candidate = requested
    if candidate is None:
        raise TraditionalPerformanceError(f"cannot resolve {label}")
    path = Path(candidate).resolve()
    _snapshot(path, label, maximum=512 << 20)
    return path


def _tool_identity(requested: str, label: str) -> dict[str, Any]:
    path = _resolve_tool(requested, label)
    snap = _snapshot(path, label, maximum=512 << 20)
    try:
        result = subprocess.run(
            [str(path), "--version"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise TraditionalPerformanceError(f"cannot inspect {label} version") from error
    raw = result.stdout.strip() or result.stderr.strip()
    try:
        first = raw.decode("utf-8", errors="strict").splitlines()[0]
    except (UnicodeDecodeError, IndexError) as error:
        raise TraditionalPerformanceError(f"{label} version is invalid") from error
    if result.returncode or not first:
        raise TraditionalPerformanceError(f"{label} version command failed")
    _assert_stable(snap, label)
    return {
        "requested_path": requested,
        "executable": {"path": str(path), **_receipt(snap)},
        "version_argv": [str(path), "--version"],
        "version_first_line": first,
    }


def _objcopy_name(compiler: str) -> str:
    path = Path(compiler)
    suffix = ".exe" if path.name.lower().endswith(".exe") else ""
    stem = path.name[:-4] if suffix else path.name
    if not stem.endswith("gcc"):
        raise TraditionalPerformanceError("toolchain compiler name does not end in gcc")
    return str(path.with_name(stem[:-3] + "objcopy" + suffix))


def _source_sample(root: Path, commit: str) -> tuple[tuple[str, int, str, str], ...]:
    try:
        return committed_source_path_sample("git", root, commit, SOURCE_PATHS)
    except DeliveryContractError as error:
        raise TraditionalPerformanceError(f"source receipt is not commit-bound: {error}") from error


def _source_receipt(sample: tuple[tuple[str, int, str, str], ...]) -> list[dict[str, Any]]:
    return [
        {"path": path, "bytes": size, "sha256": digest, "git_oid": oid}
        for path, size, digest, oid in sample
    ]


def identity(root: Path) -> str:
    try:
        return contest_demo.clean_source_identity(root)
    except contest_demo.ContestDemoError as error:
        raise TraditionalPerformanceError(str(error)) from error


def _expected_artifacts(samples: int) -> tuple[str, ...]:
    paths = ["artifacts/agentos/kernel", "artifacts/baseline/kernel",
             "artifacts/build-agentos.log", "artifacts/build-baseline.log"]
    for sample in range(1, samples + 1):
        for target in TARGETS:
            prefix = f"artifacts/pair-{sample:02d}/{target}"
            paths.extend(
                f"{prefix}-{suffix}"
                for suffix in ("fs.img", "guest.bin", "guest.elf", "exec.bin", "exec.elf")
            )
    return tuple(paths)


def _walk_files(root: Path) -> set[str]:
    if root.is_symlink() or not root.is_dir():
        raise TraditionalPerformanceError(f"artifact directory is unsafe: {root}")
    result: set[str] = set()
    for base, directories, files in os.walk(root, followlinks=False):
        base_path = Path(base)
        for name in directories:
            if (base_path / name).is_symlink():
                raise TraditionalPerformanceError("artifact tree contains a symlink")
        for name in files:
            path = base_path / name
            if path.is_symlink() or not path.is_file():
                raise TraditionalPerformanceError("artifact tree contains a special file")
            result.add(path.relative_to(root.parent).as_posix())
    return result


def _extract_root_file(image: bytes, name: str) -> bytes:
    try:
        sb = fs_extract.read_superblock(image)
        rows = fs_extract.root_entries(image, sb)
        matches = [inum for inum, entry in rows if entry == name]
        if len(matches) != 1:
            raise TraditionalPerformanceError(f"filesystem has no unique {name} image")
        inode = fs_extract.read_inode(image, sb, matches[0])
        data = fs_extract.read_file(image, inode)
        if inode.type != fs_extract.T_FILE or inode.size <= 0 or len(data) != inode.size:
            raise TraditionalPerformanceError(f"filesystem {name} inode is invalid")
        return data
    except (IndexError, ValueError) as error:
        raise TraditionalPerformanceError(f"cannot inspect filesystem program {name}") from error


def _objcopy_binary(objcopy: str, elf: Path) -> bytes:
    elf = elf.resolve()
    try:
        elf_argument = str(elf.relative_to(Path.cwd().resolve()))
    except ValueError:
        elf_argument = str(elf)
    with tempfile.TemporaryDirectory(prefix="tradperf-objcopy-") as directory:
        output = Path(directory) / "program.bin"
        try:
            result = subprocess.run(
                [objcopy, "-O", "binary", elf_argument, str(output)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False, timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise TraditionalPerformanceError("objcopy validation could not run") from error
        if result.returncode:
            raise TraditionalPerformanceError("objcopy validation failed")
        return _snapshot(output, "objcopy output", capture=True, maximum=32 << 20)["payload"]


def _validate_program_chain(campaign: Path, samples: int, objcopy: str) -> None:
    for sample in range(1, samples + 1):
        for target in TARGETS:
            prefix = campaign / "artifacts" / f"pair-{sample:02d}" / target
            image = _snapshot(Path(f"{prefix}-fs.img"), "filesystem image",
                              capture=True)["payload"]
            for archive, guest_name in (("guest", "tradperf"), ("exec", "tradexec")):
                binary = _snapshot(Path(f"{prefix}-{archive}.bin"),
                                   "archived binary", capture=True,
                                   maximum=32 << 20)["payload"]
                rebuilt = _objcopy_binary(objcopy, Path(f"{prefix}-{archive}.elf"))
                if rebuilt != binary:
                    raise TraditionalPerformanceError("archived ELF does not reproduce its binary")
                if _extract_root_file(image, guest_name) != binary:
                    raise TraditionalPerformanceError("filesystem program differs from archived binary")


def prepare(root: Path, campaign: Path, run_id: str, round_nonce: str,
            samples: int, make_tool: str, qemu: str,
            toolchain_cc: str, build_jobs: int) -> str:
    root = root.resolve()
    campaign = campaign.resolve()
    if not HEX64.fullmatch(run_id) or not HEX64.fullmatch(round_nonce) or run_id == round_nonce:
        raise TraditionalPerformanceError("campaign nonces must be distinct 64-hex values")
    if samples < 8 or samples > 32 or samples % 2:
        raise TraditionalPerformanceError("sample count must be even and between 8 and 32")
    if build_jobs < 2 or build_jobs > 64:
        raise TraditionalPerformanceError("build job count is outside its bound")
    commit = identity(root)
    tree = contest_demo._run_git(root, "rev-parse", "--verify", f"{commit}^{{tree}}")
    if not HEX40.fullmatch(tree):
        raise TraditionalPerformanceError("source tree identity is invalid")
    source = _source_sample(root, commit)
    objcopy_requested = _objcopy_name(toolchain_cc)
    tools = {
        "make": _tool_identity(make_tool, "make"),
        "qemu": _tool_identity(qemu, "QEMU"),
        "toolchain_cc": _tool_identity(toolchain_cc, "toolchain compiler"),
        "toolchain_objcopy": _tool_identity(objcopy_requested, "toolchain objcopy"),
        "python": _tool_identity(sys.executable, "Python"),
    }
    expected = set(_expected_artifacts(samples))
    actual = _walk_files(campaign / "artifacts")
    if actual != expected:
        raise TraditionalPerformanceError(
            f"build artifact inventory differs: missing={sorted(expected-actual)} extra={sorted(actual-expected)}"
        )
    snapshots = {
        path: _snapshot(campaign / path, path, maximum=MAX_FS_BYTES)
        for path in sorted(expected)
    }
    _validate_program_chain(
        campaign, samples, tools["toolchain_objcopy"]["executable"]["path"]
    )
    guest_nonce = int(run_id[:16], 16)
    if guest_nonce == 0:
        raise TraditionalPerformanceError("Guest nonce must be nonzero")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": MANIFEST_KIND,
        "run_id": run_id,
        "round_nonce": round_nonce,
        "guest_nonce": guest_nonce,
        "samples": samples,
        "build_jobs": build_jobs,
        "source": {
            "commit": commit,
            "tree": tree,
            "receipt": _source_receipt(source),
        },
        "tools": tools,
        "design": {
            "pairing": "alternating_ab_ba",
            "qemu_slots": 1,
            "targets": list(TARGETS),
            "workloads": list(WORKLOADS),
            "clock": "guest_monotonic_us_with_ms_projection",
        },
        "artifacts": {path: _receipt(snapshots[path]) for path in sorted(snapshots)},
    }
    if identity(root) != commit or _source_sample(root, commit) != source:
        raise TraditionalPerformanceError("source changed while preparing evidence")
    for path, snapshot in snapshots.items():
        _assert_stable(snapshot, path)
    payload = _canonical_json(manifest)
    destination = campaign / "build-manifest.json"
    if destination.exists() or destination.is_symlink():
        raise TraditionalPerformanceError("build manifest already exists")
    contest_demo._atomic_write(destination, payload)
    digest = hashlib.sha256(payload).hexdigest()
    if contest_demo._sha256(destination) != digest:
        raise TraditionalPerformanceError("published build manifest differs")
    return digest


def _fnv_bytes(value: int, payload: bytes) -> int:
    for byte in payload:
        value ^= byte
        value = (value * FNV_PRIME) & UINT64_MAX
    return value


def _fnv_u64(value: int, number: int) -> int:
    return _fnv_bytes(value, number.to_bytes(8, "little"))


def _outcome_seed(nonce: int, workload: str) -> int:
    value = _fnv_bytes(FNV_OFFSET, b"agentos-tradperf-v1|")
    value = _fnv_u64(value, nonce)
    value = _fnv_bytes(value, b"|" + workload.encode("ascii") + b"|")
    return value


def _expected_outcome(metric: dict[str, Any], nonce: int) -> int:
    """Recompute deterministic results; payload rules are shared by both Guests."""
    value = _outcome_seed(nonce, metric["workload"])
    workload = metric["workload"]
    ops = metric["ops"]
    sample = metric["sample"]
    if workload == "open_close":
        for index in range(ops):
            value = _fnv_u64(value, index + 1)
    elif workload in {"fork_wait", "warm_exec"}:
        multiplier = 3 if workload == "fork_wait" else 7
        stride = 1 if workload == "fork_wait" else 17
        for index in range(ops):
            status = ((nonce >> ((index & 7) * 8)) ^ sample * multiplier ^ index * stride) & 0x3F
            value = _fnv_u64(value, status)
    elif workload == "cache_read_4k":
        block = bytes(
            ((nonce >> ((index & 7) * 8)) ^ sample * 29 ^ index * 131 ^ (index >> 3)) & 0xFF
            for index in range(4096)
        )
        for _ in range(ops):
            value = _fnv_bytes(value, block)
    elif workload == "tiny_write_fsync":
        payload = bytes(
            ((nonce >> ((index & 7) * 8)) ^ sample * 43 ^ index * 73 ^ (index >> 4)) & 0xFF
            for index in range(metric["bytes_written"])
        )
        value = _fnv_bytes(value, payload)
        value = _fnv_u64(value, 8)
    else:
        raise TraditionalPerformanceError("unknown workload in outcome receipt")
    return value


def _aggregate_hash(nonce: int, metrics: list[dict[str, Any]]) -> int:
    value = _fnv_bytes(FNV_OFFSET, b"agentos-tradperf-v1|")
    value = _fnv_u64(value, nonce)
    value = _fnv_bytes(value, b"|aggregate|")
    for metric in metrics:
        value = _fnv_u64(value, metric["outcome_hash"])
    return value


def parse_guest(payload: bytes, *, target: str, sample: int,
                nonce: int, order_slot: int) -> dict[str, Any]:
    try:
        lines = contest_demo._guest_lines(payload, f"{target} sample {sample} log")
    except contest_demo.ContestDemoError as error:
        raise TraditionalPerformanceError(str(error)) from error
    protocol = [line for line in lines if line.startswith("agentos:tradperf ")]
    if len(protocol) != len(WORKLOADS) + 2:
        raise TraditionalPerformanceError("Guest protocol record count is invalid")
    begin = BEGIN_RE.fullmatch(protocol[0])
    end = END_RE.fullmatch(protocol[-1])
    if begin is None or end is None:
        raise TraditionalPerformanceError("Guest begin/end records are malformed")
    expected_head = (nonce, sample, target, order_slot)
    begin_head = (int(begin[1]), int(begin[2]), begin[3], int(begin[4]))
    end_head = (int(end[1]), int(end[2]), end[3])
    if begin_head != expected_head or end_head != expected_head[:3]:
        raise TraditionalPerformanceError("Guest identity does not match its campaign slot")
    metrics: list[dict[str, Any]] = []
    for expected_name, line in zip(WORKLOADS, protocol[1:-1], strict=True):
        match = METRIC_RE.fullmatch(line)
        if match is None:
            raise TraditionalPerformanceError("Guest metric record is malformed")
        values = [int(match[index]) for index in range(1, 3)]
        metric: dict[str, Any] = {
            "nonce": values[0], "sample": values[1], "target": match[3],
            "workload": match[4], "duration_us": int(match[5]),
            "duration_ticks": int(match[6]), "ops": int(match[7]),
            "outcome_hash": int(match[8]),
        }
        for key, group in zip(COUNTERS, range(9, 19), strict=True):
            metric[key] = int(match[group])
        for key, group in zip(MECHANISM_COUNTERS, range(19, 22), strict=True):
            metric[key] = int(match[group])
        metric["barrier_kind"] = match[22]
        if any(value > UINT64_MAX for key, value in metric.items() if isinstance(value, int)):
            raise TraditionalPerformanceError("Guest metric exceeds uint64")
        if (metric["nonce"], metric["sample"], metric["target"], metric["workload"]) != (
            nonce, sample, target, expected_name
        ):
            raise TraditionalPerformanceError("Guest metric identity or order is invalid")
        profile = PROFILES[expected_name]
        expected_counters = {
            key: (raw[target] if isinstance(raw := profile.get(key, 0), dict) else raw)
            for key in COUNTERS
        }
        if (metric["duration_us"] == 0 or
                metric["duration_ticks"] != metric["duration_us"] // 1000 or
                metric["ops"] != profile["ops"] or
                any(metric[key] != value for key, value in expected_counters.items()) or
                metric["barrier_kind"] != profile["barrier_kind"][target]):
            raise TraditionalPerformanceError("Guest duration or operation count is invalid")
        if target == "baseline" and any(metric[key] != 0 for key in MECHANISM_COUNTERS):
            raise TraditionalPerformanceError("Baseline emitted AgentOS mechanism counters")
        if target == "agentos" and expected_name == "tiny_write_fsync" and (
                metric["file_auth_full"] == 0 or
                metric["file_auth_lease_hits"] + metric["file_auth_full"] !=
                metric["write_calls"]):
            raise TraditionalPerformanceError("Open-file authorization lease did not cover tiny writes")
        if metric["outcome_hash"] != _expected_outcome(metric, nonce):
            raise TraditionalPerformanceError("Guest outcome hash is not independently reproducible")
        metrics.append(metric)
    aggregate = int(end[4])
    if aggregate > UINT64_MAX or aggregate != _aggregate_hash(nonce, metrics):
        raise TraditionalPerformanceError("Guest aggregate outcome hash is invalid")
    marker_positions = [index for index, line in enumerate(lines) if line == "tradperf: complete"]
    protocol_end = max(index for index, line in enumerate(lines) if line == protocol[-1])
    if len(marker_positions) != 1 or marker_positions[0] <= protocol_end:
        raise TraditionalPerformanceError("Guest completion marker is missing or misplaced")
    return {"target": target, "sample": sample, "order_slot": order_slot,
            "aggregate_hash": aggregate, "metrics": metrics}


def _option(argv: list[str], name: str, *, default: str | None = None) -> str:
    positions = [index for index, value in enumerate(argv) if value == name]
    if not positions:
        if default is not None:
            return default
        raise TraditionalPerformanceError(f"runner invocation omits {name}")
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise TraditionalPerformanceError(f"runner invocation duplicates {name}")
    return argv[positions[0] + 1]


def _duration_seconds(raw: str, label: str) -> float:
    unit = raw[-1:]
    number = raw[:-1] if unit.isalpha() else raw
    multiplier = {"s": 1, "m": 60, "h": 3600}.get(unit.lower(), 1)
    if unit.isalpha() and unit.lower() not in {"s", "m", "h"}:
        raise TraditionalPerformanceError(f"{label} duration unit is invalid")
    try:
        value = float(number) * multiplier
    except ValueError as error:
        raise TraditionalPerformanceError(f"{label} duration is invalid") from error
    if not math.isfinite(value) or value < 0:
        raise TraditionalPerformanceError(f"{label} duration is invalid")
    return value


def _descriptor(value: object, label: str) -> dict[str, Any]:
    row = _exact(value, ("path", "bytes", "sha256"), label)
    _text(row["path"], f"{label}.path")
    _uint(row["bytes"], f"{label}.bytes", positive=True)
    _text(row["sha256"], f"{label}.sha256", HEX64)
    return row


def _path_from_argument(root: Path, raw: str) -> Path:
    path = Path(raw)
    return (path if path.is_absolute() else root / path).resolve()


def _validate_current_descriptor(value: object, expected_path: Path,
                                 label: str) -> dict[str, Any]:
    row = _descriptor(value, label)
    if Path(row["path"]).resolve() != expected_path.resolve():
        raise TraditionalPerformanceError(f"{label} path differs")
    snap = _snapshot(expected_path, label)
    if _receipt(snap) != {"bytes": row["bytes"], "sha256": row["sha256"]}:
        raise TraditionalPerformanceError(f"{label} bytes differ")
    return snap


def _validate_executable(value: object, expected: dict[str, Any], label: str) -> None:
    row = _exact(value, ("requested_path", "executable", "version_argv", "version_first_line"), label)
    if row != expected:
        raise TraditionalPerformanceError(f"{label} differs from build identity")
    _validate_current_descriptor(row["executable"], Path(row["executable"]["path"]), label)


def _validate_attestation(value: object, *, root: Path, path: Path,
                          manifest: dict[str, Any], plan_sha: str,
                          log_snap: dict[str, Any],
                          run_image: Path, kernel: Path,
                          input_image_receipt: dict[str, Any]
                          ) -> tuple[int, int, str, str, dict[str, Any]]:
    row = _exact(value, (
        "schema_version", "format", "evidence_scope", "source", "identity",
        "runner", "executables", "invocation_argv", "qemu_argv", "request",
        "inputs", "outputs", "time", "result", "run_id", "execution_id",
    ), "execution attestation")
    if row["schema_version"] != 2 or row["format"] != ATTESTATION_FORMAT or row["evidence_scope"] != EVIDENCE_SCOPE:
        raise TraditionalPerformanceError("execution attestation schema or scope differs")
    source = _exact(row["source"], ("commit", "tree", "calibration_plan_sha256"), "attestation source")
    if source != {"commit": manifest["source"]["commit"], "tree": manifest["source"]["tree"],
                  "calibration_plan_sha256": plan_sha}:
        raise TraditionalPerformanceError("attestation source binding differs")
    ident = _exact(row["identity"], ("campaign_nonce", "round_nonce", "session_nonce", "execution_nonce"), "attestation identity")
    if any(not isinstance(value, str) or not HEX64.fullmatch(value) for value in ident.values()):
        raise TraditionalPerformanceError("attestation nonce is invalid")
    if (ident["campaign_nonce"] != manifest["run_id"] or
            ident["round_nonce"] != manifest["round_nonce"] or
            row["run_id"] != ident["session_nonce"] or
            row["execution_id"] != ident["execution_nonce"] or len(set(ident.values())) != 4):
        raise TraditionalPerformanceError("attestation nonce binding differs")
    _validate_current_descriptor(row["runner"], root / "scripts/agent_test_runner.py", "runner source")
    executables = _exact(row["executables"], ("qemu", "toolchain_cc", "python"), "attestation tools")
    for name in executables:
        _validate_executable(executables[name], manifest["tools"][name], f"attestation tool {name}")
    argv = row["invocation_argv"]
    qemu_argv = row["qemu_argv"]
    if not isinstance(argv, list) or any(not isinstance(item, str) or not item for item in argv):
        raise TraditionalPerformanceError("runner argv is invalid")
    if (len(argv) < 2 or
            Path(argv[0]).resolve() != Path(executables["python"]["executable"]["path"]).resolve() or
            _path_from_argument(root, argv[1]) != (root / "scripts/agent_test_runner.py").resolve()):
        raise TraditionalPerformanceError("runner program identity differs")
    expected = {
        "--init-proc": "tradperf", "--marker": "tradperf: complete",
        "--marker-mode": "exact-line", "--qemu": manifest["tools"]["qemu"]["requested_path"],
        "--kernel": str(kernel), "--image": str(run_image),
        "--log-file": str(log_snap["path"]), "--attestation-file": str(path),
        "--run-id": ident["session_nonce"], "--execution-id": ident["execution_nonce"],
        "--evidence-scope": EVIDENCE_SCOPE, "--source-commit": source["commit"],
        "--source-tree": source["tree"], "--campaign-nonce": ident["campaign_nonce"],
        "--calibration-plan-sha256": plan_sha, "--round-nonce": ident["round_nonce"],
        "--session-nonce": ident["session_nonce"], "--execution-nonce": ident["execution_nonce"],
        "--toolchain-cc": manifest["tools"]["toolchain_cc"]["requested_path"],
    }
    timing_options = {
        "--case-timeout": "case_timeout_seconds",
        "--idle-notice-seconds": "idle_notice_seconds",
        "--marker-grace-seconds": "marker_grace_seconds",
    }
    tail = argv[2:]
    if (len(tail) % 2 or any(not tail[index].startswith("--")
                             for index in range(0, len(tail), 2))):
        raise TraditionalPerformanceError("runner option framing differs")
    option_names = tail[::2]
    allowed = set(expected) | set(timing_options)
    if len(option_names) != len(set(option_names)) or set(option_names) != allowed:
        raise TraditionalPerformanceError("runner option inventory differs")
    for option, expected_value in expected.items():
        actual = _option(argv, option)
        if option in {"--kernel", "--image", "--log-file", "--attestation-file"}:
            if _path_from_argument(root, actual) != Path(expected_value).resolve():
                raise TraditionalPerformanceError(f"runner {option} path differs")
        elif actual != expected_value:
            raise TraditionalPerformanceError(f"runner {option} differs")
    if "--timing-file" in argv or _option(argv, "--completion-mode", default="natural") != "natural":
        raise TraditionalPerformanceError("formal runner uses a non-natural completion")
    request = _exact(row["request"], (
        "init_proc", "marker", "marker_mode", "expected_bad_addr_markers",
        "expected_fault_marker_mode", "completion_mode", "case_timeout_seconds",
        "idle_notice_seconds", "marker_grace_seconds",
    ), "attestation request")
    if (request["init_proc"], request["marker"], request["marker_mode"],
        request["expected_bad_addr_markers"], request["expected_fault_marker_mode"],
        request["completion_mode"]) != (
        "tradperf", "tradperf: complete", "exact-line", [], "exact-line", "natural"):
        raise TraditionalPerformanceError("attestation request differs")
    for option, field in timing_options.items():
        recorded = request[field]
        if (isinstance(recorded, bool) or not isinstance(recorded, (int, float)) or
                not math.isclose(recorded, _duration_seconds(_option(argv, option), option),
                                 rel_tol=0, abs_tol=1e-12)):
            raise TraditionalPerformanceError(f"attestation {field} differs")
    inputs = _exact(row["inputs"], ("kernel", "image"), "attestation inputs")
    outputs = _exact(row["outputs"], ("kernel", "image", "log"), "attestation outputs")
    kernel_snap = _validate_current_descriptor(inputs["kernel"], kernel, "attested kernel input")
    if inputs["kernel"] != outputs["kernel"] or _receipt(kernel_snap) != manifest["artifacts"][kernel.relative_to(manifest["_campaign"]).as_posix()]:
        raise TraditionalPerformanceError("attested kernel is not the built artifact")
    input_desc = _descriptor(inputs["image"], "attested image input")
    if {"bytes": input_desc["bytes"], "sha256": input_desc["sha256"]} != input_image_receipt:
        raise TraditionalPerformanceError("attested input image is not its immutable build image")
    if Path(input_desc["path"]).resolve() != run_image.resolve():
        raise TraditionalPerformanceError("attested input image path differs")
    output_image = _validate_current_descriptor(outputs["image"], run_image, "attested image output")
    if output_image["bytes"] != input_desc["bytes"]:
        raise TraditionalPerformanceError("Guest changed filesystem image size")
    log_desc = _descriptor(outputs["log"], "attested Guest log")
    if (Path(log_desc["path"]).resolve() != log_snap["path"] or
            {"bytes": log_desc["bytes"], "sha256": log_desc["sha256"]} != _receipt(log_snap)):
        raise TraditionalPerformanceError("attested Guest log differs")
    timing = _exact(row["time"], (
        "clock", "started_monotonic_ns", "finished_monotonic_ns", "elapsed_ns",
        "started_wall_time_ns", "finished_wall_time_ns",
    ), "attestation time")
    start = _uint(timing["started_monotonic_ns"], "attestation start", positive=True)
    finish = _uint(timing["finished_monotonic_ns"], "attestation finish", positive=True)
    elapsed = _uint(timing["elapsed_ns"], "attestation elapsed", positive=True)
    wall_start = _uint(timing["started_wall_time_ns"], "attestation wall start", positive=True)
    wall_finish = _uint(timing["finished_wall_time_ns"], "attestation wall finish", positive=True)
    if (timing["clock"] != "time.monotonic_ns" or finish <= start or
            elapsed != finish - start or wall_finish <= wall_start):
        raise TraditionalPerformanceError("attestation monotonic interval is invalid")
    result = _exact(row["result"], (
        "succeeded", "reason", "returncode", "supervisor_returncode", "signals_sent",
        "output_eof", "expected_faults_satisfied", "process_tree_gone",
        "process_tree_contained", "completion_signal_attested",
        "control_endpoint_restored", "supervisor_control_healthy", "elapsed_seconds",
    ), "attestation result")
    expected_elapsed = round(elapsed / 1_000_000_000, 9)
    if (result["succeeded"] is not True or result["reason"] != "process_exit" or
            result["returncode"] != 0 or result["supervisor_returncode"] is not None or
            result["signals_sent"] != [] or result["output_eof"] is not True or
            result["expected_faults_satisfied"] is not True or
            result["process_tree_gone"] is not True or
            not isinstance(result["process_tree_contained"], bool) or
            result["completion_signal_attested"] is not False or
            result["control_endpoint_restored"] is not True or
            result["supervisor_control_healthy"] is not True or
            isinstance(result["elapsed_seconds"], bool) or
            not isinstance(result["elapsed_seconds"], (int, float)) or
            not math.isclose(result["elapsed_seconds"], expected_elapsed,
                             rel_tol=0, abs_tol=5e-10)):
        raise TraditionalPerformanceError("attested QEMU execution did not finish naturally")
    expected_qemu = [
        executables["qemu"]["executable"]["path"], "-nographic", "-machine", "virt",
        "-bios", "default", "-kernel", _option(argv, "--kernel"), "-drive",
        f"file={_option(argv, '--image')},if=none,format=raw,id=x0", "-device",
        "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
    ]
    if qemu_argv != expected_qemu:
        raise TraditionalPerformanceError("attested QEMU command differs")
    return (start, finish, ident["session_nonce"], ident["execution_nonce"],
            output_image)


def _manifest(campaign: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    path = campaign / "build-manifest.json"
    snap = _snapshot(path, "build manifest", capture=True, maximum=MAX_JSON_BYTES)
    value = _exact(_json_bytes(snap["payload"], "build manifest"), (
        "schema_version", "kind", "run_id", "round_nonce", "guest_nonce",
        "samples", "build_jobs", "source", "tools", "design", "artifacts",
    ), "build manifest")
    if value["schema_version"] != SCHEMA_VERSION or value["kind"] != MANIFEST_KIND:
        raise TraditionalPerformanceError("build manifest version differs")
    if contest_demo._sha256(path) != snap["sha256"]:
        raise TraditionalPerformanceError("build manifest changed after sampling")
    value["_campaign"] = campaign
    return value, snap, snap["sha256"]


def _validate_manifest(root: Path, campaign: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    run_id = _text(manifest["run_id"], "run id", HEX64)
    round_nonce = _text(manifest["round_nonce"], "round nonce", HEX64)
    samples = _uint(manifest["samples"], "samples", positive=True)
    build_jobs = _uint(manifest["build_jobs"], "build jobs", positive=True)
    if (samples < 8 or samples > 32 or samples % 2 or
            build_jobs < 2 or build_jobs > 64 or run_id == round_nonce or
            manifest["guest_nonce"] == 0 or
            manifest["guest_nonce"] != int(run_id[:16], 16)):
        raise TraditionalPerformanceError("manifest sample or Guest nonce policy differs")
    design = _exact(manifest["design"], ("pairing", "qemu_slots", "targets", "workloads", "clock"), "campaign design")
    if design != {"pairing": "alternating_ab_ba", "qemu_slots": 1,
                  "targets": list(TARGETS), "workloads": list(WORKLOADS),
                  "clock": "guest_monotonic_us_with_ms_projection"}:
        raise TraditionalPerformanceError("campaign design differs")
    commit = identity(root)
    source = _exact(manifest["source"], ("commit", "tree", "receipt"), "source receipt")
    tree = contest_demo._run_git(root, "rev-parse", "--verify", f"{commit}^{{tree}}")
    actual_sample = _source_sample(root, commit)
    if source != {"commit": commit, "tree": tree, "receipt": _source_receipt(actual_sample)}:
        raise TraditionalPerformanceError("manifest source receipt differs from Git blobs")
    tools = _exact(manifest["tools"], ("make", "qemu", "toolchain_cc", "toolchain_objcopy", "python"), "manifest tools")
    for name, recorded in tools.items():
        current = _tool_identity(recorded.get("requested_path", "") if isinstance(recorded, dict) else "", name)
        if current != recorded:
            raise TraditionalPerformanceError(f"manifest tool {name} changed")
    expected = set(_expected_artifacts(samples))
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != expected or _walk_files(campaign / "artifacts") != expected:
        raise TraditionalPerformanceError("manifest artifact inventory differs")
    snapshots: dict[str, dict[str, Any]] = {}
    for path in sorted(expected):
        record = _exact(artifacts[path], ("bytes", "sha256"), f"artifact receipt {path}")
        snap = _snapshot(campaign / path, path)
        if _receipt(snap) != record:
            raise TraditionalPerformanceError(f"artifact receipt differs for {path}")
        snapshots[path] = snap
    _validate_program_chain(campaign, samples, tools["toolchain_objcopy"]["executable"]["path"])
    return snapshots


def _percentile(values: list[int], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return float(ordered[index])


def _summarize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for workload in WORKLOADS:
        by_target = {
            target: [next(metric for metric in record[target]["metrics"] if metric["workload"] == workload)
                     for record in records]
            for target in TARGETS
        }
        agent = by_target["agentos"]
        base = by_target["baseline"]
        deltas = [left["duration_us"] - right["duration_us"] for left, right in zip(agent, base, strict=True)]
        ratios = [left["duration_us"] / right["duration_us"] for left, right in zip(agent, base, strict=True)]
        target_rows: dict[str, Any] = {}
        for target, metrics in by_target.items():
            target_rows[target] = {
                "duration_us_p50": statistics.median(item["duration_us"] for item in metrics),
                "duration_us_p95": _percentile([item["duration_us"] for item in metrics], 0.95),
                "ops": metrics[0]["ops"],
                "logical_io": {key: statistics.median(item[key] for item in metrics) for key in COUNTERS},
                "mechanism": {key: statistics.median(item[key] for item in metrics)
                              for key in MECHANISM_COUNTERS},
                "barrier_kind": sorted({item["barrier_kind"] for item in metrics}),
            }
        rows.append({
            "workload": workload,
            "targets": target_rows,
            "paired_delta_us_p50": statistics.median(deltas),
            "paired_delta_us_p95": _percentile(deltas, 0.95),
            "agentos_to_baseline_ratio_p50": round(statistics.median(ratios), 6),
            "agentos_to_baseline_ratio_p95": round(_percentile(ratios, 0.95), 6),
            "inference": "empirical_quantile_no_inference",
        })
    return rows


def _validate_pair(pair: dict[str, Any]) -> None:
    if pair["agentos"]["aggregate_hash"] != pair["baseline"]["aggregate_hash"]:
        raise TraditionalPerformanceError("paired targets produced different aggregate outcomes")
    for left, right in zip(
        pair["agentos"]["metrics"], pair["baseline"]["metrics"], strict=True
    ):
        if (
            left["workload"] != right["workload"]
            or left["ops"] != right["ops"]
            or left["outcome_hash"] != right["outcome_hash"]
        ):
            raise TraditionalPerformanceError(
                "paired target result hash or operation count differs"
            )


def _validate_boot_order(
    intervals: list[tuple[int, int, int, int, str]], samples: int
) -> list[tuple[int, int, int, int, str]]:
    expected: list[tuple[int, int, str]] = []
    for sample in range(1, samples + 1):
        first = "agentos" if sample % 2 else "baseline"
        second = "baseline" if first == "agentos" else "agentos"
        expected.extend(((sample, 1, first), (sample, 2, second)))
    actual = sorted(intervals, key=lambda item: item[1])
    if (len(actual) != samples * 2 or
            [(sample, slot, target) for sample, _, _, slot, target in actual] != expected):
        raise TraditionalPerformanceError("attested boot order is not alternating AB/BA")
    if any(actual[index][2] > actual[index + 1][1]
           for index in range(len(actual) - 1)):
        raise TraditionalPerformanceError("attested QEMU intervals overlap")
    return actual


def _claim_execution_identities(identities: set[str], session: str,
                                execution: str) -> None:
    if session in identities or execution in identities or session == execution:
        raise TraditionalPerformanceError("execution identities are reused")
    identities.update((session, execution))


def _validate_output_location(campaign: Path, output: Path) -> None:
    reserved = (campaign / "artifacts", campaign / "runs")
    if (output == campaign or campaign.is_relative_to(output) or
            any(output == path or output.is_relative_to(path) for path in reserved)):
        raise TraditionalPerformanceError("report output overlaps campaign evidence")


def _csv(report: dict[str, Any]) -> bytes:
    import io
    output = io.StringIO(newline="")
    fields = ["pair", "workload", "agentos_order_slot", "baseline_order_slot",
              "agentos_duration_us", "baseline_duration_us", "paired_delta_us",
              "agentos_baseline_ratio", "agentos_p50_us", "agentos_p95_us",
              "baseline_p50_us", "baseline_p95_us", "paired_delta_p50_us",
              "paired_delta_p95_us", "ratio_p50", "ratio_p95", "ops",
              "outcome_hash", "agentos_barrier_kind", "baseline_barrier_kind",
              *[f"agentos_{key}" for key in COUNTERS + MECHANISM_COUNTERS],
              *[f"baseline_{key}" for key in COUNTERS + MECHANISM_COUNTERS]]
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    summaries = {row["workload"]: row for row in report["workloads"]}
    for pair in report["pairs"]:
        agent_metrics = {row["workload"]: row for row in pair["agentos"]["metrics"]}
        base_metrics = {row["workload"]: row for row in pair["baseline"]["metrics"]}
        for workload in WORKLOADS:
            item, agent, base = summaries[workload], agent_metrics[workload], base_metrics[workload]
            agent_summary, base_summary = item["targets"]["agentos"], item["targets"]["baseline"]
            row: dict[str, Any] = {
                "pair": pair["sample"], "workload": workload,
                "agentos_order_slot": pair["agentos"]["order_slot"],
                "baseline_order_slot": pair["baseline"]["order_slot"],
                "agentos_duration_us": agent["duration_us"],
                "baseline_duration_us": base["duration_us"],
                "paired_delta_us": agent["duration_us"] - base["duration_us"],
                "agentos_baseline_ratio": round(agent["duration_us"] / base["duration_us"], 6),
                "agentos_p50_us": agent_summary["duration_us_p50"],
                "agentos_p95_us": agent_summary["duration_us_p95"],
                "baseline_p50_us": base_summary["duration_us_p50"],
                "baseline_p95_us": base_summary["duration_us_p95"],
                "paired_delta_p50_us": item["paired_delta_us_p50"],
                "paired_delta_p95_us": item["paired_delta_us_p95"],
                "ratio_p50": item["agentos_to_baseline_ratio_p50"],
                "ratio_p95": item["agentos_to_baseline_ratio_p95"],
                "ops": agent["ops"], "outcome_hash": agent["outcome_hash"],
                "agentos_barrier_kind": agent["barrier_kind"],
                "baseline_barrier_kind": base["barrier_kind"],
            }
            row.update({f"agentos_{key}": agent[key]
                        for key in COUNTERS + MECHANISM_COUNTERS})
            row.update({f"baseline_{key}": base[key]
                        for key in COUNTERS + MECHANISM_COUNTERS})
            writer.writerow(row)
    return output.getvalue().encode("utf-8")


def _html(report: dict[str, Any]) -> bytes:
    cards: list[str] = []
    for row in report["workloads"]:
        agent = row["targets"]["agentos"]
        base = row["targets"]["baseline"]
        maximum = max(agent["duration_us_p50"], base["duration_us_p50"], 1)
        counts = " · ".join(
            f"{html.escape(key)} {agent['logical_io'][key]:g}/{base['logical_io'][key]:g}"
            for key in ("open_calls", "read_calls", "write_calls", "fork_calls", "exec_calls", "durability_barriers")
        )
        auth = agent["mechanism"]
        auth_counts = (f"完整授权 {auth['file_auth_full']:g} · "
                       f"租约命中 {auth['file_auth_lease_hits']:g} · "
                       f"失效重验 {auth['file_auth_revalidations']:g}")
        cards.append(f"""
        <article class="workload">
          <header><h2>{html.escape(row['workload'])}</h2><strong>{row['agentos_to_baseline_ratio_p50']:.3f}x</strong></header>
          <div class="bar-row"><span>AgentOS</span><div class="bar"><i class="agent" style="width:{100*agent['duration_us_p50']/maximum:.2f}%"></i></div><b>{agent['duration_us_p50']:g} us</b></div>
          <div class="bar-row"><span>Baseline</span><div class="bar"><i class="base" style="width:{100*base['duration_us_p50']/maximum:.2f}%"></i></div><b>{base['duration_us_p50']:g} us</b></div>
          <dl><div><dt>AgentOS p95</dt><dd>{agent['duration_us_p95']:g} us</dd></div><div><dt>Baseline p95</dt><dd>{base['duration_us_p95']:g} us</dd></div><div><dt>配对差值 p50 / p95</dt><dd>{row['paired_delta_us_p50']:+g} / {row['paired_delta_us_p95']:+g} us</dd></div><div><dt>耗时比 p50 / p95</dt><dd>{row['agentos_to_baseline_ratio_p50']:.3f} / {row['agentos_to_baseline_ratio_p95']:.3f}x</dd></div></dl>
          <p class="counts">逻辑计数 AgentOS/Baseline: {counts}</p>
          <p class="counts">AgentOS 文件授权: {auth_counts}</p>
        </article>""")
    payload = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentOS 传统接口性能矩阵</title><style>
:root{{--ink:#18211d;--muted:#68736d;--line:#d9dfdb;--paper:#f7f9f7;--agent:#087f5b;--base:#ef8354;--blue:#3567a8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,"Microsoft YaHei",sans-serif;letter-spacing:0}}
main{{max-width:1160px;margin:auto;padding:38px 24px 60px}}.mast{{border-top:5px solid var(--agent);border-bottom:1px solid var(--line);padding:24px 0 20px;margin-bottom:24px}}
h1{{font-size:34px;margin:0 0 8px}}.mast p,.counts{{color:var(--muted)}}.facts{{display:flex;gap:28px;flex-wrap:wrap;margin-top:18px}}.facts b{{display:block;font-size:22px;color:var(--blue)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}}.workload{{background:white;border:1px solid var(--line);border-radius:6px;padding:20px}}
.workload header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}}h2{{font-size:18px;margin:0}}header strong{{font-size:22px;color:var(--agent)}}
.bar-row{{display:grid;grid-template-columns:70px 1fr 90px;gap:10px;align-items:center;margin:9px 0}}.bar{{height:10px;background:#edf0ee}}.bar i{{display:block;height:100%}}.agent{{background:var(--agent)}}.base{{background:var(--base)}}.bar-row b{{text-align:right;font-variant-numeric:tabular-nums}}
dl{{display:grid;grid-template-columns:1fr 1fr;border-top:1px solid var(--line);margin:18px 0 0;padding-top:12px;gap:10px}}dl div{{min-width:0}}dt{{font-size:12px;color:var(--muted)}}dd{{margin:2px 0;font-weight:650;font-variant-numeric:tabular-nums}}.counts{{font-size:12px;overflow-wrap:anywhere}}
footer{{margin-top:24px;border-top:1px solid var(--line);padding-top:14px;color:var(--muted);font-size:12px}}code{{color:var(--blue)}}
@media(max-width:560px){{main{{padding:22px 14px}}h1{{font-size:27px}}.grid{{grid-template-columns:1fr}}.bar-row{{grid-template-columns:64px 1fr 78px}}}}
</style></head><body><main><section class="mast"><h1>传统接口性能矩阵</h1><p>同一应用请求、独立内核、{report['campaign']['pairs']} 组 AB/BA 单槽 QEMU 实测。数字来自 Guest 单调时钟与逻辑 I/O 计数；经验分位数不作总体推断。tiny write 在 AgentOS 形成 8 次 fsync，在 Baseline 形成 128 次同步写完成，结果字节和 8 个逻辑批次保持等价。</p><div class="facts"><div><b>{report['campaign']['pairs']}</b>配对样本</div><div><b>{report['campaign']['boots']}</b>独立启动</div><div><b>5</b>传统工作量</div><div><b>1</b>QEMU 槽</div></div></section><section class="grid">{''.join(cards)}</section><footer>Commit <code>{html.escape(report['source']['commit'])}</code> · Build manifest <code>{html.escape(report['campaign']['build_manifest_sha256'])}</code><br>本地 E3 证据绑定提交、工具与产物；正式脚本可复现构建，不声明独立重构建证明。</footer></main></body></html>"""
    return payload.encode("utf-8")


def render(root: Path, campaign: Path, output: Path) -> dict[str, Any]:
    root, campaign, output = root.resolve(), campaign.resolve(), output.resolve()
    _validate_output_location(campaign, output)
    manifest, manifest_snap, plan_sha = _manifest(campaign)
    build_snapshots = _validate_manifest(root, campaign, manifest)
    samples = manifest["samples"]
    nonce = manifest["guest_nonce"]
    records: list[dict[str, Any]] = []
    runtime_snapshots: dict[str, dict[str, Any]] = {}
    intervals: list[tuple[int, int, int, int, str]] = []
    identities: set[str] = set()
    for sample in range(1, samples + 1):
        pair: dict[str, Any] = {}
        expected_first = "agentos" if sample % 2 else "baseline"
        for target in TARGETS:
            run_dir = campaign / "runs" / f"pair-{sample:02d}"
            log_path = run_dir / f"{target}.log"
            attestation_path = run_dir / f"{target}.attestation.json"
            run_image = run_dir / f"{target}-run.img"
            log_snap = _snapshot(log_path, "Guest log", capture=True, maximum=MAX_LOG_BYTES)
            att_snap = _snapshot(attestation_path, "execution attestation", capture=True,
                                 maximum=MAX_JSON_BYTES)
            runtime_snapshots[f"runs/pair-{sample:02d}/{target}.log"] = log_snap
            runtime_snapshots[f"runs/pair-{sample:02d}/{target}.attestation.json"] = att_snap
            slot = 1 if target == expected_first else 2
            parsed = parse_guest(log_snap["payload"], target=target, sample=sample,
                                 nonce=nonce, order_slot=slot)
            artifact_prefix = f"artifacts/pair-{sample:02d}/{target}"
            kernel_path = campaign / "artifacts" / target / "kernel"
            attested = _validate_attestation(
                _json_bytes(att_snap["payload"], "execution attestation"),
                root=root, path=attestation_path.resolve(), manifest=manifest,
                plan_sha=plan_sha, log_snap=log_snap,
                run_image=run_image.resolve(), kernel=kernel_path.resolve(),
                input_image_receipt=manifest["artifacts"][f"{artifact_prefix}-fs.img"],
            )
            start, finish, session, execution, run_snap = attested
            runtime_snapshots[f"runs/pair-{sample:02d}/{target}-run.img"] = run_snap
            _claim_execution_identities(identities, session, execution)
            intervals.append((sample, start, finish, slot, target))
            pair[target] = parsed
        _validate_pair(pair)
        records.append(pair)
    actual_intervals = _validate_boot_order(intervals, samples)
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "campaign": {"run_id": manifest["run_id"], "pairs": samples,
                     "boots": samples * 2, "build_jobs": manifest["build_jobs"],
                     "qemu_slots": 1, "build_manifest_sha256": plan_sha,
                     "order": "AB/BA alternating"},
        "source": manifest["source"],
        "workloads": _summarize(records),
        "pairs": [
            {"sample": index, "agentos": pair["agentos"], "baseline": pair["baseline"]}
            for index, pair in enumerate(records, 1)
        ],
        "evidence": {
            "build_manifest": _receipt(manifest_snap),
            "build_artifacts": {path: _receipt(snap) for path, snap in sorted(build_snapshots.items())},
            "runtime_artifacts": {path: _receipt(snap) for path, snap in sorted(runtime_snapshots.items())},
            "guest_clock": "monotonic microseconds with a canonical millisecond projection",
            "host_order_clock": "time.monotonic_ns",
            "boot_order": [
                {"pair": sample, "order_slot": slot, "target": target,
                 "started_monotonic_ns": start, "finished_monotonic_ns": finish}
                for sample, start, finish, slot, target in actual_intervals
            ],
            "provenance_scope": (
                "local_e3_unsigned commit, tool, and artifact binding; "
                "the tracked clean-build entrypoint is reproducible but is not "
                "an independent rebuild attestation"
            ),
        },
    }
    # Re-sample every input immediately before publication. Outputs never become
    # evidence for bytes that were not also parsed through the same open handle.
    if identity(root) != manifest["source"]["commit"] or _source_receipt(_source_sample(root, manifest["source"]["commit"])) != manifest["source"]["receipt"]:
        raise TraditionalPerformanceError("source changed during rendering")
    _assert_stable(manifest_snap, "build manifest")
    for path, snap in {**build_snapshots, **runtime_snapshots}.items():
        _assert_stable(snap, path)
    output.mkdir(parents=True, exist_ok=True)
    contest_demo._atomic_write(output / "traditional-performance.json", _canonical_json(report))
    contest_demo._atomic_write(output / "traditional-performance.csv", _csv(report))
    contest_demo._atomic_write(output / "index.html", _html(report))
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    identify = commands.add_parser("identity")
    identify.add_argument("--root", required=True, type=Path)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--root", required=True, type=Path)
    prepare_parser.add_argument("--campaign-dir", required=True, type=Path)
    prepare_parser.add_argument("--run-id", required=True)
    prepare_parser.add_argument("--round-nonce", required=True)
    prepare_parser.add_argument("--samples", required=True, type=int)
    prepare_parser.add_argument("--make-tool", required=True)
    prepare_parser.add_argument("--qemu", required=True)
    prepare_parser.add_argument("--toolchain-cc", required=True)
    prepare_parser.add_argument("--build-jobs", required=True, type=int)
    render_parser = commands.add_parser("render")
    render_parser.add_argument("--root", required=True, type=Path)
    render_parser.add_argument("--campaign-dir", required=True, type=Path)
    render_parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "identity":
            print(identity(args.root))
        elif args.command == "prepare":
            print(prepare(args.root, args.campaign_dir, args.run_id,
                          args.round_nonce, args.samples, args.make_tool,
                          args.qemu, args.toolchain_cc, args.build_jobs))
        else:
            render(args.root, args.campaign_dir, args.output_dir)
    except (OSError, ValueError, TraditionalPerformanceError) as error:
        print(f"traditional_performance: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
