#!/usr/bin/env python3
"""Mutation tests for GitLab trace, ZIP, and job-attestation evidence."""
from __future__ import annotations

import copy
import io
import json
import stat
import tempfile
import unittest
import warnings
import zipfile
from dataclasses import replace
from pathlib import Path

from remote_ci_evidence import (
    CIIdentity,
    RemoteCIEvidenceError,
    _ci_identity,
    _run_git,
    build_attestation,
    canonical_json,
    marker_for,
)
from remote_ci_archive import RemoteJobExpectation, verify_downloaded_job_evidence
from agent_observe_disk_fixture import build_fixture as build_observe_fixture


ROOT = Path(__file__).resolve().parents[1]
COMMIT = _run_git(ROOT, "rev-parse", "HEAD")
JOB = "physical-resource-regression"


def physical_raw_lines(initial: int = 2, limit: int = 8) -> list[str]:
    records = (
        [(0, 2, 0)]
        + [(0, 0, 0)] * 4
        + [(-1, 0, 0)] * 3
        + [(0, 1, 1), (0, 0, 0), (0, initial, limit)]
        + [(0, 0, 0)] * 5
        + [(0, limit, limit), (-1, 0, 0), (0, 2, 0), (-1, 0, 0)]
        + [(0, 3, 0), (-1, 0, 0), (0, 0, 3), (-1, 0, 0)]
        + [(0, 0, 0), (0, initial, limit), (0, 0, 0), (0, limit, limit)]
        + [(0, 0, 0), (0, initial, limit)]
    )
    return [
        "physicalresource_ucore: raw "
        f"step={step} result={result} value0={value0} value1={value1}"
        for step, (result, value0, value1) in enumerate(records, 1)
    ]

class RemoteCIEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.artifacts = self.root / "ci-artifacts"
        self.artifacts.mkdir()
        job = b"build output\n[physical-resource] all checks passed\n"
        guest_body = "\n".join(
            [
                "physicalresource_ucore: brk_atomic=1 fork_inherit=1 shrink_refund=1 guard=1",
                "physicalresource_ucore: legacy_mail_accounting=1 alloc_delta=2 exit_delta=0",
                *physical_raw_lines(),
                "physicalresource_ucore: physical_transfer_rejected=1 mixed_atomic=1",
                "physicalresource_ucore: reserved_promise_lifecycle=1 promised=8 limit=8",
                "physicalresource_ucore: reserved_domain_fairness=1 pressure_pages=1 pressure_pipes=0 physical_usage=8 physical_limit=8",
                "physicalresource_ucore: reserved_domain_refund=1",
                "physicalresource_ucore: domain_isolation=1",
                "physicalresource_ucore: system_reserve=1",
                "physicalresource_ucore: teardown_refund=1",
                "physicalresource_ucore: parent passed",
            ]
        ).encode("ascii") + b"\n"
        guest = (
            b"===== guest:physical-resource =====\n"
            + guest_body
            + b"===== end-guest:physical-resource =====\n"
        )
        combined = (
            b"===== runner-stdout:physical-resource =====\n"
            + job
            + b"\n===== runner-guest-logs:physical-resource =====\n"
            + guest
        )
        self.files = {
            "physical-resource-job.log": job,
            "physical-resource-guest.log": guest,
            "physical-resource-combined.log": combined,
        }
        for name, data in self.files.items():
            (self.artifacts / name).write_bytes(data)
        self.identity = CIIdentity(
            project_id=31,
            project_path="team/agentos",
            pipeline_id=41,
            pipeline_source="push",
            job_id=51,
            job_name=JOB,
            commit=COMMIT,
            ref="main",
            runner_id=61,
            runner_tags=("agentos-qemu-calibrated", "linux"),
        )
        self.expectation = RemoteJobExpectation(
            project_id=31,
            project_path="team/agentos",
            pipeline_id=41,
            pipeline_source="push",
            job_id=51,
            job_name=JOB,
            commit=COMMIT,
            ref="main",
            runner_id=61,
            runner_tag="agentos-qemu-calibrated",
        )
        self.attestation = build_attestation(
            self.identity, self.artifacts, ROOT
        )[0]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_archive(
        self,
        attestation: bytes | None = None,
        files: dict[str, bytes] | None = None,
        extras: list[tuple[zipfile.ZipInfo | str, bytes]] | None = None,
    ) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in (self.files if files is None else files).items():
                archive.writestr(f"ci-artifacts/{name}", data)
            if attestation is not None:
                archive.writestr("ci-artifacts/remote-ci-attestation.json", attestation)
            for name, data in extras or []:
                archive.writestr(name, data)
        return output.getvalue()

    def proof(self, attestation: object | None = None) -> tuple[bytes, bytes]:
        value = self.attestation if attestation is None else attestation
        raw = canonical_json(value)
        marker = marker_for(value) + "\n"
        return marker.encode("ascii"), self.make_archive(raw)

    def assert_rejected(self, trace: bytes, archive: bytes,
                        expectation: RemoteJobExpectation | None = None) -> None:
        with self.assertRaises(RemoteCIEvidenceError):
            verify_downloaded_job_evidence(
                trace, archive, expectation or self.expectation, ROOT
            )

    def test_real_zip_attestation_round_trip(self) -> None:
        trace, archive = self.proof()
        result = verify_downloaded_job_evidence(
            trace, archive, self.expectation, ROOT
        )
        self.assertEqual(result["status"], "execution-attested")
        self.assertEqual(result["job"], JOB)
        self.assertEqual(result["artifact_count"], 3)

    def test_fake_trace_and_marker_mutations_are_rejected(self) -> None:
        trace, archive = self.proof()
        marker = trace.rstrip(b"\n")
        mutations = (
            b"job physical-resource-regression trace passed\n",
            b"",
            marker + b"\n" + marker + b"\n",
            trace.replace(b"sha256=", b"sha256=" + b"0" * 64 + b" #"),
            trace.replace(JOB.encode(), b"virtio-disk-regression"),
            trace.replace(COMMIT.encode(), b"2" * 40),
        )
        for mutation in mutations:
            with self.subTest(trace=mutation[:80]):
                self.assert_rejected(mutation, archive)

    def test_invalid_and_unsafe_zip_mutations_are_rejected(self) -> None:
        trace, archive = self.proof()
        symlink = zipfile.ZipInfo("ci-artifacts/link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            duplicate = self.make_archive(
                canonical_json(self.attestation),
                extras=[("ci-artifacts/physical-resource-job.log", b"duplicate")],
            )
        mutations = (
            b"PK fixture artifact",
            archive[:24],
            self.make_archive(None),
            self.make_archive(canonical_json(self.attestation), extras=[("../escape", b"x")]),
            self.make_archive(canonical_json(self.attestation), extras=[("ci-artifacts\\evil", b"x")]),
            self.make_archive(canonical_json(self.attestation), extras=[("/absolute", b"x")]),
            duplicate,
            self.make_archive(canonical_json(self.attestation), extras=[(symlink, b"target")]),
            self.make_archive(
                canonical_json(self.attestation),
                extras=[("ci-artifacts/bomb.bin", b"0" * (2 * 1024 * 1024))],
            ),
            self.make_archive(canonical_json(self.attestation), extras=[("ci-artifacts/extra.log", b"x")]),
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_rejected(trace, mutation)

    def test_attestation_schema_identity_and_strict_json_mutations_are_rejected(self) -> None:
        trace, _archive = self.proof()
        values: list[object] = []
        extra = copy.deepcopy(self.attestation)
        extra["unexpected"] = True
        values.append(extra)
        stale = copy.deepcopy(self.attestation)
        stale["identity"]["job_id"] = 999
        values.append(stale)
        wrong_source = copy.deepcopy(self.attestation)
        wrong_source["source_contract"]["files"][0]["sha256"] = "0" * 64
        values.append(wrong_source)
        wrong_semantic = copy.deepcopy(self.attestation)
        wrong_semantic["semantic"]["status"] = "claimed"
        values.append(wrong_semantic)
        wrong_hash = copy.deepcopy(self.attestation)
        wrong_hash["artifacts"][0]["sha256"] = "0" * 64
        values.append(wrong_hash)
        for index, value in enumerate(values):
            raw = canonical_json(value)
            value_trace = (marker_for(value) + "\n").encode("ascii")
            with self.subTest(index=index):
                self.assert_rejected(value_trace, self.make_archive(raw))

        raw = canonical_json(self.attestation)
        duplicate = raw.replace(b'{"artifact_contract":', b'{"schema_version":1,"artifact_contract":', 1)
        nan = raw.replace(b'"job_id":51', b'"job_id":NaN', 1)
        for index, malformed in enumerate((duplicate, nan)):
            digest_trace = (
                f"AGENTOS_REMOTE_CI_ATTESTATION_V1 job={JOB} commit={COMMIT} "
                f"sha256={__import__('hashlib').sha256(malformed).hexdigest()}\n"
            ).encode("ascii")
            with self.subTest(strict=index):
                self.assert_rejected(digest_trace, self.make_archive(malformed))

    def test_missing_changed_and_fake_semantic_artifacts_are_rejected(self) -> None:
        trace, _archive = self.proof()
        missing = dict(self.files)
        missing.pop("physical-resource-guest.log")
        changed = dict(self.files)
        changed["physical-resource-guest.log"] += b"tampered\n"
        fake_root = self.root / "fake"
        fake_root.mkdir()
        (fake_root / "physical-resource-job.log").write_text(
            "physical-resource passed\n", encoding="utf-8"
        )
        (fake_root / "physical-resource-guest.log").write_text("passed\n", encoding="utf-8")
        (fake_root / "physical-resource-combined.log").write_text("passed\n", encoding="utf-8")
        with self.assertRaises(RemoteCIEvidenceError):
            build_attestation(self.identity, fake_root, ROOT)
        for files in (missing, changed):
            with self.subTest(files=sorted(files)):
                self.assert_rejected(
                    trace,
                    self.make_archive(canonical_json(self.attestation), files=files),
                )

    def test_forged_fs_allocator_archive_is_rejected(self) -> None:
        for path in self.artifacts.iterdir():
            path.unlink()
        final = b"[fs-allocator-fault] dynamic matrix, negative mutant, and raw evidence passed\n"
        tag = b"fs-allocator:fixture:prepare"
        guest = b"===== guest:" + tag + b" =====\nfixture\n===== end-guest:" + tag + b" =====\n"
        files = {"fs-allocator-fault-job.log": final, "fs-allocator-fault-guest.log": guest,
                 "fs-allocator-fault-combined.log": b"===== runner-stdout:fs-allocator-fault =====\n" + final + b"\n===== runner-guest-logs:fs-allocator-fault =====\n" + guest,
                 "fs-allocator-evidence.tar": b"forged nonempty allocator archive"}
        for name, payload in files.items():
            (self.artifacts / name).write_bytes(payload)
        with self.assertRaisesRegex(RemoteCIEvidenceError, "allocator evidence archive"):
            build_attestation(replace(self.identity, job_name="fs-allocator-fault-regression"), self.artifacts, ROOT)

    def test_observation_remote_attestation_requires_full_acceptance(self) -> None:
        for path in self.artifacts.iterdir():
            path.unlink()
        short_image, durable_marker = build_observe_fixture()
        final = b"[observe-recovery] power-cut lease and three-boot durable evidence lifecycle passed\n"
        sections = (
            (
                "observe-recovery-boot0-cut",
                (
                    "agentobsreboot_ucore: lease_cut_alloc audit=1 span=2 event=3 control=4 agent=5 lifecycle_slot=6 lifecycle_generation=7",
                    "agentobsreboot_ucore: receipt_permission_not_agent=1",
                ),
            ),
            (
                "observe-recovery-boot1",
                (
                    "agentobsreboot_ucore: lease_cut_successor audit=11 span=12 event=13 control=14 agent=15 lifecycle_slot=6 lifecycle_generation=8",
                    durable_marker,
                    "agentobsreboot_ucore: receipt_pending_not_evidence=1 receipt_durable_exact=1 receipt_fake_stale=1 receipt_window_not_evidence=1",
                    "agentobsreboot_ucore: boot1_checkpoint_ready=1",
                ),
            ),
            (
                "observe-recovery-boot2",
                (
                    "agentobsreboot_ucore: receipt_teardown_stale=1",
                    "agentobsreboot_ucore: receipt_permission_recovery_denied=1",
                    "agentobsreboot_ucore: receipt_recovery_exact=1 receipt_v1_compatible=1 bank_generation_bound=1",
                    "agentobsreboot_ucore: boot2_reap_replicated=1",
                ),
            ),
            (
                "observe-recovery-boot3",
                (
                    "agentobsreboot_ucore: boot3_erased=1 generation_isolated=1 stable_identity=1",
                    "agentobsreboot_ucore: timeline_wait_epoch_recheck=1 injection=2 retries=1 bounded_timeout=1",
                    "agentobsreboot_ucore: timeline_wait_threads=1 filters=2 deadlines=2 targeted=1 timeout=1 cleanup=1",
                    "agentobsreboot_ucore: parent passed",
                ),
            ),
        )
        guest = b"".join(
            f"===== guest:{tag} =====\n".encode("ascii")
            + ("\n".join(lines) + "\n").encode("ascii")
            + f"===== end-guest:{tag} =====\n".encode("ascii")
            for tag, lines in sections
        )
        files = {
            "observe-recovery-job.log": final,
            "observe-recovery-guest.log": guest,
            "observe-recovery-combined.log": (
                b"===== runner-stdout:observe-recovery =====\n"
                + final
                + b"\n===== runner-guest-logs:observe-recovery =====\n"
                + guest
            ),
            "observe-recovery-before-reap.img": b"forged nonempty observation image",
        }
        identity = replace(self.identity, job_name="observe-recovery-regression")
        for label, image in (
            ("forged", b"forged nonempty observation image"),
            ("short", short_image),
        ):
            files["observe-recovery-before-reap.img"] = image
            for name, payload in files.items():
                (self.artifacts / name).write_bytes(payload)
            with self.subTest(image=label), self.assertRaisesRegex(
                RemoteCIEvidenceError, "semantic"
            ):
                build_attestation(identity, self.artifacts, ROOT)

        full_image, full_marker = build_observe_fixture(full_acceptance=True)
        for name in ("observe-recovery-guest.log", "observe-recovery-combined.log"):
            files[name] = files[name].replace(
                durable_marker.encode("ascii"), full_marker.encode("ascii")
            )
        files["observe-recovery-before-reap.img"] = full_image
        for name, payload in files.items():
            (self.artifacts / name).write_bytes(payload)
        attestation = build_attestation(identity, self.artifacts, ROOT)[0]
        self.assertEqual(attestation["semantic"]["status"], "passed")

    def test_symlink_artifact_is_rejected(self) -> None:
        target = self.artifacts / "physical-resource-guest.log"
        target.unlink()
        try:
            target.symlink_to(self.artifacts / "physical-resource-job.log")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(RemoteCIEvidenceError):
            build_attestation(self.identity, self.artifacts, ROOT)

    def test_dangerous_ci_environment_is_rejected(self) -> None:
        base = {
            "CI_JOB_NAME": JOB,
            "CI_COMMIT_SHA": COMMIT,
            "CI_PROJECT_PATH": "team/agentos",
            "CI_COMMIT_REF_NAME": "main",
            "CI_PIPELINE_SOURCE": "push",
            "CI_RUNNER_TAGS": '["agentos-qemu-calibrated"]',
            "CI_PROJECT_ID": "31",
            "CI_PIPELINE_ID": "41",
            "CI_JOB_ID": "51",
            "CI_RUNNER_ID": "61",
        }
        for name in ("BASH_ENV", "PYTHONPATH", "LD_PRELOAD", "MAKEFLAGS", "GIT_CONFIG_COUNT"):
            environment = dict(base)
            environment[name] = "/tmp/hijack"
            with self.subTest(name=name), self.assertRaises(RemoteCIEvidenceError):
                _ci_identity(JOB, environment)

    def test_remote_ci_modules_stay_within_maintenance_budgets(self) -> None:
        budgets = {
            "remote_ci_evidence.py": 650,
            "remote_ci_archive.py": 650,
            "remote_ci_job_semantics.py": 300,
            "remote_ci_bundle.py": 250,
            "remote_ci_test_fixture.py": 250,
        }
        for name, maximum in budgets.items():
            with self.subTest(module=name):
                lines = (ROOT / "host_tools" / name).read_text(encoding="utf-8").splitlines()
                self.assertLessEqual(len(lines), maximum)


if __name__ == "__main__":
    unittest.main(verbosity=2)
