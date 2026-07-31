#!/usr/bin/env python3
"""Regression tests for the recursive formal Python runtime."""

from __future__ import annotations

import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import formal_python_runtime as runtime_module
from evidence_toolchain_attestation import resolve_bash_executable, resolve_executable


REPOSITORY = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix", "formal runtime requires POSIX shims")
class FormalPythonRuntimeTests(unittest.TestCase):
    def _fixture(self, base: Path, identity: str = "inner"):
        try:
            git = resolve_executable("git")
            bash = resolve_bash_executable("bash", git)
        except (OSError, ValueError):
            self.skipTest("Git or Bash is unavailable")
        repository = base / "repository"
        (repository / "scripts").mkdir(parents=True)
        (repository / "host_tools").mkdir()
        shutil.copyfile(
            REPOSITORY / runtime_module.DISPATCHER_PATH,
            repository / runtime_module.DISPATCHER_PATH,
        )
        shutil.copyfile(
            REPOSITORY / "host_tools/formal_python_runtime.py",
            repository / "host_tools/formal_python_runtime.py",
        )
        (repository / "host_tools/identity.py").write_text(
            f"VALUE = {identity!r}\n", encoding="ascii"
        )
        subprocess.run([str(git), "init", "-q"], cwd=repository, check=True)
        subprocess.run(
            [str(git), "-c", "user.name=Runtime Test", "-c",
             "user.email=runtime@example.invalid", "add", "-A"],
            cwd=repository, check=True,
        )
        subprocess.run(
            [str(git), "-c", "user.name=Runtime Test", "-c",
             "user.email=runtime@example.invalid", "commit", "-q", "-m", "fixture"],
            cwd=repository, check=True,
        )
        commit = subprocess.check_output(
            [str(git), "rev-parse", "HEAD"], cwd=repository, text=True
        ).strip()
        environment = dict(os.environ)
        environment.update({"TMPDIR": str(base), "PATH": os.environ.get("PATH", "")})
        root = base / "runtime-root"
        root.mkdir()
        formal = runtime_module.create_formal_python_runtime(
            root=root, real_python=Path(sys.executable), shell=bash,
            git=git, repository=repository, worktree=repository,
            commit=commit, environment=environment,
        )
        return formal, repository, git, bash, commit, environment

    def test_recursive_executable_and_base_remain_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            formal, *_ = self._fixture(Path(temporary))
            program = (
                "import json,subprocess,sys; out=[]; "
                "code=\"import json,sys;print(json.dumps([sys.flags.isolated,"
                "sys.flags.no_site,sys.executable,getattr(sys,'_base_executable','')]))\"; "
                "[out.append(subprocess.check_output([p,'-c',code],text=True).strip()) "
                "for p in (sys.executable,sys._base_executable)]; print(json.dumps(out))"
            )
            result = subprocess.run(
                [str(formal.executable), "-c", program], text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
                env=formal._environment,
            )
            for encoded in json.loads(result.stdout):
                isolated, no_site, executable, base = json.loads(encoded)
                self.assertEqual((isolated, no_site), (1, 1))
                self.assertEqual(executable, str(formal.executable))
                self.assertEqual(base, str(formal.executable))

    def test_nested_runtime_discards_outer_repository_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            outer_base = base / "outer"; outer_base.mkdir()
            inner_base = base / "inner"; inner_base.mkdir()
            formal, outer_repo, git, bash, outer_commit, environment = self._fixture(
                outer_base, "outer"
            )
            _unused, inner_repo, _git, _bash, inner_commit, _environment = self._fixture(
                inner_base, "inner"
            )
            nested_script = outer_repo / "scripts/nested.py"
            nested_script.write_text(
                "import json,os,subprocess,sys\n"
                "from pathlib import Path\n"
                "from formal_python_runtime import create_formal_python_runtime\n"
                "nested=create_formal_python_runtime(root=Path(sys.argv[1]),"
                "real_python=Path(sys.executable),shell=Path(sys.argv[2]),"
                "git=Path(sys.argv[3]),repository=Path(sys.argv[4]),"
                "worktree=Path(sys.argv[4]),commit=sys.argv[5],environment=dict(os.environ))\n"
                "print(subprocess.check_output([str(nested.executable),'-c',"
                "'import identity,sys;print(identity.VALUE);print(sys.executable);"
                "print(sys._base_executable)'],text=True,env=nested._environment))\n",
                encoding="ascii",
            )
            subprocess.run([str(git), "add", "scripts/nested.py"], cwd=outer_repo, check=True)
            subprocess.run(
                [str(git), "-c", "user.name=Runtime Test", "-c",
                 "user.email=runtime@example.invalid", "commit", "-q", "-m", "nested"],
                cwd=outer_repo, check=True,
            )
            outer_commit = subprocess.check_output(
                [str(git), "rev-parse", "HEAD"], cwd=outer_repo, text=True
            ).strip()
            # Recreate the outer runtime so its authenticated dispatcher commit matches.
            refreshed_root = outer_base / "refreshed"; refreshed_root.mkdir()
            formal = runtime_module.create_formal_python_runtime(
                root=refreshed_root, real_python=Path(sys.executable), shell=bash,
                git=git, repository=outer_repo, worktree=outer_repo,
                commit=outer_commit, environment=environment,
            )
            child_env = dict(formal._environment)
            nested_root = outer_base / "nested-root"; nested_root.mkdir()
            result = subprocess.run(
                [str(formal.executable), str(nested_script), str(nested_root),
                 str(bash), str(git), str(inner_repo), inner_commit],
                env=child_env, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=True,
            )
            lines = [line for line in result.stdout.splitlines() if line]
            self.assertEqual(lines[0], "inner")
            self.assertEqual(lines[1], lines[2])
            self.assertTrue(lines[1].endswith("/python"))

    def test_startup_injection_and_path_selected_shell_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            hostile = base / "hostile"; hostile.mkdir()
            sentinel = base / "sentinel"
            fake_sh = hostile / "sh"
            fake_sh.write_text(
                f"#!/bin/sh\nprintf ran >{sentinel!s}\nexit 91\n", encoding="ascii"
            )
            fake_sh.chmod(0o755)
            (hostile / "sitecustomize.py").write_text(
                f"open({str(sentinel)!r},'w').write('site')\n", encoding="ascii"
            )
            formal, repository, *_ = self._fixture(base)
            victim = repository / "victim.py"
            poison = base / "poison.py"
            victim.write_text("VALUE='safe'\n", encoding="ascii")
            poison.write_text(f"open({str(sentinel)!r},'w').write('pyc')\nVALUE='bad'\n", encoding="ascii")
            cache = repository / "__pycache__" / f"victim.{sys.implementation.cache_tag}.pyc"
            cache.parent.mkdir()
            py_compile.compile(
                str(poison), cfile=str(cache), dfile=str(victim), doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
            )
            injected = dict(formal._environment)
            injected.update({"PATH": str(hostile) + os.pathsep + injected["PATH"],
                             "PYTHONPATH": str(hostile)})
            result = subprocess.run(
                [str(formal.executable), "-c", "import victim;print(victim.VALUE)"],
                cwd=repository, env=injected, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=True,
            )
            self.assertEqual(result.stdout.strip(), "safe")
            self.assertFalse(sentinel.exists())

    def test_link_backed_child_script_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            formal, repository, *_ = self._fixture(base)
            target = repository / "target.py"
            alias = repository / "alias.py"
            target.write_text("print('unsafe')\n", encoding="ascii")
            try:
                alias.symlink_to(target)
            except OSError as error:
                self.skipTest(f"symlink fixture is unavailable: {error}")
            if not alias.is_symlink():
                self.skipTest("runtime did not create a native symlink")
            result = subprocess.run(
                [str(formal.executable), str(alias)], env=formal._environment,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("link-backed", result.stderr)


if __name__ == "__main__":
    unittest.main()
