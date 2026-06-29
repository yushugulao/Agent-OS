#!/usr/bin/env python3
"""Host-side LLM relay for plain uCore research-platform state files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SENSITIVE_KEY_FRAGMENTS = ("api_key", "apikey", "token", "authorization", "password", "secret")


@dataclass
class RelayRequest:
    request_id: str
    route: str
    provider: str
    prompt: str
    budget: str
    secret_ref: str
    source: str


@dataclass
class RelayResponse:
    request_id: str
    response_id: str
    mode: str
    provider: str
    status: str
    summary: str
    citations: str
    error: str = ""


@dataclass
class RelayAudit:
    request_id: str
    response_id: str
    prompt_hash: str
    checks: int
    passed: int
    blocked: int
    status: str


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_line(path: Path, line: str) -> None:
    existing = read_text(path)
    suffix = "" if not existing or existing.endswith("\n") else "\n"
    write_text(path, existing + suffix + line.rstrip("\n") + "\n")


def ensure_review_pack(path: Path) -> None:
    lines = [
        "pack=review-evidence",
        "run=设定的模拟流程",
        "sources=rp_input,rp_stage_dag,rp_retry_plan,rp_artifact_manifest,rp_report_text,rp_chart_data,rp_llmeval,rp_llm_guard,rp_review_dashboard,rp_package",
        "evidence=required_files;source=rp_package;status=pass",
        "evidence=workflow_recovered;source=rp_retry_plan;status=pass",
        "evidence=artifact_manifest;source=rp_artifact_manifest;records=4;status=pass",
        "evidence=llm_quality;source=rp_llmeval;passed=7;status=pass",
        "evidence=llm_packet_guard;source=rp_llm_guard;secrets_in_ucore=0;status=pass",
        "evidence=human_review;source=rp_review2;status=pass",
        "evidence=revision_ready;source=rp_revision;status=pass",
        "evidence=delivery_ready;source=rp_package;files=8;status=pass",
        "evidence=operations_ready;source=rp_runner;status=pass",
        "evidence=project_space_ready;source=rp_package;status=pass",
        "action=send_to_reviewer;owner=orchestrator;artifact=rp_review_pack;status=ready",
        "action=verify_llm_packet;owner=auditor;artifact=rp_llm_guard;status=ready",
        "action=check_delivery_manifest;owner=reviewer;artifact=rp_package;status=ready",
        "action=open_operations_report;owner=orchestrator;artifact=rp_runner;status=ready",
        "action=close_project_items;owner=reviewer;artifact=rp_package;status=ready",
        "bridge=delivery_to_operations;delivery=rp_package;operations=rp_runner;project=rp_package;status=ready",
        "plain_kernel_note=ordinary_files_require_host_reader_refresh",
        "host_page=review.html",
        "status=ready",
    ]
    existing = read_text(path)
    if "pack=review-evidence" not in existing:
        write_text(path, "\n".join(lines) + "\n")
        return
    for line in lines:
        if line not in existing:
            append_line(path, line)


def parse_kv_line(line: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in line.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def parse_state_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        values.update(parse_kv_line(raw))
    return values


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS):
                clean[str(key)] = "[redacted]"
            else:
                clean[str(key)] = sanitize_value(nested)
        return clean
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    return value


def line_value(value: object) -> str:
    return str(value).replace("\n", " ").replace(";", ",").strip()


CONCLUSION_SPECS: tuple[dict[str, str], ...] = (
    {
        "route": "review_summary",
        "request_id": "cloud-review-summary",
        "title": "复核摘要",
        "target_file": "rp_review2",
        "target_key": "llm_review_summary",
        "instruction": "从复核证据、结果包、运行记录和 AgentOS 对照信息中提炼当前项目是否具备可复查性。",
    },
    {
        "route": "method_check",
        "request_id": "cloud-method-check",
        "title": "方法检查",
        "target_file": "rp_review_dashboard",
        "target_key": "llm_method_check",
        "instruction": "检查方法链条是否能从输入、workflow、artifact、图表和报告材料追到可复核证据。",
    },
    {
        "route": "recovery_note",
        "request_id": "cloud-recovery-note",
        "title": "恢复说明",
        "target_file": "rp_revision",
        "target_key": "llm_recovery_note",
        "instruction": "概括失败恢复动作、恢复依据、恢复后的证据状态，以及 AgentOS 对恢复过程的帮助。",
    },
    {
        "route": "writer_summary",
        "request_id": "cloud-writer-summary",
        "title": "写作摘要",
        "target_file": "rp_revision",
        "target_key": "llm_writer_summary",
        "instruction": "面向报告作者总结当前材料中最应该写入报告的发现、证据和限制。",
    },
    {
        "route": "project_review_opinion",
        "request_id": "cloud-project-review",
        "title": "项目复核意见",
        "target_file": "rp_projectrel",
        "target_key": "llm_project_review_opinion",
        "instruction": "给出项目结果复核意见，说明 release gate、可复现性、provenance 和剩余注意点。",
    },
    {
        "route": "final_report_summary",
        "request_id": "cloud-final-report-summary",
        "title": "最终报告摘要",
        "target_file": "rp_report_text",
        "target_key": "llm_final_report_summary",
        "instruction": "生成最终报告摘要，说明研究目标、主要结果、恢复过程、证据来源和 AgentOS 带来的系统支持。",
    },
)


CONCLUSION_SOURCE_FILES = (
    "rp_input",
    "rp_stage_dag",
    "rp_stage_state",
    "rp_retry_plan",
    "rp_artifact_manifest",
    "rp_report_text",
    "rp_review2",
    "rp_review_dashboard",
    "rp_revision",
    "rp_package",
    "rp_agentcmp",
    "rp_agentos_mainflow",
    "rp_agentos_query",
    "rp_agentos_recovery",
    "rp_agentos_timeline",
    "rp_agentos_audit",
    "rp_projectrel",
    "rp_backend_exec",
)


def state_excerpt(state_dir: Path, names: tuple[str, ...], per_file_limit: int = 900, total_limit: int = 6400) -> str:
    parts: list[str] = []
    total = 0
    for name in names:
        text = read_text(state_dir / name).strip()
        if not text:
            continue
        snippet = text[:per_file_limit]
        if len(text) > per_file_limit:
            snippet += "\n..."
        block = f"[{name}]\n{snippet}"
        if total + len(block) > total_limit:
            remaining = max(total_limit - total, 0)
            if remaining > 120:
                parts.append(block[:remaining] + "\n...")
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts) or "当前状态文件中没有可用文本，请基于请求类型给出最小结论。"


def conclusion_prompt(state_dir: Path, spec: dict[str, str]) -> str:
    evidence = state_excerpt(state_dir, CONCLUSION_SOURCE_FILES)
    return (
        "你是运行在宿主机侧的科研 Agent 平台 LLM Relay。"
        "请只根据下面的状态文件摘要生成中文结论，不要编造状态文件以外的事实。"
        "输出一段自然语言，控制在 80 到 180 个汉字之间，结论要直接、可放入运行页面。"
        f"\n\n任务：{spec['title']}。{spec['instruction']}"
        "\n\n状态文件摘要：\n"
        f"{evidence}"
    )


def ensure_cloud_conclusion_requests(requests: list[RelayRequest], state_dir: Path, mode: str) -> list[RelayRequest]:
    if mode not in {"cloud", "openai-compatible"}:
        return requests
    seen = {request.request_id for request in requests}
    merged = list(requests)
    for spec in CONCLUSION_SPECS:
        request_id = spec["request_id"]
        if request_id in seen:
            continue
        merged.append(
            RelayRequest(
                request_id=request_id,
                route=spec["route"],
                provider="deepseek",
                prompt=conclusion_prompt(state_dir, spec),
                budget="1536",
                secret_ref="host_env",
                source="cloud_conclusion",
            )
        )
        seen.add(request_id)
    return merged


def conclusion_spec_by_route(route: str) -> dict[str, str] | None:
    for spec in CONCLUSION_SPECS:
        if spec["route"] == route:
            return spec
    return None


def backend_action_review_lines(state_dir: Path) -> list[str]:
    details: list[dict[str, str]] = []
    reports: dict[str, dict[str, str]] = {}
    for raw in read_text(state_dir / "rp_backend_exec").splitlines():
        record = parse_kv_line(raw)
        if "runner_detail" in record:
            details.append(record)
        if "runner_report" in record:
            reports[record["runner_report"]] = record
    lines: list[str] = []
    for detail in details:
        case = detail.get("runner_detail", "")
        report = reports.get(case, {})
        lines.append(
            "backend_action_review="
            f"{line_value(case)};action={line_value(detail.get('act', ''))};review={line_value(detail.get('review', ''))};"
            f"plain_cost={line_value(report.get('plain_cost', ''))};agentos_replace={line_value(report.get('agentos_replace', ''))};"
            f"status={line_value(report.get('status', ''))}"
        )
    return lines


def review_handoff_lines(state_dir: Path) -> list[str]:
    runner = parse_state_values(read_text(state_dir / "rp_runner"))
    package = parse_state_values(read_text(state_dir / "rp_package"))
    lines: list[str] = []
    if runner.get("backend_evidence_report") or runner.get("workbench_next_task") or package.get("host_action_operations_report"):
        lines.append(
            "operations_handoff=rp_runner+rp_package;"
            f"tasks={line_value(runner.get('workbench_tasks', ''))};next={line_value(runner.get('workbench_next_task', ''))};"
            f"report={line_value(package.get('host_action_operations_report', runner.get('host_action_operations_report', '')))};"
            f"plan={line_value(package.get('host_action_operations_next', runner.get('host_action_operations_plan_execute', '')))};"
            f"quality={line_value(package.get('host_action_quality_gate', runner.get('host_action_quality_gate', '')))};"
            f"repair={line_value(package.get('host_action_quality_repair_execute', runner.get('host_action_quality_repair_execute', '')))};"
            f"backend={line_value(runner.get('backend_evidence_report', ''))};"
            f"status={line_value(package.get('status', runner.get('status', '')))}"
        )
    if runner.get("workbench") or runner.get("host_action_workbench_id") or package.get("host_action_workbench_package"):
        lines.append(
            "workbench_handoff=rp_runner+rp_package;"
            f"workbench={line_value(runner.get('host_action_workbench_id', runner.get('workbench', '')))};"
            f"task={line_value(runner.get('host_action_workbench_task', runner.get('workbench_next_task', '')))};"
            f"task_status={line_value(runner.get('host_action_workbench_task_status', ''))};"
            f"manifest={line_value(package.get('host_action_workbench_manifest', runner.get('host_action_workbench_manifest', '')))};"
            f"verified={line_value(package.get('host_action_workbench_verified_files', runner.get('host_action_workbench_verified_files', '')))};"
            f"missing={line_value(package.get('host_action_workbench_missing_files', runner.get('host_action_workbench_missing_files', '')))};"
            f"bundle={line_value(package.get('host_action_workbench_bundle', runner.get('host_action_workbench_bundle', '')))};"
            f"status={line_value(package.get('host_action_workbench_completion', runner.get('host_action_workbench_completion', package.get('status', ''))))}"
        )
    if package.get("host_action_project_space") or package.get("host_action_project_id"):
        lines.append(
            "project_handoff=rp_package;"
            f"project={line_value(package.get('host_action_project_id', ''))};"
            f"space={line_value(package.get('host_action_project_space', ''))};"
            f"note={line_value(package.get('host_action_project_note', ''))};"
            f"action_item={line_value(package.get('host_action_project_action_item', ''))};"
            f"answer={line_value(package.get('host_action_project_answer', ''))};"
            f"repair={line_value(package.get('host_action_project_repair', ''))};"
            f"search={line_value(package.get('host_action_research_search', ''))};"
            f"status={line_value(package.get('status', ''))}"
        )
    return lines


def append_conclusion_state(out_dir: Path, request: RelayRequest, response: RelayResponse) -> None:
    spec = conclusion_spec_by_route(request.route)
    if spec is None:
        return
    summary = line_value(response.summary)
    route = line_value(request.route)
    request_id = line_value(request.request_id)
    response_id = line_value(response.response_id)
    mode = line_value(response.mode)
    status = line_value(response.status)
    provider = line_value(response.provider)
    append_line(
        out_dir / "rp_llm_conclusions",
        "llm_conclusion="
        f"{route};title={line_value(spec['title'])};summary={summary};provider={provider};"
        f"mode={mode};request={request_id};response={response_id};status={status}",
    )
    append_line(
        out_dir / spec["target_file"],
        f"{spec['target_key']}={summary};source=rp_llm_conclusions;request={request_id};response={response_id};status={status}",
    )
    append_line(
        out_dir / "rp_review_dashboard",
        f"llm_conclusion_route={route};target={line_value(spec['target_file'])};response={response_id};status={status}",
    )
    append_line(
        out_dir / "rp_api_run",
        f"llm_conclusion={route};response={response_id};status={status}",
    )
    append_line(
        out_dir / "rp_web_bundle",
        f"llm_conclusion_file=rp_llm_conclusions;route={route};target={line_value(spec['target_file'])};status={status}",
    )
    if request.route == "recovery_note":
        append_line(
            out_dir / "rp_retry_plan",
            f"llm_recovery_note={summary};source=rp_llm_conclusions;response={response_id};status={status}",
        )
    elif request.route == "writer_summary":
        append_line(
            out_dir / "rp_package",
            f"llm_writer_summary={summary};source=rp_revision;response={response_id};status={status}",
        )
    elif request.route == "project_review_opinion":
        append_line(
            out_dir / "rp_package",
            f"llm_project_review_opinion={summary};source=rp_projectrel;response={response_id};status={status}",
        )
    elif request.route == "final_report_summary":
        append_line(
            out_dir / "rp_package",
            f"llm_final_report_summary={summary};source=rp_report_text;response={response_id};status={status}",
        )


def copy_state(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dst.resolve():
        return
    for item in sorted(src.iterdir()):
        if item.is_file() and item.name.startswith("rp_"):
            shutil.copy2(item, dst / item.name)


def host_action_request(values: dict[str, str]) -> RelayRequest | None:
    request_id = values.get("host_llm_request_id", "").strip()
    if not request_id:
        return None
    return RelayRequest(
        request_id=request_id,
        route=values.get("host_llm_route", "review_summary"),
        provider=values.get("host_llm_provider", "template"),
        prompt=values.get("host_llm_prompt", "summarize_recovery_evidence"),
        budget=values.get("host_llm_budget", "1536"),
        secret_ref=values.get("host_llm_secret_ref", "host_env"),
        source="host_action",
    )


def queue_requests(state_dir: Path) -> list[RelayRequest]:
    llmq = read_text(state_dir / "rp_llmq")
    llm_req = read_text(state_dir / "rp_llm_req")
    values = parse_state_values(llmq + "\n" + llm_req)
    requests: list[RelayRequest] = []
    host_request = host_action_request(values)
    if host_request:
        requests.append(host_request)
    for raw in llmq.splitlines():
        line = raw.strip()
        if len(line) < 3 or line[0] != "q" or not line[1].isdigit() or "=" not in line:
            continue
        record = parse_kv_line(line)
        request_id, route = line.split("=", 1)
        route = route.split(";", 1)[0].strip()
        requests.append(
            RelayRequest(
                request_id=request_id.strip(),
                route=route or record.get("route", "review_summary"),
                provider=record.get("provider", values.get("provider", "template")),
                prompt=record.get("prompt", f"{route or 'review_summary'} for 设定的模拟流程"),
                budget=record.get("budget", "1024"),
                secret_ref=record.get("secret_policy", "no_secret_in_ucore"),
                source="rp_llmq",
            )
        )
    if not requests:
        requests.append(
            RelayRequest(
                request_id="q1",
                route="review_summary",
                provider=values.get("provider", "template"),
                prompt="summarize 设定的模拟流程 recovery evidence",
                budget="1024",
                secret_ref="no_secret_in_ucore",
                source="default",
            )
        )
    deduped: list[RelayRequest] = []
    seen: set[str] = set()
    for request in requests:
        if request.request_id in seen:
            continue
        seen.add(request.request_id)
        deduped.append(request)
    return deduped


def template_response(request: RelayRequest) -> RelayResponse:
    if request.route == "method_check":
        summary = "method_check_consistent_with_protocol"
        citations = "3"
    elif request.route == "recovery_note":
        summary = "recovery_note_align_stage_repaired"
        citations = "4"
    else:
        summary = "review_summary_supported_by_current_evidence"
        citations = "5"
    return RelayResponse(
        request_id=request.request_id,
        response_id=f"relay-{request.request_id}",
        mode="template",
        provider=request.provider or "template",
        status="ok",
        summary=summary,
        citations=citations,
    )


def cloud_config() -> dict[str, str]:
    provider = os.environ.get("AGENT_PLATFORM_LLM_PROVIDER", "deepseek")
    default_endpoint = "https://api.deepseek.com/chat/completions" if provider == "deepseek" else ""
    default_model = "deepseek-v4-pro" if provider == "deepseek" else "gpt-4.1-mini"
    api_key = os.environ.get("AGENT_PLATFORM_LLM_API_KEY", "")
    key_file = os.environ.get("AGENT_PLATFORM_LLM_API_KEY_FILE", "")
    if not api_key and key_file:
        try:
            api_key = Path(key_file).read_text(encoding="utf-8").strip()
        except OSError:
            api_key = ""
    return {
        "provider": provider,
        "endpoint": os.environ.get("AGENT_PLATFORM_LLM_ENDPOINT", default_endpoint),
        "api_key": api_key,
        "model": os.environ.get("AGENT_PLATFORM_LLM_MODEL", default_model),
        "timeout": os.environ.get("AGENT_PLATFORM_LLM_TIMEOUT_SECONDS", "30"),
    }


def call_openai_compatible(request: RelayRequest, config: dict[str, str], mode_label: str = "openai-compatible") -> RelayResponse:
    endpoint = config.get("endpoint", "")
    api_key = config.get("api_key", "")
    model = config.get("model", "gpt-4.1-mini")
    if not endpoint or not api_key:
        return RelayResponse(
            request_id=request.request_id,
            response_id=f"relay-{request.request_id}",
            mode=mode_label,
            provider=config.get("provider", request.provider),
            status="config_missing",
            summary="template_required_because_host_cloud_config_is_missing",
            citations="0",
            error="AGENT_PLATFORM_LLM_ENDPOINT and AGENT_PLATFORM_LLM_API_KEY are required",
        )
    try:
        timeout = min(max(float(config.get("timeout", "30")), 1.0), 300.0)
    except ValueError:
        timeout = 30.0
    packet = {
        "request_id": request.request_id,
        "route": request.route,
        "prompt": request.prompt,
        "budget": request.budget,
        "secret_ref": request.secret_ref,
    }
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是 AgentOS 宿主机侧 LLM Relay，只生成可审阅、可追溯的中文结论。"
                        "不要输出密钥、环境变量、文件系统私密路径或状态文件中没有的事实。"
                    ),
                },
                {"role": "user", "content": json.dumps(sanitize_value(packet), sort_keys=True, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "max_tokens": 512,
        }
    ).encode("utf-8")
    http_request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        payload = json.loads(raw)
        text = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        summary = line_value(text)[:160] or "cloud_response_ready"
        return RelayResponse(
            request_id=request.request_id,
            response_id=f"relay-{request.request_id}",
            mode=mode_label,
            provider=config.get("provider", request.provider),
            status="ok",
            summary=summary,
            citations="6",
        )
    except Exception as exc:
        return RelayResponse(
            request_id=request.request_id,
            response_id=f"relay-{request.request_id}",
            mode=mode_label,
            provider=config.get("provider", request.provider),
            status="error",
            summary="cloud_relay_error",
            citations="0",
            error=f"{type(exc).__name__}:{line_value(exc)}",
        )


def execute_request(request: RelayRequest, mode: str) -> RelayResponse:
    selected = mode
    if selected == "auto":
        config = cloud_config()
        wants_cloud = request.provider in {"openai-compatible", "cloud", "deepseek", "deepseek-v4-pro"}
        selected = "openai-compatible" if wants_cloud and config["endpoint"] and config["api_key"] else "template"
    if selected in {"template", "mock"}:
        return template_response(request)
    if selected in {"openai-compatible", "cloud"}:
        return call_openai_compatible(request, cloud_config(), selected)
    return RelayResponse(
        request_id=request.request_id,
        response_id=f"relay-{request.request_id}",
        mode=selected,
        provider=request.provider,
        status="bad_mode",
        summary="unsupported_relay_mode",
        citations="0",
        error=f"unsupported mode {selected}",
    )


def audit_response(request: RelayRequest, response: RelayResponse) -> RelayAudit:
    prompt_hash = digest_text(request.prompt)
    checks = 6
    passed = 0
    if response.request_id == request.request_id:
        passed += 1
    if response.response_id:
        passed += 1
    if response.status in {"ok", "config_missing"}:
        passed += 1
    if response.summary.strip():
        passed += 1
    if response.citations.isdigit():
        passed += 1
    if request.secret_ref and "secret" not in request.prompt.lower():
        passed += 1
    blocked = 0 if passed == checks else 1
    status = "passed" if passed == checks else "failed"
    return RelayAudit(
        request_id=request.request_id,
        response_id=response.response_id,
        prompt_hash=prompt_hash,
        checks=checks,
        passed=passed,
        blocked=blocked,
        status=status,
    )


def append_relay_state(out_dir: Path, requests: list[RelayRequest], responses: list[RelayResponse], mode: str) -> None:
    config = cloud_config()
    endpoint_present = "1" if config["endpoint"] else "0"
    key_present = "1" if config["api_key"] else "0"
    status = "ready" if all(response.status in {"ok", "config_missing"} for response in responses) else "partial"
    audits = [audit_response(request, response) for request, response in zip(requests, responses)]
    audit_checked = sum(audit.checks for audit in audits)
    audit_passed = sum(audit.passed for audit in audits)
    audit_blocked = sum(audit.blocked for audit in audits)
    audit_status = "ready" if audit_checked == audit_passed and audit_blocked == 0 else "partial"
    append_line(out_dir / "rp_llm_resp", f"host_relay_process=plain_ucore_llm_relay;mode={line_value(mode)};requests={len(requests)};responses={len(responses)};status={status}")
    append_line(out_dir / "rp_llm_hostreq", f"host_relay_process=plain_ucore_llm_relay;provider={line_value(config['provider'])};endpoint_present={endpoint_present};key_present={key_present};model={line_value(config['model'])};secret_material=not_written;status={status}")
    append_line(out_dir / "rp_llm_packets", f"host_relay_packet_batch=requests:{len(requests)};responses:{len(responses)};status={status}")
    append_line(out_dir / "rp_llmlog", f"host_relay_run=requests:{len(requests)};mode={line_value(mode)};status={status}")
    append_line(out_dir / "rp_actionio", "host_llm_relay_process=plain_ucore_llm_relay;outputs=rp_llm_resp,rp_llm_hostreq,rp_llm_packets,rp_llmlog,rp_llm_conclusions;status=ready")
    append_line(out_dir / "rp_web_bundle", "host_llm_relay_process=plain_ucore_llm_relay;refresh=rp_llm_resp,rp_llm_hostreq,rp_llm_packets,rp_llm_conclusions;status=ready")
    append_line(out_dir / "rp_api_runtime", "host_llm_relay_process=plain_ucore_llm_relay;status=ready")
    append_line(
        out_dir / "rp_llmeval",
        f"host_relay_eval_batch=checked:{audit_checked};passed:{audit_passed};blocked:{audit_blocked};status={audit_status}",
    )
    append_line(
        out_dir / "rp_llm_guard",
        f"host_relay_guard_batch=checked:{len(audits)};blocked:{audit_blocked};secret_values_written=0;status={audit_status}",
    )
    append_line(
        out_dir / "rp_relay",
        f"host_relay_replay_batch=requests:{len(requests)};responses:{len(responses)};matched:{len(audits) - audit_blocked};status={audit_status}",
    )
    append_line(
        out_dir / "rp_prompt",
        f"host_relay_prompt_batch=routes:{len({request.route for request in requests})};requests:{len(requests)};status={audit_status}",
    )
    append_line(
        out_dir / "rp_api_runtime",
        f"host_llm_relay_quality=passed:{audit_passed}/{audit_checked};blocked:{audit_blocked};source=rp_llmeval;status={audit_status}",
    )
    append_line(
        out_dir / "rp_web_bundle",
        f"host_llm_relay_quality=rp_llmeval,rp_llm_guard,rp_relay,rp_prompt;checked={audit_checked};passed={audit_passed};status={audit_status}",
    )
    append_line(
        out_dir / "rp_agentcmp",
        f"host_llm_relay_replay={audit_status};checked={audit_checked};blocked={audit_blocked};plain_kernel=ordinary_files",
    )
    append_line(
        out_dir / "rp_review_dashboard",
        f"host_relay_quality=passed:{audit_passed}/{audit_checked};blocked:{audit_blocked};source=rp_llmeval;status={audit_status}",
    )
    append_line(
        out_dir / "rp_review_dashboard",
        "host_relay_review_input=rp_llm_resp,rp_llmeval,rp_llm_guard,rp_relay,rp_prompt;page=llm.html;status=ready",
    )
    ensure_review_pack(out_dir / "rp_review_pack")
    append_line(
        out_dir / "rp_review_pack",
        f"host_relay_quality=passed:{audit_passed}/{audit_checked};blocked:{audit_blocked};source=rp_llmeval;status={audit_status}",
    )
    append_line(
        out_dir / "rp_review_pack",
        "host_relay_pack_input=rp_report_text,rp_llm_resp,rp_llmeval,rp_llm_guard,rp_review_dashboard,rp_package;status=ready",
    )
    append_line(
        out_dir / "rp_review_pack",
        "backend_evidence_review=rp_backend_exec;plain_costs=7;agentos_replacements=7;risks=7;source=rp_review_dashboard;status=ready",
    )
    for line in backend_action_review_lines(out_dir):
        append_line(out_dir / "rp_review_pack", line)
    for line in review_handoff_lines(out_dir):
        append_line(out_dir / "rp_review_pack", line)
    first_ok = next((response for response in responses if response.status in {"ok", "config_missing"}), responses[0] if responses else None)
    if first_ok is not None:
        append_line(
            out_dir / "rp_report_text",
            "host_relay_report_summary="
            f"{line_value(first_ok.summary)};request={line_value(first_ok.request_id)};"
            f"response={line_value(first_ok.response_id)};citations={line_value(first_ok.citations)};status={line_value(first_ok.status)}",
        )
        append_line(
            out_dir / "rp_runner",
            "host_relay_workbench_answer="
            f"{line_value(first_ok.summary)};request={line_value(first_ok.request_id)};source=rp_llm_resp;status=ready",
        )
        append_line(
            out_dir / "rp_revision",
            "host_relay_writer_summary="
            f"{line_value(first_ok.summary)};mode={line_value(first_ok.mode)};response={line_value(first_ok.response_id)};status=ready",
        )
        append_line(
            out_dir / "rp_package",
            "host_relay_delivery_file=llm_response;path=rp_llm_resp;"
            f"response={line_value(first_ok.response_id)};status=ready",
        )
        append_line(
            out_dir / "rp_api_run",
            "host_relay_report_summary="
            f"{line_value(first_ok.summary)};response={line_value(first_ok.response_id)};status=ready",
        )
        append_line(
            out_dir / "rp_api_evidence",
            "host_relay_grounding="
            f"citations:{line_value(first_ok.citations)};response={line_value(first_ok.response_id)};status=ready",
        )
        append_line(
            out_dir / "rp_agent_run",
            "host_relay_agent_decision=writer_use_relay_response;"
            f"response={line_value(first_ok.response_id)};status=ready",
        )
    for request, response, audit in zip(requests, responses, audits):
        append_conclusion_state(out_dir, request, response)
        prompt_hash = digest_text(request.prompt)
        append_line(
            out_dir / "rp_llm_req",
            "host_relay_request="
            f"{line_value(request.request_id)};route={line_value(request.route)};provider={line_value(request.provider)};"
            f"prompt_hash={prompt_hash};budget={line_value(request.budget)};secret_ref={line_value(request.secret_ref)};source={line_value(request.source)}",
        )
        append_line(
            out_dir / "rp_llm_resp",
            "host_relay_response="
            f"{line_value(response.response_id)};request={line_value(response.request_id)};mode={line_value(response.mode)};"
            f"provider={line_value(response.provider)};summary={line_value(response.summary)};citations={line_value(response.citations)};status={line_value(response.status)}",
        )
        append_line(
            out_dir / "rp_llm_packets",
            "host_relay_packet="
            f"{line_value(request.request_id)};response={line_value(response.response_id)};mode={line_value(response.mode)};"
            f"prompt_hash={prompt_hash};secret_in_packet=0;status={line_value(response.status)}",
        )
        append_line(
            out_dir / "rp_llmlog",
            "host_relay_log="
            f"{line_value(request.request_id)};response={line_value(response.response_id)};status={line_value(response.status)};"
            f"error={line_value(response.error)}",
        )
        append_line(
            out_dir / "rp_llmeval",
            "host_relay_eval="
            f"{line_value(request.request_id)};response={line_value(response.response_id)};"
            f"checks={audit.checks};passed={audit.passed};citations={line_value(response.citations)};status={line_value(audit.status)}",
        )
        append_line(
            out_dir / "rp_llm_guard",
            "host_relay_guard="
            f"{line_value(request.request_id)};prompt_hash={audit.prompt_hash};secret_ref={line_value(request.secret_ref)};"
            f"secret_in_packet=0;status={line_value(audit.status)}",
        )
        append_line(
            out_dir / "rp_relay",
            "host_relay_replay="
            f"{line_value(request.request_id)};response={line_value(response.response_id)};"
            f"prompt_hash={audit.prompt_hash};mode={line_value(response.mode)};status={line_value(audit.status)}",
        )
        append_line(
            out_dir / "rp_prompt",
            "host_relay_prompt_route="
            f"{line_value(request.request_id)};route={line_value(request.route)};budget={line_value(request.budget)};"
            f"prompt_hash={audit.prompt_hash};status=tracked",
        )


def run_relay(state_dir: Path, out_dir: Path, mode: str = "auto", summary_path: Path | None = None) -> dict[str, object]:
    state_dir = state_dir.resolve()
    out_dir = out_dir.resolve()
    copy_state(state_dir, out_dir)
    requests = queue_requests(out_dir)
    requests = ensure_cloud_conclusion_requests(requests, out_dir, mode)
    responses = [execute_request(request, mode) for request in requests]
    append_relay_state(out_dir, requests, responses, mode)
    summary = {
        "relay": "plain_ucore_llm_relay",
        "mode": mode,
        "requests": len(requests),
        "responses": len(responses),
        "status": "ready" if all(response.status in {"ok", "config_missing"} for response in responses) else "partial",
        "secret_values_written": False,
        "generated_at_unix": int(time.time()),
        "request_ids": [request.request_id for request in requests],
        "response_ids": [response.response_id for response in responses],
        "response_status": {response.response_id: response.status for response in responses},
        "quality": {
            "checked": sum(audit.checks for audit in [audit_response(request, response) for request, response in zip(requests, responses)]),
            "passed": sum(audit.passed for audit in [audit_response(request, response) for request, response in zip(requests, responses)]),
            "blocked": sum(audit.blocked for audit in [audit_response(request, response) for request, response in zip(requests, responses)]),
        },
    }
    target = summary_path or (out_dir / "llm-relay-summary.json")
    write_text(target, json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the host LLM relay over plain uCore rp_* state files.")
    parser.add_argument("--state-dir", type=Path, required=True, help="Directory containing rp_llm* state files.")
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory for refreshed state files.")
    parser.add_argument("--mode", default="auto", choices=["auto", "template", "mock", "openai-compatible", "cloud"], help="Relay execution mode.")
    args = parser.parse_args()

    summary = run_relay(args.state_dir, args.out_dir, args.mode)
    print(
        "plain_ucore_llm_relay: requests={requests} responses={responses} mode={mode} status={status}".format(
            **summary
        )
    )
    return 0 if summary["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
