#!/usr/bin/env python3
"""演示/参考目录证据的源码与身份合同。"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from benchmark_source_contract import _lex, _split_arguments


class ReferenceCatalogError(RuntimeError):
    pass


@dataclass(frozen=True, order=True)
class ReferenceRecordIdentity:
    destination: str
    anchor: str

    def canonical(self) -> str:
        return f"record:{self.destination}:{self.anchor}"


@dataclass(frozen=True)
class ReferenceSourceSpec:
    products: tuple[str, ...] = ()
    records: tuple[ReferenceRecordIdentity, ...] = ()
    demo_guest_marker: bool = False


def _records(
    destination: str, *anchors: str
) -> tuple[ReferenceRecordIdentity, ...]:
    return tuple(ReferenceRecordIdentity(destination, anchor) for anchor in anchors)


REFERENCE_SOURCES: dict[str, dict[str, ReferenceSourceSpec]] = {
    "agentos": {
        "rp_backend.c": ReferenceSourceSpec(
            records=_records(
                "rp_backend_exec",
                "runtime_claim_protocol=source-bound-v1",
                "workflow_portability=rp_wfio",
                "runner_report=plain-ucore",
                "runner_report=retry-recovery",
                "runner_report=agentos-context",
                "runner_report=agentos-fsmeta",
                "runner_report=agentos-recovery",
                "runner_report=agentos-event",
                "runner_report=agentos-audit",
                "runner_report=agentos-edit",
                "runner_cases=8",
            )
        ),
        "rp_coherenceplane.c": ReferenceSourceSpec(
            products=("rp_coherence",),
            records=(
                *_records("rp_web_bundle", "coherence_plane=rp_coherence"),
                *_records(
                    "rp_review_dashboard",
                    "subsection=coherence_plane;source=rp_coherence",
                ),
                *_records(
                    "rp_agentcmp",
                    "coherence_plane_checks=40",
                    "coherence_kernel_binding=run_state_views,tool_contract_table,delivery_metadata,agent_coordination_trace",
                ),
            ),
            demo_guest_marker=True,
        ),
        "rp_compare_plain.c": ReferenceSourceSpec(
            records=_records(
                "rp_agentcmp",
                "demo_expected_programs=70",
                "demo_expected_provenance_view_checks=64",
                "demo_expected_provenance_query_checks=72",
                "portability_backend_checks=18",
                "backend_runner_checks=24",
            )
        ),
        "rp_decsupport.c": ReferenceSourceSpec(
            products=(
                "rp_decsupport",
                "rp_decopt",
                "rp_deccrit",
                "rp_decscore",
                "rp_decpacket",
            ),
            records=(
                *_records("rp_package", "decision_support=rp_decsupport"),
                *_records(
                    "rp_web_bundle", "decision_support_page=rp_decsupport"
                ),
                *_records(
                    "rp_review_dashboard",
                    "subsection=decision_support;source=rp_decsupport",
                ),
                *_records("rp_agentcmp", "decision_support_checks=80"),
            ),
            demo_guest_marker=True,
        ),
# 导出器不得提前发布归后续生产者所有的记录。
        "rp_web_export.c": ReferenceSourceSpec(),
    },
    "plain": {
        "rp_backend.c": ReferenceSourceSpec(
            products=("rp_backend_exec",), demo_guest_marker=True
        ),
        "rp_coherenceplane.c": ReferenceSourceSpec(
            products=("rp_coherence",),
            records=(
                *_records("rp_web_bundle", "coherence_plane=rp_coherence"),
                *_records(
                    "rp_review_dashboard",
                    "subsection=coherence_plane;source=rp_coherence",
                ),
                *_records("rp_agentcmp", "coherence_plane_checks=40"),
            ),
            demo_guest_marker=True,
        ),
        "rp_compare_plain.c": ReferenceSourceSpec(
            records=_records(
                "rp_agentcmp",
                "demo_expected_programs=70",
                "portability_backend_reference_checks=18",
                "backend_reference_checks=21",
            ),
            demo_guest_marker=True,
        ),
        "rp_decsupport.c": ReferenceSourceSpec(
            products=(
                "rp_decsupport",
                "rp_decopt",
                "rp_deccrit",
                "rp_decscore",
                "rp_decpacket",
            ),
            records=(
                *_records("rp_package", "decision_support=rp_decsupport"),
                *_records(
                    "rp_web_bundle", "decision_support_page=rp_decsupport"
                ),
                *_records(
                    "rp_review_dashboard",
                    "subsection=decision_support;source=rp_decsupport",
                ),
                *_records("rp_agentcmp", "decision_support_checks=80"),
            ),
            demo_guest_marker=True,
        ),
        "rp_metrics.c": ReferenceSourceSpec(products=("rp_agentcmp",)),
        "rp_prov_query.c": ReferenceSourceSpec(
            products=(
                "rp_prov_query",
                "rp_prov_specs",
                "rp_prov_exec",
                "rp_prov_query_pkg",
            ),
            records=(
                *_records(
                    "rp_web_bundle", "provenance_queries_page=rp_prov_query"
                ),
                *_records(
                    "rp_review_dashboard",
                    "subsection=provenance_queries;source=rp_prov_query",
                ),
                *_records(
                    "rp_package", "provenance_query_package=rp_prov_query"
                ),
                *_records("rp_agentcmp", "provenance_query_checks=72"),
            ),
            demo_guest_marker=True,
        ),
        "rp_prov_view.c": ReferenceSourceSpec(
            products=(
                "rp_prov_view",
                "rp_prov_edges",
                "rp_evidence_packet",
                "rp_timeline_view",
            ),
            records=(
                *_records("rp_web_bundle", "provenance_page=rp_prov_view"),
                *_records(
                    "rp_review_dashboard",
                    "subsection=provenance_view;source=rp_prov_view",
                ),
                *_records("rp_package", "provenance_view_report=rp_prov_view"),
                *_records("rp_agentcmp", "provenance_view_checks=64"),
            ),
            demo_guest_marker=True,
        ),
        "rp_test_suite.c": ReferenceSourceSpec(
            products=("rp_tests",), demo_guest_marker=True
        ),
# 导出器不得提前发布归后续生产者所有的记录。
        "rp_web_export.c": ReferenceSourceSpec(),
    },
}

REFERENCE_OBSERVATIONS = {
    "plain": frozenset(
        {ReferenceRecordIdentity("rp_agentcmp", "program_source=rp_orch_timing")}
    ),
    "agentos": frozenset(),
}

FILE_ENVELOPE = (
    "evidence_file_role=demo_reference",
    "evidence_file_generation=demo_expected",
    "evidence_file_status=reference_ready",
)
RECORD_ENVELOPE = (
    "evidence_role=demo_reference",
    "catalog_generation=demo_expected",
    "status=reference_ready",
)
_FILE_ENVELOPE_KEYS = tuple(field.split("=", 1)[0] for field in FILE_ENVELOPE)
_RECORD_ENVELOPE_VALUES = dict(field.split("=", 1) for field in RECORD_ENVELOPE)
_C_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_FIELD_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")


def _target_sources(target: str) -> dict[str, ReferenceSourceSpec]:
    try:
        return REFERENCE_SOURCES[target]
    except KeyError as error:
        raise ReferenceCatalogError(f"unknown reference target: {target}") from error


def _unique_registry_items(target: str, attribute: str) -> frozenset[object]:
    owners: dict[object, str] = {}
    for source, spec in _target_sources(target).items():
        for identity in getattr(spec, attribute):
            if identity in owners:
                raise ReferenceCatalogError(
                    f"duplicate {target} reference identity: {identity} "
                    f"({owners[identity]}, {source})"
                )
            owners[identity] = source
    return frozenset(owners)


def allowed_file_identities(target: str) -> frozenset[str]:
    return _unique_registry_items(target, "products")  # type: ignore[return-value]


def allowed_record_identities(target: str) -> frozenset[ReferenceRecordIdentity]:
    return _unique_registry_items(target, "records")  # type: ignore[return-value]


def allowed_observation_identities(
    target: str,
) -> frozenset[ReferenceRecordIdentity]:
    _target_sources(target)
    return REFERENCE_OBSERVATIONS[target]


def expected_reference_identities(target: str) -> tuple[str, ...]:
    files = (f"file:{name}" for name in allowed_file_identities(target))
    records = (identity.canonical() for identity in allowed_record_identities(target))
    observations = (
        "observation:" + identity.canonical().removeprefix("record:")
        for identity in allowed_observation_identities(target)
    )
    return tuple(sorted((*files, *records, *observations)))


def _anchor_fields(anchor: str) -> tuple[tuple[str, str], ...]:
    fields: list[tuple[str, str]] = []
    for part in anchor.split(";"):
        if part.count("=") != 1:
            raise ReferenceCatalogError(f"invalid reference anchor: {anchor}")
        key, value = part.split("=", 1)
        if not _FIELD_NAME.fullmatch(key) or not value or any(
            existing == key for existing, _ in fields
        ):
            raise ReferenceCatalogError(f"invalid reference anchor: {anchor}")
        fields.append((key, value))
    return tuple(fields)


def match_record_identity(
    target: str, destination: str, fields: dict[str, str]
) -> ReferenceRecordIdentity:
    anchor_field = next(
        (
            (key, value)
            for key, value in fields.items()
            if key not in _RECORD_ENVELOPE_VALUES
        ),
        None,
    )
    matches = [
        identity
        for identity in allowed_record_identities(target)
        if identity.destination == destination
        and _anchor_fields(identity.anchor)[0] == anchor_field
        and all(fields.get(key) == value for key, value in _anchor_fields(identity.anchor))
    ]
    if len(matches) != 1:
        raise ReferenceCatalogError(
            f"{target} reference record has unknown or ambiguous identity: {destination}"
        )
    return matches[0]


def _lex_source(path: Path) -> list[str]:
    try:
        raw_source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReferenceCatalogError(
            f"reference source cannot be read: {path}"
        ) from error

    # C 行拼接先于注释和 token 识别执行。
    spliced_source = raw_source.replace("\\\r\n", "").replace("\\\n", "")
    try:
        return _lex(spliced_source)
    except ValueError as error:
        raise ReferenceCatalogError(
            f"reference source cannot be parsed: {error}"
        ) from error


def _calls(tokens: list[str], function: str) -> list[list[str]]:
    calls: list[list[str]] = []
    for position, token in enumerate(tokens[:-1]):
        if token != function or tokens[position + 1] != "(":
            continue
        depth = 0
        for closing in range(position + 1, len(tokens)):
            if tokens[closing] == "(":
                depth += 1
            elif tokens[closing] == ")":
                depth -= 1
                if depth == 0:
                    call = "".join(tokens[position + 2 : closing])
                    calls.append(_split_arguments(call))
                    break
        else:
            raise ReferenceCatalogError(
                f"reference {function} call is unterminated"
            )
    return calls


def _string_argument(value: str) -> str:
    literals = _C_STRING.findall(value)
    try:
        return "".join(ast.literal_eval(literal) for literal in literals)
    except (SyntaxError, ValueError) as error:
        raise ReferenceCatalogError("reference call has an invalid string literal") from error


def _parse_record(line: str, location: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in line.split(";"):
        if part.count("=") != 1:
            raise ReferenceCatalogError(f"{location} reference record is not canonical")
        key, value = part.split("=", 1)
        if not _FIELD_NAME.fullmatch(key) or not value or key in fields:
            raise ReferenceCatalogError(f"{location} reference record is not canonical")
        fields[key] = value
    return fields


def _infer_target(path: Path) -> str:
    return "plain" if "baseline_ucore" in path.parts else "agentos"


def _declares_reference_envelope(tokens: list[str]) -> bool:
    for function in ("rp_write_file", "rp_append_file"):
        for arguments in _calls(tokens, function):
            if len(arguments) != 2:
                continue
            body = _string_argument(arguments[1])
            if all(field in body for field in FILE_ENVELOPE):
                return True
            for line in body.splitlines():
                if all(field in line for field in RECORD_ENVELOPE):
                    return True
    return False


def validate_reference_source_tree(source_root: Path, target: str) -> None:
    """校验每个已声明生产者，并拒绝未声明生产者。"""

    if not source_root.is_dir() or source_root.is_symlink():
        raise ReferenceCatalogError(
            f"reference source tree is missing or unsafe: {source_root}"
        )
    sources = _target_sources(target)
    scanned: dict[str, Path] = {}
    for path in sorted(source_root.glob("rp_*.c"), key=lambda item: item.name):
        if not path.is_file() or path.is_symlink():
            raise ReferenceCatalogError(
                f"reference source is missing or unsafe: {path}"
            )
        tokens = _lex_source(path)
        scanned[path.name] = path
        if path.name not in sources and _declares_reference_envelope(tokens):
            raise ReferenceCatalogError(
                f"undeclared {target} reference producer: {path.name}"
            )

    for name in sorted(sources):
        path = scanned.get(name, source_root / name)
        validate_reference_source(path, target)


def validate_reference_source(path: Path, target: str | None = None) -> None:
    if not path.is_file() or path.is_symlink():
        raise ReferenceCatalogError(f"reference source is missing or unsafe: {path}")
    target = target or _infer_target(path)
    sources = _target_sources(target)
    name = path.name
    if name not in sources:
        raise ReferenceCatalogError(f"unsupported {target} reference source: {name}")
    spec = sources[name]
    tokens = _lex_source(path)
    source = "".join(tokens)

    expected_products = set(spec.products)
    seen_products: set[str] = set()
    seen_records: set[ReferenceRecordIdentity] = set()
    calls: list[tuple[str, list[str]]] = []
    for function in ("rp_write_file", "rp_append_file"):
        calls.extend((function, arguments) for arguments in _calls(tokens, function))

    for function, arguments in calls:
        if len(arguments) != 2:
            continue
        destination = _string_argument(arguments[0])
        body = _string_argument(arguments[1])
        claims_file = any(key + "=" in body for key in _FILE_ENVELOPE_KEYS)
        if function == "rp_write_file" and destination in expected_products:
            if destination in seen_products:
                raise ReferenceCatalogError(f"duplicate reference product: {destination}")
            for field in FILE_ENVELOPE:
                if body.count(field) != 1:
                    raise ReferenceCatalogError(
                        f"{destination} must declare {field} exactly once"
                    )
            seen_products.add(destination)
        elif claims_file:
            raise ReferenceCatalogError(
                f"unauthorized {target} reference product: {destination}"
            )

        for line_no, line in enumerate(body.splitlines(), 1):
            if "evidence_role=demo_reference" not in line:
                continue
            fields = _parse_record(line, f"{name}:{destination}:{line_no}")
            if any(fields.get(key) != value for key, value in _RECORD_ENVELOPE_VALUES.items()):
                raise ReferenceCatalogError(
                    f"{destination} export has an incomplete reference envelope"
                )
            if fields.get("result") == "passed":
                raise ReferenceCatalogError(
                    f"{destination} export promotes reference data to a pass claim"
                )
            identity = match_record_identity(target, destination, fields)
            if identity not in spec.records:
                raise ReferenceCatalogError(
                    f"{name} does not own {identity.canonical()}"
                )
            if identity in seen_records:
                raise ReferenceCatalogError(
                    f"duplicate reference record: {identity.canonical()}"
                )
            seen_records.add(identity)

    missing_products = expected_products - seen_products
    if missing_products:
        raise ReferenceCatalogError(
            "missing reference products: " + ", ".join(sorted(missing_products))
        )
    missing_records = set(spec.records) - seen_records
    if missing_records:
        raise ReferenceCatalogError(
            "missing tagged reference records: "
            + ", ".join(identity.canonical() for identity in sorted(missing_records))
        )

    if spec.demo_guest_marker:
        marker = (
            f"rp_{name.removeprefix('rp_').removesuffix('.c')}: "
            "evidence_role=demo_reference"
        )
        if (
            source.count(marker) != 1
            or "status=reference_ready" not in source[source.index(marker) :]
        ):
            raise ReferenceCatalogError("reference Guest marker is missing or malformed")
