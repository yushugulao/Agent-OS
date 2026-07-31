#!/usr/bin/env python3
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import plain_ucore_fs_extract as fsx
import test_plain_ucore_fs_extract as fs_fixture

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compatibility_overhead_contract import (  # noqa: E402
    BUILD_STAMP_SCHEMA,
    EVIDENCE_TIER,
    FORMAL_BOOT_COUNT,
    FORMAL_CONTEXT_SCHEMA,
    LIMITATIONS,
    SCHEMA,
    CompatibilityContractError,
    METRICS,
    _extract_make_variable,
    canonical_json_bytes,
    create_plan,
    guest_receipt,
    parse_guest_log,
    source_receipt,
    summarize_boots,
    validate_campaign,
    validate_plan,
    workload_outcome_sha256,
)
from compatibility_overhead import (  # noqa: E402
    CompatibilityRunError,
    _source_gate,
    _verify_gzip_archive,
    verify_campaign_artifacts,
)


COMMIT = "a" * 40


class CompatibilitySourceGateTests(unittest.TestCase):
    def test_info_exclude_cannot_hide_compatibility_build_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "os").mkdir()
            (root / "user" / "src").mkdir(parents=True)
            (root / "Makefile").write_text("all:\n\t@true\n", encoding="ascii")
            (root / "os" / "kernel.c").write_text("int kernel;\n", encoding="ascii")
            (root / "user" / "src" / "app.c").write_text(
                "int app;\n", encoding="ascii"
            )
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "compat@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Compatibility Test"],
                cwd=root,
                check=True,
            )
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "fixture"], cwd=root, check=True
            )
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            (root / ".git" / "info" / "exclude").write_text(
                "os/hidden.c\nuser/src/hidden.c\n", encoding="ascii"
            )
            for relative in ("os/hidden.c", "user/src/hidden.c"):
                hidden = root / relative
                hidden.write_text("int hidden;\n", encoding="ascii")
                self.assertEqual(
                    subprocess.check_output(
                        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                        cwd=root,
                    ),
                    b"",
                )
                with self.assertRaisesRegex(CompatibilityRunError, "source gate"):
                    _source_gate(
                        root,
                        commit,
                        "results/evaluation/runs/formal-fixture",
                        "before build",
                    )
                hidden.unlink()


def guest_log(challenge: str, elapsed_bias: int = 0) -> str:
    samples: list[dict[str, object]] = []
    lines = [
        f"compatbench: begin schema=1 challenge={challenge} "
        "clock=gettimeofday_ms rounds=3 source=canonical-v1"
    ]
    for round_number in range(1, 4):
        for metric_number, spec in enumerate(METRICS):
            elapsed = 10 + elapsed_bias + round_number + metric_number
            checksum = f"{(round_number * 0x1000 + metric_number):08x}"
            sample = {
                "challenge": challenge,
                "metric": spec["id"],
                "round": round_number,
                "operations": spec["operations"],
                "elapsed_ms": elapsed,
                "checksum": checksum,
            }
            samples.append(sample)
            lines.append(
                "compatbench: sample schema=1 "
                f"challenge={challenge} metric={spec['id']} round={round_number} "
                f"ops={spec['operations']} elapsed_ms={elapsed} checksum={checksum}"
            )
    receipt = guest_receipt(challenge, samples)
    lines.extend(
        (
            f"compatbench: done schema=1 challenge={challenge} "
            f"samples=12 receipt={receipt}",
            "compatbench: passed",
            "[runner] pass marker observed",
        )
    )
    return "\n".join(lines) + "\n"


def build_stamp(
    target: str,
    challenge: str,
    source_hash: str,
    *,
    source_commit: str = COMMIT,
) -> dict[str, object]:
    artifacts = {
        name: {"path": name, "bytes": 100, "sha256": "c" * 64}
        for name in (
            "compatbench_binary",
            "compatbench_elf",
            "filesystem_image",
            "kernel",
        )
    }
    artifacts["compatbench_binary"]["archive"] = {
        "path": "compatbench.bin",
        "bytes": 100,
        "sha256": "c" * 64,
    }
    artifacts["compatbench_elf"]["archive"] = {
        "path": "compatbench.elf",
        "bytes": 100,
        "sha256": "c" * 64,
    }
    for name, path in (("kernel", "kernel.gz"), ("filesystem_image", "fs-input.img.gz")):
        artifacts[name]["archive"] = {
            "path": path,
            "encoding": "gzip-mtime0",
            "bytes": 50,
            "sha256": "e" * 64,
            "uncompressed_bytes": 100,
            "uncompressed_sha256": "c" * 64,
        }
    return {
        "schema": BUILD_STAMP_SCHEMA,
        "target": target,
        "challenge": challenge,
        "source_commit": source_commit,
        "source_tracked_sha256": "d" * 64,
        "canonical_source_sha256": source_hash,
        "chapter": "compat_eval",
        "init_proc": "compatbench",
        "build_command": "make compatibility workload",
        "build_log": "build.log",
        "build_log_sha256": "e" * 64,
        "artifacts": artifacts,
    }


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def runtime_attestation(stamp: dict[str, object]) -> dict[str, object]:
    hashes = {
        name: artifact["sha256"] for name, artifact in stamp["artifacts"].items()
    }
    return {
        "launch_contract": "make-run-prebuilt-fixed-kernel-and-fs-paths",
        "pre_run_sha256": hashes,
        "post_run_sha256": dict(hashes),
        "immutable_runtime_artifacts_unchanged": True,
        "filesystem_expected_mutable": True,
    }


def source_identity(*, source_commit: str = COMMIT) -> dict[str, object]:
    return {
        "source_commit": source_commit,
        "source_tree_clean": True,
        "source_tracked_sha256": "d" * 64,
    }


def formal_context(
    *, micro_campaign_sha256: str = "9" * 64,
    platform_sha256: str = "8" * 64,
    environment_sha256: str = "7" * 64,
    tool_identities_sha256: str = "6" * 64,
    source_commit: str = COMMIT,
    micro_run_id: str | None = None,
    execution_domain: str = "native-linux",
) -> dict[str, object]:
    return {
        "schema": FORMAL_CONTEXT_SCHEMA,
        "micro_campaign_path": "campaign.json",
        "micro_campaign_sha256": micro_campaign_sha256,
        "micro_run_id": micro_run_id or f"formal-{source_commit}",
        "source_commit": source_commit,
        "clean_worktree": True,
        "phase": "collected",
        "formal_boot_count": FORMAL_BOOT_COUNT,
        "platform_sha256": platform_sha256,
        "environment_sha256": environment_sha256,
        "tool_identities_sha256": tool_identities_sha256,
        "execution_domain": execution_domain,
    }


def observer() -> dict[str, object]:
    return {
        "marker_seen": True,
        "failure_seen": False,
        "timed_out": False,
        "returncode": 0,
        "runner_terminated": True,
        "termination_mode": "observer_sigterm",
        "runner_signals": [15],
        "raw_returncode": -15,
        "elapsed_seconds": 1.25,
        "host_process_quiesced": True,
        "output_eof": True,
        "output_error": "",
        "wsl_cleanup_applicable": False,
        "wsl_cleanup_verified": True,
        "wsl_cleanup_initial_survivors": 0,
        "wsl_cleanup_remaining_survivors": 0,
        "wsl_cleanup_error": "",
    }


def target_result(
    target: str,
    challenge: str,
    guest: dict[str, object],
    stamp: dict[str, object],
) -> dict[str, object]:
    return {
        "status": "ready",
        "target": target,
        "challenge": challenge,
        "fresh_boot": True,
        "build_log": "build.log",
        "build_log_sha256": stamp["build_log_sha256"],
        "guest_log": "guest.log",
        "guest_log_sha256": "f" * 64,
        "build_stamp_path": "build-stamp.json",
        "guest": guest,
        "build_stamp": stamp,
        "runtime_artifact_attestation": runtime_attestation(stamp),
        "observer": observer(),
    }


def write_gzip(path: Path, data: bytes) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            compressed.write(data)


def compatibility_filesystem(binary: bytes) -> bytes:
    if not binary or len(binary) > fsx.BSIZE:
        raise ValueError("fixture compatibility binary must fit one block")
    image = bytearray(fsx.BSIZE * 64)
    fs_fixture.put_u32(image, fsx.BSIZE, fsx.FSMAGIC_LEGACY)
    fs_fixture.put_u32(image, fsx.BSIZE + 4, 64)
    fs_fixture.put_u32(image, fsx.BSIZE + 8, 48)
    fs_fixture.put_u32(image, fsx.BSIZE + 12, 32)
    fs_fixture.put_u32(image, fsx.BSIZE + 16, 2)
    fs_fixture.put_u32(image, fsx.BSIZE + 20, 15)
    directory = bytearray(fsx.BSIZE)
    fs_fixture.put_dirent(directory, 0, 2, "compatbench")
    image[20 * fsx.BSIZE : 21 * fsx.BSIZE] = directory
    fs_fixture.put_inode(
        image, 1, 1, fsx.BSIZE, [20], fsx.DINODE_SIZE_LEGACY
    )
    image[21 * fsx.BSIZE : 21 * fsx.BSIZE + len(binary)] = binary
    fs_fixture.put_inode(
        image,
        2,
        fsx.T_FILE,
        len(binary),
        [21],
        fsx.DINODE_SIZE_LEGACY,
    )
    return bytes(image)


def materialize_compatibility_fixture(
    run_root: Path, repo: Path, micro_campaign: dict[str, object]
) -> Path:
    run = micro_campaign["run"]
    platform = micro_campaign["platform"]
    environment = micro_campaign["environment"]
    source_commit = str(run["commit"])
    compatibility_root = run_root / "compatibility"
    compatibility_root.mkdir()
    snapshot = compatibility_root / "source-snapshot"
    for relative in ("evaluation_guest", "user", "baseline_ucore/user"):
        (snapshot / relative).mkdir(parents=True, exist_ok=True)
    for relative in (
        "evaluation_guest/compatbench.c",
        "Makefile",
        "user/Makefile",
        "baseline_ucore/Makefile",
        "baseline_ucore/user/Makefile",
    ):
        shutil.copyfile(repo / relative, snapshot / relative)

    source = source_receipt(repo)
    plan = create_plan(source_commit)
    (compatibility_root / "plan.json").write_text(
        json.dumps(plan), encoding="utf-8"
    )
    (compatibility_root / "source-receipt.json").write_text(
        json.dumps(source), encoding="utf-8"
    )
    boots: list[dict[str, object]] = []
    for planned in plan["boots"]:
        challenge = str(planned["challenge"])
        boot_dir = compatibility_root / str(planned["boot_id"])
        boot_dir.mkdir()
        targets: dict[str, object] = {}
        for target, bias in (("plain", 0), ("agentos", 2)):
            target_dir = boot_dir / target
            target_dir.mkdir()
            build_data = f"build target={target} challenge={challenge}\n".encode()
            guest_data = guest_log(challenge, bias).encode()
            binary_data = f"bin:{target}:{challenge}".encode()
            elf_data = b"\x7fELF" + binary_data
            kernel_data = b"\x7fELF kernel\0compatbench\0" + challenge.encode()
            filesystem_data = compatibility_filesystem(binary_data)
            (target_dir / "build.log").write_bytes(build_data)
            (target_dir / "guest.log").write_bytes(guest_data)
            (target_dir / "compatbench.bin").write_bytes(binary_data)
            (target_dir / "compatbench.elf").write_bytes(elf_data)
            write_gzip(target_dir / "kernel.gz", kernel_data)
            write_gzip(target_dir / "fs-input.img.gz", filesystem_data)
            stamp = build_stamp(
                target,
                challenge,
                str(source["canonical_sha256"]),
                source_commit=source_commit,
            )
            stamp["build_log_sha256"] = sha256(build_data)
            for artifact_name, archive_name, raw_data, encoding in (
                ("compatbench_binary", "compatbench.bin", binary_data, "raw"),
                ("compatbench_elf", "compatbench.elf", elf_data, "raw"),
                ("kernel", "kernel.gz", kernel_data, "gzip-mtime0"),
                ("filesystem_image", "fs-input.img.gz", filesystem_data, "gzip-mtime0"),
            ):
                archive_data = (target_dir / archive_name).read_bytes()
                archive: dict[str, object] = {
                    "path": archive_name,
                    "bytes": len(archive_data),
                    "sha256": sha256(archive_data),
                }
                if encoding == "gzip-mtime0":
                    archive.update(
                        {
                            "encoding": encoding,
                            "uncompressed_bytes": len(raw_data),
                            "uncompressed_sha256": sha256(raw_data),
                        }
                    )
                stamp["artifacts"][artifact_name] = {
                    "path": artifact_name,
                    "bytes": len(raw_data),
                    "sha256": sha256(raw_data),
                    "archive": archive,
                }
            (target_dir / "build-stamp.json").write_text(
                json.dumps(stamp), encoding="utf-8"
            )
            result = target_result(
                target,
                challenge,
                parse_guest_log(guest_data.decode(), challenge),
                stamp,
            )
            result["build_log_sha256"] = sha256(build_data)
            result["guest_log_sha256"] = sha256(guest_data)
            result["runtime_artifact_attestation"] = runtime_attestation(stamp)
            targets[target] = result
        boot = {
            "boot_id": planned["boot_id"],
            "challenge": challenge,
            "target_order": planned["target_order"],
            "targets": targets,
        }
        boots.append(boot)
        (boot_dir / "boot-summary.json").write_text(
            json.dumps(boot), encoding="utf-8"
        )
    micro_manifest = run_root / "campaign.json"
    context = formal_context(
        micro_campaign_sha256=sha256(micro_manifest.read_bytes()),
        platform_sha256=sha256(canonical_json_bytes(platform)),
        environment_sha256=sha256(canonical_json_bytes(environment)),
        tool_identities_sha256=sha256(
            canonical_json_bytes(platform["tools"])
        ),
        source_commit=source_commit,
        micro_run_id=str(run["id"]),
        execution_domain=str(run["execution_domain"]),
    )
    campaign = {
        "schema": SCHEMA,
        "status": "ready",
        "formal_bundle_eligible": True,
        "evidence_tier": EVIDENCE_TIER,
        "limitations": list(LIMITATIONS),
        "formal_context": context,
        "source_identity": source_identity(source_commit=source_commit),
        "source": source,
        "plan": plan,
        "boots": boots,
        "summary": summarize_boots(boots),
    }
    validate_campaign(campaign)
    summary_path = compatibility_root / "compatibility-overhead.json"
    summary_path.write_text(json.dumps(campaign), encoding="utf-8")
    return summary_path


class CompatibilityOverheadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = Path(__file__).resolve().parents[1]
        cls.source = source_receipt(cls.repo)

    def test_both_targets_compile_one_canonical_source(self) -> None:
        self.assertTrue(self.source["single_canonical_source"])
        self.assertFalse(self.source["target_specific_guest_branches"])
        self.assertEqual(
            {row["resolved_source"] for row in self.source["target_bindings"]},
            {"evaluation_guest/compatbench.c"},
        )
        self.assertEqual(
            {row["target"] for row in self.source["target_bindings"]},
            {"plain", "agentos"},
        )

    def test_make_path_binding_expansion_is_closed_and_unique(self) -> None:
        text = (
            "COMPAT_BENCH_REPO_SOURCE := evaluation_guest/compatbench.c\n"
            "COMPAT_BENCH_SOURCE := ../$(COMPAT_BENCH_REPO_SOURCE)\n"
        )
        self.assertEqual(
            _extract_make_variable(text, "COMPAT_BENCH_SOURCE"),
            "../evaluation_guest/compatbench.c",
        )
        for invalid in (
            text + "COMPAT_BENCH_SOURCE := ../alternate.c\n",
            "COMPAT_BENCH_SOURCE := $(MISSING)\n",
            "COMPAT_BENCH_SOURCE := $(COMPAT_BENCH_SOURCE)\n",
            "COMPAT_BENCH_SOURCE := $(shell-touch-pwned)\n",
        ):
            with self.assertRaises(CompatibilityContractError):
                _extract_make_variable(invalid, "COMPAT_BENCH_SOURCE")

    def test_plan_is_commit_bound_fixed_and_precommitted(self) -> None:
        plan = create_plan(COMMIT)
        validate_plan(plan)
        orders = [boot["target_order"] for boot in plan["boots"]]
        self.assertEqual(len(plan["boots"]), FORMAL_BOOT_COUNT)
        self.assertEqual(len(set(boot["challenge"] for boot in plan["boots"])), 7)
        self.assertTrue(all(left != right for left, right in zip(orders, orders[1:])))
        self.assertFalse(plan["target_order_balanced"])
        self.assertEqual(plan["target_order_max_count_difference"], 1)
        self.assertEqual(sorted(plan["target_order_counts"].values()), [3, 4])
        self.assertTrue(plan["optional_stopping_forbidden"])
        self.assertEqual(plan, create_plan(COMMIT))
        self.assertNotEqual(plan, create_plan("b" * 40))
        tampered = copy.deepcopy(plan)
        tampered["boots"][0]["challenge"] = "1" * 16
        with self.assertRaisesRegex(CompatibilityContractError, "differs"):
            validate_plan(tampered)

    def test_parser_accepts_only_complete_bound_raw_stream(self) -> None:
        challenge = "1234567890abcdef"
        parsed = parse_guest_log(guest_log(challenge), challenge)
        self.assertEqual(parsed["sample_count"], 12)
        self.assertEqual(
            [(item["round"], item["metric"]) for item in parsed["samples"][:5]],
            [
                (1, "fork_wait"),
                (1, "fork_exec_wait"),
                (1, "pipe_roundtrip"),
                (1, "seq_file_io"),
                (2, "fork_wait"),
            ],
        )
        with self.assertRaisesRegex(CompatibilityContractError, "Host plan"):
            parse_guest_log(guest_log(challenge), "fedcba0987654321")
        with self.assertRaisesRegex(CompatibilityContractError, "once"):
            parse_guest_log(guest_log(challenge) + "compatbench: passed\n", challenge)
        with self.assertRaisesRegex(CompatibilityContractError, "no compatibility records"):
            parse_guest_log("kernel booted\npassed\n", challenge)

    def test_parser_rejects_operation_and_receipt_rewrites(self) -> None:
        challenge = "1234567890abcdef"
        changed_ops = guest_log(challenge).replace(
            "metric=fork_wait round=1 ops=32", "metric=fork_wait round=1 ops=31", 1
        )
        with self.assertRaisesRegex(CompatibilityContractError, "operation count"):
            parse_guest_log(changed_ops, challenge)
        changed_elapsed = guest_log(challenge).replace("elapsed_ms=11", "elapsed_ms=12", 1)
        with self.assertRaisesRegex(CompatibilityContractError, "receipt"):
            parse_guest_log(changed_elapsed, challenge)

    def test_gzip_replay_rejects_trailing_members_and_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "artifact.gz"
            payload = b"bound-payload"
            write_gzip(path, payload)
            with path.open("ab") as handle:
                handle.write(gzip.compress(b"second-member", mtime=0))
            compressed = path.read_bytes()
            artifact = {"bytes": len(payload), "sha256": sha256(payload)}
            archive = {
                "encoding": "gzip-mtime0",
                "bytes": len(compressed),
                "sha256": sha256(compressed),
                "uncompressed_bytes": len(payload),
                "uncompressed_sha256": sha256(payload),
            }
            with self.assertRaisesRegex(CompatibilityRunError, "trailing"):
                _verify_gzip_archive(path, archive, artifact)

            write_path = Path(temporary) / "large.gz"
            write_gzip(write_path, b"12345")
            compressed = write_path.read_bytes()
            artifact = {"bytes": 5, "sha256": sha256(b"12345")}
            archive = {
                "encoding": "gzip-mtime0",
                "bytes": len(compressed),
                "sha256": sha256(compressed),
                "uncompressed_bytes": 5,
                "uncompressed_sha256": sha256(b"12345"),
            }
            with mock.patch(
                "compatibility_overhead.MAX_RUNTIME_ARTIFACT_BYTES", 4
            ):
                with self.assertRaisesRegex(CompatibilityRunError, "expands"):
                    _verify_gzip_archive(write_path, archive, artifact)

    def test_campaign_keeps_metrics_separate_and_revalidates_samples(self) -> None:
        plan = create_plan(COMMIT)
        boots: list[dict[str, object]] = []
        for planned in plan["boots"]:
            challenge = str(planned["challenge"])
            targets: dict[str, object] = {}
            for target, bias in (("plain", 0), ("agentos", 2)):
                stamp = build_stamp(
                    target, challenge, str(self.source["canonical_sha256"])
                )
                targets[target] = target_result(
                    target,
                    challenge,
                    parse_guest_log(guest_log(challenge, bias), challenge),
                    stamp,
                )
            boots.append(
                {
                    "boot_id": planned["boot_id"],
                    "challenge": challenge,
                    "target_order": planned["target_order"],
                    "targets": targets,
                }
            )
        summary = summarize_boots(boots)
        self.assertIsNone(summary["aggregate_score"])
        self.assertTrue(summary["aggregate_score_forbidden"])
        self.assertEqual(set(summary["metrics"]), {item["id"] for item in METRICS})
        campaign = {
            "schema": SCHEMA,
            "status": "ready",
            "formal_bundle_eligible": True,
            "evidence_tier": EVIDENCE_TIER,
            "limitations": list(LIMITATIONS),
            "formal_context": formal_context(),
            "source_identity": source_identity(),
            "source": self.source,
            "plan": plan,
            "boots": boots,
            "summary": summary,
        }
        validate_campaign(campaign)
        tampered = copy.deepcopy(campaign)
        tampered["boots"][0]["targets"]["plain"]["guest"]["samples"][0][
            "elapsed_ms"
        ] += 1
        with self.assertRaisesRegex(CompatibilityContractError, "rewritten"):
            validate_campaign(tampered)

        observer_tamper = copy.deepcopy(campaign)
        observer_tamper["boots"][0]["targets"]["plain"]["observer"][
            "wsl_cleanup_initial_survivors"
        ] = 1
        with self.assertRaisesRegex(CompatibilityContractError, "observer"):
            validate_campaign(observer_tamper)

        natural_exit = copy.deepcopy(campaign)
        natural_observer = natural_exit["boots"][0]["targets"]["plain"]["observer"]
        natural_observer.update(
            {
                "runner_terminated": False,
                "termination_mode": "natural_exit",
                "runner_signals": [],
                "raw_returncode": 0,
            }
        )
        validate_campaign(natural_exit)

        nonzero_exit = copy.deepcopy(natural_exit)
        failed_observer = nonzero_exit["boots"][0]["targets"]["plain"]["observer"]
        failed_observer["returncode"] = 7
        failed_observer["raw_returncode"] = 7
        with self.assertRaisesRegex(CompatibilityContractError, "observer"):
            validate_campaign(nonzero_exit)

        outcome_tamper = copy.deepcopy(campaign)
        guest = outcome_tamper["boots"][0]["targets"]["plain"]["guest"]
        guest["samples"][0]["checksum"] = "deadbeef"
        guest["receipt"] = guest_receipt(guest["challenge"], guest["samples"])
        guest["workload_outcome_sha256"] = workload_outcome_sha256(
            guest["challenge"], guest["samples"]
        )
        with self.assertRaisesRegex(CompatibilityContractError, "outcomes"):
            validate_campaign(outcome_tamper)

    def test_artifact_replay_binds_raw_logs_stamps_and_archived_elf(self) -> None:
        plan = create_plan(COMMIT)
        with tempfile.TemporaryDirectory() as temporary:
            evaluation_root = Path(temporary)
            root = evaluation_root / "compatibility"
            root.mkdir()
            snapshot = root / "source-snapshot"
            for relative in (
                "evaluation_guest",
                "user",
                "baseline_ucore/user",
            ):
                (snapshot / relative).mkdir(parents=True, exist_ok=True)
            for relative in (
                "evaluation_guest/compatbench.c",
                "Makefile",
                "user/Makefile",
                "baseline_ucore/Makefile",
                "baseline_ucore/user/Makefile",
            ):
                shutil.copyfile(self.repo / relative, snapshot / relative)
            platform = {"tools": {"make": {"sha256": "1" * 64}}}
            environment = {"make": {"sha256": "1" * 64}}
            micro_campaign = {
                "phase": "collected",
                "run": {
                    "id": f"formal-{COMMIT}",
                    "commit": COMMIT,
                    "clean_worktree": True,
                    "execution_domain": "native-linux",
                },
                "platform": platform,
                "environment": environment,
                "boots": [{} for _ in range(FORMAL_BOOT_COUNT)],
            }
            micro_manifest = evaluation_root / "campaign.json"
            micro_manifest.write_text(json.dumps(micro_campaign), encoding="utf-8")
            context = formal_context(
                micro_campaign_sha256=sha256(micro_manifest.read_bytes()),
                platform_sha256=sha256(
                    json.dumps(
                        platform,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode("ascii")
                ),
                environment_sha256=sha256(
                    json.dumps(
                        environment,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode("ascii")
                ),
                tool_identities_sha256=sha256(
                    json.dumps(
                        platform["tools"],
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode("ascii")
                ),
            )
            boots: list[dict[str, object]] = []
            (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            (root / "source-receipt.json").write_text(
                json.dumps(self.source), encoding="utf-8"
            )
            for planned in plan["boots"]:
                challenge = str(planned["challenge"])
                boot_dir = root / str(planned["boot_id"])
                boot_dir.mkdir()
                targets: dict[str, object] = {}
                for target, bias in (("plain", 0), ("agentos", 2)):
                    target_dir = boot_dir / target
                    target_dir.mkdir()
                    build_data = f"build target={target} challenge={challenge}\n".encode()
                    guest_data = guest_log(challenge, bias).encode()
                    binary_data = f"bin:{target}:{challenge}".encode()
                    elf_data = f"elf:{target}:{challenge}".encode()
                    kernel_data = (
                        b"\x7fELF synthetic-kernel\0compatbench\0" + challenge.encode()
                    )
                    filesystem_data = binary_data
                    (target_dir / "build.log").write_bytes(build_data)
                    (target_dir / "guest.log").write_bytes(guest_data)
                    (target_dir / "compatbench.bin").write_bytes(binary_data)
                    (target_dir / "compatbench.elf").write_bytes(elf_data)
                    write_gzip(target_dir / "kernel.gz", kernel_data)
                    write_gzip(target_dir / "fs-input.img.gz", filesystem_data)
                    stamp = build_stamp(
                        target, challenge, str(self.source["canonical_sha256"])
                    )
                    stamp["build_log_sha256"] = sha256(build_data)
                    stamp["artifacts"]["compatbench_binary"] = {
                        "path": "compatbench",
                        "bytes": len(binary_data),
                        "sha256": sha256(binary_data),
                        "archive": {
                            "path": "compatbench.bin",
                            "bytes": len(binary_data),
                            "sha256": sha256(binary_data),
                        },
                    }
                    stamp["artifacts"]["compatbench_elf"] = {
                        "path": "compatbench",
                        "bytes": len(elf_data),
                        "sha256": sha256(elf_data),
                        "archive": {
                            "path": "compatbench.elf",
                            "bytes": len(elf_data),
                            "sha256": sha256(elf_data),
                        },
                    }
                    for artifact_name, archive_name, raw_data in (
                        ("kernel", "kernel.gz", kernel_data),
                        ("filesystem_image", "fs-input.img.gz", filesystem_data),
                    ):
                        archive_path = target_dir / archive_name
                        archive_data = archive_path.read_bytes()
                        stamp["artifacts"][artifact_name] = {
                            "path": artifact_name,
                            "bytes": len(raw_data),
                            "sha256": sha256(raw_data),
                            "archive": {
                                "path": archive_name,
                                "encoding": "gzip-mtime0",
                                "bytes": len(archive_data),
                                "sha256": sha256(archive_data),
                                "uncompressed_bytes": len(raw_data),
                                "uncompressed_sha256": sha256(raw_data),
                            },
                        }
                    (target_dir / "build-stamp.json").write_text(
                        json.dumps(stamp), encoding="utf-8"
                    )
                    result = target_result(
                        target,
                        challenge,
                        parse_guest_log(guest_data.decode(), challenge),
                        stamp,
                    )
                    result["build_log_sha256"] = sha256(build_data)
                    result["guest_log_sha256"] = sha256(guest_data)
                    result["runtime_artifact_attestation"] = runtime_attestation(stamp)
                    targets[target] = result
                boot = {
                    "boot_id": planned["boot_id"],
                    "challenge": challenge,
                    "target_order": planned["target_order"],
                    "targets": targets,
                }
                boots.append(boot)
                (boot_dir / "boot-summary.json").write_text(
                    json.dumps(boot), encoding="utf-8"
                )
            campaign = {
                "schema": SCHEMA,
                "status": "ready",
                "formal_bundle_eligible": True,
                "evidence_tier": EVIDENCE_TIER,
                "limitations": list(LIMITATIONS),
                "formal_context": context,
                "source_identity": source_identity(),
                "source": self.source,
                "plan": plan,
                "boots": boots,
                "summary": summarize_boots(boots),
            }
            summary = root / "compatibility-overhead.json"
            summary.write_text(json.dumps(campaign), encoding="utf-8")
            with mock.patch(
                "compatibility_overhead.extract_compatbench_from_image",
                side_effect=lambda image: image,
            ):
                verify_campaign_artifacts(summary, micro_manifest=micro_manifest)
                micro_bytes = micro_manifest.read_bytes()
                micro_manifest.write_bytes(micro_bytes + b"\n")
                with self.assertRaisesRegex(
                    CompatibilityRunError, "micro campaign hash"
                ):
                    verify_campaign_artifacts(
                        summary, micro_manifest=micro_manifest
                    )
                micro_manifest.write_bytes(micro_bytes)
                guest_path = root / "compat-01/plain/guest.log"
                guest_path.write_text(guest_path.read_text() + "tampered\n")
                with self.assertRaisesRegex(CompatibilityRunError, "Guest log hash"):
                    verify_campaign_artifacts(summary, micro_manifest=micro_manifest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
