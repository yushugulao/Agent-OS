#!/usr/bin/env python3
"""Render host-readable pages from plain uCore research platform state files."""

from __future__ import annotations

import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlparse


PAGE_SPECS = [
    ("index.html", "Home", "rp_api_home", ["rp_ui_home", "rp_web_bundle"]),
    ("run.html", "Run Detail", "rp_api_run", ["rp_ui_run", "rp_runner", "rp_artifact"]),
    ("agents.html", "Agents", "rp_api_agents", ["rp_ui_agent", "rp_agents", "rp_decisions"]),
    ("evidence.html", "Evidence", "rp_api_evidence", ["rp_ui_evidence", "rp_evidence", "rp_package"]),
    ("compare.html", "Compare", "rp_api_compare", ["rp_ui_compare", "rp_agentcmp", "rp_consistency"]),
    ("artifacts.html", "Artifacts", "rp_api_artifacts", ["rp_artifact", "rp_artifact_manifest", "rp_package"]),
    ("data.html", "Data", "rp_api_data", ["rp_input", "rp_dataset_snapshot", "rp_data_quality"]),
    ("actions.html", "Actions", "rp_api_action", ["rp_actionio", "rp_web_routes", "rp_web_bundle"]),
]


def parse_state_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for part in line.split(";"):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                key, value = part.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def load_state(state_dir: Path) -> dict[str, dict[str, object]]:
    state: dict[str, dict[str, object]] = {}
    for path in sorted(state_dir.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        state[path.name] = {
            "text": text,
            "values": parse_state_text(text),
            "lines": [line for line in text.splitlines() if line.strip()],
        }
    return state


def state_values(state: dict[str, dict[str, object]], name: str) -> dict[str, str]:
    item = state.get(name)
    if not item:
        return {}
    return item["values"]  # type: ignore[return-value]


def state_lines(state: dict[str, dict[str, object]], name: str) -> list[str]:
    item = state.get(name)
    if not item:
        return []
    return item["lines"]  # type: ignore[return-value]


def split_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def reader_contract(state: dict[str, dict[str, object]]) -> dict[str, object]:
    bundle = state_values(state, "rp_web_bundle")
    payload_files = split_list(bundle.get("reader_payload_files", ""))
    refresh_files = split_list(bundle.get("reader_refresh_files", ""))
    return {
        "contract": bundle.get("reader_contract", ""),
        "version": bundle.get("reader_contract_version", ""),
        "ready": bundle.get("reader_ready", ""),
        "views": bundle.get("reader_views", ""),
        "actions": bundle.get("reader_actions", ""),
        "payload_files": payload_files,
        "refresh_files": refresh_files,
        "required_sections": split_list(bundle.get("reader_required_sections", "")),
        "event_stream": bundle.get("reader_event_stream", ""),
        "fallback": bundle.get("reader_fallback", ""),
        "state_source": bundle.get("reader_state_source", ""),
        "missing_payload_files": [name for name in payload_files if name not in state],
        "missing_refresh_files": [name for name in refresh_files if name not in state],
    }


def validate_contract(contract: dict[str, object]) -> list[str]:
    problems: list[str] = []
    if contract.get("contract") != "host_plain_ucore_v2":
        problems.append("reader_contract is not host_plain_ucore_v2")
    if contract.get("ready") != "1":
        problems.append("reader_ready is not 1")
    if contract.get("missing_payload_files"):
        problems.append("missing payload files: " + ",".join(contract["missing_payload_files"]))  # type: ignore[index]
    if contract.get("missing_refresh_files"):
        problems.append("missing refresh files: " + ",".join(contract["missing_refresh_files"]))  # type: ignore[index]
    return problems


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def render_table(title: str, rows: Iterable[str]) -> str:
    body = []
    for row in rows:
        if "=" in row:
            key, value = row.split("=", 1)
        else:
            key, value = "record", row
        body.append(
            "<tr><th>{}</th><td>{}</td></tr>".format(
                html.escape(key.strip()),
                html.escape(value.strip()),
            )
        )
    if not body:
        body.append("<tr><td colspan='2'>No source rows</td></tr>")
    return "<section><h2>{}</h2><table>{}</table></section>".format(html.escape(title), "".join(body))


def page_html(title: str, nav: str, sections: list[str]) -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; color: #1f2933; background: #f6f8fb; }}
    header {{ background: #102a43; color: white; padding: 18px 24px; }}
    nav {{ display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 24px; background: #d9e2ec; }}
    nav a {{ color: #102a43; text-decoration: none; font-weight: 700; }}
    main {{ padding: 20px 24px 40px; max-width: 1180px; margin: 0 auto; }}
    section {{ background: white; border: 1px solid #d9e2ec; border-radius: 6px; padding: 16px; margin: 0 0 16px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th {{ width: 240px; text-align: left; color: #334e68; background: #f0f4f8; }}
    th, td {{ border: 1px solid #d9e2ec; padding: 7px 9px; vertical-align: top; }}
    code {{ background: #f0f4f8; padding: 2px 4px; border-radius: 3px; }}
  </style>
</head>
<body>
  <header><h1>{title}</h1><p>Rendered from plain uCore state files.</p></header>
  {nav}
  <main>{sections}</main>
</body>
</html>
""".format(title=html.escape(title), nav=nav, sections="\n".join(sections))


def render_site(state_dir: Path, out_dir: Path) -> dict[str, object]:
    state = load_state(state_dir)
    contract = reader_contract(state)
    problems = validate_contract(contract)
    out_dir.mkdir(parents=True, exist_ok=True)
    api_dir = out_dir / "api"

    for name, item in state.items():
        write_json(api_dir / f"{name}.json", {"name": name, "values": item["values"], "lines": item["lines"]})

    nav = "<nav>{}</nav>".format(
        " ".join(f"<a href='{html.escape(file)}'>{html.escape(title)}</a>" for file, title, _, _ in PAGE_SPECS)
    )
    for file_name, title, primary, extras in PAGE_SPECS:
        sections = [render_table(primary, state_lines(state, primary))]
        for extra in extras:
            sections.append(render_table(extra, state_lines(state, extra)))
        (out_dir / file_name).write_text(page_html(title, nav, sections), encoding="utf-8")

    summary = {
        "state_dir": str(state_dir),
        "state_files": len(state),
        "pages": len(PAGE_SPECS),
        "api_json_files": len(state),
        "contract": contract,
        "problems": problems,
        "status": "ready" if not problems else "invalid",
    }
    write_json(out_dir / "reader-summary.json", summary)
    return summary


def append_action_record(out_dir: Path, state_dir: Path, action_path: str, payload: dict[str, object], write_state: bool) -> dict[str, object]:
    out_dir.mkdir(parents=True, exist_ok=True)
    action_log = out_dir / "host-actions.jsonl"
    sequence = 1
    if action_log.exists():
        sequence += sum(1 for line in action_log.read_text(encoding="utf-8").splitlines() if line.strip())
    record = {
        "sequence": sequence,
        "path": action_path,
        "payload": payload,
        "status": "accepted",
    }
    with action_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    if write_state:
        inbox = state_dir / "rp_host_action_inbox"
        with inbox.open("a", encoding="utf-8") as handle:
            handle.write(f"action={sequence};path={action_path};status=accepted\n")
    write_json(out_dir / "last-action.json", record)
    return record


def parse_request_body(headers: object, body: bytes) -> dict[str, object]:
    content_type = ""
    if hasattr(headers, "get"):
        content_type = headers.get("Content-Type", "")  # type: ignore[assignment]
    text = body.decode("utf-8", errors="replace")
    if "application/json" in content_type:
        try:
            data = json.loads(text or "{}")
            if isinstance(data, dict):
                return data
            return {"value": data}
        except json.JSONDecodeError:
            return {"raw": text, "parse_error": "json"}
    if "application/x-www-form-urlencoded" in content_type:
        parsed = parse_qs(text, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}
    return {"raw": text}


def make_service_handler(state_dir: Path, out_dir: Path, write_state: bool) -> type[BaseHTTPRequestHandler]:
    class PlainUCoreReaderHandler(BaseHTTPRequestHandler):
        server_version = "PlainUCoreReader/0.2"

        def log_message(self, format: str, *args: object) -> None:
            return

        def send_json(self, status: int, data: object) -> None:
            payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def send_text_file(self, path: Path, content_type: str) -> None:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/reader-summary":
                self.send_json(200, render_site(state_dir, out_dir))
                return
            if path == "/api/contract":
                state = load_state(state_dir)
                contract = reader_contract(state)
                self.send_json(200, {"contract": contract, "problems": validate_contract(contract)})
                return
            if path == "/api/live":
                summary = render_site(state_dir, out_dir)
                action_log = out_dir / "host-actions.jsonl"
                action_count = 0
                if action_log.exists():
                    action_count = sum(1 for line in action_log.read_text(encoding="utf-8").splitlines() if line.strip())
                self.send_json(200, {"summary": summary, "action_count": action_count})
                return
            if path.startswith("/api/state/"):
                name = unquote(path[len("/api/state/") :])
                state = load_state(state_dir)
                item = state.get(name)
                if not item:
                    self.send_json(404, {"error": "state_not_found", "name": name})
                    return
                self.send_json(200, {"name": name, "values": item["values"], "lines": item["lines"]})
                return

            render_site(state_dir, out_dir)
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            if "/" in rel or "\\" in rel or ".." in rel:
                self.send_json(404, {"error": "not_found"})
                return
            file_path = out_dir / rel
            if not file_path.exists() or not file_path.is_file():
                self.send_json(404, {"error": "not_found"})
                return
            content_type = "text/html; charset=utf-8" if file_path.suffix == ".html" else "application/octet-stream"
            if file_path.suffix == ".json":
                content_type = "application/json; charset=utf-8"
            self.send_text_file(file_path, content_type)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/actions/"):
                self.send_json(404, {"error": "action_not_found"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b""
            payload = parse_request_body(self.headers, body)
            record = append_action_record(out_dir, state_dir, parsed.path, payload, write_state)
            render_site(state_dir, out_dir)
            self.send_json(202, record)

    return PlainUCoreReaderHandler


def serve_dir(state_dir: Path, out_dir: Path, port: int, write_state: bool) -> None:
    handler = make_service_handler(state_dir, out_dir, write_state)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"plain_ucore_reader: serving http://127.0.0.1:{port}/")
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Render plain uCore research platform state for host viewing.")
    parser.add_argument("--state-dir", type=Path, required=True, help="Directory containing rp_* state files.")
    parser.add_argument("--out-dir", type=Path, required=True, help="Output directory for static HTML and JSON.")
    parser.add_argument("--serve", action="store_true", help="Serve dynamic host pages, APIs, and POST action capture.")
    parser.add_argument("--port", type=int, default=8767, help="Local port for --serve.")
    parser.add_argument("--write-state-actions", action="store_true", help="Also append POST action records to the state directory.")
    args = parser.parse_args()

    summary = render_site(args.state_dir, args.out_dir)
    print(
        "plain_ucore_reader: pages={pages} api_json={api_json_files} state_files={state_files} status={status}".format(
            **summary
        )
    )
    if summary["status"] != "ready":
        for problem in summary["problems"]:  # type: ignore[index]
            print(f"plain_ucore_reader: problem={problem}")
        return 1
    if args.serve:
        serve_dir(args.state_dir, args.out_dir, args.port, args.write_state_actions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
