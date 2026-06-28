#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestTheme:
    name: str
    keywords: tuple[str, ...]
    evidence_tokens: tuple[str, ...]


TEST_THEMES: tuple[TestTheme, ...] = (
    TestTheme(
        "core_state_config",
        (
            "json_store",
            "deepseek_key",
            "reset_recovers",
            "documentation",
            "extended_platform",
            "platform_consistency",
            "status_semantics",
            "mature_capabilities",
            "mature_platform",
        ),
        (
            "state_catalog=passed",
            "startup_doctor=passed",
            "consistency=passed",
            "mature_capabilities=passed",
        ),
    ),
    TestTheme(
        "workflow_runtime",
        (
            "demo_recovers",
            "host_workflow",
            "workflow_",
            "execution_cache",
            "run_configurations",
            "run_state",
            "lifecycle_order",
            "execution_observer",
            "execution_control",
            "resource_budget",
            "run_telemetry",
            "runbooks",
            "worker_executor",
            "worker_pool",
            "worker_operations",
            "queue_dashboard",
            "scheduler",
            "user_space_index",
        ),
        (
            "workflow=passed",
            "workflow_runner_detail=passed",
            "execution_scale=passed",
            "operations_scale=passed",
            "runbook_service=passed",
        ),
    ),
    TestTheme(
        "research_workbench",
        (
            "usable_research",
            "research_studio",
            "workbench",
            "project_",
            "research_package",
            "research_object",
            "study_protocol",
            "research_protocols",
            "sop_execution",
            "protocol_",
            "literature",
            "bibtex",
            "systematic_review",
            "knowledge_base",
            "semantic_catalog",
            "notebook",
        ),
        (
            "usable_research=passed",
            "workbench=passed",
            "project_delivery=passed",
            "study_protocol=passed",
            "knowledge_index=passed",
        ),
    ),
    TestTheme(
        "data_lab_science",
        (
            "csv_summary",
            "data_",
            "dataset",
            "calculation",
            "sample_",
            "cohort",
            "study_design",
            "statistical_design",
            "lab_operations",
            "experiment_",
            "training_compliance",
            "analysis_results",
            "visualization",
            "fair_data",
            "publication",
            "model_registry",
            "charts",
            "evaluation_reports",
        ),
        (
            "data_pipeline=passed",
            "lab_governance_ops=passed",
            "statistical_design=passed",
            "experiment_scheduling=passed",
            "model_registry=passed",
            "analysis_results=passed",
        ),
    ),
    TestTheme(
        "agents_llm_compare",
        (
            "llm",
            "prompt_ops",
            "agent_coordination",
            "collaboration",
            "agentcompare",
            "compare_",
            "backend_scenario",
            "comparative_study",
            "decision_support",
            "agentos_",
            "worker",
        ),
        (
            "llm=passed",
            "llm_relay=passed",
            "agent_collaboration=passed",
            "agent_compare=passed",
            "decision_support=passed",
        ),
    ),
    TestTheme(
        "ui_api_delivery",
        (
            "dashboard",
            "web_ui",
            "routes",
            "api",
            "site",
            "export",
            "download",
            "bundle",
            "cards",
            "pages",
            "selects",
            "surface_coverage",
            "delivery",
            "package",
            "real_artifact",
        ),
        (
            "ui_export=passed",
            "host_web_export=passed",
            "static_site=passed",
            "export_package=passed",
            "reserved_research_surfaces=passed",
        ),
    ),
    TestTheme(
        "provenance_review_governance",
        (
            "provenance",
            "lineage",
            "review",
            "release",
            "governance",
            "privacy",
            "ethics",
            "access_governance",
            "reference_integrity",
            "evidence_traceability",
            "risk_register",
            "claim",
            "integrity",
            "coherence",
            "object_namespace",
            "report_validation",
            "result_review",
            "quality",
        ),
        (
            "provenance_view=passed",
            "provenance_query=passed",
            "review_dashboard=passed",
            "release_dossier=passed",
            "regulated_research=passed",
            "integrity_plane=passed",
            "coherence_plane=passed",
        ),
    ),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_host_dir(root: Path) -> Path:
    override = os.environ.get("HOST_PLATFORM_DIR")
    if override:
        return Path(override)
    return root.parent / "research-agent-platform-userland"


def collect_test_names(host_dir: Path) -> list[str]:
    test_file = host_dir / "tests" / "test_platform.py"
    if not test_file.exists():
        raise FileNotFoundError(f"host test file is missing: {test_file}")
    text = test_file.read_text(encoding="utf-8")
    return re.findall(r"^\s+def (test_[A-Za-z0-9_]+)\(", text, re.M)


def classify_test(name: str) -> list[str]:
    lowered = name.lower()
    return [
        theme.name
        for theme in TEST_THEMES
        if any(keyword in lowered for keyword in theme.keywords)
    ]


def read_evidence(root: Path, relative: str) -> str:
    path = root / relative
    if not path.exists():
        raise FileNotFoundError(f"evidence source is missing: {relative}")
    return path.read_text(encoding="utf-8")


def read_runtime_evidence(state_dir: Path | None) -> str:
    if state_dir is None:
        return ""
    if not state_dir.is_dir():
        raise ValueError(f"state directory is missing: {state_dir}")
    path = state_dir / "rp_tests"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def run_check(
    root: Path,
    host_dir: Path,
    require_host: bool,
    plain_state_dir: Path | None = None,
    agentos_state_dir: Path | None = None,
) -> dict[str, object]:
    if not host_dir.exists():
        if require_host:
            raise SystemExit(f"host platform is missing: {host_dir}")
        return {
            "status": "skipped",
            "reason": "host_platform_not_found",
            "host_dir": str(host_dir),
        }

    test_names = collect_test_names(host_dir)
    plain_evidence = read_evidence(root, "user/src/rp_test_suite.c")
    agentos_evidence = read_evidence(root, "agentos_ucore/user/src/rp_test_suite.c")
    check_runtime_state = plain_state_dir is not None or agentos_state_dir is not None
    if check_runtime_state and (plain_state_dir is None or agentos_state_dir is None):
        raise ValueError("plain and AgentOS state directories must be supplied together")
    plain_runtime_evidence = read_runtime_evidence(plain_state_dir)
    agentos_runtime_evidence = read_runtime_evidence(agentos_state_dir)
    failures: list[str] = []
    theme_counts = {theme.name: 0 for theme in TEST_THEMES}
    unclassified: list[str] = []

    for test_name in test_names:
        matched = classify_test(test_name)
        if not matched:
            unclassified.append(test_name)
            continue
        for theme_name in matched:
            theme_counts[theme_name] += 1

    if not test_names:
        failures.append("host test file contains no test methods")
    if unclassified:
        failures.append("host tests are not mapped to themes: " + ", ".join(unclassified[:20]))

    theme_results: list[dict[str, object]] = []
    for theme in TEST_THEMES:
        missing_plain = [token for token in theme.evidence_tokens if token not in plain_evidence]
        missing_agentos = [token for token in theme.evidence_tokens if token not in agentos_evidence]
        missing_plain_runtime = [
            token for token in theme.evidence_tokens if check_runtime_state and token not in plain_runtime_evidence
        ]
        missing_agentos_runtime = [
            token for token in theme.evidence_tokens if check_runtime_state and token not in agentos_runtime_evidence
        ]
        if theme_counts[theme.name] == 0:
            failures.append(f"{theme.name}: no host tests matched this theme")
        if missing_plain:
            failures.append(f"{theme.name}: missing plain evidence tokens: {', '.join(missing_plain)}")
        if missing_agentos:
            failures.append(f"{theme.name}: missing AgentOS evidence tokens: {', '.join(missing_agentos)}")
        if missing_plain_runtime:
            failures.append(f"{theme.name}: missing plain runtime evidence tokens: {', '.join(missing_plain_runtime)}")
        if missing_agentos_runtime:
            failures.append(f"{theme.name}: missing AgentOS runtime evidence tokens: {', '.join(missing_agentos_runtime)}")
        failed = (
            theme_counts[theme.name] == 0
            or missing_plain
            or missing_agentos
            or missing_plain_runtime
            or missing_agentos_runtime
        )
        theme_results.append(
            {
                "name": theme.name,
                "host_tests": theme_counts[theme.name],
                "evidence_tokens": len(theme.evidence_tokens),
                "missing_plain": missing_plain,
                "missing_agentos": missing_agentos,
                "missing_plain_runtime": missing_plain_runtime,
                "missing_agentos_runtime": missing_agentos_runtime,
                "status": "failed" if failed else "ok",
            }
        )

    return {
        "status": "failed" if failures else "ready",
        "host_dir": str(host_dir),
        "host_tests": len(test_names),
        "themes_ok": sum(1 for item in theme_results if item["status"] == "ok"),
        "themes_total": len(theme_results),
        "unclassified_tests": len(unclassified),
        "runtime_state_checked": check_runtime_state,
        "theme_results": theme_results,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check host platform test themes against uCore platform evidence.")
    parser.add_argument("--host-dir", type=Path, default=None)
    parser.add_argument("--plain-state-dir", type=Path, default=None)
    parser.add_argument("--agentos-state-dir", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--require-host", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    host_dir = args.host_dir or default_host_dir(root)
    summary = run_check(root, host_dir, args.require_host, args.plain_state_dir, args.agentos_state_dir)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if summary["status"] == "skipped":
        print(f"host_test_alignment: status=skipped reason={summary['reason']} host_dir={summary['host_dir']}")
        return 0

    print(
        "host_test_alignment: "
        f"host_tests={summary['host_tests']} "
        f"themes_ok={summary['themes_ok']} "
        f"themes_total={summary['themes_total']} "
        f"unclassified_tests={summary['unclassified_tests']} "
        f"runtime_state_checked={int(bool(summary['runtime_state_checked']))} "
        f"status={summary['status']}"
    )
    if summary["status"] == "failed":
        for failure in summary["failures"]:
            print(f"host_test_alignment: failed: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
