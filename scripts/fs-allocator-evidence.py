#!/usr/bin/env python3
"""Build and verify the complete filesystem allocator fault evidence package.

The package contract is intentionally closed: all 36 cases, their exact files,
the volatile-cache backend identity, flush receipts, and the delete-FLUSH
negative mutation result must be present.  The generated manifest is a
deterministic function of those inputs and binds every artifact by size and
SHA-256.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any


def _load_guest_failure_classifier() -> Any:
    path = Path(__file__).with_name("guest_failure_classifier.py")
    spec = importlib.util.spec_from_file_location(
        "_agentos_guest_failure_classifier", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Guest failure classifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GUEST_FAILURE_CLASSIFIER = _load_guest_failure_classifier()


MANIFEST_FORMAT = "agentos-fs-allocator-evidence-manifest-v1"
BACKEND_FORMAT = "agentos-fs-allocator-backend-v1"
MUTATION_FORMAT = "agentos-fs-allocator-flush-mutation-v1"
RECEIPT_FORMAT = "agentos-fs-allocator-flush-receipt-v1"
RUN_FORMAT = "agentos-fs-allocator-run-attestation-v1"
EXECUTION_FORMAT = "agentos-qemu-execution-attestation-v1"
BUILD_FORMAT = "agentos-fs-allocator-case-build-v1"
TRANSCRIPT_FORMAT = "agentos-fs-allocator-run-transcript-v1"
SEAL_FORMAT = "agentos-fs-allocator-run-seal-v1"
SNAPSHOT_FORMAT = "agentos-fs-allocator-v2"
VERIFIED_FORMAT = "agentos-fs-allocator-case-v2"
DIFF_FORMAT = "agentos-fs-allocator-diff-v2"
CANONICAL_FORMAT = "agentos-fs-allocator-canonical-v2"
SUITE = "fs-allocator-fault"
SCHEMA_VERSION = 1
BACKEND_MODEL = "deterministic-volatile-write-cache"
DELETE_FLUSH_MUTATION = "delete-flush"
PROFILE_BUILD_FLAG = "FS_ALLOCATOR_FAULT_TEST_PROFILE=1"
MUTANT_BUILD_FLAG = "FS_ALLOCATOR_DELETE_BARRIER_MUTANT=1"
MUTATION_REJECTION_CODE = "FS_ALLOCATOR_IMAGE_INVALID"
MUTATION_REJECTION_MESSAGE = (
    "('alloc', 'intent', 'crash') qmap transition count []"
)
GENERATOR_NAME = "fs-allocator-image.py"
WORKLOAD_SOURCE_NAME = "fsallocfault_ucore"
WORKLOAD_IMAGE_NAME = WORKLOAD_SOURCE_NAME[:14]
ARCHIVE_BASENAME = "fs-allocator-evidence.tar"
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_RAW_IMAGE_BYTES = 16 * 1024 * 1024
RUN_ID = re.compile(r"[0-9a-f]{64}\Z")
GIT_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")

# Formal allocator children receive only platform plumbing plus deterministic
# locale/Git policy.  In particular, Python startup hooks, Make include flags,
# compiler search paths, and every caller-supplied FS_* knob are absent.
CONTROLLED_ENV_PASSTHROUGH = frozenset(
    {
        "HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "SYSTEMDRIVE",
        "USERPROFILE",
        "MSYSTEM",
    }
)
CONTROLLED_ENV_FIXED = {
    "PATH": "/usr/bin:/bin",
    "LC_ALL": "C",
    "LANG": "C",
    "TZ": "UTC",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "ASAN_OPTIONS": "detect_leaks=0:halt_on_error=1",
    "UBSAN_OPTIONS": "halt_on_error=1",
}

ROOT_BUILD_SOURCE_PATHS = (
    "Makefile",
    "agent_lifecycle_abi.h",
    "agent_metadata_disk_abi.h",
    "agent_metadata_test_abi.h",
    "agent_observe_abi.h",
    "agent_observe_test_phase_abi.h",
    "agent_resource_abi.h",
    "agent_tool_abi.h",
    "exec_image_policy.h",
    "file_resource_policy.h",
    "fs_allocator_test_abi.h",
    "fs_storage_policy.h",
    "io_policy.h",
    "kernel_work_abi.h",
    "physical_page_policy.h",
    "physical_page_test_abi.h",
    "thread_resource_policy.h",
    "user_stack_policy.h",
    "virtio_test_abi.h",
    "wait_atomic_test_abi.h",
)

# The profile kernel is selected from Makefile's os/*.c and os/*.S wildcards.
# Snapshot the complete checked-in input set, including inactive test owners,
# rather than trying to maintain a fragile hand-picked allocator call graph.
KERNEL_BUILD_SOURCE_PATHS = (
    "os/agent_background.c",
    "os/agent_context_path.c",
    "os/agent_context_path.h",
    "os/agent_context.c",
    "os/agent_context.h",
    "os/agent_core.c",
    "os/agent_durable_section.c",
    "os/agent_durable_section.h",
    "os/agent_file_name_policy.h",
    "os/agent_file_state_internal.h",
    "os/agent_file_state.c",
    "os/agent_identity_lease.c",
    "os/agent_identity_lease.h",
    "os/agent_identity.c",
    "os/agent_internal.h",
    "os/agent_ipc.c",
    "os/agent_lifecycle.c",
    "os/agent_lifecycle.h",
    "os/agent_metadata_actions.c",
    "os/agent_metadata_actions.h",
    "os/agent_metadata_catalog.c",
    "os/agent_metadata_catalog.h",
    "os/agent_metadata_directory.c",
    "os/agent_metadata_directory.h",
    "os/agent_metadata_disk.h",
    "os/agent_metadata_internal.h",
    "os/agent_metadata_objects.c",
    "os/agent_metadata_prefetch.c",
    "os/agent_metadata_prefetch.h",
    "os/agent_metadata_probe.c",
    "os/agent_metadata_probe.h",
    "os/agent_metadata_query.c",
    "os/agent_metadata_query.h",
    "os/agent_metadata_recovery_test.c",
    "os/agent_metadata_recovery_test.h",
    "os/agent_metadata_recovery.c",
    "os/agent_metadata_recovery.h",
    "os/agent_metadata_scan.c",
    "os/agent_metadata_scan.h",
    "os/agent_metadata_store_format.c",
    "os/agent_metadata_store_format.h",
    "os/agent_metadata_store_io.c",
    "os/agent_metadata_store_io.h",
    "os/agent_metadata_store.c",
    "os/agent_metadata_test.c",
    "os/agent_metadata.c",
    "os/agent_observe_audit_query.c",
    "os/agent_observe_capacity.c",
    "os/agent_observe_capacity.h",
    "os/agent_observe_internal.h",
    "os/agent_observe_ledger.c",
    "os/agent_observe_persist_context.h",
    "os/agent_observe_recovery_store.h",
    "os/agent_observe_recovery.c",
    "os/agent_observe_recovery.h",
    "os/agent_observe_store.c",
    "os/agent_observe_store.h",
    "os/agent_observe_test.c",
    "os/agent_observe_test.h",
    "os/agent_observe_timeline.c",
    "os/agent_observe.c",
    "os/agent_resource.c",
    "os/agent_tool_protocol.c",
    "os/agent_tool_protocol.h",
    "os/agent.c",
    "os/agent.h",
    "os/bio.c",
    "os/bio.h",
    "os/console.c",
    "os/console.h",
    "os/const.h",
    "os/defs.h",
    "os/entry.S",
    "os/exec_policy.c",
    "os/exec_policy.h",
    "os/fcntl.h",
    "os/file.c",
    "os/file.h",
    "os/fs_allocator_test.c",
    "os/fs_allocator_test.h",
    "os/fs.c",
    "os/fs.h",
    "os/kalloc.c",
    "os/kalloc.h",
    "os/kernel_work.c",
    "os/kernel_work.h",
    "os/kernel.ld",
    "os/kernelvec.S",
    "os/loader.c",
    "os/loader.h",
    "os/log.h",
    "os/main.c",
    "os/metadata_crash_test.h",
    "os/physical_page_test.c",
    "os/physical_page_test.h",
    "os/pipe.c",
    "os/plic.c",
    "os/plic.h",
    "os/printf.c",
    "os/printf.h",
    "os/proc.c",
    "os/proc.h",
    "os/queue.c",
    "os/queue.h",
    "os/resource_controller.c",
    "os/resource_controller.h",
    "os/riscv.h",
    "os/sbi.c",
    "os/sbi.h",
    "os/string.c",
    "os/string.h",
    "os/switch.S",
    "os/sync.c",
    "os/sync.h",
    "os/syscall_ids.h",
    "os/syscall.c",
    "os/syscall.h",
    "os/timer.c",
    "os/timer.h",
    "os/trampoline.S",
    "os/trap.c",
    "os/trap.h",
    "os/types.h",
    "os/user_stack_layout.h",
    "os/vfs_security.c",
    "os/vfs_security.h",
    "os/virtio_disk.c",
    "os/virtio.h",
    "os/vm.c",
    "os/vm.h",
    "os/wait_atomic_test.c",
    "os/wait_atomic_test.h",
    "os/wait.c",
    "os/wait.h",
    "os/workflow_lifecycle.c",
    "os/workflow_lifecycle.h",
)

USER_BUILD_SOURCE_PATHS = (
    "user/Makefile",
    "user/src/fsallocfault_ucore.c",
    "user/include/agent_metadata_test_abi.h",
    "user/include/agent_observe_test_phase_abi.h",
    "user/include/agent.h",
    "user/include/exec_policy_manifest.h",
    "user/include/fcntl.h",
    "user/include/fs_allocator_test_abi.h",
    "user/include/io_policy.h",
    "user/include/kernel_work_abi.h",
    "user/include/physical_page_test_abi.h",
    "user/include/research_platform_state.h",
    "user/include/rp_evidence.h",
    "user/include/rp_launch_attestation.h",
    "user/include/rp_program_manifest.h",
    "user/include/rp_resource_stability.h",
    "user/include/stddef.h",
    "user/include/stdio.h",
    "user/include/stdlib.h",
    "user/include/string.h",
    "user/include/unistd.h",
    "user/include/user_stack_policy.h",
    "user/include/virtio_test_abi.h",
    "user/include/wait_atomic_test_abi.h",
    "user/lib/arch/riscv/crt.S",
    "user/lib/arch/riscv/syscall_arch.h",
    "user/lib/arch/riscv/syscall_ids.h.in",
    "user/lib/arch/riscv/user.ld",
    "user/lib/main.c",
    "user/lib/stdio.c",
    "user/lib/stdlib.c",
    "user/lib/string.c",
    "user/lib/syscall_ids.h",
    "user/lib/syscall.c",
    "user/lib/syscall.h",
)

MKFS_SOURCE_PATHS = (
    "nfs/Makefile",
    "nfs/elf_compat.h",
    "nfs/fs.c",
    "nfs/fs.h",
    "nfs/host_image_snapshot.c",
    "nfs/host_image_snapshot.h",
    "nfs/host_windows_compat.h",
    "nfs/types.h",
)

HOST_VERIFIER_SOURCE_PATHS = (
    ".gitattributes",
    "ci/agent-metadata-disk-format.json",
    "ci/agent-observe-disk-format.json",
    "host_tools/__init__.py",
    "host_tools/agent_metadata_disk_format.py",
    "host_tools/agent_observe_disk_acceptance.py",
    "host_tools/agent_observe_disk_contract.py",
    "host_tools/agent_observe_disk_evidence.py",
    "host_tools/plain_ucore_fs_extract.py",
    "host_tools/research_state_manifest.py",
    "scripts/agent_test_runner.py",
    "scripts/check-agent-metadata-disk-format.py",
    "scripts/check-agent-observe-disk-format.py",
    "scripts/check-fs-allocator-state.py",
    "scripts/check-kernel-stack-usage.py",
    "scripts/evidence-wiring.sh",
    "scripts/fs-allocator-evidence.py",
    "scripts/fs-allocator-image.py",
    "scripts/guest_failure_classifier.py",
    "scripts/host-probe-toolchain.sh",
    "scripts/host_probe_toolchain.py",
    "scripts/initproc.py",
    "scripts/probes/agent-metadata-disk-layout.c",
    "scripts/probes/agent-observe-disk-layout.c",
    "scripts/run-fs-allocator-fault-tests.sh",
    "scripts/test-fs-allocator-image.py",
    "scripts/trusted-python-entry.py",
)

SOURCE_PATHS = (
    ROOT_BUILD_SOURCE_PATHS
    + KERNEL_BUILD_SOURCE_PATHS
    + USER_BUILD_SOURCE_PATHS
    + MKFS_SOURCE_PATHS
    + HOST_VERIFIER_SOURCE_PATHS
)
if len(SOURCE_PATHS) != len(set(SOURCE_PATHS)):
    raise RuntimeError("filesystem allocator source inventory contains duplicates")
SOURCE_FILES = tuple(f"sources/{path}" for path in SOURCE_PATHS)


def _discover_guest_build_inputs(root: Path) -> set[str]:
    paths = {"Makefile"}
    paths.update(path.name for path in root.glob("*.h") if path.is_file())
    paths.update(
        path.relative_to(root).as_posix()
        for path in (root / "os").iterdir()
        if path.is_file()
        and path.name != "initproc.S"
        and path.suffix in {".c", ".h", ".S", ".ld"}
    )
    paths.update({"user/Makefile", "user/src/fsallocfault_ucore.c"})
    paths.update(
        path.relative_to(root).as_posix()
        for path in (root / "user" / "include").glob("*.h")
        if path.is_file()
    )
    paths.update(
        path.relative_to(root).as_posix()
        for path in (root / "user" / "lib").rglob("*")
        if path.is_file()
        and path.name.endswith((".c", ".h", ".S", ".ld", ".h.in"))
    )
    paths.add("nfs/Makefile")
    paths.update(
        path.relative_to(root).as_posix()
        for path in (root / "nfs").iterdir()
        if path.is_file() and path.suffix in {".c", ".h"}
    )
    return paths

STAGES = ("prepare", "fault", "reboot")
ACTIONS = ("busy", "eio", "crash")
OPERATION_PHASES = (
    ("alloc", ("intent", "bitmap", "owner")),
    ("free", ("intent", "bitmap", "owner", "refund")),
    ("ialloc", ("intent", "owner")),
    ("ifree", ("intent", "owner", "refund")),
)
CASES = tuple(
    (operation, phase, action)
    for operation, phases in OPERATION_PHASES
    for phase in phases
    for action in ACTIONS
)
CASE_IDS = tuple("-".join(case) for case in CASES)
OPERATION_IDS = {"alloc": 1, "free": 2, "ialloc": 3, "ifree": 4}
PHASE_IDS = {"intent": 1, "bitmap": 2, "owner": 3, "refund": 4}
ACTION_IDS = {"busy": 1, "eio": 2, "crash": 3}

ROOT_SOURCE_FILES = (
    "run.json",
    "run-seal.json",
    "runner-transcript.json",
    "backend.json",
    "profile.kernel",
    "flush-deletion-mutation.json",
    "flush-deletion-mutation.log",
    "flush-deletion-mutant.kernel",
    "flush-deletion-selection.diff",
    "flush-deletion-before.img.gz",
    "flush-deletion-fault.img.gz",
    "flush-deletion-reboot.img.gz",
    "flush-deletion-fault.guest.log",
    "flush-deletion-reboot.guest.log",
    "flush-deletion-fault.execution.json",
    "flush-deletion-reboot.execution.json",
)
CASE_FILES = (
    "build.json",
    "program.bin",
    "program.elf",
    "before.img.gz",
    "before.snapshot.json",
    "fault.img.gz",
    "fault.snapshot.json",
    "fault.diff.json",
    "reboot.img.gz",
    "reboot.snapshot.json",
    "reboot.canonical.json",
    "reboot.diff.json",
    "verified.json",
    "prepare.guest.log",
    "prepare.flush.json",
    "fault.guest.log",
    "fault.flush.json",
    "reboot.guest.log",
    "reboot.flush.json",
    "prepare.execution.json",
    "fault.execution.json",
    "reboot.execution.json",
)
SNAPSHOT_FIELDS = {
    "format",
    "geometry",
    "allocated_blocks",
    "owned_blocks",
    "qmap_entries",
    "qmap_state_counts",
    "qmap_top_state_counts",
    "canonical_violations",
    "allocated_unowned",
    "owner_without_bitmap",
    "inodes",
    "free_inode_owners",
    "inode_owner_entries",
    "inode_owner_state_counts",
    "root_names",
    "reachable_inodes",
    "reachable_blocks",
    "inode_blocks",
    "payload_sha256",
    "block_sha256",
    "orphan_inodes",
    "orphan_blocks",
    "image",
    "generator",
    "state_sha256",
}
SNAPSHOT_LIST_FIELDS = {
    "allocated_blocks",
    "canonical_violations",
    "allocated_unowned",
    "owner_without_bitmap",
    "reachable_inodes",
    "reachable_blocks",
    "orphan_inodes",
    "orphan_blocks",
}
SNAPSHOT_OBJECT_FIELDS = {
    "owned_blocks",
    "qmap_entries",
    "qmap_state_counts",
    "qmap_top_state_counts",
    "inodes",
    "free_inode_owners",
    "inode_owner_entries",
    "inode_owner_state_counts",
    "root_names",
    "inode_blocks",
    "payload_sha256",
    "block_sha256",
}

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
STABLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,255}\Z")


class EvidenceError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EvidenceError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise EvidenceError(f"non-finite JSON number: {value}")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise EvidenceError(f"{label} is unavailable: {error}") from error
    if size <= 0 or size > MAX_JSON_BYTES:
        raise EvidenceError(f"{label} size is outside the JSON evidence limit")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except EvidenceError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceError(f"{label} is invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be a JSON object")
    return value


def _read_canonical_json(path: Path, label: str) -> dict[str, Any]:
    value = _read_json(path, label)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise EvidenceError(f"{label} is unavailable: {error}") from error
    if raw != _render_json(value):
        raise EvidenceError(f"{label} is not canonical JSON")
    return value


def _is_link(path: Path) -> bool:
    junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(junction) and junction())


def _reject_links(root: Path) -> None:
    if _is_link(root):
        raise EvidenceError("evidence root must not be a symlink or junction")
    for directory, directories, files in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in directories + files:
            candidate = base / name
            if _is_link(candidate):
                relative = candidate.relative_to(root).as_posix()
                raise EvidenceError(f"evidence package contains a symlink: {relative}")


def _require_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise EvidenceError(
            f"{label} fields mismatch: missing={missing}, extra={extra}"
        )


def _require_text(value: object, label: str, *, stable_id: bool = False) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > 4096:
        raise EvidenceError(f"{label} must be a non-empty bounded string")
    if stable_id and not STABLE_ID.fullmatch(value):
        raise EvidenceError(f"{label} is not a stable identifier")
    return value


def _require_argv(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 256:
        raise EvidenceError(f"{label} must be a non-empty argv array")
    return [
        _require_text(item, f"{label}[{index}]") for index, item in enumerate(value)
    ]


def _normalize_profile_compile_argv(
    argv: list[str], label: str, *, mutant: bool, run: dict[str, Any] | None = None
) -> list[str]:
    """Bind profile builds while ignoring only their private output directory."""
    normalized: list[str] = []
    profile_count = 0
    mutant_count = 0
    builddir_count = 0
    for item in argv:
        if item.startswith("FS_ALLOCATOR_FAULT_TEST_PROFILE="):
            if item != PROFILE_BUILD_FLAG:
                raise EvidenceError(f"{label} selects an unsupported fault-test profile")
            profile_count += 1
            normalized.append(item)
        elif item.startswith("FS_ALLOCATOR_DELETE_BARRIER_MUTANT="):
            if item != MUTANT_BUILD_FLAG:
                raise EvidenceError(f"{label} selects an unsupported allocator mutant")
            mutant_count += 1
        elif item.startswith("BUILDDIR="):
            if item == "BUILDDIR=":
                raise EvidenceError(f"{label} has an empty build output directory")
            builddir_count += 1
            normalized.append("BUILDDIR=<OUTPUT>")
        else:
            normalized.append(item)
    if profile_count != 1:
        raise EvidenceError(f"{label} must select the fault-test profile exactly once")
    if builddir_count != 1:
        raise EvidenceError(f"{label} must select one private build output directory")
    expected_mutant_count = 1 if mutant else 0
    if mutant_count != expected_mutant_count:
        qualifier = "exactly once" if mutant else "not at all"
        raise EvidenceError(f"{label} must select the allocator mutant {qualifier}")
    if run is not None:
        tools = run["toolchain"]
        expected = [
            tools["make"]["resolved"],
            "build",
            "TOOLPREFIX=",
            f"CC={tools['cross_gcc']['resolved']}",
            f"AS={tools['cross_gcc']['resolved']}",
            f"LD={tools['cross_ld']['resolved']}",
            f"OBJCOPY={tools['cross_objcopy']['resolved']}",
            f"OBJDUMP={tools['cross_objdump']['resolved']}",
            "LOG=error",
            "BUILDDIR=<OUTPUT>",
            "INIT_PROC=fsallocfault_ucore",
            f"PYTHON_BIN={tools['python']['resolved']}",
            PROFILE_BUILD_FLAG,
        ]
        if normalized != expected:
            raise EvidenceError(f"{label} differs from the canonical profile build")
    return normalized


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise EvidenceError(f"{label} must be a lowercase SHA-256")
    return value


def _require_nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceError(f"{label} must be a non-negative integer")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _bytes_identity(raw: bytes) -> dict[str, Any]:
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def controlled_environment(source: dict[str, str]) -> dict[str, str]:
    """Return the complete environment admitted to a formal runner child."""

    environment = {
        name: value
        for name, value in source.items()
        if name in CONTROLLED_ENV_PASSTHROUGH and value
    }
    environment.update(CONTROLLED_ENV_FIXED)
    return environment


def clean_exec(argv: list[str]) -> None:
    if not argv or any(not isinstance(value, str) or not value for value in argv):
        raise EvidenceError("clean-exec requires a non-empty argv")
    environment = controlled_environment(dict(os.environ))
    try:
        if os.name == "posix":
            os.execvpe(argv[0], argv, environment)
        completed = subprocess.run(
            argv,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise EvidenceError(f"clean-exec could not start child: {error}") from error
    raise SystemExit(completed.returncode)


def _capture_command(
    command: list[str], cwd: Path, label: str, *, max_bytes: int = 1024 * 1024
) -> bytes:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise EvidenceError(f"could not inspect {label}: {error}") from error
    if completed.returncode != 0 or not completed.stdout or len(completed.stdout) > max_bytes:
        raise EvidenceError(f"{label} inspection failed with exit {completed.returncode}")
    return completed.stdout


def _git_source_state(source_root: Path, git: Path | None = None) -> tuple[str, str, bytes]:
    if git is None:
        git = _resolved_tool("git", source_root, "git")
    command = [
        str(git),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.hooksPath=/dev/null",
    ]
    commit = _capture_command(
        [*command, "rev-parse", "--verify", "HEAD"], source_root, "source commit"
    ).decode("ascii", errors="strict").strip()
    tree = _capture_command(
        [*command, "rev-parse", "--verify", "HEAD^{tree}"],
        source_root,
        "source tree",
    ).decode("ascii", errors="strict").strip()
    if GIT_OBJECT_ID.fullmatch(commit) is None or GIT_OBJECT_ID.fullmatch(tree) is None:
        raise EvidenceError("source commit or tree is not a full Git object id")
    try:
        status = subprocess.run(
            [
                *command,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
            cwd=source_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise EvidenceError(f"could not inspect source dirty state: {error}") from error
    if status.returncode != 0 or status.stderr or len(status.stdout) > MAX_JSON_BYTES:
        raise EvidenceError("source dirty-state inspection failed")
    return commit, tree, status.stdout


def _git_head_source_payloads(
    source_root: Path, git: Path, commit: str
) -> dict[str, bytes]:
    """Read the selected source closure from the captured commit, not the index."""

    command = [
        str(git),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.hooksPath=/dev/null",
    ]
    flags_raw = _capture_command(
        [*command, "ls-files", "-v", "-z", "--", *SOURCE_PATHS],
        source_root,
        "source index flags",
        max_bytes=MAX_JSON_BYTES,
    )
    flags: dict[str, str] = {}
    for record in flags_raw.rstrip(b"\0").split(b"\0"):
        try:
            flag, raw_path = record[:1].decode("ascii"), record[2:].decode("utf-8")
        except (UnicodeDecodeError, IndexError) as error:
            raise EvidenceError("source index flags are malformed") from error
        if len(record) < 3 or record[1:2] != b" " or raw_path in flags:
            raise EvidenceError("source index flags are malformed")
        flags[raw_path] = flag
    if set(flags) != set(SOURCE_PATHS):
        raise EvidenceError("source closure is not fully tracked by Git")
    unsafe = sorted(path for path, flag in flags.items() if flag != "H")
    if unsafe:
        raise EvidenceError(
            "source closure uses assume-unchanged/skip-worktree index flags: "
            + ", ".join(unsafe)
        )

    payloads: dict[str, bytes] = {}
    for relative in SOURCE_PATHS:
        listing = _capture_command(
            [*command, "ls-tree", "-z", commit, "--", relative],
            source_root,
            f"committed source entry {relative}",
            max_bytes=8192,
        )
        if not listing.endswith(b"\0") or listing.count(b"\0") != 1:
            raise EvidenceError(f"committed source entry is ambiguous: {relative}")
        try:
            metadata, raw_path = listing[:-1].split(b"\t", 1)
            mode, kind, raw_oid = metadata.split(b" ", 2)
            path = raw_path.decode("utf-8")
            oid = raw_oid.decode("ascii")
        except (ValueError, UnicodeDecodeError) as error:
            raise EvidenceError(f"committed source entry is malformed: {relative}") from error
        if (
            path != relative
            or mode not in {b"100644", b"100755"}
            or kind != b"blob"
            or GIT_OBJECT_ID.fullmatch(oid) is None
        ):
            raise EvidenceError(f"committed source entry is unsafe: {relative}")
        payloads[relative] = _capture_command(
            [*command, "cat-file", "blob", oid],
            source_root,
            f"committed source blob {relative}",
            max_bytes=MAX_ARTIFACT_BYTES,
        )
    return payloads


def _require_sources_match_commit(
    source_root: Path, git: Path, commit: str, payloads: dict[str, bytes]
) -> None:
    committed = _git_head_source_payloads(source_root, git, commit)
    if committed != {path.removeprefix("sources/"): raw for path, raw in payloads.items()}:
        raise EvidenceError("source snapshot bytes differ from the captured Git commit")


def _source_snapshot(
    source_root: Path, *, include_payloads: bool
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    payloads: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    for relative in SOURCE_PATHS:
        raw = _read_bounded_regular(
            source_root / relative, f"source snapshot {relative}", MAX_ARTIFACT_BYTES
        )
        packaged = f"sources/{relative}"
        if include_payloads:
            payloads[packaged] = raw
        records.append(
            {"source_path": relative, "artifact": {"path": packaged, **_bytes_identity(raw)}}
        )
    return records, payloads


def _require_guest_source_inventory(source_root: Path, label: str) -> None:
    declared = set(
        ROOT_BUILD_SOURCE_PATHS
        + KERNEL_BUILD_SOURCE_PATHS
        + USER_BUILD_SOURCE_PATHS
        + MKFS_SOURCE_PATHS
    )
    try:
        discovered = _discover_guest_build_inputs(source_root)
    except OSError as error:
        raise EvidenceError(f"could not enumerate {label} Guest build inputs: {error}") from error
    if discovered != declared:
        raise EvidenceError(
            f"{label} Guest build source inventory differs: "
            f"missing={sorted(declared - discovered)}, "
            f"extra={sorted(discovered - declared)}"
        )


def _resolved_tool(command: str, source_root: Path, label: str) -> Path:
    candidate = shutil.which(command)
    if candidate is None:
        explicit = Path(command)
        if not explicit.is_absolute():
            explicit = source_root / explicit
        candidate = str(explicit)
    try:
        resolved = Path(candidate).resolve(strict=True)
    except OSError as error:
        raise EvidenceError(f"{label} executable is unavailable: {error}") from error
    if _is_link(resolved) or not resolved.is_file():
        raise EvidenceError(f"{label} executable is not a regular file")
    return resolved


def _tool_attestation(
    source_root: Path, requested: str, version_args: list[str], label: str
) -> dict[str, Any]:
    executable = _resolved_tool(requested, source_root, label)
    raw = _read_bounded_regular(executable, f"{label} executable", MAX_ARTIFACT_BYTES)
    version = _capture_command([str(executable), *version_args], source_root, label)
    first_line = version.decode("utf-8", errors="replace").splitlines()[0]
    return {
        "requested": str(executable),
        "resolved": str(executable),
        "executable": _bytes_identity(raw),
        "version_argv": [str(executable), *version_args],
        "version_first_line": _require_text(first_line, f"{label} version"),
        "version_sha256": hashlib.sha256(version).hexdigest(),
    }


def capture_run(
    output: Path,
    source_root: Path,
    run_id: str,
    qemu: str,
    python: str,
    toolprefix: str | None,
    make: str = "make",
    host_cc: str | None = None,
    cross_gcc: str | None = None,
    cross_ld: str | None = None,
    cross_objcopy: str | None = None,
    cross_objdump: str | None = None,
) -> dict[str, Any]:
    if not RUN_ID.fullmatch(run_id):
        raise EvidenceError("run id must be 32 random bytes encoded as lowercase hex")
    if output.exists() or _is_link(output):
        raise EvidenceError("capture-run output must not already exist")
    try:
        source_root = source_root.resolve(strict=True)
    except OSError as error:
        raise EvidenceError(f"source root is unavailable: {error}") from error
    _require_guest_source_inventory(source_root, "live")
    git = _resolved_tool("git", source_root, "git")
    commit, tree, status = _git_source_state(source_root, git)
    if status:
        raise EvidenceError(
            "source tree must be clean before filesystem allocator evidence capture"
        )
    source_records, source_payloads = _source_snapshot(
        source_root, include_payloads=True
    )
    _require_sources_match_commit(source_root, git, commit, source_payloads)
    snapshot_sha256 = hashlib.sha256(_render_json(source_records)).hexdigest()
    commit_after, tree_after, status_after = _git_source_state(source_root, git)
    records_after, _ = _source_snapshot(source_root, include_payloads=False)
    _require_sources_match_commit(source_root, git, commit_after, source_payloads)
    if (
        (commit_after, tree_after, status_after) != (commit, tree, status)
        or records_after != source_records
    ):
        raise EvidenceError("source changed while filesystem allocator snapshot was captured")
    tools = {
        "python": _tool_attestation(source_root, python, ["--version"], "python"),
        "qemu": _tool_attestation(source_root, qemu, ["--version"], "qemu"),
        "make": _tool_attestation(source_root, make, ["--version"], "make"),
        "host_cc": _tool_attestation(
            source_root,
            host_cc or "cc",
            ["--version"],
            "host cc",
        ),
        "cross_gcc": _tool_attestation(
            source_root,
            cross_gcc or f"{toolprefix or ''}gcc",
            ["--version"],
            "cross gcc",
        ),
        "cross_ld": _tool_attestation(
            source_root,
            cross_ld or f"{toolprefix or ''}ld",
            ["--version"],
            "cross ld",
        ),
        "cross_objcopy": _tool_attestation(
            source_root,
            cross_objcopy or f"{toolprefix or ''}objcopy",
            ["--version"],
            "cross objcopy",
        ),
        "cross_objdump": _tool_attestation(
            source_root,
            cross_objdump or f"{toolprefix or ''}objdump",
            ["--version"],
            "cross objdump",
        ),
    }
    record = {
        "schema_version": SCHEMA_VERSION,
        "format": RUN_FORMAT,
        "run_id": run_id,
        "source": {
            "commit": commit,
            "tree": tree,
            "dirty": bool(status),
            "status_bytes": len(status),
            "status_sha256": hashlib.sha256(status).hexdigest(),
            "status_hex": status.hex(),
            "snapshot_sha256": snapshot_sha256,
        },
        "sources": source_records,
        "toolchain": tools,
    }
    output.mkdir(parents=True)
    for relative, raw in source_payloads.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_publish_bytes(destination, raw)
    _atomic_publish_bytes(output / "run.json", _render_json(record))
    return record


def _validate_run_record(root: Path, value: dict[str, Any]) -> dict[str, Any]:
    _require_fields(
        value,
        {"schema_version", "format", "run_id", "source", "sources", "toolchain"},
        "run attestation",
    )
    if value["schema_version"] != SCHEMA_VERSION or value["format"] != RUN_FORMAT:
        raise EvidenceError("run attestation version is unsupported")
    run_id = _require_text(value["run_id"], "run id")
    if not RUN_ID.fullmatch(run_id):
        raise EvidenceError("run id is not 32 random bytes encoded as lowercase hex")
    source = value["source"]
    if not isinstance(source, dict):
        raise EvidenceError("run source attestation must be an object")
    _require_fields(
        source,
        {
            "commit",
            "tree",
            "dirty",
            "status_bytes",
            "status_sha256",
            "status_hex",
            "snapshot_sha256",
        },
        "run source attestation",
    )
    if (
        not isinstance(source["commit"], str)
        or GIT_OBJECT_ID.fullmatch(source["commit"]) is None
        or not isinstance(source["tree"], str)
        or GIT_OBJECT_ID.fullmatch(source["tree"]) is None
    ):
        raise EvidenceError("run source commit or tree is not a full Git object id")
    if not isinstance(source["dirty"], bool):
        raise EvidenceError("run source dirty state must be boolean")
    status_bytes = _require_nonnegative_int(
        source["status_bytes"], "run source status_bytes"
    )
    try:
        status_raw = bytes.fromhex(source["status_hex"])
    except (TypeError, ValueError) as error:
        raise EvidenceError("run source status_hex is invalid") from error
    if (
        len(status_raw) != status_bytes
        or hashlib.sha256(status_raw).hexdigest()
        != _require_sha256(source["status_sha256"], "run source status hash")
        or source["dirty"] != bool(status_raw)
    ):
        raise EvidenceError("run source dirty-state attestation is inconsistent")
    if source["dirty"] or status_bytes != 0:
        raise EvidenceError("run source attestation is not from a clean tree")
    sources = value["sources"]
    if not isinstance(sources, list) or len(sources) != len(SOURCE_PATHS):
        raise EvidenceError("run source snapshot inventory is incomplete")
    for expected, item in zip(SOURCE_PATHS, sources, strict=True):
        if not isinstance(item, dict):
            raise EvidenceError("run source snapshot entry must be an object")
        _require_fields(item, {"source_path", "artifact"}, "run source entry")
        relative = f"sources/{expected}"
        if item["source_path"] != expected:
            raise EvidenceError("run source snapshot order or path differs")
        _validate_artifact_reference(
            item["artifact"], _artifact_record(root, relative), f"source {expected}"
        )
    if hashlib.sha256(_render_json(sources)).hexdigest() != _require_sha256(
        source["snapshot_sha256"], "run source snapshot hash"
    ):
        raise EvidenceError("run source snapshot hash differs")
    toolchain = value["toolchain"]
    expected_tools = {
        "python",
        "qemu",
        "make",
        "host_cc",
        "cross_gcc",
        "cross_ld",
        "cross_objcopy",
        "cross_objdump",
    }
    if not isinstance(toolchain, dict) or set(toolchain) != expected_tools:
        raise EvidenceError("run toolchain inventory is incomplete")
    for label, record in toolchain.items():
        if not isinstance(record, dict):
            raise EvidenceError(f"run tool {label} must be an object")
        _require_fields(
            record,
            {
                "requested",
                "resolved",
                "executable",
                "version_argv",
                "version_first_line",
                "version_sha256",
            },
            f"run tool {label}",
        )
        requested = _require_text(record["requested"], f"run tool {label} requested")
        resolved = _require_text(record["resolved"], f"run tool {label} resolved")
        normalized = resolved.replace("\\", "/")
        if (
            requested != resolved
            or not (
                normalized.startswith("/")
                or re.fullmatch(r"[A-Za-z]:/.*", normalized)
            )
        ):
            raise EvidenceError(f"run tool {label} is not bound to one absolute path")
        executable = record["executable"]
        if not isinstance(executable, dict):
            raise EvidenceError(f"run tool {label} executable identity is invalid")
        _require_fields(executable, {"bytes", "sha256"}, f"run tool {label} executable")
        if _require_nonnegative_int(executable["bytes"], f"run tool {label} bytes") == 0:
            raise EvidenceError(f"run tool {label} executable is empty")
        _require_sha256(executable["sha256"], f"run tool {label} executable hash")
        version_argv = _require_argv(
            record["version_argv"], f"run tool {label} version argv"
        )
        if version_argv != [resolved, "--version"]:
            raise EvidenceError(f"run tool {label} version argv is not canonical")
        _require_text(record["version_first_line"], f"run tool {label} version")
        _require_sha256(record["version_sha256"], f"run tool {label} version hash")
    return value


def _load_run(root: Path) -> dict[str, Any]:
    return _validate_run_record(
        root, _read_canonical_json(root / "run.json", "run attestation")
    )


def _verify_source_tree_against_run(
    source_root: Path, run: dict[str, Any], label: str, *, exact_tree: bool = False
) -> None:
    try:
        source_root = source_root.resolve(strict=True)
    except OSError as error:
        raise EvidenceError(f"{label} source root is unavailable: {error}") from error
    if _is_link(source_root) or not source_root.is_dir():
        raise EvidenceError(f"{label} source root is link-backed or not a directory")
    if exact_tree:
        expected_files = set(SOURCE_PATHS)
        expected_directories: set[str] = set()
        for relative in SOURCE_PATHS:
            parent = PurePosixPath(relative).parent
            while parent.parts:
                expected_directories.add(parent.as_posix())
                parent = parent.parent
        actual_files: set[str] = set()
        actual_directories: set[str] = set()
        for directory, directories, files in os.walk(source_root, followlinks=False):
            base = Path(directory)
            for name in directories:
                candidate = base / name
                if _is_link(candidate):
                    raise EvidenceError(
                        f"{label} source tree contains a linked directory"
                    )
                actual_directories.add(candidate.relative_to(source_root).as_posix())
            for name in files:
                candidate = base / name
                if _is_link(candidate):
                    raise EvidenceError(f"{label} source tree contains a linked file")
                actual_files.add(candidate.relative_to(source_root).as_posix())
        extra_files = actual_files - expected_files
        if extra_files - {"os/initproc.S"} or actual_directories != expected_directories:
            raise EvidenceError(
                f"{label} source tree entries differ from the captured closure"
            )
    _require_guest_source_inventory(source_root, label)
    records, _ = _source_snapshot(source_root, include_payloads=False)
    if records != run["sources"]:
        raise EvidenceError(f"{label} source snapshot differs from captured source")
    actual_hash = hashlib.sha256(_render_json(records)).hexdigest()
    if actual_hash != run["source"]["snapshot_sha256"]:
        raise EvidenceError(f"{label} source snapshot hash differs from captured source")


def materialize_source(root: Path, output: Path) -> None:
    run = _load_run(root)
    if output.exists() or _is_link(output):
        raise EvidenceError("materialized source output must not already exist")
    try:
        output.parent.resolve(strict=True)
        output.mkdir()
        for item in run["sources"]:
            relative = item["source_path"]
            raw = _read_bounded_regular(
                root / "sources" / relative,
                f"captured source {relative}",
                MAX_ARTIFACT_BYTES,
            )
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            _atomic_publish_bytes(destination, raw)
        _verify_source_tree_against_run(output, run, "materialized", exact_tree=True)
    except BaseException:
        if output.exists() and not _is_link(output):
            shutil.rmtree(output, ignore_errors=True)
        raise


def verify_source_boundary(
    root: Path, live_source_root: Path, snapshot_root: Path, boundary: str
) -> dict[str, str]:
    boundary = _require_text(boundary, "source boundary", stable_id=True)
    run = _load_run(root)
    git = _resolved_tool("git", live_source_root, "git")
    commit, tree, status = _git_source_state(live_source_root, git)
    if (
        commit != run["source"]["commit"]
        or tree != run["source"]["tree"]
        or status
    ):
        raise EvidenceError(
            f"live source commit/tree/clean state changed at boundary {boundary}"
        )
    _, live_payloads = _source_snapshot(live_source_root, include_payloads=True)
    _require_sources_match_commit(live_source_root, git, commit, live_payloads)
    _verify_source_tree_against_run(live_source_root, run, "live")
    _verify_source_tree_against_run(
        snapshot_root, run, "materialized", exact_tree=True
    )
    return {
        "boundary": boundary,
        "commit": commit,
        "tree": tree,
        "snapshot_sha256": run["source"]["snapshot_sha256"],
    }


def attested_tool_path(root: Path, label: str) -> str:
    run = _load_run(root)
    if label not in run["toolchain"]:
        raise EvidenceError(f"unknown attested tool: {label}")
    record = run["toolchain"][label]
    path = Path(record["resolved"])
    raw = _read_bounded_regular(path, f"attested {label} executable", MAX_ARTIFACT_BYTES)
    if _bytes_identity(raw) != record["executable"]:
        raise EvidenceError(f"attested {label} executable changed after capture")
    return str(path)


_IMAGE_TOOL: Any | None = None


def _image_tool() -> Any:
    global _IMAGE_TOOL
    if _IMAGE_TOOL is not None:
        return _IMAGE_TOOL
    script = Path(__file__).with_name("fs-allocator-image.py")
    spec = importlib.util.spec_from_file_location("fs_allocator_image_evidence", script)
    if spec is None or spec.loader is None:
        raise EvidenceError("could not load fs-allocator-image.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _IMAGE_TOOL = module
    return module


def _canonical_gzip(raw: bytes) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", compresslevel=9, fileobj=output, mtime=0
    ) as handle:
        handle.write(raw)
    return output.getvalue()


def _read_canonical_gzip_image(
    path: Path, expected: dict[str, Any], label: str
) -> bytes:
    try:
        compressed = path.read_bytes()
    except OSError as error:
        raise EvidenceError(f"{label} is unavailable: {error}") from error
    if not compressed or len(compressed) > MAX_ARTIFACT_BYTES:
        raise EvidenceError(f"{label} compressed size is invalid")
    output = bytearray()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as handle:
            while True:
                chunk = handle.read(min(1024 * 1024, MAX_RAW_IMAGE_BYTES + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > MAX_RAW_IMAGE_BYTES:
                    raise EvidenceError(f"{label} expands beyond the raw image limit")
    except EvidenceError:
        raise
    except (OSError, EOFError, gzip.BadGzipFile) as error:
        raise EvidenceError(f"{label} is not a valid gzip stream: {error}") from error
    raw = bytes(output)
    if len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise EvidenceError(f"{label} does not match its snapshot image identity")
    if compressed != _canonical_gzip(raw):
        raise EvidenceError(f"{label} is not deterministic gzip (level 9, mtime 0)")
    return raw


def _write_raw_temp(directory: Path, name: str, raw: bytes) -> Path:
    path = directory / name
    try:
        with path.open("xb") as handle:
            handle.write(raw)
    except OSError as error:
        raise EvidenceError(f"could not materialize private raw image: {error}") from error
    return path


def _run_image_cli_json(
    directory: Path, arguments: list[str], output_name: str, label: str
) -> dict[str, Any]:
    output = directory / output_name
    if output.exists() or _is_link(output):
        raise EvidenceError(f"raw verifier output already exists: {output_name}")
    tool = _image_tool()
    stderr = io.StringIO()
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            status = tool.main(arguments + ["--output", str(output)])
    except SystemExit as error:
        raise EvidenceError(
            f"{label} raw CLI rejected its inputs: exit={error.code} {stderr.getvalue().strip()}"
        ) from error
    if status != 0:
        raise EvidenceError(
            f"{label} raw CLI failed: exit={status} {stderr.getvalue().strip()}"
        )
    if stdout.getvalue():
        raise EvidenceError(f"{label} raw CLI emitted unexpected stdout")
    return _read_canonical_json(output, f"{label} raw CLI output")


def _raw_case_cli_results(
    directory: Path,
    paths: dict[str, Path],
    case: tuple[str, str, str],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    raw_path = {stage: str(path) for stage, path in paths.items()}
    snapshots = {
        stage: _run_image_cli_json(
            directory,
            ["snapshot", raw_path[stage]],
            f"{stage}.snapshot.actual.json",
            f"{stage} snapshot",
        )
        for stage in ("before", "fault", "reboot")
    }
    fault_diff = _run_image_cli_json(
        directory,
        ["diff", raw_path["before"], raw_path["fault"]],
        "fault.diff.actual.json",
        "fault diff",
    )
    reboot_diff = _run_image_cli_json(
        directory,
        ["diff", raw_path["before"], raw_path["reboot"]],
        "reboot.diff.actual.json",
        "reboot diff",
    )
    canonical = _run_image_cli_json(
        directory,
        ["validate", raw_path["reboot"]],
        "reboot.canonical.actual.json",
        "reboot canonical validation",
    )
    verified = _run_image_cli_json(
        directory,
        [
            "verify-case-raw",
            raw_path["before"],
            raw_path["fault"],
            raw_path["reboot"],
            "--operation",
            case[0],
            "--phase",
            case[1],
            "--action",
            case[2],
            "--require-metadata-cow",
        ],
        "verified.actual.json",
        "case verification",
    )
    return snapshots, fault_diff, reboot_diff, canonical, verified


def _raw_mutation_cli_rejection(
    directory: Path, paths: dict[str, Path]
) -> tuple[int, dict[str, str]]:
    launcher = Path(__file__).with_name("trusted-python-entry.py").resolve()
    command = [
        sys.executable,
        "-I",
        "-S",
        str(launcher),
        "scripts/fs-allocator-image.py",
        "verify-case-raw",
        paths["before"].name,
        paths["fault"].name,
        paths["reboot"].name,
        "--operation",
        "alloc",
        "--phase",
        "intent",
        "--action",
        "crash",
        "--require-metadata-cow",
        "--output",
        "mutation-verified.json",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=directory,
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise EvidenceError(f"could not execute raw mutation verifier: {error}") from error
    if completed.stdout:
        raise EvidenceError("raw mutation verifier emitted unexpected stdout")
    if completed.returncode == 0:
        raise EvidenceError("delete-FLUSH mutant raw images were accepted by verify-case")
    try:
        stderr_lines = completed.stderr.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise EvidenceError(f"raw mutation verifier stderr is not UTF-8: {error}") from error
    if not stderr_lines:
        raise EvidenceError("raw mutation verifier failed without a diagnostic")
    try:
        diagnostic = json.loads(
            stderr_lines[-1], object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (EvidenceError, json.JSONDecodeError) as error:
        raise EvidenceError("raw mutation verifier did not return stable error JSON") from error
    if not isinstance(diagnostic, dict) or set(diagnostic) != {"error"}:
        raise EvidenceError("raw mutation verifier error envelope is invalid")
    error_record = diagnostic["error"]
    if not isinstance(error_record, dict) or set(error_record) != {"code", "message"}:
        raise EvidenceError("raw mutation verifier error record is invalid")
    code = _require_text(error_record["code"], "raw verifier error code", stable_id=True)
    message = _require_text(error_record["message"], "raw verifier error message")
    return completed.returncode, {"code": code, "message": message}


def _raw_mutation_control_acceptance(
    directory: Path, paths: dict[str, Path]
) -> dict[str, Any]:
    verified = _run_image_cli_json(
        directory,
        [
            "verify-case-raw",
            str(paths["before"]),
            str(paths["fault"]),
            str(paths["reboot"]),
            "--operation",
            "alloc",
            "--phase",
            "intent",
            "--action",
            "busy",
            "--require-metadata-cow",
        ],
        "mutation-busy-control.json",
        "mutant busy control",
    )
    if (
        verified.get("operation") != "alloc"
        or verified.get("phase") != "intent"
        or verified.get("action") != "busy"
        or verified.get("verified") is not True
    ):
        raise EvidenceError("delete-FLUSH busy control returned the wrong verification")
    return verified


def _artifact_record(root: Path, relative: str) -> dict[str, Any]:
    path = root / Path(relative)
    if _is_link(path) or not path.is_file():
        raise EvidenceError(f"required artifact is missing or unsafe: {relative}")
    size = path.stat().st_size
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise EvidenceError(f"required artifact has an invalid size: {relative}")
    return {"path": relative, "bytes": size, "sha256": _sha256_file(path)}


def _validate_exact_tree(root: Path, *, require_manifest: bool) -> None:
    expected_root = set(ROOT_SOURCE_FILES) | {"cases", "sources"}
    if require_manifest:
        expected_root.add("manifest.json")
    elif (root / "manifest.json").exists() or _is_link(root / "manifest.json"):
        expected_root.add("manifest.json")
    actual_root = {path.name for path in root.iterdir()}
    if actual_root != expected_root:
        raise EvidenceError(
            "evidence root entries mismatch: "
            f"missing={sorted(expected_root - actual_root)}, "
            f"extra={sorted(actual_root - expected_root)}"
        )
    sources_root = root / "sources"
    if _is_link(sources_root) or not sources_root.is_dir():
        raise EvidenceError("sources must be a real directory")
    expected_source_entries = set(SOURCE_PATHS)
    for relative in SOURCE_PATHS:
        parent = PurePosixPath(relative).parent
        while str(parent) != ".":
            expected_source_entries.add(parent.as_posix() + "/")
            parent = parent.parent
    actual_source_entries = {
        path.relative_to(sources_root).as_posix() + ("/" if path.is_dir() else "")
        for path in sources_root.rglob("*")
    }
    if actual_source_entries != expected_source_entries:
        raise EvidenceError(
            "source snapshot entries mismatch: "
            f"missing={sorted(expected_source_entries - actual_source_entries)}, "
            f"extra={sorted(actual_source_entries - expected_source_entries)}"
        )
    cases_root = root / "cases"
    if _is_link(cases_root) or not cases_root.is_dir():
        raise EvidenceError("cases must be a real directory")
    actual_cases = {path.name for path in cases_root.iterdir()}
    expected_cases = set(CASE_IDS)
    if actual_cases != expected_cases:
        raise EvidenceError(
            "case directory set mismatch: "
            f"missing={sorted(expected_cases - actual_cases)}, "
            f"extra={sorted(actual_cases - expected_cases)}"
        )
    expected_files = set(CASE_FILES)
    for case_id in CASE_IDS:
        case_dir = cases_root / case_id
        if _is_link(case_dir) or not case_dir.is_dir():
            raise EvidenceError(f"case path is not a real directory: {case_id}")
        actual_files = {path.name for path in case_dir.iterdir()}
        if actual_files != expected_files:
            raise EvidenceError(
                f"case {case_id} files mismatch: "
                f"missing={sorted(expected_files - actual_files)}, "
                f"extra={sorted(actual_files - expected_files)}"
            )
        for name in CASE_FILES:
            path = case_dir / name
            if _is_link(path) or not path.is_file():
                raise EvidenceError(f"case artifact is missing or unsafe: {case_id}/{name}")


def _validate_backend(
    value: dict[str, Any], run: dict[str, Any] | None = None
) -> dict[str, Any]:
    _require_fields(value, {"schema_version", "format", "backend"}, "backend record")
    if value["schema_version"] != SCHEMA_VERSION or value["format"] != BACKEND_FORMAT:
        raise EvidenceError("backend record version is unsupported")
    backend = value["backend"]
    if not isinstance(backend, dict):
        raise EvidenceError("backend must be a JSON object")
    _require_fields(
        backend,
        {
            "identity",
            "version",
            "abi_version",
            "model",
            "deterministic",
            "volatile_cache",
            "capacity_bytes",
            "build_sha256",
            "compile_argv",
            "launch_argv",
        },
        "backend",
    )
    identity = _require_text(backend["identity"], "backend identity", stable_id=True)
    version = _require_text(backend["version"], "backend version", stable_id=True)
    abi_version = _require_text(
        backend["abi_version"], "backend ABI version", stable_id=True
    )
    if backend["model"] != BACKEND_MODEL:
        raise EvidenceError(f"backend model must be {BACKEND_MODEL}")
    if backend["deterministic"] is not True or backend["volatile_cache"] is not True:
        raise EvidenceError("backend must declare deterministic volatile-cache semantics")
    capacity_bytes = _require_nonnegative_int(
        backend["capacity_bytes"], "backend capacity_bytes"
    )
    if capacity_bytes == 0 or capacity_bytes % 1024 != 0:
        raise EvidenceError("backend capacity_bytes must be positive and block-aligned")
    build_sha256 = _require_sha256(backend["build_sha256"], "backend build_sha256")
    compile_argv = _require_argv(backend["compile_argv"], "backend compile_argv")
    _normalize_profile_compile_argv(
        compile_argv, "backend compile argv", mutant=False, run=run
    )
    argv = _require_argv(backend["launch_argv"], "backend launch_argv")
    if run is not None:
        trusted = argv[4].replace("\\", "/") if len(argv) > 4 else ""
        expected_prefix = [
            run["toolchain"]["python"]["resolved"],
            "-I",
            "-S",
            "-B",
        ]
        if (
            len(argv) != 8
            or argv[:4] != expected_prefix
            or not trusted.endswith("/sources/scripts/trusted-python-entry.py")
            or argv[5] != "scripts/agent_test_runner.py"
            or argv[6:] != ["--init-proc", "fsallocfault_ucore"]
        ):
            raise EvidenceError("backend launch argv is not bound to trusted Python")
    return {
        "identity": identity,
        "version": version,
        "abi_version": abi_version,
        "model": BACKEND_MODEL,
        "deterministic": True,
        "volatile_cache": True,
        "capacity_bytes": capacity_bytes,
        "build_sha256": build_sha256,
        "compile_argv": compile_argv,
        "launch_argv": argv,
    }


def _validate_artifact_reference(
    value: object, actual: dict[str, Any], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an artifact reference")
    _require_fields(value, {"path", "bytes", "sha256"}, label)
    if value != actual:
        raise EvidenceError(f"{label} does not match its artifact")
    return dict(actual)


def _validate_mutation(
    value: dict[str, Any],
    backend: dict[str, Any],
    root: Path,
    artifacts: dict[str, dict[str, Any]],
    *,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    _require_fields(
        value,
        {
            "schema_version",
            "format",
            "mutation",
            "mutation_target",
            "status",
            "backend_identity",
            "backend_version",
            "case",
            "baseline_compile_argv",
            "mutant_compile_argv",
            "command",
            "mutant_verification_exit_code",
            "expected_outcome",
            "observed_outcome",
            "verifier_error",
            "powercut",
            "baseline_kernel",
            "mutant_kernel",
            "selection_diff",
            "images",
            "log",
        },
        "delete-FLUSH mutation record",
    )
    if value["schema_version"] != SCHEMA_VERSION or value["format"] != MUTATION_FORMAT:
        raise EvidenceError("delete-FLUSH mutation record version is unsupported")
    if value["mutation"] != DELETE_FLUSH_MUTATION or value["status"] != "passed":
        raise EvidenceError("delete-FLUSH negative mutation did not pass")
    if value["mutation_target"] != "allocator-phase-barrier":
        raise EvidenceError("delete-FLUSH mutation target is not the allocator phase barrier")
    if (
        value["backend_identity"] != backend["identity"]
        or value["backend_version"] != backend["version"]
    ):
        raise EvidenceError("delete-FLUSH mutation backend identity mismatch")
    expected_case = {
        "id": "alloc-intent-crash",
        "operation": "alloc",
        "phase": "intent",
        "action": "crash",
    }
    if value["case"] != expected_case:
        raise EvidenceError("delete-FLUSH mutation must exercise alloc:intent:crash")
    baseline_compile_argv = _require_argv(
        value["baseline_compile_argv"], "delete-FLUSH baseline compile argv"
    )
    mutant_compile_argv = _require_argv(
        value["mutant_compile_argv"], "delete-FLUSH mutant compile argv"
    )
    baseline_normalized = _normalize_profile_compile_argv(
        baseline_compile_argv, "delete-FLUSH baseline compile argv", mutant=False
    )
    mutant_normalized = _normalize_profile_compile_argv(
        mutant_compile_argv, "delete-FLUSH mutant compile argv", mutant=True
    )
    if (
        baseline_compile_argv != backend["compile_argv"]
        or mutant_normalized != baseline_normalized
    ):
        raise EvidenceError("delete-FLUSH compile argv is not bound to the baseline build")
    command = _require_argv(value["command"], "delete-FLUSH mutation command")
    if command[: len(backend["launch_argv"])] != backend["launch_argv"]:
        raise EvidenceError("delete-FLUSH command is not bound to the backend launch argv")
    baseline_kernel = _validate_artifact_reference(
        value["baseline_kernel"], artifacts["profile.kernel"], "baseline kernel"
    )
    mutant_kernel = _validate_artifact_reference(
        value["mutant_kernel"],
        artifacts["flush-deletion-mutant.kernel"],
        "mutant kernel",
    )
    if mutant_kernel["sha256"] == baseline_kernel["sha256"]:
        raise EvidenceError("delete-FLUSH mutant build must differ from the baseline")
    selection_diff = _validate_artifact_reference(
        value["selection_diff"],
        artifacts["flush-deletion-selection.diff"],
        "mutant selection diff",
    )
    try:
        diff_text = (root / selection_diff["path"]).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise EvidenceError(f"mutant selection diff is unreadable: {error}") from error
    if (
        "FS_ALLOCATOR_DELETE_BARRIER_MUTANT" not in diff_text
        or "fs_durable_barrier_forward" not in diff_text
        or not any(
            line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
            for line in diff_text.splitlines()
        )
    ):
        raise EvidenceError("mutant selection diff does not bind the allocator barrier bypass")
    exit_code = value["mutant_verification_exit_code"]
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code == 0:
        raise EvidenceError("delete-FLUSH mutant must be rejected with a non-zero exit code")
    if (
        value["expected_outcome"] != "verification-rejected"
        or value["observed_outcome"] != "verification-rejected"
    ):
        raise EvidenceError("delete-FLUSH mutation outcome is not fail-closed")
    log = _validate_artifact_reference(
        value["log"], artifacts["flush-deletion-mutation.log"], "mutation log"
    )
    images = value["images"]
    if not isinstance(images, dict):
        raise EvidenceError("delete-FLUSH mutation images must be an object")
    _require_fields(images, {"before", "fault", "reboot"}, "mutation images")
    image_identities = {
        stage: _validate_image(images[stage], f"mutation {stage} image")
        for stage in ("before", "fault", "reboot")
    }
    raw_images = {
        stage: _read_canonical_gzip_image(
            root / f"flush-deletion-{stage}.img.gz",
            image_identities[stage],
            f"mutation {stage} raw image",
        )
        for stage in ("before", "fault", "reboot")
    }
    reference_root = root if evidence_root is None else evidence_root
    reference_snapshot = _read_canonical_json(
        reference_root / "cases" / "alloc-intent-crash" / "before.snapshot.json",
        "alloc:intent:crash reference before snapshot",
    )
    reference_image, _, _ = _validate_snapshot(
        reference_snapshot,
        "alloc:intent:crash reference before snapshot",
        require_canonical=True,
    )
    reference_before = _read_canonical_gzip_image(
        reference_root / "cases" / "alloc-intent-crash" / "before.img.gz",
        reference_image,
        "alloc:intent:crash reference before raw image",
    )
    if raw_images["before"] != reference_before:
        raise EvidenceError(
            "delete-FLUSH before image is not the alloc:intent:crash case baseline"
        )
    powercut = value["powercut"]
    if not isinstance(powercut, dict):
        raise EvidenceError("delete-FLUSH powercut receipt must be an object")
    _require_fields(
        powercut,
        {"durable_epoch", "pending_write_count", "discarded_write_count"},
        "delete-FLUSH powercut receipt",
    )
    durable_epoch = _require_nonnegative_int(
        powercut["durable_epoch"], "delete-FLUSH durable epoch"
    )
    pending = _require_nonnegative_int(
        powercut["pending_write_count"], "delete-FLUSH pending write count"
    )
    discarded = _require_nonnegative_int(
        powercut["discarded_write_count"], "delete-FLUSH discarded write count"
    )
    if durable_epoch == 0 or pending != 1 or discarded != 1:
        raise EvidenceError(
            "delete-FLUSH powercut must discard exactly one pending qmap write"
        )

    with tempfile.TemporaryDirectory(prefix="fsalloc-mutant-") as temporary:
        directory = Path(temporary)
        paths = {
            stage: _write_raw_temp(directory, f"{stage}.img", raw)
            for stage, raw in raw_images.items()
        }
        fault_snapshot = _run_image_cli_json(
            directory,
            ["snapshot", str(paths["fault"])],
            "fault.snapshot.actual.json",
            "mutant fault snapshot",
        )
        fault_allocating = [
            entry
            for entry in dict(fault_snapshot["qmap_entries"]).values()
            if entry["state"] == "ALLOCATING"
        ]
        if fault_allocating:
            raise EvidenceError(
                "delete-FLUSH fault image unexpectedly persisted the alloc intent transition"
            )
        _raw_mutation_control_acceptance(directory, paths)
        actual_exit_code, actual_error = _raw_mutation_cli_rejection(directory, paths)
    if exit_code != actual_exit_code:
        raise EvidenceError("delete-FLUSH recorded verifier exit code is not reproducible")
    verifier_error = value["verifier_error"]
    if not isinstance(verifier_error, dict):
        raise EvidenceError("delete-FLUSH verifier error must be an object")
    _require_fields(verifier_error, {"code", "message"}, "delete-FLUSH verifier error")
    _require_text(verifier_error["code"], "delete-FLUSH verifier error code", stable_id=True)
    _require_text(verifier_error["message"], "delete-FLUSH verifier error message")
    expected_error = {
        "code": MUTATION_REJECTION_CODE,
        "message": MUTATION_REJECTION_MESSAGE,
    }
    if actual_error != expected_error:
        raise EvidenceError(
            "delete-FLUSH raw verifier did not reject the missing qmap checkpoint"
        )
    if verifier_error != actual_error:
        raise EvidenceError("delete-FLUSH recorded verifier error is not reproducible")

    try:
        log_lines = (root / log["path"]).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise EvidenceError(f"delete-FLUSH mutation log is unreadable: {error}") from error
    crash_marker = "fsallocfault_kernel: durability_receipt_failed=1"
    overlay_marker = (
        "fsalloc-cache: mutation=delete-flush target=allocator-phase-barrier "
        f"durable_epoch={durable_epoch} pending_at_powercut={pending} "
        f"discarded_on_powercut={discarded} powercut=1"
    )
    result_marker = (
        "fsalloc-mutation: mutation=delete-flush target=allocator-phase-barrier "
        "case=alloc-intent-crash "
        f"baseline_kernel_sha256={baseline_kernel['sha256']} "
        f"mutant_kernel_sha256={mutant_kernel['sha256']} "
        f"verifier_exit_code={exit_code} outcome=verification-rejected"
    )
    for marker in (crash_marker, overlay_marker, result_marker):
        if log_lines.count(marker) != 1:
            raise EvidenceError("delete-FLUSH mutation log lacks a unique factual marker")
    return {
        "mutation": DELETE_FLUSH_MUTATION,
        "mutation_target": "allocator-phase-barrier",
        "status": "passed",
        "backend_identity": backend["identity"],
        "backend_version": backend["version"],
        "case": expected_case,
        "baseline_compile_argv": baseline_compile_argv,
        "mutant_compile_argv": mutant_compile_argv,
        "command": command,
        "mutant_verification_exit_code": exit_code,
        "expected_outcome": "verification-rejected",
        "observed_outcome": "verification-rejected",
        "verifier_error": verifier_error,
        "powercut": {
            "durable_epoch": durable_epoch,
            "pending_write_count": pending,
            "discarded_write_count": discarded,
        },
        "baseline_kernel": baseline_kernel,
        "mutant_kernel": mutant_kernel,
        "selection_diff": selection_diff,
        "images": image_identities,
        "log": log,
    }


def _validate_generator(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be a generator object")
    _require_fields(value, {"name", "version"}, label)
    expected = dict(_image_tool().GENERATOR)
    if value != expected:
        raise EvidenceError(f"{label} must identify the loaded verifier generator")
    version = _require_text(value["version"], f"{label} version", stable_id=True)
    return {"name": GENERATOR_NAME, "version": version}


def _validate_image(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an image identity object")
    _require_fields(value, {"bytes", "sha256"}, label)
    size = _require_nonnegative_int(value["bytes"], f"{label} bytes")
    if size == 0 or size > MAX_RAW_IMAGE_BYTES or size % 1024 != 0:
        raise EvidenceError(f"{label} bytes must be a positive block-aligned size")
    return {"bytes": size, "sha256": _require_sha256(value["sha256"], f"{label} sha256")}


def _validate_snapshot(
    value: dict[str, Any], label: str, *, require_canonical: bool
) -> tuple[dict[str, Any], dict[str, str], str]:
    if value.get("format") != SNAPSHOT_FORMAT:
        raise EvidenceError(f"{label} format must be {SNAPSHOT_FORMAT}")
    image = _validate_image(value.get("image"), f"{label} image")
    generator = _validate_generator(value.get("generator"), f"{label} generator")
    state_sha256 = _require_sha256(value.get("state_sha256"), f"{label} state_sha256")
    geometry = value["geometry"]
    if not isinstance(geometry, dict):
        raise EvidenceError(f"{label} geometry must be an object")
    if "size" not in geometry:
        raise EvidenceError(f"{label} geometry lacks image size")
    for field, item in geometry.items():
        if isinstance(item, int):
            _require_nonnegative_int(item, f"{label} geometry {field}")
    if geometry["size"] == 0 or image["bytes"] != geometry["size"] * 1024:
        raise EvidenceError(f"{label} image size does not match filesystem geometry")
    for field in SNAPSHOT_LIST_FIELDS:
        if not isinstance(value[field], list):
            raise EvidenceError(f"{label} {field} must be an array")
    for field in SNAPSHOT_OBJECT_FIELDS:
        if not isinstance(value[field], dict):
            raise EvidenceError(f"{label} {field} must be an object")
    for field in ("payload_sha256", "block_sha256"):
        for identity, digest in value[field].items():
            _require_text(identity, f"{label} {field} identity", stable_id=True)
            _require_sha256(digest, f"{label} {field} digest")
    violations = value.get("canonical_violations")
    if require_canonical and violations:
        raise EvidenceError(f"{label} is not canonical")
    semantic = dict(value)
    del semantic["image"]
    del semantic["generator"]
    del semantic["state_sha256"]
    canonical = json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != state_sha256:
        raise EvidenceError(f"{label} state_sha256 does not bind its semantic content")
    return image, generator, state_sha256


def _validate_diff(
    value: dict[str, Any], before_sha256: str, after_sha256: str, label: str
) -> None:
    if value.get("format") != DIFF_FORMAT:
        raise EvidenceError(f"{label} format must be {DIFF_FORMAT}")
    for field in ("bitmap_set", "bitmap_cleared", "owner_changes", "inode_changes"):
        if not isinstance(value[field], list):
            raise EvidenceError(f"{label} {field} must be an array")
    if value.get("before_sha256") != before_sha256 or value.get("after_sha256") != after_sha256:
        raise EvidenceError(f"{label} state identities do not match its snapshots")


def _validate_canonical(value: dict[str, Any], reboot: dict[str, Any]) -> None:
    _require_fields(
        value,
        {
            "format",
            "generator",
            "image",
            "state_sha256",
            "qmap_state_counts",
            "qmap_top_state_counts",
            "inode_owner_state_counts",
            "violations",
            "transitions",
            "inode_transitions",
        },
        "reboot canonical record",
    )
    if (
        value["format"] != CANONICAL_FORMAT
        or value["generator"] != reboot["generator"]
        or value["image"] != reboot["image"]
        or value["state_sha256"] != reboot["state_sha256"]
        or value["qmap_state_counts"] != reboot["qmap_state_counts"]
        or value["qmap_top_state_counts"] != reboot["qmap_top_state_counts"]
        or value["inode_owner_state_counts"]
        != reboot["inode_owner_state_counts"]
        or value["violations"] != []
        or value["transitions"] != []
        or value["inode_transitions"] != []
    ):
        raise EvidenceError("reboot canonical record is not clean or is for another state")


def _rerun_case_from_raw(
    case_root: Path,
    case: tuple[str, str, str],
    snapshots: dict[str, dict[str, Any]],
    fault_diff: dict[str, Any],
    reboot_diff: dict[str, Any],
    canonical: dict[str, Any],
    verified: dict[str, Any],
) -> None:
    case_id = "-".join(case)
    raw_images = {
        stage: _read_canonical_gzip_image(
            case_root / f"{stage}.img.gz",
            snapshots[stage]["image"],
            f"{case_id} {stage} raw image",
        )
        for stage in ("before", "fault", "reboot")
    }
    with tempfile.TemporaryDirectory(prefix=f"fsalloc-{case_id}-") as temporary:
        directory = Path(temporary)
        paths = {
            stage: _write_raw_temp(directory, f"{stage}.img", raw)
            for stage, raw in raw_images.items()
        }
        actual, actual_fault_diff, actual_reboot_diff, actual_canonical, actual_verified = (
            _raw_case_cli_results(directory, paths, case)
        )
        for stage in ("before", "fault", "reboot"):
            if actual[stage] != snapshots[stage]:
                raise EvidenceError(
                    f"{case_id} {stage} snapshot is not reproduced by its raw image"
                )
        if actual_fault_diff != fault_diff or actual_reboot_diff != reboot_diff:
            raise EvidenceError(f"{case_id} exact diff is not reproduced by raw images")
        if actual_canonical != canonical:
            raise EvidenceError(
                f"{case_id} reboot canonical result is not reproduced by its raw image"
            )
        if actual_verified != verified:
            raise EvidenceError(
                f"{case_id} verified result is not reproduced by raw images"
            )


def _receipt_log_marker(
    receipt_id: str,
    backend_instance_id: str,
    abi_version: str,
    capacity_bytes: int,
    durable_epoch: int,
    raw_write_count: int,
    cached_write_count: int,
    flush_command_count: int,
    acknowledged_flush_count: int,
    last_acknowledged_sequence: int,
    pending_before: int,
    pending_after: int,
    pending_at_stage_end: int,
    powercut_after_receipt: bool,
) -> str:
    return (
        "fsalloc-cache: "
        f"receipt_id={receipt_id} "
        f"backend_instance_id={backend_instance_id} "
        f"abi_version={abi_version} "
        f"capacity_bytes={capacity_bytes} "
        f"durable_epoch={durable_epoch} "
        f"raw_write_count={raw_write_count} "
        f"cached_write_count={cached_write_count} "
        f"flush_command_count={flush_command_count} "
        f"acknowledged_flush_count={acknowledged_flush_count} "
        f"last_acknowledged_sequence={last_acknowledged_sequence} "
        f"pending_before={pending_before} "
        f"pending_after={pending_after} "
        f"pending_at_stage_end={pending_at_stage_end} "
        f"powercut_after_receipt={int(powercut_after_receipt)}"
    )


def _validate_receipt(
    value: dict[str, Any],
    case: tuple[str, str, str],
    stage: str,
    backend: dict[str, Any],
    source_log: dict[str, Any],
    source_log_path: Path,
) -> dict[str, Any]:
    operation, phase, action = case
    case_id = "-".join(case)
    _require_fields(
        value,
        {
            "schema_version",
            "format",
            "case",
            "stage",
            "backend",
            "launch_argv",
            "receipt",
            "physical_io",
            "source_log",
        },
        f"{case_id} {stage} flush receipt",
    )
    if value["schema_version"] != SCHEMA_VERSION or value["format"] != RECEIPT_FORMAT:
        raise EvidenceError(f"{case_id} {stage} flush receipt version is unsupported")
    expected_case = {
        "id": case_id,
        "operation": operation,
        "phase": phase,
        "action": action,
    }
    if value["case"] != expected_case or value["stage"] != stage:
        raise EvidenceError(f"{case_id} {stage} flush receipt identity mismatch")
    receipt_backend = value["backend"]
    if not isinstance(receipt_backend, dict):
        raise EvidenceError(f"{case_id} {stage} receipt backend must be an object")
    expected_backend = {
        key: backend[key]
        for key in (
            "identity",
            "version",
            "abi_version",
            "model",
            "deterministic",
            "volatile_cache",
            "capacity_bytes",
            "build_sha256",
        )
    }
    if receipt_backend != expected_backend:
        raise EvidenceError(f"{case_id} {stage} receipt backend mismatch")
    launch_argv = _require_argv(value["launch_argv"], f"{case_id} {stage} launch_argv")
    base_argv = backend["launch_argv"]
    if launch_argv[: len(base_argv)] != base_argv:
        raise EvidenceError(f"{case_id} {stage} launch_argv is not bound to the backend")
    receipt = value["receipt"]
    if not isinstance(receipt, dict):
        raise EvidenceError(f"{case_id} {stage} receipt body must be an object")
    _require_fields(
        receipt,
        {
            "backend_instance_id",
            "receipt_id",
            "raw_write_count",
            "cached_write_count",
            "flush_command_count",
            "acknowledged_flush_count",
            "last_acknowledged_sequence",
            "durable_epoch",
            "pending_write_count_before_flush",
            "pending_write_count_after_flush",
            "pending_write_count_at_stage_end",
            "powercut_after_receipt",
        },
        f"{case_id} {stage} receipt body",
    )
    backend_instance_id = _require_text(
        receipt["backend_instance_id"],
        f"{case_id} {stage} backend_instance_id",
        stable_id=True,
    )
    receipt_id = _require_text(
        receipt["receipt_id"], f"{case_id} {stage} receipt_id", stable_id=True
    )
    if backend_instance_id != f"{case_id}:{stage}" or receipt_id != f"{case_id}:{stage}:flush":
        raise EvidenceError(f"{case_id} {stage} receipt has an unstable identity")
    raw_write_count = _require_nonnegative_int(
        receipt["raw_write_count"], f"{case_id} {stage} raw_write_count"
    )
    cached_write_count = _require_nonnegative_int(
        receipt["cached_write_count"], f"{case_id} {stage} cached_write_count"
    )
    flush_command_count = _require_nonnegative_int(
        receipt["flush_command_count"], f"{case_id} {stage} flush_command_count"
    )
    acknowledged_flush_count = _require_nonnegative_int(
        receipt["acknowledged_flush_count"],
        f"{case_id} {stage} acknowledged_flush_count",
    )
    last_ack = _require_nonnegative_int(
        receipt["last_acknowledged_sequence"],
        f"{case_id} {stage} last_acknowledged_sequence",
    )
    durable_epoch = _require_nonnegative_int(
        receipt["durable_epoch"], f"{case_id} {stage} durable_epoch"
    )
    pending_before = _require_nonnegative_int(
        receipt["pending_write_count_before_flush"],
        f"{case_id} {stage} pending_write_count_before_flush",
    )
    pending_after = _require_nonnegative_int(
        receipt["pending_write_count_after_flush"],
        f"{case_id} {stage} pending_write_count_after_flush",
    )
    pending_at_stage_end = _require_nonnegative_int(
        receipt["pending_write_count_at_stage_end"],
        f"{case_id} {stage} pending_write_count_at_stage_end",
    )
    if (
        flush_command_count == 0
        or acknowledged_flush_count == 0
        or acknowledged_flush_count > flush_command_count
        or acknowledged_flush_count != flush_command_count
        or durable_epoch != acknowledged_flush_count
        or cached_write_count < raw_write_count
        or last_ack != cached_write_count
        or durable_epoch == 0
        or pending_after != 0
        or raw_write_count < pending_before
    ):
        raise EvidenceError(f"{case_id} {stage} has no acknowledged durable flush")
    if stage == "prepare" and (raw_write_count == 0 or pending_before == 0):
        raise EvidenceError(f"{case_id} prepare did not explicitly flush staged writes")
    expected_powercut = stage == "fault" and action == "crash"
    if expected_powercut and pending_before == 0:
        raise EvidenceError(f"{case_id} crash receipt did not flush the target phase")
    if receipt["powercut_after_receipt"] is not expected_powercut:
        raise EvidenceError(f"{case_id} {stage} power-cut receipt flag mismatch")
    if pending_at_stage_end != 0:
        raise EvidenceError(f"{case_id} {stage} left volatile writes pending")
    log = _validate_artifact_reference(
        value["source_log"], source_log, f"{case_id} {stage} source_log"
    )
    marker = _receipt_log_marker(
        receipt_id,
        backend_instance_id,
        backend["abi_version"],
        backend["capacity_bytes"],
        durable_epoch,
        raw_write_count,
        cached_write_count,
        flush_command_count,
        acknowledged_flush_count,
        last_ack,
        pending_before,
        pending_after,
        pending_at_stage_end,
        expected_powercut,
    )
    try:
        log_lines = source_log_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise EvidenceError(f"{case_id} {stage} source log is not stable UTF-8: {error}") from error
    if marker not in log_lines:
        raise EvidenceError(f"{case_id} {stage} flush receipt marker is absent from its log")
    physical_io = _parse_physical_io_receipts(
        case, stage, backend, receipt, log_lines
    )
    if value["physical_io"] != physical_io:
        raise EvidenceError(
            f"{case_id} {stage} physical I/O schema differs from its Guest log"
        )
    return {
        "launch_argv": launch_argv,
        "receipt": {
            "backend_instance_id": backend_instance_id,
            "receipt_id": receipt_id,
            "raw_write_count": raw_write_count,
            "cached_write_count": cached_write_count,
            "flush_command_count": flush_command_count,
            "acknowledged_flush_count": acknowledged_flush_count,
            "last_acknowledged_sequence": last_ack,
            "durable_epoch": durable_epoch,
            "pending_write_count_before_flush": pending_before,
            "pending_write_count_after_flush": pending_after,
            "pending_write_count_at_stage_end": pending_at_stage_end,
            "powercut_after_receipt": expected_powercut,
        },
        "physical_io": physical_io,
        "receipt_log_marker": marker,
        "source_log": log,
    }


def _validate_physical_io_chain(
    receipts: dict[str, dict[str, Any]], case_id: str, action: str
) -> dict[str, Any]:
    if list(receipts) != list(STAGES):
        raise EvidenceError(f"{case_id} physical receipt stage order differs")
    flushes = [
        item
        for stage in STAGES
        for item in receipts[stage]["physical_io"]["flushes"]
    ]
    operations = [
        item
        for stage in STAGES
        for item in receipts[stage]["physical_io"]["operations"]
    ]
    expected_flush_count = 3 if action == "crash" else 4
    expected_operation_count = 0 if action == "crash" else 1
    if (
        len(flushes) != expected_flush_count
        or len(operations) != expected_operation_count
    ):
        raise EvidenceError(f"{case_id} physical receipt chain is incomplete")
    raw_write_delta = sum(item["raw_write_delta"] for item in flushes)
    physical_write_delta = sum(item["physical_write_delta"] for item in flushes)
    logical_flush_delta = sum(item["logical_flush_delta"] for item in flushes)
    physical_flush_delta = sum(item["physical_flush_delta"] for item in flushes)
    operation_raw_write_delta = sum(
        item["raw_write_delta"] for item in operations
    )
    operation_physical_write_delta = sum(
        item["physical_write_delta"] for item in operations
    )
    operation_logical_flush_delta = sum(
        item["logical_flush_delta"] for item in operations
    )
    operation_physical_flush_delta = sum(
        item["physical_flush_delta"] for item in operations
    )
    if (
        raw_write_delta != physical_write_delta
        or logical_flush_delta != physical_flush_delta
        or operation_raw_write_delta != operation_physical_write_delta
        or operation_logical_flush_delta != operation_physical_flush_delta
    ):
        raise EvidenceError(f"{case_id} cross-stage physical I/O totals differ")
    return {
        "stage_order": list(STAGES),
        "flush_receipt_count": len(flushes),
        "operation_receipt_count": len(operations),
        "flush_totals": {
            "raw_write_delta": raw_write_delta,
            "physical_write_delta": physical_write_delta,
            "logical_flush_delta": logical_flush_delta,
            "physical_flush_delta": physical_flush_delta,
        },
        "operation_totals": {
            "raw_write_delta": operation_raw_write_delta,
            "physical_write_delta": operation_physical_write_delta,
            "logical_flush_delta": operation_logical_flush_delta,
            "physical_flush_delta": operation_physical_flush_delta,
        },
    }


def _case_manifest(
    root: Path,
    case: tuple[str, str, str],
    backend: dict[str, Any],
    mutation: dict[str, Any],
    receipt_ids: set[str],
) -> dict[str, Any]:
    operation, phase, action = case
    case_id = "-".join(case)
    case_root = root / "cases" / case_id
    run = _load_run(root)
    artifacts = {
        name: _artifact_record(root, f"cases/{case_id}/{name}") for name in CASE_FILES
    }
    build = _validate_case_build(
        root,
        case_id,
        _read_canonical_json(case_root / "build.json", f"{case_id} build"),
        run,
    )
    before = _read_canonical_json(case_root / "before.snapshot.json", f"{case_id} before snapshot")
    fault = _read_canonical_json(case_root / "fault.snapshot.json", f"{case_id} fault snapshot")
    reboot = _read_canonical_json(case_root / "reboot.snapshot.json", f"{case_id} reboot snapshot")
    before_image, before_generator, before_state = _validate_snapshot(
        before, f"{case_id} before snapshot", require_canonical=True
    )
    fault_image, fault_generator, fault_state = _validate_snapshot(
        fault, f"{case_id} fault snapshot", require_canonical=False
    )
    reboot_image, reboot_generator, reboot_state = _validate_snapshot(
        reboot, f"{case_id} reboot snapshot", require_canonical=True
    )
    if before_generator != fault_generator or before_generator != reboot_generator:
        raise EvidenceError(f"{case_id} snapshot generator versions differ")
    if len({before_image["bytes"], fault_image["bytes"], reboot_image["bytes"]}) != 1:
        raise EvidenceError(f"{case_id} image sizes differ between stages")
    workload = _validate_workload_image_chain(
        {"before": before, "fault": fault, "reboot": reboot}, build, case_id
    )

    fault_diff = _read_canonical_json(case_root / "fault.diff.json", f"{case_id} fault diff")
    reboot_diff = _read_canonical_json(case_root / "reboot.diff.json", f"{case_id} reboot diff")
    _validate_diff(fault_diff, before_state, fault_state, f"{case_id} fault diff")
    _validate_diff(reboot_diff, before_state, reboot_state, f"{case_id} reboot diff")
    canonical = _read_canonical_json(
        case_root / "reboot.canonical.json", f"{case_id} reboot canonical record"
    )
    _validate_canonical(canonical, reboot)

    verified = _read_canonical_json(case_root / "verified.json", f"{case_id} verified record")
    if verified.get("format") != VERIFIED_FORMAT:
        raise EvidenceError(f"{case_id} verified format must be {VERIFIED_FORMAT}")
    if (
        verified.get("operation") != operation
        or verified.get("phase") != phase
        or verified.get("action") != action
        or verified.get("verified") is not True
    ):
        raise EvidenceError(f"{case_id} is not semantically verified")
    verified_generator = _validate_generator(
        verified.get("generator"), f"{case_id} verified generator"
    )
    if verified_generator != before_generator:
        raise EvidenceError(f"{case_id} verified generator mismatch")
    images = verified.get("images")
    expected_images = {
        "before": before_image,
        "fault": fault_image,
        "reboot": reboot_image,
    }
    if images != expected_images:
        raise EvidenceError(f"{case_id} verified image identities mismatch")
    if (
        verified.get("before_sha256") != before_state
        or verified.get("fault_sha256") != fault_state
        or verified.get("reboot_sha256") != reboot_state
        or verified.get("fault_exact_diff") != fault_diff
        or verified.get("reboot_exact_diff") != reboot_diff
    ):
        raise EvidenceError(f"{case_id} verified state or exact diff mismatch")
    if not isinstance(verified["fault_qmap_transitions"], list) or not isinstance(
        verified["fault_inode_transitions"], list
    ):
        raise EvidenceError(f"{case_id} verified transitions must be arrays")
    for field in ("reboot_block_delta", "reboot_inode_delta"):
        if isinstance(verified[field], bool) or not isinstance(verified[field], int):
            raise EvidenceError(f"{case_id} verified {field} must be an integer")

    _rerun_case_from_raw(
        case_root,
        case,
        {"before": before, "fault": fault, "reboot": reboot},
        fault_diff,
        reboot_diff,
        canonical,
        verified,
    )

    receipts: dict[str, Any] = {}
    executions: dict[str, Any] = {}
    for stage in STAGES:
        log_record = artifacts[f"{stage}.guest.log"]
        receipt = _read_canonical_json(
            case_root / f"{stage}.flush.json", f"{case_id} {stage} flush receipt"
        )
        receipt_result = _validate_receipt(
            receipt,
            case,
            stage,
            backend,
            log_record,
            case_root / f"{stage}.guest.log",
        )
        receipt_id = receipt_result["receipt"]["receipt_id"]
        if receipt_id in receipt_ids:
            raise EvidenceError(f"duplicate flush receipt identity: {receipt_id}")
        receipt_ids.add(receipt_id)
        receipt_result["receipt_artifact"] = artifacts[f"{stage}.flush.json"]
        receipts[stage] = receipt_result
        executions[stage] = _validate_execution(
            root,
            case_id,
            stage,
            False,
            _read_canonical_json(
                case_root / f"{stage}.execution.json",
                f"{case_id} {stage} execution",
            ),
            run,
            backend,
        )

    physical_io = _validate_physical_io_chain(receipts, case_id, action)

    if (
        executions["prepare"]["output_image"] != before_image
        or executions["fault"]["input_image"] != before_image
        or executions["fault"]["output_image"] != fault_image
        or executions["reboot"]["input_image"] != fault_image
        or executions["reboot"]["output_image"] != reboot_image
    ):
        raise EvidenceError(f"{case_id} runner image chain differs from raw evidence")

    case_backend = {
        key: backend[key]
        for key in (
            "identity",
            "version",
            "abi_version",
            "model",
            "deterministic",
            "volatile_cache",
            "capacity_bytes",
            "build_sha256",
        )
    }
    return {
        "id": case_id,
        "operation": operation,
        "phase": phase,
        "action": action,
        "verified": True,
        "build": build,
        "workload": workload,
        "backend": case_backend,
        "generator": before_generator,
        "images": expected_images,
        "states": {
            "before_sha256": before_state,
            "fault_sha256": fault_state,
            "reboot_sha256": reboot_state,
        },
        "executions": receipts,
        "physical_io": physical_io,
        "runner_executions": executions,
        "negative_mutations": {
            DELETE_FLUSH_MUTATION: {
                "status": mutation["status"],
                "report_sha256": _sha256_file(root / "flush-deletion-mutation.json"),
            }
        },
        "artifacts": artifacts,
    }


def construct_manifest(evidence_root: Path) -> dict[str, Any]:
    if _is_link(evidence_root) or not evidence_root.is_dir():
        raise EvidenceError("evidence root is missing or is a symlink")
    try:
        root = evidence_root.resolve(strict=True)
    except OSError as error:
        raise EvidenceError(f"evidence root is unavailable: {error}") from error
    _reject_links(root)
    _validate_exact_tree(root, require_manifest=False)

    root_artifacts = {
        name: _artifact_record(root, name) for name in ROOT_SOURCE_FILES
    }
    run = _load_run(root)
    transcript, run_seal = _validate_run_seal(root)
    backend_record = _read_canonical_json(root / "backend.json", "backend record")
    backend = _validate_backend(backend_record, run)
    if backend["build_sha256"] != root_artifacts["profile.kernel"]["sha256"]:
        raise EvidenceError("backend build identity does not match profile.kernel")
    if backend["capacity_bytes"] > MAX_RAW_IMAGE_BYTES:
        raise EvidenceError("backend capacity exceeds the raw image verification limit")
    mutation_record = _read_canonical_json(
        root / "flush-deletion-mutation.json", "delete-FLUSH mutation record"
    )
    mutation = _validate_mutation(mutation_record, backend, root, root_artifacts)
    mutation_executions = {
        stage: _validate_execution(
            root,
            "mutation-alloc-intent-crash",
            stage,
            True,
            _read_canonical_json(
                root / f"flush-deletion-{stage}.execution.json",
                f"delete-FLUSH {stage} execution",
            ),
            run,
            backend,
        )
        for stage in ("fault", "reboot")
    }
    if (
        mutation_executions["fault"]["input_image"] != mutation["images"]["before"]
        or mutation_executions["fault"]["output_image"]
        != mutation["images"]["fault"]
        or mutation_executions["reboot"]["input_image"]
        != mutation["images"]["fault"]
        or mutation_executions["reboot"]["output_image"]
        != mutation["images"]["reboot"]
    ):
        raise EvidenceError("delete-FLUSH runner image chain differs from raw evidence")

    receipt_ids: set[str] = set()
    cases = [
        _case_manifest(root, case, backend, mutation, receipt_ids) for case in CASES
    ]
    if len(cases) != 36 or len(receipt_ids) != 36 * len(STAGES):
        raise EvidenceError("complete 36-case receipt inventory was not constructed")
    return {
        "schema_version": SCHEMA_VERSION,
        "format": MANIFEST_FORMAT,
        "suite": SUITE,
        "case_count": 36,
        "run": run,
        "run_seal": run_seal,
        "runner_transcript": transcript,
        "backend": backend,
        "negative_mutations": {
            DELETE_FLUSH_MUTATION: {
                **mutation,
                "executions": mutation_executions,
            }
        },
        "artifacts": root_artifacts,
        "cases": cases,
    }


def _render_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_publish_bytes(destination: Path, data: bytes) -> None:
    if _is_link(destination) or destination.exists():
        raise EvidenceError(f"writer refuses to overwrite: {destination}")
    temporary = destination.parent / f".{destination.name}.tmp.{os.getpid()}"
    if temporary.exists() or _is_link(temporary):
        raise EvidenceError(f"writer temporary path already exists: {temporary}")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as error:
            raise EvidenceError(f"writer destination appeared concurrently: {destination}") from error
        except OSError as error:
            raise EvidenceError(f"writer could not publish {destination}: {error}") from error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_bounded_regular(path: Path, label: str, limit: int) -> bytes:
    if _is_link(path) or not path.is_file():
        raise EvidenceError(f"{label} is missing or is a symlink")
    size = path.stat().st_size
    if size <= 0 or size > limit:
        raise EvidenceError(f"{label} size is invalid")
    try:
        return path.read_bytes()
    except OSError as error:
        raise EvidenceError(f"{label} could not be read: {error}") from error


def _load_argv_json(path: Path, label: str) -> list[str]:
    raw = _read_bounded_regular(path, label, MAX_JSON_BYTES)
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (EvidenceError, UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceError(f"{label} is not a JSON argv array: {error}") from error
    return _require_argv(value, label)


def _case_tuple(case_id: str) -> tuple[str, str, str]:
    try:
        return CASES[CASE_IDS.index(case_id)]
    except ValueError as error:
        raise EvidenceError(f"unsupported allocator evidence case: {case_id}") from error


def _load_backend_root(root: Path) -> dict[str, Any]:
    if _is_link(root) or not root.is_dir():
        raise EvidenceError("evidence writer root is missing or unsafe")
    run = _load_run(root)
    backend = _validate_backend(
        _read_canonical_json(root / "backend.json", "backend record"), run
    )
    profile = _artifact_record(root, "profile.kernel")
    if backend["build_sha256"] != profile["sha256"]:
        raise EvidenceError("backend build identity does not match profile.kernel")
    return backend


def _case_value(case_id: str) -> dict[str, str]:
    operation, phase, action = _case_tuple(case_id)
    return {
        "id": case_id,
        "operation": operation,
        "phase": phase,
        "action": action,
    }


def _expected_user_build_flag(case: tuple[str, str, str]) -> str:
    operation, phase, action = case
    return (
        "USER_EXTRA_CFLAGS=-Werror "
        f"-DFSALLOC_FAULT_OP={OPERATION_IDS[operation]} "
        f"-DFSALLOC_FAULT_PHASE={PHASE_IDS[phase]} "
        f"-DFSALLOC_FAULT_ACTION={ACTION_IDS[action]}"
    )


def _normalize_case_build_argv(
    argv: list[str], case: tuple[str, str, str], run: dict[str, Any]
) -> list[str]:
    tools = run["toolchain"]
    if len(argv) != 14:
        raise EvidenceError("case build argv length differs from the canonical build")
    values: dict[str, str] = {}
    for name, index in (("build_dir", 10), ("out_dir", 11), ("asm_dir", 12)):
        prefix = f"{name}="
        if not argv[index].startswith(prefix) or argv[index] == prefix:
            raise EvidenceError(f"case build argv lacks one private {name}")
        values[name] = argv[index][len(prefix) :]
    if argv[13] != f"{values['build_dir']}/riscv64/fsallocfault_ucore":
        raise EvidenceError("case build target is not inside its private build directory")
    normalized = list(argv)
    normalized[10:14] = [
        "build_dir=<BUILD>",
        "out_dir=<OUT>",
        "asm_dir=<ASM>",
        "<BUILD>/riscv64/fsallocfault_ucore",
    ]
    expected = [
        tools["make"]["resolved"],
        "-C",
        "user",
        "TOOLPREFIX=",
        "CHAPTER=agent",
        f"CC={tools['cross_gcc']['resolved']}",
        f"OBJCOPY={tools['cross_objcopy']['resolved']}",
        f"OBJDUMP={tools['cross_objdump']['resolved']}",
        f"PYTHON_BIN={tools['python']['resolved']}",
        "build_dir=<BUILD>",
        "out_dir=<OUT>",
        "asm_dir=<ASM>",
        "<BUILD>/riscv64/fsallocfault_ucore",
    ]
    expected.insert(9, _expected_user_build_flag(case))
    if normalized != expected:
        raise EvidenceError("case build argv differs from the canonical user build")
    return normalized


def _elf_image_shape(program_raw: bytes, elf_raw: bytes, label: str) -> dict[str, Any]:
    """Validate the mkfs ELF/flat pair and return its durable inode shape."""
    elf_header = struct.Struct("<16sHHIQQQIHHHHHH")
    program_header = struct.Struct("<IIQQQQQQ")
    if len(elf_raw) < elf_header.size:
        raise EvidenceError(f"{label} paired ELF has a truncated header")
    (
        ident,
        elf_type,
        machine,
        version,
        entry,
        program_header_offset,
        _section_header_offset,
        _flags,
        elf_header_size,
        program_header_size,
        program_header_count,
        _section_header_size,
        _section_header_count,
        _section_name_index,
    ) = elf_header.unpack_from(elf_raw)
    if (
        ident[:4] != b"\x7fELF"
        or ident[4] != 2
        or ident[5] != 1
        or ident[6] != 1
        or elf_type != 2
        or machine != 243
        or version != 1
        or entry != 0x1000
        or elf_header_size != elf_header.size
        or program_header_size != program_header.size
        or program_header_count == 0
        or program_header_count > 32
        or program_header_offset > len(elf_raw)
        or program_header_count * program_header.size
        > len(elf_raw) - program_header_offset
    ):
        raise EvidenceError(f"{label} paired ELF header is unsupported")

    load_segments: dict[str, dict[str, int]] = {}
    for index in range(program_header_count):
        (
            segment_type,
            segment_flags,
            file_offset,
            virtual_address,
            physical_address,
            file_size,
            memory_size,
            alignment,
        ) = program_header.unpack_from(
            elf_raw, program_header_offset + index * program_header.size
        )
        if segment_type != 1:
            continue
        if (
            file_size == 0
            or file_size != memory_size
            or virtual_address != physical_address
            or alignment != 4096
            or file_offset > len(elf_raw)
            or file_size > len(elf_raw) - file_offset
        ):
            raise EvidenceError(f"{label} paired ELF has an invalid load segment")
        kind = {5: "rx", 6: "rw"}.get(segment_flags)
        if kind is None or kind in load_segments:
            raise EvidenceError(
                f"{label} paired ELF must contain exactly one RX and one RW segment"
            )
        load_segments[kind] = {
            "file_offset": file_offset,
            "virtual_address": virtual_address,
            "bytes": file_size,
            "flags": segment_flags,
            "alignment": alignment,
        }
    if set(load_segments) != {"rx", "rw"}:
        raise EvidenceError(
            f"{label} paired ELF must contain exactly one RX and one RW segment"
        )
    rx = load_segments["rx"]
    rw = load_segments["rw"]
    rx_end = rx["virtual_address"] + rx["bytes"]
    rw_end = rw["virtual_address"] + rw["bytes"]
    expected_rw_address = (rx_end + 4095) & ~4095
    rw_offset = rw["virtual_address"] - 0x1000
    if (
        rx["virtual_address"] != 0x1000
        or not (rx["virtual_address"] <= entry < rx_end)
        or rw["virtual_address"] != expected_rw_address
        or rw_offset < 4096
        or rw_offset % 4096
        or rx["bytes"] > rw_offset
        or rw_end - 0x1000 != len(program_raw)
    ):
        raise EvidenceError(f"{label} paired ELF layout does not match the flat image")
    if (
        program_raw[: rx["bytes"]]
        != elf_raw[rx["file_offset"] : rx["file_offset"] + rx["bytes"]]
        or program_raw[rw_offset : rw_offset + rw["bytes"]]
        != elf_raw[rw["file_offset"] : rw["file_offset"] + rw["bytes"]]
        or any(program_raw[rx["bytes"] : rw_offset])
    ):
        raise EvidenceError(f"{label} flat image contents differ from the paired ELF")
    return {
        "format": "elf64-little-riscv-exec",
        "entry": entry,
        "layout_version": 1,
        "rw_offset": rw_offset,
        "flat_image_bytes": len(program_raw),
        "flat_image_sha256": hashlib.sha256(program_raw).hexdigest(),
        "elf_sha256": hashlib.sha256(elf_raw).hexdigest(),
        "rx": rx,
        "rw": rw,
    }


def _validate_case_build(
    root: Path, case_id: str, value: dict[str, Any], run: dict[str, Any]
) -> dict[str, Any]:
    case = _case_tuple(case_id)
    _require_fields(
        value,
        {
            "schema_version",
            "format",
            "run_id",
            "case",
            "build_argv",
            "program",
            "elf",
            "image_shape",
        },
        f"{case_id} build attestation",
    )
    if value["schema_version"] != SCHEMA_VERSION or value["format"] != BUILD_FORMAT:
        raise EvidenceError(f"{case_id} build attestation version is unsupported")
    if value["run_id"] != run["run_id"] or value["case"] != _case_value(case_id):
        raise EvidenceError(f"{case_id} build is not bound to this run and case")
    argv = _require_argv(value["build_argv"], f"{case_id} build argv")
    _normalize_case_build_argv(argv, case, run)
    program = _artifact_record(root, f"cases/{case_id}/program.bin")
    _validate_artifact_reference(value["program"], program, f"{case_id} program")
    elf = _artifact_record(root, f"cases/{case_id}/program.elf")
    _validate_artifact_reference(value["elf"], elf, f"{case_id} paired ELF")
    program_raw = _read_bounded_regular(
        root / program["path"], f"{case_id} sealed program", MAX_ARTIFACT_BYTES
    )
    elf_raw = _read_bounded_regular(
        root / elf["path"], f"{case_id} sealed paired ELF", MAX_ARTIFACT_BYTES
    )
    expected_shape = _elf_image_shape(program_raw, elf_raw, case_id)
    if value["image_shape"] != expected_shape:
        raise EvidenceError(f"{case_id} ELF shape is not bound to its sealed build")
    return value


def record_build(
    root: Path, case_id: str, program: Path, elf: Path, build_argv_json: Path
) -> dict[str, Any]:
    _case_tuple(case_id)
    run = _load_run(root)
    _load_backend_root(root)
    program_raw = _read_bounded_regular(
        program, f"{case_id} program", MAX_ARTIFACT_BYTES
    )
    elf_raw = _read_bounded_regular(elf, f"{case_id} paired ELF", MAX_ARTIFACT_BYTES)
    image_shape = _elf_image_shape(program_raw, elf_raw, case_id)
    argv = _load_argv_json(build_argv_json, f"{case_id} build argv")
    case_root = root / "cases" / case_id
    if case_root.exists():
        if _is_link(case_root) or not case_root.is_dir():
            raise EvidenceError(f"case directory is unsafe: {case_id}")
    else:
        case_root.mkdir()
    program_record = {
        "path": f"cases/{case_id}/program.bin",
        **_bytes_identity(program_raw),
    }
    elf_record = {
        "path": f"cases/{case_id}/program.elf",
        **_bytes_identity(elf_raw),
    }
    record = {
        "schema_version": SCHEMA_VERSION,
        "format": BUILD_FORMAT,
        "run_id": run["run_id"],
        "case": _case_value(case_id),
        "build_argv": argv,
        "program": program_record,
        "elf": elf_record,
        "image_shape": image_shape,
    }
    for path in (
        case_root / "program.bin",
        case_root / "program.elf",
        case_root / "build.json",
    ):
        if path.exists() or _is_link(path):
            raise EvidenceError(f"record-build refuses to overwrite: {path.name}")
    _atomic_publish_bytes(case_root / "program.bin", program_raw)
    _atomic_publish_bytes(case_root / "program.elf", elf_raw)
    _atomic_publish_bytes(case_root / "build.json", _render_json(record))
    _validate_case_build(root, case_id, record, run)
    return record


def _validate_workload_snapshot(
    snapshot: dict[str, Any], build: dict[str, Any], label: str
) -> dict[str, Any]:
    root_names = snapshot.get("root_names")
    inodes = snapshot.get("inodes")
    payloads = snapshot.get("payload_sha256")
    if not isinstance(root_names, dict) or not isinstance(inodes, dict) or not isinstance(
        payloads, dict
    ):
        raise EvidenceError(f"{label} lacks the workload inode inventory")
    inum = root_names.get(WORKLOAD_IMAGE_NAME)
    if isinstance(inum, bool) or not isinstance(inum, int) or inum <= 0:
        raise EvidenceError(
            f"{label} lacks the exact {WORKLOAD_SOURCE_NAME} inode payload"
        )
    key = str(inum)
    inode = inodes.get(key)
    if not isinstance(inode, dict):
        raise EvidenceError(f"{label} workload inode record is missing")
    shape = build["image_shape"]
    if (
        inode.get("type") != 2
        or inode.get("size") != build["program"]["bytes"]
        or inode.get("exec_layout_version") != shape["layout_version"]
        or inode.get("exec_rw_offset") != shape["rw_offset"]
        or payloads.get(key) != build["program"]["sha256"]
    ):
        raise EvidenceError(
            f"{label} workload payload or ELF-derived inode shape differs from build record"
        )
    return {
        "root_name": WORKLOAD_IMAGE_NAME,
        "inum": inum,
        "payload_sha256": payloads[key],
        "size": inode["size"],
        "layout_version": inode["exec_layout_version"],
        "rw_offset": inode["exec_rw_offset"],
        "program_sha256": build["program"]["sha256"],
        "elf_sha256": build["elf"]["sha256"],
    }


def _validate_workload_image_chain(
    snapshots: dict[str, dict[str, Any]], build: dict[str, Any], case_id: str
) -> dict[str, Any]:
    bindings = {
        stage: _validate_workload_snapshot(
            snapshots[stage], build, f"{case_id} {stage} image"
        )
        for stage in ("before", "fault", "reboot")
    }
    if len({json.dumps(value, sort_keys=True) for value in bindings.values()}) != 1:
        raise EvidenceError(f"{case_id} workload inode identity changed across stages")
    return {
        "build_program": build["program"],
        "build_elf": build["elf"],
        "image_shape": build["image_shape"],
        "stages": bindings,
    }


_RUNNER_OPTIONS = {
    "--init-proc",
    "--marker",
    "--marker-mode",
    "--log-file",
    "--case-timeout",
    "--idle-notice-seconds",
    "--marker-grace-seconds",
    "--qemu",
    "--kernel",
    "--image",
    "--completion-mode",
}


def _parse_runner_argv(argv: list[str], label: str) -> dict[str, str]:
    trusted = argv[4].replace("\\", "/") if len(argv) > 4 else ""
    target = PurePosixPath(argv[5].replace("\\", "/")) if len(argv) > 5 else None
    if (
        len(argv) < 8
        or argv[1:4] != ["-I", "-S", "-B"]
        or not trusted.endswith("/sources/scripts/trusted-python-entry.py")
        or target is None
        or target.as_posix() != "scripts/agent_test_runner.py"
    ):
        raise EvidenceError(
            f"{label} must invoke the runner through isolated trusted Python"
        )
    options: dict[str, str] = {}
    index = 6
    while index < len(argv):
        option = argv[index]
        if option not in _RUNNER_OPTIONS or option in options or index + 1 >= len(argv):
            raise EvidenceError(f"{label} has an unknown, duplicate, or valueless option")
        options[option] = argv[index + 1]
        index += 2
    required = _RUNNER_OPTIONS - {"--completion-mode"}
    if set(options) not in (required, _RUNNER_OPTIONS):
        raise EvidenceError(f"{label} option inventory differs from the canonical runner")
    if options["--init-proc"] != "fsallocfault_ucore" or options[
        "--marker-mode"
    ] != "exact-line":
        raise EvidenceError(f"{label} weakens the canonical Guest marker contract")
    completion = options.get("--completion-mode", "natural")
    if completion not in {"natural", "checkpoint", "powercut"}:
        raise EvidenceError(f"{label} completion mode is unsupported")
    options["completion"] = completion
    return options


def _expected_execution_semantics(
    case_id: str, stage: str, mutation: bool
) -> tuple[str, str, str]:
    operation, phase, action = _case_tuple(
        "alloc-intent-crash" if mutation else case_id
    )
    if mutation:
        if stage == "fault":
            return (
                "fsallocfault_kernel: durability_receipt_failed=1",
                "powercut",
                "flush-deletion-mutant.kernel",
            )
        if stage == "reboot":
            return (
                "fsallocfault_ucore: case=alloc phase=intent action=crash reboot_ready=1",
                "natural",
                "profile.kernel",
            )
        raise EvidenceError("delete-FLUSH mutation has only fault and reboot executions")
    if stage == "prepare":
        return (
            f"fsallocfault_ucore: case={operation} phase={phase} action={action} prepared=1",
            "checkpoint",
            "profile.kernel",
        )
    if stage == "fault":
        if action == "crash":
            return (
                f"fsallocfault_kernel: case={operation} phase={phase} crash_checkpoint=1",
                "powercut",
                "profile.kernel",
            )
        return ("fsallocfault_ucore: runtime_verified=1", "natural", "profile.kernel")
    if stage == "reboot":
        return (
            f"fsallocfault_ucore: case={operation} phase={phase} action={action} reboot_ready=1",
            "natural",
            "profile.kernel",
        )
    raise EvidenceError(f"unsupported execution stage: {stage}")


def _execution_relative(case_id: str, stage: str, mutation: bool) -> str:
    return (
        f"flush-deletion-{stage}.execution.json"
        if mutation
        else f"cases/{case_id}/{stage}.execution.json"
    )


def _execution_log_relative(case_id: str, stage: str, mutation: bool) -> str:
    return (
        f"flush-deletion-{stage}.guest.log"
        if mutation
        else f"cases/{case_id}/{stage}.guest.log"
    )


def record_execution(
    root: Path,
    case_id: str,
    stage: str,
    log_path: Path,
    launch_argv_json: Path,
    kernel: Path,
    input_image: Path,
    output_image: Path,
    started_ns: int,
    ended_ns: int,
    returncode: int,
    *,
    mutation: bool,
) -> dict[str, Any]:
    semantic_case = "alloc-intent-crash" if mutation else case_id
    _case_tuple(semantic_case)
    if (not mutation and stage not in STAGES) or (mutation and stage not in {"fault", "reboot"}):
        raise EvidenceError("execution stage is unsupported")
    if returncode != 0 or started_ns <= 0 or ended_ns <= started_ns:
        raise EvidenceError("execution must be a successful, positive-duration runner invocation")
    run = _load_run(root)
    backend = _load_backend_root(root)
    argv = _load_argv_json(launch_argv_json, f"{case_id} {stage} launch argv")
    options = _parse_runner_argv(argv, f"{case_id} {stage} launch argv")
    expected_marker, expected_completion, expected_kernel = _expected_execution_semantics(
        case_id, stage, mutation
    )
    if options["--marker"] != expected_marker or options["completion"] != expected_completion:
        raise EvidenceError("execution marker or completion mode differs from the stage contract")
    for option, path in (
        ("--log-file", log_path),
        ("--kernel", kernel),
        ("--image", output_image),
    ):
        try:
            if Path(options[option]).resolve(strict=True) != path.resolve(strict=True):
                raise EvidenceError(f"execution {option} is not bound to its captured artifact")
        except OSError as error:
            raise EvidenceError(f"execution {option} path is unavailable: {error}") from error
    log_raw = _read_bounded_regular(log_path, "execution Guest log", MAX_ARTIFACT_BYTES)
    kernel_raw = _read_bounded_regular(kernel, "execution kernel", MAX_ARTIFACT_BYTES)
    input_raw = _read_raw_image_input(input_image, "execution input image")
    output_raw = _read_raw_image_input(output_image, "execution output image")
    kernel_record = {"path": expected_kernel, **_bytes_identity(kernel_raw)}
    if expected_kernel == "profile.kernel" and kernel_record["sha256"] != backend[
        "build_sha256"
    ]:
        raise EvidenceError("execution profile kernel differs from the backend build")
    log_relative = _execution_log_relative(case_id, stage, mutation)
    record = {
        "schema_version": SCHEMA_VERSION,
        "format": EXECUTION_FORMAT,
        "run_id": run["run_id"],
        "source_commit": run["source"]["commit"],
        "case": _case_value(semantic_case),
        "stage": stage,
        "mutation": mutation,
        "started_ns": started_ns,
        "ended_ns": ended_ns,
        "returncode": returncode,
        "launch_argv": argv,
        "marker": expected_marker,
        "completion": expected_completion,
        "kernel": kernel_record,
        "input_image": _bytes_identity(input_raw),
        "output_image": _bytes_identity(output_raw),
        "source_log": {"path": log_relative, **_bytes_identity(log_raw)},
    }
    destination = root / _execution_relative(case_id, stage, mutation)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _atomic_publish_bytes(destination, _render_json(record))
    if mutation:
        _atomic_publish_bytes(root / log_relative, log_raw)
    return record


def _validate_image_identity(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an image identity")
    _require_fields(value, {"bytes", "sha256"}, label)
    size = _require_nonnegative_int(value["bytes"], f"{label} bytes")
    if size == 0 or size > MAX_RAW_IMAGE_BYTES or size % 1024:
        raise EvidenceError(f"{label} size is invalid")
    return {"bytes": size, "sha256": _require_sha256(value["sha256"], label)}


def _validate_execution(
    root: Path,
    case_id: str,
    stage: str,
    mutation: bool,
    value: dict[str, Any],
    run: dict[str, Any],
    backend: dict[str, Any],
) -> dict[str, Any]:
    _require_fields(
        value,
        {
            "schema_version",
            "format",
            "run_id",
            "source_commit",
            "case",
            "stage",
            "mutation",
            "started_ns",
            "ended_ns",
            "returncode",
            "launch_argv",
            "marker",
            "completion",
            "kernel",
            "input_image",
            "output_image",
            "source_log",
        },
        f"{case_id} {stage} execution",
    )
    semantic_case = "alloc-intent-crash" if mutation else case_id
    if (
        value["schema_version"] != SCHEMA_VERSION
        or value["format"] != EXECUTION_FORMAT
        or value["run_id"] != run["run_id"]
        or value["source_commit"] != run["source"]["commit"]
        or value["case"] != _case_value(semantic_case)
        or value["stage"] != stage
        or value["mutation"] is not mutation
        or value["returncode"] != 0
    ):
        raise EvidenceError(f"{case_id} {stage} execution identity differs")
    started = _require_nonnegative_int(value["started_ns"], "execution start")
    ended = _require_nonnegative_int(value["ended_ns"], "execution end")
    if started == 0 or ended <= started:
        raise EvidenceError(f"{case_id} {stage} execution duration is invalid")
    argv = _require_argv(value["launch_argv"], f"{case_id} {stage} launch argv")
    options = _parse_runner_argv(argv, f"{case_id} {stage} launch argv")
    marker, completion, kernel_path = _expected_execution_semantics(
        case_id, stage, mutation
    )
    if (
        value["marker"] != marker
        or value["completion"] != completion
        or options["--marker"] != marker
        or options["completion"] != completion
    ):
        raise EvidenceError(f"{case_id} {stage} execution semantics differ")
    tag = "mutation-alloc-intent-crash" if mutation else case_id
    expected_kernel_basename = (
        "fsalloc-delete-barrier-mutant-kernel"
        if kernel_path == "flush-deletion-mutant.kernel"
        else "fsalloc-profile-kernel"
    )
    if (
        argv[:8] != backend["launch_argv"]
        or argv[0] != run["toolchain"]["python"]["resolved"]
        or options["--qemu"] != run["toolchain"]["qemu"]["resolved"]
        or Path(options["--kernel"]).name != expected_kernel_basename
        or Path(options["--image"]).name != f"{tag}.img"
        or Path(options["--log-file"]).name != f"{tag}-{stage}.log"
    ):
        raise EvidenceError(
            f"{case_id} {stage} execution argv is not bound to attested tools and paths"
        )
    kernel = _artifact_record(root, kernel_path)
    _validate_artifact_reference(value["kernel"], kernel, "execution kernel")
    if kernel_path == "profile.kernel" and kernel["sha256"] != backend["build_sha256"]:
        raise EvidenceError("execution profile kernel differs from the backend")
    log_path = _execution_log_relative(case_id, stage, mutation)
    log_artifact = _validate_artifact_reference(
        value["source_log"], _artifact_record(root, log_path), "execution Guest log"
    )
    try:
        log_lines = (root / log_artifact["path"]).read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, UnicodeError) as error:
        raise EvidenceError(f"{case_id} {stage} execution log is unreadable: {error}") from error
    if log_lines.count(marker) != 1:
        raise EvidenceError(f"{case_id} {stage} execution lacks one exact success marker")
    if any(
        _GUEST_FAILURE_CLASSIFIER.is_failure_line(
            line, phase=_GUEST_FAILURE_CLASSIFIER.PHASE_GUEST
        )
        for line in log_lines
    ):
        raise EvidenceError(f"{case_id} {stage} execution log contains a Guest failure")
    _validate_image_identity(value["input_image"], "execution input image")
    _validate_image_identity(value["output_image"], "execution output image")
    return value


def _execution_inventory(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    run = _load_run(root)
    backend = _load_backend_root(root)
    builds: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    for case_id in CASE_IDS:
        case_root = root / "cases" / case_id
        build = _validate_case_build(
            root,
            case_id,
            _read_canonical_json(case_root / "build.json", f"{case_id} build"),
            run,
        )
        builds.append(
            {
                "case": case_id,
                "artifact": _artifact_record(root, f"cases/{case_id}/build.json"),
                "program": build["program"],
                "elf": build["elf"],
                "image_shape": build["image_shape"],
            }
        )
        for stage in STAGES:
            relative = _execution_relative(case_id, stage, False)
            execution = _validate_execution(
                root,
                case_id,
                stage,
                False,
                _read_canonical_json(root / relative, f"{case_id} {stage} execution"),
                run,
                backend,
            )
            executions.append(
                {
                    "case": case_id,
                    "stage": stage,
                    "mutation": False,
                    "started_ns": execution["started_ns"],
                    "ended_ns": execution["ended_ns"],
                    "artifact": _artifact_record(root, relative),
                }
            )
    for stage in ("fault", "reboot"):
        relative = _execution_relative("mutation-alloc-intent-crash", stage, True)
        execution = _validate_execution(
            root,
            "mutation-alloc-intent-crash",
            stage,
            True,
            _read_canonical_json(root / relative, f"mutation {stage} execution"),
            run,
            backend,
        )
        executions.append(
            {
                "case": "mutation-alloc-intent-crash",
                "stage": stage,
                "mutation": True,
                "started_ns": execution["started_ns"],
                "ended_ns": execution["ended_ns"],
                "artifact": _artifact_record(root, relative),
            }
        )
    executions.sort(key=lambda item: (item["started_ns"], item["ended_ns"]))
    expected_order = [
        (case_id, stage, False) for case_id in CASE_IDS for stage in STAGES
    ] + [
        ("mutation-alloc-intent-crash", "fault", True),
        ("mutation-alloc-intent-crash", "reboot", True),
    ]
    actual_order = [
        (item["case"], item["stage"], item["mutation"]) for item in executions
    ]
    if actual_order != expected_order:
        raise EvidenceError("runner transcript order differs from the canonical matrix")
    for index, execution in enumerate(executions):
        execution["ordinal"] = index
        if index and execution["started_ns"] < executions[index - 1]["ended_ns"]:
            raise EvidenceError("runner executions overlap or were replayed out of order")
    return builds, executions


def _expected_transcript(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    run = _load_run(root)
    builds, executions = _execution_inventory(root)
    transcript = {
        "schema_version": SCHEMA_VERSION,
        "format": TRANSCRIPT_FORMAT,
        "run_id": run["run_id"],
        "source_commit": run["source"]["commit"],
        "execution_count": len(executions),
        "executions": executions,
    }
    transcript_raw = _render_json(transcript)
    seal_inventory = {"builds": builds, "executions": executions}
    seal = {
        "schema_version": SCHEMA_VERSION,
        "format": SEAL_FORMAT,
        "run_id": run["run_id"],
        "source_commit": run["source"]["commit"],
        "source_dirty": run["source"]["dirty"],
        "case_build_count": len(builds),
        "execution_count": len(executions),
        "first_started_ns": executions[0]["started_ns"],
        "last_ended_ns": executions[-1]["ended_ns"],
        "transcript": {
            "path": "runner-transcript.json",
            **_bytes_identity(transcript_raw),
        },
        "inventory_sha256": hashlib.sha256(_render_json(seal_inventory)).hexdigest(),
    }
    return transcript, seal


def seal_run(root: Path) -> dict[str, Any]:
    for name in ("runner-transcript.json", "run-seal.json"):
        if (root / name).exists() or _is_link(root / name):
            raise EvidenceError(f"seal-run refuses to overwrite: {name}")
    transcript, seal = _expected_transcript(root)
    _atomic_publish_bytes(root / "runner-transcript.json", _render_json(transcript))
    _atomic_publish_bytes(root / "run-seal.json", _render_json(seal))
    return seal


def _validate_run_seal(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    actual_transcript = _read_canonical_json(
        root / "runner-transcript.json", "runner transcript"
    )
    actual_seal = _read_canonical_json(root / "run-seal.json", "run seal")
    expected_transcript, expected_seal = _expected_transcript(root)
    if actual_transcript != expected_transcript or actual_seal != expected_seal:
        raise EvidenceError("run transcript or seal does not match exact executions")
    return actual_transcript, actual_seal


def init_backend(
    root: Path,
    kernel: Path,
    compile_argv_json: Path,
    launch_argv_json: Path,
    *,
    capacity_bytes: int,
    identity: str,
    version: str,
    abi_version: str,
) -> dict[str, Any]:
    if root.exists():
        if _is_link(root) or not root.is_dir():
            raise EvidenceError("init-backend requires a non-symlink root")
        entries = {path.name for path in root.iterdir()}
        if entries not in (set(), {"run.json", "sources"}):
            raise EvidenceError(
                "init-backend root may contain only a captured run attestation"
            )
        if entries:
            _load_run(root)
    else:
        try:
            root.mkdir(parents=True)
        except OSError as error:
            raise EvidenceError(f"could not create evidence root: {error}") from error
    kernel_raw = _read_bounded_regular(kernel, "profile kernel", MAX_ARTIFACT_BYTES)
    compile_argv = _load_argv_json(compile_argv_json, "backend compile argv")
    launch_argv = _load_argv_json(launch_argv_json, "backend launch argv")
    identity = _require_text(identity, "backend identity", stable_id=True)
    version = _require_text(version, "backend version", stable_id=True)
    abi_version = _require_text(abi_version, "backend ABI version", stable_id=True)
    if capacity_bytes <= 0 or capacity_bytes > MAX_RAW_IMAGE_BYTES or capacity_bytes % 1024:
        raise EvidenceError("backend capacity must be positive, block-aligned, and at most 16 MiB")
    backend_record = {
        "schema_version": SCHEMA_VERSION,
        "format": BACKEND_FORMAT,
        "backend": {
            "identity": identity,
            "version": version,
            "abi_version": abi_version,
            "model": BACKEND_MODEL,
            "deterministic": True,
            "volatile_cache": True,
            "capacity_bytes": capacity_bytes,
            "build_sha256": hashlib.sha256(kernel_raw).hexdigest(),
            "compile_argv": compile_argv,
            "launch_argv": launch_argv,
        },
    }
    _validate_backend(backend_record, _load_run(root))
    try:
        (root / "cases").mkdir()
    except OSError as error:
        raise EvidenceError(f"could not initialize cases directory: {error}") from error
    _atomic_publish_bytes(root / "profile.kernel", kernel_raw)
    _atomic_publish_bytes(root / "backend.json", _render_json(backend_record))
    return backend_record


_RECEIPT_LINE = re.compile(
    r"fsalloc-cache: receipt_id=(?P<receipt_id>[A-Za-z0-9._:+-]+) "
    r"backend_instance_id=(?P<backend_instance_id>[A-Za-z0-9._:+-]+) "
    r"abi_version=(?P<abi_version>[A-Za-z0-9._:+-]+) "
    r"capacity_bytes=(?P<capacity_bytes>[0-9]+) "
    r"durable_epoch=(?P<durable_epoch>[0-9]+) "
    r"raw_write_count=(?P<raw_write_count>[0-9]+) "
    r"cached_write_count=(?P<cached_write_count>[0-9]+) "
    r"flush_command_count=(?P<flush_command_count>[0-9]+) "
    r"acknowledged_flush_count=(?P<acknowledged_flush_count>[0-9]+) "
    r"last_acknowledged_sequence=(?P<last_acknowledged_sequence>[0-9]+) "
    r"pending_before=(?P<pending_before>[0-9]+) "
    r"pending_after=(?P<pending_after>[0-9]+) "
    r"pending_at_stage_end=(?P<pending_at_stage_end>[0-9]+) "
    r"powercut_after_receipt=(?P<powercut>[01])\Z"
)


_PHYSICAL_FLUSH_LINE = re.compile(
    r"fsallocfault_ucore: flush_receipt stage=(?P<label>[a-z-]+) "
    r"abi=(?P<abi>[0-9]+) capacity=(?P<capacity>[0-9]+) "
    r"epoch_before=(?P<epoch_before>[0-9]+) epoch_after=(?P<epoch_after>[0-9]+) "
    r"pending_before=(?P<pending_before>[0-9]+) pending_after=(?P<pending_after>[0-9]+) "
    r"raw_writes_delta=(?P<raw_write>[0-9]+) "
    r"physical_write_delta=(?P<physical_write>[0-9]+) "
    r"physical_flush_delta=(?P<physical_flush>[0-9]+) "
    r"real_flush_delta=(?P<real_flush>[0-9]+) "
    r"failed_flush_delta=(?P<failed_flush>[0-9]+) "
    r"capacity_failures=(?P<capacity_failures>[0-9]+)\Z"
)

_PHYSICAL_OPERATION_LINE = re.compile(
    r"fsallocfault_ucore: operation_io raw=(?P<raw>[0-9]+) "
    r"physical_write=(?P<physical_write>[0-9]+) "
    r"flush=(?P<flush>[0-9]+) physical_flush=(?P<physical_flush>[0-9]+)\Z"
)


def _physical_flush_log_marker(
    label: str,
    abi: int,
    capacity: int,
    epoch_before: int,
    epoch_after: int,
    pending_before: int,
    pending_after: int,
    raw_write: int,
    physical_write: int,
    physical_flush: int,
    real_flush: int,
    failed_flush: int,
    capacity_failures: int,
) -> str:
    return (
        f"fsallocfault_ucore: flush_receipt stage={label} abi={abi} "
        f"capacity={capacity} epoch_before={epoch_before} "
        f"epoch_after={epoch_after} pending_before={pending_before} "
        f"pending_after={pending_after} raw_writes_delta={raw_write} "
        f"physical_write_delta={physical_write} "
        f"physical_flush_delta={physical_flush} real_flush_delta={real_flush} "
        f"failed_flush_delta={failed_flush} capacity_failures={capacity_failures}"
    )


def _physical_operation_log_marker(
    raw: int, physical_write: int, flush: int, physical_flush: int
) -> str:
    return (
        f"fsallocfault_ucore: operation_io raw={raw} "
        f"physical_write={physical_write} flush={flush} "
        f"physical_flush={physical_flush}"
    )


def _parse_physical_io_receipts(
    case: tuple[str, str, str],
    stage: str,
    backend: dict[str, Any],
    receipt: dict[str, Any],
    log_lines: list[str],
) -> dict[str, Any]:
    case_id = "-".join(case)
    flush_matches = [
        (index, match)
        for index, line in enumerate(log_lines)
        if (match := _PHYSICAL_FLUSH_LINE.fullmatch(line))
    ]
    expected_labels = (
        ["fault-baseline", "fault"]
        if stage == "fault" and case[2] != "crash"
        else ["fault-baseline"]
        if stage == "fault"
        else [stage]
    )
    if [match.group("label") for _, match in flush_matches] != expected_labels:
        raise EvidenceError(
            f"{case_id} {stage} physical flush receipt inventory differs"
        )
    flushes: list[dict[str, Any]] = []
    for _index, match in flush_matches:
        parsed = {
            "label": match.group("label"),
            "abi_version": int(match.group("abi")),
            "capacity_blocks": int(match.group("capacity")),
            "epoch_before": int(match.group("epoch_before")),
            "epoch_after": int(match.group("epoch_after")),
            "pending_before": int(match.group("pending_before")),
            "pending_after": int(match.group("pending_after")),
            "raw_write_delta": int(match.group("raw_write")),
            "physical_write_delta": int(match.group("physical_write")),
            "physical_flush_delta": int(match.group("physical_flush")),
            "logical_flush_delta": int(match.group("real_flush")),
            "failed_flush_delta": int(match.group("failed_flush")),
            "capacity_failures": int(match.group("capacity_failures")),
        }
        if (
            str(parsed["abi_version"]) != backend["abi_version"]
            or parsed["capacity_blocks"] * 1024 != backend["capacity_bytes"]
            or parsed["epoch_after"] != parsed["epoch_before"] + 1
            or parsed["pending_after"] != 0
            or parsed["raw_write_delta"] != parsed["pending_before"]
            or parsed["physical_write_delta"] != parsed["raw_write_delta"]
            or parsed["physical_flush_delta"] != 1
            or parsed["logical_flush_delta"] != parsed["physical_flush_delta"]
            or parsed["failed_flush_delta"] != 0
            or parsed["capacity_failures"] != 0
        ):
            raise EvidenceError(
                f"{case_id} {stage} physical flush receipt is inconsistent"
            )
        flushes.append(parsed)

    primary = next((item for item in flushes if item["label"] == stage), None)
    if primary is not None and (
        primary["epoch_after"] != receipt["durable_epoch"]
        or primary["pending_before"]
        != receipt["pending_write_count_before_flush"]
        or primary["pending_after"]
        != receipt["pending_write_count_after_flush"]
    ):
        raise EvidenceError(
            f"{case_id} {stage} physical receipt differs from durable receipt"
        )

    operation_matches = [
        (index, match)
        for index, line in enumerate(log_lines)
        if (match := _PHYSICAL_OPERATION_LINE.fullmatch(line))
    ]
    expected_operation_count = 1 if stage == "fault" and case[2] != "crash" else 0
    if len(operation_matches) != expected_operation_count:
        raise EvidenceError(
            f"{case_id} {stage} physical operation receipt inventory differs"
        )
    operations: list[dict[str, int]] = []
    for _index, match in operation_matches:
        parsed_operation = {
            "raw_write_delta": int(match.group("raw")),
            "physical_write_delta": int(match.group("physical_write")),
            "logical_flush_delta": int(match.group("flush")),
            "physical_flush_delta": int(match.group("physical_flush")),
        }
        if (
            parsed_operation["raw_write_delta"]
            != parsed_operation["physical_write_delta"]
            or parsed_operation["logical_flush_delta"]
            != parsed_operation["physical_flush_delta"]
        ):
            raise EvidenceError(
                f"{case_id} {stage} physical operation receipt is inconsistent"
            )
        operations.append(parsed_operation)
    if operation_matches and not (
        flush_matches[0][0] < operation_matches[0][0] < flush_matches[-1][0]
    ):
        raise EvidenceError(
            f"{case_id} fault physical receipts are not in execution order"
        )
    return {"flushes": flushes, "operations": operations}


def record_stage(
    root: Path,
    case_id: str,
    stage: str,
    log_path: Path,
    launch_argv_json: Path,
) -> dict[str, Any]:
    case = _case_tuple(case_id)
    if stage not in STAGES:
        raise EvidenceError(f"unsupported allocator evidence stage: {stage}")
    backend = _load_backend_root(root)
    log_raw = _read_bounded_regular(log_path, f"{case_id} {stage} Guest log", MAX_ARTIFACT_BYTES)
    try:
        log_lines = log_raw.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise EvidenceError(f"{case_id} {stage} Guest log is not UTF-8: {error}") from error
    matches = [match for line in log_lines if (match := _RECEIPT_LINE.fullmatch(line))]
    if len(matches) != 1:
        raise EvidenceError(f"{case_id} {stage} Guest log must contain one exact flush receipt")
    match = matches[0]
    if (
        match.group("abi_version") != backend["abi_version"]
        or int(match.group("capacity_bytes")) != backend["capacity_bytes"]
    ):
        raise EvidenceError(f"{case_id} {stage} receipt backend ABI or capacity mismatch")
    launch_argv = _load_argv_json(
        launch_argv_json, f"{case_id} {stage} launch argv"
    )
    expected_log_path = f"cases/{case_id}/{stage}.guest.log"
    log_record = {
        "path": expected_log_path,
        "bytes": len(log_raw),
        "sha256": hashlib.sha256(log_raw).hexdigest(),
    }
    receipt_backend = {
        key: backend[key]
        for key in (
            "identity",
            "version",
            "abi_version",
            "model",
            "deterministic",
            "volatile_cache",
            "capacity_bytes",
            "build_sha256",
        )
    }
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "format": RECEIPT_FORMAT,
        "case": {
            "id": case_id,
            "operation": case[0],
            "phase": case[1],
            "action": case[2],
        },
        "stage": stage,
        "backend": receipt_backend,
        "launch_argv": launch_argv,
        "receipt": {
            "backend_instance_id": match.group("backend_instance_id"),
            "receipt_id": match.group("receipt_id"),
            "raw_write_count": int(match.group("raw_write_count")),
            "cached_write_count": int(match.group("cached_write_count")),
            "flush_command_count": int(match.group("flush_command_count")),
            "acknowledged_flush_count": int(match.group("acknowledged_flush_count")),
            "last_acknowledged_sequence": int(match.group("last_acknowledged_sequence")),
            "durable_epoch": int(match.group("durable_epoch")),
            "pending_write_count_before_flush": int(match.group("pending_before")),
            "pending_write_count_after_flush": int(match.group("pending_after")),
            "pending_write_count_at_stage_end": int(match.group("pending_at_stage_end")),
            "powercut_after_receipt": match.group("powercut") == "1",
        },
        "source_log": log_record,
    }
    receipt["physical_io"] = _parse_physical_io_receipts(
        case, stage, backend, receipt["receipt"], log_lines
    )
    _validate_receipt(receipt, case, stage, backend, log_record, log_path)
    case_root = root / "cases" / case_id
    if case_root.exists():
        if _is_link(case_root) or not case_root.is_dir():
            raise EvidenceError(f"case directory is unsafe: {case_id}")
    else:
        case_root.mkdir()
    _atomic_publish_bytes(case_root / f"{stage}.guest.log", log_raw)
    _atomic_publish_bytes(case_root / f"{stage}.flush.json", _render_json(receipt))
    return receipt


def _read_raw_image_input(path: Path, label: str) -> bytes:
    raw = _read_bounded_regular(path, label, MAX_RAW_IMAGE_BYTES)
    if len(raw) % 1024:
        raise EvidenceError(f"{label} is not block-aligned")
    return raw


def record_case(
    root: Path,
    case_id: str,
    before_image: Path,
    fault_image: Path,
    reboot_image: Path,
) -> dict[str, Any]:
    case = _case_tuple(case_id)
    _load_backend_root(root)
    run = _load_run(root)
    build = _validate_case_build(
        root,
        case_id,
        _read_canonical_json(
            root / "cases" / case_id / "build.json", f"{case_id} build"
        ),
        run,
    )
    inputs = {
        "before": before_image,
        "fault": fault_image,
        "reboot": reboot_image,
    }
    raw = {
        stage: _read_raw_image_input(path, f"{case_id} {stage} image")
        for stage, path in inputs.items()
    }
    with tempfile.TemporaryDirectory(prefix=f"fsalloc-record-{case_id}-") as temporary:
        snapshots, fault_diff, reboot_diff, canonical, verified = _raw_case_cli_results(
            Path(temporary), inputs, case
        )
    _validate_workload_image_chain(snapshots, build, case_id)
    case_root = root / "cases" / case_id
    if case_root.exists():
        if _is_link(case_root) or not case_root.is_dir():
            raise EvidenceError(f"case directory is unsafe: {case_id}")
    else:
        case_root.mkdir()
    outputs = {
        "before.img.gz": _canonical_gzip(raw["before"]),
        "before.snapshot.json": _render_json(snapshots["before"]),
        "fault.img.gz": _canonical_gzip(raw["fault"]),
        "fault.snapshot.json": _render_json(snapshots["fault"]),
        "fault.diff.json": _render_json(fault_diff),
        "reboot.img.gz": _canonical_gzip(raw["reboot"]),
        "reboot.snapshot.json": _render_json(snapshots["reboot"]),
        "reboot.canonical.json": _render_json(canonical),
        "reboot.diff.json": _render_json(reboot_diff),
        "verified.json": _render_json(verified),
    }
    for name in outputs:
        if (case_root / name).exists() or _is_link(case_root / name):
            raise EvidenceError(f"record-case refuses to overwrite: {case_id}/{name}")
    for name, data in outputs.items():
        _atomic_publish_bytes(case_root / name, data)
    return verified


_MUTATION_OVERLAY_LINE = re.compile(
    r"fsalloc-cache: mutation=delete-flush target=allocator-phase-barrier "
    r"durable_epoch=(?P<durable_epoch>[0-9]+) "
    r"pending_at_powercut=(?P<pending>[0-9]+) "
    r"discarded_on_powercut=(?P<discarded>[0-9]+) powercut=1\Z"
)


def record_mutation(
    root: Path,
    baseline_kernel: Path,
    mutant_kernel: Path,
    selection_diff: Path,
    before_image: Path,
    fault_image: Path,
    reboot_image: Path,
    log_path: Path,
    baseline_compile_argv_json: Path,
    mutant_compile_argv_json: Path,
    command_argv_json: Path,
) -> dict[str, Any]:
    backend = _load_backend_root(root)
    baseline_raw = _read_bounded_regular(
        baseline_kernel, "mutation baseline kernel", MAX_ARTIFACT_BYTES
    )
    profile_raw = _read_bounded_regular(
        root / "profile.kernel", "packaged profile kernel", MAX_ARTIFACT_BYTES
    )
    if baseline_raw != profile_raw:
        raise EvidenceError("mutation baseline kernel differs from profile.kernel")
    mutant_raw = _read_bounded_regular(
        mutant_kernel, "mutation mutant kernel", MAX_ARTIFACT_BYTES
    )
    if hashlib.sha256(mutant_raw).digest() == hashlib.sha256(baseline_raw).digest():
        raise EvidenceError("mutation kernels are identical")
    diff_raw = _read_bounded_regular(
        selection_diff, "mutation selection diff", MAX_JSON_BYTES
    )
    try:
        diff_text = diff_raw.decode("utf-8")
    except UnicodeError as error:
        raise EvidenceError(f"mutation selection diff is not UTF-8: {error}") from error
    if (
        "FS_ALLOCATOR_DELETE_BARRIER_MUTANT" not in diff_text
        or "fs_durable_barrier_forward" not in diff_text
    ):
        raise EvidenceError("mutation selection diff does not show the guarded barrier bypass")
    raw_images = {
        "before": _read_raw_image_input(before_image, "mutation before image"),
        "fault": _read_raw_image_input(fault_image, "mutation fault image"),
        "reboot": _read_raw_image_input(reboot_image, "mutation reboot image"),
    }
    log_raw = _read_bounded_regular(log_path, "mutation QEMU log", MAX_ARTIFACT_BYTES)
    try:
        log_text = log_raw.decode("utf-8")
    except UnicodeError as error:
        raise EvidenceError(f"mutation QEMU log is not UTF-8: {error}") from error
    log_lines = log_text.splitlines()
    if log_lines.count("fsallocfault_kernel: durability_receipt_failed=1") != 1:
        raise EvidenceError("mutation QEMU log lacks the unique durability failure marker")
    overlay_matches = [
        match for line in log_lines if (match := _MUTATION_OVERLAY_LINE.fullmatch(line))
    ]
    if len(overlay_matches) != 1:
        raise EvidenceError("mutation QEMU log lacks one exact overlay powercut marker")
    overlay = overlay_matches[0]
    durable_epoch = int(overlay.group("durable_epoch"))
    pending = int(overlay.group("pending"))
    discarded = int(overlay.group("discarded"))
    if durable_epoch == 0 or pending != 1 or discarded != 1:
        raise EvidenceError(
            "mutation overlay marker must prove exactly one pending qmap write was lost"
        )

    baseline_compile_argv = _load_argv_json(
        baseline_compile_argv_json, "mutation baseline compile argv"
    )
    mutant_compile_argv = _load_argv_json(
        mutant_compile_argv_json, "mutation mutant compile argv"
    )
    command = _load_argv_json(command_argv_json, "mutation QEMU command argv")
    with tempfile.TemporaryDirectory(prefix="fsalloc-mutation-writer-") as temporary:
        directory = Path(temporary)
        paths = {
            stage: _write_raw_temp(directory, f"{stage}.img", raw)
            for stage, raw in raw_images.items()
        }
        fault_snapshot = _run_image_cli_json(
            directory,
            ["snapshot", str(paths["fault"])],
            "fault.snapshot.actual.json",
            "mutant fault snapshot",
        )
        if any(
            entry["state"] == "ALLOCATING"
            for entry in dict(fault_snapshot["qmap_entries"]).values()
        ):
            raise EvidenceError("mutation fault image persisted the bypassed alloc intent")
        _raw_mutation_control_acceptance(directory, paths)
        verifier_exit_code, verifier_error = _raw_mutation_cli_rejection(directory, paths)

    baseline_sha256 = hashlib.sha256(baseline_raw).hexdigest()
    mutant_sha256 = hashlib.sha256(mutant_raw).hexdigest()
    result_marker = (
        "fsalloc-mutation: mutation=delete-flush target=allocator-phase-barrier "
        "case=alloc-intent-crash "
        f"baseline_kernel_sha256={baseline_sha256} "
        f"mutant_kernel_sha256={mutant_sha256} "
        f"verifier_exit_code={verifier_exit_code} outcome=verification-rejected"
    )
    if any(line.startswith("fsalloc-mutation:") for line in log_lines):
        raise EvidenceError("record-mutation owns the host result marker")
    complete_log = log_raw + (b"" if log_raw.endswith(b"\n") else b"\n") + result_marker.encode("ascii") + b"\n"

    with tempfile.TemporaryDirectory(prefix="fsalloc-mutation-stage-") as temporary:
        staging = Path(temporary)
        staged_bytes = {
            "profile.kernel": baseline_raw,
            "flush-deletion-mutant.kernel": mutant_raw,
            "flush-deletion-selection.diff": diff_raw,
            "flush-deletion-before.img.gz": _canonical_gzip(raw_images["before"]),
            "flush-deletion-fault.img.gz": _canonical_gzip(raw_images["fault"]),
            "flush-deletion-reboot.img.gz": _canonical_gzip(raw_images["reboot"]),
            "flush-deletion-mutation.log": complete_log,
        }
        for name, data in staged_bytes.items():
            (staging / name).write_bytes(data)
        artifacts = {
            name: _artifact_record(staging, name) for name in staged_bytes
        }
        images = {
            stage: {
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            for stage, raw in raw_images.items()
        }
        mutation = {
            "schema_version": SCHEMA_VERSION,
            "format": MUTATION_FORMAT,
            "mutation": DELETE_FLUSH_MUTATION,
            "mutation_target": "allocator-phase-barrier",
            "status": "passed",
            "backend_identity": backend["identity"],
            "backend_version": backend["version"],
            "case": {
                "id": "alloc-intent-crash",
                "operation": "alloc",
                "phase": "intent",
                "action": "crash",
            },
            "baseline_compile_argv": baseline_compile_argv,
            "mutant_compile_argv": mutant_compile_argv,
            "command": command,
            "mutant_verification_exit_code": verifier_exit_code,
            "expected_outcome": "verification-rejected",
            "observed_outcome": "verification-rejected",
            "verifier_error": verifier_error,
            "powercut": {
                "durable_epoch": durable_epoch,
                "pending_write_count": pending,
                "discarded_write_count": discarded,
            },
            "baseline_kernel": artifacts["profile.kernel"],
            "mutant_kernel": artifacts["flush-deletion-mutant.kernel"],
            "selection_diff": artifacts["flush-deletion-selection.diff"],
            "images": images,
            "log": artifacts["flush-deletion-mutation.log"],
        }
        _validate_mutation(
            mutation, backend, staging, artifacts, evidence_root=root
        )

    publish = {
        name: data for name, data in staged_bytes.items() if name != "profile.kernel"
    }
    publish["flush-deletion-mutation.json"] = _render_json(mutation)
    for name in publish:
        if (root / name).exists() or _is_link(root / name):
            raise EvidenceError(f"record-mutation refuses to overwrite: {name}")
    for name, data in publish.items():
        _atomic_publish_bytes(root / name, data)
    return mutation


def write_manifest(evidence_root: Path) -> dict[str, Any]:
    manifest = construct_manifest(evidence_root)
    root = evidence_root.resolve(strict=True)
    destination = root / "manifest.json"
    if _is_link(destination):
        raise EvidenceError("manifest path must not be a symlink")
    temporary = root / f".manifest.json.tmp.{os.getpid()}"
    if temporary.exists() or _is_link(temporary):
        raise EvidenceError("manifest temporary path already exists")
    try:
        with temporary.open("xb") as handle:
            handle.write(_render_json(manifest))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    verify_manifest(root)
    return manifest


def verify_manifest(evidence_root: Path) -> dict[str, Any]:
    if _is_link(evidence_root) or not evidence_root.is_dir():
        raise EvidenceError("evidence root is missing or is a symlink")
    root = evidence_root.resolve(strict=True)
    _reject_links(root)
    _validate_exact_tree(root, require_manifest=True)
    actual = _read_canonical_json(root / "manifest.json", "evidence manifest")
    expected = construct_manifest(root)
    if actual != expected:
        raise EvidenceError("evidence manifest does not match the exact artifact inventory")
    return actual


def _archive_directories() -> tuple[str, ...]:
    source_directories = {"sources"}
    for relative in SOURCE_FILES:
        parent = PurePosixPath(relative).parent
        while str(parent) != ".":
            source_directories.add(parent.as_posix())
            parent = parent.parent
    return tuple(sorted(source_directories)) + ("cases",) + tuple(
        f"cases/{case_id}" for case_id in CASE_IDS
    )


def _archive_files() -> tuple[str, ...]:
    root_files = ROOT_SOURCE_FILES + SOURCE_FILES + ("manifest.json",)
    case_files = tuple(
        f"cases/{case_id}/{name}" for case_id in CASE_IDS for name in CASE_FILES
    )
    return root_files + case_files


def _archive_inventory() -> dict[str, str]:
    inventory = {name: "directory" for name in _archive_directories()}
    inventory.update({name: "file" for name in _archive_files()})
    return inventory


def _canonical_tar_info(name: str, kind: str, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    info.linkname = ""
    if kind == "directory":
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.size = 0
    else:
        info.type = tarfile.REGTYPE
        info.mode = 0o644
        info.size = size
    return info


def _write_canonical_archive(root: Path, destination: Path) -> None:
    inventory = _archive_inventory()
    try:
        with tarfile.open(destination, mode="x:", format=tarfile.USTAR_FORMAT) as archive:
            for name in sorted(inventory):
                kind = inventory[name]
                if kind == "directory":
                    archive.addfile(_canonical_tar_info(name, kind))
                    continue
                path = root.joinpath(*PurePosixPath(name).parts)
                if _is_link(path) or not path.is_file():
                    raise EvidenceError(f"archive source is missing or unsafe: {name}")
                size = path.stat().st_size
                if size <= 0 or size > MAX_ARTIFACT_BYTES:
                    raise EvidenceError(f"archive source has an invalid size: {name}")
                with path.open("rb") as handle:
                    archive.addfile(_canonical_tar_info(name, kind, size), handle)
    except EvidenceError:
        raise
    except (OSError, tarfile.TarError) as error:
        raise EvidenceError(f"could not create deterministic evidence archive: {error}") from error


def _validate_archive_member_name(name: str) -> None:
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or "\x00" in name
        or name.endswith("/")
    ):
        raise EvidenceError(f"archive contains an unsafe member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise EvidenceError(f"archive contains an unsafe member path: {name!r}")
    if path.as_posix() != name:
        raise EvidenceError(f"archive member path is not canonical: {name!r}")


def _validate_archive_members(
    archive: tarfile.TarFile,
) -> tuple[dict[str, tarfile.TarInfo], int]:
    expected = _archive_inventory()
    members: dict[str, tarfile.TarInfo] = {}
    total_size = 0
    for member in archive:
        _validate_archive_member_name(member.name)
        if member.name in members:
            raise EvidenceError(f"archive contains duplicate member: {member.name}")
        members[member.name] = member
        expected_kind = expected.get(member.name)
        if expected_kind is None:
            raise EvidenceError(f"archive contains an unexpected member: {member.name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise EvidenceError(f"archive contains a forbidden special member: {member.name}")
        if expected_kind == "directory":
            if not member.isdir() or member.size != 0 or member.mode != 0o755:
                raise EvidenceError(f"archive directory metadata is not canonical: {member.name}")
        else:
            if not member.isreg() or member.size <= 0 or member.mode != 0o644:
                raise EvidenceError(f"archive file metadata is not canonical: {member.name}")
            if member.size > MAX_ARTIFACT_BYTES:
                raise EvidenceError(f"archive artifact is too large: {member.name}")
            total_size += member.size
        if (
            member.uid != 0
            or member.gid != 0
            or member.uname != ""
            or member.gname != ""
            or member.mtime != 0
            or member.linkname != ""
            or member.pax_headers
        ):
            raise EvidenceError(f"archive member metadata is not canonical: {member.name}")
    actual_names = set(members)
    expected_names = set(expected)
    if actual_names != expected_names:
        raise EvidenceError(
            "archive inventory mismatch: "
            f"missing={sorted(expected_names - actual_names)}, "
            f"extra={sorted(actual_names - expected_names)}"
        )
    if total_size > MAX_ARCHIVE_BYTES:
        raise EvidenceError("archive expands beyond the evidence size limit")
    return members, total_size


def _extract_verified_archive(archive_path: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members, _ = _validate_archive_members(archive)
            for name in sorted(_archive_directories()):
                destination.joinpath(*PurePosixPath(name).parts).mkdir(parents=True)
            for name in sorted(_archive_files()):
                member = members[name]
                source = archive.extractfile(member)
                if source is None:
                    raise EvidenceError(f"archive file cannot be read: {name}")
                target = destination.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                remaining = member.size
                with target.open("xb") as handle:
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise EvidenceError(f"archive file is truncated: {name}")
                        handle.write(chunk)
                        remaining -= len(chunk)
                    if source.read(1):
                        raise EvidenceError(f"archive file exceeds its declared size: {name}")
    except EvidenceError:
        raise
    except (OSError, tarfile.TarError, EOFError) as error:
        raise EvidenceError(f"invalid evidence archive: {error}") from error


def _verify_archive_content(archive_path: Path, *, enforce_basename: bool) -> dict[str, Any]:
    if enforce_basename and archive_path.name != ARCHIVE_BASENAME:
        raise EvidenceError(f"evidence archive basename must be {ARCHIVE_BASENAME}")
    if _is_link(archive_path) or not archive_path.is_file():
        raise EvidenceError("evidence archive is missing or is a symlink")
    size = archive_path.stat().st_size
    if size <= 0 or size > MAX_ARCHIVE_BYTES:
        raise EvidenceError("evidence archive size is invalid")
    with tempfile.TemporaryDirectory(prefix="fs-allocator-evidence-") as temporary:
        temporary_root = Path(temporary)
        extracted = temporary_root / "extracted"
        extracted.mkdir()
        _extract_verified_archive(archive_path, extracted)
        manifest = verify_manifest(extracted)
        canonical = temporary_root / ARCHIVE_BASENAME
        _write_canonical_archive(extracted, canonical)
        if (
            archive_path.stat().st_size != canonical.stat().st_size
            or _sha256_file(archive_path) != _sha256_file(canonical)
        ):
            raise EvidenceError("evidence archive bytes are not canonical")
        return manifest


def pack_archive(evidence_root: Path, output: Path) -> dict[str, Any]:
    manifest = verify_manifest(evidence_root)
    if output.name != ARCHIVE_BASENAME:
        raise EvidenceError(f"evidence archive basename must be {ARCHIVE_BASENAME}")
    try:
        root = evidence_root.resolve(strict=True)
        parent = output.parent.resolve(strict=True)
    except OSError as error:
        raise EvidenceError(f"archive output path is unavailable: {error}") from error
    destination = parent / output.name
    try:
        destination.relative_to(root)
    except ValueError:
        pass
    else:
        raise EvidenceError("evidence archive must be outside the evidence directory")
    if _is_link(destination) or (destination.exists() and not destination.is_file()):
        raise EvidenceError("archive output is a symlink or is not a regular file")
    temporary = parent / f".{ARCHIVE_BASENAME}.tmp.{os.getpid()}"
    if temporary.exists() or _is_link(temporary):
        raise EvidenceError("archive temporary path already exists")
    try:
        _write_canonical_archive(root, temporary)
        _verify_archive_content(temporary, enforce_basename=False)
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    verified = verify_archive(destination)
    if verified != manifest:
        raise EvidenceError("packed archive manifest changed during publication")
    return verified


def _copy_archive_to_private(source: Path, destination: Path) -> None:
    try:
        lexical = source.lstat()
    except OSError as error:
        raise EvidenceError(f"evidence archive is unavailable: {error}") from error
    if stat.S_ISLNK(lexical.st_mode) or not stat.S_ISREG(lexical.st_mode):
        raise EvidenceError("evidence archive is not a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as error:
        raise EvidenceError(f"could not safely open evidence archive: {error}") from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size <= 0 or opened.st_size > MAX_ARCHIVE_BYTES:
            raise EvidenceError("evidence archive size or file type is invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as input_handle:
            with destination.open("xb") as output_handle:
                remaining = opened.st_size
                while remaining:
                    chunk = input_handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise EvidenceError("evidence archive changed while being copied")
                    output_handle.write(chunk)
                    remaining -= len(chunk)
                if input_handle.read(1):
                    raise EvidenceError("evidence archive grew while being copied")
    except OSError as error:
        raise EvidenceError(f"could not copy evidence archive privately: {error}") from error
    finally:
        os.close(descriptor)


def verify_archive(archive_path: Path) -> dict[str, Any]:
    if archive_path.name != ARCHIVE_BASENAME:
        raise EvidenceError(f"evidence archive basename must be {ARCHIVE_BASENAME}")
    with tempfile.TemporaryDirectory(prefix="fs-allocator-archive-input-") as temporary:
        private = Path(temporary) / ARCHIVE_BASENAME
        _copy_archive_to_private(archive_path, private)
        return _verify_archive_content(private, enforce_basename=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--root", type=Path, required=True)
    pack_parser = subparsers.add_parser("pack")
    pack_parser.add_argument("--root", type=Path, required=True)
    pack_parser.add_argument("--output", type=Path, required=True)
    archive_parser = subparsers.add_parser("verify-archive")
    archive_parser.add_argument("--archive", type=Path, required=True)
    run_parser = subparsers.add_parser("capture-run")
    run_parser.add_argument("--root", type=Path, required=True)
    run_parser.add_argument("--source-root", type=Path, required=True)
    run_parser.add_argument("--run-id", required=True)
    run_parser.add_argument("--qemu", required=True)
    run_parser.add_argument("--python", required=True)
    run_parser.add_argument("--toolprefix")
    run_parser.add_argument("--make", default="make")
    run_parser.add_argument("--host-cc")
    run_parser.add_argument("--cross-gcc")
    run_parser.add_argument("--cross-ld")
    run_parser.add_argument("--cross-objcopy")
    run_parser.add_argument("--cross-objdump")
    tool_parser = subparsers.add_parser("tool-path")
    tool_parser.add_argument("--root", type=Path, required=True)
    tool_parser.add_argument(
        "--tool",
        choices=(
            "python",
            "qemu",
            "make",
            "host_cc",
            "cross_gcc",
            "cross_ld",
            "cross_objcopy",
            "cross_objdump",
        ),
        required=True,
    )
    materialize_parser = subparsers.add_parser("materialize-source")
    materialize_parser.add_argument("--root", type=Path, required=True)
    materialize_parser.add_argument("--output", type=Path, required=True)
    boundary_parser = subparsers.add_parser("verify-source-boundary")
    boundary_parser.add_argument("--root", type=Path, required=True)
    boundary_parser.add_argument("--source-root", type=Path, required=True)
    boundary_parser.add_argument("--snapshot-root", type=Path, required=True)
    boundary_parser.add_argument("--boundary", required=True)
    clean_parser = subparsers.add_parser("clean-exec")
    clean_parser.add_argument("argv", nargs=argparse.REMAINDER)
    init_parser = subparsers.add_parser("init-backend")
    init_parser.add_argument("--root", type=Path, required=True)
    init_parser.add_argument("--kernel", type=Path, required=True)
    init_parser.add_argument("--compile-argv-json", type=Path, required=True)
    init_parser.add_argument("--launch-argv-json", type=Path, required=True)
    init_parser.add_argument("--capacity-bytes", type=int, required=True)
    init_parser.add_argument("--identity", default="agentos-virtio-ram-overlay")
    init_parser.add_argument("--backend-version", default="1")
    init_parser.add_argument("--abi-version", default="2")
    build_parser = subparsers.add_parser("record-build")
    build_parser.add_argument("--root", type=Path, required=True)
    build_parser.add_argument("--case", required=True)
    build_parser.add_argument("--program", type=Path, required=True)
    build_parser.add_argument("--elf", type=Path, required=True)
    build_parser.add_argument("--build-argv-json", type=Path, required=True)
    execution_parser = subparsers.add_parser("record-execution")
    execution_parser.add_argument("--root", type=Path, required=True)
    execution_parser.add_argument("--case", required=True)
    execution_parser.add_argument("--stage", required=True)
    execution_parser.add_argument("--log", type=Path, required=True)
    execution_parser.add_argument("--launch-argv-json", type=Path, required=True)
    execution_parser.add_argument("--kernel", type=Path, required=True)
    execution_parser.add_argument("--input-image", type=Path, required=True)
    execution_parser.add_argument("--output-image", type=Path, required=True)
    execution_parser.add_argument("--started-ns", type=int, required=True)
    execution_parser.add_argument("--ended-ns", type=int, required=True)
    execution_parser.add_argument("--returncode", type=int, required=True)
    execution_parser.add_argument("--mutation", action="store_true")
    seal_parser = subparsers.add_parser("seal-run")
    seal_parser.add_argument("--root", type=Path, required=True)
    stage_parser = subparsers.add_parser("record-stage")
    stage_parser.add_argument("--root", type=Path, required=True)
    stage_parser.add_argument("--case", required=True)
    stage_parser.add_argument("--stage", choices=STAGES, required=True)
    stage_parser.add_argument("--log", type=Path, required=True)
    stage_parser.add_argument("--launch-argv-json", type=Path, required=True)
    case_parser = subparsers.add_parser("record-case")
    case_parser.add_argument("--root", type=Path, required=True)
    case_parser.add_argument("--case", required=True)
    case_parser.add_argument("--before-image", type=Path, required=True)
    case_parser.add_argument("--fault-image", type=Path, required=True)
    case_parser.add_argument("--reboot-image", type=Path, required=True)
    mutation_parser = subparsers.add_parser("record-mutation")
    mutation_parser.add_argument("--root", type=Path, required=True)
    mutation_parser.add_argument("--baseline-kernel", type=Path, required=True)
    mutation_parser.add_argument("--mutant-kernel", type=Path, required=True)
    mutation_parser.add_argument("--selection-diff", type=Path, required=True)
    mutation_parser.add_argument("--before-image", type=Path, required=True)
    mutation_parser.add_argument("--fault-image", type=Path, required=True)
    mutation_parser.add_argument("--reboot-image", type=Path, required=True)
    mutation_parser.add_argument("--log", type=Path, required=True)
    mutation_parser.add_argument("--baseline-compile-argv-json", type=Path, required=True)
    mutation_parser.add_argument("--mutant-compile-argv-json", type=Path, required=True)
    mutation_parser.add_argument("--command-argv-json", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        writer_label = ""
        if args.command == "build":
            manifest = write_manifest(args.root)
        elif args.command == "verify":
            manifest = verify_manifest(args.root)
        elif args.command == "pack":
            manifest = pack_archive(args.root, args.output)
        elif args.command == "verify-archive":
            manifest = verify_archive(args.archive)
        elif args.command == "capture-run":
            capture_run(
                args.root,
                args.source_root,
                args.run_id,
                args.qemu,
                args.python,
                args.toolprefix,
                args.make,
                args.host_cc,
                args.cross_gcc,
                args.cross_ld,
                args.cross_objcopy,
                args.cross_objdump,
            )
            writer_label = "run captured"
            manifest = None
        elif args.command == "tool-path":
            print(attested_tool_path(args.root, args.tool))
            return 0
        elif args.command == "materialize-source":
            materialize_source(args.root, args.output)
            writer_label = "source materialized"
            manifest = None
        elif args.command == "verify-source-boundary":
            boundary = verify_source_boundary(
                args.root,
                args.source_root,
                args.snapshot_root,
                args.boundary,
            )
            writer_label = (
                f"source boundary verified boundary={boundary['boundary']} "
                f"snapshot={boundary['snapshot_sha256']}"
            )
            manifest = None
        elif args.command == "clean-exec":
            child_argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            clean_exec(child_argv)
            raise AssertionError("clean-exec unexpectedly returned")
        elif args.command == "init-backend":
            init_backend(
                args.root,
                args.kernel,
                args.compile_argv_json,
                args.launch_argv_json,
                capacity_bytes=args.capacity_bytes,
                identity=args.identity,
                version=args.backend_version,
                abi_version=args.abi_version,
            )
            writer_label = "backend initialized"
            manifest = None
        elif args.command == "record-build":
            record_build(
                args.root,
                args.case,
                args.program,
                args.elf,
                args.build_argv_json,
            )
            writer_label = f"build recorded case={args.case}"
            manifest = None
        elif args.command == "record-execution":
            record_execution(
                args.root,
                args.case,
                args.stage,
                args.log,
                args.launch_argv_json,
                args.kernel,
                args.input_image,
                args.output_image,
                args.started_ns,
                args.ended_ns,
                args.returncode,
                mutation=args.mutation,
            )
            writer_label = f"execution recorded case={args.case} stage={args.stage}"
            manifest = None
        elif args.command == "record-stage":
            record_stage(
                args.root, args.case, args.stage, args.log, args.launch_argv_json
            )
            writer_label = f"stage recorded case={args.case} stage={args.stage}"
            manifest = None
        elif args.command == "record-case":
            record_case(
                args.root,
                args.case,
                args.before_image,
                args.fault_image,
                args.reboot_image,
            )
            writer_label = f"case recorded case={args.case}"
            manifest = None
        elif args.command == "record-mutation":
            record_mutation(
                args.root,
                args.baseline_kernel,
                args.mutant_kernel,
                args.selection_diff,
                args.before_image,
                args.fault_image,
                args.reboot_image,
                args.log,
                args.baseline_compile_argv_json,
                args.mutant_compile_argv_json,
                args.command_argv_json,
            )
            writer_label = "delete-FLUSH mutation recorded"
            manifest = None
        else:
            seal_run(args.root)
            writer_label = "run sealed"
            manifest = None
    except (EvidenceError, OSError) as error:
        print(f"fs_allocator_evidence: invalid: {error}", file=sys.stderr)
        return 1
    if manifest is None:
        print(f"fs_allocator_evidence: {writer_label}")
    else:
        print(
            "fs_allocator_evidence: valid "
            f"cases={manifest['case_count']} backend={manifest['backend']['identity']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
