#!/usr/bin/env python3
"""Regression checks for the shared Guest state manifest."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if __package__:
    from .research_state_manifest import (
        MANIFEST_RELATIVE_PATH,
        StateManifestError,
        archive_state_names,
        guest_state_inventory_sha256,
        load_manifest,
        parse_manifest_text,
        repo_state_names,
        repository_state_inventory,
        short_name_map,
        target_state_names,
        validate_repository_state_contract,
    )
else:
    from research_state_manifest import (
        MANIFEST_RELATIVE_PATH,
        StateManifestError,
        archive_state_names,
        guest_state_inventory_sha256,
        load_manifest,
        parse_manifest_text,
        repo_state_names,
        repository_state_inventory,
        short_name_map,
        target_state_names,
        validate_repository_state_contract,
    )


ROOT = Path(__file__).resolve().parents[1]


def expect_error(text: str, fragment: str) -> None:
    try:
        parse_manifest_text(text)
    except StateManifestError as error:
        assert fragment in str(error), error
    else:
        raise AssertionError(f"invalid state manifest accepted: {fragment}")


def check_manifest_mutations() -> None:
    path = ROOT / MANIFEST_RELATIVE_PATH
    text = path.read_text(encoding="utf-8")
    raw = json.loads(text)

    missing = json.loads(text)
    del missing["targets"]["plain"]
    expect_error(json.dumps(missing), "missing=plain")

    unknown = json.loads(text)
    unknown["unexpected_policy"] = True
    expect_error(json.dumps(unknown), "unknown keys")

    unknown_target = json.loads(text)
    unknown_target["targets"]["legacy"] = unknown_target["targets"]["plain"]
    expect_error(json.dumps(unknown_target), "unknown=legacy")

    duplicate_call = json.loads(text)
    duplicate_call["state_file_calls"].append(duplicate_call["state_file_calls"][0])
    expect_error(json.dumps(duplicate_call), "duplicate entries")

    missing_call = json.loads(text)
    missing_call["state_file_calls"].remove("rp_write_file")
    expect_error(json.dumps(missing_call), "missing core operations")

    unknown_call = json.loads(text)
    unknown_call["state_file_calls"].append("accept_any_rp_symbol")
    expect_error(json.dumps(unknown_call), "unsupported entries")

    missing_archive_optional = json.loads(text)
    del missing_archive_optional["archive_optional_state_files"]
    expect_error(json.dumps(missing_archive_optional), "missing keys")

    duplicate_archive_optional = json.loads(text)
    duplicate_archive_optional["archive_optional_state_files"].append(
        duplicate_archive_optional["archive_optional_state_files"][0]
    )
    expect_error(json.dumps(duplicate_archive_optional), "duplicate entries")

    overlapping_archive_optional = json.loads(text)
    overlapping_archive_optional["archive_optional_state_files"].append(
        overlapping_archive_optional["host_state_files"][0]
    )
    expect_error(json.dumps(overlapping_archive_optional), "inventories overlap")

    missing_opaque = json.loads(text)
    del missing_opaque["opaque_guest_state_files"]
    expect_error(json.dumps(missing_opaque), "missing keys")

    duplicate_opaque = json.loads(text)
    duplicate_opaque["opaque_guest_state_files"].append(
        duplicate_opaque["opaque_guest_state_files"][0]
    )
    expect_error(json.dumps(duplicate_opaque), "duplicate entries")

    host_opaque = json.loads(text)
    host_opaque["opaque_guest_state_files"].append(
        host_opaque["host_state_files"][0]
    )
    expect_error(json.dumps(host_opaque), "opaque Guest and Host")

    duplicate_key = text.replace(
        '"schema_version": 4,',
        '"schema_version": 4,\n  "schema_version": 4,',
        1,
    )
    expect_error(duplicate_key, "duplicate manifest key")
    assert raw["schema_version"] == 4


def check_repository_contract() -> None:
    summary = validate_repository_state_contract(ROOT)
    assert summary["status"] == "ready", summary
    assert summary["plain_state_names"] >= 250, summary
    assert summary["agentos_state_names"] > summary["plain_state_names"], summary

    manifest = load_manifest(ROOT)
    plain = target_state_names(ROOT, manifest, "plain")
    agentos = target_state_names(ROOT, manifest, "agentos")
    plain_archive = archive_state_names(ROOT, manifest, "plain")
    agentos_archive = archive_state_names(ROOT, manifest, "agentos")
    inventory = repository_state_inventory(ROOT, manifest)
    retired_reader_state = {"rp_llm_conclusions", "rp_metrics", "rp_review_pack"}
    assert plain <= agentos
    assert "rp_evidence_packet" in plain
    assert "rp_evidence_packet" in inventory
    assert not retired_reader_state & inventory
    assert not retired_reader_state & plain_archive
    assert not retired_reader_state & agentos_archive
    assert set(manifest.archive_optional_state_files) == {
        "rp_input_fastq", "rp_object_records"
    }
    assert set(manifest.opaque_guest_state_files) == {
        "rp_task6_norm", "rp_task6_raw"
    }
    for target, derived, archive in (
        ("plain", plain, plain_archive), ("agentos", agentos, agentos_archive)
    ):
        assert archive == (
            derived
            - set(manifest.host_state_files)
            - set(manifest.archive_optional_state_files)
        ), target
        assert not set(manifest.archive_optional_state_files) & archive, target
    plain_map = short_name_map(
        plain,
        excluded_names=manifest.host_state_files,
    )
    assert plain_map["rp_evidence_pa"] == "rp_evidence_packet"


def check_only_state_file_operands_enter_inventory() -> None:
    with tempfile.TemporaryDirectory(prefix="state-manifest-source-") as tmp:
        repo = Path(tmp)
        source = repo / "user/src"
        include = repo / "user/include"
        source.mkdir(parents=True)
        include.mkdir(parents=True)
        (source / "fixture.c").write_text(
            "int rp_evidence_parse_program_record(void) { return 0; }\n"
            'int f(void) { return rp_write_file("rp_evidence_packet", "status=ready\\n"); }\n'
            '// rp_write_file("rp_commented_out", "status=ready\\n");\n',
            encoding="utf-8",
        )
        (include / "fixture.h").write_text("/* fixture */\n", encoding="utf-8")
        names = repo_state_names(repo, ROOT)
        assert names == {"rp_evidence_packet"}, names
        mapping = short_name_map(names)
        assert mapping == {"rp_evidence_pa": "rp_evidence_packet"}, mapping


def check_ambiguous_guest_names_fail_closed() -> None:
    try:
        short_name_map(
            {"rp_manifest_collision_one", "rp_manifest_collision_two"}
        )
    except StateManifestError as error:
        assert "prefixes are ambiguous" in str(error), error
    else:
        raise AssertionError("ambiguous guest state filenames were accepted")


def check_guest_state_digest_binds_inventory_and_contents() -> None:
    with tempfile.TemporaryDirectory(prefix="guest-state-digest-") as tmp:
        state_dir = Path(tmp)
        (state_dir / "rp_alpha").write_bytes(b"alpha\n")
        (state_dir / "rp_beta").write_bytes(b"beta\x00payload")
        (state_dir / "rp_host_run_result").write_bytes(b"ignored\n")
        first = guest_state_inventory_sha256(
            state_dir, excluded_names={"rp_host_run_result"}
        )
        second = guest_state_inventory_sha256(
            state_dir, excluded_names={"rp_host_run_result"}
        )
        assert first == second
        assert first[0] == 2
        assert len(first[1]) == 64

        (state_dir / "rp_alpha").write_bytes(b"omega\n")
        content_changed = guest_state_inventory_sha256(
            state_dir, excluded_names={"rp_host_run_result"}
        )
        assert content_changed[0] == first[0]
        assert content_changed[1] != first[1]

        (state_dir / "rp_gamma").write_bytes(b"")
        inventory_changed = guest_state_inventory_sha256(
            state_dir, excluded_names={"rp_host_run_result"}
        )
        assert inventory_changed[0] == 3
        assert inventory_changed[1] != content_changed[1]

        unsafe = state_dir / "rp_unsafe"
        try:
            unsafe.symlink_to(state_dir / "rp_alpha")
        except OSError:
            return
        # Some MSYS Python builds emulate symlink_to() by copying the target.
        # A regular copy is not a link traversal fixture, so skip it explicitly.
        if not unsafe.is_symlink():
            return
        try:
            guest_state_inventory_sha256(
                state_dir, excluded_names={"rp_host_run_result"}
            )
        except StateManifestError as error:
            assert "unsafe" in str(error), error
        else:
            raise AssertionError("Guest state digest followed a symlink")


def check_import_modes() -> None:
    cases = (
        (
            ROOT,
            "from host_tools import plain_ucore_fs_extract; "
            "from host_tools.agent_metadata_disk_format import load_contract; "
            "load_contract()",
        ),
        (
            ROOT / "host_tools",
            "import plain_ucore_fs_extract; "
            "import research_state_manifest; from pathlib import Path; "
            "research_state_manifest.validate_repository_state_contract("
            "Path.cwd().parent)",
        ),
    )
    for cwd, source in cases:
        completed = subprocess.run(
            [sys.executable, "-c", source],
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert completed.returncode == 0, (
            f"host tool import failed from {cwd}:\n{completed.stdout}\n{completed.stderr}"
        )


class ResearchStateManifestTests(unittest.TestCase):
    def test_manifest_mutations(self) -> None:
        check_manifest_mutations()

    def test_repository_contract(self) -> None:
        check_repository_contract()

    def test_only_state_file_operands_enter_inventory(self) -> None:
        check_only_state_file_operands_enter_inventory()

    def test_ambiguous_guest_names_fail_closed(self) -> None:
        check_ambiguous_guest_names_fail_closed()

    def test_guest_state_digest_binds_inventory_and_contents(self) -> None:
        check_guest_state_digest_binds_inventory_and_contents()

    def test_import_modes(self) -> None:
        check_import_modes()


if __name__ == "__main__":
    unittest.main(verbosity=2)
