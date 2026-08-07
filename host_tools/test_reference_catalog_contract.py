#!/usr/bin/env python3
"""目标绑定的演示/参考身份注册表变异测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from benchmark_source_contract import _extract_call
from reference_catalog_contract import (
    RECORD_ENVELOPE,
    REFERENCE_SOURCES,
    ReferenceCatalogError,
    allowed_file_identities,
    allowed_observation_identities,
    allowed_record_identities,
    expected_reference_identities,
    match_record_identity,
    validate_reference_source,
    validate_reference_source_tree,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = {
    "agentos": ROOT / "user" / "src",
    "plain": ROOT / "baseline_ucore" / "user" / "src",
}
SOURCE_CASES = tuple(
    (target, SOURCE_ROOTS[target] / name)
    for target, sources in REFERENCE_SOURCES.items()
    for name in sources
)


def expect_rejected(path: Path, target: str) -> None:
    try:
        validate_reference_source(path, target)
    except ReferenceCatalogError:
        return
    raise AssertionError(f"invalid {target} reference producer was accepted: {path.name}")


def write_mutation(
    root: Path, target: str, source: Path, mutation: str, text: str
) -> Path:
    path = root / target / mutation / source.name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def duplicate_first_record(source: str, anchor: str) -> str:
    first_field = anchor.split(";", 1)[0]
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if "evidence_role=demo_reference" in line and first_field in line:
            lines.insert(index + 1, line)
            return "".join(lines)
    raise AssertionError(f"reference record is missing from source: {anchor}")


def mutate_first_record(
    source: str, anchor: str, old: str, replacement: str
) -> str:
    first_field = anchor.split(";", 1)[0]
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if "evidence_role=demo_reference" in line and first_field in line:
            mutated = line.replace(old, replacement, 1)
            if mutated == line:
                raise AssertionError(f"record mutation token is missing: {old}")
            lines[index] = mutated
            return "".join(lines)
    raise AssertionError(f"reference record is missing from source: {anchor}")


def comment_owned_call(
    source: str, functions: tuple[str, ...], owned_fragment: str
) -> str:
    for function in functions:
        start = 0
        needle = function + "("
        while True:
            position = source.find(needle, start)
            if position < 0:
                break
            opening = position + len(function)
            arguments = _extract_call(source, opening)
            closing = opening + len(arguments) + 1
            if owned_fragment in arguments:
                return (
                    source[:position]
                    + "/*"
                    + source[position : closing + 1]
                    + "*/"
                    + source[closing + 1 :]
                )
            start = closing + 1
    raise AssertionError(f"owned call is missing: {owned_fragment}")


def main() -> int:
    for target, source_root in SOURCE_ROOTS.items():
        validate_reference_source_tree(source_root, target)
    for target, source in SOURCE_CASES:
        validate_reference_source(source, target)

    assert len(allowed_file_identities("agentos")) == 6
    assert len(allowed_file_identities("plain")) == 17
    assert len(allowed_record_identities("agentos")) == 24
    assert len(allowed_record_identities("plain")) == 18
    assert len(allowed_observation_identities("agentos")) == 0
    assert len(allowed_observation_identities("plain")) == 1
    assert len(expected_reference_identities("agentos")) == 30
    assert len(expected_reference_identities("plain")) == 36
    assert "rp_backend_exec" not in allowed_file_identities("agentos")
    assert "rp_backend_exec" in allowed_file_identities("plain")

    fields = {
        "evidence_role": "demo_reference",
        "catalog_generation": "demo_expected",
        "subsection": "coherence_plane",
        "source": "rp_coherence",
        "status": "reference_ready",
    }
    identity = match_record_identity("plain", "rp_review_dashboard", fields)
    assert identity.anchor == "subsection=coherence_plane;source=rp_coherence"
    try:
        match_record_identity(
            "agentos",
            "rp_agentcmp",
            {
                "evidence_role": "demo_reference",
                "catalog_generation": "demo_expected",
                "unknown_claim": "1",
                "status": "reference_ready",
            },
        )
    except ReferenceCatalogError:
        pass
    else:
        raise AssertionError("unknown reference record identity was accepted")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for target, sources in REFERENCE_SOURCES.items():
            source_root = root / target / "source-tree"
            source_root.mkdir(parents=True)
            for name in sources:
                (source_root / name).write_bytes((SOURCE_ROOTS[target] / name).read_bytes())
            forged = source_root / "rp_forged.c"
            forged.write_text(
                'void forged(void) { rp_append_file("rp_agentcmp", '
                '"evidence_role=demo_reference;catalog_generation=demo_expected;'
                'forged=1;status=reference_ready"); }\n',
                encoding="utf-8",
            )
            try:
                validate_reference_source_tree(source_root, target)
            except ReferenceCatalogError as error:
                assert "undeclared" in str(error), error
            else:
                raise AssertionError(f"undeclared {target} producer was accepted")

        for target, source in SOURCE_CASES:
            spec = REFERENCE_SOURCES[target][source.name]
            original = source.read_text(encoding="utf-8")
            mutations: dict[str, str] = {}
            if spec.products:
                mutations.update(
                    {
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
                            '"evidence_file_status=reference_ready\\n"',
                            '"evidence_file_status=ready\\n"',
                            1,
                        ),
                        "duplicate-file-status": original.replace(
                            '"evidence_file_status=reference_ready\\n"',
                            '"evidence_file_status=reference_ready\\n"'
                            '"evidence_file_status=reference_ready\\n"',
                            1,
                        ),
                        "unknown-product": original.replace(
                            f'rp_write_file("{spec.products[0]}"',
                            'rp_write_file("rp_unknown_reference"',
                            1,
                        ),
                        "commented-product": comment_owned_call(
                            original,
                            ("rp_write_file",),
                            f'"{spec.products[0]}"',
                        ),
                    }
                )
            if spec.records:
                first = spec.records[0]
                mutations.update(
                    {
                        "record-role": mutate_first_record(
                            original,
                            first.anchor,
                            RECORD_ENVELOPE[0],
                            "evidence_role=runtime_verified",
                        ),
                        "unknown-anchor": mutate_first_record(
                            original,
                            first.anchor,
                            first.anchor,
                            first.anchor + "_unknown",
                        ),
                        "duplicate-record": duplicate_first_record(
                            original, first.anchor
                        ),
                        "commented-record": comment_owned_call(
                            original,
                            ("rp_write_file", "rp_append_file"),
                            first.anchor,
                        ),
                    }
                )
            if spec.demo_guest_marker:
                marker = (
                    f"rp_{source.stem.removeprefix('rp_')}: "
                    "evidence_role=demo_reference"
                )
                mutations["commented-marker"] = comment_owned_call(
                    original, ("printf",), marker
                )
            for mutation, text in mutations.items():
                assert text != original, (source, mutation)
                expect_rejected(
                    write_mutation(root, target, source, mutation, text), target
                )

        for target in ("agentos", "plain"):
            source = SOURCE_ROOTS[target] / "rp_web_export.c"
            original = source.read_text(encoding="utf-8")
            injected = original.replace(
                "\treturn 0;",
                '\tif (!rp_append_file("rp_web_bundle", '
                '"evidence_role=demo_reference;catalog_generation=demo_expected;'
                'decision_support_page=rp_decsupport;status=reference_ready")) '
                "return 1;\n\treturn 0;",
                1,
            )
            assert injected != original
            expect_rejected(
                write_mutation(root, target, source, "wrong-owner", injected),
                target,
            )

        # 相同源码基名不能借用另一个目标的权限。
        expect_rejected(
            SOURCE_ROOTS["agentos"] / "rp_coherenceplane.c", "plain"
        )
        expect_rejected(
            SOURCE_ROOTS["plain"] / "rp_coherenceplane.c", "agentos"
        )

        malformed = SOURCE_ROOTS["agentos"] / "rp_web_export.c"
        expect_rejected(
            write_mutation(
                root,
                "agentos",
                malformed,
                "unsupported-token",
                malformed.read_text(encoding="utf-8") + "\n`\n",
            ),
            "agentos",
        )

    print("test_reference_catalog_contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
