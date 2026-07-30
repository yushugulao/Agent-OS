#!/usr/bin/env python3
"""Regression tests for the trusted dual-kernel build harness."""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from . import evaluation_kernel_build as builder
    from . import evaluation_kernel_cost as cost
except ImportError:
    import evaluation_kernel_build as builder
    import evaluation_kernel_cost as cost


ROOT = Path(__file__).resolve().parent.parent
CONFIG_TEMPLATE = ROOT / "ci" / "evaluation-kernel-cost.json"


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


def _run(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


class FakeMake:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.fail: tuple[str, str] | None = None
        self.mutate_source = False
        self.leave_stale = False
        self.mutate_previous_artifact = False

    def __call__(
        self,
        argv: builder.Sequence[str],
        cwd: Path,
        environment: builder.Mapping[str, str],
        timeout: int,
        maximum: int,
    ) -> builder.CommandExecution:
        del cwd, timeout, maximum
        command = list(argv)
        self.calls.append((command, dict(environment)))
        if command[-1] == "--version":
            return builder.CommandExecution(0, b"GNU Make 4.4 fixture\n", b"", 3)
        baseline = "-C" in command
        target_id = "baseline" if baseline else "agentos"
        phase = "clean" if command[-1] == "clean" else "build"
        artifact = (
            self.root / "baseline_ucore" / "build" / "kernel"
            if baseline
            else self.root / "build" / "kernel"
        )
        if self.fail == (target_id, phase):
            return builder.CommandExecution(23, b"", b"fixture failure\n", 4)
        if phase == "clean":
            if not self.leave_stale:
                artifact.unlink(missing_ok=True)
        else:
            artifact.parent.mkdir(parents=True, exist_ok=True)
            payload = b"baseline" if baseline else b"agentos"
            artifact.write_bytes(_valid_elf(payload))
            if self.mutate_previous_artifact and not baseline:
                previous = self.root / "baseline_ucore" / "build" / "kernel"
                previous.write_bytes(_valid_elf(b"changed later"))
            if self.mutate_source:
                (self.root / "Makefile").write_text("mutated\n", encoding="utf-8")
        return builder.CommandExecution(
            0,
            f"{target_id} {phase}\n".encode(),
            b"",
            5,
        )


class Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="trusted-kernel-build-")
        self.root = Path(self.temporary.name)
        (self.root / "ci").mkdir(parents=True)
        (self.root / "baseline_ucore").mkdir()
        self.config = self.root / "ci" / "evaluation-kernel-cost.json"
        self.config.write_bytes(CONFIG_TEMPLATE.read_bytes())
        (self.root / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
        (self.root / "baseline_ucore" / "Makefile").write_text(
            "all:\n\t@true\n", encoding="utf-8"
        )
        (self.root / ".gitignore").write_text(
            "/build/\n/baseline_ucore/build/\n/results/\n", encoding="utf-8"
        )
        _run(self.root, "init", "-q")
        _run(self.root, "config", "user.name", "Kernel Build Test")
        _run(self.root, "config", "user.email", "kernel-build@example.invalid")
        _run(self.root, "add", ".")
        _run(self.root, "commit", "-q", "-m", "fixture")
        self.commit = _run(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        self.output = self.root / "results" / "kernel-run"
        self.make_tool = Path(sys.executable)
        self.runner = FakeMake(self.root)

    def close(self) -> None:
        self.temporary.cleanup()

    def build(self) -> dict[str, object]:
        return builder.build_evidence(
            config_path=self.config,
            repository_root=self.root,
            make_tool=self.make_tool,
            run_id="kernel-fixture-1",
            output_dir=self.output,
            runner=self.runner,
        )


class TrustedKernelBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_builds_both_targets_and_emits_collector_compatible_sidecars(self) -> None:
        result = self.fixture.build()
        self.assertEqual(result["source_commit"], self.fixture.commit)
        environment_path = self.fixture.output / "environment.json"
        manifest_path = self.fixture.output / "kernel-build.json"
        environment, environment_raw = cost.load_environment(environment_path)
        config, config_raw = cost.load_config(self.fixture.config)
        manifest, _ = cost.load_build_manifest(manifest_path, config)
        self.assertEqual(environment["source_commit"], self.fixture.commit)
        self.assertEqual(manifest["environment_sha256"], cost._bytes_sha(environment_raw))
        self.assertEqual(
            [target["path"] for target in manifest["targets"]],
            ["baseline_ucore/build/kernel", "build/kernel"],
        )
        for target in manifest["targets"]:
            artifact = self.fixture.root / Path(*target["path"].split("/"))
            self.assertEqual(target["sha256"], cost._file_sha(artifact))
            cost.parse_elf_identity(artifact)
        build_config = json.loads(
            (self.fixture.output / "kernel-build-config.json").read_text("utf-8")
        )
        build_log = json.loads(
            (self.fixture.output / "raw" / "kernel-build.log").read_text("utf-8")
        )
        self.assertIs(
            cost.validate_trusted_build_config(
                build_config, config, cost._bytes_sha(config_raw)
            ),
            build_config,
        )
        self.assertIs(
            cost.validate_trusted_build_log(build_log, build_config, manifest),
            build_log,
        )
        self.assertIs(
            cost.validate_trusted_build_environment(environment, build_config),
            environment,
        )
        self.assertEqual(build_config["environment_sha256"], build_log["environment_sha256"])
        self.assertEqual(len(build_log["commands"]), 5)
        self.assertTrue(all(item["returncode"] == 0 for item in build_log["commands"]))
        environments = [environment for _, environment in self.fixture.runner.calls]
        self.assertTrue(all(value == environments[0] for value in environments))
        status = _run(
            self.fixture.root, "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout
        self.assertEqual(status, b"")

    def test_commands_are_harness_owned_and_have_canonical_order(self) -> None:
        self.fixture.build()
        commands = [call[0] for call in self.fixture.runner.calls]
        make = str(self.fixture.make_tool.resolve())
        self.assertEqual(
            commands,
            [
                [make, "--version"],
                [make, "-C", "baseline_ucore", "clean"],
                [make, "-C", "baseline_ucore", "build/kernel"],
                [make, "clean"],
                [make, "build/kernel"],
            ],
        )

    def test_portable_receipts_can_be_rooted_at_the_evaluation_run(self) -> None:
        evidence_root = self.fixture.root / "results" / "portable-run"
        evidence_root.mkdir(parents=True)
        output = evidence_root / "kernel-build"
        result = builder.build_evidence(
            config_path=self.fixture.config,
            repository_root=self.fixture.root,
            make_tool=self.fixture.make_tool,
            run_id="kernel-fixture-1",
            output_dir=output,
            evidence_root=evidence_root,
            runner=self.fixture.runner,
        )
        manifest, _ = cost.load_build_manifest(
            output / "kernel-build.json", cost.load_config(self.fixture.config)[0]
        )
        self.assertEqual(result["environment_manifest"], "kernel-build/environment.json")
        self.assertEqual(result["build_manifest"], "kernel-build/kernel-build.json")
        self.assertEqual(
            manifest["build_config"]["path"], "kernel-build/kernel-build-config.json"
        )
        self.assertEqual(
            manifest["build_log"]["path"], "kernel-build/raw/kernel-build.log"
        )

    def test_dirty_or_untracked_source_is_rejected_before_execution(self) -> None:
        (self.fixture.root / "untracked.txt").write_text("no\n", encoding="utf-8")
        with self.assertRaisesRegex(builder.KernelBuildError, "must be clean"):
            self.fixture.build()
        self.assertEqual(self.fixture.runner.calls, [])
        self.assertFalse(self.fixture.output.exists())

    def test_nonzero_real_return_code_aborts_atomic_publication(self) -> None:
        self.fixture.runner.fail = ("baseline", "build")
        with self.assertRaisesRegex(builder.KernelBuildError, "return code 23"):
            self.fixture.build()
        self.assertFalse(self.fixture.output.exists())
        self.assertEqual(list(self.fixture.output.parent.glob(".kernel-run.tmp-*")), [])

    def test_source_mutation_during_build_is_fail_closed(self) -> None:
        self.fixture.runner.mutate_source = True
        with self.assertRaisesRegex(builder.KernelBuildError, "must be clean"):
            self.fixture.build()
        self.assertFalse(self.fixture.output.exists())

    def test_clean_must_actually_remove_prebuilt_artifact(self) -> None:
        artifact = self.fixture.root / "baseline_ucore" / "build" / "kernel"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(_valid_elf(b"stale"))
        self.fixture.runner.leave_stale = True
        with self.assertRaisesRegex(builder.KernelBuildError, "stale kernel"):
            self.fixture.build()
        self.assertFalse(self.fixture.output.exists())

    def test_later_build_cannot_change_the_already_measured_peer(self) -> None:
        self.fixture.runner.mutate_previous_artifact = True
        with self.assertRaisesRegex(builder.KernelBuildError, "changed after"):
            self.fixture.build()
        self.assertFalse(self.fixture.output.exists())

    def test_output_must_be_ignored_and_absent(self) -> None:
        with self.assertRaisesRegex(builder.KernelBuildError, "must be ignored"):
            builder.build_evidence(
                config_path=self.fixture.config,
                repository_root=self.fixture.root,
                make_tool=self.fixture.make_tool,
                run_id="kernel-fixture-1",
                output_dir=self.fixture.root / "published" / "run",
                runner=self.fixture.runner,
            )
        self.fixture.output.mkdir(parents=True)
        with self.assertRaisesRegex(builder.KernelBuildError, "already exists"):
            self.fixture.build()

    def test_duplicate_config_key_is_rejected(self) -> None:
        raw = self.fixture.config.read_text("utf-8")
        self.fixture.config.write_text(
            raw.replace('"schema_version": 1,', '"schema_version": 1,\n  "schema_version": 1,'),
            encoding="utf-8",
        )
        _run(self.fixture.root, "add", str(self.fixture.config.relative_to(self.fixture.root)))
        _run(self.fixture.root, "commit", "-q", "-m", "duplicate config")
        with self.assertRaises((cost.KernelCostError, ValueError)):
            self.fixture.build()
        self.assertEqual(self.fixture.runner.calls, [])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_link_backed_config_is_rejected(self) -> None:
        real = self.fixture.root / "ci" / "real.json"
        shutil.copyfile(self.fixture.config, real)
        try:
            self.fixture.config.unlink()
            os.symlink(real, self.fixture.config)
        except OSError as error:
            self.skipTest(f"cannot create symlink: {error}")
        with self.assertRaisesRegex(builder.KernelBuildError, "link-backed"):
            self.fixture.build()


if __name__ == "__main__":
    unittest.main(verbosity=2)
