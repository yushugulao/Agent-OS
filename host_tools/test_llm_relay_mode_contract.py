#!/usr/bin/env python3
"""Contract checks for host LLM relay mode selection and secret handling."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import plain_ucore_llm_relay as relay


STATE = {
    "rp_llmq": (
        "queue=host_relay_packets\n"
        "q1=review_summary;provider=template;prompt=review current evidence;secret_policy=no_secret_in_ucore\n"
        "status=ready\n"
    ),
    "rp_llm_req": "provider=template\nstatus=queued\n",
    "rp_llm_resp": "status=ready\n",
    "rp_llm_hostreq": "status=ready\n",
    "rp_llm_packets": "status=ready\n",
    "rp_llmlog": "status=ready\n",
    "rp_llmeval": "status=ready\n",
    "rp_llm_guard": "secrets_in_ucore=0\n",
    "rp_relay": "status=ready\n",
    "rp_prompt": "status=ready\n",
    "rp_actionio": "status=ready\n",
    "rp_web_bundle": "status=ready\n",
    "rp_api_runtime": "status=ready\n",
    "rp_report_text": "status=ready\n",
    "rp_runner": "status=ready\n",
    "rp_revision": "status=ready\n",
    "rp_package": "status=ready\n",
    "rp_api_run": "status=ready\n",
    "rp_api_evidence": "status=ready\n",
    "rp_agent_run": "status=ready\n",
    "rp_agentcmp": "status=ready\n",
    "rp_review_dashboard": "status=ready\n",
    "rp_review_pack": "status=ready\n",
    "rp_backend_exec": "status=ready\n",
}


ENV_KEYS = (
    "AGENT_PLATFORM_LLM_ENDPOINT",
    "AGENT_PLATFORM_LLM_API_KEY",
    "AGENT_PLATFORM_LLM_API_KEY_FILE",
    "AGENT_PLATFORM_LLM_PROVIDER",
    "AGENT_PLATFORM_LLM_MODEL",
    "AGENT_PLATFORM_LLM_TIMEOUT_SECONDS",
)


def write_state(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for name, text in STATE.items():
        (path / name).write_text(text, encoding="utf-8")


def read_all_files(path: Path) -> str:
    parts: list[str] = []
    for item in sorted(path.iterdir()):
        if item.is_file():
            parts.append(item.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def clear_llm_env() -> dict[str, str | None]:
    previous: dict[str, str | None] = {}
    for key in ENV_KEYS:
        previous[key] = os.environ.pop(key, None)
    return previous


def restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def run_with_state(mode: str) -> tuple[dict[str, object], str]:
    with tempfile.TemporaryDirectory() as state_tmp, tempfile.TemporaryDirectory() as out_tmp:
        state_dir = Path(state_tmp)
        out_dir = Path(out_tmp)
        write_state(state_dir)
        summary = relay.run_relay(state_dir, out_dir, mode=mode)
        output = read_all_files(out_dir)
        return summary, output


def main() -> int:
    previous = clear_llm_env()
    fake_secret = "unit-test-secret-value-for-external-relay-file"
    try:
        summary, output = run_with_state("auto")
        assert summary["status"] == "ready", summary
        assert "mode=template" in output, output
        assert "provider=deepseek" in output, output
        assert "model=deepseek-v4-pro" in output, output
        assert "key_present=0" in output, output
        assert "secret_material=not_written" in output, output
        assert fake_secret not in output, output

        with tempfile.TemporaryDirectory() as key_tmp:
            key_file = Path(key_tmp) / "relay.key"
            key_file.write_text(fake_secret, encoding="utf-8")
            os.environ["AGENT_PLATFORM_LLM_PROVIDER"] = "deepseek"
            os.environ["AGENT_PLATFORM_LLM_API_KEY_FILE"] = str(key_file)
            os.environ["AGENT_PLATFORM_LLM_ENDPOINT"] = "https://api.deepseek.com/chat/completions"
            summary, output = run_with_state("template")
            assert summary["status"] == "ready", summary
            assert "mode=template" in output, output
            assert "provider=deepseek" in output, output
            assert "model=deepseek-v4-pro" in output, output
            assert "key_present=1" in output, output
            assert "secret_material=not_written" in output, output
            assert fake_secret not in output, output
            assert str(key_file) not in output, output

        request = relay.RelayRequest(
            request_id="deepseek-q1",
            route="review_summary",
            provider="deepseek",
            prompt="summarize current evidence",
            budget="1024",
            secret_ref="host_env",
            source="unit",
        )
        clear_llm_env()
        response = relay.execute_request(request, "auto")
        assert response.mode == "template", response
        assert response.status == "ok", response
    finally:
        restore_env(previous)
    print("test_llm_relay_mode_contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
