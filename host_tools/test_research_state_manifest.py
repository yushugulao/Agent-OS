#!/usr/bin/env python3
"""Regression checks for the shared Reader/guest state manifest."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if __package__:
    from . import plain_ucore_reader
    from .research_state_manifest import (
        MANIFEST_RELATIVE_PATH,
        StateManifestError,
        fixture_state_names,
        load_manifest,
        parse_manifest_text,
        repo_state_names,
        repository_state_inventory,
        short_name_map,
        target_state_names,
        validate_repository_state_contract,
    )
else:
    import plain_ucore_reader
    from research_state_manifest import (
        MANIFEST_RELATIVE_PATH,
        StateManifestError,
        fixture_state_names,
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


def test_manifest_mutations() -> None:
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

    duplicate_key = text.replace(
        '"schema_version": 1,',
        '"schema_version": 1,\n  "schema_version": 1,',
        1,
    )
    expect_error(duplicate_key, "duplicate manifest key")
    assert raw["schema_version"] == 1


def test_repository_contract() -> None:
    summary = validate_repository_state_contract(ROOT)
    assert summary["status"] == "ready", summary
    assert summary["plain_state_names"] >= 250, summary
    assert summary["agentos_state_names"] > summary["plain_state_names"], summary

    manifest = load_manifest(ROOT)
    plain = target_state_names(ROOT, manifest, "plain")
    agentos = target_state_names(ROOT, manifest, "agentos")
    inventory = repository_state_inventory(ROOT, manifest)
    fixture = fixture_state_names(ROOT / "host_tools/test_plain_ucore_reader.py")
    assert plain <= agentos
    assert fixture <= inventory
    assert "rp_evidence_packet" in plain
    assert "rp_evidence_packet" in fixture
    assert plain_ucore_reader.STATE_API_ALLOWLIST == frozenset(inventory)

    plain_map = short_name_map(
        plain,
        excluded_names=manifest.host_state_files,
    )
    assert plain_map["rp_evidence_pa"] == "rp_evidence_packet"


def test_only_state_file_operands_enter_inventory() -> None:
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


def test_ambiguous_guest_names_fail_closed() -> None:
    try:
        short_name_map(
            {"rp_manifest_collision_one", "rp_manifest_collision_two"}
        )
    except StateManifestError as error:
        assert "prefixes are ambiguous" in str(error), error
    else:
        raise AssertionError("ambiguous guest state filenames were accepted")


def test_import_modes() -> None:
    cases = (
        (
            ROOT,
            "from host_tools import plain_ucore_fs_extract, plain_ucore_reader; "
            "from host_tools.agent_metadata_disk_format import load_contract; "
            "load_contract()",
        ),
        (
            ROOT / "host_tools",
            "import plain_ucore_fs_extract, plain_ucore_reader; "
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
        test_manifest_mutations()

    def test_repository_contract(self) -> None:
        test_repository_contract()

    def test_only_state_file_operands_enter_inventory(self) -> None:
        test_only_state_file_operands_enter_inventory()

    def test_ambiguous_guest_names_fail_closed(self) -> None:
        test_ambiguous_guest_names_fail_closed()

    def test_import_modes(self) -> None:
        test_import_modes()


if __name__ == "__main__":
    unittest.main(verbosity=2)
