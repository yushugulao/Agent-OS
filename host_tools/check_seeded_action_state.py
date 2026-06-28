#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from plain_ucore_action_runner import action_kind, prepare_action_state, run_seeded_ucore


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def seeded_actions() -> list[dict[str, object]]:
    return [
        {
            "sequence": 1,
            "path": "/actions/research/rerun",
            "status": "accepted",
            "payload": {
                "run_id": "RUN-999-rerun",
                "parent_run": "RUN-999",
                "provider": "template",
                "question": "seeded action smoke",
                "dataset_rows": "3",
                "reference_entries": "2",
                "workspace_files": "4",
            },
        },
        {
            "sequence": 2,
            "path": "/actions/research/artifact-derive",
            "status": "accepted",
            "payload": {
                "run_id": "RUN-999-rerun",
                "input": "raw-counts.csv",
                "output": "normalized-counts.csv",
                "operation": "normalize",
                "stage": "analyze",
                "sha256": "sha-derived-999",
            },
        },
        {
            "sequence": 3,
            "path": "/actions/host-workflow/run",
            "status": "accepted",
            "payload": {
                "workflow_id": "wf-host-999",
                "run_id": "RUN-999",
                "engine": "host-runner",
                "dag": "ingest>analyze>report",
                "retry_stage": "analyze",
                "cache_hit_stage": "ingest",
                "worker_slots": "3",
                "queue_depth": "5",
                "observer_events": "7",
                "retry_reason": "quality_gate",
            },
        },
        {
            "sequence": 4,
            "path": "/actions/research/llm-relay-request",
            "status": "accepted",
            "payload": {
                "request_id": "llm-999",
                "route": "review_summary",
                "provider": "template",
            },
        },
        {
            "sequence": 5,
            "path": "/actions/research/workbench",
            "status": "accepted",
            "payload": {
                "workbench": "WB999",
                "workbench_title": "WB999",
                "literature_query": "agent",
            },
        },
        {
            "sequence": 6,
            "path": "/actions/research/workbench-task",
            "status": "accepted",
            "payload": {
                "workbench": "WB-999",
                "task": "draft",
                "status": "queued",
            },
        },
        {
            "sequence": 7,
            "path": "/actions/research/project-release-gate",
            "status": "accepted",
            "payload": {
                "project_id": "proj-999",
                "decision": "approved",
            },
        },
        {
            "sequence": 8,
            "path": "/actions/workflow-portability/run",
            "status": "accepted",
            "payload": {
                "import_id": "wfimp-999",
                "source_format": "snakemake",
                "source": "Snakefile",
                "target_runtime": "agentos_ucore",
                "execution_plan": "exec-plan-999",
                "compare_profile": "plain-vs-agentos",
                "scenario_id": "scenario-999",
                "rehearsal_status": "passed",
                "readiness_decision": "ready",
                "package": "wf-port-999.zip",
            },
        },
    ]


def seeded_action_kinds() -> list[str]:
    return [action_kind(str(action["path"])) for action in seeded_actions()]


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def require_contains(failures: list[str], label: str, state_dir: Path, name: str, token: str) -> None:
    path = state_dir / name
    text = read_text(path)
    if not text:
        failures.append(f"{label}: missing {name}")
        return
    if token not in text:
        failures.append(f"{label}: {name} missing {token}")


def validate_seed_package(label: str, run_dir: Path) -> list[str]:
    failures: list[str] = []
    state_dir = run_dir / "state-next"
    for kind in seeded_action_kinds():
        require_contains(failures, label, state_dir, "rp_host_action_seed", f"kind={kind}")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "run_id=RUN-999-rerun")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "parent_run=RUN-999")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "workflow_id=wf-host-999")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "request_id=llm-999")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "compare_profile=plain-vs-agentos")
    return failures


def validate_extracted_state(label: str, run_dir: Path) -> list[str]:
    failures: list[str] = []
    state_dir = run_dir / "state-extracted"
    if not state_dir.is_dir():
        return [f"{label}: missing extracted state"]

    require_contains(failures, label, state_dir, "rp_input", "host_action_rerun_id=RUN-999-rerun")
    require_contains(failures, label, state_dir, "rp_input", "host_action_rerun_parent=RUN-999")
    require_contains(failures, label, state_dir, "rp_input", "host_action_rerun_provider=template")
    require_contains(failures, label, state_dir, "rp_runner", "host_action_rerun=usable-run:RUN-999-rerun;parent=RUN-999;status=completed")
    require_contains(failures, label, state_dir, "rp_runner", "host_action_kind=research_rerun")
    require_contains(failures, label, state_dir, "rp_report_text", "host_report_rerun_id=RUN-999-rerun")
    require_contains(failures, label, state_dir, "rp_report_text", "host_report_rerun_parent=RUN-999")
    require_contains(failures, label, state_dir, "rp_artifact_manifest", "host_manifest_rerun=RUN-999-rerun;parent=RUN-999;status=ready")
    require_contains(failures, label, state_dir, "rp_artifact", "host_artifact_derive=raw-counts.csv;output=normalized-counts.csv;operation=normalize;stage=analyze;sha256=sha-derived-999")
    require_contains(failures, label, state_dir, "rp_artifact_manifest", "host_artifact_manifest_derive=raw-counts.csv;output=normalized-counts.csv;operation=normalize;stage=analyze;sha256=sha-derived-999")
    require_contains(failures, label, state_dir, "rp_stage_dag", "host_workflow_id=wf-host-999")
    require_contains(failures, label, state_dir, "rp_stage_dag", "host_workflow_dag=ingest>analyze>report")
    require_contains(failures, label, state_dir, "rp_stage_state", "host_workflow_run_id=RUN-999")
    require_contains(failures, label, state_dir, "rp_stage_state", "host_workflow_retry_stage=analyze")
    require_contains(failures, label, state_dir, "rp_runner", "host_action_workflow=wf-host-999;run_id=RUN-999;engine=host-runner;status=ready")
    require_contains(failures, label, state_dir, "rp_llm_packets", "host_llm_packet_request=llm-999")
    require_contains(failures, label, state_dir, "rp_llm_routes", "host_llm_route=review_summary")
    require_contains(failures, label, state_dir, "rp_llm_routes", "host_llm_route_provider=template")
    require_contains(failures, label, state_dir, "rp_llm_guard", "host_llm_guard_status=passed")
    require_contains(failures, label, state_dir, "rp_report_text", "host_report_workbench_task=draft")
    require_contains(failures, label, state_dir, "rp_artifact_manifest", "host_manifest_workbench_task=draft")
    require_contains(failures, label, state_dir, "rp_web_bundle", "host_action_project_release_gate=approved")
    require_contains(failures, label, state_dir, "rp_wfio", "host_portability_import=wfimp-999;format=snakemake;source=Snakefile")
    require_contains(failures, label, state_dir, "rp_wfio", "host_portability_target=agentos_ucore")
    require_contains(failures, label, state_dir, "rp_wfio", "host_portability_compare_profile=plain-vs-agentos")
    return failures


def run_target(
    label: str,
    repo_dir: Path,
    work_dir: Path,
    timeout: int,
    wsl_distro: str,
    chapter: str,
    init_proc: str,
    pass_marker: str,
) -> dict[str, object]:
    run_dir = work_dir / label
    state_dir = run_dir / "state-current"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"seeded_action_state: {label} start chapter={chapter} init={init_proc} action_count={len(seeded_actions())} log={run_dir / 'ucore-run.log'}",
        flush=True,
    )
    prepare_summary = prepare_action_state(seeded_actions(), state_dir, run_dir)
    run_summary = run_seeded_ucore(
        repo_dir,
        run_dir,
        timeout,
        wsl_distro,
        chapter=chapter,
        init_proc=init_proc,
        pass_marker=pass_marker,
    )
    failures = validate_seed_package(label, run_dir) + validate_extracted_state(label, run_dir)
    if not run_summary.get("passed"):
        failures.append(f"{label}: qemu run failed")
    if int(run_summary.get("extracted_state_files", 0) or 0) < 200:
        failures.append(f"{label}: extracted too few state files")

    print(
        "seeded_action_state: {label} done passed={passed} extracted_state_files={files} status={status}".format(
            label=label,
            passed=int(bool(run_summary.get("passed", False))),
            files=run_summary.get("extracted_state_files", 0),
            status="ready" if not failures else "failed",
        ),
        flush=True,
    )
    return {
        "label": label,
        "repo_dir": str(repo_dir),
        "run_dir": str(run_dir),
        "prepare": prepare_summary,
        "run": run_summary,
        "failures": failures,
        "status": "ready" if not failures else "failed",
    }


def run_check(root: Path, work_dir: Path, timeout: int, wsl_distro: str) -> dict[str, object]:
    plain = run_target(
        "plain",
        root,
        work_dir,
        timeout,
        wsl_distro,
        chapter="platform_seeded",
        init_proc="rp_seed_orch",
        pass_marker="rp_orch: passed",
    )
    agentos = run_target(
        "agentos",
        root / "agentos_ucore",
        work_dir,
        timeout,
        wsl_distro,
        chapter="platform_agentos",
        init_proc="rp_agentos_orch",
        pass_marker="rp_agentos_orch: passed",
    )
    failures = list(plain["failures"]) + list(agentos["failures"])
    return {
        "status": "ready" if not failures else "failed",
        "action": "/actions/research/rerun",
        "action_count": len(seeded_actions()),
        "action_kinds": seeded_action_kinds(),
        "plain": plain,
        "agentos": agentos,
        "failures": failures,
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser(description="Run seeded host actions through plain uCore and AgentOS-uCore.")
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/agentos-seeded-action-state"))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--wsl-distro", default="Ubuntu")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    summary = run_check(root, args.work_dir, args.timeout, args.wsl_distro)
    if args.json_out:
        write_json(args.json_out, summary)
    print(
        "seeded_action_state: action={action} action_count={action_count} plain={plain_status} agentos={agentos_status} status={status}".format(
            action=summary["action"],
            action_count=summary["action_count"],
            plain_status=summary["plain"]["status"],
            agentos_status=summary["agentos"]["status"],
            status=summary["status"],
        )
    )
    for failure in summary["failures"]:
        print(f"seeded_action_state: failed: {failure}")
    return 0 if summary["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
