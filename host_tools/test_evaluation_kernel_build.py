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
from unittest import mock

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


def _create_detectable_file_symlink(target: Path, link: Path) -> bool:
    """Create a real link even when MSYS2 defaults to its deep-copy fallback."""

    try:
        os.symlink(target, link)
    except (NotImplementedError, OSError):
        return False
    if link.is_symlink():
        return True

    # The default MSYS2 winsymlinks mode may report success after copying the
    # target.  Such a regular file cannot exercise the anti-link contract.
    # Retry in a child whose runtime is explicitly configured for MSYS links.
    if sys.platform != "cygwin":
        link.unlink(missing_ok=True)
        return False
    if not link.is_file():
        raise AssertionError(f"symlink fallback created an unexpected path: {link}")
    link.unlink()
    environment = os.environ.copy()
    environment["MSYS"] = "winsymlinks:sys"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys; os.symlink(sys.argv[1], sys.argv[2])",
            str(target),
            str(link),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )
    return result.returncode == 0 and link.is_symlink()


class FakeMake:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.fail: tuple[str, str] | None = None
        self.mutate_source = False
        self.recreate_stale_after_clean = False
        self.mutate_previous_artifact = False
        self.mutate_toolchain = False
        self.guardrail_rebuilds_treatment = False

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
            name = Path(command[0]).name
            version = (
                "GNU Make 4.4 fixture"
                if command[0] == str(Path(sys.executable).resolve())
                else f"{name} fixture version"
            )
            return builder.CommandExecution(0, (version + "\n").encode(), b"", 3)
        if command[-1] == "kernel-budget-check":
            if self.guardrail_rebuilds_treatment:
                artifact = self.root / "build" / "kernel"
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(_valid_elf(b"guardrail-profile"))
            return builder.CommandExecution(
                0,
                b"[kernel-budget] struct proc: actual=26448 bytes baseline=26448 bytes limit=27233 bytes\n",
                b"",
                5,
            )
        if command[-1] == "user-stack-check":
            return builder.CommandExecution(
                0,
                b"user stack call-path budget: apps=182 max=2944 (app:main) budget=3072 stack=4096 reserve=1024\n",
                b"",
                5,
            )
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
            if self.recreate_stale_after_clean:
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(_valid_elf(b"stale-after-clean"))
            else:
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
            if self.mutate_toolchain:
                Path(self.root / "tools" / "riscv64-unknown-elf-gcc").write_bytes(
                    b"mutated-gcc"
                )
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
        for relative in (
            "ci/kernel-budgets.json",
            "scripts/check-kernel-budgets.py",
            "scripts/probes/struct-proc-size.c",
            "scripts/check-user-stack-usage.py",
            "user_stack_policy.h",
            "user/Makefile",
        ):
            destination = self.root / Path(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        (self.root / ".gitignore").write_text(
            "/build/\n/baseline_ucore/build/\n/results/\n", encoding="utf-8"
        )
        _run(self.root, "init", "-q")
        _run(self.root, "config", "user.name", "Kernel Build Test")
        _run(self.root, "config", "user.email", "kernel-build@example.invalid")
        self.output = self.root / "results" / "evaluation" / "kernel-run"
        self.make_tool = Path(sys.executable)
        tool_stem = self.root / "tools" / "riscv64-unknown-elf-"
        tool_stem.parent.mkdir()
        self.toolprefix = str(tool_stem.resolve())
        for name in cost.TRUSTED_BUILD_TOOL_NAMES:
            Path(self.toolprefix + name).write_bytes(f"fixture-{name}".encode())
        _run(self.root, "add", ".")
        _run(self.root, "commit", "-q", "-m", "fixture")
        self.commit = _run(self.root, "rev-parse", "HEAD").stdout.decode().strip()
        self.runner = FakeMake(self.root)

    def close(self) -> None:
        self.temporary.cleanup()

    def build(self) -> dict[str, object]:
        return builder.build_evidence(
            config_path=self.config,
            repository_root=self.root,
            make_tool=self.make_tool,
            toolprefix=self.toolprefix,
            run_id="kernel-fixture-1",
            output_dir=self.output,
            runner=self.runner,
        )


class RepositoryLockTests(unittest.TestCase):
    def _repository(self, root: Path) -> None:
        root.mkdir()
        _run(root, "init", "-q")
        _run(root, "config", "user.name", "Kernel Lock Test")
        _run(root, "config", "user.email", "kernel-lock@example.invalid")
        (root / "tracked.txt").write_text("bound\n", encoding="utf-8")
        _run(root, "add", ".")
        _run(root, "commit", "-q", "-m", "fixture")

    def test_unicode_linked_worktree_lock_uses_real_common_directory(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="kernel-lock-中文-", dir=ROOT.parent
        ) as temporary:
            base = Path(temporary) / "源仓库"
            worktree = Path(temporary) / "验收工作树"
            self._repository(base)
            _run(base, "worktree", "add", "--detach", str(worktree), "HEAD")

            lock = builder._RepositoryLock(worktree)
            self.assertTrue(os.path.samefile(lock.path.parent, base / ".git"))
            with lock:
                self.assertTrue(lock.path.is_file())
            self.assertFalse(
                any(
                    "agentos-kernel-build.lock" in str(path)
                    for path in worktree.rglob("*")
                )
            )

    def test_linked_worktrees_share_one_nonblocking_build_lock(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kernel-lock-shared-") as temporary:
            base = Path(temporary) / "repository"
            first = Path(temporary) / "worktree-a"
            second = Path(temporary) / "worktree-b"
            self._repository(base)
            _run(base, "worktree", "add", "--detach", str(first), "HEAD")
            _run(base, "worktree", "add", "--detach", str(second), "HEAD")

            with builder._RepositoryLock(first):
                contender = builder._RepositoryLock(second)
                with self.assertRaisesRegex(
                    builder.KernelBuildError, "another trusted kernel build is active"
                ):
                    contender.__enter__()

    def test_linked_worktree_lock_rejects_tampered_gitfile_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kernel-lock-tamper-") as temporary:
            base = Path(temporary) / "repository"
            worktree = Path(temporary) / "worktree"
            self._repository(base)
            _run(base, "worktree", "add", "--detach", str(worktree), "HEAD")
            gitfile = worktree / ".git"
            original = worktree / ".git.original"
            gitfile.rename(original)
            try:
                gitfile.write_text(
                    f"gitdir: {(base / '.git').as_posix()}\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    builder.KernelBuildError, "Git administration is unsafe"
                ):
                    builder._RepositoryLock(worktree)
            finally:
                gitfile.unlink(missing_ok=True)
                original.rename(gitfile)


class TrustedKernelBuildTests(unittest.TestCase):
    def test_fixed_environment_preserves_msys_unicode_paths(self) -> None:
        with mock.patch.dict(builder.os.environ, {"TMPDIR": "/r/tmp"}, clear=True):
            for platform_name in ("cygwin", "msys"):
                with mock.patch.object(builder.sys, "platform", platform_name):
                    environment = builder._fixed_environment(
                        "1700000000", "/tools/riscv-"
                    )
                self.assertEqual(environment["LANG"], "C.UTF-8")
                self.assertEqual(environment["LC_ALL"], "C.UTF-8")
                self.assertEqual(environment["TMPDIR"], "/r/tmp")

        with mock.patch.dict(builder.os.environ, {}, clear=True), mock.patch.object(
            builder.sys, "platform", "linux"
        ):
            environment = builder._fixed_environment(
                "1700000000", "/tools/riscv-"
            )
        self.assertEqual(environment["LANG"], "C")
        self.assertEqual(environment["LC_ALL"], "C")
        self.assertNotIn("TMPDIR", environment)

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
        self.assertEqual(
            len(build_log["commands"]),
            1 + len(cost.TRUSTED_BUILD_TOOL_NAMES) + 4 + len(cost.GUARDRAIL_IDS),
        )
        self.assertTrue(all(item["returncode"] == 0 for item in build_log["commands"]))
        environments = [environment for _, environment in self.fixture.runner.calls]
        self.assertTrue(all(value == environments[0] for value in environments))
        expected_prefix = (
            Path(self.fixture.toolprefix).resolve().as_posix()
            if os.name == "nt"
            else str(Path(self.fixture.toolprefix).resolve())
        )
        self.assertEqual(environments[0]["TOOLPREFIX"], expected_prefix)
        status = _run(
            self.fixture.root, "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout
        self.assertEqual(status, b"")

    def test_commands_are_harness_owned_and_have_canonical_order(self) -> None:
        self.fixture.build()
        commands = [call[0] for call in self.fixture.runner.calls]
        make = str(self.fixture.make_tool.resolve())
        prefix = (
            Path(self.fixture.toolprefix).resolve().as_posix()
            if os.name == "nt"
            else str(Path(self.fixture.toolprefix).resolve())
        )
        tool_versions = [
            [
                (
                    Path(self.fixture.toolprefix + name).resolve().as_posix()
                    if os.name == "nt"
                    else str(Path(self.fixture.toolprefix + name).resolve())
                ),
                "--version",
            ]
            for name in cost.TRUSTED_BUILD_TOOL_NAMES
        ]
        self.assertEqual(
            commands,
            [
                [make, "--version"],
                *tool_versions,
                [make, f"TOOLPREFIX={prefix}", "kernel-budget-check"],
                [make, f"TOOLPREFIX={prefix}", "user-stack-check"],
                [make, "-C", "baseline_ucore", f"TOOLPREFIX={prefix}", "clean"],
                [make, "-C", "baseline_ucore", f"TOOLPREFIX={prefix}", "build/kernel"],
                [make, f"TOOLPREFIX={prefix}", "clean"],
                [make, f"TOOLPREFIX={prefix}", "build/kernel"],
            ],
        )

    def test_guardrail_rebuild_is_replaced_by_the_measured_treatment_build(self) -> None:
        self.fixture.runner.guardrail_rebuilds_treatment = True
        result = self.fixture.build()
        treatment = next(
            target for target in result["targets"] if target["id"] == "agentos"
        )
        artifact = self.fixture.root / "build" / "kernel"
        self.assertEqual(treatment["sha256"], cost._file_sha(artifact))
        self.assertNotIn(b"guardrail-profile", artifact.read_bytes())

    def test_portable_receipts_can_be_rooted_at_the_evaluation_run(self) -> None:
        evidence_root = self.fixture.root / "results" / "evaluation" / "portable-run"
        evidence_root.mkdir(parents=True)
        output = evidence_root / "kernel-build"
        result = builder.build_evidence(
            config_path=self.fixture.config,
            repository_root=self.fixture.root,
            make_tool=self.fixture.make_tool,
            toolprefix=self.fixture.toolprefix,
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
        with self.assertRaisesRegex(builder.KernelBuildError, "source gate"):
            self.fixture.build()
        self.assertEqual(self.fixture.runner.calls, [])
        self.assertFalse(self.fixture.output.exists())

    def test_staged_only_source_change_is_rejected_by_source_gate(self) -> None:
        source = self.fixture.root / "Makefile"
        original = source.read_bytes()
        source.write_bytes(original + b"# staged only\n")
        _run(self.fixture.root, "add", "--", "Makefile")
        source.write_bytes(original)
        with self.assertRaisesRegex(builder.KernelBuildError, "source gate"):
            self.fixture.build()
        self.assertEqual(self.fixture.runner.calls, [])
        self.assertFalse(self.fixture.output.exists())

    def test_info_exclude_cannot_hide_make_wildcard_source(self) -> None:
        exclude = self.fixture.root / ".git" / "info" / "exclude"
        exclude.write_text("os/hidden.c\nuser/src/hidden.c\n", encoding="ascii")
        for relative in ("os/hidden.c", "user/src/hidden.c"):
            hidden = self.fixture.root / relative
            hidden.parent.mkdir(parents=True, exist_ok=True)
            hidden.write_text("int hidden;\n", encoding="ascii")
            self.assertEqual(
                _run(
                    self.fixture.root,
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                ).stdout,
                b"",
            )
            with self.assertRaisesRegex(builder.KernelBuildError, "source gate"):
                self.fixture.build()
            self.assertEqual(self.fixture.runner.calls, [])
            hidden.unlink()

    def test_hidden_index_flags_cannot_mask_tampered_build_input(self) -> None:
        source = self.fixture.root / "Makefile"
        original = source.read_bytes()
        for enabled in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=enabled):
                _run(self.fixture.root, "update-index", enabled, "--", "Makefile")
                source.write_bytes(original + b"# hidden tamper\n")
                status = _run(
                    self.fixture.root,
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                ).stdout
                self.assertEqual(status, b"")
                with self.assertRaisesRegex(
                    builder.KernelBuildError, "source gate"
                ):
                    self.fixture.build()
                self.assertEqual(self.fixture.runner.calls, [])
                self.assertFalse(self.fixture.output.exists())
                source.write_bytes(original)
                _run(
                    self.fixture.root,
                    "update-index",
                    "--no-assume-unchanged",
                    "--no-skip-worktree",
                    "--",
                    "Makefile",
                )

    def test_nonzero_real_return_code_aborts_atomic_publication(self) -> None:
        self.fixture.runner.fail = ("baseline", "build")
        with self.assertRaisesRegex(builder.KernelBuildError, "return code 23"):
            self.fixture.build()
        self.assertFalse(self.fixture.output.exists())
        self.assertEqual(list(self.fixture.output.parent.glob(".kernel-run.tmp-*")), [])

    def test_source_mutation_during_build_is_fail_closed(self) -> None:
        self.fixture.runner.mutate_source = True
        with self.assertRaisesRegex(builder.KernelBuildError, "source gate"):
            self.fixture.build()
        self.assertFalse(self.fixture.output.exists())

    def test_clean_must_actually_remove_prebuilt_artifact(self) -> None:
        artifact = self.fixture.root / "baseline_ucore" / "build" / "kernel"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(_valid_elf(b"stale"))
        self.fixture.runner.recreate_stale_after_clean = True
        with self.assertRaisesRegex(builder.KernelBuildError, "stale kernel"):
            self.fixture.build()
        self.assertFalse(self.fixture.output.exists())

    def test_later_build_cannot_change_the_already_measured_peer(self) -> None:
        self.fixture.runner.mutate_previous_artifact = True
        with self.assertRaisesRegex(builder.KernelBuildError, "changed after"):
            self.fixture.build()
        self.assertFalse(self.fixture.output.exists())

    def test_toolchain_binary_mutation_during_build_is_fail_closed(self) -> None:
        self.fixture.runner.mutate_toolchain = True
        with self.assertRaisesRegex(builder.KernelBuildError, "toolchain gcc changed"):
            self.fixture.build()
        self.assertFalse(self.fixture.output.exists())

    def test_toolprefix_must_be_absolute_and_complete(self) -> None:
        with self.assertRaisesRegex(builder.KernelBuildError, "must be absolute"):
            builder.build_evidence(
                config_path=self.fixture.config,
                repository_root=self.fixture.root,
                make_tool=self.fixture.make_tool,
                toolprefix="riscv64-unknown-elf-",
                run_id="kernel-fixture-1",
                output_dir=self.fixture.output,
                runner=self.fixture.runner,
            )
        Path(self.fixture.toolprefix + "objdump").unlink()
        with self.assertRaisesRegex(builder.KernelBuildError, "exactly one objdump"):
            self.fixture.build()
        self.assertEqual(self.fixture.runner.calls, [])

    def test_output_must_be_ignored_and_absent(self) -> None:
        with self.assertRaisesRegex(builder.KernelBuildError, "must be ignored"):
            builder.build_evidence(
                config_path=self.fixture.config,
                repository_root=self.fixture.root,
                make_tool=self.fixture.make_tool,
                toolprefix=self.fixture.toolprefix,
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
        with tempfile.TemporaryDirectory(
            prefix="trusted-kernel-config-target-",
            dir=self.fixture.root.parent,
        ) as target_directory:
            real = Path(target_directory) / "evaluation-kernel-cost.json"
            shutil.copyfile(self.fixture.config, real)
            self.fixture.config.unlink()
            if not _create_detectable_file_symlink(real, self.fixture.config):
                self.skipTest("runtime cannot create a detectable file symlink")

            # Keep the anti-link assertion independent of the clean-worktree
            # assertion.  On hosts where Git records the mode change, commit
            # the link; on Windows/MSYS core.symlinks=false already treats the
            # byte-identical external target as clean.
            relative = str(self.fixture.config.relative_to(self.fixture.root))
            _run(self.fixture.root, "add", "--", relative)
            staged = subprocess.run(
                ["git", "-C", str(self.fixture.root), "diff", "--cached", "--quiet"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if staged.returncode == 1:
                _run(self.fixture.root, "commit", "-q", "-m", "link-backed config")
            elif staged.returncode != 0:
                self.fail(f"cannot inspect staged link: {staged.stderr.decode()}")
            status = _run(
                self.fixture.root,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ).stdout
            self.assertEqual(status, b"")

            with self.assertRaisesRegex(builder.KernelBuildError, "link-backed"):
                self.fixture.build()
            self.assertEqual(self.fixture.runner.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
