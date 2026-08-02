#!/usr/bin/env python3
"""Regression tests for portable kernel cost evidence."""

from __future__ import annotations

import copy
import base64
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from . import evaluation_kernel_cost as cost
except ImportError:
    import evaluation_kernel_cost as cost


ROOT = Path(__file__).resolve().parent.parent
CONFIG_TEMPLATE = ROOT / "ci" / "evaluation-kernel-cost.json"
COMMIT = "a" * 40


def _valid_elf(payload: bytes) -> bytes:
    ident = bytearray(16)
    ident[:7] = b"\x7fELF\x02\x01\x01"
    total_size = 64 + 56 + len(payload)
    virtual_address = 0x80200000
    header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        bytes(ident),
        2,
        243,
        1,
        virtual_address,
        64,
        0,
        0,
        64,
        56,
        1,
        0,
        0,
        0,
    )
    program_header = struct.pack(
        "<IIQQQQQQ",
        1,
        5,
        0,
        virtual_address,
        virtual_address,
        total_size,
        total_size,
        4096,
    )
    return header + program_header + payload


def _elf_with_file_only_metadata_segment(payload: bytes) -> bytes:
    ident = bytearray(16)
    ident[:7] = b"\x7fELF\x02\x01\x01"
    metadata = b"riscv-attributes"
    program_header_bytes = 2 * 56
    total_size = 64 + program_header_bytes + len(payload) + len(metadata)
    virtual_address = 0x80200000
    header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        bytes(ident), 2, 243, 1, virtual_address, 64, 0, 0,
        64, 56, 2, 0, 0, 0,
    )
    load = struct.pack(
        "<IIQQQQQQ",
        1, 5, 0, virtual_address, virtual_address, total_size, total_size, 4096,
    )
    metadata_offset = 64 + program_header_bytes + len(payload)
    attributes = struct.pack(
        "<IIQQQQQQ",
        0x70000003, 4, metadata_offset, 0, 0, len(metadata), 0, 1,
    )
    return header + load + attributes + payload + metadata


def _write_json(path: Path, value: object) -> bytes:
    raw = (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


class Fixture:
    def __init__(
        self,
        *,
        source_commit: str = COMMIT,
        run_id: str = "kernel-cost-run-1",
    ) -> None:
        self.source_commit = source_commit
        self.run_id = run_id
        self._temporary = tempfile.TemporaryDirectory(
            prefix="kernel-cost-", dir=Path(__file__).resolve().parent
        )
        self.root = Path(self._temporary.name)
        self.config = self.root / "ci" / "evaluation-kernel-cost.json"
        self.build_config = self.root / "ci" / "kernel-build-config.json"
        self.build_log = self.root / "evidence" / "kernel-build.log"
        self.environment = self.root / "evidence" / "environment.json"
        self.build_manifest = self.root / "evidence" / "kernel-build.json"
        self.report = self.root / "evidence" / "kernel-cost-report.json"
        self.baseline = self.root / "baseline_ucore" / "build" / "kernel"
        self.agentos = self.root / "build" / "kernel"
        self.tool = self.root / "tools" / "riscv64-linux-gnu-size"
        self.make = self.root / "tools" / "make"
        self.config.parent.mkdir(parents=True)
        self.baseline.parent.mkdir(parents=True)
        self.agentos.parent.mkdir(parents=True)
        self.tool.parent.mkdir(parents=True)
        self.config.write_bytes(CONFIG_TEMPLATE.read_bytes())
        self.baseline.write_bytes(_valid_elf(b"B" * 80))
        self.agentos.write_bytes(_valid_elf(b"A" * 56))
        self.tool.write_bytes(b"fixture-size-tool-v1")
        self.make.write_bytes(b"fixture-make-tool-v1")
        make_path = str(self.make.resolve())
        make_version = "GNU Make 4.4 fixture"
        toolprefix = str((self.root / "tools" / "riscv64-linux-gnu-").resolve())
        tool_versions = {
            "gcc": "riscv64-linux-gnu-gcc fixture",
            "ld": "GNU ld fixture",
            "objcopy": "GNU objcopy fixture",
            "objdump": "GNU objdump fixture",
            "nm": "GNU nm fixture",
            "size": "GNU size fixture",
        }
        toolchain_tools = []
        for name in cost.TRUSTED_BUILD_TOOL_NAMES:
            path = Path(toolprefix + name)
            path.write_bytes(f"fixture-{name}-tool-v1".encode())
            toolchain_tools.append(
                {
                    "name": name,
                    "path": str(path),
                    "resolved_path": str(path.resolve()),
                    "sha256": cost._file_sha(path),
                    "version_argv": [str(path), "--version"],
                    "version": tool_versions[name],
                }
            )
        toolchain_sha = cost._bytes_sha(
            cost._canonical_json({"prefix": toolprefix, "tools": toolchain_tools})
        )
        build_environment = {
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/fixture/bin",
            "PYTHONHASHSEED": "0",
            "SOURCE_DATE_EPOCH": "1700000000",
            "TOOLPREFIX": toolprefix,
            "TZ": "UTC",
        }
        build_environment_sha = cost._bytes_sha(cost._canonical_json(build_environment))
        self.trusted_config = {
            "schema_version": cost.TRUSTED_BUILD_SCHEMA_VERSION,
            "kind": cost.TRUSTED_BUILD_CONFIG_KIND,
            "run_id": run_id,
            "source_commit": source_commit,
            "kernel_cost_config": {
                "path": "ci/evaluation-kernel-cost.json",
                "sha256": cost._file_sha(self.config),
            },
            "make_tool": {
                "path": make_path,
                "sha256": cost._file_sha(self.make),
                "version_argv": [make_path, "--version"],
                "version": make_version,
            },
            "toolchain": {
                "prefix": toolprefix,
                "identity_sha256": toolchain_sha,
                "tools": toolchain_tools,
            },
            "command_policy": {
                "timeout_seconds": cost.TRUSTED_BUILD_TIMEOUT_SECONDS,
                "max_output_bytes": cost.TRUSTED_BUILD_MAX_OUTPUT_BYTES,
            },
            "environment": build_environment,
            "environment_sha256": build_environment_sha,
            "targets": [
                {
                    "id": "baseline",
                    "role": "baseline",
                    "path": "baseline_ucore/build/kernel",
                    "clean_argv": [
                        make_path, "-C", "baseline_ucore",
                        f"TOOLPREFIX={toolprefix}", "clean",
                    ],
                    "build_argv": [
                        make_path, "-C", "baseline_ucore",
                        f"TOOLPREFIX={toolprefix}", "build/kernel",
                    ],
                },
                {
                    "id": "agentos",
                    "role": "treatment",
                    "path": "build/kernel",
                    "clean_argv": [make_path, f"TOOLPREFIX={toolprefix}", "clean"],
                    "build_argv": [
                        make_path, f"TOOLPREFIX={toolprefix}", "build/kernel",
                    ],
                },
            ],
            "guardrail_commands": [
                {
                    "id": "struct_proc_bytes",
                    "target_id": "agentos",
                    "phase": "kernel_budget",
                    "argv": [
                        make_path, f"TOOLPREFIX={toolprefix}",
                        "kernel-budget-check",
                    ],
                },
                {
                    "id": "user_stack_call_path_bytes",
                    "target_id": "agentos",
                    "phase": "user_stack",
                    "argv": [
                        make_path, f"TOOLPREFIX={toolprefix}", "user-stack-check",
                    ],
                },
            ],
        }
        _write_json(self.build_config, self.trusted_config)

        def command_record(
            sequence: int, target_id: str, phase: str, argv: list[str], stdout: bytes
        ) -> dict[str, object]:
            stderr = b""
            return {
                "sequence": sequence,
                "target_id": target_id,
                "phase": phase,
                "cwd": ".",
                "argv": argv,
                "environment_sha256": build_environment_sha,
                "returncode": 0,
                "duration_ms": 1,
                "error": None,
                "stdout_base64": base64.b64encode(stdout).decode("ascii"),
                "stdout_sha256": cost._bytes_sha(stdout),
                "stderr_base64": base64.b64encode(stderr).decode("ascii"),
                "stderr_sha256": cost._bytes_sha(stderr),
            }

        build_start = 1 + len(toolchain_tools)
        guardrail_start = build_start + 4
        self.trusted_log = {
            "schema_version": cost.TRUSTED_BUILD_SCHEMA_VERSION,
            "kind": cost.TRUSTED_BUILD_LOG_KIND,
            "run_id": run_id,
            "source_commit": source_commit,
            "environment_sha256": build_environment_sha,
            "toolchain_sha256": toolchain_sha,
            "commands": [
                command_record(0, "builder", "make_version", [make_path, "--version"], (make_version + "\n").encode()),
                *[
                    command_record(
                        index + 1,
                        "toolchain",
                        f"{tool['name']}_version",
                        tool["version_argv"],
                        (tool["version"] + "\n").encode(),
                    )
                    for index, tool in enumerate(toolchain_tools)
                ],
                command_record(build_start, "baseline", "clean", [make_path, "-C", "baseline_ucore", f"TOOLPREFIX={toolprefix}", "clean"], b"baseline clean\n"),
                command_record(build_start + 1, "baseline", "build", [make_path, "-C", "baseline_ucore", f"TOOLPREFIX={toolprefix}", "build/kernel"], b"baseline build\n"),
                command_record(build_start + 2, "agentos", "clean", [make_path, f"TOOLPREFIX={toolprefix}", "clean"], b"agentos clean\n"),
                command_record(build_start + 3, "agentos", "build", [make_path, f"TOOLPREFIX={toolprefix}", "build/kernel"], b"agentos build\n"),
                command_record(
                    guardrail_start,
                    "agentos",
                    "kernel_budget",
                    [make_path, f"TOOLPREFIX={toolprefix}", "kernel-budget-check"],
                    b"[kernel-budget] struct proc: actual=26448 bytes baseline=26448 bytes limit=27233 bytes\n",
                ),
                command_record(
                    guardrail_start + 1,
                    "agentos",
                    "user_stack",
                    [make_path, f"TOOLPREFIX={toolprefix}", "user-stack-check"],
                    b"user stack call-path budget: apps=182 max=2944 (app:main) budget=3072 stack=4096 reserve=1024\n",
                ),
            ],
        }
        _write_json(self.build_log, self.trusted_log)
        environment = {
            "schema_version": 1,
            "kind": cost.ENVIRONMENT_KIND,
            "run_id": run_id,
            "source_commit": source_commit,
            "environment_id": "qemu-riscv64-fixture",
            "facts": [
                {"name": "build_environment_sha256", "value": build_environment_sha},
                {"name": "builder", "value": f"evaluation_kernel_build.py/{cost.TRUSTED_BUILD_SCHEMA_VERSION}"},
                {"name": "git", "value": "git version fixture"},
                {"name": "make", "value": make_version},
                {"name": "make_path", "value": make_path},
                {"name": "make_sha256", "value": cost._file_sha(self.make)},
                {"name": "platform", "value": "fixture-platform"},
                {"name": "python", "value": "3.fixture"},
                {"name": "source_date_epoch", "value": "1700000000"},
                {"name": "toolchain_identity_sha256", "value": toolchain_sha},
                {"name": "toolchain_prefix", "value": toolprefix},
            ],
        }
        environment_raw = _write_json(self.environment, environment)
        self.build = {
            "schema_version": 1,
            "kind": cost.BUILD_KIND,
            "run_id": environment["run_id"],
            "source_commit": source_commit,
            "environment_sha256": cost._bytes_sha(environment_raw),
            "toolchain_sha256": toolchain_sha,
            "build_config": {
                "path": "ci/kernel-build-config.json",
                "sha256": cost._file_sha(self.build_config),
            },
            "build_log": {
                "path": "evidence/kernel-build.log",
                "sha256": cost._file_sha(self.build_log),
            },
            "targets": [
                {
                    "id": "baseline",
                    "path": "baseline_ucore/build/kernel",
                    "sha256": cost._file_sha(self.baseline),
                    "command_argv": [make_path, "-C", "baseline_ucore", f"TOOLPREFIX={toolprefix}", "build/kernel"],
                },
                {
                    "id": "agentos",
                    "path": "build/kernel",
                    "sha256": cost._file_sha(self.agentos),
                    "command_argv": [make_path, f"TOOLPREFIX={toolprefix}", "build/kernel"],
                },
            ],
        }
        _write_json(self.build_manifest, self.build)
        self.commands: list[list[str]] = []

    def close(self) -> None:
        self._temporary.cleanup()

    def rewrite_build(self) -> None:
        _write_json(self.build_manifest, self.build)

    def rewrite_trusted_config(self) -> None:
        _write_json(self.build_config, self.trusted_config)

    def rewrite_trusted_log(self) -> None:
        _write_json(self.build_log, self.trusted_log)

    def rebind_portable_report(self, report: dict[str, object]) -> None:
        self.build["build_config"]["sha256"] = cost._file_sha(self.build_config)
        self.build["build_log"]["sha256"] = cost._file_sha(self.build_log)
        self.rewrite_build()
        report["binding"]["build_manifest"]["sha256"] = cost._file_sha(
            self.build_manifest
        )
        report["content_sha256"] = cost._bytes_sha(
            cost._canonical_json(
                {key: value for key, value in report.items() if key != "content_sha256"}
            )
        )
        self.save_report(report)

    def repository(self, _root: Path) -> tuple[str, bool]:
        return self.source_commit, True

    def runner(
        self, argv: cost.Sequence[str], timeout: int, maximum: int
    ) -> cost.ToolExecution:
        del timeout, maximum
        command = list(argv)
        self.commands.append(command)
        if command[-1] == "--version":
            return cost.ToolExecution(0, b"GNU size (fixture) 2.46\n", b"")
        source = Path(command[-1])
        values = (100, 8, 200) if "baseline_ucore" in source.parts else (90, 4, 120)
        total = sum(values)
        output = (
            "text data bss dec hex filename\n"
            f"{values[0]} {values[1]} {values[2]} {total} {total:x} {source}\n"
        ).encode()
        return cost.ToolExecution(0, output, b"")

    def collect(
        self,
        *,
        tool: Path | None = None,
        repository_reader: cost.RepositoryReader | None = None,
        runner: cost.ToolRunner | None = None,
    ) -> dict[str, object]:
        return cost.collect_report(
            config_path=self.config,
            repository_root=self.root,
            environment_manifest_path=self.environment,
            build_manifest_path=self.build_manifest,
            size_tool=tool or self.tool,
            runner=runner or self.runner,
            repository_reader=repository_reader or self.repository,
        )

    def save_report(self, report: dict[str, object]) -> None:
        _write_json(self.report, report)


class KernelCostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_collects_manifest_bound_costs_and_raw_receipts(self) -> None:
        report = self.fixture.collect()
        config, _ = cost.load_config(self.fixture.config)
        self.assertIs(cost.validate_report(report, config), report)
        targets = {target["id"]: target for target in report["targets"]}
        baseline = {metric["id"]: metric["value"] for metric in targets["baseline"]["metrics"]}
        agentos = {metric["id"]: metric["value"] for metric in targets["agentos"]["metrics"]}
        self.assertEqual(baseline["text_bytes"], 100)
        self.assertEqual(agentos["bss_bytes"], 120)
        self.assertEqual(targets["baseline"]["source"]["elf_identity"]["machine"], "RISC-V")
        self.assertEqual(report["tool"]["version"], "GNU size (fixture) 2.46")
        self.assertIn("stdout_base64", targets["agentos"]["size_command"])

    def test_collection_can_emit_a_relocatable_run_root(self) -> None:
        portable = self.fixture.root / "portable-run"
        portable.mkdir()
        config_path = portable / "kernel-cost-config.json"
        environment_path = portable / "environment.json"
        build_config_path = portable / "kernel-build-config.json"
        build_log_path = portable / "kernel-build.log"
        for source, destination in (
            (self.fixture.config, config_path),
            (self.fixture.environment, environment_path),
            (self.fixture.build_config, build_config_path),
            (self.fixture.build_log, build_log_path),
        ):
            destination.write_bytes(source.read_bytes())
        build = dict(self.fixture.build)
        build["build_config"] = {
            "path": "kernel-build-config.json",
            "sha256": cost._file_sha(build_config_path),
        }
        build["build_log"] = {
            "path": "kernel-build.log",
            "sha256": cost._file_sha(build_log_path),
        }
        build_path = portable / "kernel-build.json"
        _write_json(build_path, build)
        report = cost.collect_report(
            config_path=config_path,
            repository_root=self.fixture.root,
            environment_manifest_path=environment_path,
            build_manifest_path=build_path,
            size_tool=self.fixture.tool,
            evidence_root=portable,
            runner=self.fixture.runner,
            repository_reader=self.fixture.repository,
        )
        report_path = portable / "kernel-cost-report.json"
        _write_json(report_path, report)
        verified, _, _ = cost.verify_portable(report_path, config_path, portable)
        self.assertEqual(verified, report)

    def test_missing_target_is_unavailable_without_zero_fill(self) -> None:
        self.fixture.baseline.unlink()
        report = self.fixture.collect()
        target = report["targets"][0]
        self.assertEqual(target["status"], "unavailable")
        self.assertTrue(all(metric["value"] is None for metric in target["metrics"]))
        self.assertTrue(
            all(metric["status"] == "unavailable" for metric in target["metrics"])
        )

    def test_missing_size_tool_keeps_only_elf_file_metric(self) -> None:
        report = self.fixture.collect(tool=self.fixture.root / "tools" / "missing")
        for target in report["targets"]:
            self.assertEqual(target["status"], "partial")
            self.assertEqual(target["metrics"][0]["status"], "measured")
            self.assertTrue(
                all(metric["value"] is None for metric in target["metrics"][1:])
            )

    def test_source_hash_must_match_build_manifest(self) -> None:
        self.fixture.agentos.write_bytes(_valid_elf(b"tampered"))
        with self.assertRaisesRegex(cost.KernelCostError, "build manifest"):
            self.fixture.collect()

    def test_invalid_elf_header_cannot_produce_a_measured_file_metric(self) -> None:
        self.fixture.agentos.write_bytes(b"\x7fELF" + b"not-an-elf")
        self.fixture.build["targets"][1]["sha256"] = cost._file_sha(self.fixture.agentos)
        self.fixture.rewrite_build()
        report = self.fixture.collect()
        target = report["targets"][1]
        self.assertEqual(target["status"], "failed")
        self.assertTrue(all(metric["value"] is None for metric in target["metrics"]))

    def test_file_only_metadata_segment_is_valid(self) -> None:
        artifact = self.fixture.root / "file-only-metadata.elf"
        artifact.write_bytes(_elf_with_file_only_metadata_segment(b"kernel"))
        self.assertEqual(cost.parse_elf_identity(artifact)["machine"], "RISC-V")

    def test_load_segment_file_image_cannot_exceed_memory_image(self) -> None:
        artifact = self.fixture.root / "invalid-load-size.elf"
        raw = bytearray(_valid_elf(b"kernel"))
        file_bytes = struct.unpack_from("<Q", raw, 64 + 32)[0]
        struct.pack_into("<Q", raw, 64 + 40, file_bytes - 1)
        artifact.write_bytes(raw)
        with self.assertRaisesRegex(cost.KernelCostError, "PT_LOAD"):
            cost.parse_elf_identity(artifact)

    def test_file_only_metadata_segment_still_obeys_file_bounds(self) -> None:
        artifact = self.fixture.root / "invalid-metadata-range.elf"
        raw = bytearray(_elf_with_file_only_metadata_segment(b"kernel"))
        second_program_header = 64 + 56
        struct.pack_into("<Q", raw, second_program_header + 8, len(raw) + 1)
        artifact.write_bytes(raw)
        with self.assertRaisesRegex(cost.KernelCostError, "escapes the file"):
            cost.parse_elf_identity(artifact)

    def test_load_segment_memory_range_cannot_overflow_elf64(self) -> None:
        artifact = self.fixture.root / "invalid-load-address-range.elf"
        raw = bytearray(_valid_elf(b"kernel"))
        struct.pack_into("<Q", raw, 64 + 16, (1 << 64) - 4)
        struct.pack_into("<Q", raw, 64 + 32, 1)
        struct.pack_into("<Q", raw, 64 + 40, 8)
        artifact.write_bytes(raw)
        with self.assertRaisesRegex(cost.KernelCostError, "overflows ELF64"):
            cost.parse_elf_identity(artifact)

    def test_dirty_or_wrong_commit_repository_is_rejected(self) -> None:
        for state in ((COMMIT, False), ("c" * 40, True)):
            with self.subTest(state=state):
                with self.assertRaisesRegex(cost.KernelCostError, "clean HEAD"):
                    self.fixture.collect(repository_reader=lambda _root, state=state: state)

    def test_repository_state_rejects_hidden_index_flags(self) -> None:
        source = self.fixture.root / "tracked-source.c"
        source.write_bytes(b"int trusted_source;\n")
        _git(self.fixture.root, "init", "-q")
        _git(self.fixture.root, "config", "user.name", "Kernel Cost Test")
        _git(
            self.fixture.root,
            "config",
            "user.email",
            "kernel-cost@example.invalid",
        )
        _git(self.fixture.root, "add", "--", source.name)
        _git(self.fixture.root, "commit", "-q", "-m", "source identity fixture")
        original = source.read_bytes()
        for enabled in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=enabled):
                _git(self.fixture.root, "update-index", enabled, "--", source.name)
                source.write_bytes(original + b"/* hidden tamper */\n")
                status = _git(
                    self.fixture.root,
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=no",
                ).stdout
                self.assertEqual(status, b"")
                with self.assertRaisesRegex(
                    cost.KernelCostError, "tracked source identity is unsafe"
                ):
                    cost._repository_state(self.fixture.root)
                source.write_bytes(original)
                _git(
                    self.fixture.root,
                    "update-index",
                    "--no-assume-unchanged",
                    "--no-skip-worktree",
                    "--",
                    source.name,
                )

    def test_production_gate_rejects_info_exclude_sources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kernel-cost-source-gate-") as temporary:
            root = Path(temporary)
            (root / "os").mkdir()
            (root / "user" / "src").mkdir(parents=True)
            (root / "Makefile").write_text("all:\n\t@true\n", encoding="ascii")
            (root / "os" / "kernel.c").write_text("int kernel;\n", encoding="ascii")
            (root / "user" / "src" / "app.c").write_text(
                "int app;\n", encoding="ascii"
            )
            _git(root, "init", "-q")
            _git(root, "config", "user.name", "Kernel Cost Gate")
            _git(root, "config", "user.email", "kernel-gate@example.invalid")
            _git(root, "add", "-A")
            _git(root, "commit", "-q", "-m", "fixture")
            commit = _git(root, "rev-parse", "HEAD").stdout.decode().strip()
            (root / ".git" / "info" / "exclude").write_text(
                "os/hidden.c\nuser/src/hidden.c\n", encoding="ascii"
            )
            for relative in ("os/hidden.c", "user/src/hidden.c"):
                hidden = root / relative
                hidden.write_text("int hidden;\n", encoding="ascii")
                self.assertEqual(
                    _git(
                        root,
                        "status",
                        "--porcelain=v1",
                        "--untracked-files=all",
                    ).stdout,
                    b"",
                )
                with self.assertRaisesRegex(cost.KernelCostError, "source gate"):
                    cost._production_source_gate(
                        root, commit, None, "before collection"
                    )
                hidden.unlink()

    def test_build_target_swap_is_rejected_before_tools_run(self) -> None:
        self.fixture.build["targets"][0]["path"] = "build/kernel"
        self.fixture.build["targets"][1]["path"] = "baseline_ucore/build/kernel"
        self.fixture.rewrite_build()
        with self.assertRaisesRegex(cost.KernelCostError, "role/path binding"):
            self.fixture.collect()
        self.assertEqual(self.fixture.commands, [])

    def test_build_log_and_environment_are_portably_bound(self) -> None:
        report = self.fixture.collect()
        self.fixture.save_report(report)
        cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)
        self.fixture.build_log.write_text("forged\n", encoding="utf-8")
        with self.assertRaisesRegex(cost.KernelCostError, "build log"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

    def test_handwritten_config_and_plaintext_log_cannot_pass_portable_verify(self) -> None:
        report = self.fixture.collect()
        self.fixture.build_config.write_text(
            '{"handwritten":true}\n', encoding="utf-8", newline="\n"
        )
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "trusted build config fields"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

        self.fixture.rewrite_trusted_config()
        self.fixture.build_log.write_text(
            "baseline build passed\nagentos build passed\n",
            encoding="utf-8",
            newline="\n",
        )
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "trusted build log.*strict JSON"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

    def test_portable_verify_rejects_command_and_returncode_tampering(self) -> None:
        report = self.fixture.collect()
        build_index = 1 + len(cost.TRUSTED_BUILD_TOOL_NAMES) + 1
        self.fixture.trusted_log["commands"][build_index]["argv"][-1] = "forged-target"
        self.fixture.rewrite_trusted_log()
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "sequence/cwd/phase/target/argv"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

        self.fixture.trusted_log["commands"][build_index]["argv"][-1] = "build/kernel"
        self.fixture.trusted_log["commands"][build_index + 1]["returncode"] = 23
        self.fixture.rewrite_trusted_log()
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "not a successful command"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

    def test_build_manifest_command_must_match_trusted_log(self) -> None:
        report = self.fixture.collect()
        self.fixture.build["targets"][1]["command_argv"][-1] = "clean"
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "command_argv"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

    def test_portable_verify_rejects_environment_and_stream_hash_tampering(self) -> None:
        report = self.fixture.collect()
        self.fixture.trusted_config["environment"]["TZ"] = "Asia/Shanghai"
        self.fixture.rewrite_trusted_config()
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "deterministic environment"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

        self.fixture.trusted_config["environment"]["TZ"] = "UTC"
        self.fixture.trusted_config["make_tool"]["sha256"] = "f" * 64
        self.fixture.rewrite_trusted_config()
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "environment/Make identity"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

        self.fixture.trusted_config["make_tool"]["sha256"] = cost._file_sha(
            self.fixture.make
        )
        self.fixture.rewrite_trusted_config()
        self.fixture.trusted_log["commands"][4]["stdout_sha256"] = "0" * 64
        self.fixture.rewrite_trusted_log()
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "stdout SHA-256"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

    def test_trusted_build_locale_domain_is_closed_and_utf8_capable(self) -> None:
        config, config_raw = cost.load_config(self.fixture.config)
        environment = self.fixture.trusted_config["environment"]
        environment["LANG"] = "C.UTF-8"
        environment["LC_ALL"] = "C.UTF-8"
        self.fixture.trusted_config["environment_sha256"] = cost._bytes_sha(
            cost._canonical_json(environment)
        )
        self.assertIs(
            cost.validate_trusted_build_config(
                self.fixture.trusted_config,
                config,
                cost._bytes_sha(config_raw),
            ),
            self.fixture.trusted_config,
        )

        environment["LC_ALL"] = "C"
        self.fixture.trusted_config["environment_sha256"] = cost._bytes_sha(
            cost._canonical_json(environment)
        )
        with self.assertRaisesRegex(cost.KernelCostError, "deterministic environment"):
            cost.validate_trusted_build_config(
                self.fixture.trusted_config,
                config,
                cost._bytes_sha(config_raw),
            )

        environment["LANG"] = "en_US.UTF-8"
        environment["LC_ALL"] = "en_US.UTF-8"
        self.fixture.trusted_config["environment_sha256"] = cost._bytes_sha(
            cost._canonical_json(environment)
        )
        with self.assertRaisesRegex(cost.KernelCostError, "deterministic environment"):
            cost.validate_trusted_build_config(
                self.fixture.trusted_config,
                config,
                cost._bytes_sha(config_raw),
            )

    def test_portable_verify_rejects_toolchain_path_hash_and_version_mutation(self) -> None:
        report = self.fixture.collect()
        self.fixture.trusted_config["toolchain"]["tools"][0]["path"] = str(
            (self.fixture.root / "tools" / "host-gcc").resolve()
        )
        self.fixture.rewrite_trusted_config()
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "derived from TOOLPREFIX"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

        self.fixture.close()
        self.fixture = Fixture()
        report = self.fixture.collect()
        self.fixture.trusted_config["toolchain"]["tools"][1]["sha256"] = "f" * 64
        self.fixture.rewrite_trusted_config()
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "identity SHA-256 differs"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

        self.fixture.close()
        self.fixture = Fixture()
        report = self.fixture.collect()
        forged = b"forged cross linker\n"
        command = self.fixture.trusted_log["commands"][3]
        command["stdout_base64"] = base64.b64encode(forged).decode("ascii")
        command["stdout_sha256"] = cost._bytes_sha(forged)
        self.fixture.rewrite_trusted_log()
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "version output differs"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

    def test_portable_verify_rejects_toolprefix_and_manifest_rebinding(self) -> None:
        report = self.fixture.collect()
        self.fixture.trusted_config["environment"]["TOOLPREFIX"] = str(
            (self.fixture.root / "tools" / "host-").resolve()
        )
        self.fixture.rewrite_trusted_config()
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "deterministic environment"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

        self.fixture.close()
        self.fixture = Fixture()
        report = self.fixture.collect()
        self.fixture.build["toolchain_sha256"] = "0" * 64
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(cost.KernelCostError, "manifest identity differs"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

    def test_environment_rebinding_is_rejected_even_after_content_rehash(self) -> None:
        report = self.fixture.collect()
        forged = copy.deepcopy(report)
        forged["binding"]["environment_sha256"] = "f" * 64
        forged["content_sha256"] = cost._bytes_sha(
            cost._canonical_json(
                {key: value for key, value in forged.items() if key != "content_sha256"}
            )
        )
        self.fixture.save_report(forged)
        with self.assertRaisesRegex(cost.KernelCostError, "bindings differ"):
            cost.verify_portable(self.fixture.report, self.fixture.config, self.fixture.root)

    def test_runtime_values_are_rederived_from_raw_size_output(self) -> None:
        report = self.fixture.collect()
        forged = copy.deepcopy(report)
        forged["targets"][1]["metrics"][1]["value"] += 1000000
        forged["content_sha256"] = cost._bytes_sha(
            cost._canonical_json(
                {key: value for key, value in forged.items() if key != "content_sha256"}
            )
        )
        config, _ = cost.load_config(self.fixture.config)
        with self.assertRaisesRegex(cost.KernelCostError, "raw size output"):
            cost.validate_report(forged, config)

    def test_long_integer_nan_and_unknown_fields_fail_closed(self) -> None:
        expected = str(self.fixture.agentos)
        too_long = (
            "text data bss dec hex filename\n"
            f"{'9' * 10000} 1 1 1 1 {expected}\n"
        ).encode()
        with self.assertRaises(cost.KernelCostError):
            cost.parse_size_output(too_long, expected)

        bad_config = self.fixture.root / "ci" / "nan.json"
        bad_config.write_text('{"schema_version":NaN}', encoding="utf-8")
        with self.assertRaisesRegex(cost.KernelCostError, "strict JSON"):
            cost.load_config(bad_config)

        report = self.fixture.collect()
        report["unknown"] = True
        config, _ = cost.load_config(self.fixture.config)
        with self.assertRaisesRegex(cost.KernelCostError, "extra=.*unknown"):
            cost.validate_report(report, config)

    def test_portable_verify_survives_relocation_without_elf_or_tool(self) -> None:
        report = self.fixture.collect()
        self.fixture.save_report(report)
        with tempfile.TemporaryDirectory(
            prefix="kernel-cost-relocated-", dir=Path(__file__).resolve().parent
        ) as temporary:
            relocated = Path(temporary)
            for relative in (
                "ci/evaluation-kernel-cost.json",
                "ci/kernel-build-config.json",
                "evidence/environment.json",
                "evidence/kernel-build.json",
                "evidence/kernel-build.log",
                "evidence/kernel-cost-report.json",
            ):
                source = self.fixture.root / relative
                target = relocated / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            verified, _, _ = cost.verify_portable(
                relocated / "evidence/kernel-cost-report.json",
                relocated / "ci/evaluation-kernel-cost.json",
                relocated,
            )
            self.assertEqual(verified["binding"]["source_commit"], COMMIT)
            self.assertFalse((relocated / "build/kernel").exists())

    def test_local_verify_replays_tool_and_detects_artifact_tamper(self) -> None:
        report = self.fixture.collect()
        self.fixture.save_report(report)
        cost.verify_local(
            self.fixture.report,
            self.fixture.config,
            self.fixture.root,
            self.fixture.root,
            self.fixture.tool,
            runner=self.fixture.runner,
            repository_reader=self.fixture.repository,
        )
        self.fixture.agentos.write_bytes(_valid_elf(b"changed"))
        with self.assertRaisesRegex(cost.KernelCostError, "local target changed"):
            cost.verify_local(
                self.fixture.report,
                self.fixture.config,
                self.fixture.root,
                self.fixture.root,
                self.fixture.tool,
                runner=self.fixture.runner,
                repository_reader=self.fixture.repository,
            )

    def test_guardrails_are_rederived_from_canonical_checker_output(self) -> None:
        report = self.fixture.collect()
        forged = copy.deepcopy(report)
        forged["guardrails"][0]["value"] -= 1
        forged["content_sha256"] = cost._bytes_sha(
            cost._canonical_json(
                {
                    key: value
                    for key, value in forged.items()
                    if key != "content_sha256"
                }
            )
        )
        self.fixture.save_report(forged)
        with self.assertRaisesRegex(
            cost.KernelCostError, "differ from canonical checker output"
        ):
            cost.verify_portable(
                self.fixture.report, self.fixture.config, self.fixture.root
            )

        changed = (
            b"[kernel-budget] struct proc: actual=26447 bytes "
            b"baseline=26448 bytes limit=27233 bytes\n"
        )
        command = self.fixture.trusted_log["commands"][-2]
        command["stdout_base64"] = base64.b64encode(changed).decode("ascii")
        command["stdout_sha256"] = cost._bytes_sha(changed)
        self.fixture.rewrite_trusted_log()
        self.fixture.rebind_portable_report(copy.deepcopy(report))
        with self.assertRaisesRegex(
            cost.KernelCostError, "differ from canonical checker output"
        ):
            cost.verify_portable(
                self.fixture.report, self.fixture.config, self.fixture.root
            )

    def test_fragment_is_accepted_by_dashboard_contract(self) -> None:
        report = self.fixture.collect()
        self.fixture.save_report(report)
        fragment = cost.build_dashboard_fragment(
            self.fixture.report, self.fixture.config, self.fixture.root
        )
        # Kernel cost is a separately re-derived sidecar, not an evaluation
        # summary.  The renderer compares this exact fragment with a fresh
        # build_dashboard_fragment() result before displaying it; treating its
        # intentionally smaller methodology as the headline summary contract
        # would conflate artifact cost with performance inference.
        self.assertEqual(
            set(fragment),
            {
                "schema_version", "kind", "run", "targets", "benchmarks",
                "guardrails", "evidence", "methodology",
            },
        )
        self.assertEqual(fragment["schema_version"], 1)
        self.assertEqual(fragment["kind"], cost.FRAGMENT_KIND)
        self.assertEqual(fragment["run"]["commit"], COMMIT)
        self.assertTrue(fragment["benchmarks"])
        self.assertEqual(fragment["evidence"][0]["id"], "kernel-cost-report")
        self.assertIn(
            "not evidence of CPU performance",
            fragment["methodology"]["limitations"][0],
        )


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(KernelCostTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
