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


def write_seed_header(next_state: Path, repo_dir: Path) -> int:
    inbox = next_state / "rp_host_action_inbox"
    text = inbox.read_text(encoding="utf-8") if inbox.is_file() else ""
    header = repo_dir / "user" / "build" / "generated" / "rp_host_action_seed.h"
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_text(
        "#ifndef __RP_HOST_ACTION_SEED_H__\n"
        "#define __RP_HOST_ACTION_SEED_H__\n"
        f"#define RP_HOST_ACTION_SEED {c_string_literal(text)}\n"
        "#endif\n",
        encoding="utf-8",
    )
    return len([line for line in text.splitlines() if line.strip()])


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
    run_command_text = (
        f"cd {shell_quote(repo_bash)} && "
        "make user TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform >/dev/null"
        " && "
        "rm -f nfs/fs.img nfs/fs-copy.img && "
        "make nfs/fs.img >/dev/null && "
        "make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_orch >/dev/null && "
        f"timeout {timeout_seconds}s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_orch"
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
    parser.add_argument("--run-ucore", action="store_true", help="Run the plain uCore rp_orch path after preparing actions.")
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
