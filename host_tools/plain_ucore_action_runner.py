#!/usr/bin/env python3
"""Prepare and optionally run plain uCore work from host reader action records."""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterable

from plain_ucore_fs_extract import extract_state_files

UCORE_FS_BLOCK_SIZE = 1024
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
GUEST_FAILURE_RULES = (
    (
        "kernel_panic",
        re.compile(
            r"^(?:\[PANIC\s+-?\d+--?\d+\]|PANIC:)(?:\s|.).*$",
            re.IGNORECASE,
        ),
    ),
    (
        "kernel_fault",
        re.compile(
            r"^\[ERROR\s+-?\d+--?\d+\].*(?:unknown syscall|bad addr\s*=|IllegalInstruction).*$",
            re.IGNORECASE,
        ),
    ),
    (
        "guest_check_failed",
        re.compile(r"^[A-Za-z0-9_.-]+:\s+check failed(?:\s|:|$).*$", re.IGNORECASE),
    ),
    (
        "orchestrator_failed",
        re.compile(
            r"^rp_(?:seed_|agentos_)?orch:\s+(?:child_failed|failed)(?:\s|:|$).*$",
            re.IGNORECASE,
        ),
    ),
)
DEFAULT_FAILURE_PATTERN = "|".join(f"(?:{rule.pattern})" for _, rule in GUEST_FAILURE_RULES)


def normalize_guest_log_line(line: str) -> str:
    return ANSI_ESCAPE_RE.sub("", line).rstrip("\r\n")


def classify_guest_failure(line: str) -> str:
    normalized = normalize_guest_log_line(line)
    for reason, rule in GUEST_FAILURE_RULES:
        if rule.fullmatch(normalized):
            return reason
    return ""


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
    if path.endswith("/research/studio-launch"):
        return "studio_launch"
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
    if path.endswith("/research/llm-relay-request"):
        return "llm_relay_request"
    if path.endswith("/research/llm-relay-response"):
        return "llm_relay_response"
    if path.endswith("/research/llm-relay-fallback"):
        return "llm_relay_fallback"
    if path.endswith("/research/artifact-input"):
        return "artifact_input"
    if path.endswith("/research/artifact-derive"):
        return "artifact_derive"
    if path.endswith("/research/artifact-log"):
        return "artifact_log"
    if path.endswith("/research/artifact-chart"):
        return "artifact_chart"
    if path.endswith("/research/artifact-package"):
        return "artifact_package"
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
    if path.endswith("/research/dataset-preview"):
        return "dataset_preview"
    if path.endswith("/research/dataset-visualization"):
        return "dataset_visualization"
    if path.endswith("/research/dataset-card"):
        return "dataset_card"
    if path.endswith("/research/dataset-answer"):
        return "dataset_answer"
    if path.endswith("/research/dataset-run"):
        return "dataset_run"
    if path.endswith("/research/dataset-run-comparison"):
        return "dataset_run_comparison"
    if path.endswith("/research/dataset-portfolio"):
        return "dataset_portfolio"
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
    if path.endswith("/research/project-scaffold"):
        return "project_scaffold"
    if path.endswith("/research/project-launch"):
        return "project_launch"
    if path.endswith("/research/project-action-execute"):
        return "project_action_execute"
    if path.endswith("/research/sample-workbench"):
        return "sample_workbench"
    if path.endswith("/research/study-protocol"):
        return "study_protocol"
    if path.endswith("/research/run-study-protocol"):
        return "study_protocol_run"
    if path.endswith("/research/study-protocol-compliance"):
        return "study_protocol_compliance"
    if path.endswith("/research/study-protocol-bundle"):
        return "study_protocol_bundle"
    if path.endswith("/research/study-protocol-launch"):
        return "study_protocol_launch"
    if path.endswith("/research/study-protocol-launch-rerun"):
        return "study_protocol_launch_rerun"
    if path.endswith("/research/study-protocol-launch-comparison"):
        return "study_protocol_launch_comparison"
    if path.endswith("/research/study-protocol-reproduction-package"):
        return "study_protocol_reproduction_package"
    if path.endswith("/research/study-protocol-reproduction-package-review"):
        return "study_protocol_reproduction_package_review"
    if path.endswith("/research/study-protocol-reproduction-package-action-plan"):
        return "study_protocol_reproduction_package_action_plan"
    if path.endswith("/research/study-protocol-reproduction-package-action-execute"):
        return "study_protocol_reproduction_package_action_execute"
    if path.endswith("/research/source-portfolio"):
        return "source_portfolio"
    if path.endswith("/research/project-space"):
        return "project_space"
    if path.endswith("/research/project-space-note"):
        return "project_space_note"
    if path.endswith("/research/project-space-action-item"):
        return "project_space_action_item"
    if path.endswith("/research/project-space-review"):
        return "project_space_review"
    if path.endswith("/research/project-space-answer"):
        return "project_space_answer"
    if path.endswith("/research/project-space-repair-execute"):
        return "project_space_repair_execute"
    if path.endswith("/research/project-space-task-board-row"):
        return "project_space_task_board_row"
    if path.endswith("/research/project-handoff-audit"):
        return "project_handoff_audit"
    if path.endswith("/research/project-release-gate"):
        return "project_release_gate"
    if path.endswith("/research/project-snapshot"):
        return "project_snapshot"
    if path.endswith("/research/project-snapshot-comparison"):
        return "project_snapshot_comparison"
    if path.endswith("/research/project-reproducibility-audit"):
        return "project_reproducibility_audit"
    if path.endswith("/research/project-provenance-graph"):
        return "project_provenance_graph"
    if path.endswith("/research/project-delivery"):
        return "project_delivery"
    if path.endswith("/research/package-intake"):
        return "package_intake"
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
    if path.endswith("/host-workflow/stage-attempt"):
        return "host_workflow_stage"
    if path.endswith("/host-workflow/cache-decision"):
        return "host_workflow_cache"
    if path.endswith("/host-workflow/retry-decision"):
        return "host_workflow_retry"
    if path.endswith("/host-workflow/artifact-manifest"):
        return "host_workflow_artifact"
    if path.endswith("/host-workflow/report-export"):
        return "host_workflow_report"
    if path.endswith("/workflow-portability/import"):
        return "workflow_portability_import"
    if path.endswith("/workflow-portability/plan"):
        return "workflow_portability_plan"
    if path.endswith("/workflow-portability/bind"):
        return "workflow_portability_bind"
    if path.endswith("/workflow-portability/rehearse"):
        return "workflow_portability_rehearse"
    if path.endswith("/workflow-portability/review"):
        return "workflow_portability_review"
    if path.endswith("/workflow-portability/package"):
        return "workflow_portability_package"
    if path.endswith("/workflow-portability/run"):
        return "workflow_portability"
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
    if kind == "research_rerun":
        return f"plan={sequence};kind=research_rerun;prepare=rp_input;execute=rp_orch;collect=rp_runner;status=ready"
    if kind == "studio_launch":
        return f"plan={sequence};kind=studio_launch;prepare=rp_input;execute=rp_orch;collect=rp_studio;status=ready"
    if kind == "agentcompare":
        return f"plan={sequence};kind=agentcompare;prepare=rp_agentcmp;execute=rp_orch;collect=rp_compare_plain;status=ready"
    if kind in {
        "host_workflow",
        "host_workflow_stage",
        "host_workflow_cache",
        "host_workflow_retry",
        "host_workflow_artifact",
        "host_workflow_report",
    }:
        return f"plan={sequence};kind={kind};prepare=rp_stage_dag;execute=rp_orch;collect=rp_artifact_manifest;status=ready"
    if kind in {
        "artifact_input",
        "artifact_derive",
        "artifact_log",
        "artifact_chart",
        "artifact_package",
    }:
        return f"plan={sequence};kind={kind};prepare=rp_artifact;execute=rp_artifact_ops;collect=rp_artifact_manifest;status=ready"
    if kind in {
        "workflow_portability",
        "workflow_portability_import",
        "workflow_portability_plan",
        "workflow_portability_bind",
        "workflow_portability_rehearse",
        "workflow_portability_review",
        "workflow_portability_package",
    }:
        return f"plan={sequence};kind={kind};prepare=rp_wfio;execute=rp_orch;collect=rp_compare_plain;status=ready"
    if kind in {
        "project_scaffold",
        "project_launch",
        "project_action_execute",
    }:
        return f"plan={sequence};kind={kind};prepare=rp_usableproj;execute=rp_orch;collect=rp_usablelaunch;status=ready"
    if kind in {
        "dataset_preview",
        "dataset_visualization",
        "dataset_card",
        "dataset_answer",
        "dataset_run",
        "dataset_run_comparison",
        "dataset_portfolio",
        "source_portfolio",
        "sample_workbench",
    }:
        return f"plan={sequence};kind={kind};prepare=rp_usable;execute=rp_orch;collect=rp_usableds;status=ready"
    if kind.startswith("study_protocol"):
        return f"plan={sequence};kind={kind};prepare=rp_studyproto;execute=rp_orch;collect=rp_usablepack;status=ready"
    return f"plan={sequence};kind={kind};prepare=rp_host_action_queue;execute=rp_orch;collect=rp_web_bundle;status=ready"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_fs_aligned_seed(path: Path, seed_text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = seed_text.encode("utf-8")
    remainder = len(data) % UCORE_FS_BLOCK_SIZE
    if remainder:
        data += b"\0" * (UCORE_FS_BLOCK_SIZE - remainder)
    path.write_bytes(data)


def pad_file_for_ucore_fs(path: Path) -> None:
    data = path.read_bytes()
    remainder = len(data) % UCORE_FS_BLOCK_SIZE
    if remainder:
        path.write_bytes(data + b"\0" * (UCORE_FS_BLOCK_SIZE - remainder))


def pad_state_files_for_ucore_fs(state_dir: Path) -> None:
    for item in sorted(state_dir.iterdir()):
        if item.is_file() and item.name.startswith("rp_"):
            pad_file_for_ucore_fs(item)


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


def make_var_arg(name: str, value: str) -> str:
    return f"{name}={shell_quote(value)}"


def toolprefix_arg() -> str:
    return make_var_arg("TOOLPREFIX", os.environ.get("TOOLPREFIX", "riscv64-linux-gnu-"))


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


def parse_seconds(value: str | int | float) -> float:
    text = str(value)
    unit = text[-1:]
    number = text[:-1] if unit.isalpha() else text
    seconds = float(number)
    if unit in ("s", "S") or not unit.isalpha():
        return seconds
    if unit in ("m", "M"):
        return seconds * 60
    if unit in ("h", "H"):
        return seconds * 3600
    raise ValueError(f"unsupported duration: {text}")


def terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=2)
    except Exception:
        if proc.poll() is None:
            if os.name == "nt":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=2)


def run_observed_command(
    command: list[str],
    log_path: Path,
    timeout_seconds: int,
    append: bool = False,
    pass_marker: str = "",
    failure_pattern: str = DEFAULT_FAILURE_PATTERN,
    idle_notice_seconds: int = 20,
    marker_grace_seconds: int = 2,
) -> dict[str, object]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    output_queue: queue.Queue[str | None] = queue.Queue()
    popen_kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["preexec_fn"] = os.setsid
    proc = subprocess.Popen(command, **popen_kwargs)
    assert proc.stdout is not None

    def read_output() -> None:
        try:
            for line in proc.stdout:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    start = time.monotonic()
    last_output = start
    last_notice = start
    marker_seen_at = 0.0
    marker_seen = False
    failure_seen = False
    failure_line = ""
    failure_reason = ""
    timed_out = False
    idle_notices = 0
    last_lines: list[str] = []
    failure_re = re.compile(failure_pattern, re.IGNORECASE) if failure_pattern else None

    with log_path.open(mode, encoding="utf-8", errors="replace") as handle:
        while True:
            now = time.monotonic()
            if now - start > timeout_seconds:
                timed_out = True
                handle.write(f"\n[runner] exceeded {timeout_seconds}s\n")
                break
            if now - last_output >= idle_notice_seconds and now - last_notice >= idle_notice_seconds:
                idle_notices += 1
                notice = f"[runner] no output for {int(now - last_output)}s\n"
                handle.write(notice)
                handle.flush()
                print(notice.rstrip())
                last_notice = now
            if marker_seen and marker_seen_at > 0 and now - marker_seen_at >= marker_grace_seconds:
                if proc.poll() is None:
                    handle.write(f"[runner] pass marker observed; stopping process after {marker_grace_seconds}s grace\n")
                    handle.flush()
                    break
            try:
                item = output_queue.get(timeout=0.2)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            if item is None:
                if proc.poll() is not None:
                    break
                continue
            last_output = time.monotonic()
            handle.write(item)
            handle.flush()
            line = item.rstrip("\n")
            last_lines.append(line)
            if len(last_lines) > 80:
                last_lines = last_lines[-80:]
            normalized_line = normalize_guest_log_line(item)
            if failure_re and failure_re.fullmatch(normalized_line):
                failure_seen = True
                failure_line = normalized_line
                failure_reason = classify_guest_failure(normalized_line) or "custom_failure_pattern"
                handle.write(f"[runner] guest failure detected: {failure_reason}\n")
                break
            if pass_marker and pass_marker in item and not marker_seen:
                marker_seen = True
                marker_seen_at = time.monotonic()
                handle.write("[runner] pass marker observed\n")
                handle.flush()

        if proc.poll() is None:
            terminate_process(proc)
        reader.join(timeout=1)
        if timed_out or failure_seen or (pass_marker and not marker_seen):
            handle.write("[runner] last log lines:\n")
            for line in last_lines[-40:]:
                handle.write(line + "\n")

    elapsed = time.monotonic() - start
    returncode = proc.returncode
    ok = not timed_out and not failure_seen and (not pass_marker or marker_seen)
    if not ok and not failure_reason:
        if timed_out:
            failure_reason = "guest_timeout"
        elif returncode not in (0, None):
            failure_reason = "guest_exit_nonzero"
        elif pass_marker and not marker_seen:
            failure_reason = "pass_marker_missing"
        else:
            failure_reason = "guest_failed"
    if ok and returncode not in (0, None):
        returncode = 0
    elif not ok and returncode in (0, None):
        returncode = 1
    return {
        "returncode": returncode,
        "marker_seen": marker_seen,
        "failure_seen": failure_seen,
        "failure_line": failure_line,
        "failure_reason": failure_reason,
        "timed_out": timed_out,
        "idle_notices": idle_notices,
        "elapsed_seconds": round(elapsed, 3),
    }


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
        "studio_launch": {"title", "goal", "workbench_id", "workbench"},
        "research_run": {"run_id", "title", "question", "provider", "dataset_rows", "reference_entries", "workspace_files", "csv_file", "reference_file"},
        "research_rerun": {"run_id", "parent_run", "source_run", "provider", "question", "dataset_rows", "reference_entries", "workspace_files"},
        "dataset": {"title", "dataset_rows", "columns"},
        "dataset_preview": {"dataset_id", "rows", "quality"},
        "dataset_visualization": {"dataset_id", "chart", "x_field", "y_field", "group_field", "points"},
        "dataset_card": {"dataset_id", "readiness", "warnings"},
        "dataset_answer": {"dataset_id", "question", "answer"},
        "dataset_run": {"dataset_id", "run_id", "provider_id", "question", "artifacts"},
        "dataset_run_comparison": {"dataset_id", "left_run", "right_run", "decision"},
        "dataset_portfolio": {"dataset_id", "filter", "datasets", "ready"},
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
        "workbench_plan_queue_row": {"workbench_id", "plan_item_id", "status"},
        "workbench_plan_queue_execute": {"workbench_id", "plan_item_id"},
        "workbench_runbook": {"runbook_format"},
        "workbench_timeline": {"timeline_format"},
        "workbench_file_manifest": {"workbench", "manifest", "files", "sha_records"},
        "workbench_file_verify": {"workbench", "manifest", "files", "sha_records", "verified", "missing"},
        "workbench_export": {"workbench", "bundle"},
        "workbench_quality_gate": {"workbench_id"},
        "workbench_quality_repair_plan": {"workbench_id"},
        "workbench_quality_repair_execute": {"workbench_id", "repair_id"},
        "workbench_action_item": {"workbench_id", "title", "status"},
        "workbench_delivery_dashboard": {"tag", "query"},
        "workbench_delivery_execute_next": {"tag", "query"},
        "operations_report": {"format"},
        "operations_advance_next": {"review_decision"},
        "operations_execute_next_plan": set(),
        "project_scaffold": {"template_id", "project_id", "title", "dataset_id", "library_source_id", "files", "workspace"},
        "project_launch": {"project_id", "scaffold_id", "workbench_id", "run_id", "provider_id", "question"},
        "project_action_execute": {"project_id", "action_id", "action_key", "provider_id", "max_steps", "result"},
        "sample_workbench": {"workbench_id", "template_id", "dataset_id", "question"},
        "study_protocol": {"protocol_id", "title", "question", "hypothesis", "dataset_tags", "source_tags"},
        "study_protocol_run": {"protocol_id", "run_id", "provider_id"},
        "study_protocol_compliance": {"run_id", "decision", "findings"},
        "study_protocol_bundle": {"run_id", "bundle", "files"},
        "study_protocol_launch": {"launch_id", "protocol_id", "run_id", "provider_id"},
        "study_protocol_launch_rerun": {"launch_id", "rerun_id", "provider_id"},
        "study_protocol_launch_comparison": {"launch_id", "left", "right", "changed_metrics"},
        "study_protocol_reproduction_package": {"launch_id", "package_id", "files", "notebooks", "datasets"},
        "study_protocol_reproduction_package_review": {"package_id", "decision", "reviewer"},
        "study_protocol_reproduction_package_action_plan": {"package_id", "steps", "owner"},
        "study_protocol_reproduction_package_action_execute": {"package_id", "steps_done", "result", "provider_id"},
        "source_portfolio": {"source_id", "query", "sources", "reviewed"},
        "project_space": {"workbench_id", "project_id", "query"},
        "project_space_note": {"workbench_id", "kind", "title"},
        "project_space_action_item": {"workbench_id", "title", "status"},
        "project_space_review": {"workbench_id", "project_id", "decision", "reviewer", "required_changes"},
        "project_space_answer": {"workbench_id", "question", "limit"},
        "project_space_repair_execute": {"workbench_id", "repair_id"},
        "project_space_task_board_row": {"workbench_id", "row_id", "row_status", "row_note"},
        "project_handoff_audit": {"project_id", "scope", "decision"},
        "project_release_gate": {"project_id", "decision", "checks", "required_actions", "suggested_actions"},
        "project_snapshot": {"project_id", "snapshot_id", "files", "hash_records", "changes"},
        "project_snapshot_comparison": {"project_id", "left", "right", "changed_files", "decision"},
        "project_reproducibility_audit": {"project_id", "inputs", "outputs", "notebooks", "claim_audits", "decision"},
        "project_provenance_graph": {"project_id", "nodes", "edges", "dot"},
        "project_delivery": {"project_id", "bundle", "decision", "release_gate", "handoff"},
        "package_intake": {"package_id", "label", "files", "sha256", "decision"},
        "research_search_save": {"query", "name"},
        "research_search_export": {"query", "limit"},
        "research_search_note": {"workbench_id", "query", "title"},
        "research_search_action_item": {"workbench_id", "query", "title"},
        "host_workflow": {"workflow_id", "run_id", "engine", "dag", "retry_stage", "cache_hit_stage", "worker_slots", "queue_depth", "observer_events", "retry_reason"},
        "host_workflow_export": {"workflow_id", "run_id", "format", "bundle"},
        "host_workflow_stage": {"workflow_id", "run_id", "stage", "attempt", "status", "command", "duration_ms"},
        "host_workflow_cache": {"workflow_id", "run_id", "stage", "cache_key", "cache_result", "cache_policy"},
        "host_workflow_retry": {"workflow_id", "run_id", "stage", "retry_reason", "next_attempt", "decision"},
        "host_workflow_artifact": {"workflow_id", "run_id", "artifact", "artifact_kind", "sha256", "bytes"},
        "host_workflow_report": {"workflow_id", "run_id", "report", "format", "sections", "status"},
        "artifact_input": {"run_id", "file", "artifact_kind", "sha256", "bytes", "source"},
        "artifact_derive": {"run_id", "input", "output", "operation", "stage", "sha256"},
        "artifact_log": {"run_id", "stage", "log", "level", "message"},
        "artifact_chart": {"run_id", "chart", "chart_type", "data_file", "points"},
        "artifact_package": {"run_id", "package", "manifest", "files", "status"},
        "workflow_portability": {"import_id", "source_format", "source", "target_runtime", "execution_plan", "compare_profile", "scenario_id", "rehearsal_status", "readiness_decision", "package"},
        "workflow_portability_import": {"import_id", "source_format", "source", "normalized_steps", "adapter_id"},
        "workflow_portability_plan": {"import_id", "migration_plan", "target_runtime", "migration_steps", "risk_items"},
        "workflow_portability_bind": {"execution_plan", "compare_profile", "scenario_id", "backend_cases"},
        "workflow_portability_rehearse": {"rehearsal_id", "binding_id", "rehearsal_status", "observed_ready", "skipped"},
        "workflow_portability_review": {"review_id", "readiness_decision", "blocking_items", "work_items"},
        "workflow_portability_package": {"import_id", "package", "export_format", "bundle"},
        "llm_relay_request": {"request_id", "route", "provider"},
        "llm_relay_response": {"response_id", "summary"},
        "llm_relay_fallback": {"case", "action"},
    }
    lines: list[str] = []
    for raw in text.splitlines():
        fields = [field for field in raw.split(";") if field]
        kind = ""
        saw_action_field = False
        saw_path_field = False
        saw_status_field = False
        for field in fields:
            if field.startswith("kind="):
                kind = field.split("=", 1)[1]
                break
        keep_keys = keep_by_kind.get(kind)
        compact: list[str] = []
        for field in fields:
            if field.startswith("kind="):
                compact.insert(0, field)
            else:
                key = field.split("=", 1)[0]
                if key == "action" and not saw_action_field:
                    saw_action_field = True
                    continue
                if key == "path" and not saw_path_field:
                    saw_path_field = True
                    continue
                if key == "status" and not saw_status_field:
                    saw_status_field = True
                    continue
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
    write_fs_aligned_seed(next_state / "rp_host_action_seed", seed_text)
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
        f"build_returncode={run_summary.get('build_returncode', '')}",
        f"guest_returncode={run_summary.get('guest_returncode', '')}",
        f"failure_phase={line_value(run_summary.get('failure_phase', ''))}",
        f"failure_reason={line_value(run_summary.get('failure_reason', ''))}",
        f"build_log={line_value(run_summary.get('build_log', ''))}",
        f"log={line_value(run_summary.get('log', ''))}",
        f"qemu_elapsed_seconds={run_summary.get('elapsed_seconds', 0)}",
        f"qemu_idle_notices={run_summary.get('idle_notices', 0)}",
        f"qemu_timed_out={1 if run_summary.get('timed_out') else 0}",
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


def run_seeded_ucore(
    repo_dir: Path,
    run_dir: Path,
    timeout_seconds: int,
    wsl_distro: str,
    chapter: str = "platform_seeded",
    init_proc: str = "rp_seed_orch",
    pass_marker: str = "rp_orch: passed",
) -> dict[str, object]:
    repo_dir = repo_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    build_log_path = run_dir / "ucore-build.log"
    log_path = run_dir / "ucore-run.log"
    next_state = run_dir / "state-next"
    repo_bash = bash_path(repo_dir)
    clean_command = (
        f"cd {shell_quote(repo_bash)} && "
        "make -C user clean >/dev/null && "
        "make clean >/dev/null"
    )
    clean_code = run_command(
        make_wsl_command(clean_command, wsl_distro),
        build_log_path,
        timeout_seconds + 30,
    )
    if clean_code != 0:
        summary = {
            "commands": [clean_command],
            "returncode": clean_code,
            "build_returncode": clean_code,
            "guest_returncode": None,
            "embedded_action_records": 0,
            "passed": False,
            "build_log": str(build_log_path),
            "log": str(log_path),
            "failure_phase": "clean",
            "status": "failed",
        }
        write_json(run_dir / "ucore-run-summary.json", summary)
        return summary
    embedded_records = write_seed_header(next_state, repo_dir)
    pad_state_files_for_ucore_fs(next_state)
    seed_file = next_state / "rp_host_action_seed"
    seed_file_bash = bash_path(seed_file)
    toolprefix = toolprefix_arg()
    build_command_text = (
        f"cd {shell_quote(repo_bash)} && "
        f"make user {toolprefix} CHAPTER={chapter}"
        " && "
        f"cp {shell_quote(seed_file_bash)} user/target/bin/rp_host_action_seed"
        " && "
        "rm -rf nfs/fs nfs/fs.img nfs/fs-copy.img && "
        f"make nfs/fs-copy.img {toolprefix} CHAPTER={chapter} && "
        f"make build {toolprefix} CHAPTER={chapter} LOG=warn INIT_PROC={init_proc}"
    )
    build_code = run_command(
        make_wsl_command(build_command_text, wsl_distro),
        build_log_path,
        timeout_seconds + 30,
        append=True,
    )
    if build_code != 0:
        summary = {
            "commands": [clean_command, f"embedded_action_records={embedded_records}", build_command_text],
            "returncode": build_code,
            "build_returncode": build_code,
            "guest_returncode": None,
            "embedded_action_records": embedded_records,
            "passed": False,
            "build_log": str(build_log_path),
            "log": str(log_path),
            "failure_phase": "build",
            "status": "failed",
        }
        write_json(run_dir / "ucore-run-summary.json", summary)
        return summary
    run_command_text = (
        f"cd {shell_quote(repo_bash)} && "
        f"make run-prebuilt {toolprefix} CHAPTER={chapter} LOG=warn INIT_PROC={init_proc}"
    )
    observed = run_observed_command(
        make_wsl_command(run_command_text, wsl_distro),
        log_path,
        timeout_seconds + 30,
        append=False,
        pass_marker=pass_marker,
        idle_notice_seconds=int(os.environ.get("SEEDED_ACTION_IDLE_NOTICE_SECONDS", "20")),
    )
    code = int(observed["returncode"])
    text = log_path.read_text(encoding="utf-8", errors="replace")
    passed = (
        bool(observed["marker_seen"])
        and not bool(observed["failure_seen"])
        and not bool(observed["timed_out"])
    )
    guest_passed = passed
    failure_phase = "" if passed else "guest"
    failure_reason = str(observed["failure_reason"])
    extract_summary: dict[str, object] = {"status": "skipped", "extracted_state_files": 0}
    image_path = repo_dir / "nfs" / "fs-copy.img"
    if image_path.exists():
        try:
            extract_summary = extract_state_files(
                image_path,
                run_dir / "state-extracted",
                repo_dir,
                require_single_scope=True,
            )
            for item in sorted((run_dir / "state-extracted").iterdir()):
                if item.is_file() and item.name.startswith("rp_"):
                    shutil.copy2(item, next_state / item.name)
        except (OSError, ValueError) as error:
            passed = False
            if guest_passed:
                failure_phase = "extract"
                failure_reason = "extract_failed"
            extract_summary = {
                "status": "failed",
                "error": str(error),
                "extracted_state_files": 0,
            }
    else:
        passed = False
        if guest_passed:
            failure_phase = "extract"
            failure_reason = "missing_image"
        extract_summary = {"status": "missing_image", "extracted_state_files": 0}
    summary = {
        "commands": [
            clean_command,
            f"embedded_action_records={embedded_records}",
            build_command_text,
            run_command_text,
        ],
        "returncode": code if passed or code != 0 else 1,
        "build_returncode": build_code,
        "guest_returncode": code,
        "marker_seen": bool(observed["marker_seen"]),
        "failure_seen": bool(observed["failure_seen"]),
        "failure_line": str(observed["failure_line"]),
        "failure_reason": failure_reason,
        "failure_phase": failure_phase,
        "timed_out": bool(observed["timed_out"]),
        "idle_notices": int(observed["idle_notices"]),
        "elapsed_seconds": observed["elapsed_seconds"],
        "embedded_action_records": embedded_records,
        "extracted_state_files": extract_summary.get("extracted_state_files", 0),
        "extract_status": extract_summary.get("status", "unknown"),
        "passed": passed,
        "build_log": str(build_log_path),
        "log": str(log_path),
        "status": "ready" if passed else "failed",
    }
    write_run_result_state(next_state, summary, text)
    write_json(run_dir / "ucore-run-summary.json", summary)
    return summary


def run_plain_ucore(repo_dir: Path, run_dir: Path, timeout_seconds: int, wsl_distro: str) -> dict[str, object]:
    return run_seeded_ucore(repo_dir, run_dir, timeout_seconds, wsl_distro)


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
