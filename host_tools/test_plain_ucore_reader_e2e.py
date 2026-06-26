#!/usr/bin/env python3
"""End-to-end check for host POST action -> plain uCore run -> extracted state -> reader API."""

from __future__ import annotations

import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import request

import plain_ucore_reader


def read_json(url: str, timeout: int = 10) -> dict[str, object]:
    with request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def read_text(url: str, timeout: int = 10) -> str:
    with request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def main() -> int:
    repo_dir = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="plain-ucore-reader-e2e-") as tmp:
        root = Path(tmp)
        state_dir = root / "state"
        out_dir = root / "reader"
        run_root = root / "runs"
        state_dir.mkdir()

        handler = plain_ucore_reader.make_service_handler(
            state_dir,
            out_dir,
            write_state=False,
            auto_run_ucore=True,
            repo_dir=repo_dir,
            run_root=run_root,
            runner_timeout=90,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            action = request.Request(
                base + "/actions/research/run",
                data=json.dumps({"run_id": "RUN-E2E", "source": "reader-e2e"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(action, timeout=180) as response:
                result = json.loads(response.read().decode("utf-8"))
            assert result["action"]["path"] == "/actions/research/run", result
            assert result["run"]["status"] == "ready", result
            extracted = int(result["run"]["run"]["extracted_state_files"])
            assert extracted >= 100, result

            live = read_json(base + "/api/live")
            assert live["action_count"] == 1, live
            assert live["last_run"]["status"] == "ready", live
            assert int(live["last_run"]["run"]["extracted_state_files"]) >= 100, live

            rp_input = read_json(base + "/api/state/rp_input")
            assert any("host_action_run_id=RUN-E2E" in line for line in rp_input["lines"]), rp_input
            rp_runner = read_json(base + "/api/state/rp_runner")
            assert any("host_action_status=completed" in line for line in rp_runner["lines"]), rp_runner
            rp_result = read_json(base + "/api/state/rp_host_run_result")
            assert rp_result["values"]["qemu_orch_passed"] == "1", rp_result
            assert int(rp_result["values"]["extracted_state_files"]) >= 100, rp_result

            run_html = read_text(base + "/run.html")
            assert "host_action_status" in run_html
            actions_html = read_text(base + "/actions.html")
            assert "qemu_orch_passed" in actions_html
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    print("test_plain_ucore_reader_e2e: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
