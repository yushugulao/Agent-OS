#!/usr/bin/env python3
"""Mutation tests for dual-target demo/reference producers."""

from __future__ import annotations

import tempfile
from pathlib import Path

from reference_catalog_contract import ReferenceCatalogError, validate_reference_source


ROOT = Path(__file__).resolve().parents[1]
SOURCES = tuple(
    root / "user" / "src" / name
    for root in (ROOT, ROOT / "baseline_ucore")
    for name in ("rp_coherenceplane.c", "rp_decsupport.c")
)


def expect_rejected(path: Path) -> None:
    try:
        validate_reference_source(path)
    except ReferenceCatalogError:
        return
    raise AssertionError(f"invalid reference producer was accepted: {path.name}")


def main() -> int:
    for source in SOURCES:
        validate_reference_source(source)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for source in SOURCES:
            original = source.read_text(encoding="utf-8")
            mutations = {
                "role": original.replace(
                    '"evidence_file_role=demo_reference\\n"',
                    '"evidence_file_role=runtime_verified\\n"',
                    1,
                ),
                "generation": original.replace(
                    '"evidence_file_generation=demo_expected\\n"',
                    '"evidence_file_generation=runtime\\n"',
                    1,
                ),
                "status": original.replace(
                    '"evidence_file_status=reference_ready\\n")) {',
                    '"evidence_file_status=ready\\n")) {',
                    1,
                ),
                "duplicate-file-status": original.replace(
                    '"evidence_file_status=reference_ready\\n"',
                    '"evidence_file_status=reference_ready\\n"'
                    '"evidence_file_status=reference_ready\\n"',
                    1,
                ),
                "export-role": original.replace(
                    "evidence_role=demo_reference;catalog_generation=demo_expected",
                    "evidence_role=runtime_verified;catalog_generation=demo_expected",
                    1,
                ),
            }
            for mutation, text in mutations.items():
                assert text != original, (source, mutation)
                path = (
                    root
                    / source.parent.parent.parent.name
                    / mutation
                    / source.name
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                expect_rejected(path)
    print("test_reference_catalog_contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
