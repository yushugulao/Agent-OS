#!/usr/bin/env python3
"""证据工具身份与嵌套解析的回归测试。"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import evidence_toolchain_attestation as attestation
import evaluation_source_gate as source_gate


class EvidenceToolchainAttestationTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "formal environment is POSIX-only")
    def test_formal_environment_uses_shared_runtime_constants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool_directories = [Path("/usr/bin"), Path("/fixture/bin")]
            environment = attestation.controlled_environment(root, tool_directories)
            temporary_binding = attestation.capture_formal_temporary_binding(
                environment
            )
            native_temporary_matches = (
                os.path.samefile(environment["TMPDIR"], environment["TEMP"])
                if sys.platform == "cygwin"
                else environment["TMPDIR"] == environment["TEMP"]
            )
        expected_fixed = dict(attestation.FORMAL_ENVIRONMENT_FIXED)
        if sys.platform == "cygwin":
            expected_fixed.update(
                LANG=attestation.FORMAL_CYGWIN_LOCALE,
                LC_ALL=attestation.FORMAL_CYGWIN_LOCALE,
            )
        self.assertEqual(
            {name: environment[name] for name in expected_fixed}, expected_fixed
        )
        self.assertEqual(
            environment["PATH"],
            attestation.controlled_search_path(
                tool_directories, os.pathsep, attestation.POSIX_SYSTEM_PATHS
            ),
        )
        expected_drive = os.environ["SYSTEMDRIVE"] if sys.platform == "cygwin" else "/"
        self.assertEqual(environment["SYSTEMDRIVE"], expected_drive)
        self.assertTrue(native_temporary_matches)
        self.assertEqual(environment["TEMP"], environment["TMP"])
        self.assertEqual(
            temporary_binding["execution_platform"],
            "cygwin" if sys.platform == "cygwin" else "posix",
        )
        self.assertEqual(temporary_binding["posix_path"], environment["TMPDIR"])
        self.assertEqual(temporary_binding["native_path"], environment["TEMP"])
        self.assertEqual(
            temporary_binding["checks"],
            ["posix-native-samefile", "posix-roundtrip-samefile"],
        )

    @unittest.skipUnless(os.name == "posix", "formal environment is POSIX-only")
    def test_formal_environment_rejects_base_variable_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                attestation.ToolAttestationError, "override schema"
            ):
                attestation.controlled_environment(
                    Path(temporary),
                    [Path("/usr/bin")],
                    {"TMPDIR": "/hostile"},
                )

    def test_msys_system_drive_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, (
            mock.patch.object(attestation.sys, "platform", "cygwin")
        ), mock.patch.dict(os.environ, {"SYSTEMDRIVE": "c:"}, clear=False):
            with self.assertRaisesRegex(
                attestation.ToolAttestationError, "system drive identity"
            ):
                attestation.controlled_environment(Path(temporary), [Path("/usr/bin")])

    def _source_fixture(self, base: Path) -> tuple[Path, Path, str, dict[str, str]]:
        git_name = shutil.which("git")
        if git_name is None:
            self.skipTest("git is unavailable")
        git = Path(git_name).resolve()
        root = base / "source-gate"
        (root / "os").mkdir(parents=True)
        (root / "user" / "src").mkdir(parents=True)
        (root / "Makefile").write_text("all:\n\t@true\n", encoding="ascii")
        (root / "os" / "kernel.c").write_text("int kernel;\n", encoding="ascii")
        (root / "user" / "src" / "app.c").write_text("int app;\n", encoding="ascii")
        subprocess.run([str(git), "init", "-q"], cwd=root, check=True)
        subprocess.run(
            [str(git), "config", "user.email", "evidence@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            [str(git), "config", "user.name", "Evidence Test"],
            cwd=root,
            check=True,
        )
        subprocess.run([str(git), "add", "-A"], cwd=root, check=True)
        subprocess.run(
            [str(git), "commit", "-q", "-m", "fixture"], cwd=root, check=True
        )
        commit = subprocess.check_output(
            [str(git), "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        return root, git, commit, dict(os.environ)

    def test_clean_head_binds_commit_tracked_bytes_and_untracked_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, commit, environment = self._source_fixture(Path(temporary))
            self.assertEqual(
                source_gate.require_clean_head(git, root, environment), commit
            )
            tracked = root / "os" / "kernel.c"
            original = tracked.read_bytes()
            tracked.write_bytes(b"int forged;\n")
            with self.assertRaisesRegex(
                source_gate.ToolAttestationError, "bytes differ"
            ):
                source_gate.require_clean_head(git, root, environment)
            tracked.write_bytes(original)
            (root / "untracked.c").write_text("int untracked;\n", encoding="ascii")
            with self.assertRaisesRegex(
                source_gate.ToolAttestationError, "worktree is dirty"
            ):
                source_gate.require_clean_head(git, root, environment)

    @unittest.skipUnless(os.name == "posix", "filter command fixture requires POSIX")
    def test_isolated_checkout_does_not_run_repository_filter(self) -> None:
        git_name = shutil.which("git")
        if git_name is None:
            self.skipTest("git is unavailable")
        git = Path(git_name).resolve()
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            source.mkdir()
            commands = (
                ("init", "-q"),
                ("config", "user.email", "evidence@example.invalid"),
                ("config", "user.name", "Evidence Test"),
            )
            for arguments in commands:
                subprocess.run([str(git), *arguments], cwd=source, check=True)
            (source / ".gitattributes").write_text("tracked filter=hostile\n", encoding="ascii")
            (source / "tracked").write_bytes(b"committed\n")
            subprocess.run([str(git), "add", "-A"], cwd=source, check=True)
            subprocess.run([str(git), "commit", "-q", "-m", "fixture"], cwd=source, check=True)
            sentinel = base / "filter-ran"
            command = f"sh -c 'printf ran >{shlex.quote(str(sentinel))}; cat'"
            for operation in ("clean", "smudge"):
                subprocess.run(
                    [str(git), "config", f"filter.hostile.{operation}", command],
                    cwd=source, check=True,
                )
            subprocess.run(
                [str(git), "config", "filter.hostile.required", "true"],
                cwd=source, check=True,
            )
            commit = subprocess.check_output(
                [str(git), "rev-parse", "HEAD"], cwd=source, text=True
            ).strip()
            checkout_root = base / "checkout"
            checkout_root.mkdir()

            repository, worktree = attestation.create_isolated_detached_worktree(
                git, source, commit, checkout_root, dict(os.environ)
            )

            self.assertEqual((worktree / "tracked").read_bytes(), b"committed\n")
            self.assertFalse(sentinel.exists())
            receipt = attestation.verify_evaluation_source_tree(
                git, repository, worktree, commit, dict(os.environ),
                stage="linked worktree fixture",
            )
            self.assertEqual(receipt.tracked_files, 2)

    def test_linked_worktree_gitfile_is_strictly_bound(self) -> None:
        git_name = shutil.which("git")
        if git_name is None:
            self.skipTest("git is unavailable")
        git = Path(git_name).resolve()
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, _git, commit, environment = self._source_fixture(base)
            checkout = base / "checkout"; checkout.mkdir()
            repository, worktree = attestation.create_isolated_detached_worktree(
                git, source, commit, checkout, environment
            )
            gitfile = worktree / ".git"
            original = gitfile.read_bytes()
            gitfile.chmod(0o600)
            if os.name == "nt":
                subprocess.run(["attrib.exe", "-H", str(gitfile)], check=True)
            for label, payload in (
                ("relative", b"gitdir: relative\n"),
                ("escape", f"gitdir: {(base / 'outside').as_posix()}\n".encode("utf-8")),
                ("multiline", original + b"gitdir: duplicate\n"),
            ):
                with self.subTest(label=label):
                    gitfile.write_bytes(payload)
                    with self.assertRaisesRegex(
                        attestation.ToolAttestationError, "administration file"
                    ):
                        source_gate._filesystem_worktree_paths(
                            repository, worktree, git=git, environment=environment
                        )
                    gitfile.write_bytes(original)
            target_name = os.fsdecode(original.removeprefix(b"gitdir: ").rstrip(b"\n"))
            backpointer = Path(target_name) / "gitdir"
            original_backpointer = backpointer.read_bytes()
            backpointer.chmod(0o600)
            try:
                backpointer.write_text(str(base / "outside") + "\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    attestation.ToolAttestationError, "administration file"
                ):
                    source_gate._filesystem_worktree_paths(
                        repository, worktree, git=git, environment=environment
                    )
            finally:
                backpointer.write_bytes(original_backpointer)
            commondir = Path(target_name) / "commondir"
            original_commondir = commondir.read_bytes()
            try:
                commondir.write_bytes(b"../../outside\n")
                with self.assertRaisesRegex(
                    attestation.ToolAttestationError, "administration file"
                ):
                    source_gate._filesystem_worktree_paths(
                        repository, worktree, git=git, environment=environment
                    )
            finally:
                commondir.write_bytes(original_commondir)
            if os.name == "posix":
                external = base / "external-gitfile"
                external.write_bytes(original)
                gitfile.unlink()
                try:
                    gitfile.symlink_to(external)
                except OSError:
                    gitfile.write_bytes(original)
                else:
                    if not gitfile.is_symlink():
                        gitfile.unlink()
                        gitfile.write_bytes(original)
                        self.skipTest("runtime did not create a native symlink")
                    with self.assertRaisesRegex(
                        attestation.ToolAttestationError,
                        "administration (entry|file).*escapes|administration entry",
                    ):
                        source_gate._filesystem_worktree_paths(
                            repository, worktree, git=git, environment=environment
                        )

    def test_linked_worktree_is_accepted_from_its_own_repository_root(self) -> None:
        git_name = shutil.which("git")
        if git_name is None:
            self.skipTest("git is unavailable")
        git = Path(git_name).resolve()
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            unicode_root = base / "含中文路径"
            source, _git, commit, environment = self._source_fixture(unicode_root)
            checkout = unicode_root / "checkout"
            checkout.mkdir()
            _repository, worktree = attestation.create_isolated_detached_worktree(
                git, source, commit, checkout, environment
            )

            receipt = source_gate.verify_evaluation_source_tree(
                git,
                worktree,
                worktree,
                commit,
                environment,
                stage="direct linked worktree fixture",
            )

            self.assertEqual(receipt.tracked_files, 3)

    def test_linked_worktree_rejects_an_alternate_object_store(self) -> None:
        git_name = shutil.which("git")
        if git_name is None:
            self.skipTest("git is unavailable")
        git = Path(git_name).resolve()
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, _git, commit, environment = self._source_fixture(base)
            checkout = base / "checkout"
            checkout.mkdir()
            repository, worktree = attestation.create_isolated_detached_worktree(
                git, source, commit, checkout, environment
            )
            alternates = repository / ".git" / "objects" / "info" / "alternates"
            alternates.parent.mkdir(exist_ok=True)
            alternates.write_text(str(base / "outside") + "\n", encoding="utf-8")

            with self.assertRaisesRegex(
                source_gate.ToolAttestationError, "alternate object store"
            ):
                source_gate.verify_evaluation_source_tree(
                    git,
                    worktree,
                    worktree,
                    commit,
                    environment,
                    stage="alternate object fixture",
                )

    def test_linked_worktree_from_alternate_common_directory_is_rejected(self) -> None:
        git_name = shutil.which("git")
        if git_name is None:
            self.skipTest("git is unavailable")
        git = Path(git_name).resolve()
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository, _git, _commit, environment = self._source_fixture(
                base / "first"
            )
            source, _git, commit, _environment = self._source_fixture(base / "second")
            checkout = base / "checkout"
            checkout.mkdir()
            _other_repository, worktree = attestation.create_isolated_detached_worktree(
                git, source, commit, checkout, environment
            )

            with self.assertRaisesRegex(
                source_gate.ToolAttestationError, "administration file"
            ):
                source_gate._filesystem_worktree_paths(
                    repository, worktree, git=git, environment=environment
                )

    def test_raw_worktree_bytes_must_equal_committed_blob(self) -> None:
        git_name = shutil.which("git")
        if git_name is None:
            self.skipTest("git is unavailable")
        git = Path(git_name).resolve()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run([str(git), "init", "-q"], cwd=root, check=True)
            subprocess.run(
                [str(git), "config", "user.email", "evidence@example.invalid"],
                cwd=root, check=True,
            )
            subprocess.run(
                [str(git), "config", "user.name", "Evidence Test"],
                cwd=root, check=True,
            )
            tracked = root / "tracked.txt"
            tracked.write_bytes(b"committed\n")
            subprocess.run([str(git), "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run([str(git), "commit", "-q", "-m", "fixture"], cwd=root, check=True)
            commit = subprocess.check_output(
                [str(git), "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            self.assertEqual(
                attestation.verify_tracked_worktree_bytes(
                    git, root, root, commit, dict(os.environ)
                ),
                1,
            )
            tracked.write_bytes(b"filtered\n")
            with self.assertRaisesRegex(
                attestation.ToolAttestationError, "differ from HEAD blob"
            ):
                attestation.verify_tracked_worktree_bytes(
                    git, root, root, commit, dict(os.environ)
                )

    def test_ignored_make_wildcard_sources_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, commit, environment = self._source_fixture(Path(temporary))
            exclude = root / ".git" / "info" / "exclude"
            exclude.write_text("os/hidden.c\nuser/src/hidden.c\n", encoding="ascii")
            for relative in ("os/hidden.c", "user/src/hidden.c"):
                path = root / relative
                path.write_text("int hidden;\n", encoding="ascii")
                status = subprocess.check_output(
                    [str(git), "status", "--porcelain=v1", "--untracked-files=all"],
                    cwd=root,
                )
                self.assertEqual(status, b"")
                with self.assertRaisesRegex(
                    attestation.ToolAttestationError, "nontracked source input"
                ):
                    attestation.verify_evaluation_source_tree(
                        git,
                        root,
                        root,
                        commit,
                        environment,
                        allowed_output_roots=("build", "results/evaluation/formal-fixture"),
                        stage="before build",
                    )
                path.unlink()

    def test_ignored_empty_directory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, commit, environment = self._source_fixture(Path(temporary))
            (root / ".gitignore").write_text("os/empty/\n", encoding="ascii")
            subprocess.run([str(git), "add", ".gitignore"], cwd=root, check=True)
            subprocess.run(
                [str(git), "commit", "-q", "-m", "ignore fixture"], cwd=root, check=True
            )
            commit = subprocess.check_output(
                [str(git), "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            (root / "os" / "empty").mkdir()
            with self.assertRaisesRegex(
                attestation.ToolAttestationError, "nontracked source input"
            ):
                attestation.verify_evaluation_source_tree(
                    git, root, root, commit, environment,
                    allowed_output_roots=("build",), stage="before build",
                )

    @unittest.skipUnless(os.name == "posix", "fsmonitor hook fixture requires POSIX")
    def test_local_fsmonitor_is_not_executed_by_source_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, commit, environment = self._source_fixture(Path(temporary))
            sentinel = Path(temporary) / "fsmonitor-ran"
            hook = Path(temporary) / "fsmonitor.sh"
            hook.write_text(
                f"#!/bin/sh\nprintf ran >{shlex.quote(str(sentinel))}\nprintf '0\\n'\n",
                encoding="ascii",
            )
            hook.chmod(0o755)
            subprocess.run(
                [str(git), "config", "core.fsmonitor", str(hook)], cwd=root, check=True
            )
            subprocess.run(
                [str(git), "status", "--porcelain=v1"], cwd=root,
                stdout=subprocess.DEVNULL, check=True,
            )
            self.assertTrue(sentinel.exists(), "fixture did not exercise fsmonitor")
            sentinel.unlink()
            attestation.verify_evaluation_source_tree(
                git, root, root, commit, environment,
                allowed_output_roots=("build",), stage="before build",
            )
            self.assertFalse(sentinel.exists())

    def test_exact_generated_roots_are_allowed_and_safely_purged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, commit, environment = self._source_fixture(Path(temporary))
            (root / "build" / "os").mkdir(parents=True)
            (root / "build" / "os" / "kernel.d").write_text(
                "$(error preseeded dependency was parsed)\n", encoding="ascii"
            )
            (root / "build" / "os" / "kernel.o").write_bytes(b"preseeded object\n")
            (root / "results" / "evaluation" / "formal-fixture").mkdir(parents=True)
            (root / "results" / "evaluation" / "formal-fixture" / "runner.log").write_text(
                "generated\n", encoding="ascii"
            )
            receipt = attestation.verify_evaluation_source_tree(
                git,
                root,
                root,
                commit,
                environment,
                allowed_output_roots=("build", "results/evaluation/formal-fixture"),
                stage="before trusted purge",
            )
            self.assertIn("build/os/kernel.d", receipt.generated_paths)
            self.assertIn("build/os/kernel.o", receipt.generated_paths)
            attestation.purge_evaluation_generated_outputs(
                git,
                root,
                root,
                commit,
                environment,
                output_roots=("build",),
                output_files=(),
            )
            self.assertFalse((root / "build").exists())
            self.assertTrue(
                (root / "results" / "evaluation" / "formal-fixture" / "runner.log").is_file()
            )
            attestation.verify_evaluation_source_tree(
                git,
                root,
                root,
                commit,
                environment,
                allowed_output_roots=("build", "results/evaluation/formal-fixture"),
                stage="after trusted purge",
            )

    def test_source_and_generated_inventories_have_independent_budgets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, commit, environment = self._source_fixture(Path(temporary))
            build = root / "build"
            build.mkdir()
            for index in range(4):
                (build / f"output-{index}").write_bytes(b"generated\n")

            with mock.patch.object(source_gate, "DEFAULT_MAX_WALK_FILES", 4):
                receipt = attestation.verify_evaluation_source_tree(
                    git,
                    root,
                    root,
                    commit,
                    environment,
                    allowed_output_roots=("build",),
                    stage="independent inventory budgets",
                )
                self.assertEqual(
                    len([path for path in receipt.generated_paths if path.startswith("build/")]),
                    4,
                )

                (build / "output-over-budget").write_bytes(b"generated\n")
                with self.assertRaisesRegex(
                    attestation.ToolAttestationError, "generated inventory exceeds"
                ):
                    attestation.verify_evaluation_source_tree(
                        git,
                        root,
                        root,
                        commit,
                        environment,
                        allowed_output_roots=("build",),
                        stage="generated inventory over budget",
                    )
                (build / "output-over-budget").unlink()

                (root / "extra-one").write_bytes(b"source\n")
                (root / "extra-two").write_bytes(b"source\n")
                with self.assertRaisesRegex(
                    attestation.ToolAttestationError, "source inventory exceeds"
                ):
                    attestation.verify_evaluation_source_tree(
                        git,
                        root,
                        root,
                        commit,
                        environment,
                        allowed_output_roots=("build",),
                        stage="source inventory over budget",
                    )

    @unittest.skipUnless(os.name == "posix", "tracked symlink fixture requires POSIX")
    def test_tracked_symlink_cannot_escape_source_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, _commit, environment = self._source_fixture(Path(temporary))
            external = Path(temporary) / "external.c"
            external.write_text("int outside;\n", encoding="ascii")
            link = root / "os" / "external.c"
            try:
                link.symlink_to(external)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")
            if not link.is_symlink():
                self.skipTest("the host materialized a pseudo-symlink")
            subprocess.run([str(git), "add", "os/external.c"], cwd=root, check=True)
            subprocess.run(
                [str(git), "commit", "-q", "-m", "tracked link"], cwd=root, check=True
            )
            commit = subprocess.check_output(
                [str(git), "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            with self.assertRaisesRegex(
                attestation.ToolAttestationError, "link or other unsafe entry"
            ):
                attestation.verify_evaluation_source_tree(
                    git,
                    root,
                    root,
                    commit,
                    environment,
                    allowed_output_roots=("build",),
                    stage="before build",
                )
    def test_output_prefix_does_not_authorize_a_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, commit, environment = self._source_fixture(Path(temporary))
            (root / "results" / "evaluation" / "formal-forged").mkdir(parents=True)
            (root / "results" / "evaluation" / "formal-forged" / "protocol.py").write_text(
                "raise SystemExit(0)\n", encoding="ascii"
            )
            with self.assertRaisesRegex(
                attestation.ToolAttestationError,
                "results/evaluation/formal-forged/protocol.py",
            ):
                attestation.verify_evaluation_source_tree(
                    git,
                    root,
                    root,
                    commit,
                    environment,
                    allowed_output_roots=("results/evaluation/formal-fixture",),
                    stage="before collection",
                )

    def test_run_root_allows_exact_pointers_but_rejects_sibling_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, commit, environment = self._source_fixture(Path(temporary))
            current = root / "results" / "evaluation" / "runs" / "formal-current"
            sibling = root / "results" / "evaluation" / "runs" / "formal-sibling"
            current.mkdir(parents=True)
            sibling.mkdir(parents=True)
            (current / "campaign.json").write_text("{}\n", encoding="ascii")
            (sibling / "campaign.json").write_text("{}\n", encoding="ascii")
            for name in ("last-attempt.txt", "latest-run.txt"):
                (root / "results" / "evaluation" / name).write_text(
                    "formal-current\n", encoding="ascii"
                )
            (root / ".git" / "info" / "exclude").write_text(
                "results/evaluation/\n", encoding="ascii"
            )
            with self.assertRaisesRegex(
                attestation.ToolAttestationError, "formal-sibling/campaign.json"
            ):
                attestation.verify_evaluation_source_tree(
                    git,
                    root,
                    root,
                    commit,
                    environment,
                    allowed_output_roots=(
                        "results/evaluation/runs/formal-current",
                    ),
                    allowed_output_files=attestation.EVALUATION_ARTIFACT_OUTPUT_FILES,
                    stage="before full verify",
                )
            shutil.rmtree(sibling)
            attestation.verify_evaluation_source_tree(
                git,
                root,
                root,
                commit,
                environment,
                allowed_output_roots=("results/evaluation/runs/formal-current",),
                allowed_output_files=attestation.EVALUATION_ARTIFACT_OUTPUT_FILES,
                stage="before full verify",
            )
            with self.assertRaisesRegex(
                attestation.ToolAttestationError, "exact namespace descendant"
            ):
                attestation.verify_evaluation_source_tree(
                    git,
                    root,
                    root,
                    commit,
                    environment,
                    allowed_output_roots=("results/evaluation",),
                    allowed_output_files=(),
                    stage="before full verify",
                )

    def test_core_worktree_cannot_redirect_nontracked_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, commit, environment = self._source_fixture(Path(temporary))
            alternate = Path(temporary) / "alternate"
            alternate.mkdir()
            subprocess.run(
                [str(git), f"--work-tree={alternate}", "checkout", "-f", commit],
                cwd=root,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            subprocess.run(
                [str(git), "config", "core.worktree", str(alternate)],
                cwd=root,
                check=True,
            )
            hidden = root / "os" / "hidden.c"
            hidden.write_text("int hidden;\n", encoding="ascii")
            (root / ".git" / "info" / "exclude").write_text(
                "os/hidden.c\n", encoding="ascii"
            )
            self.assertEqual(
                subprocess.check_output(
                    [str(git), "status", "--porcelain=v1", "--untracked-files=all"],
                    cwd=root,
                ),
                b"",
            )
            with self.assertRaisesRegex(
                attestation.ToolAttestationError, "registered worktree differs"
            ):
                attestation.verify_evaluation_source_tree(
                    git,
                    root,
                    root,
                    commit,
                    environment,
                    allowed_output_roots=("build",),
                    stage="before build",
                )

    @unittest.skipUnless(
        os.name == "posix" and sys.platform != "cygwin",
        "executable-mode fixture requires native POSIX",
    )
    def test_tracked_executable_mode_must_match_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, commit, environment = self._source_fixture(Path(temporary))
            source = root / "os" / "kernel.c"
            source.chmod(source.stat().st_mode | 0o111)
            with self.assertRaisesRegex(
                attestation.ToolAttestationError, "executable mode differs"
            ):
                attestation.verify_evaluation_source_tree(
                    git,
                    root,
                    root,
                    commit,
                    environment,
                    allowed_output_roots=("build",),
                    stage="before build",
                )

    def test_core_filemode_false_records_unverifiable_without_false_reject(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, commit, environment = self._source_fixture(Path(temporary))
            subprocess.run(
                [str(git), "config", "core.filemode", "false"], cwd=root, check=True
            )
            source = root / "os" / "kernel.c"
            source.chmod(source.stat().st_mode | 0o111)
            receipt = attestation.verify_evaluation_source_tree(
                git,
                root,
                root,
                commit,
                environment,
                allowed_output_roots=("build",),
                stage="before build",
            )
            self.assertFalse(receipt.executable_mode_verified)

    @unittest.skipUnless(os.name == "posix", "symlink fixture requires POSIX")
    def test_generated_output_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, git, commit, environment = self._source_fixture(Path(temporary))
            outside = Path(temporary) / "outside"
            outside.mkdir()
            try:
                (root / "build").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks are unavailable: {error}")
            if not attestation.path_is_link(root / "build"):
                self.skipTest("this runtime materializes directory symlinks as ordinary directories")
            with self.assertRaisesRegex(
                attestation.ToolAttestationError,
                "unsafe|link-backed|contains a link",
            ):
                attestation.verify_evaluation_source_tree(
                    git,
                    root,
                    root,
                    commit,
                    environment,
                    allowed_output_roots=("build",),
                    stage="before build",
                )

    def test_changed_tool_is_rejected_after_pre_run_capture(self) -> None:
        if os.name != "posix":
            self.skipTest("executable script fixture requires POSIX")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = root / "tool"
            tool.write_text("#!/usr/bin/env sh\necho tool-v1\n", encoding="ascii")
            tool.chmod(0o755)
            records = [
                attestation.capture_version("tool", tool, root, dict(os.environ))
            ]
            tool.write_text("#!/usr/bin/env sh\necho tool-v2\n", encoding="ascii")
            tool.chmod(0o755)
            with self.assertRaisesRegex(
                attestation.ToolAttestationError, "identity changed"
            ):
                attestation.verify_tool_attestations(
                    {"tool": tool}, records, root, dict(os.environ), "during execution"
                )

    def test_crlf_version_output_is_compared_as_exact_bytes(self) -> None:
        if os.name != "posix":
            self.skipTest("executable script fixture requires POSIX")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = root / "tool"
            tool.write_text(
                "#!/bin/sh\nprintf 'tool-v1\\r\\nsecond-line\\r\\n'\n",
                encoding="ascii",
            )
            tool.chmod(0o755)
            records = [
                attestation.capture_version("tool", tool, root, dict(os.environ))
            ]
            version_log = root / str(records[0]["log"])
            self.assertEqual(version_log.read_bytes(), b"tool-v1\r\nsecond-line\r\n")
            attestation.verify_tool_attestations(
                {"tool": tool}, records, root, dict(os.environ), "during execution"
            )

    def test_nested_bare_tools_must_resolve_to_attested_paths(self) -> None:
        roots = {
            label: Path(f"/trusted/{command}")
            for label, command in (("git", "git"), ("make", "make"), ("bash", "bash"))
        }

        def matching(command: str, *, path: str) -> str:
            del path
            return str(roots[command])

        resolve = mock.patch.object(
            Path,
            "resolve",
            autospec=True,
            side_effect=lambda value, strict: value,
        )
        with mock.patch.object(attestation.shutil, "which", side_effect=matching), resolve:
            attestation.require_nested_tool_resolution(roots, {"PATH": "/trusted"})

        with (
            mock.patch.object(
                attestation.shutil,
                "which",
                side_effect=lambda command, path: (
                    "/untrusted/make" if command == "make" else str(roots[command])
                ),
            ),
            mock.patch.object(
                Path,
                "resolve",
                autospec=True,
                side_effect=lambda value, strict: value,
            ),
            self.assertRaisesRegex(
                attestation.ToolAttestationError, "attested make"
            ),
        ):
            attestation.require_nested_tool_resolution(roots, {"PATH": "/trusted"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
