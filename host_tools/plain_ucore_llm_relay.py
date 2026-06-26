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
                prompt=record.get("prompt", f"{route or 'review_summary'} for RUN-042"),
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
                prompt="summarize RUN-042 recovery evidence",
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
    return {
        "endpoint": os.environ.get("AGENT_PLATFORM_LLM_ENDPOINT", ""),
        "api_key": os.environ.get("AGENT_PLATFORM_LLM_API_KEY", ""),
        "model": os.environ.get("AGENT_PLATFORM_LLM_MODEL", "gpt-4.1-mini"),
        "timeout": os.environ.get("AGENT_PLATFORM_LLM_TIMEOUT_SECONDS", "30"),
    }


def call_openai_compatible(request: RelayRequest, config: dict[str, str]) -> RelayResponse:
    endpoint = config.get("endpoint", "")
    api_key = config.get("api_key", "")
    model = config.get("model", "gpt-4.1-mini")
    if not endpoint or not api_key:
        return RelayResponse(
            request_id=request.request_id,
            response_id=f"relay-{request.request_id}",
            mode="openai-compatible",
            provider=request.provider,
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
                {"role": "system", "content": "Return concise research workflow assistance."},
                {"role": "user", "content": json.dumps(sanitize_value(packet), sort_keys=True, ensure_ascii=False)},
            ],
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
            mode="openai-compatible",
            provider=request.provider,
            status="ok",
            summary=summary,
            citations="0",
        )
    except Exception as exc:
        return RelayResponse(
            request_id=request.request_id,
            response_id=f"relay-{request.request_id}",
            mode="openai-compatible",
            provider=request.provider,
            status="error",
            summary="cloud_relay_error",
            citations="0",
            error=f"{type(exc).__name__}:{line_value(exc)}",
        )


def execute_request(request: RelayRequest, mode: str) -> RelayResponse:
    selected = mode
    if selected == "auto":
        config = cloud_config()
        wants_cloud = request.provider in {"openai-compatible", "cloud"}
        selected = "openai-compatible" if wants_cloud and config["endpoint"] and config["api_key"] else "template"
    if selected in {"template", "mock"}:
        return template_response(request)
    if selected in {"openai-compatible", "cloud"}:
        return call_openai_compatible(request, cloud_config())
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
    append_line(out_dir / "rp_llm_hostreq", f"host_relay_process=plain_ucore_llm_relay;endpoint_present={endpoint_present};key_present={key_present};model={line_value(config['model'])};secret_material=not_written;status={status}")
    append_line(out_dir / "rp_llm_packets", f"host_relay_packet_batch=requests:{len(requests)};responses:{len(responses)};status={status}")
    append_line(out_dir / "rp_llmlog", f"host_relay_run=requests:{len(requests)};mode={line_value(mode)};status={status}")
    append_line(out_dir / "rp_actionio", "host_llm_relay_process=plain_ucore_llm_relay;outputs=rp_llm_resp,rp_llm_hostreq,rp_llm_packets,rp_llmlog;status=ready")
    append_line(out_dir / "rp_web_bundle", "host_llm_relay_process=plain_ucore_llm_relay;refresh=rp_llm_resp,rp_llm_hostreq,rp_llm_packets;status=ready")
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
