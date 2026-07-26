#!/usr/bin/env python3
"""No-QEMU regression tests for the compact final-evidence pipeline."""
from __future__ import annotations
import csv
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
COLLECTOR = REPO / "scripts" / "capture-final-evidence.py"
PROFILE_ARTIFACTS = {
    "target-structure": [],
    "kernel-budgets": [],
    "reader-e2e": ["reader-e2e.log", "reader-e2e-log-manifest.json",
                   "reader-e2e-run-fixture-ucore-build.log",
                   "reader-e2e-run-fixture-ucore-run.log",
                   "reader-e2e-run-fixture-ucore-run-summary.json"],
    "host-platform-alignment": [],
    "dual-platforms": ["dual-plain-qemu.log", "dual-agentos-qemu.log",
                       "dual-stage-timings.csv", "dual-state-compare.json",
                       "dual-reader-compare.json"],
    "agent-suite": ["agent-suite-timings.log", "agent-suite-guest.log"],
    "proc-reap": ["proc-reap.log"],
    "syscall-fairness": ["syscall-fairness.log"],
    "file-resource": ["file-resource.log"],
    "thread-resource": ["thread-resource.log"],
    "workflow-teardown-race": ["workflow-teardown-race.log"],
    "fs-enospc": ["fs-enospc.log"],
}
def run(argv: list[str], cwd: Path, check: bool = True,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, cwd=cwd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False, env=env)
    if check and result.returncode:
        raise AssertionError(f"command failed: {argv}\n{result.stdout}\n{result.stderr}")
    return result
def executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
def rewrite_checksums(bundle: Path) -> None:
    checksum = bundle / "checksums.sha256"
    rows = []
    for path in sorted(item for item in bundle.rglob("*") if item.is_file() and item != checksum):
        digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {path.relative_to(bundle).as_posix()}")
    checksum.write_text("\n".join(rows) + "\n", encoding="ascii")
def populate_summary_stage(stage: Path) -> Path:
    incoming, runtime = stage / "incoming", stage / "runtime"
    incoming.mkdir(parents=True)
    runtime.mkdir()
    for artifacts in PROFILE_ARTIFACTS.values():
        for name in artifacts:
            (incoming / name).write_text(f"{name} passed\n", encoding="utf-8")
    reader_manifest = {"schema_version": 1,
                       "required_files": ["ucore-build.log", "ucore-run.log",
                                          "ucore-run-summary.json"],
                       "runs": [{"run": "run-fixture",
                                 "files": ["ucore-build.log", "ucore-run.log",
                                           "ucore-run-summary.json"], "missing": []}]}
    (incoming / "reader-e2e-log-manifest.json").write_text(
        json.dumps(reader_manifest) + "\n", encoding="utf-8")
    (incoming / "agent-suite-timings.log").write_text(
        "case_one 1.250000000\ncase_two 1.500000000\n", encoding="utf-8")
    rows = []
    for index, (name, artifacts) in enumerate(PROFILE_ARTIFACTS.items(), 1):
        rows.append("\t".join([name, str(index), str(index + 1), *artifacts]))
    steps = runtime / "steps.tsv"
    steps.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return steps
def init_fixture_repo(root: Path, failing_make: bool = False, slow_make: bool = False,
                      bad_stack: bool = False, bad_timing: bool = False) -> dict[str, Path]:
    run(["git", "init", "-q"], root)
    run(["git", "config", "user.email", "evidence@example.invalid"], root)
    run(["git", "config", "user.name", "Evidence Test"], root)
    (root / "scripts").mkdir()
    shutil.copyfile(COLLECTOR, root / "scripts" / COLLECTOR.name)
    config = {
        "agent_test_suite": {"expected_cases": (["case_two", "case_one"] if bad_timing else
                                                  ["case_one", "case_two"]),
                             "baseline_seconds": 2.0, "max_seconds": 4.0},
        "kernel_stack": {"baseline_required_bytes": 100, "max_required_bytes": 110,
                         "stack_size_bytes": 121 if bad_stack else 120,
                         "baseline_boot_required_bytes": 80,
                         "max_boot_required_bytes": 90, "boot_stack_size_bytes": 100},
    }
    (root / "ci").mkdir()
    (root / "ci" / "kernel-budgets.json").write_text(json.dumps(config) + "\n", encoding="utf-8")
    tools = root / "tools"
    version_tool = "#!/usr/bin/env bash\necho 'fixture tool 1.0'\n"
    compiler = tools / "riscv64-linux-gnu-gcc"
    qemu = tools / "qemu-system-riscv64"
    host_cc = tools / "cc"
    for tool in (compiler, qemu, host_cc):
        executable(tool, version_tool)
    make = tools / "make"
    if failing_make:
        make_body = "#!/usr/bin/env bash\n[[ ${1:-} == --version ]] && { echo fixture-make; exit 0; }\nexit 7\n"
    else:
        make_body = r'''#!/usr/bin/env bash
set -eu
if [[ ${1:-} == --version ]]; then echo 'fixture make 1.0'; exit 0; fi
incoming="${FINAL_EVIDENCE_STAGE}/incoming"
runtime="${FINAL_EVIDENCE_STAGE}/runtime/full-verify"
mkdir -p "${incoming}" "${runtime}"
env | sort >"${incoming}/reader-e2e.log"
cat >"${incoming}/reader-e2e-log-manifest.json" <<'EOF'
{"schema_version":1,"required_files":["ucore-build.log","ucore-run.log","ucore-run-summary.json"],"runs":[{"run":"run-fixture","files":["ucore-build.log","ucore-run.log","ucore-run-summary.json"],"missing":[]}]}
EOF
for artifact in \
  reader-e2e-run-fixture-ucore-build.log reader-e2e-run-fixture-ucore-run.log \
  reader-e2e-run-fixture-ucore-run-summary.json dual-plain-qemu.log dual-agentos-qemu.log \
  dual-stage-timings.csv dual-state-compare.json dual-reader-compare.json agent-suite-guest.log \
  proc-reap.log syscall-fairness.log file-resource.log thread-resource.log \
  workflow-teardown-race.log fs-enospc.log; do
  printf '%s passed\n' "${artifact}" >"${incoming}/${artifact}"
done
printf 'case_one 1.250000000\ncase_two 1.500000000\n' >"${incoming}/agent-suite-timings.log"
printf 'target-structure\t1\t2\nkernel-budgets\t2\t3\nreader-e2e\t3\t4\treader-e2e.log\treader-e2e-log-manifest.json\treader-e2e-run-fixture-ucore-build.log\treader-e2e-run-fixture-ucore-run.log\treader-e2e-run-fixture-ucore-run-summary.json\nhost-platform-alignment\t4\t5\ndual-platforms\t5\t6\tdual-plain-qemu.log\tdual-agentos-qemu.log\tdual-stage-timings.csv\tdual-state-compare.json\tdual-reader-compare.json\nagent-suite\t6\t7\tagent-suite-timings.log\tagent-suite-guest.log\nproc-reap\t7\t8\tproc-reap.log\nsyscall-fairness\t8\t9\tsyscall-fairness.log\nfile-resource\t9\t10\tfile-resource.log\nthread-resource\t10\t11\tthread-resource.log\nworkflow-teardown-race\t11\t12\tworkflow-teardown-race.log\nfs-enospc\t12\t13\tfs-enospc.log\n' >"${runtime}/steps.tsv"
python3 scripts/capture-final-evidence.py write-summary \
  --stage "${FINAL_EVIDENCE_STAGE}" --steps "${runtime}/steps.tsv" \
  --commit "$(git rev-parse HEAD)" --agent-grace 2s --mechanism-grace 5s --workflow-runs 3
echo 'kernel stack budget: user=1 interrupt=1 margin=1 required=4095 limit=4096'
echo 'boot stack budget: root=main path=1 interrupt=1 margin=1 required=2047 limit=2048'
echo '[kernel-budget] kernel source (os): actual=100 lines baseline=90 lines limit=110 lines'
echo '[kernel-budget] stripped kernel ELF: actual=100 bytes baseline=90 bytes limit=110 bytes'
echo '[kernel-budget] raw kernel image: actual=100 bytes baseline=90 bytes limit=110 bytes'
echo '[kernel-budget] kernel runtime text: actual=60 bytes baseline=50 bytes limit=70 bytes'
echo '[kernel-budget] kernel runtime data: actual=20 bytes baseline=15 bytes limit=25 bytes'
echo '[kernel-budget] kernel runtime bss: actual=20 bytes baseline=15 bytes limit=25 bytes'
echo '[kernel-budget] kernel runtime total: actual=100 bytes baseline=80 bytes limit=120 bytes'
echo '[kernel-budget] struct proc: actual=100 bytes baseline=90 bytes limit=110 bytes'
echo 'kernel stack budget: user=1 interrupt=1 margin=1 required=101 limit=120'
echo 'boot stack budget: root=main path=1 interrupt=1 margin=1 required=81 limit=100'
echo '[kernel-budget] kernel checks passed'
echo 'kernel stack budget: user=1 interrupt=1 margin=1 required=8191 limit=8192'
echo 'boot stack budget: root=main path=1 interrupt=1 margin=1 required=4095 limit=4096'
echo '[full-verify] all checks passed'
'''
        if slow_make:
            slow_body = ("bash -c 'trap \"\" TERM; sleep 2; printf ran >\"$1\"' _ "
                         f"{shlex.quote(str(root / 'slow-sentinel'))} &\nwait\n")
            make_body = make_body.replace(
                "if [[ ${1:-} == --version ]]; then echo 'fixture make 1.0'; exit 0; fi\n",
                "if [[ ${1:-} == --version ]]; then echo 'fixture make 1.0'; exit 0; fi\n" + slow_body)
    executable(make, make_body)
    run(["git", "add", "-A"], root)
    run(["git", "commit", "-q", "-m", "fixture"], root)
    return {"make": make, "compiler_prefix": tools / "riscv64-linux-gnu-",
            "qemu": qemu, "host_cc": host_cc, "sentinel": root / "slow-sentinel"}
def collect_args(repo: Path, output: Path, tools: dict[str, Path]) -> list[str]:
    return [sys.executable, str(COLLECTOR), "collect", "--repo-root", str(repo),
            "--output", str(output), "--toolprefix", str(tools["compiler_prefix"]),
            "--qemu", str(tools["qemu"]), "--make", str(tools["make"]),
            "--host-cc", str(tools["host_cc"]), "--python", sys.executable,
            "--bash", shutil.which("bash") or "bash", "--command-timeout", "30"]
def start_gitlab_fixture(commit: str, pipeline_sha: str | None = None,
                         missing_trace: bool = False, redirect_artifact_url: str | None = None
                         ) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    specs = (("kernel-budgets", 801, 901, "agentos-host-calibrated"),
             ("reader-e2e", 802, 902, "agentos-qemu-calibrated"),
             ("agent-regression", 803, 902, "agentos-qemu-calibrated"),
             ("kernel-mechanism-regression", 804, 902, "agentos-qemu-calibrated"))
    actual_sha = pipeline_sha or commit
    project = {"id": 39809, "path_with_namespace": "contest/agentos",
               "web_url": "https://gitlab.example/contest/agentos"}
    pipeline = {"id": 701, "sha": actual_sha, "ref": "main", "source": "push",
                "status": "success", "web_url": "https://gitlab.example/pipelines/701"}
    jobs = [{"id": job_id, "name": name, "status": "success"}
            for name, job_id, _, _ in specs]
    details = {job_id: {"id": job_id, "name": name, "status": "success",
                        "runner": {"id": runner_id}, "tag_list": [tag],
                        "pipeline": {"id": 701, "sha": actual_sha}}
               for name, job_id, runner_id, tag in specs}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.headers.get("PRIVATE-TOKEN") != "fixture-token":
                self.send_error(401); return
            path = self.path.split("?", 1)[0]
            if path == "/api/v4/projects/39809/jobs/804/artifacts" and redirect_artifact_url:
                self.send_response(302); self.send_header("Location", redirect_artifact_url)
                self.end_headers(); return
            value: object | None = None
            content_type = "application/json"
            if path == "/api/v4/projects/39809": value = project
            elif path == "/api/v4/projects/39809/pipelines/701": value = pipeline
            elif path == "/api/v4/projects/39809/pipelines/701/jobs": value = jobs
            else:
                match = __import__("re").fullmatch(r"/api/v4/projects/39809/jobs/(\d+)(?:/(trace|artifacts))?", path)
                if match:
                    job_id, kind = int(match.group(1)), match.group(2)
                    if kind is None: value = details.get(job_id)
                    elif kind == "trace" and not (missing_trace and job_id == 804):
                        value = f"job {job_id} trace passed\n".encode(); content_type = "text/plain"
                    elif kind == "artifacts":
                        value = f"PK fixture artifact {job_id}".encode(); content_type = "application/zip"
            if value is None:
                self.send_error(404); return
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            if path.endswith("/jobs"): self.send_header("X-Next-Page", "")
            self.end_headers(); self.wfile.write(data)
        def log_message(self, format: str, *args: object) -> None:
            return
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"
def start_redirect_sink() -> tuple[ThreadingHTTPServer, threading.Thread, str, list[str | None]]:
    observed: list[str | None] = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed.append(self.headers.get("PRIVATE-TOKEN"))
            data = b"PK redirected fixture artifact"
            self.send_response(200); self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data)
        def log_message(self, format: str, *args: object) -> None:
            return
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}/artifact.zip", observed
class FinalEvidenceTests(unittest.TestCase):
    def assert_verify_rejected(self, bundle: Path, cwd: Path, message: str) -> None:
        rewrite_checksums(bundle)
        result = run([sys.executable, str(COLLECTOR), "verify", str(bundle)], cwd, check=False)
        self.assertIn(message, result.stderr)
    def assert_metric_tamper_rejected(self, bundle: Path, cwd: Path, key: str, replacement: tuple[str, str], message: str) -> None:
        manifest_path, manifest_bytes = bundle / "manifest.json", (bundle / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        target = bundle / manifest["metrics"][key]["path"]; original = target.read_bytes()
        target.write_bytes(original.replace(replacement[0].encode(), replacement[1].encode(), 1))
        manifest["metrics"][key]["sha256"] = __import__("hashlib").sha256(target.read_bytes()).hexdigest()
        if key == "chart":
            manifest["metrics"]["chart_sha256"] = manifest["metrics"][key]["sha256"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assert_verify_rejected(bundle, cwd, message)
        target.write_bytes(original)
        manifest_path.write_bytes(manifest_bytes)
    def test_kernel_budget_metrics_are_bound_to_unique_block(self) -> None:
        namespace = __import__("runpy").run_path(str(COLLECTOR))
        parse_measurements = namespace["parse_measurements"]
        evidence_error = namespace["EvidenceError"]
        canonical = [
            "[kernel-budget] kernel source (os): actual=100 lines baseline=90 lines limit=110 lines",
            "[kernel-budget] stripped kernel ELF: actual=100 bytes baseline=90 bytes limit=110 bytes",
            "[kernel-budget] raw kernel image: actual=100 bytes baseline=90 bytes limit=110 bytes",
            "[kernel-budget] kernel runtime text: actual=60 bytes baseline=50 bytes limit=70 bytes",
            "[kernel-budget] kernel runtime data: actual=20 bytes baseline=15 bytes limit=25 bytes",
            "[kernel-budget] kernel runtime bss: actual=20 bytes baseline=15 bytes limit=25 bytes",
            "[kernel-budget] kernel runtime total: actual=100 bytes baseline=80 bytes limit=120 bytes",
            "[kernel-budget] struct proc: actual=100 bytes baseline=90 bytes limit=110 bytes",
            "kernel stack budget: user=1 interrupt=1 margin=1 required=101 limit=120",
            "boot stack budget: root=main path=1 interrupt=1 margin=1 required=81 limit=100",
            "[kernel-budget] kernel checks passed",
        ]
        config = {
            "agent_test_suite": {"expected_cases": ["case_one", "case_two"],
                                 "baseline_seconds": 2.0, "max_seconds": 4.0},
            "kernel_stack": {"baseline_required_bytes": 100, "max_required_bytes": 110,
                             "stack_size_bytes": 120, "baseline_boot_required_bytes": 80,
                             "max_boot_required_bytes": 90, "boot_stack_size_bytes": 100},
        }
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            full_log, timing_log = base / "full.log", base / "timing.log"
            timing_log.write_text("case_one 1.250000000\ncase_two 1.500000000\n")
            outside = [
                "kernel stack budget: user=1 required=4095 limit=4096",
                "boot stack budget: root=main required=2047 limit=2048",
            ]
            lines = outside + canonical + list(reversed(outside))
            full_log.write_text("\n".join(lines) + "\n")
            rows, _ = parse_measurements(full_log, timing_log, config)
            metrics = {row["metric"]: row for row in rows}
            self.assertEqual(metrics["kernel_stack_required_bytes"]["actual"], 101)
            self.assertEqual(metrics["boot_stack_required_bytes"]["actual"], 81)
            self.assertEqual(metrics["kernel_stack_required_bytes"]["source_line"],
                             lines.index(canonical[8]) + 1)
            malformed = (
                (canonical[:9] + [canonical[8]] + canonical[9:], "duplicate stack metric"),
                (canonical[:10] + [canonical[9]] + canonical[10:], "duplicate stack metric"),
                (canonical + canonical, "multiple kernel budget blocks"),
                (canonical[:1] + canonical, "multiple kernel budget blocks"),
                (canonical[:-1], "unterminated"),
                (canonical[:-1] + [" " + canonical[-1]], "unterminated"),
                (canonical[1:], "block boundary"),
                ([canonical[-1]] + canonical, "block boundary"),
                (canonical[:2] + [canonical[1]] + canonical[2:], "duplicate kernel metric"),
                (canonical + [canonical[-1]], "block boundary"),
                (outside + canonical[:8] + canonical[9:] + outside,
                 "kernel_stack_required_bytes"),
                ([canonical[1]] + canonical[:1] + canonical[2:], "stripped_kernel_elf_bytes"),
            )
            for index, (contents, message) in enumerate(malformed):
                with self.subTest(index=index, message=message):
                    full_log.write_text("\n".join(contents) + "\n")
                    with self.assertRaisesRegex(evidence_error, message):
                        parse_measurements(full_log, timing_log, config)
    def test_summary_inventory_is_generated_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            stage = base / "good"
            steps = populate_summary_stage(stage)
            incoming = stage / "incoming"
            commit = "a" * 40
            run([sys.executable, str(COLLECTOR), "write-summary", "--stage", str(stage),
                 "--steps", str(steps), "--commit", commit], REPO)
            summary = json.loads((incoming / "verification-summary.json").read_text())
            self.assertEqual(summary["full_verify_profile_version"], 1)
            self.assertEqual([step["name"] for step in summary["steps"]], list(PROFILE_ARTIFACTS))
            self.assertEqual({item["name"] for item in summary["artifacts"]},
                             {item for items in PROFILE_ARTIFACTS.values() for item in items})
            self.assertEqual(summary["settings"]["mechanism_marker_grace_seconds"], "5s")
            cases = []
            missing = base / "missing-step"; missing_steps = populate_summary_stage(missing)
            missing_steps.write_text("\n".join(line for line in missing_steps.read_text().splitlines()
                                                if not line.startswith("file-resource\t")) + "\n")
            (missing / "incoming/file-resource.log").unlink()
            cases.append((missing, missing_steps, [], "step order"))
            replaced = base / "replaced-artifact"; replaced_steps = populate_summary_stage(replaced)
            (replaced / "incoming/proc-reap.log").rename(replaced / "incoming/wrong.log")
            replaced_steps.write_text(replaced_steps.read_text().replace(
                "proc-reap\t7\t8\tproc-reap.log", "proc-reap\t7\t8\twrong.log"))
            cases.append((replaced, replaced_steps, [], "artifact contract"))
            for label, options in (("agent-setting", ["--agent-grace", "3s"]),
                                   ("mechanism-setting", ["--mechanism-grace", "4s"]),
                                   ("workflow-setting", ["--workflow-runs", "2"])):
                bad = base / label
                cases.append((bad, populate_summary_stage(bad), options, "settings contract"))
            for bad, bad_steps, options, message in cases:
                result = run([sys.executable, str(COLLECTOR), "write-summary", "--stage", str(bad),
                              "--steps", str(bad_steps), "--commit", commit, *options], REPO,
                             check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse((bad / "incoming/verification-summary.json").exists())
                self.assertFalse(any("partial" in item.name for item in (bad / "incoming").iterdir()))
    @unittest.skipUnless(os.name == "posix", "shell wiring test is POSIX-only")
    def test_wiring_and_full_modes_are_fail_closed_and_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            stage = base / "stage"
            (stage / "incoming").mkdir(parents=True)
            runner = base / "runner.sh"
            executable(runner, """#!/usr/bin/env bash
set -eu
printf '%s,%s\n' "${WORKFLOW_TEARDOWN_STABILITY_RUNS}" "${MARKER_GRACE_SECONDS}" >"${CAPTURE_OUT}"
[[ -z "${EVIDENCE_GUEST_LOG_FILE:-}" ]] || printf 'guest\n' >"${EVIDENCE_GUEST_LOG_FILE}"
printf 'runner passed\n'
""")
            wiring = REPO / "scripts" / "evidence-wiring.sh"
            full = REPO / "scripts" / "run-full-verification.sh"
            script = f"""
set -euo pipefail
source {shlex.quote(str(wiring))}
eval "$(sed -n '/^run_resource_regression() {{$/,/^}}$/p' {shlex.quote(str(full))})"
TOOLPREFIX=x QEMU=x PYTHON_BIN={shlex.quote(sys.executable)} CASE_TIMEOUT=1s
IDLE_NOTICE_SECONDS=1 MECHANISM_MARKER_GRACE_SECONDS=5s
unset FINAL_EVIDENCE_STAGE
CAPTURE_OUT={shlex.quote(str(base / 'normal'))} run_resource_regression \
  race race.log {shlex.quote(str(runner))} WORKFLOW_TEARDOWN_STABILITY_RUNS=3
FINAL_EVIDENCE_STAGE={shlex.quote(str(stage))}; evidence_initialize
CAPTURE_OUT={shlex.quote(str(base / 'evidence'))} run_resource_regression \
  race race.log {shlex.quote(str(runner))} WORKFLOW_TEARDOWN_STABILITY_RUNS=3
test "$(cat {shlex.quote(str(base / 'normal'))})" = 3,5s
test "$(cat {shlex.quote(str(base / 'evidence'))})" = 3,5s
test -s "${{EVIDENCE_INCOMING_DIR}}/race.log"
tee() {{ command cat >/dev/null; return 9; }}
if evidence_capture "${{EVIDENCE_WORK_DIR}}/tee.log" bash -c 'echo x'; then
  exit 20
else
  status=$?
fi
test "${{status}}" -eq 74
"""
            result = run(["bash", "-c", script], REPO, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    @unittest.skipUnless(os.name == "posix", "detached worktree fixture is POSIX-only")
    def test_collect_verify_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / "repo"
            repo.mkdir()
            tools = init_fixture_repo(repo)
            output = base / "release"
            poisoned = dict(os.environ)
            poisoned.update({"BASH_ENV": "secret-bash-env", "DEEPSEEK_API_KEY": "secret-key",
                             "GIT_DIR": "secret-git-dir", "MAKEFLAGS": "secret-makeflags",
                             "MAKEFILES": "secret-makefiles", "LD_PRELOAD": "secret-preload",
                             "PYTHONPATH": "secret-pythonpath"})
            run(collect_args(repo, output, tools), repo, env=poisoned)
            manifest = json.loads((output / "manifest.json").read_text())
            summary = json.loads((output / "verification-summary.json").read_text())
            self.assertEqual((manifest["status"], manifest["commit"]), ("ready", summary["commit"]))
            self.assertEqual({item["name"] for item in summary["artifacts"]},
                             {item for items in PROFILE_ARTIFACTS.values() for item in items})
            self.assertEqual(manifest["authenticity"]["remote_ci"], {"status": "not-attached"})
            self.assertNotIn("CI artifact", json.dumps(manifest))
            observed_env = (output / "logs/raw/reader-e2e.log").read_text()
            for secret in ("BASH_ENV", "DEEPSEEK_API_KEY", "GIT_DIR", "MAKEFLAGS", "MAKEFILES",
                           "LD_PRELOAD", "PYTHONPATH", "secret-"):
                self.assertNotIn(secret, observed_env)
                self.assertNotIn(secret, json.dumps(manifest["command"]["environment"]))
            with (output / "metrics" / "measurements.csv").open(newline="") as handle:
                metrics = {row["metric"]: row for row in csv.DictReader(handle)}
            agent = metrics["agent_suite_total_seconds"]
            self.assertEqual(tuple(float(agent[key]) for key in ("actual", "baseline", "limit", "usage_ratio")),
                             (2.75, 2.0, 4.0, 0.6875))
            self.assertEqual(tuple((float(metrics[name]["limit"]), int(metrics[name]["source_line"]) > 0)
                                   for name in ("kernel_stack_required_bytes", "boot_stack_required_bytes")),
                             ((110, True), (90, True)))
            svg = (output / "charts" / "budget-usage.svg").read_text()
            for token in ("agent_suite_total_seconds", "actual=", "baseline=", "limit=", "usage="):
                self.assertIn(token, svg)
            self.assertIn(manifest["command"]["log_sha256"], svg)
            timing_hash = next(item["sha256"] for item in manifest["raw_artifacts"]
                               if item["name"] == "agent-suite-timings.log")
            self.assertIn(timing_hash, svg)
            self.assertEqual(manifest["metrics"]["chart_sha256"], __import__("hashlib").sha256(svg.encode()).hexdigest())
            self.assertIn(manifest["metrics"]["source_measurements_sha256"], svg)
            verified = run([sys.executable, str(COLLECTOR), "verify", str(output)], repo)
            self.assertEqual((json.loads(verified.stdout)["status"],
                              json.loads(verified.stdout)["remote_ci"]), ("ready", "not-attached"))
            summary_path = output / "verification-summary.json"
            manifest_path = output / "manifest.json"
            original_summary, original_manifest = summary_path.read_bytes(), manifest_path.read_bytes()
            for kind, message in (("negative-duration", "step timing is invalid"),
                                  ("zero-start", "step timing is invalid"),
                                  ("invalid-calendar", "summary provenance is invalid")):
                malformed = json.loads(original_summary)
                if kind == "negative-duration":
                    malformed["steps"][1]["duration_seconds"] = -1
                elif kind == "zero-start":
                    malformed["steps"][0].update(
                        {"started_epoch": 0, "ended_epoch": 1, "duration_seconds": 1})
                else:
                    malformed["completed_at_utc"] = "2026-02-30T12:00:00Z"
                summary_path.write_text(json.dumps(malformed) + "\n")
                timing_manifest = json.loads(original_manifest)
                timing_manifest["verification_summary"]["sha256"] = \
                    __import__("hashlib").sha256(summary_path.read_bytes()).hexdigest()
                manifest_path.write_text(json.dumps(timing_manifest) + "\n")
                self.assert_verify_rejected(output, repo, message)
                summary_path.write_bytes(original_summary); manifest_path.write_bytes(original_manifest)
                rewrite_checksums(output)
            self.assert_metric_tamper_rejected(output, repo, "command_csv",
                                               ("make full-verify", "forged command"), "command CSV differs")
            self.assert_metric_tamper_rejected(output, repo, "chart",
                                               ("</svg>", "<metadata>forged</metadata></svg>"), "chart provenance")
            original_manifest = (output / "manifest.json").read_bytes()
            forged = json.loads(original_manifest)
            forged["raw_artifacts"][1] = dict(forged["raw_artifacts"][0])
            (output / "manifest.json").write_text(json.dumps(forged), encoding="utf-8")
            self.assert_verify_rejected(output, repo, "raw name sets differ")
            (output / "manifest.json").write_bytes(original_manifest)
            rewrite_checksums(output)
            metric_path = output / "metrics/measurements.csv"
            original_metrics = metric_path.read_text(encoding="utf-8")
            metric_path.write_text("\n".join(line for line in original_metrics.splitlines()
                                              if not line.startswith("kernel_stack_required_bytes,"))
                                   + "\n", encoding="utf-8")
            self.assert_verify_rejected(output, repo, "critical metrics")
            metric_path.write_text(original_metrics, encoding="utf-8")
            rewrite_checksums(output)
            for relative in ("metrics/measurements.csv", "metrics/agent-case-timings.csv", "metrics/commands.csv"):
                csv_path, original = output / relative, (output / relative).read_text(encoding="utf-8")
                csv_path.write_text(original.splitlines()[0] + "\n", encoding="utf-8")
                self.assert_verify_rejected(output, repo, "CSV schema or data")
                csv_path.write_text(original, encoding="utf-8")
            rewrite_checksums(output)
            reader_path = output / "logs/raw/reader-e2e.log"
            reader_original = reader_path.read_bytes()
            reader_path.write_text("tampered\n")
            rejected = run([sys.executable, str(COLLECTOR), "verify", str(output)], repo,
                           check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("checksum mismatch", rejected.stderr)
            reader_path.write_bytes(reader_original)
            (output / "environment/versions/git.txt").unlink()
            self.assert_verify_rejected(output, repo, "environment version record")
    @unittest.skipUnless(os.name == "posix", "remote provenance binding is POSIX-only")
    def test_remote_ci_provenance_is_explicit_and_composable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / "repo"; repo.mkdir()
            tools = init_fixture_repo(repo)
            local = base / "local"
            run(collect_args(repo, local, tools), repo)
            manifest = json.loads((local / "manifest.json").read_text())
            commit = manifest["commit"]
            self.assertEqual(manifest["authenticity"]["remote_ci"], {"status": "not-attached"})
            manifest_path = local / "manifest.json"
            original_manifest = manifest_path.read_bytes()
            forged_manifest = dict(manifest); forged_manifest["ci_artifact"] = True
            manifest_path.write_text(json.dumps(forged_manifest) + "\n")
            self.assert_verify_rejected(local, repo, "manifest is not ready")
            manifest_path.write_bytes(original_manifest)
            rewrite_checksums(local)
            token_path = base / "gitlab-token.txt"
            token_path.write_text("fixture-token\n")
            combined = base / "combined"
            sink, sink_thread, redirect_url, observed_tokens = start_redirect_sink()
            server, thread, gitlab_url = start_gitlab_fixture(
                commit, redirect_artifact_url=redirect_url)
            try:
                run([sys.executable, str(COLLECTOR), "bind-remote-ci", "--bundle", str(local),
                     "--output", str(combined), "--gitlab-url", gitlab_url,
                     "--project-id", "39809", "--pipeline-id", "701",
                     "--token-file", str(token_path)], repo)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=5)
                sink.shutdown(); sink.server_close(); sink_thread.join(timeout=5)
            self.assertEqual(observed_tokens, [None])
            combined_manifest = json.loads((combined / "manifest.json").read_text())
            remote = combined_manifest["authenticity"]["remote_ci"]
            self.assertEqual((remote["status"], remote["pipeline_sha"]),
                             ("provenance-attached", commit))
            for relative in ("remote-ci/provenance.json", "remote-ci/api/project.json",
                             "remote-ci/api/pipeline.json", "remote-ci/api/pipeline-jobs.json",
                             "remote-ci/jobs/kernel-budgets.trace.log",
                             "remote-ci/jobs/kernel-budgets.artifacts.zip",
                             "remote-ci/jobs/kernel-mechanism-regression.trace.log"):
                self.assertTrue((combined / relative).is_file(), relative)
            self.assertNotIn("fixture-token", "".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in combined.rglob("*") if path.is_file()))
            verified = json.loads(run([sys.executable, str(COLLECTOR), "verify", str(combined)],
                                      repo).stdout)
            self.assertEqual((verified["status"], verified["remote_ci"]),
                             ("ready", "provenance-attached"))
            proof_path = combined / "remote-ci/provenance.json"
            manifest_path = combined / "manifest.json"
            original_proof, original_manifest = proof_path.read_bytes(), manifest_path.read_bytes()
            bad_timestamp = json.loads(original_proof)
            bad_timestamp["capture"]["fetched_at_utc"] = "2026-13-01T00:00:00Z"
            proof_path.write_text(json.dumps(bad_timestamp) + "\n")
            rebound_manifest = json.loads(original_manifest)
            rebound_manifest["authenticity"]["remote_ci"]["provenance"]["sha256"] = \
                __import__("hashlib").sha256(proof_path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(rebound_manifest) + "\n")
            self.assert_verify_rejected(combined, repo, "capture provenance is invalid")
            proof_path.write_bytes(original_proof); manifest_path.write_bytes(original_manifest)
            rewrite_checksums(combined)
            for label, options, message in (
                ("wrong-sha", {"pipeline_sha": "f" * 40}, "final main push"),
                ("missing-trace", {"missing_trace": True}, "API request failed"),
            ):
                rejected_output = base / label
                server, thread, gitlab_url = start_gitlab_fixture(commit, **options)
                try:
                    result = run([sys.executable, str(COLLECTOR), "bind-remote-ci",
                                  "--bundle", str(local), "--output", str(rejected_output),
                                  "--gitlab-url", gitlab_url, "--project-id", "39809",
                                  "--pipeline-id", "701", "--token-file", str(token_path)],
                                 repo, check=False)
                finally:
                    server.shutdown(); server.server_close(); thread.join(timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(rejected_output.exists())
            trace = combined / "remote-ci/jobs/kernel-budgets.trace.log"
            trace.write_text("forged trace\n")
            self.assert_verify_rejected(combined, repo, "manifest file hash differs")
    @unittest.skipUnless(os.name == "posix", "detached worktree fixture is POSIX-only")
    def test_dirty_and_failed_runs_never_publish_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            dirty = base / "dirty"
            dirty.mkdir()
            tools = init_fixture_repo(dirty)
            (dirty / "dirty.txt").write_text("dirty\n")
            output = base / "dirty-release"
            result = run(collect_args(dirty, output, tools), dirty, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            failing = base / "failing"
            failing.mkdir()
            failing_tools = init_fixture_repo(failing, failing_make=True)
            failed_output = base / "failed-release"
            result = run(collect_args(failing, failed_output, failing_tools), failing,
                         check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(failed_output.exists())
            self.assertTrue((base / "failed-release.failed" / "failure.json").is_file())
            for label, options, message in (
                ("bad-stack", {"bad_stack": True}, "stack capacity"),
                ("bad-timing", {"bad_timing": True}, "timing cases"),
            ):
                repo = base / label
                repo.mkdir()
                bad_tools = init_fixture_repo(repo, **options)
                result = run(collect_args(repo, base / f"{label}-release", bad_tools), repo, check=False)
                self.assertIn(message, result.stderr)
            concurrent = base / "concurrent"
            concurrent.mkdir()
            concurrent_tools = init_fixture_repo(concurrent, slow_make=True)
            concurrent_output = base / "concurrent-release"
            argv = collect_args(concurrent, concurrent_output, concurrent_tools)
            first = subprocess.Popen(argv, cwd=concurrent, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
            time.sleep(0.4)
            second = run(argv, concurrent, check=False)
            stdout, stderr = first.communicate(timeout=15)
            self.assertEqual(first.returncode, 0, stdout + stderr)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already running", second.stderr)
            interrupted = base / "interrupted"
            interrupted.mkdir()
            interrupt_tools = init_fixture_repo(interrupted, slow_make=True)
            interrupt_output = base / "interrupt-release"
            process = subprocess.Popen(collect_args(interrupted, interrupt_output, interrupt_tools),
                                       cwd=interrupted, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            time.sleep(0.4)
            process.send_signal(2)
            process.communicate(timeout=10)
            time.sleep(2.1)
            self.assertEqual(process.returncode, 130)
            self.assertFalse(interrupt_output.exists())
            self.assertFalse(interrupt_tools["sentinel"].exists())
    def test_public_contract_grace_ci_and_trackability(self) -> None:
        collector = COLLECTOR.read_text(encoding="utf-8")
        wiring = (REPO / "scripts" / "evidence-wiring.sh").read_text(encoding="utf-8")
        full = (REPO / "scripts" / "run-full-verification.sh").read_text(encoding="utf-8")
        structure = (REPO / "scripts" / "verify-dual-target-structure.sh").read_text(encoding="utf-8")
        evidence_readme = (REPO / "evidence" / "README.md").read_text(encoding="utf-8")
        ci = (REPO / ".gitlab-ci.yml").read_text(encoding="utf-8")
        self.assertLessEqual(len(collector.splitlines()), 1400)
        self.assertLessEqual(len(wiring.splitlines()), 80)
        for token in ("write-summary", "PLAIN_UCORE_READER_E2E_LOG_DIR", "reader-e2e-log-manifest.json",
                      'MECHANISM_MARKER_GRACE_SECONDS="${MECHANISM_MARKER_GRACE_SECONDS:-5s}"'):
            self.assertIn(token, full)
        self.assertLess(full.index("write-summary"), full.index("[full-verify] all checks passed"))
        self.assertNotIn("hash-tree", collector + full)
        self.assertNotIn("immutable CI artifact", collector)
        for contract in ("'^SCHEMA_VERSION = 2$'", "'^FULL_VERIFY_PROFILE_VERSION = 1$'",
                         "'^REMOTE_CI_SCHEMA_VERSION = 1$'"):
            self.assertIn(contract, structure)
        self.assertNotIn("'^SCHEMA_VERSION = 1$'", structure)
        self.assertNotIn("依赖已提交的 Git 对象和不可变的 CI artifact", evidence_readme)
        for token in ("bind-remote-ci", "not-attached", "provenance-attached", "GitLab API",
                      "trace", "artifact"):
            self.assertIn(token, evidence_readme)
        for token in ("FULL_VERIFY_PROFILE_VERSION", "STEP_CONTRACT", "validate_settings",
                      "bind-remote-ci", "agent_marker_grace_seconds",
                      "mechanism_marker_grace_seconds", "workflow_stability_runs"):
            self.assertIn(token, collector)
        for name in ("reader-e2e.log", "dual-plain-qemu.log", "dual-agentos-qemu.log",
                     "proc-reap.log", "syscall-fairness.log", "file-resource.log",
                     "thread-resource.log", "workflow-teardown-race.log", "fs-enospc.log"):
            self.assertIn(f'"{name}"', collector)
        for name in ("proc-reap", "syscall-fairness", "file-resource", "thread-resource",
                     "workflow-teardown-race", "fs-enospc"):
            runner = (REPO / "scripts" / f"run-{name}-tests.sh").read_text()
            self.assertIn('MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"', runner)
        for token in ("MARKER_GRACE_SECONDS=2s", "MARKER_GRACE_SECONDS: 5s",
                      "test_plain_ucore_action_runner.py", "stages:\n  - budget\n  - reader\n  - test"):
            self.assertIn(token, ci)
        statuses = tuple(run(["git", "check-ignore", "-q", "--no-index", path], REPO, check=False).returncode
                         for path in ("probe.log", "evidence/releases/probe/logs/raw/probe.log"))
        self.assertEqual(statuses, (0, 1))
if __name__ == "__main__":
    unittest.main(verbosity=2)
