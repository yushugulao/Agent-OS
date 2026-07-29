#!/usr/bin/env python3
"""Mutation tests for the source-C to evidence-E delivery boundary."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from evidence_delivery_contract import (
    DeliveryContractError,
    INDEX_PATH,
    make_manifest_binding,
    publish_bundle_and_index,
    verify_manifest_delivery,
)
from strict_json import strict_json_loads


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


class DeliveryFixture:
    def __init__(self, root: Path, name: str = "release-1") -> None:
        self.repo = root
        self.name = name
        git(root, "init", "-q")
        git(root, "config", "user.email", "evidence@example.invalid")
        git(root, "config", "user.name", "Evidence Test")
        git(root, "config", "core.autocrlf", "false")
        index = root / INDEX_PATH
        index.parent.mkdir(parents=True)
        index.write_bytes(
            b"# Final Evidence Releases\n\n"
            b"Release records below are append-only and are validated against the containing Git commit.\n"
        )
        (root / "source.txt").write_bytes(b"source C\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "source")
        self.source = git(root, "rev-parse", "HEAD")
        self.source_branch = git(root, "branch", "--show-current")

    def publish(self, *, symlink: bool = False) -> Path:
        release = make_manifest_binding(self.source, self.name)
        output = self.repo / release["release"]["path"]
        stage = output.with_name(f".{self.name}.stage")
        stage.mkdir()
        (stage / "manifest.json").write_bytes(
            (json.dumps({"commit": self.source, "delivery": release}) + "\n").encode(
                "utf-8"
            )
        )
        (stage / "payload.log").write_bytes(b"measured evidence\n")
        if symlink:
            os.symlink("payload.log", stage / "alias.log")
        publish_bundle_and_index(
            self.repo, stage, output, self.source, release["release"]
        )
        return output

    def commit_evidence(self, message: str = "evidence") -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")


class EvidenceDeliveryContractTests(unittest.TestCase):
    def test_exact_evidence_only_commit_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "path-alias").mkdir()
            # Preserve a lexical alias until the production containment check.
            # This reproduces Windows 8.3/long-path disagreement on every OS.
            fixture = DeliveryFixture(root / "path-alias" / "..")
            bundle = fixture.publish()
            with self.assertRaisesRegex(DeliveryContractError, "dirty"):
                verify_manifest_delivery(bundle, fixture.repo)
            evidence = fixture.commit_evidence()
            result = verify_manifest_delivery(bundle, fixture.repo)
            self.assertEqual(result["status"], "committed")
            self.assertEqual(result["source_commit"], fixture.source)
            self.assertEqual(result["evidence_commit"], evidence)
            self.assertEqual(result["files_verified"], 2)

    def test_extra_source_change_and_index_rewrite_are_rejected(self) -> None:
        mutations = ("extra", "index")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                fixture = DeliveryFixture(Path(temp))
                bundle = fixture.publish()
                if mutation == "extra":
                    (fixture.repo / "source.txt").write_text("changed in E\n", encoding="ascii")
                else:
                    index = fixture.repo / INDEX_PATH
                    index.write_text(index.read_text(encoding="ascii") + "forged\n", encoding="ascii")
                fixture.commit_evidence()
                with self.assertRaises(DeliveryContractError):
                    verify_manifest_delivery(bundle, fixture.repo)

    def test_merge_commit_and_symlink_entry_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            fixture.commit_evidence()
            git(fixture.repo, "checkout", "-q", "-b", "side", fixture.source)
            git(fixture.repo, "commit", "-q", "--allow-empty", "-m", "side")
            git(fixture.repo, "checkout", "-q", fixture.source_branch)
            git(fixture.repo, "merge", "-q", "--no-ff", "side", "-m", "merge")
            with self.assertRaisesRegex(DeliveryContractError, "sole parent"):
                verify_manifest_delivery(bundle, fixture.repo)
        if not hasattr(os, "symlink"):
            return
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            try:
                bundle = fixture.publish(symlink=True)
            except OSError:
                self.skipTest("symlink creation is unavailable")
            fixture.commit_evidence()
            with self.assertRaisesRegex(DeliveryContractError, "regular data file"):
                verify_manifest_delivery(bundle, fixture.repo)

    def test_dirty_bytes_and_manifest_binding_mutations_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            fixture.commit_evidence()
            (bundle / "payload.log").write_text("tampered\n", encoding="ascii")
            with self.assertRaisesRegex(DeliveryContractError, "dirty"):
                verify_manifest_delivery(bundle, fixture.repo)
        for mutation in (
            {"oid": "0" * 40},
            {"binding": "literal-hash"},
            {"source_parent": "0" * 40},
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                fixture = DeliveryFixture(Path(temp))
                bundle = fixture.publish()
                manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
                manifest["delivery"]["evidence_commit"].update(mutation)
                (bundle / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
                fixture.commit_evidence()
                with self.assertRaises(DeliveryContractError):
                    verify_manifest_delivery(bundle, fixture.repo)

    def test_strict_json_rejects_duplicate_and_nonfinite_security_fields(self) -> None:
        for value in (
            '{"commit":"a","commit":"b"}',
            '{"elapsed_seconds":NaN}',
            '{"timeout_seconds":Infinity}',
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                strict_json_loads(value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
