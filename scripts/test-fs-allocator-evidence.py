#!/usr/bin/env python3
"""构造分配器故障证据包测试夹具。"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("fs-allocator-evidence.py")
SPEC = importlib.util.spec_from_file_location("fs_allocator_evidence", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

BASELINE_COMPILE_ARGV = [
    "/fixture/bin/make",
    "build",
    "TOOLPREFIX=",
    "CC=/fixture/bin/riscv64-linux-gnu-gcc",
    "AS=/fixture/bin/riscv64-linux-gnu-gcc",
    "LD=/fixture/bin/riscv64-linux-gnu-ld",
    "OBJCOPY=/fixture/bin/riscv64-linux-gnu-objcopy",
    "OBJDUMP=/fixture/bin/riscv64-linux-gnu-objdump",
    "LOG=error",
    "BUILDDIR=/tmp/kernel-build",
    "INIT_PROC=fsallocfault_ucore",
    "PYTHON_BIN=/fixture/bin/python3",
    MODULE.PROFILE_BUILD_FLAG,
]
MUTANT_COMPILE_ARGV = [
    "/fixture/bin/make",
    "build",
    "TOOLPREFIX=",
    "CC=/fixture/bin/riscv64-linux-gnu-gcc",
    "AS=/fixture/bin/riscv64-linux-gnu-gcc",
    "LD=/fixture/bin/riscv64-linux-gnu-ld",
    "OBJCOPY=/fixture/bin/riscv64-linux-gnu-objcopy",
    "OBJDUMP=/fixture/bin/riscv64-linux-gnu-objdump",
    "LOG=error",
    "BUILDDIR=/tmp/mutant-kernel-build",
    "INIT_PROC=fsallocfault_ucore",
    "PYTHON_BIN=/fixture/bin/python3",
    MODULE.PROFILE_BUILD_FLAG,
    MODULE.MUTANT_BUILD_FLAG,
]
BASELINE_LAUNCH_ARGV = [
    "/fixture/bin/python3",
    "-I",
    "-S",
    "-B",
    "/fixture/evidence/sources/scripts/trusted-python-entry.py",
    "scripts/agent_test_runner.py",
    "--init-proc",
    "fsallocfault_ucore",
]


def canonical_case_build_argv(
    case: tuple[str, str, str], stem: str
) -> list[str]:
    build_dir = f"/tmp/{stem}-user-build"
    return [
        "/fixture/bin/make",
        "-C",
        "user",
        "TOOLPREFIX=",
        "CHAPTER=agent",
        "CC=/fixture/bin/riscv64-linux-gnu-gcc",
        "OBJCOPY=/fixture/bin/riscv64-linux-gnu-objcopy",
        "OBJDUMP=/fixture/bin/riscv64-linux-gnu-objdump",
        "PYTHON_BIN=/fixture/bin/python3",
        MODULE._expected_user_build_flag(case),
        f"build_dir={build_dir}",
        f"out_dir=/tmp/{stem}-user-target",
        f"asm_dir=/tmp/{stem}-user-asm",
        f"{build_dir}/riscv64/fsallocfault_ucore",
    ]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def artifact_record(path: Path, relative: str) -> dict[str, object]:
    raw = path.read_bytes()
    return {"path": relative, "bytes": len(raw), "sha256": sha256_bytes(raw)}


def fake_program_pair(case_id: str) -> tuple[bytes, bytes]:
    rx = hashlib.sha256(f"{case_id}:rx".encode("ascii")).digest()
    rw = hashlib.sha256(f"{case_id}:rw".encode("ascii")).digest()
    rw_offset = 4096
    program = rx + bytes(rw_offset - len(rx)) + rw
    ident = bytearray(16)
    ident[:7] = b"\x7fELF\x02\x01\x01"
    elf_header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        bytes(ident),
        2,
        243,
        1,
        0x1000,
        64,
        0,
        0,
        64,
        56,
        2,
        0,
        0,
        0,
    )
    program_headers = b"".join(
        (
            struct.pack("<IIQQQQQQ", 1, 5, 4096, 0x1000, 0x1000, len(rx), len(rx), 4096),
            struct.pack("<IIQQQQQQ", 1, 6, 8192, 0x2000, 0x2000, len(rw), len(rw), 4096),
        )
    )
    elf = elf_header + program_headers
    elf += bytes(4096 - len(elf)) + rx
    elf += bytes(8192 - len(elf)) + rw
    return program, elf


def fake_raw_image(case_id: str, stage: str) -> bytes:
    header = json.dumps({"case": case_id, "stage": stage}, sort_keys=True).encode("ascii") + b"\n"
    return header + bytes(64 * 1024 - len(header))


def parse_fake_raw(path: Path) -> tuple[str, str, bytes]:
    raw = path.read_bytes()
    identity = json.loads(raw.split(b"\n", 1)[0].decode("ascii"))
    return identity["case"], identity["stage"], raw


def make_snapshot(case_id: str, stage: str, raw: bytes | None = None) -> dict[str, object]:
    if raw is None:
        raw = fake_raw_image(case_id, stage)
    program, _elf = fake_program_pair(case_id)
    semantic: dict[str, object] = {
        "format": MODULE.SNAPSHOT_FORMAT,
        "geometry": {
            "size": 64,
            "nblocks": 58,
            "ninodes": 16,
            "inodestart": 2,
            "bmapstart": 4,
            "qmapstart": 5,
            "datastart": 6,
            "storage_policy_version": 1,
            "storage_scope_slots": 8,
            "workflow_block_guarantee": 1,
            "workflow_inode_guarantee": 1,
            "system_block_reserve": 1,
            "system_inode_reserve": 1,
            "public_principal_id": 2,
            "storage_policy_checksum": 1,
            "superblock_sha256": sha256_bytes(b"fixture-superblock"),
        },
        "allocated_blocks": [],
        "owned_blocks": {},
        "qmap_entries": {},
        "qmap_state_counts": {},
        "qmap_top_state_counts": {},
        "canonical_violations": [],
        "allocated_unowned": [],
        "owner_without_bitmap": [],
        "inodes": {
            "3": {
                "type": 2,
                "size": len(program),
                "exec_layout_version": 1,
                "exec_rw_offset": 4096,
            }
        },
        "inode_raw_sha256": {},
        "inode_incarnations": {},
        "free_inode_owners": {},
        "inode_owner_entries": {},
        "inode_owner_state_counts": {},
        "root_names": {"fixture": 2, MODULE.WORKLOAD_IMAGE_NAME: 3},
        "root_dirents": [
            {"name": "fixture", "inum": 2},
            {"name": MODULE.WORKLOAD_IMAGE_NAME, "inum": 3},
        ],
        "reachable_inodes": [],
        "reachable_blocks": [],
        "inode_blocks": {},
        "payload_sha256": {
            "2": sha256_bytes(f"{case_id}:{stage}:payload".encode("ascii")),
            "3": sha256_bytes(program),
        },
        "block_sha256": {},
        "orphan_inodes": [],
        "orphan_blocks": [],
    }
    canonical = json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    semantic["state_sha256"] = sha256_bytes(canonical)
    semantic["image"] = {"bytes": len(raw), "sha256": sha256_bytes(raw)}
    semantic["generator"] = {"name": MODULE.GENERATOR_NAME, "version": "2"}
    return semantic


def make_canonical(snapshot: dict[str, object]) -> dict[str, object]:
    return {
        "format": MODULE.CANONICAL_FORMAT,
        "generator": snapshot["generator"],
        "image": snapshot["image"],
        "state_sha256": snapshot["state_sha256"],
        "qmap_state_counts": snapshot["qmap_state_counts"],
        "qmap_top_state_counts": snapshot["qmap_top_state_counts"],
        "inode_owner_state_counts": snapshot["inode_owner_state_counts"],
        "transitions": [],
        "inode_transitions": [],
        "violations": [],
    }


def make_diff(case_id: str, after_stage: str) -> dict[str, object]:
    before = make_snapshot(case_id, "before")
    after = make_snapshot(case_id, after_stage)
    return {
        "format": MODULE.DIFF_FORMAT,
        "generator": before["generator"],
        "images": {"before": before["image"], "after": after["image"]},
        "bitmap_set": [],
        "bitmap_cleared": [],
        "owner_changes": [],
        "inode_changes": [],
        "payload_changes": [
            {
                "inum": 2,
                "before_sha256": before["payload_sha256"]["2"],
                "after_sha256": after["payload_sha256"]["2"],
            }
        ],
        "block_content_changes": [],
        "root_names_before": before["root_names"],
        "root_names_after": after["root_names"],
        "before_sha256": before["state_sha256"],
        "after_sha256": after["state_sha256"],
    }


def make_verified(case_id: str) -> dict[str, object]:
    operation, phase, action = MODULE.CASES[MODULE.CASE_IDS.index(case_id)]
    before = make_snapshot(case_id, "before")
    fault = make_snapshot(case_id, "fault")
    reboot = make_snapshot(case_id, "reboot")
    return {
        "format": MODULE.VERIFIED_FORMAT,
        "generator": before["generator"],
        "images": {
            "before": before["image"],
            "fault": fault["image"],
            "reboot": reboot["image"],
        },
        "operation": operation,
        "phase": phase,
        "action": action,
        "expected_manifest": {},
        "fault_qmap_transitions": [],
        "fault_inode_transitions": [],
        "before_sha256": before["state_sha256"],
        "fault_sha256": fault["state_sha256"],
        "fault_exact_diff": make_diff(case_id, "fault"),
        "reboot_exact_diff": make_diff(case_id, "reboot"),
        "reboot_block_delta": 0,
        "reboot_inode_delta": 0,
        "reboot_sha256": reboot["state_sha256"],
        "verified": True,
    }


class FakeImageTool:
    GENERATOR = {"name": MODULE.GENERATOR_NAME, "version": "2"}

    @staticmethod
    def main(arguments: list[str]) -> int:
        output_index = arguments.index("--output")
        output = Path(arguments[output_index + 1])
        args = arguments[:output_index]
        command = args[0]
        if command == "snapshot":
            case_id, stage, raw = parse_fake_raw(Path(args[1]))
            value = make_snapshot(case_id, stage, raw)
        elif command == "diff":
            case_id, _, _ = parse_fake_raw(Path(args[1]))
            _, after_stage, _ = parse_fake_raw(Path(args[2]))
            value = make_diff(case_id, after_stage)
        elif command == "validate":
            case_id, stage, _ = parse_fake_raw(Path(args[1]))
            snapshot = make_snapshot(case_id, stage)
            value = make_canonical(snapshot)
        elif command == "verify-case-raw":
            case_id, before_stage, _ = parse_fake_raw(Path(args[1]))
            fault_case, fault_stage, _ = parse_fake_raw(Path(args[2]))
            reboot_case, reboot_stage, _ = parse_fake_raw(Path(args[3]))
            if (
                (before_stage, fault_stage, reboot_stage)
                != ("before", "fault", "reboot")
                or case_id != fault_case
                or case_id != reboot_case
            ):
                return 2
            value = make_verified(case_id)
            value["operation"] = args[args.index("--operation") + 1]
            value["phase"] = args[args.index("--phase") + 1]
            value["action"] = args[args.index("--action") + 1]
        else:
            raise AssertionError(command)
        write_json(output, value)
        return 0


def fake_mutation_rejection(
    directory: Path, paths: dict[str, Path]
) -> tuple[int, dict[str, str]]:
    del directory, paths
    return 2, {
        "code": MODULE.MUTATION_REJECTION_CODE,
        "message": MODULE.MUTATION_REJECTION_MESSAGE,
    }


def make_package(root: Path) -> None:
    run_id = "a" * 64
    source_commit = "b" * 40
    source_records = []
    for relative in MODULE.SOURCE_PATHS:
        raw = f"fixture source: {relative}\n".encode("utf-8")
        destination = root / "sources" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        source_records.append(
            {
                "source_path": relative,
                "artifact": {
                    "path": f"sources/{relative}",
                    "bytes": len(raw),
                    "sha256": sha256_bytes(raw),
                },
            }
        )

    def tool_record(label: str) -> dict[str, object]:
        executable_name = {
            "python": "python3",
            "qemu": "qemu-system-riscv64",
            "make": "make",
            "host_cc": "cc",
            "cross_gcc": "riscv64-linux-gnu-gcc",
            "cross_ld": "riscv64-linux-gnu-ld",
            "cross_objcopy": "riscv64-linux-gnu-objcopy",
            "cross_objdump": "riscv64-linux-gnu-objdump",
        }[label]
        requested = f"/fixture/bin/{executable_name}"
        executable = f"fixture-{label}-executable".encode("ascii")
        version = f"fixture {label} 1.0\n".encode("ascii")
        return {
            "requested": requested,
            "resolved": requested,
            "executable": {
                "bytes": len(executable),
                "sha256": sha256_bytes(executable),
            },
            "version_argv": [requested, "--version"],
            "version_first_line": version.decode("ascii").strip(),
            "version_sha256": sha256_bytes(version),
        }

    write_json(
        root / "run.json",
        {
            "schema_version": 1,
            "format": MODULE.RUN_FORMAT,
            "run_id": run_id,
            "source": {
                "commit": source_commit,
                "tree": "c" * 40,
                "dirty": False,
                "status_bytes": 0,
                "status_sha256": sha256_bytes(b""),
                "status_hex": "",
                "snapshot_sha256": sha256_bytes(MODULE._render_json(source_records)),
            },
            "sources": source_records,
            "toolchain": {
                label: tool_record(label)
                for label in (
                    "python",
                    "qemu",
                    "make",
                    "host_cc",
                    "cross_gcc",
                    "cross_ld",
                    "cross_objcopy",
                    "cross_objdump",
                )
            },
        },
    )
    profile_kernel = b"agentos profile kernel with volatile cache overlay\n"
    mutant_kernel = b"agentos mutant kernel without alloc intent barrier\n"
    backend_build_sha256 = sha256_bytes(profile_kernel)
    backend = {
        "schema_version": 1,
        "format": MODULE.BACKEND_FORMAT,
        "backend": {
            "identity": "agentos-virtio-ram-overlay",
            "version": "1.0",
            "abi_version": "2",
            "model": MODULE.BACKEND_MODEL,
            "deterministic": True,
            "volatile_cache": True,
            "capacity_bytes": 4 * 1024 * 1024,
            "build_sha256": backend_build_sha256,
            "compile_argv": BASELINE_COMPILE_ARGV,
            "launch_argv": BASELINE_LAUNCH_ARGV,
        },
    }
    (root / "profile.kernel").write_bytes(profile_kernel)
    write_json(root / "backend.json", backend)
    (root / "flush-deletion-mutant.kernel").write_bytes(mutant_kernel)

    execution_clock = 1_000_000_000

    def runner_argv(case_id: str, stage: str, mutation: bool) -> list[str]:
        marker, completion, kernel_name = MODULE._expected_execution_semantics(
            case_id, stage, mutation
        )
        tag = "mutation-alloc-intent-crash" if mutation else case_id
        argv = [
            "/fixture/bin/python3",
            "-I",
            "-S",
            "-B",
            "/fixture/evidence/sources/scripts/trusted-python-entry.py",
            "scripts/agent_test_runner.py",
            "--init-proc",
            "fsallocfault_ucore",
            "--marker",
            marker,
            "--marker-mode",
            "exact-line",
            "--log-file",
            f"/tmp/{tag}-{stage}.log",
            "--case-timeout",
            "60s",
            "--idle-notice-seconds",
            "20s",
            "--marker-grace-seconds",
            "0s" if completion == "powercut" else "5s",
            "--qemu",
            "/fixture/bin/qemu-system-riscv64",
            "--kernel",
            "/tmp/fsalloc-delete-barrier-mutant-kernel"
            if kernel_name == "flush-deletion-mutant.kernel"
            else "/tmp/fsalloc-profile-kernel",
            "--image",
            f"/tmp/{tag}.img",
        ]
        if completion != "natural":
            argv.extend(["--completion-mode", completion])
        return argv

    def write_execution(
        case_id: str,
        stage: str,
        mutation: bool,
        input_image: dict[str, object],
        output_image: dict[str, object],
    ) -> None:
        nonlocal execution_clock
        semantic_case = "alloc-intent-crash" if mutation else case_id
        marker, completion, kernel_name = MODULE._expected_execution_semantics(
            case_id, stage, mutation
        )
        log_relative = MODULE._execution_log_relative(case_id, stage, mutation)
        kernel_relative = kernel_name
        record = {
            "schema_version": 1,
            "format": MODULE.EXECUTION_FORMAT,
            "run_id": run_id,
            "source_commit": source_commit,
            "case": MODULE._case_value(semantic_case),
            "stage": stage,
            "mutation": mutation,
            "started_ns": execution_clock,
            "ended_ns": execution_clock + 10,
            "returncode": 0,
            "launch_argv": runner_argv(case_id, stage, mutation),
            "marker": marker,
            "completion": completion,
            "kernel": artifact_record(root / kernel_relative, kernel_relative),
            "input_image": input_image,
            "output_image": output_image,
            "source_log": artifact_record(root / log_relative, log_relative),
        }
        execution_clock += 100
        write_json(root / MODULE._execution_relative(case_id, stage, mutation), record)

    (root / "flush-deletion-selection.diff").write_text(
        "- result = fs_durable_barrier_forward();\n"
        "+ #ifdef FS_ALLOCATOR_DELETE_BARRIER_MUTANT\n"
        "+ result = 0;\n"
        "+ #endif\n",
        encoding="utf-8",
    )
    mutation_raw = {
        stage: fake_raw_image("alloc-intent-crash", stage)
        for stage in ("before", "fault", "reboot")
    }
    for stage, raw in mutation_raw.items():
        (root / f"flush-deletion-{stage}.img.gz").write_bytes(MODULE._canonical_gzip(raw))
    mutation_exit = 2
    mutation_error = {
        "code": MODULE.MUTATION_REJECTION_CODE,
        "message": MODULE.MUTATION_REJECTION_MESSAGE,
    }
    overlay_marker = (
        "fsalloc-cache: mutation=delete-flush target=allocator-phase-barrier "
        "durable_epoch=1 pending_at_powercut=1 discarded_on_powercut=1 powercut=1"
    )
    result_marker = (
        "fsalloc-mutation: mutation=delete-flush target=allocator-phase-barrier "
        "case=alloc-intent-crash "
        f"baseline_kernel_sha256={backend_build_sha256} "
        f"mutant_kernel_sha256={sha256_bytes(mutant_kernel)} "
        f"verifier_exit_code={mutation_exit} outcome=verification-rejected"
    )
    mutation_fault_log = root / "flush-deletion-fault.guest.log"
    mutation_fault_log.write_text(
        "fsallocfault_kernel: durability_receipt_failed=1\n"
        f"{overlay_marker}\n",
        encoding="utf-8",
    )
    mutation_reboot_log = root / "flush-deletion-reboot.guest.log"
    mutation_reboot_log.write_text(
        "fsallocfault_ucore: case=alloc phase=intent action=crash reboot_ready=1\n",
        encoding="utf-8",
    )
    mutation_log = root / "flush-deletion-mutation.log"
    mutation_log.write_text(
        mutation_fault_log.read_text(encoding="utf-8") + f"{result_marker}\n",
        encoding="utf-8",
    )
    write_json(
        root / "flush-deletion-mutation.json",
        {
            "schema_version": 1,
            "format": MODULE.MUTATION_FORMAT,
            "mutation": MODULE.DELETE_FLUSH_MUTATION,
            "mutation_target": "allocator-phase-barrier",
            "status": "passed",
            "backend_identity": backend["backend"]["identity"],
            "backend_version": backend["backend"]["version"],
            "case": {
                "id": "alloc-intent-crash",
                "operation": "alloc",
                "phase": "intent",
                "action": "crash",
            },
            "baseline_compile_argv": BASELINE_COMPILE_ARGV,
            "mutant_compile_argv": MUTANT_COMPILE_ARGV,
            "command": runner_argv("mutation-alloc-intent-crash", "fault", True),
            "mutant_verification_exit_code": mutation_exit,
            "expected_outcome": "verification-rejected",
            "observed_outcome": "verification-rejected",
            "verifier_error": mutation_error,
            "powercut": {
                "durable_epoch": 1,
                "pending_write_count": 1,
                "discarded_write_count": 1,
            },
            "baseline_kernel": artifact_record(root / "profile.kernel", "profile.kernel"),
            "mutant_kernel": artifact_record(
                root / "flush-deletion-mutant.kernel", "flush-deletion-mutant.kernel"
            ),
            "selection_diff": artifact_record(
                root / "flush-deletion-selection.diff", "flush-deletion-selection.diff"
            ),
            "images": {
                stage: {"bytes": len(raw), "sha256": sha256_bytes(raw)}
                for stage, raw in mutation_raw.items()
            },
            "log": artifact_record(mutation_log, mutation_log.name),
        },
    )
    mutation_images = {
        stage: {"bytes": len(raw), "sha256": sha256_bytes(raw)}
        for stage, raw in mutation_raw.items()
    }
    for operation, phase, action in MODULE.CASES:
        case = (operation, phase, action)
        case_id = "-".join(case)
        case_root = root / "cases" / case_id
        case_root.mkdir(parents=True)
        program_raw, elf_raw = fake_program_pair(case_id)
        (case_root / "program.bin").write_bytes(program_raw)
        (case_root / "program.elf").write_bytes(elf_raw)
        build_argv = canonical_case_build_argv(case, case_id)
        write_json(
            case_root / "build.json",
            {
                "schema_version": 1,
                "format": MODULE.BUILD_FORMAT,
                "run_id": run_id,
                "case": MODULE._case_value(case_id),
                "build_argv": build_argv,
                "program": artifact_record(
                    case_root / "program.bin", f"cases/{case_id}/program.bin"
                ),
                "elf": artifact_record(
                    case_root / "program.elf", f"cases/{case_id}/program.elf"
                ),
                "image_shape": MODULE._elf_image_shape(
                    program_raw, elf_raw, case_id
                ),
            },
        )
        before = make_snapshot(case_id, "before")
        fault = make_snapshot(case_id, "fault")
        reboot = make_snapshot(case_id, "reboot")
        fault_diff = make_diff(case_id, "fault")
        reboot_diff = make_diff(case_id, "reboot")
        for stage in ("before", "fault", "reboot"):
            (case_root / f"{stage}.img.gz").write_bytes(
                MODULE._canonical_gzip(fake_raw_image(case_id, stage))
            )
        write_json(case_root / "before.snapshot.json", before)
        write_json(case_root / "fault.snapshot.json", fault)
        write_json(case_root / "fault.diff.json", fault_diff)
        write_json(case_root / "reboot.snapshot.json", reboot)
        write_json(
            case_root / "reboot.canonical.json",
            make_canonical(reboot),
        )
        write_json(case_root / "reboot.diff.json", reboot_diff)
        write_json(case_root / "verified.json", make_verified(case_id))
        expected_case = {
            "id": case_id,
            "operation": operation,
            "phase": phase,
            "action": action,
        }
        receipt_backend = {
            key: backend["backend"][key]
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
        for stage_index, stage in enumerate(MODULE.STAGES, start=1):
            backend_instance_id = f"{case_id}:{stage}"
            receipt_id = f"{case_id}:{stage}:flush"
            powercut = stage == "fault" and action == "crash"
            receipt_body = {
                "backend_instance_id": backend_instance_id,
                "receipt_id": receipt_id,
                "raw_write_count": stage_index,
                "cached_write_count": stage_index,
                "flush_command_count": stage_index,
                "acknowledged_flush_count": stage_index,
                "last_acknowledged_sequence": stage_index,
                "durable_epoch": stage_index,
                "pending_write_count_before_flush": stage_index,
                "pending_write_count_after_flush": 0,
                "pending_write_count_at_stage_end": 0,
                "powercut_after_receipt": powercut,
            }
            receipt_marker = MODULE._receipt_log_marker(
                receipt_id,
                backend_instance_id,
                "2",
                4 * 1024 * 1024,
                stage_index,
                stage_index,
                stage_index,
                stage_index,
                stage_index,
                stage_index,
                stage_index,
                0,
                0,
                powercut,
            )
            success_marker, _, _ = MODULE._expected_execution_semantics(
                case_id, stage, False
            )
            physical_lines = []
            if stage == "fault":
                physical_lines.append(
                    MODULE._physical_flush_log_marker(
                        "fault-baseline", 2, 4096, 8, 9, 1, 0, 1, 1, 1, 1, 0, 0
                    )
                )
                if action != "crash":
                    physical_lines.append(
                        MODULE._physical_operation_log_marker(1, 1, 1, 1)
                    )
            if stage != "fault" or action != "crash":
                physical_lines.append(
                    MODULE._physical_flush_log_marker(
                        stage,
                        2,
                        4096,
                        stage_index - 1,
                        stage_index,
                        stage_index,
                        0,
                        stage_index,
                        stage_index,
                        1,
                        1,
                        0,
                        0,
                    )
                )
            log_lines = [
                *physical_lines,
                success_marker,
                f"{case_id}: {stage}: durable flush receipt",
                receipt_marker,
            ]
            log_path = case_root / f"{stage}.guest.log"
            log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
            physical_io = MODULE._parse_physical_io_receipts(
                case, stage, backend["backend"], receipt_body, log_lines
            )
            log_record = artifact_record(log_path, f"cases/{case_id}/{stage}.guest.log")
            write_json(
                case_root / f"{stage}.flush.json",
                {
                    "schema_version": 1,
                    "format": MODULE.RECEIPT_FORMAT,
                    "case": expected_case,
                    "stage": stage,
                    "backend": receipt_backend,
                    "launch_argv": runner_argv(case_id, stage, False),
                    "receipt": receipt_body,
                    "physical_io": physical_io,
                    "source_log": log_record,
                },
            )
            images = {
                "before": before["image"],
                "fault": fault["image"],
                "reboot": reboot["image"],
            }
            stage_input = {
                "prepare": before["image"],
                "fault": before["image"],
                "reboot": fault["image"],
            }[stage]
            write_execution(
                case_id,
                stage,
                False,
                stage_input,
                images[{"prepare": "before", "fault": "fault", "reboot": "reboot"}[stage]],
            )

    write_execution(
        "mutation-alloc-intent-crash",
        "fault",
        True,
        mutation_images["before"],
        mutation_images["fault"],
    )
    write_execution(
        "mutation-alloc-intent-crash",
        "reboot",
        True,
        mutation_images["fault"],
        mutation_images["reboot"],
    )
    MODULE.seal_run(root)


def write_test_archive(
    path: Path, members: tuple[tuple[str, bytes, bytes, str], ...]
) -> None:
    path.parent.mkdir(parents=True)
    with tarfile.open(path, "w:", format=tarfile.USTAR_FORMAT) as archive:
        for name, member_type, payload, linkname in members:
            info = tarfile.TarInfo(name)
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            info.type = member_type
            info.linkname = linkname
            info.size = len(payload) if member_type == tarfile.REGTYPE else 0
            archive.addfile(info, io.BytesIO(payload) if info.size else None)


class EvidenceNegativeOracle(unittest.TestCase):
    """用一次完整夹具守住最危险的证据边界。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.old_image_tool = MODULE._IMAGE_TOOL
        cls.old_mutation_oracle = MODULE._raw_mutation_cli_rejection
        MODULE._IMAGE_TOOL = FakeImageTool()
        MODULE._raw_mutation_cli_rejection = fake_mutation_rejection
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "evidence"
        cls.root.mkdir()
        make_package(cls.root)
        MODULE.write_manifest(cls.root)
        cls.archive = Path(cls.temporary.name) / "canonical" / MODULE.ARCHIVE_BASENAME
        cls.archive.parent.mkdir()
        MODULE.pack_archive(cls.root, cls.archive)

    @classmethod
    def tearDownClass(cls) -> None:
        MODULE._IMAGE_TOOL = cls.old_image_tool
        MODULE._raw_mutation_cli_rejection = cls.old_mutation_oracle
        cls.temporary.cleanup()

    def test_archive_fails_closed(self) -> None:
        scenarios = (
            ("parent", "../escape", tarfile.REGTYPE, "unsafe member path"),
            ("absolute", "/escape", tarfile.REGTYPE, "unsafe member path"),
            ("symlink", "backend.json", tarfile.SYMTYPE, "forbidden special member"),
            ("device", "backend.json", tarfile.CHRTYPE, "forbidden special member"),
        )
        for label, name, member_type, error in scenarios:
            with self.subTest(label=label):
                archive = Path(self.temporary.name) / label / MODULE.ARCHIVE_BASENAME
                write_test_archive(archive, ((name, member_type, b"x", "target"),))
                with self.assertRaisesRegex(MODULE.EvidenceError, error):
                    MODULE.verify_archive(archive)

        duplicate = Path(self.temporary.name) / "duplicate" / MODULE.ARCHIVE_BASENAME
        member = ("backend.json", tarfile.REGTYPE, b"{}", "")
        write_test_archive(duplicate, (member, member))
        with self.assertRaisesRegex(MODULE.EvidenceError, "duplicate member"):
            MODULE.verify_archive(duplicate)

        noncanonical = Path(self.temporary.name) / "noncanonical" / MODULE.ARCHIVE_BASENAME
        noncanonical.parent.mkdir()
        shutil.copyfile(self.archive, noncanonical)
        with noncanonical.open("ab") as handle:
            handle.write(b"trailing-data")
        with self.assertRaisesRegex(MODULE.EvidenceError, "bytes are not canonical"):
            MODULE.verify_archive(noncanonical)

    def test_json_manifest_and_receipt_bindings(self) -> None:
        cases = (
            ("nonfinite", "run.json", ("source", "status_bytes"), float("nan"), False,
             "non-finite JSON number"),
            ("manifest", "manifest.json", ("case_count",), 35, True,
             "does not match"),
            ("receipt", f"cases/{MODULE.CASE_IDS[0]}/prepare.flush.json",
             ("receipt", "receipt_id"), "unbound:prepare:flush", False,
             "unstable identity"),
        )
        for label, relative, keys, replacement, verify, error in cases:
            with self.subTest(label=label):
                path = self.root / relative
                original = path.read_bytes()
                try:
                    value = json.loads(original)
                    target = value
                    for key in keys[:-1]:
                        target = target[key]
                    target[keys[-1]] = replacement
                    write_json(path, value)
                    checker = MODULE.verify_manifest if verify else MODULE.construct_manifest
                    with self.assertRaisesRegex(MODULE.EvidenceError, error):
                        checker(self.root)
                finally:
                    path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
