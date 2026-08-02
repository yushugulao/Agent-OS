#!/usr/bin/env python3
"""Mutation tests for the source-C to evidence-E delivery boundary."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import evidence_delivery_contract as delivery_contract
import evaluation_bundle as evaluation_bundle_contract
from evidence_delivery_contract import (
    DeliveryContractError,
    INDEX_PATH,
    make_manifest_binding,
    publish_bundle_and_index,
    verify_historical_committed_delivery,
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

    def publish(
        self,
        *,
        symlink: bool = False,
        schema_version: int = evaluation_bundle_contract.SCHEMA_VERSION,
    ) -> Path:
        release = make_manifest_binding(self.source, self.name)
        output = self.repo / release["release"]["path"]
        stage = output.with_name(f".{self.name}.stage")
        stage.mkdir()
        (stage / "manifest.json").write_bytes(
            (
                json.dumps({
                    "kind": "agentos-evaluation-evidence-bundle",
                    "schema_version": schema_version,
                    "source_commit": self.source,
                    "delivery": release,
                })
                + "\n"
            ).encode("utf-8")
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
    def test_current_evaluation_bundle_schema_is_the_only_registered_version(self) -> None:
        identity = (
            evaluation_bundle_contract.KIND,
            evaluation_bundle_contract.SCHEMA_VERSION,
        )
        self.assertEqual(
            delivery_contract.MANIFEST_SOURCE_FIELDS.get(identity),
            "source_commit",
        )
        self.assertNotIn(
            (evaluation_bundle_contract.KIND, 4),
            delivery_contract.MANIFEST_SOURCE_FIELDS,
        )

        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            evidence = fixture.commit_evidence()
            result = verify_manifest_delivery(bundle, fixture.repo)
            self.assertEqual(result["status"], "committed-history")
            self.assertEqual(result["evidence_commit"], evidence)

        for invalid_schema in (4, 5.0, True, "5"):
            with self.subTest(schema_version=invalid_schema):
                with tempfile.TemporaryDirectory() as temp:
                    fixture = DeliveryFixture(Path(temp))
                    bundle = fixture.publish(schema_version=invalid_schema)  # type: ignore[arg-type]
                    fixture.commit_evidence()
                    with self.assertRaisesRegex(
                        DeliveryContractError, "kind/schema is unsupported"
                    ):
                        verify_manifest_delivery(bundle, fixture.repo)

    def test_full_evidence_schemas_use_historical_delivery(self) -> None:
        for schema_version in (6, 7):
            with self.subTest(schema_version=schema_version), (
                tempfile.TemporaryDirectory()
            ) as temp:
                fixture = DeliveryFixture(Path(temp))
                bundle = fixture.publish()
                manifest_path = bundle / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                delivery = manifest["delivery"]
                manifest = {
                    "schema_version": schema_version,
                    "status": "ready",
                    "commit": fixture.source,
                    "collected_at_utc": "2026-01-01T00:00:00Z",
                    "authenticity": {},
                    "delivery": delivery,
                    "command": {},
                    "verification_summary": {},
                    "raw_artifacts": [],
                    "environment": {},
                    "configuration": {},
                    "metrics": [],
                }
                manifest_path.write_text(
                    json.dumps(manifest) + "\n", encoding="utf-8"
                )
                evidence = fixture.commit_evidence()
                documentation = fixture.repo / "docs" / "after-evidence.md"
                documentation.parent.mkdir()
                documentation.write_text("documentation\n", encoding="ascii")
                git(fixture.repo, "add", "docs/after-evidence.md")
                git(fixture.repo, "commit", "-q", "-m", "documentation descendant")
                result = verify_manifest_delivery(bundle, fixture.repo)
                self.assertEqual(result["status"], "committed-history")
                self.assertEqual(result["evidence_commit"], evidence)

    def test_unknown_or_incomplete_manifest_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("kind")
            manifest["schema_version"] = 6
            manifest["commit"] = manifest.pop("source_commit")
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            fixture.commit_evidence()
            with self.assertRaisesRegex(DeliveryContractError, "schema v6 manifest"):
                verify_manifest_delivery(bundle, fixture.repo)

    def test_exact_evidence_only_commit_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "path-alias").mkdir()
            # Preserve a lexical alias until the production containment check.
            # This reproduces Windows 8.3/long-path disagreement on every OS.
            fixture = DeliveryFixture(root / "path-alias" / "..")
            bundle = fixture.publish()
            for index in range(12):
                (bundle / f"payload-{index}.log").write_text(
                    f"payload {index}\n", encoding="ascii"
                )
            with self.assertRaisesRegex(DeliveryContractError, "dirty"):
                verify_manifest_delivery(bundle, fixture.repo)
            evidence = fixture.commit_evidence()
            calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
            original = delivery_contract._run

            def record(*args: object, **kwargs: object) -> object:
                if len(args) > 2 and args[2] == "hash-object":
                    calls.append((args, kwargs))
                return original(*args, **kwargs)  # type: ignore[arg-type]

            with patch.object(delivery_contract, "_run", side_effect=record):
                result = verify_manifest_delivery(bundle, fixture.repo)
            self.assertEqual(result["status"], "committed-history")
            self.assertEqual(result["source_commit"], fixture.source)
            self.assertEqual(result["evidence_commit"], evidence)
            self.assertEqual(result["files_verified"], 14)
            self.assertEqual(len(calls), 1)
            self.assertIn("--stdin-paths", calls[0][0])
            self.assertEqual(calls[0][1]["input_bytes"].count(b"\n"), 14)

    def test_git_verification_ignores_ambient_git_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture_root = root / "repository"
            fixture_root.mkdir()
            fixture = DeliveryFixture(fixture_root)
            bundle = fixture.publish()
            evidence = fixture.commit_evidence()
            decoy = root / "decoy-git-dir"
            decoy.mkdir()
            git(decoy, "init", "-q")
            ambient = {
                "GIT_CEILING_DIRECTORIES": str(root),
                "GIT_CONFIG_COUNT": "0",
                "GIT_DIR": str(decoy / ".git"),
                "GIT_INDEX_FILE": str(decoy / "forged-index"),
                "GIT_NO_REPLACE_OBJECTS": "0",
                "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
                "GIT_WORK_TREE": str(decoy),
            }
            with patch.dict(os.environ, ambient, clear=False):
                result = verify_manifest_delivery(bundle, fixture.repo)
            self.assertEqual(result["status"], "committed-history")
            self.assertEqual(result["source_commit"], fixture.source)
            self.assertEqual(result["evidence_commit"], evidence)

    def test_git_verification_rejects_replace_ref_forged_ancestry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            git(fixture.repo, "commit", "-q", "--allow-empty", "-m", "intermediate")
            evidence = fixture.commit_evidence()
            evidence_tree = git(
                fixture.repo, "rev-parse", f"{evidence}^{{tree}}"
            )
            replacement = git(
                fixture.repo,
                "commit-tree",
                evidence_tree,
                "-p",
                fixture.source,
                "-m",
                "forged evidence ancestry",
            )
            git(fixture.repo, "replace", evidence, replacement)

            with patch.dict(
                os.environ, {"GIT_NO_REPLACE_OBJECTS": "0"}, clear=False
            ):
                with self.assertRaisesRegex(
                    DeliveryContractError, "sole parent|immutable introducing"
                ):
                    verify_manifest_delivery(bundle, fixture.repo)

    def test_git_verification_rejects_graft_forged_ancestry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            git(fixture.repo, "commit", "-q", "--allow-empty", "-m", "intermediate")
            evidence = fixture.commit_evidence()
            grafts = fixture.repo / ".git" / "info" / "grafts"
            grafts.write_text(f"{evidence} {fixture.source}\n", encoding="ascii")

            with self.assertRaisesRegex(
                DeliveryContractError, "sole parent|raw ancestry"
            ):
                verify_manifest_delivery(bundle, fixture.repo)

    def test_committed_tree_delivery_limits_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            fixture.commit_evidence()
            release_path = bundle.relative_to(fixture.repo).as_posix()
            sizes = tuple(
                int(git(fixture.repo, "cat-file", "-s", f"HEAD:{release_path}/{name}"))
                for name in ("manifest.json", "payload.log")
            )
            cases = (
                ("MAX_COMMITTED_FILES", 1, "too many tracked files"),
                ("MAX_COMMITTED_FILE_BYTES", max(sizes) - 1, "file exceeds size limit"),
                ("MAX_COMMITTED_TOTAL_BYTES", max(sizes), "exceeds total size limit"),
            )
            for constant, limit, message in cases:
                with self.subTest(constant=constant):
                    with patch.object(delivery_contract, constant, limit):
                        with self.assertRaisesRegex(DeliveryContractError, message):
                            verify_manifest_delivery(bundle, fixture.repo)

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
            with self.assertRaisesRegex(
                DeliveryContractError, "sole parent|documentation-only"
            ):
                verify_manifest_delivery(bundle, fixture.repo)
        if not hasattr(os, "symlink"):
            return
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            try:
                bundle = fixture.publish(symlink=True)
            except OSError:
                self.skipTest("symlink creation is unavailable")
            alias = bundle / "alias.log"
            isjunction = getattr(os.path, "isjunction", None)
            if not alias.is_symlink() and not bool(isjunction and isjunction(alias)):
                # MSYS may implement os.symlink as a regular-file copy when
                # Windows link creation is unavailable; there is no link entry
                # for the delivery contract to reject in that execution mode.
                return
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

    def test_clean_check_rejects_hidden_index_flags_and_real_bytes(self) -> None:
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as temp:
                fixture = DeliveryFixture(Path(temp))
                bundle = fixture.publish()
                fixture.commit_evidence()
                git(fixture.repo, "update-index", flag, "source.txt")
                (fixture.repo / "source.txt").write_text(
                    "hidden worktree mutation\n", encoding="ascii"
                )
                self.assertEqual(
                    git(fixture.repo, "status", "--porcelain", "--untracked-files=all"),
                    "",
                )
                with self.assertRaisesRegex(
                    DeliveryContractError, "hidden or nonstandard tracked flag"
                ):
                    verify_manifest_delivery(bundle, fixture.repo)

    def test_index_must_remain_a_100644_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            git(fixture.repo, "add", "-A")
            git(fixture.repo, "update-index", "--chmod=+x", INDEX_PATH)
            git(fixture.repo, "commit", "-q", "-m", "executable index")
            self.assertTrue(git(fixture.repo, "ls-tree", "HEAD", INDEX_PATH).startswith("100755 "))
            with self.assertRaisesRegex(DeliveryContractError, "100644 regular blob"):
                verify_manifest_delivery(bundle, fixture.repo)

    def test_strict_json_rejects_duplicate_and_nonfinite_security_fields(self) -> None:
        for value in (
            '{"commit":"a","commit":"b"}',
            '{"elapsed_seconds":NaN}',
            '{"timeout_seconds":Infinity}',
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                strict_json_loads(value)

    def test_historical_delivery_survives_documentation_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            evidence = fixture.commit_evidence()
            documentation = fixture.repo / "docs" / "delivery.md"
            documentation.parent.mkdir()
            documentation.write_text(
                "post-evidence documentation\n", encoding="ascii"
            )
            git(fixture.repo, "add", "docs/delivery.md")
            git(fixture.repo, "commit", "-q", "-m", "docs")
            descendant = git(fixture.repo, "rev-parse", "HEAD")
            binding = make_manifest_binding(fixture.source, fixture.name)
            result = verify_historical_committed_delivery(
                bundle,
                fixture.source,
                binding["evidence_commit"],
                binding["release"],
                repo_root=fixture.repo,
                require_committed=True,
            )
            self.assertEqual(result["status"], "committed-history")
            self.assertEqual(result["evidence_commit"], evidence)
            self.assertEqual(result["containing_head"], descendant)
            dispatched = verify_manifest_delivery(bundle, fixture.repo)
            self.assertEqual(dispatched["status"], "committed-history")
            self.assertEqual(dispatched["evidence_commit"], evidence)

    def test_historical_walk_is_batched_and_budgeted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            fixture.commit_evidence()
            documentation = fixture.repo / "docs" / "history.md"
            documentation.parent.mkdir()
            for index in range(3):
                documentation.write_text(f"revision {index}\n", encoding="ascii")
                git(fixture.repo, "add", "docs/history.md")
                git(fixture.repo, "commit", "-q", "-m", f"docs {index}")

            calls: list[tuple[str, ...]] = []
            events: list[str] = []
            history_contract = delivery_contract._git_history
            original = history_contract._bounded_git_bytes
            original_delivery = delivery_contract._verify_delivery_commit

            def record(*args: object, **kwargs: object) -> bytes:
                events.append("history")
                calls.append(tuple(str(value) for value in args[2:]))
                return original(*args, **kwargs)  # type: ignore[arg-type]

            def record_delivery(*args: object, **kwargs: object) -> dict[str, object]:
                events.append("delivery")
                return original_delivery(*args, **kwargs)  # type: ignore[arg-type]

            with patch.object(
                history_contract, "_bounded_git_bytes", side_effect=record
            ), patch.object(
                delivery_contract, "_verify_delivery_commit", side_effect=record_delivery
            ):
                result = verify_manifest_delivery(bundle, fixture.repo)
            self.assertEqual(result["status"], "committed-history")
            self.assertEqual(events, ["history"] * 4 + ["delivery"])
            self.assertEqual(
                [call[0] for call in calls],
                ["rev-list", "cat-file", "cat-file", "diff-tree"],
            )
            self.assertNotIn("--batch-all-objects", calls[1])
            self.assertEqual(calls[1][1:], ("--batch",))
            self.assertTrue(
                any(argument.startswith("--batch-check=") for argument in calls[2])
            )

            for constant, limit, message in (
                ("MAX_PROCESSES", 2, "process budget"),
                ("MAX_COMMIT_BYTES", 1, "commit object exceeds"),
                ("MAX_DIFF_BYTES", 1, "output budget"),
                ("MAX_SECONDS", 0.0, "time budget"),
            ):
                with self.subTest(constant=constant), patch.object(
                    history_contract, constant, limit
                ):
                    with self.assertRaisesRegex(DeliveryContractError, message):
                        verify_manifest_delivery(bundle, fixture.repo)

    def test_historical_candidate_fanout_uses_one_bounded_path_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            evidence = fixture.commit_evidence()
            source_tree = git(fixture.repo, "rev-parse", f"{fixture.source}^{{tree}}")
            decoys = [
                git(
                    fixture.repo,
                    "commit-tree",
                    source_tree,
                    "-p",
                    fixture.source,
                    "-m",
                    f"fanout {index}",
                )
                for index in range(12)
            ]
            evidence_tree = git(fixture.repo, "rev-parse", f"{evidence}^{{tree}}")
            merge_arguments = ["commit-tree", evidence_tree]
            for parent in [evidence, *decoys]:
                merge_arguments.extend(("-p", parent))
            merge_arguments.extend(("-m", "fanout merge"))
            head = git(fixture.repo, *merge_arguments)
            git(fixture.repo, "reset", "--hard", head)

            calls: list[tuple[str, bytes | None]] = []
            history_contract = delivery_contract._git_history
            original = history_contract._bounded_git_bytes

            def record(*args: object, **kwargs: object) -> bytes:
                calls.append(
                    (
                        str(kwargs["label"]),
                        kwargs.get("input_bytes"),  # type: ignore[arg-type]
                    )
                )
                return original(*args, **kwargs)  # type: ignore[arg-type]

            binding = make_manifest_binding(fixture.source, fixture.name)
            with patch.object(
                history_contract, "_bounded_git_bytes", side_effect=record
            ):
                with self.assertRaisesRegex(
                    DeliveryContractError, "documentation-only"
                ):
                    verify_historical_committed_delivery(
                        bundle,
                        fixture.source,
                        binding["evidence_commit"],
                        binding["release"],
                        repo_root=fixture.repo,
                        require_committed=True,
                    )
            self.assertEqual(
                [label for label, _request in calls],
                [
                    "raw ancestry commit inventory",
                    "raw ancestry commit object",
                    "raw ancestry candidate path inventory",
                    "documentation descendant diff",
                ],
            )
            candidate_request = calls[2][1]
            self.assertIsNotNone(candidate_request)
            self.assertEqual(candidate_request.count(b"\n"), 1 + len(decoys))

    def test_publish_requires_source_commit_as_current_head(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            git(fixture.repo, "commit", "-q", "--allow-empty", "-m", "descendant")
            with self.assertRaisesRegex(DeliveryContractError, "current HEAD"):
                fixture.publish()

    def test_historical_delivery_rejects_descendant_bypass_tampering(self) -> None:
        for mutation in ("bundle", "index_rewrite", "index_append", "code"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                fixture = DeliveryFixture(Path(temp))
                bundle = fixture.publish()
                fixture.commit_evidence()
                if mutation == "bundle":
                    (bundle / "payload.log").write_text("later rewrite\n", encoding="ascii")
                    git(
                        fixture.repo,
                        "add",
                        (bundle / "payload.log").relative_to(fixture.repo).as_posix(),
                    )
                elif mutation == "index_rewrite":
                    index = fixture.repo / INDEX_PATH
                    index.write_text("rewritten index\n", encoding="ascii")
                    git(fixture.repo, "add", INDEX_PATH)
                elif mutation == "index_append":
                    index = fixture.repo / INDEX_PATH
                    index.write_text(
                        index.read_text(encoding="ascii") + "forged trailing record\n",
                        encoding="ascii",
                    )
                    git(fixture.repo, "add", INDEX_PATH)
                else:
                    (fixture.repo / "source.txt").write_text(
                        "post-evidence code change\n", encoding="ascii"
                    )
                    git(fixture.repo, "add", "source.txt")
                git(fixture.repo, "commit", "-q", "-m", "tamper")
                binding = make_manifest_binding(fixture.source, fixture.name)
                with self.assertRaises(DeliveryContractError):
                    verify_historical_committed_delivery(
                        bundle,
                        fixture.source,
                        binding["evidence_commit"],
                        binding["release"],
                        repo_root=fixture.repo,
                        require_committed=True,
                    )

    def test_historical_delivery_rejects_reverted_code_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            fixture.commit_evidence()
            source = fixture.repo / "source.txt"
            original = source.read_bytes()
            source.write_bytes(b"temporary code mutation\n")
            git(fixture.repo, "add", "source.txt")
            git(fixture.repo, "commit", "-q", "-m", "temporary code")
            source.write_bytes(original)
            git(fixture.repo, "add", "source.txt")
            git(fixture.repo, "commit", "-q", "-m", "revert code")
            binding = make_manifest_binding(fixture.source, fixture.name)
            with self.assertRaisesRegex(
                DeliveryContractError, "documentation-only|raw ancestry"
            ):
                verify_historical_committed_delivery(
                    bundle,
                    fixture.source,
                    binding["evidence_commit"],
                    binding["release"],
                    repo_root=fixture.repo,
                    require_committed=True,
                )

    def test_historical_delivery_rejects_graft_hidden_tamper_and_revert(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = DeliveryFixture(Path(temp))
            bundle = fixture.publish()
            evidence = fixture.commit_evidence()
            payload = bundle / "payload.log"
            original = payload.read_bytes()
            payload.write_bytes(b"temporary evidence tamper\n")
            git(fixture.repo, "add", payload.relative_to(fixture.repo).as_posix())
            git(fixture.repo, "commit", "-q", "-m", "tamper")
            payload.write_bytes(original)
            documentation = fixture.repo / "docs" / "delivery.md"
            documentation.parent.mkdir()
            documentation.write_text("documentation\n", encoding="ascii")
            git(
                fixture.repo,
                "add",
                payload.relative_to(fixture.repo).as_posix(),
                "docs/delivery.md",
            )
            git(fixture.repo, "commit", "-q", "-m", "revert and docs")
            head = git(fixture.repo, "rev-parse", "HEAD")
            grafts = fixture.repo / ".git" / "info" / "grafts"
            grafts.write_text(f"{head} {evidence}\n", encoding="ascii")
            binding = make_manifest_binding(fixture.source, fixture.name)

            with self.assertRaisesRegex(
                DeliveryContractError, "documentation-only|raw ancestry"
            ):
                verify_historical_committed_delivery(
                    bundle,
                    fixture.source,
                    binding["evidence_commit"],
                    binding["release"],
                    repo_root=fixture.repo,
                    require_committed=True,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
