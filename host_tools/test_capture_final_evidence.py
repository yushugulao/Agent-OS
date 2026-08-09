#!/usr/bin/env python3
"""精简最终证据流水线的无 QEMU 回归测试。"""
from __future__ import annotations
import csv
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from evidence_semantic_registry import (
    RAW_ARTIFACT_REGISTRY,
    EvidenceSemanticError,
    validate_selected_artifacts,
)
from evidence_semantic_profiles import _validate_dual_state
from evidence_semantic_common import ValidationContext
from evidence_semantic_dual import _validate_program_ledger, _validate_telemetry
from check_host_platform_alignment import read_expected_programs
import dual_state_archive
import test_compare_dual_platform_state as state_fixture
from dual_state_archive import _canonical_zip
from dual_state_evidence_contract import (
    AGENTOS_PROGRAM_FILESYSTEM_CAPABILITIES,
    AGENTOS_REQUIRED_AGENT_ROLES,
    AGENTOS_ROLE_NUMBERS,
    AGENTOS_WORKER_BATCH_GROUPS,
    AGENTOS_WORKER_BATCH_PROGRAMS,
    AGENTOS_WORKER_DIRECT_PROGRAMS,
    AGENT_TO_PLAIN_CASE,
    BACKEND_REPORT_CASES,
    DUAL_STATE_RAW_ARTIFACTS,
    MAIN_FLOW_SOURCE_ARTIFACTS,
    MAIN_FLOW_SOURCE_SPECS,
    MAIN_FLOW_TELEMETRY_ARTIFACT,
    PLATFORM_PROGRAMS,
    PROGRAM_LEDGER_ARTIFACTS,
    RUN_RESULT_ARTIFACTS,
    STATE_ARCHIVE_ARTIFACTS,
    evidence_check_count,
    expected_scenario_rows,
    fnv1a64,
    agentos_program_launch_contract,
)
from reference_catalog_contract import expected_reference_identities
from research_state_manifest import load_manifest, target_state_names
from evidence_toolchain_attestation import (
    decode_external_output, resolve_bash_executable, resolve_executable,
)
REPO = Path(__file__).resolve().parents[1]
BACKING_PYTHON = getattr(sys, "_agentos_backing_executable", sys.executable)
COLLECTOR = REPO / "scripts" / "capture-final-evidence.py"
MEASUREMENT_MODULE = REPO / "host_tools" / "measured_experiments.py"
FULL_VERIFICATION_METRICS = REPO / "host_tools" / "full_verification_metrics.py"
BENCHMARK_SOURCE_CONTRACT = REPO / "host_tools" / "benchmark_source_contract.py"
DELIVERY_CONTRACT = REPO / "host_tools" / "evidence_delivery_contract.py"
GIT_HISTORY_CONTRACT = REPO / "host_tools" / "git_history_contract.py"
STRICT_JSON = REPO / "host_tools" / "strict_json.py"
SEMANTIC_REGISTRY = REPO / "host_tools" / "evidence_semantic_registry.py"
SEMANTIC_COMMON = REPO / "host_tools" / "evidence_semantic_common.py"
SEMANTIC_PROFILES = REPO / "host_tools" / "evidence_semantic_profiles.py"
SEMANTIC_METADATA = REPO / "host_tools" / "evidence_semantic_metadata.py"
SEMANTIC_DUAL = REPO / "host_tools" / "evidence_semantic_dual.py"
DUAL_STATE_CONTRACT = REPO / "host_tools" / "dual_state_evidence_contract.py"
DUAL_STATE_ARCHIVE = REPO / "host_tools" / "dual_state_archive.py"
REFERENCE_CATALOG_CONTRACT = REPO / "host_tools" / "reference_catalog_contract.py"
COMPARE_DUAL_STATE = REPO / "host_tools" / "compare_dual_platform_state.py"
CHECK_HOST_PLATFORM = REPO / "host_tools" / "check_host_platform_alignment.py"
CHECK_HOST_TEST = REPO / "host_tools" / "check_host_test_alignment.py"
COMPARE_DUAL_TEST = REPO / "host_tools" / "test_compare_dual_platform_state.py"
OBSERVE_EVIDENCE_MODULES = tuple(
    REPO / "host_tools" / name
    for name in (
        "agent_metadata_disk_format.py", "agent_metadata_journal.py",
        "agent_observe_disk_acceptance.py",
        "agent_observe_disk_contract.py", "agent_observe_disk_evidence.py",
        "agent_observe_disk_fixture.py", "plain_ucore_fs_extract.py",
        "research_state_manifest.py",
    )
)
OBSERVE_EVIDENCE_CONTRACTS = tuple(
    REPO / "ci" / name
    for name in (
        "agent-metadata-disk-format.json", "agent-observe-disk-format.json",
        "research-state-manifest.json",
    )
)
BACKEND_EVIDENCE_CONTRACT = REPO / "host_tools" / "backend_evidence_contract.py"
KERNEL_LOG_VALIDATOR = REPO / "scripts" / "validate-kernel-test-log.py"
METADATA_LOG_VALIDATOR = REPO / "scripts" / "validate-metadata-crash-log.py"
METADATA_REPROBE_VALIDATOR = REPO / "scripts" / "validate-metadata-reprobe-log.py"
VIRTIO_LOG_VALIDATOR = REPO / "scripts" / "validate-virtio-disk-log.py"
FS_ALLOCATOR_IMAGE = REPO / "scripts" / "fs-allocator-image.py"
BENCHMARK_SOURCE = REPO / "user" / "src" / "agentbench_ucore.c"
FS_EVIDENCE_TEST = REPO / "scripts" / "test-fs-allocator-evidence.py"
BENCHMARK_MARKER = (
    "agentbench_ucore: file_query_benchmark schema=2 unit=us load=143 "
    "traversal_ops=64 traversal_records=143 traversal_duration_us=36 "
    "cold_index_ops=1 cold_index_records=6 cold_index_duration_us=2 "
    "cold_rebuild_records=512 cold_rebuild_included=1 "
    "warm_index_ops=64 warm_index_records=6 warm_index_duration_us=20 "
    "status=measured"
)
FIXTURE_AGENT_CASES = (
    "agentfinal_ucore", "agentfs_ucore", "agentscan_ucore", "agentloop_ucore",
    "agentsched_ucore", "agentconflict_ucore", "agentllm_ucore", "agentbench_ucore",
    "labdemo_ucore", "agentsecurity_ucore", "agenttoolabi_ucore",
    "agentscope_ucore", "agenttrust_ucore", "agentvfs_ucore", "iobudget_ucore",
    "usersafety_ucore", "blocking_semantics_ucore",
)
FIXTURE_AGENT_TOTAL_SECONDS = (len(FIXTURE_AGENT_CASES) - 1) * 0.1 + 1.05
PROFILE_ARTIFACTS = {
    "target-structure": [],
    "kernel-budgets": [],
    "host-platform-alignment": [],
    "ch3-trace": ["ch3-trace-guest.log"],
    "agent-suite": ["agent-suite-timings.log", "agent-suite-guest.log"],
    "dual-platforms": ["dual-plain-qemu.log", "dual-agentos-qemu.log",
                        "dual-stage-timings.csv", "dual-state-compare.json",
                        "host-platform-alignment.json",
                        *DUAL_STATE_RAW_ARTIFACTS,
                        "dual-targeted-agentbench-guest.log",
                        "dual-measured-experiments.json",
                        "dual-file-query-benchmark.csv"],
    "proc-reap": ["proc-reap.log"],
    "syscall-fairness": ["syscall-fairness.log"],
    "file-resource": ["file-resource.log"],
    "thread-resource": ["thread-resource.log"],
    "physical-resource": ["physical-resource.log"],
    "metadata-recovery": ["metadata-recovery.log"],
    "observe-recovery": ["observe-recovery.log", "observe-recovery-before-reap.img"],
    "virtio-disk": ["virtio-disk.log"],
    "workflow-teardown-race": ["workflow-teardown-race.log"],
    "fs-enospc": ["fs-enospc.log"],
    "fs-allocator-fault": ["fs-allocator-fault.log", "fs-allocator-evidence.tar"],
}
def run(argv: list[str], cwd: Path, check: bool = True,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, check=False, env=env)
    result = subprocess.CompletedProcess(
        completed.args, completed.returncode,
        decode_external_output(completed.stdout),
        decode_external_output(completed.stderr),
    )
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
def rebind_raw_artifact(bundle: Path, name: str, payload: bytes) -> tuple[bytes, bytes, bytes]:
    raw_path = bundle / "logs" / "raw" / name
    summary_path = bundle / "verification-summary.json"
    manifest_path = bundle / "manifest.json"
    originals = (raw_path.read_bytes(), summary_path.read_bytes(), manifest_path.read_bytes())
    raw_path.write_bytes(payload)
    digest = __import__("hashlib").sha256(payload).hexdigest()
    summary = json.loads(originals[1])
    summary_record = next(record for record in summary["artifacts"] if record["name"] == name)
    summary_record.update({"bytes": len(payload), "sha256": digest})
    summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")
    manifest = json.loads(originals[2])
    raw_record = next(record for record in manifest["raw_artifacts"] if record["name"] == name)
    raw_record.update({"bytes": len(payload), "sha256": digest})
    manifest["verification_summary"]["sha256"] = \
        __import__("hashlib").sha256(summary_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    rewrite_checksums(bundle)
    return originals
def restore_raw_artifact(bundle: Path, name: str, originals: tuple[bytes, bytes, bytes]) -> None:
    (bundle / "logs" / "raw" / name).write_bytes(originals[0])
    (bundle / "verification-summary.json").write_bytes(originals[1])
    (bundle / "manifest.json").write_bytes(originals[2])
    rewrite_checksums(bundle)
def rebind_full_log(bundle: Path, payload: bytes) -> dict[Path, bytes]:
    paths = [bundle / relative for relative in (
        "logs/full-verify.log", "metrics/measurements.csv", "metrics/commands.csv",
        "charts/budget-usage.svg", "manifest.json")]
    originals = {path: path.read_bytes() for path in paths}
    manifest = json.loads(originals[paths[-1]])
    old_full_hash = manifest["command"]["log_sha256"]
    paths[0].write_bytes(payload)
    new_full_hash = __import__("hashlib").sha256(payload).hexdigest()
    manifest["command"]["log_sha256"] = new_full_hash
    for key, path in (("measurements_csv", paths[1]), ("command_csv", paths[2])):
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle)); fields = list(rows[0])
        for row in rows:
            if row["source_log"] == "logs/full-verify.log":
                row["source_log_sha256"] = new_full_hash
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)
        manifest["metrics"][key]["sha256"] = \
            __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    old_measurements_hash = manifest["metrics"]["source_measurements_sha256"]
    new_measurements_hash = manifest["metrics"]["measurements_csv"]["sha256"]
    chart = paths[3].read_text(encoding="utf-8").replace(
        old_full_hash, new_full_hash).replace(old_measurements_hash, new_measurements_hash)
    paths[3].write_text(chart, encoding="utf-8")
    chart_hash = __import__("hashlib").sha256(paths[3].read_bytes()).hexdigest()
    manifest["metrics"]["source_measurements_sha256"] = new_measurements_hash
    manifest["metrics"]["chart"].update({"sha256": chart_hash})
    manifest["metrics"]["chart_sha256"] = chart_hash
    paths[4].write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    rewrite_checksums(bundle)
    return originals
def restore_files(bundle: Path, originals: dict[Path, bytes]) -> None:
    for path, payload in originals.items():
        path.write_bytes(payload)
    rewrite_checksums(bundle)
def write_fs_evidence_fixture(output: Path) -> None:
    spec = importlib.util.spec_from_file_location("fs_allocator_evidence_fixture", FS_EVIDENCE_TEST)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as temp:
        package = Path(temp) / "package"
        package.mkdir()
        original_tool = module.MODULE._IMAGE_TOOL
        original_mutation = module.MODULE._raw_mutation_cli_rejection
        try:
            module.MODULE._IMAGE_TOOL = module.FakeImageTool()
            module.MODULE._raw_mutation_cli_rejection = module.fake_mutation_rejection
            module.make_package(package)
            module.MODULE.write_manifest(package)
            module.MODULE.pack_archive(package, output)
        finally:
            module.MODULE._IMAGE_TOOL = original_tool
            module.MODULE._raw_mutation_cli_rejection = original_mutation
def write_fixture_fs_verifier(path: Path, archive: Path) -> None:
    digest = __import__("hashlib").sha256(archive.read_bytes()).hexdigest()
    path.write_text(f'''#!/usr/bin/env python3
import hashlib, pathlib, sys
EXPECTED = "{digest}"
def verify_archive(path):
    raw = pathlib.Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED:
        raise ValueError("fixture allocator archive differs")
    return {{"case_count": 36, "backend": {{"identity": "fixture"}}}}
if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1:3] != ["verify-archive", "--archive"]:
        raise SystemExit(2)
    try:
        verify_archive(sys.argv[3])
    except ValueError:
        raise SystemExit(1)
''', encoding="utf-8")
def write_semantic_fixture_generator(path: Path) -> None:
    executable(path, r'''#!/usr/bin/env python3
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT / "host_tools"))
from agent_observe_disk_fixture import write_fixture
from dual_state_evidence_contract import (
    AGENTOS_EVIDENCE_REQUIREMENTS, AGENTOS_MAINFLOW_FACTS,
    AGENTOS_REQUIRED_AGENT_ROLES, AGENTOS_ROLE_NUMBERS,
    AGENTOS_WORKER_BATCH_PROGRAMS, AGENTOS_WORKER_DIRECT_PROGRAMS,
    AGENT_TO_PLAIN_CASE,
    BACKEND_REPORT_ARTIFACTS, BACKEND_REPORT_CASES, MAIN_FLOW_SOURCE_ARTIFACTS, MAIN_FLOW_SOURCE_SPECS,
    MAIN_FLOW_TELEMETRY_ARTIFACT, PLATFORM_PROGRAMS, PROGRAM_LEDGER_ARTIFACTS,
    RUN_RESULT_ARTIFACTS, SEEDED_ACTION_SUMMARY_ARTIFACT,
    STATE_ARCHIVE_ARTIFACTS, evidence_check_count,
    agentos_program_launch_contract, expected_scenario_rows, fnv1a64,
)
from reference_catalog_contract import expected_reference_identities
import compare_dual_platform_state as compare_state_module
import test_compare_dual_platform_state as state_fixture
from check_host_platform_alignment import (
    CAPABILITY_GROUPS, collect_source_names, runtime_candidates,
)
from dual_state_archive import pack_state
from research_state_manifest import archive_state_names, load_manifest

def load(name):
    source = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location("fixture_" + name.replace("-", "_"), source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

kernel = load("validate-kernel-test-log.py")
virtio = load("validate-virtio-disk-log.py")
agent_cases = (
    "agentfinal_ucore", "agentfs_ucore", "agentscan_ucore", "agentloop_ucore",
    "agentsched_ucore", "agentconflict_ucore", "agentllm_ucore", "agentbench_ucore",
    "labdemo_ucore", "agentsecurity_ucore", "agenttoolabi_ucore",
    "agentscope_ucore", "agenttrust_ucore", "agentvfs_ucore", "iobudget_ucore",
    "usersafety_ucore", "blocking_semantics_ucore",
)

def write(name, value):
    (OUT / name).write_text(value.rstrip("\n") + "\n", encoding="utf-8")

def section(tag, lines):
    if isinstance(lines, str):
        lines = lines.splitlines()
    return "\n".join([f"===== guest:{tag} =====", *lines, "", f"===== end-guest:{tag} ====="])

def combined(label, sections, final):
    body = [f"===== runner-stdout:{label} =====", final,
            "", f"===== runner-guest-logs:{label} ====="]
    body.extend(section(tag, lines) for tag, lines in sections)
    write(label + ".log", "\n".join(body))

def thread_lines():
    return [marker if marker != "threadresource_ucore: domain_fairness=1"
            else marker + " hog=575 victim=512 bound=576"
            for marker in kernel.THREAD_MARKERS]

def physical_lines():
    result = []
    for marker in kernel.PHYSICAL_RESOURCE_MARKERS:
        if marker == "physicalresource_ucore: reserved_domain_fairness=1":
            marker += " pressure_pages=8 pressure_pipes=2 physical_usage=48 physical_limit=48"
        if marker == "physicalresource_ucore: reserved_promise_lifecycle=1":
            marker += " promised=64 limit=64"
        result.append(marker)
    raw = (
        [(0, 2, 0)] + [(0, 0, 0)] * 4 + [(-1, 0, 0)] * 3
        + [(0, 1, 1), (0, 0, 0), (0, 16, 64)] + [(0, 0, 0)] * 5
        + [(0, 64, 64), (-1, 0, 0), (0, 2, 0), (-1, 0, 0)]
        + [(0, 3, 0), (-1, 0, 0), (0, 0, 3), (-1, 0, 0)]
        + [(0, 0, 0), (0, 16, 64), (0, 0, 0), (0, 64, 64)]
        + [(0, 0, 0), (0, 16, 64)]
    )
    raw_lines = [
        "physicalresource_ucore: raw "
        f"step={step} result={rc} value0={value0} value1={value1}"
        for step, (rc, value0, value1) in enumerate(raw, 1)
    ]
    result[1:1] = raw_lines
    return result

def syscall_lines():
    result = []
    for name, begin, peer, end in kernel.SYSCALL_PHASES:
        result.extend((begin, peer))
        if name == "inode":
            result.append("SYSCALLFAIR_INODE_SHORT")
        elif name == "trunc":
            result.append(
                "syscallfair_ucore: "
                "truncate_preemptions=3 peer_progress=1"
            )
        result.append(end)
    result.append("syscallfair_ucore: parent passed")
    return result

def workflow_lines():
    return list(kernel.workflow_teardown_expected_lines(14, 64))

def quota_lines(profile):
    result = list(kernel.FS_QUOTA_MARKERS)
    result = [line.replace("public_version_churn=1",
                           "public_version_churn=1 cycles=" + ("513" if profile == "domain" else "700"))
              for line in result]
    result = [line.replace("public_domain_limited=1",
                           "public_domain_limited=1 blocks=" +
                           ("16 inodes=8" if profile == "domain" else "64 inodes=16"))
              for line in result]
    if profile == "domain":
        result.append("fsquota_ucore: quota_reuse=1")
    result.append("fsquota_ucore: parent passed")
    return result

def crash_lines(bank, phase):
    return [
        "agentmetacrash_ucore: baseline_dirty=0x0000000000000029 baseline_durable=0x0000000000000029 pending=0",
        "agentmetacrash_ucore: baseline_ready=1 replicated=1",
        "agentmetacrash_ucore: target_armed scope=3 generation=0x000000000000002a token=0x0000000000000099",
        "agentmetacrash_ucore: target_bound scope=3 generation=0x000000000000002a token=0x0000000000000099 job=0x0000000000000077",
        "agentmetacrash_ucore: target_fire scope=3 generation=0x000000000000002a token=0x0000000000000099 job=0x0000000000000077 bank=%d phase=%d" % (bank, phase),
        "agentmetacrash_ucore: metadata_phase=%d" % phase,
    ]

def reprobe_lines(kind="busy", bank=None, progress=False):
    banks = (0, 1) if bank is None else (bank,)
    faults = [
        f"agentmeta_boot_fault: kind={kind} bank={item} remaining={remaining}"
        for remaining in (2, 1, 0) for item in banks
    ]
    retries = len(faults) - 1
    lines = [faults[0], "agentmeta_boot_reprobe: admission_rejected status=-17"]
    if progress:
        lines.extend((
            "agentmeta_boot_reprobe: progress sequence=0x0000000000000001 bank=1 phase=1 offset=64",
            "agentmeta_boot_reprobe: progress sequence=0x0000000000000002 bank=1 phase=2 offset=128",
            "agentmeta_boot_reprobe: progress sequence=0x0000000000000003 bank=1 phase=4 offset=16",
        ))
    now = 0x10
    for attempt, fault in enumerate(faults[1:], 1):
        delay = 1 << min(attempt, 6)
        deadline = now + delay
        lines.extend((
            fault,
            f"agentmeta_boot_reprobe: deferred attempt={attempt} now=0x{now:016x} deadline=0x{deadline:016x}",
            f"agentmetatransient_ucore: admission_retry={attempt} status=-17",
            "agentmeta_boot_reprobe: admission_rejected status=-17",
        ))
        now = deadline
    lines.extend((
        f"agentmeta_boot_reprobe: recovered=1 retries={retries}",
        f"agentmetatransient_ucore: admission_retry={retries + 1} status=-17",
        "agentmetatransient_ucore: create_succeeded=1",
        "agentmetatransient_ucore: query_succeeded=1",
        "agentmetatransient_ucore: unavailable_seen=1 recovered=1",
        "agentmetatransient_ucore: parent passed",
    ))
    return lines

def virtio_lines():
    requests = []
    for index, (case, request_type, result) in enumerate(virtio.REQUEST_CASES, 1):
        tick = index * 10
        requests.append("virtiodisk_ucore: request case=%s id=0x%016x type=%d submit=0x%016x complete=0x%016x result=%d" %
                        (case, tick, request_type, tick, tick + 1, result))
    return [
        requests[0], virtio.MARKERS[0], requests[1], virtio.MARKERS[1],
        requests[2], virtio.MARKERS[2], virtio.MARKERS[3], requests[3],
        virtio.MARKERS[4],
        "virtiodisk_ucore: range-rejection id=0x000000000000002d rejected=0x0000000000000001 submits=0x0000000000000000 result=-6",
        virtio.MARKERS[5], requests[4], virtio.MARKERS[6], requests[5],
        virtio.MARKERS[7], requests[6], virtio.MARKERS[8], requests[7],
        virtio.MARKERS[9], requests[8], virtio.MARKERS[10], requests[9],
        virtio.MARKERS[11], virtio.MARKERS[12],
    ]

benchmark = "agentbench_ucore: file_query_benchmark schema=2 unit=us load=143 traversal_ops=64 traversal_records=143 traversal_duration_us=36 cold_index_ops=1 cold_index_records=6 cold_index_duration_us=2 cold_rebuild_records=512 cold_rebuild_included=1 warm_index_ops=64 warm_index_records=6 warm_index_duration_us=20 status=measured"
write("ch3-trace-guest.log", "\n".join(kernel.CH3_TRACE_MARKERS))
mechanism = list(dict.fromkeys([
    "agentfinal_ucore: context_sync_atomic=1 append=1 rollback=1 clear=1 recovery=1",
    *kernel.WAIT_ATOMIC_MARKERS,
    *kernel.AGENT_CASE_MARKERS["agentfinal_ucore"],
]))
agent_sections = [("agent-mechanism:context-sync-atomicity", mechanism)]
for case in agent_cases:
    lines = list(kernel.AGENT_CASE_MARKERS.get(case, ()))
    if case == "agentbench_ucore":
        lines.append(benchmark)
    lines.append(case + ": parent passed")
    agent_sections.append(("agent-case:" + case, lines))
write("agent-suite-guest.log", "\n".join(section(tag, lines) for tag, lines in agent_sections))
write("agent-suite-timings.log", "\n".join(
    "%s %.9f" % (case, 1.05 if index == len(agent_cases) - 1 else 0.1)
    for index, case in enumerate(agent_cases)
))
write("dual-targeted-agentbench-guest.log",
      section("agent-case:agentbench_ucore", [benchmark, "agentbench_ucore: parent passed"]))

programs = list(PLATFORM_PROGRAMS)
plain_ledger = ["orchestrator=rp_seed_orch", "launcher=fork_seeded"] + [
    f"program={program};launcher=fork_seeded;ok=1;code=0;elapsed_ms={index}"
    for index, program in enumerate(programs, 1)
]
agent_ledger = ["orchestrator=rp_orch", "launcher=mixed_attested"]
for index, program in enumerate(programs, 1):
    role = AGENTOS_REQUIRED_AGENT_ROLES.get(program)
    is_agent = role is not None
    launcher, identity_source = agentos_program_launch_contract(program)
    agent_ledger.append(
        f"program={program};role={role if is_agent else 'plain'};"
        f"launcher={launcher};identity_source={identity_source};"
        f"is_agent={int(is_agent)};"
        f"agent_role={AGENTOS_ROLE_NUMBERS[role] if is_agent else 0};"
        f"filesystem_domain=3;filesystem_capabilities=66;ok=1;code=0;elapsed_ms={index}"
    )
write(PROGRAM_LEDGER_ARTIFACTS["plain"], "\n".join(plain_ledger))
write(PROGRAM_LEDGER_ARTIFACTS["agentos"], "\n".join(agent_ledger))

def program_receipt(target):
    raw = (OUT / PROGRAM_LEDGER_ARTIFACTS[target]).read_bytes()
    digest = 1469598103934665603
    for program in programs:
        digest = fnv1a64(program.encode() + b"\0", digest)
    return f"program_source_bytes={len(raw)} program_source_hash={fnv1a64(raw)} program_names_digest={digest} programs_observed={len(programs)}"

write("dual-plain-qemu.log", "\n".join([
    "rp_backend: evidence_role=demo_reference catalog_generation=demo_expected cases=7 status=reference_ready",
    "rp_backend: runtime_cases=0",
    "rp_orch: evidence_role=demo_reference evidence_generation=runtime observation_source=guest_runtime program_source=rp_orch_timing " + program_receipt("plain") + " status=reference_observed",
    f"rp_orch: programs_ok={len(programs)} programs_total={len(programs)}",
    "rp_web_export: host_reader_actions=1",
    "rp_compare_plain: host_actions=1 verified",
    "rp_compare_plain: evidence_role=demo_reference catalog_generation=demo_expected status=reference_ready",
    "rp_orch: passed",
]))
write("dual-agentos-qemu.log", "\n".join([
    "rp_backend: evidence_generation=runtime runtime_cases=8 source_reads=8 kernel_checks=4 context_sequence=1 query_returned=1 query_used_index=1 status=verified",
    "rp_orch: evidence_role=runtime_verified evidence_generation=runtime program_source=rp_orch_timing " + program_receipt("agentos") + " status=verified",
    f"rp_orch: programs_ok={len(programs)} programs_total={len(programs)}",
    "rp_web_export: host_reader_actions=1",
    "rp_compare_plain: host_actions=1 verified",
    "rp_compare_plain: evidence_generation=runtime runtime_assertions_executed=8 runtime_assertions_passed=8 status=verified",
    "rp_agentos_orch: kernel_agent=1 workflow=rp_orch status=ready",
    "rp_agentos_orch: passed",
]))
(OUT / SEEDED_ACTION_SUMMARY_ARTIFACT).write_text(json.dumps({
    "status": "ready", "action": "/actions/research/rerun", "action_count": 1,
    "action_kinds": ["fixture_action"],
}) + "\n", encoding="utf-8", newline="\n")
stages = ("structure-check", "seeded-dual-run", "qemu-log-marker-check",
          "state-extract-copy", "host-alignment", "state-compare",
          "measured-file-query")
write("dual-stage-timings.csv", "stage,start_epoch,end_epoch,duration_seconds,status\n" +
      "\n".join("%s,%d,%d,1,ready" % (stage, index, index + 1)
                  for index, stage in enumerate(stages, 1)))
cost_rows = []
for index, case in enumerate(BACKEND_REPORT_CASES["agentos"], 1):
    plain_case = AGENT_TO_PLAIN_CASE[case]
    cost_rows.append({
        "case": case, "plain_cost": f"fixture_cost_{index}",
        "agentos_replace": f"fixture_replace_{index}", "risk": f"fixture_risk_{index}",
        "plain_case": plain_case, "preserved_from_plain": int(bool(plain_case)),
        "status": "reference_ready",
    })
plain_cost_by_case = {
    row["plain_case"]: row for row in cost_rows if row["plain_case"]
}
plain_backend_rows = [
    f"runner_report={case};plain_cost={plain_cost_by_case[case]['plain_cost']};"
    f"agentos_replace={plain_cost_by_case[case]['agentos_replace']};"
    f"risk={plain_cost_by_case[case]['risk']};status=reference_ready"
    for case in BACKEND_REPORT_CASES["plain"]
]
write(BACKEND_REPORT_ARTIFACTS["plain"], "\n".join([
    "evidence_file_role=demo_reference",
    "evidence_file_generation=demo_expected",
    "runtime_cases=0",
    *plain_backend_rows,
    "evidence_file_status=reference_ready",
]))
write(BACKEND_REPORT_ARTIFACTS["agentos"], "\n".join(
    f"evidence_role=demo_reference;catalog_generation=demo_expected;"
    f"runner_report={row['case']};plain_cost={row['plain_cost']};"
    f"agentos_replace={row['agentos_replace']};risk={row['risk']};"
    f"status=reference_ready" for row in cost_rows
))
(OUT / "dual-state-compare.json").write_text(json.dumps({
    "plain_files": 300, "agentos_files": 320, "common_files": 300,
    "agentos_extra_files": 20, "checked_compatibility_records": 10,
    "plain_reference_products": 17, "agentos_reference_products": 6,
    "plain_reference_records": 19, "agentos_reference_records": 24,
    "plain_reference_identities": list(expected_reference_identities("plain")),
    "agentos_reference_identities": list(expected_reference_identities("agentos")),
    "guest_source_bound_runtime_records": 0, "preserved_plain_costs": 7,
    "cost_replacements": cost_rows,
    "cost_replacement_count": 8, "runner_tick_status": "unavailable",
    "runner_tick_reason": "plain_runtime_cases_zero", "embedded_action_records": 1,
    "run_result_match": 1, "agentos_evidence_checks": evidence_check_count(),
    "scenario_evidence": expected_scenario_rows(),
    "host_derived_mainflow_stages": len(MAIN_FLOW_SOURCE_SPECS),
    "agentos_mainflow_facts": len(AGENTOS_MAINFLOW_FACTS),
    "agentos_mainflow_verification_origin": "host_inventory",
    "plain_timing_records": len(programs), "plain_agent_launches": 0,
    "plain_fork_launches": len(programs), "agentos_timing_records": len(programs),
    "agentos_agent_launches": len(AGENTOS_REQUIRED_AGENT_ROLES),
    "agentos_worker_launches": len(programs) - len(AGENTOS_REQUIRED_AGENT_ROLES),
    "backend_query_receipts": {
        target: {
            field: (
                int(value)
                if field in {
                    "dataset_records", "query_operations", "query_matches",
                    "records_examined",
                }
                else value
            )
            for field, value in (
                item.split("=", 1)
                for item in state_fixture.backend_query_receipt_line(target).split(";")
            )
        }
        for target in ("plain", "agentos")
    },
    "status": "ready",
}) + "\n", encoding="utf-8")

telemetry = []
source_rows = []
producer_source_lines = {
    "rp_agentos_kernel": (
        "target=agentos_ucore",
        "mode=kernel_agent_orchestrated",
        "context_snapshot=present",
        "status=ready",
        "dependency_update=generic_record",
        "dependency_query=generic_record",
        "metadata_query=stage_index",
        "metadata_index=stage_query",
    ),
    "rp_agentos_collab_ack": (
        "agent=sentinel",
        "event=handoff",
        "route=recovery-auditor",
        "delivery=kernel_event_queue",
        "permission_control=sentinel_action_denied",
        "status=ready",
    ),
}
for spec in MAIN_FLOW_SOURCE_SPECS:
    telemetry.append(";".join([
        f"stage={spec.stage}",
        *(f"{key}={value}" for key, value in spec.telemetry_fields),
        "status=ready",
    ]))
    source_lines = producer_source_lines.get(
        spec.source,
        (*AGENTOS_EVIDENCE_REQUIREMENTS[spec.source], f"status={spec.source_status}"),
    )
    raw = ("\n".join(source_lines) + "\n").encode()
    (OUT / MAIN_FLOW_SOURCE_ARTIFACTS[spec.source]).write_bytes(raw)
    source_rows.append({
        "stage": spec.stage, "source": spec.source,
        "claim_key": spec.claim_key, "claim_value": spec.claim_value,
        "source_status": spec.source_status, "source_bytes": len(raw),
        "source_hash": fnv1a64(raw), "claim_verified": True,
        "status_verified": True,
        "telemetry_fields": [
            {"key": key, "value": value} for key, value in spec.telemetry_fields
        ],
        "telemetry_verified": True,
    })
write(MAIN_FLOW_TELEMETRY_ARTIFACT, "\n".join(telemetry))
telemetry_raw = (OUT / MAIN_FLOW_TELEMETRY_ARTIFACT).read_bytes()

state_root = OUT.parent / "fixture-complete-state"
plain_state, agentos_state = state_root / "plain", state_root / "agentos"
plain_state.mkdir(parents=True)
agentos_state.mkdir(parents=True)
state_manifest = load_manifest(ROOT)
archive_inventories = {
    target: archive_state_names(ROOT, state_manifest, target)
    for target in ("plain", "agentos")
}
for target, state_dir in (("plain", plain_state), ("agentos", agentos_state)):
    for name in sorted(archive_inventories[target]):
        state_fixture.write_state_file(state_dir, name, f"fixture={target}\n")
state_fixture.write_state_file(
    plain_state, "rp_backend",
    "runner_report=file_scan\n" + state_fixture.backend_query_receipt_line("plain")
    + "\nruntime_cases=0\nstatus=ready\n"
)
state_fixture.write_state_file(
    agentos_state, "rp_backend",
    "runner_report=file_scan;status=ready;kernel=observed\n"
    + state_fixture.backend_query_receipt_line("agentos") + "\nstatus=ready\n",
)
state_fixture.write_backend_catalog(plain_state, "plain")
state_fixture.write_backend_catalog(agentos_state, "agentos")
for target, state_dir in (("plain", plain_state), ("agentos", agentos_state)):
    (state_dir / "rp_orch_timing").write_bytes(
        (OUT / PROGRAM_LEDGER_ARTIFACTS[target]).read_bytes()
    )
    state_fixture.write_state_file(
        state_dir, "rp_agentcmp",
        "plain_kernel=passed;status=ready\n" +
        state_fixture.program_inventory_line(state_dir, target),
    )
    state_fixture.seed_reference_inventory(state_dir, target)
for spec in MAIN_FLOW_SOURCE_SPECS:
    (agentos_state / spec.source).write_bytes(
        (OUT / MAIN_FLOW_SOURCE_ARTIFACTS[spec.source]).read_bytes()
    )
(agentos_state / "rp_agentos_mainflow").write_bytes(telemetry_raw)
for group in CAPABILITY_GROUPS:
    for target, state_dir, sources in (
        ("plain", plain_state, group.plain_sources),
        ("agentos", agentos_state, group.agentos_sources),
    ):
        candidates = runtime_candidates(group, sources)
        candidate = next(
            (name for name in candidates if name in archive_inventories[target]),
            None,
        )
        if candidate is None:
            raise RuntimeError(
                f"{target} capability group {group.name} has no manifested runtime state"
            )
for target, state_dir in (("plain", plain_state), ("agentos", agentos_state)):
    names = sorted(path.name for path in state_dir.iterdir() if path.is_file())
    if set(names) != archive_inventories[target]:
        raise RuntimeError(f"{target} fixture state inventory differs from manifest")
    state_fixture.write_summary(state_dir, names)
state_fixture.write_host_run_result(
    OUT / RUN_RESULT_ARTIFACTS["plain"], target="plain",
    state_dir=plain_state,
    extracted_state_files=len(archive_inventories["plain"]),
)
state_fixture.write_host_run_result(
    OUT / RUN_RESULT_ARTIFACTS["agentos"], target="agentos",
    state_dir=agentos_state,
    extracted_state_files=len(archive_inventories["agentos"]),
)
state_summary = compare_state_module.compare_state(
    plain_state,
    agentos_state,
    min_common_files=240,
    plain_run_result=OUT / RUN_RESULT_ARTIFACTS["plain"],
    agentos_run_result=OUT / RUN_RESULT_ARTIFACTS["agentos"],
    plain_log=OUT / "dual-plain-qemu.log",
    agentos_log=OUT / "dual-agentos-qemu.log",
    seeded_summary=OUT / SEEDED_ACTION_SUMMARY_ARTIFACT,
)
(OUT / "dual-state-compare.json").write_text(
    json.dumps(state_summary) + "\n", encoding="utf-8"
)
for target, state_dir in (("plain", plain_state), ("agentos", agentos_state)):
    (OUT / BACKEND_REPORT_ARTIFACTS[target]).write_bytes(
        (state_dir / "rp_backend_exec").read_bytes()
    )
    pack_state(state_dir, OUT / STATE_ARCHIVE_ARTIFACTS[target])

plain_inventory = set(json.loads((plain_state / "extract-summary.json").read_text())["files"])
agentos_inventory = set(json.loads((agentos_state / "extract-summary.json").read_text())["files"])
alignment_groups = []
for group in CAPABILITY_GROUPS:
    alignment_groups.append({
        "name": group.name, "host_modules": len(group.host_modules),
        "plain_sources": len(group.plain_sources),
        "agentos_sources": len(group.agentos_sources),
        "status": "ok", "missing_host": [], "missing_plain": [],
        "missing_agentos": [],
        "plain_runtime_hits": [
            name for name in runtime_candidates(group, group.plain_sources)
            if name in plain_inventory
        ],
        "agentos_runtime_hits": [
            name for name in runtime_candidates(group, group.agentos_sources)
            if name in agentos_inventory
        ],
    })
tracked_host_modules = {
    module for group in CAPABILITY_GROUPS for module in group.host_modules
}
(OUT / "host-platform-alignment.json").write_text(json.dumps({
    "status": "ready", "host_dir": "fixture",
    "host_modules": len(tracked_host_modules),
    "tracked_host_modules": len(tracked_host_modules), "untracked_host_modules": 0,
    "plain_sources": len(collect_source_names(ROOT, "baseline_ucore/user/src")),
    "agentos_sources": len(collect_source_names(ROOT, "user/src")),
    "plain_state_files": len(plain_inventory),
    "agentos_state_files": len(agentos_inventory), "runtime_state_checked": True,
    "runtime_evidence_verified": True, "program_inventory_verified": True,
    "mainflow_host_verified": True, "mainflow_verification_origin": "host_inventory",
    "mainflow_host_stages": len(MAIN_FLOW_SOURCE_SPECS),
    "mainflow_host_assertions_executed": 2 * len(MAIN_FLOW_SOURCE_SPECS),
    "mainflow_host_assertions_passed": 2 * len(MAIN_FLOW_SOURCE_SPECS),
    "mainflow_host_telemetry_sequence": [spec.stage for spec in MAIN_FLOW_SOURCE_SPECS],
    "mainflow_host_telemetry_source": "rp_agentos_mainflow",
    "mainflow_host_telemetry_bytes": len(telemetry_raw),
    "mainflow_host_telemetry_hash": fnv1a64(telemetry_raw),
    "mainflow_host_sources": source_rows, "plain_programs_observed": len(programs),
    "agentos_programs_observed": len(programs), "plain_evidence_role": "demo_reference",
    "groups_ok": len(alignment_groups), "groups_total": len(alignment_groups),
    "groups": alignment_groups,
    "failures": [], "untracked_host_module_sample": [],
}) + "\n", encoding="utf-8")
proc = [
    "procreap_ucore: process lifecycle verification", "procreap_ucore: child-first=160",
    "procreap_ucore: parent-first=160", "procreap_ucore: orphan-resource=136",
    "procreap_ucore: blocked-syscall=384", "procreap_ucore: wait-queue cancellation passed",
    "procreap_ucore: detached-wait=8", "procreap_ucore: unreaped-parent-isolated=1",
    "procreap_ucore: live-domain-limit=1", "procreap_ucore: lineage-bypass-denied=1",
    "procreap_ucore: live-quota-returned=1", "procreap_ucore: peer-domain-isolated=1",
    "procreap_ucore: parent passed",
]
proc_agent = ["procreap_agent_ucore: bounded teardown scheduling",
              "procreap_agent_ucore: child-pressure-isolated=1",
              "procreap_agent_ucore: reserved-agent-slot=1",
              "procreap_agent_ucore: adversarial-agent=1",
              "procreap_agent_ucore: parent passed"]
combined("proc-reap", [("proc-reap:agent", proc),
                       ("proc-reap:agent-adversarial", proc_agent),
                       ("proc-reap:baseline", proc)], "[proc-reap] both targets passed")
combined("syscall-fairness", [("syscall-fairness:agent", syscall_lines()),
                              ("syscall-fairness:baseline", syscall_lines())],
         "[syscall-fairness] both targets passed")
combined("file-resource", [("file-resource:agent", kernel.FILE_MARKERS),
                            ("file-resource:baseline", kernel.FILE_MARKERS)],
         "[file-resource] both targets passed")
combined("thread-resource", [("thread-resource", thread_lines())],
         "[thread-resource] all checks passed")
combined("physical-resource", [("physical-resource", physical_lines())],
         "[physical-resource] all checks passed")

metadata = []
recover = ["agentmetarecover_ucore: readonly_recovery=1 metadata_available=1",
           "agentmetarecover_ucore: query_found=0 returned=0",
           "agentmetarecover_ucore: parent passed"]
for bank_name, bank in (("primary", 0), ("mirror", 1)):
    for phase in range(1, 9):
        metadata.append((f"metadata-agentmetacrash_ucore-{bank_name}-{phase}", crash_lines(bank, phase)))
        metadata.append((f"metadata-agentmetarecover_ucore-{bank_name}-{phase}", recover))
metadata.extend([
    ("metadata-agentmetarecover_ucore-select-baseline",
     ["agentmetarecover_ucore: query_found=0 returned=0", "agentmetarecover_ucore: parent passed"]),
    *[(f"metadata-agentmetatransient_ucore-boot-{kind}-{target}",
       reprobe_lines(kind, None if target == "all" else 1))
      for kind in ("busy", "io", "interrupted") for target in ("all", "newer")],
    ("metadata-agentmetalarge_ucore-large-seed",
     ["agentmetalarge_ucore: runtime_reload_completed=1",
      "agentmetalarge_ucore: seed_ready=1 records=32"]),
    *[(f"metadata-agentmetatransient_ucore-large-{terminal}",
       reprobe_lines("busy", 1, True))
      for terminal in ("absent", "uncommitted", "corrupt")],
    ("metadata-agentmetarecover_ucore-eio-baseline",
     ["agentmetarecover_ucore: query_found=0 returned=0", "agentmetarecover_ucore: parent passed"]),
    ("metadata-agentmetaeio_ucore-eio",
     ["agentmetaeio_ucore: transient_eio_repaired=1", "agentmetaeio_ucore: parent passed"]),
])
combined("metadata-recovery", metadata,
         "\n".join([
             *[f"metadata_authority_check: kind={kind} newer_bank=1 before=41 after=42 rollback=0"
               for kind in ("busy", "io", "interrupted")],
             *[f"metadata_large_bank_check: peer={peer} selected=valid over_burst=1"
               for peer in ("valid", "absent", "uncommitted", "corrupt")],
             "[metadata-recovery] power-cut, bounded boot reprobe, over-burst terminal-peer recovery, and EIO recovery passed",
         ]))

identity0 = "audit=1 span=2 event=3 control=4 agent=5 lifecycle_slot=6 lifecycle_generation=7"
identity1 = "audit=11 span=12 event=13 control=14 agent=15 lifecycle_slot=6 lifecycle_generation=8"
durable_identity = write_fixture(OUT / "observe-recovery-before-reap.img")
observe = [
    ("observe-recovery-boot0-cut", ["agentobsreboot_ucore: lease_cut_alloc " + identity0,
                                    "agentobsreboot_ucore: receipt_permission_not_agent=1"]),
    ("observe-recovery-boot1", ["agentobsreboot_ucore: lease_cut_successor " + identity1,
                                durable_identity,
                                "agentobsreboot_ucore: receipt_pending_not_evidence=1 receipt_durable_exact=1 receipt_fake_stale=1 receipt_window_not_evidence=1",
                                "agentobsreboot_ucore: boot1_checkpoint_ready=1"]),
    ("observe-recovery-boot2", ["agentobsreboot_ucore: receipt_teardown_stale=1",
                                "agentobsreboot_ucore: receipt_permission_recovery_denied=1",
                                "agentobsreboot_ucore: receipt_recovery_exact=1 receipt_v1_compatible=1 bank_generation_bound=1",
                                "agentobsreboot_ucore: boot2_reap_replicated=1"]),
    ("observe-recovery-boot3", ["agentobsreboot_ucore: boot3_erased=1 generation_isolated=1 stable_identity=1",
                                "agentobsreboot_ucore: timeline_wait_epoch_recheck=1 injection=2 retries=1 bounded_timeout=1",
                                "agentobsreboot_ucore: timeline_wait_threads=1 filters=2 deadlines=2 targeted=1 timeout=1 cleanup=1",
                                "agentobsreboot_ucore: parent passed"]),
]
combined("observe-recovery", observe,
         "[observe-recovery] power-cut lease and three-boot durable evidence lifecycle passed")

combined("virtio-disk", [("virtio-disk:fault-matrix", virtio_lines())],
         "[virtio-disk] fault matrix passed")
combined("workflow-teardown-race",
         [(f"workflow-teardown:{index}", workflow_lines()) for index in range(1, 4)],
         "[workflow-teardown] 3 stable runs passed")

persistent_seed = ["fspquota_ucore: sponsored_object_charged=1 blocks=14",
                   "fspquota_ucore: durable_fixture=1 blocks=18 inodes=8 owner_exited=1"]
persistent_verify = [*kernel.FS_PERSISTENT_MARKERS, "fspquota_ucore: parent passed"]
fs_sections = [
    ("fs-enospc:agent", ["fsenospc_ucore: inode exhaustion survived",
                         "fsenospc_ucore: inode cache exhaustion survived",
                         "fsenospc_ucore: block exhaustion survived", "fsenospc_ucore: parent passed"]),
    ("fs-enospc:baseline", ["fsenospc_ucore: inode exhaustion survived",
                            "fsenospc_ucore: inode cache exhaustion survived",
                            "fsenospc_ucore: block exhaustion survived", "fsenospc_ucore: parent passed"]),
    ("fs-enospc:quota-domain", quota_lines("domain")),
    ("fs-enospc:quota-reserve", quota_lines("reserve")),
    ("fs-enospc:principal-agent-orphan", ["fspquota_ucore: crash_orphan_ready=1"]),
    ("fs-enospc:principal-agent-seed", persistent_seed),
    ("fs-enospc:principal-agent-verify", persistent_verify),
    ("fs-enospc:principal-baseline-orphan", ["fspquota_ucore: crash_orphan_ready=1"]),
    ("fs-enospc:principal-baseline-seed", persistent_seed),
    ("fs-enospc:principal-baseline-verify", persistent_verify),
]
combined("fs-enospc", fs_sections,
         "[fs-enospc] generic, persistent principal, and Agent quota cases passed")
combined("fs-allocator-fault", [("fs-allocator:fixture:prepare",
                                 ["fsallocfault_ucore: case=alloc phase=intent action=busy prepared=1"])],
         "[fs-allocator-fault] dynamic matrix, negative mutant, and raw evidence passed")
''')
def collector_command(root: Path, *arguments: str) -> list[str]:
    launcher = root / "scripts" / "trusted-python-entry.py"
    if not launcher.is_file():
        launcher = REPO / "scripts" / "trusted-python-entry.py"
    command = [
        sys.executable,
        "-I",
        "-S",
        str(launcher),
        "scripts/capture-final-evidence.py",
        *arguments,
    ]
    if arguments and arguments[0] == "verify":
        command.extend(("--contract-root", str(root)))
    return command
def populate_summary_stage(stage: Path) -> Path:
    incoming, runtime = stage / "incoming", stage / "runtime"
    incoming.mkdir(parents=True)
    runtime.mkdir()
    for artifacts in PROFILE_ARTIFACTS.values():
        for name in artifacts:
            (incoming / name).write_text(f"{name} passed\n", encoding="utf-8")
    (incoming / "agent-suite-timings.log").write_text(
        "case_one 1.250000000\ncase_two 1.500000000\n", encoding="utf-8")
    (incoming / "agent-suite-guest.log").write_text(
        BENCHMARK_MARKER + "\nagentbench_ucore: parent passed\n", encoding="utf-8"
    )
    rows = []
    for index, (name, artifacts) in enumerate(PROFILE_ARTIFACTS.items(), 1):
        rows.append("\t".join([name, str(index), str(index + 1), *artifacts]))
    steps = runtime / "steps.tsv"
    steps.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return steps
def init_fixture_repo(root: Path, failing_make: bool = False, slow_make: bool = False,
                      bad_stack: bool = False, bad_timing: bool = False,
                      non_utf8_version: bool = False) -> dict[str, Path]:
    run(["git", "init", "-q"], root)
    run(["git", "config", "user.email", "evidence@example.invalid"], root)
    run(["git", "config", "user.name", "Evidence Test"], root)
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="ascii")
    shutil.copyfile(REPO / ".gitlab-ci.yml", root / ".gitlab-ci.yml")
    (root / "scripts").mkdir()
    shutil.copyfile(COLLECTOR, root / "scripts" / COLLECTOR.name)
    shutil.copyfile(
        REPO / "scripts" / "trusted-python-entry.py",
        root / "scripts" / "trusted-python-entry.py",
    )
    shutil.copyfile(
        REPO / "scripts" / "trusted-python-child.py",
        root / "scripts" / "trusted-python-child.py",
    )
    (root / "host_tools").mkdir()
    shutil.copyfile(MEASUREMENT_MODULE, root / "host_tools" / MEASUREMENT_MODULE.name)
    shutil.copyfile(
        FULL_VERIFICATION_METRICS,
        root / "host_tools" / FULL_VERIFICATION_METRICS.name,
    )
    shutil.copyfile(
        BENCHMARK_SOURCE_CONTRACT,
        root / "host_tools" / BENCHMARK_SOURCE_CONTRACT.name,
    )
    shutil.copyfile(DELIVERY_CONTRACT, root / "host_tools" / DELIVERY_CONTRACT.name)
    shutil.copyfile(
        GIT_HISTORY_CONTRACT, root / "host_tools" / GIT_HISTORY_CONTRACT.name
    )
    shutil.copyfile(
        REPO / "host_tools" / "safe_host_paths.py",
        root / "host_tools" / "safe_host_paths.py",
    )
    shutil.copyfile(
        REPO / "host_tools" / "evidence_toolchain_attestation.py",
        root / "host_tools" / "evidence_toolchain_attestation.py",
    )
    shutil.copyfile(
        REPO / "host_tools" / "evaluation_source_gate.py",
        root / "host_tools" / "evaluation_source_gate.py",
    )
    shutil.copyfile(
        REPO / "host_tools" / "formal_python_runtime.py",
        root / "host_tools" / "formal_python_runtime.py",
    )
    shutil.copyfile(
        REPO / "host_tools" / "formal_temp_binding.py",
        root / "host_tools" / "formal_temp_binding.py",
    )
    shutil.copyfile(
        REPO / "host_tools" / "full_verification_metrics.py",
        root / "host_tools" / "full_verification_metrics.py",
    )
    for module_name in (
        "duration_profile_attestation.py", "full_verification_metrics_render.py",
    ):
        shutil.copyfile(
            REPO / "host_tools" / module_name,
            root / "host_tools" / module_name,
        )
    shutil.copyfile(STRICT_JSON, root / "host_tools" / STRICT_JSON.name)
    for module in (
        SEMANTIC_REGISTRY, SEMANTIC_COMMON, SEMANTIC_PROFILES,
        SEMANTIC_METADATA, SEMANTIC_DUAL, DUAL_STATE_ARCHIVE,
        DUAL_STATE_CONTRACT, REFERENCE_CATALOG_CONTRACT, COMPARE_DUAL_STATE,
        CHECK_HOST_PLATFORM, CHECK_HOST_TEST, COMPARE_DUAL_TEST,
    ):
        shutil.copyfile(module, root / "host_tools" / module.name)
    for module in OBSERVE_EVIDENCE_MODULES:
        shutil.copyfile(module, root / "host_tools" / module.name)
    shutil.copyfile(BACKEND_EVIDENCE_CONTRACT,
                    root / "host_tools" / BACKEND_EVIDENCE_CONTRACT.name)
    for validator in (KERNEL_LOG_VALIDATOR, METADATA_LOG_VALIDATOR,
                      METADATA_REPROBE_VALIDATOR,
                      VIRTIO_LOG_VALIDATOR):
        shutil.copyfile(validator, root / "scripts" / validator.name)
    shutil.copyfile(FS_ALLOCATOR_IMAGE, root / "scripts" / FS_ALLOCATOR_IMAGE.name)
    (root / "user" / "src").mkdir(parents=True)
    shutil.copyfile(BENCHMARK_SOURCE, root / "user" / "src" / BENCHMARK_SOURCE.name)
    for target in ("user", "baseline_ucore/user"):
        source_dir = root / target / "src"
        source_dir.mkdir(parents=True, exist_ok=True)
        for source in (REPO / target / "src").glob("rp_*.c"):
            destination = source_dir / source.name
            if not destination.exists():
                destination.write_text("/* fixture source inventory */\n", encoding="ascii")
    for target in ("user", "baseline_ucore/user"):
        include = root / target / "include"
        include.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / target / "include" / "rp_program_manifest.h",
                        include / "rp_program_manifest.h")
    source_manifest = load_manifest(REPO)
    for label, relative in (("plain", "baseline_ucore/user"), ("agentos", "user")):
        names = sorted(target_state_names(REPO, source_manifest, label))
        lines = ["static void fixture_state_inventory(void) {"]
        lines.extend(f'  rp_write_file("{name}", "");' for name in names)
        lines.append("}")
        (root / relative / "src" / "state_inventory_fixture.c").write_text(
            "\n".join(lines) + "\n", encoding="ascii"
        )
    write_fs_evidence_fixture(root / "fs-allocator-evidence.tar")
    write_fixture_fs_verifier(root / "scripts" / "fs-allocator-evidence.py",
                              root / "fs-allocator-evidence.tar")
    config = {
        "agent_test_suite": {"expected_cases": (list(reversed(FIXTURE_AGENT_CASES)) if bad_timing else
                                                  list(FIXTURE_AGENT_CASES)),
                             "baseline_seconds": 2.0, "max_seconds": 4.0},
        "kernel_stack": {"baseline_required_bytes": 100, "max_required_bytes": 110,
                         "stack_size_bytes": 121 if bad_stack else 120,
                         "baseline_boot_required_bytes": 80,
                         "max_boot_required_bytes": 90, "boot_stack_size_bytes": 100},
        "agent_modules": {"aggregate_budgets": [{
            "name": "metadata_control_plane",
            "baseline_source_lines": 70, "max_source_lines": 90,
            "baseline_source_bytes": 7000, "max_source_bytes": 9000,
            "baseline_loaded_text_bytes": 5000, "max_loaded_text_bytes": 7000,
            "baseline_bss_bytes": 1500, "max_bss_bytes": 2500,
        }]},
    }
    (root / "ci").mkdir()
    for contract in OBSERVE_EVIDENCE_CONTRACTS:
        shutil.copyfile(contract, root / "ci" / contract.name)
    (root / "ci" / "kernel-budgets.json").write_text(json.dumps(config) + "\n", encoding="utf-8")
    tools = root / "tools"
    version_tool = ("#!/usr/bin/env bash\nprintf '\\322fixture tool 1.0\\n'\n"
                    if non_utf8_version else
                    "#!/usr/bin/env bash\necho 'fixture tool 1.0'\n")
    compiler = tools / "riscv64-linux-gnu-gcc"
    qemu = tools / "qemu-system-riscv64"
    host_cc = tools / "cc"
    for tool in (compiler, qemu, host_cc):
        executable(tool, version_tool)
    write_semantic_fixture_generator(tools / "write-semantic-artifacts.py")
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
for artifact in \
  dual-plain-qemu.log dual-agentos-qemu.log dual-stage-timings.csv \
  dual-state-compare.json ch3-trace-guest.log agent-suite-guest.log \
  proc-reap.log syscall-fairness.log file-resource.log thread-resource.log \
  physical-resource.log metadata-recovery.log observe-recovery.log observe-recovery-before-reap.img virtio-disk.log \
  workflow-teardown-race.log fs-enospc.log fs-allocator-fault.log; do
  printf '%s passed\n' "${artifact}" >"${incoming}/${artifact}"
done
cp fs-allocator-evidence.tar "${incoming}/fs-allocator-evidence.tar"
cat >"${incoming}/agent-suite-guest.log" <<'EOF'
agentbench_ucore: file_query_benchmark schema=2 unit=us load=143 traversal_ops=64 traversal_records=143 traversal_duration_us=36 cold_index_ops=1 cold_index_records=6 cold_index_duration_us=2 cold_rebuild_records=512 cold_rebuild_included=1 warm_index_ops=64 warm_index_records=6 warm_index_duration_us=20 status=measured
agentbench_ucore: parent passed
EOF
cp "${incoming}/agent-suite-guest.log" \
  "${incoming}/dual-targeted-agentbench-guest.log"
tools/write-semantic-artifacts.py "${incoming}"
PYTHONPATH=host_tools python3 - "${incoming}" "$(git rev-parse HEAD)" <<'PY'
import sys
from pathlib import Path
from measured_experiments import (
    extract_file_query_measurements,
    write_csv,
    write_manifest,
)

incoming = Path(sys.argv[1])
commit = sys.argv[2]
source_name = "dual-targeted-agentbench-guest.log"
manifest = extract_file_query_measurements(
    incoming / source_name,
    source_name,
    ["env", "AGENT_TEST_CASE=agentbench_ucore", "bash", "scripts/run-agent-tests.sh"],
    commit,
    f"dual-{commit[:12]}-fixture",
)
write_manifest(incoming / "dual-measured-experiments.json", manifest)
write_csv(incoming / "dual-file-query-benchmark.csv", manifest["rows"])
PY
mainflow_artifacts="$(PYTHONPATH=host_tools python3 -c \
  'from dual_state_evidence_contract import DUAL_STATE_RAW_ARTIFACTS; print("\t".join(DUAL_STATE_RAW_ARTIFACTS))')"
printf 'target-structure\t1\t2\nkernel-budgets\t2\t3\nhost-platform-alignment\t3\t4\nch3-trace\t4\t5\tch3-trace-guest.log\nagent-suite\t5\t6\tagent-suite-timings.log\tagent-suite-guest.log\ndual-platforms\t6\t7\tdual-plain-qemu.log\tdual-agentos-qemu.log\tdual-stage-timings.csv\tdual-state-compare.json\thost-platform-alignment.json\t%s\tdual-targeted-agentbench-guest.log\tdual-measured-experiments.json\tdual-file-query-benchmark.csv\nproc-reap\t7\t8\tproc-reap.log\nsyscall-fairness\t8\t9\tsyscall-fairness.log\nfile-resource\t9\t10\tfile-resource.log\nthread-resource\t10\t11\tthread-resource.log\nphysical-resource\t11\t12\tphysical-resource.log\nmetadata-recovery\t12\t13\tmetadata-recovery.log\nobserve-recovery\t13\t14\tobserve-recovery.log\tobserve-recovery-before-reap.img\nvirtio-disk\t14\t15\tvirtio-disk.log\nworkflow-teardown-race\t15\t16\tworkflow-teardown-race.log\nfs-enospc\t16\t17\tfs-enospc.log\nfs-allocator-fault\t17\t18\tfs-allocator-fault.log\tfs-allocator-evidence.tar\n' \
  "${mainflow_artifacts}" >"${runtime}/steps.tsv"
python3 -I -S scripts/trusted-python-entry.py scripts/capture-final-evidence.py write-summary \
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
echo '[kernel-budget] agent-modules checks begin'
echo '[kernel-budget] Agent aggregate metadata_control_plane source_lines: actual=80 lines baseline=70 lines limit=90 lines'
echo '[kernel-budget] Agent aggregate metadata_control_plane source_bytes: actual=8000 bytes baseline=7000 bytes limit=9000 bytes'
echo '[kernel-budget] Agent aggregate metadata_control_plane loaded_text_bytes: actual=6000 bytes baseline=5000 bytes limit=7000 bytes'
echo '[kernel-budget] Agent aggregate metadata_control_plane bss_bytes: actual=2000 bytes baseline=1500 bytes limit=2500 bytes'
echo '[kernel-budget] agent-modules checks passed'
echo 'kernel stack budget: user=1 interrupt=1 margin=1 required=8191 limit=8192'
echo 'boot stack budget: root=main path=1 interrupt=1 margin=1 required=4095 limit=4096'
echo '[full-verify] Agent duration policy profile=none status=skipped-different-runner'
echo '[full-verify] all checks passed'
'''
        if slow_make:
            slow_body = ("bash -c 'trap \"\" TERM; sleep 2; printf ran >\"$1\"' _ "
                         f"{shlex.quote(str(root / 'slow-sentinel'))} &\nwait\n")
            make_body = make_body.replace(
                "if [[ ${1:-} == --version ]]; then echo 'fixture make 1.0'; exit 0; fi\n",
                "if [[ ${1:-} == --version ]]; then echo 'fixture make 1.0'; exit 0; fi\n" + slow_body)
    executable(make, make_body)
    for relative in (
        "host_tools/check_seeded_action_state.py",
        "host_tools/plain_ucore_action_runner.py",
        "scripts/run-dual-platforms.sh",
        "scripts/run-full-verification.sh",
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / relative, destination)
    run(["git", "add", "-A"], root)
    run(["git", "commit", "-q", "-m", "fixture"], root)
    return {"make": make, "compiler_prefix": tools / "riscv64-linux-gnu-",
            "qemu": qemu, "host_cc": host_cc, "sentinel": root / "slow-sentinel"}
def collect_args(repo: Path, output: Path, tools: dict[str, Path]) -> list[str]:
    return [*collector_command(repo, "collect"), "--repo-root", str(repo),
            "--output", str(output), "--toolprefix", str(tools["compiler_prefix"]),
            "--qemu", str(tools["qemu"]), "--make", str(tools["make"]),
            "--host-cc", str(tools["host_cc"]), "--python", BACKING_PYTHON,
            "--agent-test-duration-profile", "none",
            "--bash", str(resolve_bash_executable("bash", resolve_executable("git"))),
            "--command-timeout", "30"]
class FinalEvidenceTests(unittest.TestCase):
    def test_bash_resolution_selects_an_executable_gnu_bash(self) -> None:
        bash = resolve_bash_executable("bash", resolve_executable("git"))
        result = run(
            [str(bash), "--noprofile", "--norc", "-c",
             "printf '%s\\n' \"${BASH_VERSION-}\""],
            REPO, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout.strip(), r"^[0-9]+\.[0-9]+")

    def test_external_output_decode_preserves_status_and_diagnostics(self) -> None:
        result = run(
            [sys.executable, "-c",
             "import os,sys; os.write(1,b'out\\xd2\\n'); "
             "os.write(2,b'err\\xff\\n'); sys.exit(17)"],
            REPO, check=False,
        )
        self.assertEqual(result.returncode, 17)
        self.assertEqual(result.stdout, "out\ufffd\n")
        self.assertEqual(result.stderr, "err\ufffd\n")

    def test_state_archive_is_bounded_deterministic_and_link_safe(self) -> None:
        def write_state(root: Path, image: str, payloads: dict[str, bytes]) -> None:
            root.mkdir()
            for name, payload in payloads.items():
                (root / name).write_bytes(payload)
            (root / "extract-summary.json").write_text(
                json.dumps(
                    {
                        "image": image,
                        "extracted_state_files": len(payloads),
                        "files": sorted(payloads),
                        "status": "ready",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first"
            second = root / "second"
            payloads = {"rp_a": b"value=a\n", "rp_b": b"value=b\n"}
            write_state(first, "/checkout/one/fs.img", payloads)
            write_state(second, "/other/checkout/fs.img", payloads)
            first_archive = root / "missing" / "first.zip"
            second_archive = root / "second.zip"
            dual_state_archive.pack_state(first, first_archive)
            dual_state_archive.pack_state(second, second_archive)
            self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())

            victim = root / "victim.txt"
            victim.write_text("unchanged\n", encoding="utf-8")
            predictable = second_archive.with_name(second_archive.name + ".tmp")
            try:
                os.symlink(victim, predictable)
            except OSError:
                predictable = None
            if predictable is not None and not predictable.is_symlink():
                # 原生 MSYS 可能通过复制目标来模拟 os.symlink()。
                predictable.unlink(missing_ok=True)
                predictable = None
            dual_state_archive.pack_state(second, second_archive)
            self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged\n")
            if predictable is not None:
                self.assertTrue(predictable.is_symlink())

            old_file_limit = dual_state_archive.MAX_FILE_BYTES
            old_archive_limit = dual_state_archive.MAX_ARCHIVE_BYTES
            try:
                dual_state_archive.MAX_FILE_BYTES = 128
                oversized = root / "oversized"
                write_state(oversized, "/image", {"rp_a": b"x" * 129})
                with self.assertRaisesRegex(ValueError, "invalid size"):
                    dual_state_archive.pack_state(oversized, root / "oversized.zip")

                dual_state_archive.MAX_FILE_BYTES = 1024
                dual_state_archive.MAX_ARCHIVE_BYTES = 128
                total = root / "total"
                write_state(
                    total,
                    "/image",
                    {"rp_a": b"a" * 32, "rp_b": b"b" * 32},
                )
                with self.assertRaisesRegex(ValueError, "total-size limit"):
                    dual_state_archive.pack_state(total, root / "total.zip")
            finally:
                dual_state_archive.MAX_FILE_BYTES = old_file_limit
                dual_state_archive.MAX_ARCHIVE_BYTES = old_archive_limit

    def test_mainflow_telemetry_has_a_closed_record_schema(self) -> None:
        lines = [
            ";".join(
                [f"stage={spec.stage}"]
                + [f"{key}={value}" for key, value in spec.telemetry_fields]
                + ["status=ready"]
            )
            for spec in MAIN_FLOW_SOURCE_SPECS
        ]
        mutations = {
            "extra failed record": lines
            + ["diagnostic=guest_failure;status=failed"],
            "unknown field": [lines[0] + ";debug=forged", *lines[1:]],
        }
        with tempfile.TemporaryDirectory() as temp:
            raw_dir = Path(temp)
            telemetry = raw_dir / MAIN_FLOW_TELEMETRY_ARTIFACT
            context = ValidationContext(raw_dir=raw_dir, repo_root=REPO)
            telemetry.write_bytes(("\n".join(lines) + "\n").encode("ascii"))
            stages, _raw = _validate_telemetry(context)
            self.assertEqual(stages, tuple(spec.stage for spec in MAIN_FLOW_SOURCE_SPECS))
            for label, mutated in mutations.items():
                with self.subTest(label=label):
                    telemetry.write_bytes(
                        ("\n".join(mutated) + "\n").encode("ascii")
                    )
                    with self.assertRaises(EvidenceSemanticError):
                        _validate_telemetry(context)

    def test_semantic_program_manifest_matches_both_targets(self) -> None:
        pattern = re.compile(r'^\s*APPLY\("(rp_[a-z0-9_]+)"\)\s*\\?\s*$', re.MULTILINE)
        for relative in (
            "user/include/rp_program_manifest.h",
            "baseline_ucore/user/include/rp_program_manifest.h",
        ):
            with self.subTest(relative=relative):
                observed = tuple(pattern.findall((REPO / relative).read_text(encoding="utf-8")))
                self.assertEqual(observed, PLATFORM_PROGRAMS)
        programs, roles, errors = read_expected_programs(REPO)
        self.assertEqual(errors, [])
        self.assertEqual(programs, PLATFORM_PROGRAMS)
        self.assertEqual(roles, AGENTOS_REQUIRED_AGENT_ROLES)
        batch = set(AGENTOS_WORKER_BATCH_PROGRAMS)
        direct = set(AGENTOS_WORKER_DIRECT_PROGRAMS)
        role = set(roles)
        self.assertEqual(
            (len(role), len(batch), len(direct), len(AGENTOS_WORKER_BATCH_GROUPS)),
            (10, 58, 2, 3),
        )
        self.assertFalse(role & batch or role & direct or batch & direct)
        self.assertEqual(role | batch | direct, set(programs))

    def test_semantic_program_ledger_rejects_launch_classification_mutations(self) -> None:
        lines = ["orchestrator=rp_orch", "launcher=mixed_attested"]
        for index, program in enumerate(PLATFORM_PROGRAMS, 1):
            role = AGENTOS_REQUIRED_AGENT_ROLES.get(program)
            launcher, identity_source = agentos_program_launch_contract(program)
            lines.append(
                f"program={program};role={role or 'plain'};launcher={launcher};"
                f"identity_source={identity_source};is_agent={int(role is not None)};"
                f"agent_role={AGENTOS_ROLE_NUMBERS.get(role or '', 0)};"
                f"filesystem_domain=3;"
                f"filesystem_capabilities={AGENTOS_PROGRAM_FILESYSTEM_CAPABILITIES};"
                f"ok=1;code=0;elapsed_ms={index}"
            )

        def mutate(program: str, old: str, new: str) -> bytes:
            candidate = list(lines)
            row = next(
                offset
                for offset, line in enumerate(candidate)
                if line.startswith(f"program={program};")
            )
            self.assertIn(old, candidate[row])
            candidate[row] = candidate[row].replace(old, new, 1)
            return ("\n".join(candidate) + "\n").encode("ascii")

        mutations = {
            "batch as legacy direct": mutate(
                "rp_catalog", "launcher=agent_worker_batch", "launcher=agent_worker_create"
            ),
            "direct as batch": mutate(
                "rp_compare_plain", "launcher=agent_worker_create", "launcher=agent_worker_batch"
            ),
            "role as batch": mutate(
                "rp_query", "launcher=agent_create_role", "launcher=agent_worker_batch"
            ),
            "batch source swap": mutate(
                "rp_catalog",
                "identity_source=trusted_crt_batch_dispatch",
                "identity_source=trusted_crt_self_check",
            ),
            "role source swap": mutate(
                "rp_query",
                "identity_source=trusted_crt_self_check",
                "identity_source=trusted_crt_batch_dispatch",
            ),
            "domain mismatch": mutate(
                "rp_state_catalog", "filesystem_domain=3", "filesystem_domain=4"
            ),
            "capability mismatch": mutate(
                "rp_catalog", "filesystem_capabilities=66", "filesystem_capabilities=2"
            ),
        }
        with tempfile.TemporaryDirectory() as temp:
            raw_dir = Path(temp)
            artifact = raw_dir / PROGRAM_LEDGER_ARTIFACTS["agentos"]
            context = ValidationContext(raw_dir=raw_dir, repo_root=REPO)
            artifact.write_bytes(("\n".join(lines) + "\n").encode("ascii"))
            receipt, programs = _validate_program_ledger(context, "agentos")
            self.assertEqual(programs, PLATFORM_PROGRAMS)
            self.assertEqual(receipt["programs_observed"], len(PLATFORM_PROGRAMS))
            for label, payload in mutations.items():
                with self.subTest(label=label), self.assertRaises(
                    EvidenceSemanticError
                ):
                    artifact.write_bytes(payload)
                    _validate_program_ledger(context, "agentos")

    def test_dual_state_aggregates_are_bound_to_exact_rows(self) -> None:
        cost_rows = []
        for index, case in enumerate(BACKEND_REPORT_CASES["agentos"], 1):
            plain_case = AGENT_TO_PLAIN_CASE[case]
            cost_rows.append({
                "case": case, "plain_cost": f"fixture_cost_{index}",
                "agentos_replace": f"fixture_replace_{index}",
                "risk": f"fixture_risk_{index}", "plain_case": plain_case,
                "preserved_from_plain": int(bool(plain_case)),
                "status": "reference_ready",
            })
        references = {
            target: list(expected_reference_identities(target))
            for target in ("plain", "agentos")
        }
        state = {
            "plain_files": 300, "agentos_files": 320, "common_files": 300,
            "agentos_extra_files": 20, "checked_compatibility_records": 1,
            "plain_reference_products": sum(row.startswith("file:") for row in references["plain"]),
            "agentos_reference_products": sum(row.startswith("file:") for row in references["agentos"]),
            "plain_reference_records": sum(not row.startswith("file:") for row in references["plain"]),
            "agentos_reference_records": sum(not row.startswith("file:") for row in references["agentos"]),
            "plain_reference_identities": references["plain"],
            "agentos_reference_identities": references["agentos"],
            "guest_source_bound_runtime_records": 0, "preserved_plain_costs": 7,
            "cost_replacements": cost_rows, "cost_replacement_count": 8,
            "runner_tick_status": "unavailable",
            "runner_tick_reason": "plain_runtime_cases_zero",
            "embedded_action_records": 1, "run_result_match": 1,
            "agentos_evidence_checks": evidence_check_count(),
            "scenario_evidence": expected_scenario_rows(),
            "host_derived_mainflow_stages": len(MAIN_FLOW_SOURCE_SPECS),
            "agentos_mainflow_facts": 12,
            "agentos_mainflow_verification_origin": "host_inventory",
            "plain_timing_records": len(PLATFORM_PROGRAMS), "plain_agent_launches": 0,
            "plain_fork_launches": len(PLATFORM_PROGRAMS),
            "agentos_timing_records": len(PLATFORM_PROGRAMS),
            "agentos_agent_launches": len(AGENTOS_REQUIRED_AGENT_ROLES),
            "agentos_worker_launches": len(PLATFORM_PROGRAMS) - len(AGENTOS_REQUIRED_AGENT_ROLES),
            "backend_query_receipts": {
                target: {
                    field: (
                        int(value)
                        if field in {
                            "dataset_records", "query_operations", "query_matches",
                            "records_examined",
                        }
                        else value
                    )
                    for field, value in (
                        item.split("=", 1)
                        for item in state_fixture.backend_query_receipt_line(target).split(";")
                    )
                }
                for target in ("plain", "agentos")
            },
            "status": "ready",
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dual-state-compare.json"

            def validate(candidate: dict[str, object]) -> None:
                path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
                _validate_dual_state(path, len(PLATFORM_PROGRAMS), len(PLATFORM_PROGRAMS))

            validate(state)
            mutations = {
                "legacy runner ABI": lambda item: item.update(runner_tick_pairs=0),
                "file identity": lambda item: item.update(common_files=299),
                "cost row": lambda item: item["cost_replacements"].pop(),
                "scenario row": lambda item: item["scenario_evidence"][0].update(matched=99),
                "evidence aggregate": lambda item: item.update(agentos_evidence_checks=1),
                "launch/log binding": lambda item: item.update(plain_timing_records=68),
                "host origin": lambda item: item.update(
                    agentos_mainflow_verification_origin="guest_claim"
                ),
                "backend receipt digest": lambda item: item["backend_query_receipts"][
                    "agentos"
                ].update(result_digest="13819499490441518225"),
                "backend receipt digest type": lambda item: (
                    item["backend_query_receipts"]["plain"].update(
                        result_digest=13819499490441518226
                    ),
                    item["backend_query_receipts"]["agentos"].update(
                        result_digest=13819499490441518226
                    ),
                ),
                "backend receipt scan": lambda item: item["backend_query_receipts"][
                    "plain"
                ].update(records_examined=49151),
                "backend receipt schema": lambda item: item[
                    "backend_query_receipts"
                ]["agentos"].pop("backend"),
            }
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    candidate = copy.deepcopy(state)
                    mutate(candidate)
                    with self.assertRaises(EvidenceSemanticError):
                        validate(candidate)

    def test_semantic_registry_has_one_rule_per_static_raw_artifact(self) -> None:
        registered = [artifact for rule in RAW_ARTIFACT_REGISTRY for artifact in rule.artifacts]
        expected = {
            artifact for artifacts in PROFILE_ARTIFACTS.values() for artifact in artifacts
        }
        self.assertEqual(set(registered), expected)
        self.assertEqual(len(registered), len(set(registered)))
        for name in (
            "ch3-trace-guest.log", "proc-reap.log", "syscall-fairness.log", "file-resource.log",
            "thread-resource.log", "physical-resource.log", "metadata-recovery.log",
            "observe-recovery.log", "virtio-disk.log", "workflow-teardown-race.log",
            "fs-enospc.log", "fs-allocator-fault.log",
        ):
            self.assertEqual(sum(name in rule.artifacts for rule in RAW_ARTIFACT_REGISTRY), 1)

    def test_ch3_raw_evidence_rejects_missing_and_forged_lines(self) -> None:
        markers = ("string from task trace test", "Test trace OK!")
        valid = "boot noise\n" + "\n".join(markers) + "\nshutdown noise\n"
        mutations = {
            "missing-prefix": valid.replace(markers[0] + "\n", "", 1),
            "missing-completion": valid.replace(markers[1] + "\n", "", 1),
            "forged-prefix": valid.replace(markers[0], "forged " + markers[0], 1),
            "forged-suffix": valid.replace(markers[1], markers[1] + " forged", 1),
        }
        with tempfile.TemporaryDirectory() as temp:
            raw = Path(temp)
            artifact = raw / "ch3-trace-guest.log"
            artifact.write_text(valid, encoding="utf-8")
            validate_selected_artifacts(
                ("ch3-trace",), raw, REPO, require_exact_inventory=True
            )
            for label, payload in mutations.items():
                with self.subTest(label=label), self.assertRaisesRegex(
                    EvidenceSemanticError, "complete line"
                ):
                    artifact.write_text(payload, encoding="utf-8")
                    validate_selected_artifacts(
                        ("ch3-trace",), raw, REPO, require_exact_inventory=True
                    )
            artifact.write_text(valid, encoding="utf-8")
    def test_kernel_budget_metrics_are_bound_to_unique_block(self) -> None:
        namespace = __import__("runpy").run_path(str(COLLECTOR))
        parse_measurements = namespace["parse_measurements"]
        evidence_error = namespace["EvidenceError"]
        kernel = [
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
        aggregate = [
            "[kernel-budget] agent-modules checks begin",
            "[kernel-budget] Agent aggregate metadata_control_plane source_lines: actual=80 lines baseline=70 lines limit=90 lines",
            "[kernel-budget] Agent aggregate metadata_control_plane source_bytes: actual=8000 bytes baseline=7000 bytes limit=9000 bytes",
            "[kernel-budget] Agent aggregate metadata_control_plane loaded_text_bytes: actual=6000 bytes baseline=5000 bytes limit=7000 bytes",
            "[kernel-budget] Agent aggregate metadata_control_plane bss_bytes: actual=2000 bytes baseline=1500 bytes limit=2500 bytes",
            "[kernel-budget] agent-modules checks passed",
        ]
        canonical = kernel + aggregate
        config = {
            "agent_test_suite": {"expected_cases": ["case_one", "case_two"],
                                 "baseline_seconds": 2.0, "max_seconds": 4.0},
            "kernel_stack": {"baseline_required_bytes": 100, "max_required_bytes": 110,
                             "stack_size_bytes": 120, "baseline_boot_required_bytes": 80,
                             "max_boot_required_bytes": 90, "boot_stack_size_bytes": 100},
            "agent_modules": {"aggregate_budgets": [{
                "name": "metadata_control_plane",
                "baseline_source_lines": 70, "max_source_lines": 90,
                "baseline_source_bytes": 7000, "max_source_bytes": 9000,
                "baseline_loaded_text_bytes": 5000, "max_loaded_text_bytes": 7000,
                "baseline_bss_bytes": 1500, "max_bss_bytes": 2500,
            }]},
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
            rows, _ = parse_measurements(
                full_log, timing_log, config, duration_profile="none"
            )
            metrics = {row["metric"]: row for row in rows}
            self.assertEqual(metrics["kernel_stack_required_bytes"]["actual"], 101)
            self.assertEqual(metrics["boot_stack_required_bytes"]["actual"], 81)
            self.assertEqual(metrics["kernel_stack_required_bytes"]["source_line"],
                             lines.index(kernel[8]) + 1)
            self.assertEqual(
                tuple((metrics[f"metadata_control_plane_{name}"]["actual"],
                       metrics[f"metadata_control_plane_{name}"]["unit"])
                      for name in ("source_lines", "source_bytes", "loaded_text_bytes", "bss_bytes")),
                ((80, "lines"), (8000, "bytes"), (6000, "bytes"), (2000, "bytes")),
            )
            self.assertEqual(metrics["metadata_control_plane_source_lines"]["source_line"],
                             lines.index(aggregate[1]) + 1)
            wrong_unit = aggregate[1].replace("80 lines", "80 bytes").replace(
                "70 lines", "70 bytes").replace("90 lines", "90 bytes")
            malformed = (
                (kernel[:9] + [kernel[8]] + kernel[9:] + aggregate, "duplicate stack metric"),
                (kernel[:10] + [kernel[9]] + kernel[10:] + aggregate, "duplicate stack metric"),
                (canonical + kernel, "multiple kernel budget blocks"),
                (kernel[:1] + canonical, "multiple kernel budget blocks"),
                (kernel[:-1], "kernel budget block is unterminated"),
                (kernel[:-1] + [" " + kernel[-1]], "kernel budget block is unterminated"),
                (kernel[1:] + aggregate, "kernel budget block boundary"),
                ([kernel[-1]] + canonical, "kernel budget block boundary"),
                (kernel[:2] + [kernel[1]] + kernel[2:] + aggregate, "duplicate kernel metric"),
                (canonical + [kernel[-1]], "kernel budget block boundary"),
                (outside + kernel[:8] + kernel[9:] + aggregate + outside,
                 "kernel_stack_required_bytes"),
                ([kernel[1]] + kernel[:1] + kernel[2:] + aggregate, "stripped_kernel_elf_bytes"),
                (kernel + aggregate[:2] + [aggregate[1]] + aggregate[2:], "duplicate metadata aggregate"),
                (kernel + aggregate[:1] + aggregate[2:], "metadata_control_plane_source_lines"),
                (kernel + [aggregate[1]] + aggregate, "outside its budget block"),
                (canonical + [aggregate[1]], "outside its budget block"),
                (kernel + aggregate[1:], "outside its budget block"),
                (canonical[:-1], "agent module budget block is unterminated"),
                (canonical[:-1] + [" " + aggregate[-1]], "agent module budget block is unterminated"),
                (kernel + [aggregate[0]] + aggregate, "agent module budget block boundary"),
                (canonical + [aggregate[-1]], "agent module budget block boundary"),
                (kernel + [aggregate[-1]] + aggregate, "agent module budget block boundary"),
                (kernel + aggregate[:1] + [aggregate[1].replace("source_lines", "source_words")]
                 + aggregate[2:], "unexpected metadata aggregate metric"),
                (kernel + aggregate[:1] + [wrong_unit] + aggregate[2:],
                 "metadata aggregate log and configuration differ"),
            )
            for index, (contents, message) in enumerate(malformed):
                with self.subTest(index=index, message=message):
                    full_log.write_text("\n".join(contents) + "\n")
                    with self.assertRaisesRegex(evidence_error, message):
                        parse_measurements(
                            full_log, timing_log, config, duration_profile="none"
                        )
            full_log.write_text("\n".join(canonical) + "\n")
            for field in (
                "baseline_source_lines", "max_source_lines", "baseline_source_bytes", "max_source_bytes",
                "baseline_loaded_text_bytes", "max_loaded_text_bytes", "baseline_bss_bytes", "max_bss_bytes",
            ):
                with self.subTest(config_field=field):
                    changed = json.loads(json.dumps(config))
                    changed["agent_modules"]["aggregate_budgets"][0][field] += 1
                    with self.assertRaisesRegex(evidence_error, "log and configuration differ"):
                        parse_measurements(
                            full_log, timing_log, changed, duration_profile="none"
                        )

            duration_row = metrics["agent_suite_total_seconds"]
            self.assertIsNone(duration_row["baseline"])
            self.assertIsNone(duration_row["limit"])
            self.assertIsNone(duration_row["usage_ratio"])
            over_historical_limit = json.loads(json.dumps(config))
            over_historical_limit["agent_test_suite"]["max_seconds"] = 0.001
            rows, _ = parse_measurements(
                full_log,
                timing_log,
                over_historical_limit,
                duration_profile="none",
            )
            self.assertEqual(
                next(
                    row["actual"]
                    for row in rows
                    if row["metric"] == "agent_suite_total_seconds"
                ),
                2.75,
            )
            provisional = json.loads(json.dumps(config))
            provisional["agent_test_suite"][
                "calibration_status"
            ] = "provisional_requires_full_suite"
            with self.assertRaisesRegex(evidence_error, "not fully calibrated"):
                parse_measurements(
                    full_log,
                    timing_log,
                    provisional,
                    duration_profile="local-e3",
                )
    def test_summary_inventory_is_generated_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            stage = base / "good"
            steps = populate_summary_stage(stage)
            incoming = stage / "incoming"
            commit = "a" * 40
            run([*collector_command(REPO, "write-summary"), "--stage", str(stage),
                 "--steps", str(steps), "--commit", commit], REPO)
            summary = json.loads((incoming / "verification-summary.json").read_text())
            self.assertEqual((summary["schema_version"], summary["full_verify_profile_version"]), (8, 7))
            canonical_steps = json.dumps(summary["steps"], ensure_ascii=True,
                                         sort_keys=True, separators=(",", ":"))
            self.assertEqual(summary["step_contract_sha256"],
                             __import__("hashlib").sha256(canonical_steps.encode()).hexdigest())
            self.assertEqual([step["name"] for step in summary["steps"]], list(PROFILE_ARTIFACTS))
            self.assertEqual({item["name"] for item in summary["artifacts"]},
                             {item for items in PROFILE_ARTIFACTS.values() for item in items})
            self.assertEqual(summary["settings"]["mechanism_marker_grace_seconds"], "5s")
            overlap = base / "overlap"
            overlap_steps = populate_summary_stage(overlap)
            overlap_steps.write_text(
                overlap_steps.read_text().replace(
                    "syscall-fairness\t8\t9\t",
                    "syscall-fairness\t7.250000000\t8.500000000\t",
                )
            )
            run(
                [
                    *collector_command(REPO, "write-summary"),
                    "--stage", str(overlap), "--steps", str(overlap_steps),
                    "--commit", commit,
                ],
                REPO,
            )
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
                result = run([*collector_command(REPO, "write-summary"), "--stage", str(bad),
                              "--steps", str(bad_steps), "--commit", commit, *options], REPO,
                             check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse((bad / "incoming/verification-summary.json").exists())
                self.assertFalse(any("partial" in item.name for item in (bad / "incoming").iterdir()))
    def test_bind_remote_ci_cli_is_absent(self) -> None:
        help_result = run(collector_command(REPO, "--help"), REPO)
        self.assertNotIn("bind-remote-ci", help_result.stdout)
        rejected = run(collector_command(REPO, "bind-remote-ci"), REPO, check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("invalid choice", rejected.stderr)

if __name__ == "__main__":
    unittest.main(verbosity=2)
