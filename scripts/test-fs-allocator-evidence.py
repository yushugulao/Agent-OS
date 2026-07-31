#!/usr/bin/env python3
"""Regression tests for the allocator fault evidence package contract."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("fs-allocator-evidence.py")
SPEC = importlib.util.spec_from_file_location("fs_allocator_evidence", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

BASELINE_COMPILE_ARGV = [
    "make",
    "build",
    "TOOLPREFIX=riscv64-unknown-elf-",
    "LOG=error",
    "BUILDDIR=/tmp/kernel-build",
    "INIT_PROC=fsallocfault_ucore",
    MODULE.PROFILE_BUILD_FLAG,
]
MUTANT_COMPILE_ARGV = [
    "make",
    "build",
    "TOOLPREFIX=riscv64-unknown-elf-",
    "LOG=error",
    "BUILDDIR=/tmp/mutant-kernel-build",
    "INIT_PROC=fsallocfault_ucore",
    MODULE.PROFILE_BUILD_FLAG,
    MODULE.MUTANT_BUILD_FLAG,
]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def artifact_record(path: Path, relative: str) -> dict[str, object]:
    raw = path.read_bytes()
    return {"path": relative, "bytes": len(raw), "sha256": sha256_bytes(raw)}


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
        "inodes": {},
        "inode_raw_sha256": {},
        "inode_incarnations": {},
        "free_inode_owners": {},
        "inode_owner_entries": {},
        "inode_owner_state_counts": {},
        "root_names": {"fixture": 2},
        "root_dirents": [{"name": "fixture", "inum": 2}],
        "reachable_inodes": [],
        "reachable_blocks": [],
        "inode_blocks": {},
        "payload_sha256": {
            "2": sha256_bytes(f"{case_id}:{stage}:payload".encode("ascii"))
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
            value = {
                "violations": [],
                "transitions": [],
                "inode_transitions": [],
                "state_sha256": snapshot["state_sha256"],
            }
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
                "dirty": False,
                "status_bytes": 0,
                "status_sha256": sha256_bytes(b""),
                "status_hex": "",
                "snapshot_sha256": sha256_bytes(MODULE._render_json(source_records)),
            },
            "sources": source_records,
            "toolchain": {
                label: tool_record(label)
                for label in ("python", "qemu", "make", "host_cc", "cross_gcc")
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
            "launch_argv": [
                "/fixture/bin/python3",
                "scripts/agent_test_runner.py",
                "--init-proc",
                "fsallocfault_ucore",
            ],
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
        program_raw = f"fixture program: {case_id}\n".encode("ascii")
        (case_root / "program.bin").write_bytes(program_raw)
        build_argv = [
            "make",
            "-C",
            "user",
            "TOOLPREFIX=riscv64-linux-gnu-",
            "CHAPTER=agent",
            MODULE._expected_user_build_flag(case),
            f"build_dir=/tmp/{case_id}-user-build",
            f"out_dir=/tmp/{case_id}-user-target",
            f"asm_dir=/tmp/{case_id}-user-asm",
            f"/tmp/{case_id}-user-build/riscv64/fsallocfault_ucore",
        ]
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
            {
                "violations": [],
                "transitions": [],
                "inode_transitions": [],
                "state_sha256": reboot["state_sha256"],
            },
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
            log_path = case_root / f"{stage}.guest.log"
            log_path.write_text(
                f"{success_marker}\n{case_id}: {stage}: durable flush receipt\n"
                f"{receipt_marker}\n",
                encoding="utf-8",
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


class EvidenceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._original_image_tool = MODULE._IMAGE_TOOL
        cls._original_mutation_rejection = MODULE._raw_mutation_cli_rejection
        MODULE._IMAGE_TOOL = FakeImageTool()
        MODULE._raw_mutation_cli_rejection = fake_mutation_rejection
        cls._template_temp = tempfile.TemporaryDirectory()
        cls.template = Path(cls._template_temp.name) / "template"
        cls.template.mkdir()
        make_package(cls.template)
        MODULE.write_manifest(cls.template)
        cls.template_archive = Path(cls._template_temp.name) / MODULE.ARCHIVE_BASENAME
        MODULE.pack_archive(cls.template, cls.template_archive)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._template_temp.cleanup()
        MODULE._IMAGE_TOOL = cls._original_image_tool
        MODULE._raw_mutation_cli_rejection = cls._original_mutation_rejection

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "evidence"
        shutil.copytree(self.template, self.root)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def expect_rejected(self, fragment: str, *, verify: bool = False) -> None:
        function = MODULE.verify_manifest if verify else MODULE.construct_manifest
        with self.assertRaisesRegex(MODULE.EvidenceError, fragment):
            function(self.root)

    def test_builds_and_verifies_exact_36_case_manifest(self) -> None:
        manifest = MODULE.write_manifest(self.root)
        self.assertEqual(manifest["format"], MODULE.MANIFEST_FORMAT)
        self.assertEqual(manifest["case_count"], 36)
        self.assertEqual([case["id"] for case in manifest["cases"]], list(MODULE.CASE_IDS))
        self.assertEqual(manifest["backend"]["model"], MODULE.BACKEND_MODEL)
        self.assertTrue(manifest["backend"]["deterministic"])
        self.assertTrue(manifest["backend"]["volatile_cache"])
        self.assertEqual(manifest["backend"]["abi_version"], "2")
        self.assertEqual(manifest["backend"]["capacity_bytes"], 4 * 1024 * 1024)
        self.assertEqual(
            manifest["negative_mutations"][MODULE.DELETE_FLUSH_MUTATION]["status"],
            "passed",
        )
        for case in manifest["cases"]:
            self.assertEqual(set(case["artifacts"]), set(MODULE.CASE_FILES))
            self.assertEqual(set(case["executions"]), set(MODULE.STAGES))
            self.assertEqual(case["generator"]["name"], MODULE.GENERATOR_NAME)
            self.assertEqual(
                case["negative_mutations"][MODULE.DELETE_FLUSH_MUTATION]["status"],
                "passed",
            )
            expected_powercut = case["action"] == "crash"
            self.assertEqual(
                case["executions"]["fault"]["receipt"]["powercut_after_receipt"],
                expected_powercut,
            )
            for image in case["images"].values():
                self.assertRegex(image["sha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(image["bytes"], 64 * 1024)
        self.assertEqual(MODULE.verify_manifest(self.root), manifest)

    def test_rejects_missing_file_and_deleted_flush_receipt(self) -> None:
        case_id = MODULE.CASE_IDS[0]
        (self.root / "cases" / case_id / "fault.flush.json").unlink()
        self.expect_rejected(r"missing=\['fault\.flush\.json'\]")

    def test_rejects_unverified_case(self) -> None:
        path = self.root / "cases" / MODULE.CASE_IDS[0] / "verified.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["verified"] = False
        write_json(path, value)
        self.expect_rejected("not semantically verified")

    def test_rejects_extra_and_missing_case_directories(self) -> None:
        shutil.rmtree(self.root / "cases" / MODULE.CASE_IDS[0])
        (self.root / "cases" / "alloc-refund-busy").mkdir()
        self.expect_rejected("case directory set mismatch")

    def test_rejects_extra_case_artifact(self) -> None:
        path = self.root / "cases" / MODULE.CASE_IDS[0] / "unexpected.txt"
        path.write_text("not part of the evidence contract\n", encoding="utf-8")
        self.expect_rejected("files mismatch")

    def test_rejects_symlinked_artifact(self) -> None:
        case_root = self.root / "cases" / MODULE.CASE_IDS[0]
        victim = case_root / "fault.diff.json"
        target = Path(self._temp.name) / "real-fault.diff.json"
        target.write_bytes(victim.read_bytes())
        victim.unlink()
        try:
            os.symlink(target, victim)
        except OSError:
            victim.write_bytes(target.read_bytes())
        if not MODULE._is_link(victim):
            target.unlink(missing_ok=True)
            original = MODULE._is_link

            def report_victim(path: Path) -> bool:
                return (
                    path.name == victim.name
                    and path.parent.name == case_root.name
                ) or original(path)

            with patch.object(MODULE, "_is_link", report_victim):
                self.expect_rejected("symlink")
        else:
            self.expect_rejected("symlink")

    def test_rejects_failed_or_faked_delete_flush_mutation(self) -> None:
        path = self.root / "flush-deletion-mutation.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["status"] = "failed"
        write_json(path, value)
        self.expect_rejected("negative mutation did not pass")

        shutil.rmtree(self.root)
        shutil.copytree(self.template, self.root)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["mutant_verification_exit_code"] = 0
        write_json(path, value)
        self.expect_rejected("non-zero exit code")

    def test_compile_argv_binds_profile_and_only_mutant_delta(self) -> None:
        backend_path = self.root / "backend.json"
        backend = json.loads(backend_path.read_text(encoding="utf-8"))
        backend["backend"]["compile_argv"].remove(MODULE.PROFILE_BUILD_FLAG)
        write_json(backend_path, backend)
        self.expect_rejected("fault-test profile exactly once")

        shutil.rmtree(self.root)
        shutil.copytree(self.template, self.root)
        mutation_path = self.root / "flush-deletion-mutation.json"
        mutation = json.loads(mutation_path.read_text(encoding="utf-8"))
        mutation["mutant_compile_argv"].insert(-1, "UNRELATED_BUILD_DELTA=1")
        write_json(mutation_path, mutation)
        self.expect_rejected("not bound to the baseline build")

        shutil.rmtree(self.root)
        shutil.copytree(self.template, self.root)
        mutation = json.loads(mutation_path.read_text(encoding="utf-8"))
        mutation["mutant_compile_argv"].append(MODULE.MUTANT_BUILD_FLAG)
        write_json(mutation_path, mutation)
        self.expect_rejected("allocator mutant exactly once")

    def test_mutation_oracle_binds_baseline_and_exact_checkpoint_failure(self) -> None:
        mutation_path = self.root / "flush-deletion-mutation.json"
        other_case = "alloc-intent-busy"
        raw = MODULE._read_canonical_gzip_image(
            self.root / "cases" / other_case / "before.img.gz",
            make_snapshot(other_case, "before")["image"],
            "fixture other baseline",
        )
        (self.root / "flush-deletion-before.img.gz").write_bytes(
            MODULE._canonical_gzip(raw)
        )
        mutation = json.loads(mutation_path.read_text(encoding="utf-8"))
        mutation["images"]["before"] = {
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
        }
        write_json(mutation_path, mutation)
        self.expect_rejected("is not the alloc:intent:crash case baseline")

        shutil.rmtree(self.root)
        shutil.copytree(self.template, self.root)

        def arbitrary_rejection(
            directory: Path, paths: dict[str, Path]
        ) -> tuple[int, dict[str, str]]:
            del directory, paths
            return 2, {
                "code": MODULE.MUTATION_REJECTION_CODE,
                "message": "superblock checksum is invalid",
            }

        with patch.object(
            MODULE, "_raw_mutation_cli_rejection", arbitrary_rejection
        ):
            self.expect_rejected("did not reject the missing qmap checkpoint")

    def test_mutation_busy_control_rejects_malformed_reboot_stage(self) -> None:
        mutation_path = self.root / "flush-deletion-mutation.json"
        raw = fake_raw_image("alloc-intent-crash", "fault")
        (self.root / "flush-deletion-reboot.img.gz").write_bytes(
            MODULE._canonical_gzip(raw)
        )
        mutation = json.loads(mutation_path.read_text(encoding="utf-8"))
        mutation["images"]["reboot"] = {
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
        }
        write_json(mutation_path, mutation)
        self.expect_rejected("mutant busy control raw CLI failed")

    def test_mutation_powercut_has_one_exact_pending_qmap_write(self) -> None:
        mutation_path = self.root / "flush-deletion-mutation.json"
        mutation = json.loads(mutation_path.read_text(encoding="utf-8"))
        mutation["powercut"]["pending_write_count"] = 2
        mutation["powercut"]["discarded_write_count"] = 2
        log_path = self.root / "flush-deletion-mutation.log"
        log_text = log_path.read_text(encoding="utf-8").replace(
            "pending_at_powercut=1 discarded_on_powercut=1",
            "pending_at_powercut=2 discarded_on_powercut=2",
        )
        log_path.write_text(log_text, encoding="utf-8")
        mutation["log"] = artifact_record(log_path, log_path.name)
        write_json(mutation_path, mutation)
        self.expect_rejected("exactly one pending qmap write")

    def test_rejects_unbound_backend_or_mutant_binary(self) -> None:
        profile = self.root / "profile.kernel"
        profile.write_bytes(profile.read_bytes() + b"tampered\n")
        self.expect_rejected("backend build identity does not match")

        shutil.rmtree(self.root)
        shutil.copytree(self.template, self.root)
        mutant = self.root / "flush-deletion-mutant.kernel"
        mutant.write_bytes(mutant.read_bytes() + b"tampered\n")
        self.expect_rejected("mutant kernel does not match|execution kernel")

    def test_rejects_receipt_without_acknowledged_flush(self) -> None:
        path = self.root / "cases" / MODULE.CASE_IDS[0] / "prepare.flush.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["receipt"]["acknowledged_flush_count"] = 0
        write_json(path, value)
        self.expect_rejected("no acknowledged durable flush")

    def test_rejects_receipt_not_present_in_its_bound_guest_log(self) -> None:
        case_id = MODULE.CASE_IDS[0]
        case_root = self.root / "cases" / case_id
        log_path = case_root / "prepare.guest.log"
        log_path.write_text("prepare completed without a backend receipt\n", encoding="utf-8")
        receipt_path = case_root / "prepare.flush.json"
        value = json.loads(receipt_path.read_text(encoding="utf-8"))
        value["source_log"] = artifact_record(
            log_path, f"cases/{case_id}/prepare.guest.log"
        )
        write_json(receipt_path, value)
        self.expect_rejected(
            "execution Guest log does not match|success marker|flush receipt marker is absent"
        )

    def test_rejects_image_or_generator_provenance_mismatch(self) -> None:
        path = self.root / "cases" / MODULE.CASE_IDS[0] / "verified.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["images"]["fault"]["sha256"] = "f" * 64
        write_json(path, value)
        self.expect_rejected("verified image identities mismatch")

    def test_rejects_self_consistent_fabricated_snapshot_json(self) -> None:
        case_id = MODULE.CASE_IDS[0]
        case_root = self.root / "cases" / case_id
        snapshot_path = case_root / "fault.snapshot.json"
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot["payload_sha256"]["2"] = "a" * 64
        semantic = dict(snapshot)
        semantic.pop("image")
        semantic.pop("generator")
        semantic.pop("state_sha256")
        snapshot["state_sha256"] = sha256_bytes(
            json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
        )
        write_json(snapshot_path, snapshot)
        diff_path = case_root / "fault.diff.json"
        diff = json.loads(diff_path.read_text(encoding="utf-8"))
        diff["after_sha256"] = snapshot["state_sha256"]
        write_json(diff_path, diff)
        verified_path = case_root / "verified.json"
        verified = json.loads(verified_path.read_text(encoding="utf-8"))
        verified["fault_sha256"] = snapshot["state_sha256"]
        verified["fault_exact_diff"] = diff
        write_json(verified_path, verified)
        self.expect_rejected("snapshot is not reproduced by its raw image")

    def test_rejects_raw_image_tampering_and_gzip_bomb(self) -> None:
        case_id = MODULE.CASE_IDS[0]
        path = self.root / "cases" / case_id / "fault.img.gz"
        raw = bytearray(MODULE._read_canonical_gzip_image(
            path, make_snapshot(case_id, "fault")["image"], "fixture"
        ))
        raw[-1] ^= 1
        path.write_bytes(MODULE._canonical_gzip(bytes(raw)))
        self.expect_rejected("does not match its snapshot image identity")

        shutil.rmtree(self.root)
        shutil.copytree(self.template, self.root)
        path = self.root / "cases" / case_id / "fault.img.gz"
        path.write_bytes(MODULE._canonical_gzip(bytes(MODULE.MAX_RAW_IMAGE_BYTES + 1)))
        self.expect_rejected("expands beyond the raw image limit")

    def test_rejects_manifest_or_hashed_artifact_tampering(self) -> None:
        MODULE.write_manifest(self.root)
        manifest_path = self.root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["case_count"] = 35
        write_json(manifest_path, manifest)
        self.expect_rejected("does not match", verify=True)

        MODULE.write_manifest(self.root)
        log = self.root / "cases" / MODULE.CASE_IDS[0] / "prepare.guest.log"
        log.write_text(log.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        self.expect_rejected("does not match its artifact", verify=True)

    def test_rejects_nonfinite_json_evidence(self) -> None:
        path = self.root / "run.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["source"]["status_bytes"] = float("nan")
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        self.expect_rejected("non-finite JSON number")

    def test_pack_is_deterministic_and_archive_verifies(self) -> None:
        output_dir = Path(self._temp.name) / "packed"
        output_dir.mkdir()
        archive = output_dir / MODULE.ARCHIVE_BASENAME
        first = MODULE.pack_archive(self.root, archive)
        first_hash = sha256_bytes(archive.read_bytes())
        second = MODULE.pack_archive(self.root, archive)
        self.assertEqual(first, second)
        self.assertEqual(first_hash, sha256_bytes(archive.read_bytes()))
        self.assertEqual(MODULE.verify_archive(archive), first)

    def test_safe_writers_parse_facts_and_refuse_overwrite(self) -> None:
        writer_root = Path(self._temp.name) / "writer-root"
        writer_root.mkdir()
        kernel = Path(self._temp.name) / "profile-input.kernel"
        kernel.write_bytes(b"writer profile kernel\n")
        compile_argv = Path(self._temp.name) / "compile.json"
        launch_argv = Path(self._temp.name) / "launch.json"
        writer_baseline_compile = [
            "make",
            "build",
            "BUILDDIR=C:/tmp/kernel-build",
            "INIT_PROC=fsallocfault_ucore",
            MODULE.PROFILE_BUILD_FLAG,
        ]
        write_json(compile_argv, writer_baseline_compile)
        base_launch = ["qemu-system-riscv64", "-device", "agentos-ram-overlay"]
        write_json(launch_argv, base_launch)
        MODULE.init_backend(
            writer_root,
            kernel,
            compile_argv,
            launch_argv,
            capacity_bytes=4 * 1024 * 1024,
            identity="agentos-virtio-ram-overlay",
            version="1",
            abi_version="2",
        )
        case_id = "alloc-intent-crash"
        stage_argv = Path(self._temp.name) / "stage-argv.json"
        write_json(stage_argv, base_launch + ["-case", case_id, "-stage", "prepare"])
        marker = MODULE._receipt_log_marker(
            f"{case_id}:prepare:flush",
            f"{case_id}:prepare",
            "2",
            4 * 1024 * 1024,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            0,
            0,
            False,
        )
        stage_log = Path(self._temp.name) / "stage.log"
        stage_log.write_text(marker + "\n", encoding="utf-8")
        MODULE.record_stage(writer_root, case_id, "prepare", stage_log, stage_argv)
        with self.assertRaisesRegex(MODULE.EvidenceError, "refuses to overwrite"):
            MODULE.record_stage(writer_root, case_id, "prepare", stage_log, stage_argv)

        raw_paths = {}
        for stage in ("before", "fault", "reboot"):
            path = Path(self._temp.name) / f"writer-{stage}.img"
            path.write_bytes(fake_raw_image(case_id, stage))
            raw_paths[stage] = path
        MODULE.record_case(
            writer_root,
            case_id,
            raw_paths["before"],
            raw_paths["fault"],
            raw_paths["reboot"],
        )
        with self.assertRaisesRegex(MODULE.EvidenceError, "refuses to overwrite"):
            MODULE.record_case(
                writer_root,
                case_id,
                raw_paths["before"],
                raw_paths["fault"],
                raw_paths["reboot"],
            )

        mutant = Path(self._temp.name) / "mutant.kernel"
        mutant.write_bytes(b"writer mutant kernel\n")
        selection = Path(self._temp.name) / "selection.diff"
        selection.write_text(
            "- result = fs_durable_barrier_forward();\n"
            "+ #ifdef FS_ALLOCATOR_DELETE_BARRIER_MUTANT\n+ result = 0;\n",
            encoding="utf-8",
        )
        mutation_raw = {}
        for stage in ("before", "fault", "reboot"):
            path = Path(self._temp.name) / f"mutation-{stage}.img"
            path.write_bytes(fake_raw_image("alloc-intent-crash", stage))
            mutation_raw[stage] = path
        mutation_log = Path(self._temp.name) / "mutation.log"
        mutation_log.write_text(
            "fsallocfault_kernel: durability_receipt_failed=1\n"
            "fsalloc-cache: mutation=delete-flush target=allocator-phase-barrier "
            "durable_epoch=1 pending_at_powercut=1 discarded_on_powercut=1 powercut=1\n",
            encoding="utf-8",
        )
        baseline_compile = Path(self._temp.name) / "baseline-compile.json"
        mutant_compile = Path(self._temp.name) / "mutant-compile.json"
        command_argv = Path(self._temp.name) / "mutation-command.json"
        write_json(baseline_compile, writer_baseline_compile)
        write_json(
            mutant_compile,
            [
                "make",
                "build",
                "BUILDDIR=C:/tmp/mutant-kernel-build",
                "INIT_PROC=fsallocfault_ucore",
                MODULE.PROFILE_BUILD_FLAG,
                MODULE.MUTANT_BUILD_FLAG,
            ],
        )
        write_json(
            command_argv,
            base_launch + ["-kernel", "mutant"],
        )
        mutation = MODULE.record_mutation(
            writer_root,
            kernel,
            mutant,
            selection,
            mutation_raw["before"],
            mutation_raw["fault"],
            mutation_raw["reboot"],
            mutation_log,
            baseline_compile,
            mutant_compile,
            command_argv,
        )
        self.assertEqual(
            mutation["verifier_error"]["code"], MODULE.MUTATION_REJECTION_CODE
        )
        with self.assertRaisesRegex(MODULE.EvidenceError, "refuses to overwrite"):
            MODULE.record_mutation(
                writer_root,
                kernel,
                mutant,
                selection,
                mutation_raw["before"],
                mutation_raw["fault"],
                mutation_raw["reboot"],
                mutation_log,
                baseline_compile,
                mutant_compile,
                command_argv,
            )

    def test_archive_rejects_noncanonical_bytes(self) -> None:
        archive = Path(self._temp.name) / MODULE.ARCHIVE_BASENAME
        shutil.copyfile(self.template_archive, archive)
        with archive.open("ab") as handle:
            handle.write(b"noncanonical")
        with self.assertRaisesRegex(MODULE.EvidenceError, "bytes are not canonical"):
            MODULE.verify_archive(archive)

    def write_invalid_archive(
        self, label: str, members: list[tuple[str, bytes, bytes, str]]
    ) -> Path:
        directory = Path(self._temp.name) / label
        directory.mkdir()
        archive_path = directory / MODULE.ARCHIVE_BASENAME
        with tarfile.open(archive_path, mode="w:", format=tarfile.USTAR_FORMAT) as archive:
            for name, member_type, payload, linkname in members:
                info = tarfile.TarInfo(name)
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                info.mode = 0o644
                info.type = member_type
                info.linkname = linkname
                if member_type == tarfile.REGTYPE:
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
                else:
                    info.size = 0
                    archive.addfile(info)
        return archive_path

    def test_archive_rejects_unsafe_paths_duplicates_and_inventory_drift(self) -> None:
        scenarios = (
            (
                "parent",
                [("../escape", tarfile.REGTYPE, b"x", "")],
                "unsafe member path",
            ),
            (
                "absolute",
                [("/absolute", tarfile.REGTYPE, b"x", "")],
                "unsafe member path",
            ),
            (
                "duplicate",
                [
                    ("backend.json", tarfile.REGTYPE, b"{}", ""),
                    ("backend.json", tarfile.REGTYPE, b"{}", ""),
                ],
                "duplicate member",
            ),
            (
                "extra",
                [("unexpected.txt", tarfile.REGTYPE, b"x", "")],
                "unexpected member",
            ),
            (
                "missing",
                [("backend.json", tarfile.REGTYPE, b"{}", "")],
                "archive inventory mismatch",
            ),
        )
        for label, members, message in scenarios:
            with self.subTest(label=label):
                archive = self.write_invalid_archive(label, members)
                with self.assertRaisesRegex(MODULE.EvidenceError, message):
                    MODULE.verify_archive(archive)

    def test_archive_rejects_links_and_device_entries(self) -> None:
        scenarios = (
            ("symlink", tarfile.SYMTYPE, "target"),
            ("hardlink", tarfile.LNKTYPE, "target"),
            ("character-device", tarfile.CHRTYPE, ""),
            ("block-device", tarfile.BLKTYPE, ""),
            ("fifo", tarfile.FIFOTYPE, ""),
        )
        for label, member_type, linkname in scenarios:
            with self.subTest(label=label):
                archive = self.write_invalid_archive(
                    label,
                    [("backend.json", member_type, b"", linkname)],
                )
                with self.assertRaisesRegex(MODULE.EvidenceError, "forbidden special member"):
                    MODULE.verify_archive(archive)


if __name__ == "__main__":
    unittest.main()
