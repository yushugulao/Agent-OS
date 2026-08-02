#!/usr/bin/env python3
"""Regression tests for commit-bound Agent duration calibration evidence."""

import copy
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_test_calibration as calibration


ROOT = Path(__file__).resolve().parents[1]


class CalibrationTests(unittest.TestCase):
    @staticmethod
    def canonical_windows_fixture_environment():
        if sys.platform in {"cygwin", "msys"} or os.name == "nt":
            return {
                "ALLUSERSPROFILE": r"C:\ProgramData",
                "PROGRAMDATA": r"C:\ProgramData",
                "SYSTEMDRIVE": "C:",
            }
        return {}

    def git(self, root, *args):
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_source_checkout_requires_real_detached_clean_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.git(root, "init", "-q")
            self.git(root, "config", "user.email", "test@example.invalid")
            self.git(root, "config", "user.name", "Calibration Test")
            (root / "source.txt").write_text("source\n", encoding="utf-8")
            self.git(root, "add", "source.txt")
            self.git(root, "commit", "-q", "-m", "source")
            commit = self.git(root, "rev-parse", "HEAD")
            tree = self.git(root, "rev-parse", "HEAD^{tree}")
            self.git(root, "checkout", "-q", "--detach", commit)
            resolved, actual_tree = calibration.validate_source_checkout(
                root, commit
            )
            self.assertEqual(resolved, root.resolve())
            self.assertEqual(actual_tree, tree)
            self.assertEqual(
                calibration.verify_source_worktree_bytes(root, commit), 1
            )

            (root / "source.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(
                calibration.CalibrationError, "must be clean"
            ):
                calibration.validate_source_checkout(root, commit)
            self.git(root, "restore", "source.txt")

            self.git(root, "checkout", "-q", "master")
            with self.assertRaisesRegex(
                calibration.CalibrationError, "must be detached"
            ):
                calibration.validate_source_checkout(root, commit)
            with self.assertRaises(calibration.CalibrationError):
                calibration.validate_source_checkout(root, "f" * 40)

    def test_clean_filter_cannot_hide_tracked_byte_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.git(root, "init", "-q")
            self.git(root, "config", "user.email", "test@example.invalid")
            self.git(root, "config", "user.name", "Calibration Test")
            (root / ".gitattributes").write_text(
                "source.txt filter=rewrite\n", encoding="utf-8"
            )
            self.git(
                root,
                "config",
                "filter.rewrite.clean",
                "printf 'canonical\\n'",
            )
            self.git(root, "config", "filter.rewrite.smudge", "cat")
            source = root / "source.txt"
            source.write_text("worktree\n", encoding="utf-8")
            self.git(root, "add", "source.txt", ".gitattributes")
            self.git(root, "commit", "-q", "-m", "source")
            commit = self.git(root, "rev-parse", "HEAD")
            self.git(root, "checkout", "-q", "--detach", commit)
            self.assertEqual(
                self.git(root, "status", "--porcelain=v1", "--untracked-files=all"),
                "",
            )
            calibration.validate_source_checkout(root, commit)
            with self.assertRaisesRegex(
                calibration.CalibrationError, "bytes differ"
            ):
                calibration.verify_source_worktree_bytes(root, commit)

    @unittest.skipUnless(
        os.name == "posix" and sys.platform not in {"cygwin", "msys"},
        "native POSIX executable modes required",
    )
    def test_git_filemode_false_cannot_hide_executable_mode_mismatch(self):
        for committed_mode, changed_mode in ((0o755, 0o644), (0o644, 0o755)):
            with self.subTest(
                committed_mode=oct(committed_mode),
                changed_mode=oct(changed_mode),
            ), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.git(root, "init", "-q")
                self.git(root, "config", "user.email", "test@example.invalid")
                self.git(root, "config", "user.name", "Calibration Test")
                script = root / "source.sh"
                script.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
                script.chmod(committed_mode)
                self.git(root, "add", "source.sh")
                self.git(root, "commit", "-q", "-m", "source")
                commit = self.git(root, "rev-parse", "HEAD")
                self.git(root, "config", "core.filemode", "false")
                self.git(root, "checkout", "-q", "--detach", commit)
                script.chmod(changed_mode)
                self.assertEqual(
                    self.git(
                        root,
                        "status",
                        "--porcelain=v1",
                        "--untracked-files=all",
                    ),
                    "",
                )
                with self.assertRaisesRegex(
                    calibration.CalibrationError, "executable mode differs"
                ):
                    calibration.verify_source_worktree_bytes(root, commit)

    @unittest.skipUnless(
        sys.platform in {"cygwin", "msys"},
        "MSYS2/Cygwin mode emulation required",
    )
    def test_emulated_posix_mode_does_not_reject_committed_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.git(root, "init", "-q")
            self.git(root, "config", "user.email", "test@example.invalid")
            self.git(root, "config", "user.name", "Calibration Test")
            script = root / "source.sh"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
            self.git(root, "add", "source.sh")
            self.git(root, "update-index", "--chmod=+x", "source.sh")
            self.git(root, "commit", "-q", "-m", "source")
            commit = self.git(root, "rev-parse", "HEAD")
            self.git(root, "checkout", "-q", "--detach", commit)
            self.assertTrue(
                self.git(root, "ls-tree", "HEAD", "source.sh").startswith(
                    "100755 blob "
                )
            )
            self.assertEqual(
                calibration.verify_source_worktree_bytes(root, commit), 1
            )

    def test_git_repository_redirection_environment_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.git(root, "init", "-q")
            self.git(root, "config", "user.email", "test@example.invalid")
            self.git(root, "config", "user.name", "Calibration Test")
            (root / "source.txt").write_text("source\n", encoding="utf-8")
            self.git(root, "add", "source.txt")
            self.git(root, "commit", "-q", "-m", "source")
            commit = self.git(root, "rev-parse", "HEAD")
            self.git(root, "checkout", "-q", "--detach", commit)
            original = os.environ.get("GIT_DIR")
            os.environ["GIT_DIR"] = str(root / ".git")
            try:
                with self.assertRaisesRegex(
                    calibration.CalibrationError, "redirection environment"
                ):
                    calibration.validate_source_checkout(root, commit)
            finally:
                if original is None:
                    os.environ.pop("GIT_DIR", None)
                else:
                    os.environ["GIT_DIR"] = original

    def test_calibration_child_environment_drops_build_injection(self):
        poisoned = {
            "AGENT_METADATA_CRASH_PHASE": "after-header",
            "BASH_ENV": "/tmp/poison-bash-env",
            "CFLAGS": "-DPOISON",
            "GCC_EXEC_PREFIX": "/tmp/poison-gcc",
            "LOG": "trace",
            "MAKEFLAGS": "-j99",
            "MAKEFILES": "/tmp/poison.mk",
            "PYTHONPATH": "/tmp/poison-python",
        }
        with mock.patch.dict(
            os.environ,
            {**self.canonical_windows_fixture_environment(), **poisoned},
            clear=False,
        ):
            environment = calibration.calibration_child_environment()
        for name in poisoned:
            self.assertNotIn(name, environment)
        self.assertEqual(environment["LC_ALL"], "C")
        self.assertEqual(environment["PYTHONHASHSEED"], "0")
        self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
        self.assertEqual(environment["TMPDIR"], "/tmp")

    def test_calibration_runner_python_cannot_pollute_source_checkout(self):
        runner = (ROOT / "scripts" / "run-agent-tests.sh").read_text(
            encoding="utf-8"
        )
        collector = (ROOT / "scripts" / "agent_test_calibration.py").read_text(
            encoding="utf-8"
        )
        invocations = [
            line for line in runner.splitlines() if '"${PYTHON_BIN}"' in line
        ]
        self.assertEqual(len(invocations), 13)
        self.assertTrue(
            all('"${PYTHON_BIN}" -I -S -B' in line for line in invocations)
        )
        self.assertIn("loaded_msys_path_api", collector)
        self.assertNotRegex(
            collector, r'ctypes\.CDLL\(["\'](?:msys-2\.0|cygwin1)\.dll'
        )

    def test_collector_dynamic_import_cannot_write_bytecode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            scripts = root / "scripts"
            ci = root / "ci"
            scripts.mkdir(parents=True)
            ci.mkdir()
            shutil.copy2(
                ROOT / "scripts" / "agent_test_calibration.py",
                scripts / "agent_test_calibration.py",
            )
            (scripts / "check-kernel-budgets.py").write_text(
                "AGENT_TEST_CALIBRATION_PROVISIONAL = "
                "'provisional_requires_full_suite'\n"
                "def load_config(path):\n"
                "    return {'agent_test_suite': {\n"
                "        'calibration_status': "
                "AGENT_TEST_CALIBRATION_PROVISIONAL,\n"
                "        'local_calibration_profile': {},\n"
                "        'expected_cases': [],\n"
                "    }}\n"
                "def agent_test_source_fingerprint(root, config):\n"
                "    return ('0' * 64, ())\n",
                encoding="ascii",
            )
            (ci / "kernel-budgets.json").write_text("{}\n", encoding="ascii")
            self.git(root, "init", "-q")
            self.git(root, "config", "user.email", "test@example.invalid")
            self.git(root, "config", "user.name", "Calibration Test")
            self.git(root, "add", ".")
            self.git(root, "commit", "-q", "-m", "source")
            commit = self.git(root, "rev-parse", "HEAD")
            self.git(root, "checkout", "-q", "--detach", commit)

            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "scripts/agent_test_calibration.py",
                    "collect",
                    "--root",
                    ".",
                    "--source-commit",
                    commit,
                    "--output",
                    str(Path(temp) / "output"),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(root.rglob("*.pyc")), [])
            self.assertFalse((scripts / "__pycache__").exists())

    def test_msys_python_identity_uses_the_native_executable_path(self):
        with tempfile.TemporaryDirectory() as temp:
            alias = Path(temp) / "python3"
            native = Path(temp) / "python3.exe"
            alias.write_bytes(b"same-python")
            native.write_bytes(b"same-python")
            with mock.patch.object(calibration.sys, "platform", "cygwin"), \
                    mock.patch.object(calibration.sys, "executable", str(alias)):
                self.assertEqual(
                    calibration.canonical_python_executable(),
                    str(native.resolve()),
                )
                native.write_bytes(b"different-python")
                with self.assertRaisesRegex(
                    calibration.CalibrationError, "aliases differ"
                ):
                    calibration.canonical_python_executable()

    def test_calibration_child_environment_preserves_windows_system_paths(self):
        windows_paths = {
            "ALLUSERSPROFILE": r"C:\ProgramData",
            "PROGRAMDATA": r"C:\ProgramData",
            "SYSTEMDRIVE": "C:",
        }
        with mock.patch.object(calibration.sys, "platform", "msys"), mock.patch.dict(
            os.environ, windows_paths, clear=False
        ):
            environment = calibration.calibration_child_environment()
        for name, value in windows_paths.items():
            self.assertEqual(environment[name], value)

    def test_calibration_child_environment_rejects_windows_placeholders(self):
        windows_paths = {
            "ALLUSERSPROFILE": r"%SystemDrive%\ProgramData",
            "PROGRAMDATA": r"%SystemDrive%\ProgramData",
            "SYSTEMDRIVE": "C:",
        }
        with mock.patch.object(calibration.sys, "platform", "msys"), mock.patch.dict(
            os.environ, windows_paths, clear=False
        ), self.assertRaisesRegex(
            calibration.CalibrationError, "canonical system paths"
        ):
            calibration.calibration_child_environment()

    def test_calibration_child_python_does_not_execute_user_site_pth(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            site_packages = (
                home
                / ".local"
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages"
            )
            site_packages.mkdir(parents=True)
            marker = Path(temp) / "user-site-ran"
            (site_packages / "poison.pth").write_text(
                "import pathlib; pathlib.Path("
                + repr(str(marker))
                + ").write_text('ran')\n",
                encoding="ascii",
            )
            with mock.patch.dict(
                os.environ, self.canonical_windows_fixture_environment(), clear=False
            ):
                environment = calibration.calibration_child_environment()
            environment["HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-c", "import site"],
                env=environment,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())

    def test_ignored_untracked_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.git(root, "init", "-q")
            self.git(root, "config", "user.email", "test@example.invalid")
            self.git(root, "config", "user.name", "Calibration Test")
            source = root / "source.c"
            source.write_text("int source;\n", encoding="ascii")
            self.git(root, "add", "source.c")
            self.git(root, "commit", "-q", "-m", "source")
            commit = self.git(root, "rev-parse", "HEAD")
            self.git(root, "checkout", "-q", "--detach", commit)
            info_exclude = root / ".git" / "info" / "exclude"
            info_exclude.write_text("os/hidden.c\n", encoding="ascii")
            hidden = root / "os" / "hidden.c"
            hidden.parent.mkdir()
            hidden.write_text("int hidden;\n", encoding="ascii")
            self.assertEqual(
                self.git(root, "status", "--porcelain=v1", "--untracked-files=all"),
                "",
            )
            calibration.validate_source_checkout(root, commit)
            calibration.verify_source_worktree_bytes(root, commit)
            with self.assertRaisesRegex(
                calibration.CalibrationError, "untracked or ignored"
            ):
                calibration.verify_no_untracked_worktree_entries(root)
            with self.assertRaisesRegex(
                calibration.CalibrationError, "untracked or ignored"
            ):
                calibration.verify_no_untracked_worktree_entries(
                    root, allow_generated=True
                )

    def test_only_declared_generated_untracked_entries_are_admitted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.git(root, "init", "-q")
            self.git(root, "config", "user.email", "test@example.invalid")
            self.git(root, "config", "user.name", "Calibration Test")
            source = root / "source.c"
            source.write_text("int source;\n", encoding="ascii")
            (root / ".gitignore").write_text(
                "/build/\n/nfs/*.img\n", encoding="ascii"
            )
            self.git(root, "add", "source.c", ".gitignore")
            self.git(root, "commit", "-q", "-m", "source")
            commit = self.git(root, "rev-parse", "HEAD")
            self.git(root, "checkout", "-q", "--detach", commit)
            output = root / "build" / "object.o"
            output.parent.mkdir()
            output.write_bytes(b"object")
            image = root / "nfs" / "fs.img"
            image.parent.mkdir()
            image.write_bytes(b"image")
            self.assertEqual(
                calibration.verify_no_untracked_worktree_entries(
                    root, allow_generated=True
                ),
                2,
            )
            with self.assertRaisesRegex(
                calibration.CalibrationError, "untracked or ignored"
            ):
                calibration.verify_no_untracked_worktree_entries(root)
            unexpected = root / "empty-untracked-directory"
            unexpected.mkdir()
            with self.assertRaisesRegex(
                calibration.CalibrationError, "untracked or ignored"
            ):
                calibration.verify_no_untracked_worktree_entries(
                    root, allow_generated=True
                )

    def test_windows_equivalent_git_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.git(root, "init", "-q")
            self.git(root, "config", "user.email", "test@example.invalid")
            self.git(root, "config", "user.name", "Calibration Test")
            payload = root / "payload"
            payload.write_text("same bytes\n", encoding="ascii")
            blob = self.git(root, "hash-object", "-w", "payload")
            payload.unlink()
            self.git(
                root,
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{blob},Foo",
            )
            self.git(
                root,
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{blob},foo",
            )
            self.git(root, "commit", "-q", "-m", "colliding source")
            commit = self.git(root, "rev-parse", "HEAD")
            self.git(root, "checkout", "-q", "-f", "--detach", commit)
            self.assertEqual(
                self.git(root, "status", "--porcelain=v1", "--untracked-files=all"),
                "",
            )
            calibration.validate_source_checkout(root, commit)
            with self.assertRaisesRegex(
                calibration.CalibrationError, "Windows-equivalent"
            ):
                calibration.verify_source_worktree_bytes(root, commit)

    def test_serialized_paths_have_platform_independent_canonical_form(self):
        self.assertEqual(
            calibration.canonical_repo_relative("a/b", "fixture").as_posix(),
            "a/b",
        )
        for value in (
            "/abs",
            "//server/share",
            "C:/abs",
            "a//b",
            "a/./b",
            "a/../b",
            "a\\..\\b",
        ):
            with self.subTest(value=value), self.assertRaises(
                calibration.CalibrationError
            ):
                calibration.canonical_repo_relative(value, "fixture")

    def test_duration_profile_values_fail_closed_before_execution(self):
        root = Path(__file__).resolve().parents[1]
        bash = shutil.which("bash")
        self.assertIsNotNone(bash)
        self.assertTrue(Path(bash).is_file())
        for profile, calibrate, message in (
            ("typo", "0", "must be local-e3 or none"),
            ("none", "1", "requires the local-e3 duration profile"),
        ):
            environment = os.environ.copy()
            environment.update(
                {
                    "AGENT_TEST_DURATION_PROFILE": profile,
                    "AGENT_TEST_CALIBRATE": calibrate,
                }
            )
            result = subprocess.run(
                [bash, "scripts/run-agent-tests.sh"],
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                timeout=15,
            )
            with self.subTest(profile=profile, calibrate=calibrate):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message.encode("ascii"), result.stderr)

    def identity(self, name, byte):
        return {
            "requested_path": f"/tools/{name}",
            "executable": {
                "path": f"/tools/{name}",
                "bytes": 1,
                "sha256": byte * 64,
            },
            "version_argv": [f"/tools/{name}", "--version"],
            "version_first_line": f"{name} version 1",
        }

    def make_fixture(self, root):
        scripts = root / "scripts"
        scripts.mkdir()
        runner = scripts / "agent_test_runner.py"
        runner.write_text("# fixture runner\n", encoding="utf-8")
        _, runner_identity = calibration.regular_file(runner, "fixture runner")
        tools = {
            "qemu": self.identity("qemu", "1"),
            "toolchain_cc": self.identity("gcc", "2"),
            "toolchain_ld": self.identity("ld", "7"),
            "toolchain_objcopy": self.identity("objcopy", "8"),
            "toolchain_objdump": self.identity("objdump", "a"),
            "toolchain_as": self.identity("as", "9"),
            "host_cc": self.identity("cc", "b"),
            "python": self.identity("python", "3"),
            "bash": self.identity("bash", "4"),
            "make": self.identity("make", "5"),
            "git": self.identity("git", "6"),
        }
        plan = {
            "schema": 1,
            "purpose": calibration.CAMPAIGN_PURPOSE,
            "evidence_scope": calibration.EVIDENCE_SCOPE,
            "remote_ci_attestation": False,
            "source": {
                "commit": "a" * 40,
                "tree": "b" * 40,
                "fingerprint_sha256": "c" * 64,
                "fingerprint_inputs": 7,
            },
            "campaign_nonce": "0" * 64,
            "rounds": [
                {"round": 1, "round_nonce": "1" * 64},
                {"round": 2, "round_nonce": "2" * 64},
                {"round": 3, "round_nonce": "3" * 64},
            ],
            "expected_cases": ["case_one"],
            "attestation_case_count": 2,
            "executables": tools,
            "created_utc": "2026-07-31T00:00:00Z",
        }
        plan_path = root / "plan.json"
        plan_data = calibration.canonical_json_bytes(plan)
        plan_path.write_bytes(plan_data)
        plan_sha = hashlib.sha256(plan_data).hexdigest()
        attestation_dir = root / "attestations" / "01"
        attestation_dir.mkdir(parents=True)
        guest_path = root / "guest.log"
        guest_parts = []
        cases = calibration.expected_attestation_cases(plan["expected_cases"])
        for _, tag, init_proc in cases:
            raw = f"{init_proc}: parent passed\n".encode()
            guest_parts.extend(
                (
                    f"===== guest:{tag} =====\n".encode(),
                    raw,
                    f"\n===== end-guest:{tag} =====\n".encode(),
                )
            )
        guest_path.write_bytes(b"".join(guest_parts))
        sections = calibration.parse_guest_sections(
            guest_path, plan["expected_cases"]
        )
        for index, (case_key, tag, init_proc) in enumerate(cases):
            session_nonce = f"{4 + index * 2:x}" * 64
            execution_nonce = f"{5 + index * 2:x}" * 64
            start = 1_000_000_000 + index * 2_000_000_000
            finish = start + 1_000_000_000
            log_path = root / f"{case_key}.guest.log"
            log_path.write_bytes(sections[tag])
            log_identity = {
                "path": str(log_path.resolve()),
                "bytes": len(sections[tag]),
                "sha256": hashlib.sha256(sections[tag]).hexdigest(),
            }
            kernel_identity = {
                "path": str((root / "build" / "kernel").resolve()),
                "bytes": 1,
                "sha256": "d" * 64,
            }
            image_input_identity = {
                "path": str((root / "nfs" / "fs-copy.img").resolve()),
                "bytes": 4096,
                "sha256": "e" * 64,
            }
            image_output_identity = {
                **image_input_identity,
                "sha256": "f" * 64,
            }
            attestation_path = attestation_dir / f"{case_key}.json"
            argv = [
                tools["python"]["executable"]["path"],
                "scripts/agent_test_runner.py",
                "--init-proc",
                init_proc,
                "--marker",
                f"{init_proc}: parent passed",
                "--marker-mode",
                "exact-line",
                "--expected-bad-addr-marker-mode",
                "exact-line",
                "--log-file",
                str(log_path),
                "--case-timeout",
                "300s",
                "--idle-notice-seconds",
                "20",
                "--marker-grace-seconds",
                "2s",
                "--qemu",
                tools["qemu"]["requested_path"],
                "--attestation-file",
                str(attestation_path),
                "--run-id",
                session_nonce,
                "--execution-id",
                execution_nonce,
                "--evidence-scope",
                calibration.EVIDENCE_SCOPE,
                "--source-commit",
                plan["source"]["commit"],
                "--source-tree",
                plan["source"]["tree"],
                "--campaign-nonce",
                plan["campaign_nonce"],
                "--calibration-plan-sha256",
                plan_sha,
                "--round-nonce",
                plan["rounds"][0]["round_nonce"],
                "--session-nonce",
                session_nonce,
                "--execution-nonce",
                execution_nonce,
                "--toolchain-cc",
                tools["toolchain_cc"]["requested_path"],
            ]
            attestation = {
                "schema_version": 2,
                "format": calibration.ATTESTATION_FORMAT,
                "evidence_scope": calibration.EVIDENCE_SCOPE,
                "source": {
                    "commit": plan["source"]["commit"],
                    "tree": plan["source"]["tree"],
                    "calibration_plan_sha256": plan_sha,
                },
                "identity": {
                    "campaign_nonce": plan["campaign_nonce"],
                    "round_nonce": plan["rounds"][0]["round_nonce"],
                    "session_nonce": session_nonce,
                    "execution_nonce": execution_nonce,
                },
                "runner": runner_identity,
                "executables": {
                    "qemu": tools["qemu"],
                    "toolchain_cc": tools["toolchain_cc"],
                    "python": tools["python"],
                },
                "invocation_argv": argv,
                "qemu_argv": [
                    tools["qemu"]["requested_path"],
                    "-nographic",
                    "-machine",
                    "virt",
                    "-bios",
                    "default",
                    "-kernel",
                    "build/kernel",
                    "-drive",
                    "file=nfs/fs-copy.img,if=none,format=raw,id=x0",
                    "-device",
                    "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
                ],
                "request": {
                    "init_proc": init_proc,
                    "marker": f"{init_proc}: parent passed",
                    "marker_mode": "exact-line",
                    "expected_bad_addr_markers": [],
                    "expected_fault_marker_mode": "exact-line",
                    "completion_mode": "natural",
                    "case_timeout_seconds": 300,
                    "idle_notice_seconds": 20,
                    "marker_grace_seconds": 2,
                },
                "inputs": {
                    "kernel": kernel_identity,
                    "image": image_input_identity,
                },
                "outputs": {
                    "kernel": kernel_identity,
                    "image": image_output_identity,
                    "log": log_identity,
                },
                "time": {
                    "clock": "time.monotonic_ns",
                    "started_monotonic_ns": start,
                    "finished_monotonic_ns": finish,
                    "elapsed_ns": finish - start,
                    "started_wall_time_ns": start + 10_000_000_000,
                    "finished_wall_time_ns": finish + 10_000_000_000,
                },
                "result": {
                    "succeeded": True,
                    "reason": "process_exit",
                    "returncode": 0,
                    "supervisor_returncode": None,
                    "signals_sent": [],
                    "output_eof": True,
                    "expected_faults_satisfied": True,
                    "process_tree_gone": True,
                    "process_tree_contained": False,
                    "completion_signal_attested": False,
                    "control_endpoint_restored": True,
                    "supervisor_control_healthy": True,
                    "elapsed_seconds": 1.0,
                },
                "run_id": session_nonce,
                "execution_id": execution_nonce,
            }
            attestation_path.write_bytes(
                calibration.canonical_json_bytes(attestation)
            )
        return plan_path, attestation_dir, guest_path

    def mutate_attestation(self, path, callback):
        value = json.loads(path.read_text(encoding="utf-8"))
        callback(value)
        path.write_bytes(calibration.canonical_json_bytes(value))

    def build_package_fixture(self, root):
        plan_path, template_dir, guest_path = self.make_fixture(root)
        self.git(root, "init", "-q")
        self.git(root, "config", "user.email", "test@example.invalid")
        self.git(root, "config", "user.name", "Calibration Test")
        self.git(root, "add", ".")
        self.git(root, "commit", "-q", "-m", "calibration source")
        commit = self.git(root, "rev-parse", "HEAD")
        tree = self.git(root, "rev-parse", "HEAD^{tree}")

        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["source"].update({"commit": commit, "tree": tree})
        package = root / "evidence" / "calibrations" / commit[:12]
        package.mkdir(parents=True)
        package_plan = package / "plan.json"
        plan_data = calibration.canonical_json_bytes(plan)
        package_plan.write_bytes(plan_data)
        plan_sha = hashlib.sha256(plan_data).hexdigest()
        guest_raw = guest_path.read_bytes()
        template_attestations = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in template_dir.glob("*.json")
        }

        def descriptor(path, raw=None):
            data = path.read_bytes()
            result = {
                "path": path.relative_to(package).as_posix(),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            if raw is not None:
                result.update(
                    {
                        "raw_bytes": len(raw),
                        "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
            return result

        rounds = []
        for round_number in range(1, 4):
            ordinal = f"{round_number:02d}"
            round_dir = package / "attestations" / ordinal
            round_dir.mkdir(parents=True)
            attestation_descriptors = []
            attestation_hashes = []
            for case_index, (name, template) in enumerate(
                sorted(template_attestations.items())
            ):
                value = copy.deepcopy(template)
                nonce_base = 4 + (round_number - 1) * 4 + case_index * 2
                session_nonce = f"{nonce_base:x}" * 64
                execution_nonce = f"{nonce_base + 1:x}" * 64
                start = round_number * 10_000_000_000 + case_index * 2_000_000_000
                finish = start + 1_000_000_000
                target = round_dir / name
                declared_target = (
                    root
                    / f".{package.name}.partial-fixture"
                    / "attestations"
                    / ordinal
                    / name
                )
                value["source"].update(
                    {
                        "commit": commit,
                        "tree": tree,
                        "calibration_plan_sha256": plan_sha,
                    }
                )
                value["identity"].update(
                    {
                        "round_nonce": plan["rounds"][round_number - 1][
                            "round_nonce"
                        ],
                        "session_nonce": session_nonce,
                        "execution_nonce": execution_nonce,
                    }
                )
                value["run_id"] = session_nonce
                value["execution_id"] = execution_nonce
                value["time"].update(
                    {
                        "started_monotonic_ns": start,
                        "finished_monotonic_ns": finish,
                        "elapsed_ns": finish - start,
                        "started_wall_time_ns": start + 100_000_000_000,
                        "finished_wall_time_ns": finish + 100_000_000_000,
                    }
                )
                argv = value["invocation_argv"]
                replacements = {
                    "--source-commit": commit,
                    "--source-tree": tree,
                    "--calibration-plan-sha256": plan_sha,
                    "--round-nonce": value["identity"]["round_nonce"],
                    "--run-id": session_nonce,
                    "--session-nonce": session_nonce,
                    "--execution-id": execution_nonce,
                    "--execution-nonce": execution_nonce,
                    "--attestation-file": str(declared_target),
                }
                for option, replacement in replacements.items():
                    argv[argv.index(option) + 1] = replacement
                target.write_bytes(calibration.canonical_json_bytes(value))
                data = target.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                attestation_hashes.append(digest)
                attestation_descriptors.append(
                    {
                        "case_key": name.removesuffix(".json"),
                        "init_proc": value["request"]["init_proc"],
                        "path": target.relative_to(package).as_posix(),
                        "bytes": len(data),
                        "sha256": digest,
                    }
                )

            timing = package / f"{ordinal}.timing"
            timing.write_text("case_one 1.000000000\n", encoding="ascii")
            runner_raw = (
                "[agent-calibration] source-check: tracked=123 untracked=0\n"
                "[agent-tests] agentfinal_ucore passed\n"
                "[agent-tests] case_one passed\n"
                "[kernel-budget] Agent calibration source/contract: "
                "sha256="
                + plan["source"]["fingerprint_sha256"]
                + ", inputs=7\n"
                "[agent-calibration] attestation-derived timing: "
                f"round={ordinal} total=1.000000000\n"
                "[agent-tests] all Agent-OS uCore checks passed\n"
            ).encode("utf-8")
            runner_log = package / f"{ordinal}.runner.log.gz"
            guest_log = package / f"{ordinal}.guest.log.gz"
            runner_log.write_bytes(gzip.compress(runner_raw, compresslevel=9, mtime=0))
            guest_log.write_bytes(gzip.compress(guest_raw, compresslevel=9, mtime=0))
            first_start = round_number * 10_000_000_000
            last_finish = first_start + 3_000_000_000
            rounds.append(
                {
                    "round": round_number,
                    "sample_id": f"agent18-{commit[:12]}-{ordinal}",
                    "round_nonce": plan["rounds"][round_number - 1]["round_nonce"],
                    "started_utc": f"2026-07-31T00:00:{round_number * 2 - 1:02d}Z",
                    "completed_utc": f"2026-07-31T00:00:{round_number * 2:02d}Z",
                    "started_monotonic_ns": first_start - 1_000_000_000,
                    "finished_monotonic_ns": last_finish + 1_000_000_000,
                    "started_wall_time_ns": first_start + 99_000_000_000,
                    "finished_wall_time_ns": last_finish + 101_000_000_000,
                    "exit_status": 0,
                    "total_seconds": 1.0,
                    "timing_rows": 1,
                    "attestation_digest_sha256": hashlib.sha256(
                        "\n".join(attestation_hashes).encode("ascii")
                    ).hexdigest(),
                    "timing": descriptor(timing),
                    "runner_log": descriptor(runner_log, runner_raw),
                    "guest_log": descriptor(guest_log, guest_raw),
                    "attestations": attestation_descriptors,
                }
            )

        source = plan["source"]
        environment = {
            "schema": 1,
            "evidence_scope": calibration.EVIDENCE_SCOPE,
            "remote_ci_attestation": False,
            "source": source,
            "host": {
                "platform": "fixture",
                "machine": "fixture",
                "python_runtime": "fixture",
            },
            "executables": plan["executables"],
            "captured_before_and_after": True,
        }
        (package / "environment.json").write_bytes(
            calibration.canonical_json_bytes(environment)
        )
        session = {
            "schema": 1,
            "evidence_scope": calibration.EVIDENCE_SCOPE,
            "remote_ci_attestation": False,
            "campaign_nonce": plan["campaign_nonce"],
            "plan_sha256": plan_sha,
            "source": source,
            "started_utc": "2026-07-31T00:00:00Z",
            "completed_utc": "2026-07-31T00:00:07Z",
            "started_monotonic_ns": 1,
            "finished_monotonic_ns": 40_000_000_000,
            "started_wall_time_ns": 1,
            "finished_wall_time_ns": 140_000_000_000,
            "serialized": True,
            "predeclared_round_count": 3,
            "rounds": rounds,
        }
        (package / "session.json").write_bytes(
            calibration.canonical_json_bytes(session)
        )
        validation = {
            "schema": 1,
            "status": "reviewed_local_e3",
            "evidence_scope": calibration.EVIDENCE_SCOPE,
            "remote_ci_attestation": False,
            "source": source,
            "sample_count": 3,
            "attestation_count": 6,
            "baseline_seconds": 1.0,
            "max_seconds": 1.05,
            "max_to_median_ratio": 1.05,
            "all_exit_status_zero": True,
            "serialized": True,
        }
        (package / "validation.json").write_bytes(
            calibration.canonical_json_bytes(validation)
        )
        manifest = {
            "schema": 3,
            "purpose": calibration.CAMPAIGN_PURPOSE,
            "evidence_scope": calibration.EVIDENCE_SCOPE,
            "remote_ci_attestation": False,
            "source": source,
            "expected_cases": plan["expected_cases"],
            "collection": {
                "detached_clean_worktree": True,
                "serialized": True,
                "predeclared_sample_count": 3,
                "started_utc": session["started_utc"],
                "completed_utc": session["completed_utc"],
            },
            "result": {
                "status": "reviewed_local_e3",
                "baseline_seconds": 1.0,
                "max_seconds": 1.05,
                "max_to_median_ratio": 1.05,
                "limit_policy": "ceil(max(max_observed, median * 1.05) * 1000) / 1000",
                "attestation_count": 6,
            },
            "plan": descriptor(package_plan),
            "environment": descriptor(package / "environment.json"),
            "session": descriptor(package / "session.json"),
            "validation": descriptor(package / "validation.json"),
            "rounds": rounds,
            "review_boundary": (
                "Local E3 reproduction evidence only; it is unsigned and is not "
                "a GitLab Runner, CI, E4 attestation, or proof of operator honesty."
            ),
        }
        manifest_path = package / "manifest.json"
        manifest_path.write_bytes(calibration.canonical_json_bytes(manifest))
        tests = {
            "expected_cases": ["case_one"],
            "calibration_source_commit": commit,
            "calibration_source_tree": tree,
            "source_fingerprint_sha256": source["fingerprint_sha256"],
            "calibration_manifest_file": manifest_path.relative_to(root).as_posix(),
            "calibration_manifest_sha256": hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
            "baseline_seconds": 1.0,
            "max_seconds": 1.05,
            "calibration_samples": [
                {
                    "sample_id": record["sample_id"],
                    "total_seconds": 1.0,
                    "timing_file": (
                        f"evidence/calibrations/{commit[:12]}/{record['round']:02d}.timing"
                    ),
                    "timing_file_sha256": record["timing"]["sha256"],
                    "attestation_digest_sha256": record[
                        "attestation_digest_sha256"
                    ],
                }
                for record in rounds
            ],
        }
        return tests, package

    def test_timing_is_rebuilt_only_from_complete_attestations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan, attestations, guest = self.make_fixture(root)
            timing = root / "derived.timing"
            total = calibration.derive_round_timing(
                root,
                plan,
                1,
                attestations,
                guest,
                timing,
                production=False,
            )
            self.assertEqual(total, 1.0)
            self.assertEqual(timing.read_text(), "case_one 1.000000000\n")
            with self.assertRaisesRegex(
                calibration.CalibrationError, "already exists"
            ):
                calibration.derive_round_timing(
                    root,
                    plan,
                    1,
                    attestations,
                    guest,
                    timing,
                    production=False,
                )

    def test_schema_three_package_is_replayed_and_content_addressed(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source_root = base / "source"
            source_root.mkdir()
            tests, source_package = self.build_package_fixture(source_root)
            first_attestation = json.loads(
                (
                    source_package
                    / "attestations"
                    / "01"
                    / "00-context-sync-atomicity.json"
                ).read_text(encoding="utf-8")
            )
            declared_path = first_attestation["invocation_argv"][
                first_attestation["invocation_argv"].index(
                    "--attestation-file"
                )
                + 1
            ]
            self.assertIn(".partial-fixture", declared_path)
            self.assertNotEqual(
                Path(declared_path).parent.parent.parent.resolve(),
                source_package.resolve(),
            )
            root = base / "relocated"
            shutil.copytree(source_root, root)
            package = root / source_package.relative_to(source_root)
            shutil.rmtree(source_root)
            result = calibration.verify_calibration_package(root, tests, 7)
            self.assertEqual(result["sample_count"], 3)
            self.assertEqual(result["attestation_count"], 6)
            self.assertEqual(result["evidence_scope"], "local_e3_unsigned")

            (package / "01.timing").write_text(
                "case_one 9.000000000\n", encoding="ascii"
            )
            with self.assertRaisesRegex(
                calibration.CalibrationError, "descriptor mismatch"
            ):
                calibration.verify_calibration_package(root, tests, 7)

    def test_attestation_mutations_fail_closed(self):
        mutations = {
            "forged scope": lambda value: value.update(
                {"evidence_scope": "ci_e4_signed"}
            ),
            "formula elapsed": lambda value: value["result"].update(
                {"elapsed_seconds": 7.5}
            ),
            "broken monotonic": lambda value: value["time"].update(
                {"elapsed_ns": 5}
            ),
            "wrong Guest hash": lambda value: value["outputs"]["log"].update(
                {"sha256": "f" * 64}
            ),
            "timing side channel": lambda value: value["invocation_argv"].extend(
                ["--timing-file", "/tmp/forged.timing"]
            ),
            "tool identity": lambda value: value["executables"]["qemu"][
                "executable"
            ].update({"sha256": "0" * 64}),
            "QEMU image argv": lambda value: value["qemu_argv"].__setitem__(
                9, "file=/tmp/other.img,if=none,format=raw,id=x0"
            ),
            "image path changed": lambda value: value["outputs"]["image"].update(
                {"path": "/tmp/other.img"}
            ),
            "runner wrong suffix": lambda value: value["runner"].update(
                {"path": "/tmp/scripts/not_the_runner.py"}
            ),
            "kernel path escape": lambda value: value["inputs"]["kernel"].update(
                {"path": "/tmp/../build/kernel"}
            ),
            "unhealthy control": lambda value: value["result"].update(
                {"supervisor_control_healthy": False}
            ),
            "attestation relocation suffix": lambda value: value[
                "invocation_argv"
            ].__setitem__(
                value["invocation_argv"].index("--attestation-file") + 1,
                "/tmp/attestations/01/wrong.json",
            ),
            "attestation path escape": lambda value: value[
                "invocation_argv"
            ].__setitem__(
                value["invocation_argv"].index("--attestation-file") + 1,
                "/tmp/../attestations/01/01-case_one.json",
            ),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                plan, attestations, guest = self.make_fixture(root)
                target = attestations / "01-case_one.json"
                self.mutate_attestation(target, mutation)
                with self.assertRaises(calibration.CalibrationError):
                    calibration.validate_round_attestations(
                        root,
                        plan,
                        1,
                        attestations,
                        guest,
                        production=False,
                    )

    def test_plan_scope_round_count_and_extra_fields_fail_closed(self):
        mutations = {
            "forged CI plan": lambda value: value.update(
                {"evidence_scope": "ci_e4_signed", "remote_ci_attestation": True}
            ),
            "two rounds": lambda value: value["rounds"].pop(),
            "undeclared field": lambda value: value.update({"manual_total": 7.5}),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                plan_path, attestations, guest = self.make_fixture(root)
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                mutation(plan)
                plan_path.write_bytes(calibration.canonical_json_bytes(plan))
                with self.assertRaises(calibration.CalibrationError):
                    calibration.validate_round_attestations(
                        root,
                        plan_path,
                        1,
                        attestations,
                        guest,
                        production=False,
                    )

    def test_versioned_local_profile_is_enforced(self):
        profile = {
            "schema_version": 1,
            "profile_id": "fixture-local-e3-v1",
            "cpu": "Fixture CPU",
            "runtime": "MSYS2 3.6.10 on Windows build 26200; QEMU TCG",
            "toolchain_prefix": "riscv-none-elf-",
            "tool_versions": {
                "qemu": "11.0.0 (fixture)",
                "toolchain_cc": "15.2.0",
                "toolchain_ld": "2.45",
                "toolchain_objcopy": "2.45",
                "toolchain_objdump": "2.45",
                "toolchain_as": "2.45",
                "host_cc": "15.3.0",
                "python": "3.12.13",
                "bash": "5.3.15(1)-release",
                "make": "4.4.1",
                "git": "2.55.0",
            },
        }
        lines = {
            "qemu": "QEMU emulator version 11.0.0 (fixture)",
            "toolchain_cc": "riscv-none-elf-gcc (xPack) 15.2.0",
            "toolchain_ld": "GNU ld (xPack) 2.45",
            "toolchain_objcopy": "GNU objcopy (xPack) 2.45",
            "toolchain_objdump": "GNU objdump (xPack) 2.45",
            "toolchain_as": "GNU assembler (xPack) 2.45",
            "host_cc": "cc (GCC) 15.3.0",
            "python": "Python 3.12.13",
            "bash": "GNU bash, version 5.3.15(1)-release",
            "make": "GNU Make 4.4.1",
            "git": "git version 2.55.0",
        }
        names = {
            "qemu": "qemu-system-riscv64",
            "toolchain_cc": "riscv-none-elf-gcc",
            "toolchain_ld": "riscv-none-elf-ld",
            "toolchain_objcopy": "riscv-none-elf-objcopy",
            "toolchain_objdump": "riscv-none-elf-objdump",
            "toolchain_as": "riscv-none-elf-as",
            "host_cc": "cc",
            "python": "python3",
            "bash": "bash",
            "make": "make",
            "git": "git",
        }
        tools = {}
        for index, name in enumerate(calibration.CALIBRATION_TOOL_NAMES, start=1):
            tools[name] = self.identity(names[name], f"{index:x}")
            tools[name]["version_first_line"] = lines[name]
        host = {
            "platform": profile["runtime"],
            "machine": profile["cpu"],
            "python_runtime": "3.12.13 fixture runtime",
        }
        self.assertEqual(
            calibration.validate_recorded_calibration_profile(
                profile, tools, host
            ),
            profile["profile_id"],
        )
        for label, mutate in (
            (
                "QEMU version",
                lambda p, t, h: t["qemu"].update(
                    {"version_first_line": "QEMU emulator version 10.2.1"}
                ),
            ),
            (
                "compiler prefix",
                lambda p, t, h: t["toolchain_cc"].update(
                    {"requested_path": "/tools/riscv64-linux-gnu-gcc"}
                ),
            ),
            (
                "CPU",
                lambda p, t, h: h.update({"machine": "Different CPU"}),
            ),
            (
                "runtime",
                lambda p, t, h: h.update({"platform": "Ubuntu 26.04; QEMU TCG"}),
            ),
        ):
            with self.subTest(label=label):
                changed_profile = copy.deepcopy(profile)
                changed_tools = copy.deepcopy(tools)
                changed_host = copy.deepcopy(host)
                mutate(changed_profile, changed_tools, changed_host)
                with self.assertRaises(calibration.CalibrationError):
                    calibration.validate_recorded_calibration_profile(
                        changed_profile, changed_tools, changed_host
                    )

    def test_missing_duplicate_and_overlapping_cases_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan, attestations, guest = self.make_fixture(root)
            (attestations / "01-case_one.json").unlink()
            with self.assertRaisesRegex(
                calibration.CalibrationError, "inventory mismatch"
            ):
                calibration.validate_round_attestations(
                    root, plan, 1, attestations, guest, production=False
                )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan, attestations, guest = self.make_fixture(root)
            first = json.loads(
                (attestations / "00-context-sync-atomicity.json").read_text()
            )

            def duplicate_and_overlap(value):
                value["identity"]["session_nonce"] = first["identity"][
                    "session_nonce"
                ]
                value["run_id"] = value["identity"]["session_nonce"]
                value["time"]["started_monotonic_ns"] = first["time"][
                    "started_monotonic_ns"
                ]

            self.mutate_attestation(
                attestations / "01-case_one.json", duplicate_and_overlap
            )
            with self.assertRaises(calibration.CalibrationError):
                calibration.validate_round_attestations(
                    root, plan, 1, attestations, guest, production=False
                )


if __name__ == "__main__":
    unittest.main()
