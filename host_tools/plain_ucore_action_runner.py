#!/usr/bin/env python3
"""Prepare and optionally run plain uCore work from host reader action records."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from plain_ucore_fs_extract import extract_state_files


def read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        data = json.loads(line)
        if isinstance(data, dict):
            records.append(data)
    return records


def action_kind(path: str) -> str:
    if path.endswith("/research/library-source"):
        return "library_source"
    if path.endswith("/research/inspect-workspace"):
        return "workspace_inspect"
    if path.endswith("/research/import-workspace"):
        return "workspace_import"
    if path.endswith("/research/import-and-run"):
        return "workspace_import_run"
    if path.endswith("/research/literature-search"):
        return "literature_search"
    if path.endswith("/research/evidence-review"):
        return "evidence_review"
    if path.endswith("/research/evidence-protocol"):
        return "evidence_protocol"
    if path.endswith("/research/run"):
        return "research_run"
    if path.endswith("/research/rerun"):
        return "research_rerun"
    if path.endswith("/research/review"):
        return "human_review"
    if path.endswith("/research/delivery"):
        return "delivery"
    if path.endswith("/research/revision-task"):
        return "revision_task"
    if path.endswith("/research/run-revision") or path.endswith("/research/run-revision-task"):
        return "revision_run"
    if path.endswith("/research/export"):
        return "research_export"
    if path.endswith("/research/template"):
        return "template"
    if path.endswith("/research/dataset"):
        return "dataset"
    if path.endswith("/research/workbench"):
        return "workbench"
    if path.endswith("/research/workbench-advance"):
        return "workbench_advance"
    if path.endswith("/research/workbench-auto-advance"):
        return "workbench_auto_advance"
    if path.endswith("/research/workbench-task"):
        return "workbench_task"
    if path.endswith("/research/workbench-note"):
        return "workbench_note"
    if path.endswith("/research/workbench-notes"):
        return "workbench_notes"
    if path.endswith("/research/workbench-handoff-package"):
        return "workbench_handoff_package"
    if path.endswith("/research/workbench-readiness"):
        return "workbench_readiness"
    if path.endswith("/research/workbench-answer"):
        return "workbench_answer"
    if path.endswith("/research/workbench-answer-audit"):
        return "workbench_answer_audit"
    if path.endswith("/research/workbench-evidence-search"):
        return "workbench_evidence_search"
    if path.endswith("/research/workbench-brief"):
        return "workbench_brief"
    if path.endswith("/research/workbench-evidence-dossier"):
        return "workbench_evidence_dossier"
    if path.endswith("/research/workbench-evidence-graph"):
        return "workbench_evidence_graph"
    if path.endswith("/research/workbench-citations"):
        return "workbench_citations"
    if path.endswith("/research/workbench-manuscript"):
        return "workbench_manuscript"
    if path.endswith("/research/workbench-manuscript-audit"):
        return "workbench_manuscript_audit"
    if path.endswith("/research/workbench-manuscript-revision-plan"):
        return "workbench_manuscript_revision_plan"
    if path.endswith("/research/workbench-manuscript-revision-task"):
        return "workbench_manuscript_revision_task"
    if path.endswith("/research/workbench-task-board"):
        return "workbench_task_board"
    if path.endswith("/research/workbench-task-board-row"):
        return "workbench_task_board_row"
    if path.endswith("/research/workbench-plan-queue-row"):
        return "workbench_plan_queue_row"
    if path.endswith("/research/workbench-plan-queue-execute"):
        return "workbench_plan_queue_execute"
    if path.endswith("/research/workbench-runbook"):
        return "workbench_runbook"
    if path.endswith("/research/workbench-timeline"):
        return "workbench_timeline"
    if path.endswith("/research/workbench-file-manifest"):
        return "workbench_file_manifest"
    if path.endswith("/research/workbench-file-verify"):
        return "workbench_file_verify"
    if path.endswith("/research/workbench-complete"):
        return "workbench_complete"
    if path.endswith("/research/workbench-quality-gate"):
        return "workbench_quality_gate"
    if path.endswith("/research/workbench-quality-repair-plan"):
        return "workbench_quality_repair_plan"
    if path.endswith("/research/workbench-quality-repair-execute"):
        return "workbench_quality_repair_execute"
    if path.endswith("/research/workbench-action-item"):
        return "workbench_action_item"
    if path.endswith("/research/workbench-delivery-dashboard"):
        return "workbench_delivery_dashboard"
    if path.endswith("/research/workbench-delivery-execute-next"):
        return "workbench_delivery_execute_next"
    if path.endswith("/research/operations-report"):
        return "operations_report"
    if path.endswith("/research/operations-advance-next"):
        return "operations_advance_next"
    if path.endswith("/research/operations-execute-next-plan"):
        return "operations_execute_next_plan"
    if path.endswith("/research/project-space"):
        return "project_space"
    if path.endswith("/research/project-space-note"):
        return "project_space_note"
    if path.endswith("/research/project-space-action-item"):
        return "project_space_action_item"
    if path.endswith("/research/project-space-answer"):
        return "project_space_answer"
    if path.endswith("/research/project-space-repair-execute"):
        return "project_space_repair_execute"
    if path.endswith("/research-search/save"):
        return "research_search_save"
    if path.endswith("/research-search/export"):
        return "research_search_export"
    if path.endswith("/research-search/note"):
        return "research_search_note"
    if path.endswith("/research-search/action-item"):
        return "research_search_action_item"
    if path.endswith("/research/export-workbench"):
        return "workbench_export"
    if path.endswith("/research/export-notebook"):
        return "notebook_export"
    if path.endswith("/research/export-bundle"):
        return "bundle_export"
    if path.endswith("/agentcompare/run"):
        return "agentcompare"
    if path.endswith("/host-workflow/run"):
        return "host_workflow"
    if path.endswith("/host-workflow/export"):
        return "host_workflow_export"
    return "generic"


def clean_copy_state(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        return
    for item in sorted(src.iterdir()):
        if item.is_file() and item.name.startswith("rp_"):
            shutil.copy2(item, dst / item.name)


def line_value(value: object) -> str:
    text = str(value)
    return text.replace("\n", " ").replace(";", ",").strip()


def action_line(record: dict[str, object]) -> str:
    sequence = line_value(record.get("sequence", ""))
    path = line_value(record.get("path", ""))
    status = line_value(record.get("status", "accepted"))
    kind = action_kind(path)
    payload = record.get("payload", {})
    fields = [f"action={sequence}", f"path={path}", f"kind={kind}", f"status={status}"]
    if isinstance(payload, dict):
        for key in sorted(payload):
            fields.append(f"{line_value(key)}={line_value(payload[key])}")
    return ";".join(fields)


def action_plan_line(record: dict[str, object]) -> str:
    path = str(record.get("path", ""))
    kind = action_kind(path)
    sequence = line_value(record.get("sequence", ""))
    if kind == "research_run":
        return f"plan={sequence};kind=research_run;prepare=rp_input;execute=rp_orch;collect=rp_web_bundle;status=ready"
    if kind == "agentcompare":
        return f"plan={sequence};kind=agentcompare;prepare=rp_agentcmp;execute=rp_orch;collect=rp_compare_plain;status=ready"
    if kind == "host_workflow":
        return f"plan={sequence};kind=host_workflow;prepare=rp_stage_dag;execute=rp_orch;collect=rp_artifact_manifest;status=ready"
    return f"plan={sequence};kind={kind};prepare=rp_host_action_queue;execute=rp_orch;collect=rp_web_bundle;status=ready"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def prepare_action_state(actions: list[dict[str, object]], state_dir: Path, run_dir: Path) -> dict[str, object]:
    run_dir.mkdir(parents=True, exist_ok=True)
    next_state = run_dir / "state-next"
    clean_copy_state(state_dir, next_state)

    queue_lines = [action_line(action) for action in actions]
    plan_lines = [action_plan_line(action) for action in actions]
    kinds = [action_kind(str(action.get("path", ""))) for action in actions]
    accepted = sum(1 for action in actions if action.get("status", "accepted") == "accepted")

    write_text(next_state / "rp_host_action_queue", "\n".join(queue_lines + ["status=ready"]) + "\n")
    write_text(next_state / "rp_host_action_plan", "\n".join(plan_lines + ["status=ready"]) + "\n")
    write_text(next_state / "rp_host_action_inbox", "\n".join(queue_lines) + ("\n" if queue_lines else ""))
    write_json(run_dir / "actions.json", actions)

    summary = {
        "actions": len(actions),
        "accepted": accepted,
        "kinds": sorted(set(kinds)),
        "state_dir": str(state_dir),
        "next_state_dir": str(next_state),
        "status": "ready",
    }
    write_json(run_dir / "runner-summary.json", summary)
    return summary


def windows_path_to_wsl(path: Path) -> str:
    text = str(path.resolve())
    if len(text) >= 3 and text[1:3] == ":\\":
        drive = text[0].lower()
        rest = text[3:].replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return text.replace("\\", "/")


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def bash_path(path: Path, base: Path | None = None) -> str:
    resolved = path.resolve()
    if base is not None:
        try:
            rel = resolved.relative_to(base.resolve())
            return "./" + rel.as_posix()
        except ValueError:
            pass
    return windows_path_to_wsl(resolved)


def run_command(command: list[str], log_path: Path, timeout_seconds: int, append: bool = False) -> int:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    text = (result.stdout or "") + (result.stderr or "")
    if append:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        write_text(log_path, text)
    return result.returncode


def make_wsl_command(command_text: str, wsl_distro: str) -> list[str]:
    if os.name == "nt":
        return ["wsl.exe", "-d", wsl_distro, "--", "bash", "-lc", command_text]
    return ["bash", "-lc", command_text]


def c_string_literal(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\r", "")
        .replace("\n", "\\n")
    )
    return "\"" + escaped + "\""


def compact_seed_text(text: str) -> str:
    keep_by_kind = {
        "research_run": {"run_id", "title", "question", "provider", "dataset_rows", "reference_entries", "workspace_files", "csv_file", "reference_file"},
        "dataset": {"title", "dataset_rows", "columns"},
        "library_source": {"citation_key", "tags"},
        "template": {"name", "question", "provider_id"},
        "workspace_inspect": {"root", "max_files"},
        "workspace_import": {"root", "max_files", "manifest", "title", "question"},
        "workspace_import_run": {"root", "max_files", "manifest", "title", "question"},
        "literature_search": {"query", "provider", "max_results"},
        "evidence_review": {"search_id", "reviewer", "include_terms", "included"},
        "evidence_protocol": {"title", "research_question", "outcome"},
        "human_review": {"run_id", "reviewer", "decision"},
        "revision_task": {"review_id", "targets"},
        "revision_run": {"run_id", "task_id"},
        "agentcompare": {"profile"},
        "bundle_export": {"run_id", "bundle"},
        "research_export": {"run_id", "bundle"},
        "delivery": {"run_id", "bundle"},
        "notebook_export": {"run_id", "format"},
        "workbench": {"workbench", "workbench_title", "literature_query"},
        "workbench_complete": {"workbench"},
        "workbench_advance": {"workbench", "task"},
        "workbench_auto_advance": {"step_limit"},
        "workbench_task": {"workbench", "task", "status"},
        "workbench_note": {"workbench", "note_kind", "title", "body"},
        "workbench_notes": {"workbench", "notes_filter"},
        "workbench_handoff_package": {"workbench", "handoff_scope"},
        "workbench_readiness": {"workbench"},
        "workbench_answer": {"question"},
        "workbench_answer_audit": set(),
        "workbench_evidence_search": {"query"},
        "workbench_brief": {"workbench", "brief_format"},
        "workbench_evidence_dossier": {"dossier_format"},
        "workbench_evidence_graph": {"graph_format"},
        "workbench_citations": {"citation_format"},
        "workbench_manuscript": {"manuscript_format"},
        "workbench_manuscript_audit": {"audit_scope"},
        "workbench_manuscript_revision_plan": {"revision_area"},
        "workbench_manuscript_revision_task": {"revision_task", "revision_status"},
        "workbench_task_board": {"board_filter"},
        "workbench_task_board_row": {"row_id", "row_status"},
        "workbench_plan_queue_row": {"workbench_id", "plan_item_id", "source_type", "source_id", "status"},
        "workbench_plan_queue_execute": {"workbench_id", "plan_item_id", "source_type", "source_id", "provider_id", "max_steps"},
        "workbench_runbook": {"runbook_format"},
        "workbench_timeline": {"timeline_format"},
        "workbench_file_manifest": {"workbench", "manifest"},
        "workbench_file_verify": {"workbench", "manifest"},
        "workbench_export": {"workbench", "bundle"},
        "workbench_quality_gate": {"workbench_id"},
        "workbench_quality_repair_plan": {"workbench_id"},
        "workbench_quality_repair_execute": {"workbench_id", "repair_id", "action_key", "provider_id", "max_steps", "answer_question"},
        "workbench_action_item": {"workbench_id", "title", "instruction", "priority", "status", "source_query"},
        "workbench_delivery_dashboard": {"tag", "query", "include_clean"},
        "workbench_delivery_execute_next": {"tag", "query", "provider_id", "max_steps", "answer_question"},
        "operations_report": {"format"},
        "operations_advance_next": {"provider_id", "max_steps", "review_decision", "delivery_audience"},
        "operations_execute_next_plan": {"provider_id", "max_steps", "answer_question", "delivery_audience"},
        "project_space": {"workbench_id", "project_id", "query"},
        "project_space_note": {"workbench_id", "kind", "title", "body", "tags"},
        "project_space_action_item": {"workbench_id", "title", "instruction", "priority", "status", "source_query"},
        "project_space_answer": {"workbench_id", "question", "limit"},
        "project_space_repair_execute": {"workbench_id", "repair_id", "provider_id", "max_steps"},
        "research_search_save": {"query", "name"},
        "research_search_export": {"query", "limit"},
        "research_search_note": {"workbench_id", "query", "title", "note", "limit"},
        "research_search_action_item": {"workbench_id", "query", "title", "instruction", "priority", "limit"},
        "host_workflow": {"workflow_id", "run_id", "engine", "stages", "dag", "max_workers", "cache"},
        "host_workflow_export": {"workflow_id", "run_id", "format", "bundle"},
    }
    lines: list[str] = []
    for raw in text.splitlines():
        fields = [field for field in raw.split(";") if field]
        kind = ""
        for field in fields:
            if field.startswith("kind="):
                kind = field.split("=", 1)[1]
                break
        keep_keys = keep_by_kind.get(kind)
        compact: list[str] = []
        for field in fields:
            if field.startswith("kind="):
                compact.insert(0, field)
            elif field.startswith("action=") or field.startswith("path=") or field.startswith("status="):
                continue
            else:
                key = field.split("=", 1)[0]
                if keep_keys is not None and key not in keep_keys:
                    continue
                if kind.startswith("workbench_") and key == "workbench":
                    continue
                compact.append(field)
        if compact:
            lines.append(";".join(compact))
    return "\n".join(lines) + ("\n" if lines else "")


def write_seed_header(next_state: Path, repo_dir: Path) -> int:
    inbox = next_state / "rp_host_action_inbox"
    text = inbox.read_text(encoding="utf-8") if inbox.is_file() else ""
    seed_text = compact_seed_text(text)
    write_text(next_state / "rp_host_action_seed", seed_text)
    header = repo_dir / "user" / "build" / "generated" / "rp_host_action_seed.h"
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_text(
        "#ifndef __RP_HOST_ACTION_SEED_H__\n"
        "#define __RP_HOST_ACTION_SEED_H__\n"
        f"#define RP_HOST_ACTION_SEED {c_string_literal('')}\n"
        f"#define RP_HOST_ACTION_BOOTSTRAP_SEED {c_string_literal(seed_text)}\n"
        "#endif\n",
        encoding="utf-8",
    )
    return len([line for line in seed_text.splitlines() if line.strip()])




def find_log_value(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if prefix in line:
            return line.strip()
    return ""


def write_run_result_state(next_state: Path, run_summary: dict[str, object], log_text: str) -> None:
    passed = bool(run_summary.get("passed"))
    lines = [
        "host_runner=plain_ucore_action_runner",
        f"status={'ready' if passed else 'failed'}",
        f"passed={1 if passed else 0}",
        f"embedded_action_records={run_summary.get('embedded_action_records', 0)}",
        f"extracted_state_files={run_summary.get('extracted_state_files', 0)}",
        f"log={line_value(run_summary.get('log', ''))}",
    ]
    host_reader_actions = find_log_value(log_text, "rp_web_export: host_reader_actions=")
    host_actions_verified = find_log_value(log_text, "rp_compare_plain: host_actions=")
    orch_passed = find_log_value(log_text, "rp_orch: passed")
    if host_reader_actions:
        lines.append("qemu_" + host_reader_actions)
    if host_actions_verified:
        lines.append("qemu_" + host_actions_verified)
    if orch_passed:
        lines.append("qemu_orch_passed=1")
    write_text(next_state / "rp_host_run_result", "\n".join(lines) + "\n")


def publish_next_state(next_state: Path, state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    for item in sorted(next_state.iterdir()):
        if item.is_file() and item.name.startswith("rp_"):
            shutil.copy2(item, state_dir / item.name)


def run_plain_ucore(repo_dir: Path, run_dir: Path, timeout_seconds: int, wsl_distro: str) -> dict[str, object]:
    repo_dir = repo_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "ucore-run.log"
    next_state = run_dir / "state-next"
    repo_bash = bash_path(repo_dir)
    clean_command = (
        f"cd {shell_quote(repo_bash)} && "
        "make -C user clean >/dev/null && "
        "make clean >/dev/null"
    )
    build_code = run_command(make_wsl_command(clean_command, wsl_distro), log_path, timeout_seconds + 30)
    if build_code != 0:
        summary = {
            "commands": [clean_command],
            "returncode": build_code,
            "embedded_action_records": 0,
            "passed": False,
            "log": str(log_path),
            "status": "failed",
        }
        write_json(run_dir / "ucore-run-summary.json", summary)
        return summary
    embedded_records = write_seed_header(next_state, repo_dir)
    seed_file = next_state / "rp_host_action_seed"
    seed_file_bash = bash_path(seed_file)
    run_command_text = (
        f"cd {shell_quote(repo_bash)} && "
        "make user TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_seeded >/dev/null"
        " && "
        f"cp {shell_quote(seed_file_bash)} user/target/bin/rp_host_action_seed"
        " && "
        "rm -rf nfs/fs nfs/fs.img nfs/fs-copy.img && "
        "make nfs/fs.img >/dev/null && "
        "make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_seeded LOG=warn INIT_PROC=rp_seed_orch >/dev/null && "
        f"timeout {timeout_seconds}s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_seeded LOG=warn INIT_PROC=rp_seed_orch"
    )
    code = run_command(make_wsl_command(run_command_text, wsl_distro), log_path, timeout_seconds + 30, append=True)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    passed = "rp_orch: passed" in text and "child_failed" not in text and "ialloc" not in text
    extract_summary: dict[str, object] = {"status": "skipped", "extracted_state_files": 0}
    image_path = repo_dir / "nfs" / "fs-copy.img"
    if image_path.exists():
        extract_summary = extract_state_files(image_path, run_dir / "state-extracted", repo_dir)
        for item in sorted((run_dir / "state-extracted").iterdir()):
            if item.is_file() and item.name.startswith("rp_"):
                shutil.copy2(item, next_state / item.name)
    else:
        passed = False
        extract_summary = {"status": "missing_image", "extracted_state_files": 0}
    summary = {
        "commands": [clean_command, f"embedded_action_records={embedded_records}", run_command_text],
        "returncode": code,
        "embedded_action_records": embedded_records,
        "extracted_state_files": extract_summary.get("extracted_state_files", 0),
        "extract_status": extract_summary.get("status", "unknown"),
        "passed": passed,
        "log": str(log_path),
        "status": "ready" if passed else "failed",
    }
    write_run_result_state(next_state, summary, text)
    write_json(run_dir / "ucore-run-summary.json", summary)
    return summary


def append_records(existing: Iterable[dict[str, object]], extra: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    records = list(existing)
    next_sequence = 1
    if records:
        next_sequence = max(int(record.get("sequence", 0) or 0) for record in records) + 1
    for record in extra:
        copy = dict(record)
        copy.setdefault("sequence", next_sequence)
        copy.setdefault("status", "accepted")
        records.append(copy)
        next_sequence += 1
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare plain uCore input from host reader actions.")
    parser.add_argument("--actions", type=Path, required=True, help="host-actions.jsonl from plain_ucore_reader.")
    parser.add_argument("--state-dir", type=Path, required=True, help="Current rp_* state directory.")
    parser.add_argument("--run-dir", type=Path, required=True, help="Directory for prepared action package and logs.")
    parser.add_argument("--add-action", action="append", default=[], help="Add an action path, for example /actions/research/run.")
    parser.add_argument("--payload", action="append", default=[], help="Payload key=value for --add-action records.")
    parser.add_argument("--run-ucore", action="store_true", help="Run the plain uCore seeded path after preparing actions.")
    parser.add_argument("--repo-dir", type=Path, default=Path("."), help="Repository root for --run-ucore.")
    parser.add_argument("--timeout", type=int, default=80, help="QEMU run timeout in seconds.")
    parser.add_argument("--wsl-distro", default="Ubuntu", help="WSL distribution name on Windows.")
    parser.add_argument("--update-state-dir", action="store_true", help="Copy prepared rp_* action state and run result back to --state-dir.")
    args = parser.parse_args()

    extra_payload: dict[str, str] = {}
    for item in args.payload:
        if "=" in item:
            key, value = item.split("=", 1)
            extra_payload[key] = value
    extra_actions = [{"path": path, "payload": extra_payload} for path in args.add_action]

    actions = append_records(read_jsonl(args.actions), extra_actions)
    summary = prepare_action_state(actions, args.state_dir, args.run_dir)
    print(
        "plain_ucore_action_runner: actions={actions} accepted={accepted} status={status}".format(
            **summary
        )
    )
    if args.run_ucore:
        run_summary = run_plain_ucore(args.repo_dir, args.run_dir, args.timeout, args.wsl_distro)
        if args.update_state_dir:
            publish_next_state(args.run_dir / "state-next", args.state_dir)
        print("plain_ucore_action_runner: ucore_status={status} passed={passed}".format(**run_summary))
        return 0 if run_summary["passed"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
