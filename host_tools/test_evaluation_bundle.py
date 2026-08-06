#!/usr/bin/env python3
"""Regression tests for portable AgentOS evaluation evidence bundles."""

from __future__ import annotations

import binascii
import copy
import hashlib
import gzip
import io
import inspect
import json
import os
import shutil
import struct
import subprocess
import tarfile
import tempfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import evaluation_bundle as bundle
import full_verification_payload as full_verification_contract
import evaluation_platform as platform_probe
import evaluation_scenario as scenario_evidence
import functional_acceptance_compile_contract as functional_compile_contract
import plain_ucore_fs_extract as fs_extract
import test_plain_ucore_fs_extract as fs_fixture
from evaluation_campaign import (
    FORMAL_MICRO_TIMEOUT_SECONDS,
    SCHEMA_VERSION as CAMPAIGN_SCHEMA_VERSION,
    SCENARIO_SCHEMA_VERSION,
    _canonical_sha256,
    _expected_samples_per_boot,
    _micro_boot_environment,
    _scenario_boot_environment,
    export_run_plan,
    format_preflight_receipt,
    validate_campaign,
    validate_scenario_campaign,
)
from agenteval_measurement_source_contract import build_measurement_source_receipt
from evaluation_contract import (
    build,
    derive_acceptance_gates,
    load_suite,
    write_json,
    write_jsonl,
)
from evaluation_scenario import collect_scenario, read_expected_programs
from render_evaluation_dashboard import render
from test_evaluation_campaign import (
    _msys_platform_proof,
    _platform_proof,
    _scenario_environment,
)
from test_compatibility_overhead import materialize_compatibility_fixture
from test_evaluation_contract import COMMIT, SUITE_PATH, make_log
from test_evaluation_scenario import _write_target
from test_evaluation_dashboard import write_kernel_cost_sidecar
from test_full_verification_payload import make_payload as make_full_verification_payload


ROOT = Path(__file__).resolve().parents[1]
HOST_STATE_NAMES = set(bundle.load_manifest(ROOT).host_state_files)
_STATE_NAME_MAP_CACHE: dict[Path, dict[str, str]] = {}
EXPECTED_SCENARIO_IMAGE_CALLS = {
    (f"boot-{number:02d}", target)
    for number in range(1, 8)
    for target in ("plain", "agentos")
}


def assert_image_verification_delta(
    calls: list[tuple[str, str]], start: int
) -> None:
    delta = calls[start:]
    assert len(delta) == len(EXPECTED_SCENARIO_IMAGE_CALLS)
    assert set(delta) == EXPECTED_SCENARIO_IMAGE_CALLS


class _LocalLinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value is not None and name in {"href", "src"}:
                self.references.append(value)


def assert_clean_clone_dashboard_links(page: Path, allowed_root: Path) -> None:
    collector = _LocalLinkCollector()
    collector.feed(page.read_text(encoding="utf-8"))
    root = allowed_root.resolve(strict=True)
    for reference in collector.references:
        parsed = urlsplit(reference)
        assert not parsed.scheme and not parsed.netloc, reference
        path = unquote(parsed.path)
        if not path:
            continue
        assert not path.startswith(("/", "\\")), reference
        target = (page.parent / Path(*path.split("/"))).resolve(strict=True)
        target.relative_to(root)
        assert target.is_file(), reference


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_strict(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def reseal_portable_bundle_fixture(root: Path) -> None:
    """Rebind a deliberately attacker-controlled portable bundle fixture."""

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["campaign_sha256"] = digest(root / "run" / "campaign.json")
    records = {record["path"]: record for record in manifest["files"]}
    for relative, record in records.items():
        path = root.joinpath(*relative.split("/"))
        record["bytes"] = path.stat().st_size
        record["sha256"] = digest(path)
    receipt_path = "run/measurement-source-receipt.json"
    receipt_record = records[receipt_path]
    for artifact in manifest["artifacts"]:
        if artifact["path"] == receipt_path:
            artifact["bytes"] = receipt_record["bytes"]
            artifact["sha256"] = receipt_record["sha256"]
    body = {
        key: value for key, value in manifest.items() if key != "binding_sha256"
    }
    manifest["binding_sha256"] = bundle._binding_sha256(body)
    write_strict(manifest_path, manifest)
    checksum_paths = sorted(["manifest.json", *records])
    (root / "checksums.sha256").write_text(
        "".join(
            f"{digest(root.joinpath(*relative.split('/')))}  {relative}\n"
            for relative in checksum_paths
        ),
        encoding="ascii",
        newline="\n",
    )


def assert_packaged_contract_code_is_never_executed(
    original_bundle: Path, root: Path
) -> None:
    attack = root / "untrusted-contract-code-bundle"
    shutil.copytree(original_bundle, attack)
    sentinel = root / "untrusted-calibration-executed"
    snapshot = attack / "run" / bundle.MEASUREMENT_SOURCE_SNAPSHOT_ROOT
    calibration = snapshot / "scripts" / "agent_test_calibration.py"
    calibration.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('executed', encoding='ascii')\n"
        "def validate_recorded_calibration_profile(profile, tools, host):\n"
        "    return profile['profile_id']\n",
        encoding="utf-8",
        newline="\n",
    )

    # Prove the fixture is executable if a verifier ever treats the snapshot as
    # a Python contract root, then remove the setup sentinel before both APIs.
    platform_probe._load_profile_component(
        snapshot,
        "scripts/agent_test_calibration.py",
        "agentos_malicious_bundle_calibration_fixture",
    )
    assert sentinel.is_file(), "malicious calibration fixture did not execute"
    sentinel.unlink()

    receipt_path = attack / "run" / "measurement-source-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    calibration_record = next(
        record
        for record in receipt["sources"]
        if record["path"] == "scripts/agent_test_calibration.py"
    )
    calibration_record["bytes"] = calibration.stat().st_size
    calibration_record["sha256"] = digest(calibration)
    write_strict(receipt_path, receipt)

    campaign_path = attack / "run" / "campaign.json"
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    forged_platform = _msys_platform_proof(root / "untrusted-platform-fixture")
    config = json.loads(
        (ROOT / "ci" / "kernel-budgets.json").read_text(encoding="utf-8")
    )["agent_test_suite"]
    forged_platform["duration_profile"] = {
        "calibration_status": config["calibration_status"],
        "name": "local-e3",
        "profile_id": config["local_calibration_profile"]["profile_id"],
        "status": "matched",
    }
    campaign["platform"] = forged_platform
    campaign["run"]["execution_domain"] = "native-msys2"
    campaign["measurement_source_receipt"] = receipt
    write_strict(campaign_path, campaign)

    plan_path = attack / "run" / "run-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["measurement_source_receipt"] = receipt
    plan["campaign_sha256"] = digest(campaign_path)
    write_strict(plan_path, plan)
    reseal_portable_bundle_fixture(attack)

    for label, verifier in (
        ("portable", lambda: bundle.verify_bundle(attack, contract_root=ROOT)),
        (
            "embedded-contract",
            lambda: bundle.verify_bundle(attack, contract_root=snapshot),
        ),
        (
            "committed",
            lambda: bundle.verify_committed_bundle(
                attack, ROOT, contract_root=ROOT
            ),
        ),
    ):
        try:
            verifier()
        except bundle.BundleError:
            pass
        else:
            raise AssertionError(f"{label} verifier accepted malicious bundle")
        assert not sentinel.exists(), f"{label} verifier executed packaged code"


def expect_rejected(action, message: str) -> None:
    try:
        action()
    except bundle.BundleError as error:
        assert message in str(error), error
    else:
        raise AssertionError(f"accepted invalid evaluation bundle: {message}")


def _write_test_archive(
    path: Path,
    entries: list[tuple[str, bytes, bytes, int]],
    *,
    gzip_mtime: int = 0,
) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=gzip_mtime) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name, data, kind, mode in entries:
                    info = tarfile.TarInfo(name)
                    info.size = len(data) if kind == tarfile.REGTYPE else 0
                    info.type = kind
                    info.mode = mode
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                        info.linkname = "raw/boot-01/guest.log"
                    archive.addfile(info, io.BytesIO(data) if info.size else None)


def _test_archive_record(
    path: Path, members: list[tuple[str, bytes]]
) -> dict[str, object]:
    receipts = [
        {
            "path": f"run/{name}",
            "raw_bytes": len(data),
            "raw_sha256": hashlib.sha256(data).hexdigest(),
        }
        for name, data in members
    ]
    raw_total = sum(len(data) for _name, data in members)
    return {
        "archive_id": "micro/boot-01",
        "stored_path": "run/archives/micro/boot-01.tar.gz",
        "stored_bytes": path.stat().st_size,
        "stored_sha256": digest(path),
        "raw_total_bytes": raw_total,
        "stored_total_bytes": path.stat().st_size,
        "member_count": len(receipts),
        "compression": bundle._compression_record(),
        "members": receipts,
    }


def _stored_block_deflate(payload: bytes) -> bytes:
    """Emit RFC 1951 stored blocks without using a compressor library."""
    chunks = [payload[index:index + 65535] for index in range(0, len(payload), 65535)]
    if not chunks:
        chunks = [b""]
    output = bytearray()
    for index, chunk in enumerate(chunks):
        output.append(1 if index == len(chunks) - 1 else 0)
        output.extend(struct.pack("<HH", len(chunk), len(chunk) ^ 0xFFFF))
        output.extend(chunk)
    return bytes(output)


def assert_archive_safety(root: Path) -> None:
    assert bundle.MAX_COMPRESSION_RATIO >= 1024
    assert bundle.MIN_RATIO_STORED_BYTES >= 1024
    source = root / "archive-source"
    member = source / "raw" / "boot-01" / "guest.log"
    member.parent.mkdir(parents=True)
    member.write_bytes(b"deterministic archive payload\n")
    first = root / "first.tar.gz"
    second = root / "second.tar.gz"
    one = bundle._make_archive_record(
        source, ["raw/boot-01/guest.log"], first,
        archive_id="micro/boot-01",
        stored_path="run/archives/micro/boot-01.tar.gz",
    )
    bundle._make_archive_record(
        source, ["raw/boot-01/guest.log"], second,
        archive_id="micro/boot-01",
        stored_path="run/archives/micro/boot-01.tar.gz",
    )
    assert first.read_bytes() == second.read_bytes()
    header = first.read_bytes()[:10]
    assert header[:4] == b"\x1f\x8b\x08\x00"
    assert header[4:8] == b"\0\0\0\0"
    assert header[8] == 2
    assert header[9] == 255
    extracted = root / "archive-extracted"
    extracted.mkdir()
    bundle._extract_archive(first, extracted, one)
    assert (extracted / "raw" / "boot-01" / "guest.log").read_bytes() == member.read_bytes()

    # A tiny independent stored-block encoder provides valid DEFLATE without
    # calling zlib's compressor. Portable verification binds the stored bytes
    # but canonicalizes USTAR instead of reproducing compressor output.
    tar_payload = gzip.decompress(first.read_bytes())
    deflate = _stored_block_deflate(tar_payload)
    alternate = (
        b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
        + deflate
        + struct.pack(
            "<II", binascii.crc32(tar_payload), len(tar_payload) & 0xFFFFFFFF
        )
    )
    assert alternate != first.read_bytes()
    portable = root / "portable-compressor.tar.gz"
    portable.write_bytes(alternate)
    portable_record = {
        **one,
        "stored_bytes": len(alternate),
        "stored_total_bytes": len(alternate),
        "stored_sha256": digest(portable),
    }
    portable_out = root / "archive-portable-compressor"
    portable_out.mkdir()
    bundle._extract_archive(portable, portable_out, portable_record)
    assert (
        portable_out / "raw" / "boot-01" / "guest.log"
    ).read_bytes() == member.read_bytes()

    concatenated = root / "concatenated-members.tar.gz"
    concatenated.write_bytes(first.read_bytes() + gzip.compress(b"", mtime=123456))
    concatenated_record = {
        **one,
        "stored_bytes": concatenated.stat().st_size,
        "stored_total_bytes": concatenated.stat().st_size,
        "stored_sha256": digest(concatenated),
    }
    concatenated_out = root / "archive-concatenated-members"
    concatenated_out.mkdir()
    expect_rejected(
        lambda: bundle._extract_archive(
            concatenated, concatenated_out, concatenated_record
        ),
        "exactly one canonical gzip member",
    )

    canonical = "raw/boot-01/guest.log"
    payload = b"payload\n"
    attacks = [
        ("absolute", "/escape", tarfile.REGTYPE, bundle.ARCHIVE_MODE, "canonical relative"),
        ("parent", "../escape", tarfile.REGTYPE, bundle.ARCHIVE_MODE, "canonical relative"),
        ("dot", "raw/./boot-01/guest.log", tarfile.REGTYPE, bundle.ARCHIVE_MODE, "canonical relative"),
        ("double", "raw//boot-01/guest.log", tarfile.REGTYPE, bundle.ARCHIVE_MODE, "canonical relative"),
        ("duplicate", canonical, tarfile.REGTYPE, bundle.ARCHIVE_MODE, "repeats a member"),
        ("symlink", canonical, tarfile.SYMTYPE, bundle.ARCHIVE_MODE, "canonical regular"),
        ("hardlink", canonical, tarfile.LNKTYPE, bundle.ARCHIVE_MODE, "canonical regular"),
        ("device", canonical, tarfile.CHRTYPE, bundle.ARCHIVE_MODE, "canonical regular"),
        ("directory", canonical, tarfile.DIRTYPE, bundle.ARCHIVE_MODE, "canonical regular"),
        ("mode", canonical, tarfile.REGTYPE, 0o600, "canonical regular"),
    ]
    for label, actual_name, kind, mode, message in attacks:
        path = root / f"attack-{label}.tar.gz"
        entries = [(actual_name, payload, kind, mode)]
        if label == "duplicate":
            entries.append((actual_name, payload, kind, mode))
        _write_test_archive(path, entries)
        record = _test_archive_record(path, [(canonical, payload)])
        destination = root / f"extract-{label}"
        destination.mkdir()
        expect_rejected(
            lambda path=path, destination=destination, record=record: bundle._extract_archive(
                path, destination, record
            ),
            message,
        )

    noncanonical = root / "noncanonical-gzip.tar.gz"
    _write_test_archive(
        noncanonical,
        [(canonical, payload, tarfile.REGTYPE, bundle.ARCHIVE_MODE)],
        gzip_mtime=1,
    )
    noncanonical_record = _test_archive_record(noncanonical, [(canonical, payload)])
    noncanonical_out = root / "extract-noncanonical"
    noncanonical_out.mkdir()
    expect_rejected(
        lambda: bundle._extract_archive(noncanonical, noncanonical_out, noncanonical_record),
        "canonical",
    )

    limits = (
        ("MAX_ARCHIVE_MEMBERS", 1, [(canonical, payload), ("raw/boot-01/kernel", payload)], "invalid"),
        ("MAX_ARCHIVE_RAW_BYTES", 1, [(canonical, payload)], "invalid"),
        ("MAX_COMPRESSION_RATIO", 0, [(canonical, payload)], "compression ratio"),
    )
    for name, value, members, message in limits:
        original = getattr(bundle, name)
        setattr(bundle, name, value)
        try:
            expect_rejected(
                lambda members=members: bundle._archive_members({
                    **one,
                    "member_count": len(members),
                    "members": [
                        {
                            "path": f"run/{path}",
                            "raw_bytes": len(data),
                            "raw_sha256": hashlib.sha256(data).hexdigest(),
                        }
                        for path, data in members
                    ],
                    "raw_total_bytes": sum(len(data) for _path, data in members),
                }),
                message,
            )
        finally:
            setattr(bundle, name, original)

    deep = "raw/boot-01/" + "/".join(["d"] * (bundle.MAX_ARCHIVE_DEPTH + 1))
    deep_record = {**one, "members": [{
        "path": f"run/{deep}", "raw_bytes": 0,
        "raw_sha256": hashlib.sha256(b"").hexdigest(),
    }], "raw_total_bytes": 0}
    expect_rejected(lambda: bundle._archive_members(deep_record), "invalid")


def assert_formal_kernel_cost_contract(root: Path) -> None:
    missing = root / "formal-kernel-cost-missing"
    missing.mkdir()
    expect_rejected(
        lambda: bundle._verify_kernel_cost(missing, require_complete=True),
        "requires complete kernel-cost evidence",
    )

    complete = root / "formal-kernel-cost-complete"
    complete.mkdir()
    write_kernel_cost_sidecar(complete)
    bundle._verify_kernel_cost(complete, require_complete=True)

    incomplete = root / "formal-kernel-cost-incomplete"
    incomplete.mkdir()
    write_kernel_cost_sidecar(incomplete, fail_measurement=True)
    expect_rejected(
        lambda: bundle._verify_kernel_cost(incomplete, require_complete=True),
        "kernel-cost evidence is incomplete",
    )


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=bundle.controlled_git_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


def assert_isolated_fixture_repository(repo: Path) -> None:
    observed = git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    assert Path(observed).resolve(strict=True) == repo.resolve(strict=True)


def materialize_committed_measurement_sources(
    repo: Path, commit: str, source_inventory: list[dict[str, object]]
) -> None:
    """Replace fixture worktree sources with their exact committed blob bytes."""

    executable = shutil.which("git")
    assert executable is not None
    process = subprocess.Popen(
        [executable, "-C", str(repo), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=bundle.controlled_git_environment(),
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    for record in source_inventory:
        source = str(record["path"])
        process.stdin.write(f"{commit}:{source}\n".encode("utf-8"))
        process.stdin.flush()
        header = process.stdout.readline().rstrip(b"\n").split()
        assert len(header) == 3 and header[1] == b"blob", (source, header)
        size = int(header[2])
        data = process.stdout.read(size)
        assert len(data) == size and process.stdout.read(1) == b"\n"
        destination = repo.joinpath(*source.split("/"))
        destination.write_bytes(data)
    process.stdin.close()
    returncode = process.wait(timeout=30)
    diagnostics = process.stderr.read().decode("utf-8", errors="replace")
    assert returncode == 0, diagnostics


def tool(argv0: str, marker: str) -> dict[str, str]:
    return {
        "argv0": argv0,
        "path": str((Path(__file__).resolve().parent / "fixture-tools" / Path(argv0).name).resolve()),
        "sha256": marker * 64,
        "version": f"{argv0} fixture 1",
    }


def make_run(
    root: Path,
    *,
    artifact_root: str = "results/evaluation/runs/contract-test",
    commit: str = COMMIT,
    measurement_source_root: Path = ROOT,
) -> Path:
    run = root / "run"
    raw = run / "raw"
    raw.mkdir(parents=True)
    suite = load_suite(SUITE_PATH)
    platform = _platform_proof(root)
    environment = {
        "assembler": tool("riscv64-linux-gnu-as", "a"),
        "bash": tool("bash", "1"),
        "compiler": tool("riscv64-linux-gnu-gcc", "2"),
        "git": tool("git", "6"),
        "host_cc": dict(platform["tools"]["host_cc"]),
        "linker": tool("riscv64-linux-gnu-ld", "7"),
        "make": tool("make", "3"),
        "objcopy": tool("riscv64-linux-gnu-objcopy", "8"),
        "objdump": tool("riscv64-linux-gnu-objdump", "9"),
        "python": tool("python3", "4"),
        "qemu": tool("qemu-system-riscv64", "5"),
    }
    expected_samples = _expected_samples_per_boot(SUITE_PATH)
    boots = []
    prefix = artifact_root
    for index in range(7):
        number = index + 1
        boot_id = f"boot-{number:02d}"
        challenge, guest_text = make_log(suite, index)
        boot_dir = raw / boot_id
        boot_dir.mkdir()
        artifacts = {
            "guest.log": guest_text.encode("utf-8"),
            "runner.log": f"runner {boot_id}\n".encode("ascii"),
            "kernel": b"shared fixture kernel\n",
            "fs.img": f"input {challenge}\n".encode("ascii"),
            "fs-copy.img": f"final {challenge}\n".encode("ascii"),
        }
        for name, data in artifacts.items():
            (boot_dir / name).write_bytes(data)
        raw_ref = f"{prefix}/raw/{boot_id}"
        command = [
            environment["bash"]["path"],
            str(
                (Path(__file__).resolve().parents[1] / "scripts" / "run-agent-tests.sh").resolve()
            ),
            f"AGENT_EVAL_CHALLENGE_HEX={challenge}",
            f"AGENT_TEST_GUEST_LOG_FILE={raw_ref}/guest.log",
        ]
        boots.append({
            "boot_id": boot_id,
            "challenge": challenge,
            "command_argv": command,
            "command_environment": _micro_boot_environment(
                environment, platform, challenge, f"{raw_ref}/guest.log"
            ),
            "exit_code": 0,
            "finished_at_utc": f"2026-07-30T00:00:{number:02d}Z",
            "guest_log": f"{raw_ref}/guest.log",
            "guest_log_sha256": digest(boot_dir / "guest.log"),
            "image_final_path": f"{raw_ref}/fs-copy.img",
            "image_final_sha256": digest(boot_dir / "fs-copy.img"),
            "image_input_path": f"{raw_ref}/fs.img",
            "image_input_sha256": digest(boot_dir / "fs.img"),
            "kernel_path": f"{raw_ref}/kernel",
            "kernel_sha256": digest(boot_dir / "kernel"),
            "observed_sample_orders": ["AB", "BA"],
            "runner_log": f"{raw_ref}/runner.log",
            "runner_log_sha256": digest(boot_dir / "runner.log"),
            "sample_count": expected_samples,
            "status": "passed",
        })
    platform["schema_version"] = platform_probe.SCHEMA_VERSION
    for label, identity in environment.items():
        if label in platform["tools"]:
            platform["tools"][label] = dict(identity)
    platform["launcher"] = dict(platform["tools"]["bash"])
    platform.setdefault("hardware", {
        "cpu_model": "Evaluation Fixture CPU",
        "logical_cpu_count": 4,
        "memory_total_bytes": 8 * 1024 * 1024 * 1024,
        "source": "procfs:/proc/cpuinfo+/proc/meminfo",
    })
    measurement_source_receipt = build_measurement_source_receipt(
        measurement_source_root, source_commit=commit
    )
    campaign = {
        "boots": boots,
        "environment": environment,
        "kind": "agentos-evaluation-campaign",
        "measurement_source_receipt": measurement_source_receipt,
        "platform": platform,
        "phase": "collected",
        "protocol": {
            "fresh_filesystem_per_boot": True,
            "independent_unit": "fresh-qemu-boot",
            "expected_samples_per_boot": expected_samples,
            "minimum_boots": 7,
            "micro_timeout_seconds": FORMAL_MICRO_TIMEOUT_SECONDS,
            "requested_boots": 7,
            "sample_order_policy": "guest-paired-alternating-ab-ba",
            "suite_path": "ci/evaluation-suite.json",
            "suite_sha256": digest(SUITE_PATH),
            "target": "agentos-same-kernel-ablation",
        },
        "run": {
            "artifact_root": artifact_root,
            "clean_worktree": True,
            "commit": commit,
            "completed_at_utc": "2026-07-30T00:01:00Z",
            "execution_domain": "native-linux",
            "id": "contract-test",
            "started_at_utc": "2026-07-30T00:00:00Z",
        },
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
    }
    validate_campaign(campaign)
    write_strict(run / "campaign.json", campaign)
    (run / "preflight.log").write_text(
        format_preflight_receipt(campaign), encoding="ascii", newline="\n"
    )
    write_strict(run / "measurement-source-receipt.json", measurement_source_receipt)
    export_run_plan(run / "campaign.json", run / "run-plan.json")
    summary, rows = build(SUITE_PATH, run / "run-plan.json", raw)
    write_json(run / "summary.json", summary)
    write_jsonl(run / "metrics.jsonl", rows)
    materialize_compatibility_fixture(run, ROOT, campaign)
    render(run / "summary.json", run / "dashboard", contract_root=ROOT)
    return run


def _state_short_name(repo_dir: Path, full_name: str) -> str:
    canonical_repo = repo_dir.resolve(strict=True)
    name_map = _STATE_NAME_MAP_CACHE.get(canonical_repo)
    if name_map is None:
        name_map = fs_extract.discover_name_map(canonical_repo)
        _STATE_NAME_MAP_CACHE[canonical_repo] = name_map
    matches = [
        short_name
        for short_name, restored_name in name_map.items()
        if restored_name == full_name
    ]
    if len(matches) != 1:
        raise AssertionError(f"no unique filesystem name for {repo_dir}/{full_name}")
    return matches[0]


def _build_state_image(
    image_path: Path,
    repo_dir: Path,
    state_files: dict[str, bytes],
    *,
    agentos: bool,
) -> None:
    magic = (
        fs_extract.FSMAGIC_AGENT_PRINCIPAL
        if agentos
        else fs_extract.FSMAGIC_BASELINE_PRINCIPAL
    )
    dinode_size = fs_extract.DINODE_SIZE_BY_MAGIC[magic]
    block_count = 256
    inode_count = 64
    image = bytearray(block_count * fs_extract.BSIZE)

    fs_fixture.put_u32(image, fs_extract.BSIZE, magic)
    fs_fixture.put_u32(image, fs_extract.BSIZE + 4, block_count)
    fs_fixture.put_u32(image, fs_extract.BSIZE + 12, inode_count)
    fs_fixture.put_u32(image, fs_extract.BSIZE + 16, 2)
    inode_blocks = (
        inode_count + fs_extract.BSIZE // dinode_size - 1
    ) // (fs_extract.BSIZE // dinode_size)
    bmap_start = 2 + inode_blocks
    bitmap_blocks = (
        block_count + fs_extract.BSIZE * 8 - 1
    ) // (fs_extract.BSIZE * 8)
    qmap_start = bmap_start + bitmap_blocks
    owner_blocks = (block_count + fs_extract.QPB - 1) // fs_extract.QPB
    data_start = qmap_start + owner_blocks
    fs_fixture.put_u32(image, fs_extract.BSIZE + 8, block_count - data_start)
    fs_fixture.put_u32(image, fs_extract.BSIZE + 20, bmap_start)
    fs_fixture.put_u32(image, fs_extract.BSIZE + 24, qmap_start)
    fs_fixture.put_u32(image, fs_extract.BSIZE + 28, data_start)

    workflow_blocks = 8
    workflow_inodes = 8
    system_blocks = 4
    system_inodes = 4
    policy_version = (
        fs_extract.FS_STORAGE_POLICY_VERSION
        if agentos
        else fs_extract.FS_STORAGE_POLICY_VERSION_LEGACY
    )
    fs_fixture.put_u32(image, fs_extract.BSIZE + 32, policy_version)
    fs_fixture.put_u32(
        image, fs_extract.BSIZE + 36, fs_extract.FS_WORKFLOW_SCOPE_SLOTS
    )
    fs_fixture.put_u32(image, fs_extract.BSIZE + 40, workflow_blocks)
    fs_fixture.put_u32(image, fs_extract.BSIZE + 44, workflow_inodes)
    fs_fixture.put_u32(image, fs_extract.BSIZE + 48, system_blocks)
    fs_fixture.put_u32(image, fs_extract.BSIZE + 52, system_inodes)
    if agentos:
        policy_checksum = fs_extract.storage_policy_checksum(
            policy_version,
            fs_extract.FS_WORKFLOW_SCOPE_SLOTS,
            fs_extract.FS_PUBLIC_PRINCIPAL_ID,
            workflow_blocks,
            workflow_inodes,
            system_blocks,
            system_inodes,
        )
        fs_fixture.put_u32(
            image, fs_extract.BSIZE + 56, fs_extract.FS_PUBLIC_PRINCIPAL_ID
        )
        fs_fixture.put_u32(image, fs_extract.BSIZE + 60, policy_checksum)
    else:
        policy_checksum = fs_extract.legacy_storage_policy_checksum(
            policy_version,
            fs_extract.FS_WORKFLOW_SCOPE_SLOTS,
            workflow_blocks,
            workflow_inodes,
            system_blocks,
            system_inodes,
        )
        fs_fixture.put_u32(
            image, fs_extract.BSIZE + 32, fs_extract.FS_PUBLIC_PRINCIPAL_ID
        )
        fs_fixture.put_u32(image, fs_extract.BSIZE + 56, policy_checksum)

    directory_block = max(data_start, 20)
    next_block = directory_block + 1
    directory = bytearray(fs_extract.BSIZE)
    dynamic_scope = fs_extract.VFS_SCOPE_FIRST_DYNAMIC
    for slot, (full_name, data) in enumerate(sorted(state_files.items())):
        inum = slot + 2
        fs_fixture.put_dirent(
            directory, slot, inum, _state_short_name(repo_dir, full_name)
        )
        data_blocks: list[int] = []
        for offset in range(0, len(data), fs_extract.BSIZE):
            if next_block >= block_count:
                raise AssertionError("state fixture exceeds its filesystem image")
            data_blocks.append(next_block)
            chunk = data[offset : offset + fs_extract.BSIZE]
            start = next_block * fs_extract.BSIZE
            image[start : start + len(chunk)] = chunk
            next_block += 1
        inode_addrs = data_blocks[: fs_extract.NDIRECT]
        if len(data_blocks) > fs_extract.NDIRECT:
            if len(data_blocks) > fs_extract.NDIRECT + fs_extract.NINDIRECT:
                raise AssertionError("state fixture exceeds single-indirect capacity")
            indirect_block = next_block
            next_block += 1
            for index, block_number in enumerate(data_blocks[fs_extract.NDIRECT :]):
                fs_fixture.put_u32(
                    image,
                    indirect_block * fs_extract.BSIZE + index * 4,
                    block_number,
                )
            inode_addrs.append(indirect_block)
        fs_fixture.put_inode(
            image,
            inum,
            fs_extract.T_FILE,
            len(data),
            inode_addrs,
            dinode_size,
            magic,
        )
        if agentos:
            fs_fixture.put_vfs_label(
                image,
                inum,
                fs_extract.T_FILE,
                fs_extract.VFS_POLICY_WORKFLOW,
                dinode_size,
                magic,
                dynamic_scope,
            )

    start = directory_block * fs_extract.BSIZE
    image[start : start + fs_extract.BSIZE] = directory
    fs_fixture.put_inode(
        image, 1, 1, fs_extract.BSIZE, [directory_block], dinode_size, magic
    )
    if agentos:
        fs_fixture.put_vfs_label(
            image, 1, 1, fs_extract.VFS_POLICY_ROOT, dinode_size, magic
        )
    image_path.write_bytes(image)


def _bind_target_state_image(boot_dir: Path, target: str) -> None:
    target_dir = boot_dir / target
    state_dir = target_dir / "state-extracted"
    state_files = {
        path.name: path.read_bytes()
        for path in state_dir.iterdir()
        if path.name != "extract-summary.json"
    }
    repo_dir = ROOT / "baseline_ucore" if target == "plain" else ROOT
    image_path = target_dir / "artifacts" / "image_final"
    _build_state_image(
        image_path, repo_dir, state_files, agentos=target == "agentos"
    )
    summary = {
        "image": f"diagnostic/{target}/artifacts/image_final",
        "scanned_rp_entries": len(state_files),
        "extracted_state_files": len(state_files),
        "skipped_binary_entries": 0,
        "available_scope_ids": (
            [fs_extract.VFS_SCOPE_FIRST_DYNAMIC] if target == "agentos" else []
        ),
        "selected_scope_id": (
            fs_extract.VFS_SCOPE_FIRST_DYNAMIC if target == "agentos" else None
        ),
        "scope_layout": "selected" if target == "agentos" else "legacy",
        "files": sorted(state_files),
        "status": "ready",
    }
    write_strict(state_dir / "extract-summary.json", summary)

    run_summary_path = target_dir / "ucore-run-summary.json"
    run_summary = json.loads(run_summary_path.read_text(encoding="utf-8"))
    run_summary["runtime_artifacts"]["image_final"] = {
        "path": "artifacts/image_final",
        "bytes": image_path.stat().st_size,
        "sha256": digest(image_path),
    }
    write_strict(run_summary_path, run_summary)


def add_formal_scenario(
    run: Path,
    *,
    commit: str = COMMIT,
    agentos_advantage_ms: int = 5,
    source_tree: Path = ROOT,
) -> None:
    scenario_root = run / "scenario"
    raw_root = scenario_root / "raw"
    programs, roles = read_expected_programs()
    boot_dirs = []
    order_codes = []
    for number in range(1, 8):
        boot_id = f"boot-{number:02d}"
        boot_dir = raw_root / boot_id
        challenge = f"ch-{number:012d}"
        order = "AB" if number % 2 else "BA"
        _write_target(
            boot_dir, "plain", programs, roles, number, order, challenge,
            agentos_advantage_ms,
        )
        _write_target(
            boot_dir, "agentos", programs, roles, number, order, challenge,
            agentos_advantage_ms,
        )
        for target in ("plain", "agentos"):
            state_dir = boot_dir / target / "state-extracted"
            assert (state_dir / "rp_task6_raw").is_file()
            assert (state_dir / "rp_task6_norm").is_file()
        if commit != COMMIT:
            for target in ("plain", "agentos"):
                summary_path = boot_dir / target / "ucore-run-summary.json"
                target_summary = json.loads(summary_path.read_text(encoding="utf-8"))
                target_summary["source_commit"] = commit
                write_strict(summary_path, target_summary)
        _bind_target_state_image(boot_dir, "plain")
        _bind_target_state_image(boot_dir, "agentos")
        (boot_dir / "runner.log").write_text(f"scenario runner {boot_id}\n", encoding="utf-8")
        write_strict(boot_dir / "host-summary.json", {
            "status": "ready", "challenge": challenge,
            "target_order": "plain-agentos" if number % 2 else "agentos-plain",
        })
        boot_dirs.append(boot_dir)
        order_codes.append(order)
    report = collect_scenario(
        boot_dirs,
        source_commit=commit,
        run_id="contract-test",
        target_orders=order_codes,
        source_tree=source_tree,
    )
    assert report["summary"]["functional_acceptance"]["status"] == "passed"
    # Exercise the collector's real persistence format.  Resource Stability v3
    # deliberately validates the ordered nested receipt schema, while the
    # generic fixture writer sorts object keys and therefore cannot faithfully
    # replay a production scenario report.
    scenario_evidence._write_report(scenario_root / "report.json", report)

    micro = json.loads((run / "campaign.json").read_text(encoding="utf-8"))
    execution = _scenario_environment(
        micro["platform"]["tools"]["host_cc"]["path"]
    )
    python_bin = micro["environment"]["python"]["path"]
    driver_path = str(
        (Path(__file__).resolve().parent / "check_seeded_action_state.py").resolve()
    )
    toolprefix = execution["tools"]["compiler"]["path"][:-3]
    protocol = {
        "collector_path": "host_tools/evaluation_scenario.py",
        "collector_sha256": "6" * 64,
        "execution_environment": execution,
        "git_bin": micro["environment"]["git"]["path"],
        "git_sha256": micro["environment"]["git"]["sha256"],
        "input_driver_path": "host_tools/check_seeded_action_state.py",
        "input_driver_sha256": "7" * 64,
        "minimum_boots": 7,
        "python_bin": python_bin,
        "python_sha256": micro["environment"]["python"]["sha256"],
        "requested_boots": 7,
        "timeout_seconds": 600,
        "toolprefix": toolprefix,
        "wsl_distro": "Ubuntu",
    }
    boots = []
    prefix = "results/evaluation/runs/contract-test/scenario"
    for number, boot_dir in enumerate(boot_dirs, 1):
        boot_id = f"boot-{number:02d}"
        challenge = f"ch-{number:012d}"
        target_order = "plain-agentos" if number % 2 else "agentos-plain"
        work_dir = f"{prefix}/raw/{boot_id}"
        host_summary = f"{work_dir}/host-summary.json"
        boots.append({
            "boot_id": boot_id,
            "challenge": challenge,
            "command_argv": [
                python_bin, driver_path, "--work-dir", work_dir,
                "--timeout", "600", "--wsl-distro", "Ubuntu", "--target-order",
                target_order, "--challenge", challenge, "--json-out", host_summary,
            ],
            "command_environment": _scenario_boot_environment(
                {
                    "python": micro["environment"]["python"],
                    "git": micro["environment"]["git"],
                },
                execution,
            ),
            "exit_code": 0,
            "finished_at_utc": f"2026-07-30T00:02:{number:02d}Z",
            "host_summary": host_summary,
            "host_summary_sha256": digest(boot_dir / "host-summary.json"),
            "runner_log": f"{work_dir}/runner.log",
            "runner_log_sha256": digest(boot_dir / "runner.log"),
            "status": "passed",
            "target_order": target_order,
            "work_dir": work_dir,
        })
    scenario_plan = {
        "boots": boots,
        "kind": "agentos-evaluation-scenario-campaign",
        "measurement_source_receipt": micro["measurement_source_receipt"],
        "phase": "collected",
        "platform": micro["platform"],
        "protocol": protocol,
        "report": {
            "path": f"{prefix}/report.json",
            "sha256": digest(scenario_root / "report.json"),
            "status": "recorded",
        },
        "run": {
            "artifact_root": micro["run"]["artifact_root"],
            "commit": commit,
            "environment_sha256": bundle._environment_sha256(micro),
            "id": "contract-test",
            "platform_sha256": _canonical_sha256(micro["platform"]),
            "scenario_environment_sha256": hashlib.sha256(
                bundle._canonical_bytes(execution)
            ).hexdigest(),
        },
        "schema_version": SCENARIO_SCHEMA_VERSION,
    }
    validate_scenario_campaign(scenario_plan)
    write_strict(scenario_root / "scenario-plan.json", scenario_plan)
    (scenario_root / "collector.log").write_text("collector passed\n", encoding="utf-8")
    (run / "scenario-preflight.log").write_text(
        format_preflight_receipt(scenario_plan), encoding="ascii", newline="\n"
    )
    summary, rows = build(
        SUITE_PATH, run / "run-plan.json", run / "raw",
        scenario_root / "report.json", scenario_root / "scenario-plan.json",
        contract_root=ROOT,
    )
    write_json(run / "summary.json", summary)
    write_jsonl(run / "metrics.jsonl", rows)
    write_kernel_cost_sidecar(
        run, run_id="contract-test", source_commit=commit
    )
    shutil.rmtree(run / "dashboard")
    render(run / "summary.json", run / "dashboard", contract_root=ROOT)


def assert_image_replay_rejects_self_consistent_state_forgery(
    run: Path, micro_campaign: dict[str, object]
) -> None:
    state_path = run / "scenario/raw/boot-01/plain/state-extracted/rp_runner"
    image_path = run / "scenario/raw/boot-01/plain/artifacts/image_final"
    report_path = run / "scenario/report.json"
    plan_path = run / "scenario/scenario-plan.json"
    original_state = state_path.read_bytes()
    original_report = report_path.read_bytes()
    original_plan = plan_path.read_bytes()
    original_image_sha256 = digest(image_path)
    forged_state = b"runner=self-consistent-forgery\nstatus=ready\n"

    try:
        state_path.write_bytes(forged_state)
        report = json.loads(original_report.decode("utf-8"))
        sample = next(
            item
            for item in report["samples"]
            if item["binding"]["boot_id"] == "boot-01"
        )
        receipt = sample["targets"]["plain"]["raw_source_receipt"]
        state_inventory = receipt["state_inventory"]
        state_entry = next(
            item for item in state_inventory["files"] if item["path"] == "rp_runner"
        )
        state_entry["bytes"] = len(forged_state)
        state_entry["sha256"] = hashlib.sha256(forged_state).hexdigest()
        state_body = {
            "schema": state_inventory["schema"],
            "files": state_inventory["files"],
        }
        state_inventory["sha256"] = scenario_evidence._binding_sha256(
            state_body, "scenario-state-inventory-v1"
        )

        sealed = receipt["sealed_inventory"]
        sealed_entry = next(
            item
            for item in sealed["files"]
            if item["path"] == "state-extracted/rp_runner"
        )
        sealed_entry["bytes"] = len(forged_state)
        sealed_entry["sha256"] = hashlib.sha256(forged_state).hexdigest()
        sealed_body = {
            "schema": sealed["schema"],
            "files": sealed["files"],
            "file_count": sealed["file_count"],
        }
        sealed["sha256"] = scenario_evidence._binding_sha256(
            sealed_body, "scenario-sealed-inventory-v1"
        )

        receipt.pop("sha256")
        receipt["sha256"] = scenario_evidence._binding_sha256(
            receipt, "scenario-raw-source-receipt-v1"
        )
        binding = sample["binding"]
        binding["source_receipts"]["plain"] = receipt["sha256"]
        binding.pop("sha256")
        binding["sha256"] = scenario_evidence._binding_sha256(
            binding, "scenario-sample-v1"
        )
        report.pop("report_sha256")
        report["report_sha256"] = scenario_evidence._binding_sha256(
            report, "scenario-report-v2"
        )
        scenario_evidence._write_report(report_path, report)

        plan = json.loads(original_plan.decode("utf-8"))
        plan["report"]["sha256"] = digest(report_path)
        write_strict(plan_path, plan)
        assert digest(image_path) == original_image_sha256
        expect_rejected(
            lambda: bundle._verify_scenario_campaign(
                run,
                micro_campaign,
                profile="formal",
                source_tree=ROOT,
                trusted_contract_root=ROOT,
            ),
            "final filesystem state bytes differ",
        )
    finally:
        state_path.write_bytes(original_state)
        report_path.write_bytes(original_report)
        plan_path.write_bytes(original_plan)
    assert digest(image_path) == original_image_sha256


def assert_image_replay_rejects_summary_type_confusion(run: Path) -> None:
    summary_path = (
        run
        / "scenario/raw/boot-01/plain/state-extracted/extract-summary.json"
    )
    original = summary_path.read_bytes()
    try:
        summary = json.loads(original.decode("utf-8"))
        summary["scanned_rp_entries"] = float(summary["scanned_rp_entries"])
        write_strict(summary_path, summary)
        expect_rejected(
            lambda: bundle._verify_scenario_image_state(
                run,
                "boot-01",
                "plain",
                host_state_names=HOST_STATE_NAMES,
            ),
            "extract summary differs",
        )
    finally:
        summary_path.write_bytes(original)


def assert_image_budget_precedes_extraction(
    run: Path, micro_campaign: dict[str, object]
) -> None:
    image = run / "scenario/raw/boot-01/plain/artifacts/image_final"
    original_limit = bundle.MAX_ARCHIVE_MEMBER_BYTES
    original_extractor = bundle.extract_state_files

    def forbidden_extractor(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("extractor ran before the image budget check")

    try:
        bundle.MAX_ARCHIVE_MEMBER_BYTES = image.stat().st_size - 1
        bundle.extract_state_files = forbidden_extractor
        expect_rejected(
            lambda: bundle._verify_scenario_campaign(
                run,
                micro_campaign,
                profile="formal",
                source_tree=ROOT,
                trusted_contract_root=ROOT,
            ),
            "exceeds its budget",
        )
    finally:
        bundle.MAX_ARCHIVE_MEMBER_BYTES = original_limit
        bundle.extract_state_files = original_extractor


def assert_committed_contract_root_gate(root: Path) -> None:
    repo = root / "contract-root-gate-repo"
    foreign_contract = root / "contract-root-gate-foreign"
    run = root / "contract-root-gate-run"
    repo.mkdir()
    foreign_contract.mkdir()
    run.mkdir()
    suite = root / "contract-root-gate-suite.json"
    suite.write_text("{}\n", encoding="ascii", newline="\n")
    sentinel = root / "foreign-contract-semantics-executed"
    calls: list[str] = []

    def forbidden(label: str):
        def execute(*_args: object, **_kwargs: object) -> object:
            calls.append(label)
            sentinel.write_text("executed\n", encoding="ascii")
            raise AssertionError(f"committed {label} ran with a foreign contract root")

        return execute

    original_micro_verifier = bundle._verify_micro_campaign
    original_preauthenticator = bundle._preauthenticate_committed_bundle
    original_portable_verifier = bundle.verify_bundle
    try:
        bundle._verify_micro_campaign = forbidden("create semantics")
        expect_rejected(
            lambda: bundle.create_bundle(
                run_dir=run,
                suite_path=suite,
                output=repo / "evidence" / "releases" / "foreign-create",
                contract_root=foreign_contract,
                repo_root=repo,
            ),
            "same canonical directory",
        )
        bundle._preauthenticate_committed_bundle = forbidden("Git preauthentication")
        bundle.verify_bundle = forbidden("verify semantics")
        expect_rejected(
            lambda: bundle.verify_committed_bundle(
                root / "untrusted-bundle", repo, contract_root=foreign_contract
            ),
            "same canonical directory",
        )
    finally:
        bundle._verify_micro_campaign = original_micro_verifier
        bundle._preauthenticate_committed_bundle = original_preauthenticator
        bundle.verify_bundle = original_portable_verifier
    assert calls == []
    assert not sentinel.exists()


def assert_committed_delivery_roundtrip(
    root: Path, image_verifier_calls: list[tuple[str, str]]
) -> None:
    repo = root / "delivery-repo"
    repo.mkdir()
    git(repo, "init", "-q")
    assert_isolated_fixture_repository(repo)
    git(repo, "config", "user.email", "evaluation@example.invalid")
    git(repo, "config", "user.name", "Evaluation Test")
    git(repo, "config", "core.autocrlf", "true")
    shutil.copyfile(ROOT / ".gitignore", repo / ".gitignore")
    shutil.copyfile(ROOT / ".gitattributes", repo / ".gitattributes")
    index = repo / "evidence" / "releases" / "INDEX.md"
    index.parent.mkdir(parents=True)
    index.write_text(
        "# Final Evidence Releases\n\n"
        "Release records below are append-only and are validated against the containing Git commit.\n",
        encoding="ascii",
        newline="\n",
    )
    (repo / "source.txt").write_text("source C\n", encoding="ascii")
    source_inventory = build_measurement_source_receipt(
        ROOT, source_commit="0" * 40
    )["sources"]
    for record in source_inventory:
        destination = repo / record["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / record["path"], destination)
    normalization_fixture = repo / "baseline_ucore" / "user" / "src" / "rp_plain.c"
    normalization_source = normalization_fixture.read_bytes().replace(b"\r\n", b"\n")
    assert b"\n" in normalization_source
    normalization_fixture.write_bytes(normalization_source.replace(b"\n", b"\r\n"))
    alternate_contract = repo / "host_tools" / "full_verification_metrics_render.py"
    alternate_contract.write_text(
        alternate_contract.read_text(encoding="utf-8")
        + "\n# Alternate trusted contract root fixture.\n",
        encoding="utf-8",
        newline="\n",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "source")
    source = git(repo, "rev-parse", "HEAD").stdout.strip()
    normalized_blob = bundle._git_capture(
        repo,
        "cat-file",
        "blob",
        f"{source}:baseline_ucore/user/src/rp_plain.c",
    )
    assert b"\r\n" not in normalized_blob
    assert normalization_fixture.read_bytes() != normalized_blob
    materialize_committed_measurement_sources(repo, source, source_inventory)
    assert normalization_fixture.read_bytes() == normalized_blob

    formal_run = make_run(
        root / "delivery-run", commit=source, measurement_source_root=repo
    )
    add_formal_scenario(formal_run, commit=source, source_tree=repo)
    make_full_verification_payload(formal_run / "full-verification", commit=source)
    output = repo / "evidence" / "releases" / "evaluation-contract-test"
    manifest = bundle.create_bundle(
        run_dir=formal_run,
        suite_path=SUITE_PATH,
        output=output,
        contract_root=repo,
        repo_root=repo,
    )
    packaged_contract = (
        output / "run" / bundle.MEASUREMENT_SOURCE_SNAPSHOT_ROOT
        / "host_tools" / "full_verification_metrics_render.py"
    )
    assert packaged_contract.read_bytes() == alternate_contract.read_bytes()
    assert packaged_contract.read_bytes() != (
        ROOT / "host_tools" / "full_verification_metrics_render.py"
    ).read_bytes()
    assert manifest["delivery"]["release"] == {
        "name": "evaluation-contract-test",
        "path": "evidence/releases/evaluation-contract-test",
    }
    required_log = output / "run" / "archives" / "micro" / "boot-01.tar.gz"
    ignored_elsewhere = repo / "outside.log"
    assert git(
        repo,
        "check-ignore",
        "--no-index",
        required_log.relative_to(repo).as_posix(),
        check=False,
    ).returncode == 1
    assert git(
        repo,
        "check-ignore",
        "--no-index",
        ignored_elsewhere.relative_to(repo).as_posix(),
        check=False,
    ).returncode == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "evidence")
    evidence = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert required_log.relative_to(repo).as_posix() in git(
        repo, "ls-tree", "-r", "--name-only", "HEAD"
    ).stdout.splitlines()
    verified = bundle.verify_committed_bundle(
        output, repo, contract_root=repo
    )
    assert verified == manifest

    documentation = repo / "docs" / "delivery.md"
    documentation.parent.mkdir()
    documentation.write_text("delivery notes\n", encoding="ascii")
    git(repo, "add", "docs/delivery.md")
    git(repo, "commit", "-q", "-m", "docs")
    assert git(repo, "rev-parse", "HEAD^").stdout.strip() == evidence
    assert bundle.verify_committed_bundle(
        output, repo, contract_root=repo
    ) == manifest

    clone = root / "clean-clone"
    git(root, "clone", "-q", str(repo), str(clone))
    shutil.rmtree(formal_run)
    clone_output = clone / "evidence" / "releases" / "evaluation-contract-test"
    assert not (clone_output / "run" / "raw" / "boot-01" / "guest.log").exists()
    assert (clone_output / "run" / "archives" / "micro" / "boot-01.tar.gz").is_file()
    archived_paths = {
        member["path"]
        for archive in manifest["archives"]
        for member in archive["members"]
    }
    assert "run/raw/boot-01/guest.log" in archived_paths
    assert "run/measurement-source-receipt.json" not in archived_paths
    assert (
        clone_output / "run" / "measurement-source-receipt.json"
    ).is_file()
    assert any(
        item["path"] == "run/measurement-source-receipt.json"
        for item in manifest["files"]
    )
    source_receipts = [
        item
        for item in manifest["artifacts"]
        if item["artifact_id"] == "campaign/measurement-source-receipt"
    ]
    assert len(source_receipts) == 1
    assert source_receipts[0]["path"] == "run/measurement-source-receipt.json"
    receipt = json.loads(
        (clone_output / "run" / "measurement-source-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    for record in receipt["sources"]:
        snapshot_relative = f"run/measurement-sources/{record['path']}"
        snapshot = clone_output.joinpath(*snapshot_relative.split("/"))
        assert snapshot.is_file()
        assert snapshot.stat().st_size == record["bytes"]
        assert digest(snapshot) == record["sha256"]
        assert snapshot_relative not in archived_paths
        assert any(
            item["path"] == snapshot_relative for item in manifest["files"]
        )
    assert (
        clone_output / "run" / "dashboard" / "evidence" / "raw" / "boot-01" / "guest.log"
    ).is_file()
    assert_clean_clone_dashboard_links(
        clone_output / "run" / "dashboard" / "index.html",
        clone_output / "run",
    )
    clean_clone_call_start = len(image_verifier_calls)
    assert bundle.verify_bundle(clone_output, contract_root=clone) == manifest
    assert_image_verification_delta(image_verifier_calls, clean_clone_call_start)
    assert bundle.verify_committed_bundle(
        clone_output, clone, contract_root=clone
    ) == manifest


def assert_git_source_blob_tamper_rejected(root: Path) -> None:
    repo = root / "source-blob-tamper-repo"
    repo.mkdir()
    git(repo, "init", "-q")
    assert_isolated_fixture_repository(repo)
    git(repo, "config", "user.email", "evaluation@example.invalid")
    git(repo, "config", "user.name", "Evaluation Test")
    source_inventory = build_measurement_source_receipt(
        ROOT, source_commit="0" * 40
    )["sources"]
    for record in source_inventory:
        destination = repo / record["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / record["path"], destination)
    suite_record = next(
        record
        for record in source_inventory
        if record["path"] == "ci/evaluation-suite.json"
    )
    suite_source = repo / suite_record["path"]
    suite_source.write_bytes(suite_source.read_bytes() + b"\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tampered source C")
    source_commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    receipt = build_measurement_source_receipt(
        ROOT, source_commit=source_commit
    )
    expect_rejected(
        lambda: bundle._verify_committed_measurement_sources(
            repo, source_commit, receipt
        ),
        "Git source-C size differs from receipt",
    )


def main() -> int:
    for operation in (
        bundle.create_bundle,
        bundle.verify_bundle,
        bundle.verify_committed_bundle,
    ):
        parameter = inspect.signature(operation).parameters["contract_root"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty
    runner = (ROOT / "scripts" / "run-evaluation-suite.sh").read_text(
        encoding="utf-8"
    )
    assert "evidence/releases/evaluation-${run_id,,}" in runner
    assert "evidence/evaluation-releases" not in runner
    assert "full-verify)" in runner
    assert "--expected-commit \"${commit}\"" in runner
    assert "full-verification is unavailable for development runs" in runner
    assert 'KERNEL_BUDGET_POLICY_TOOL="scripts/check-kernel-budgets.py"' in runner
    assert "--check agent-test-policy" in runner
    expected = Path("raw/boot-01/guest.log")
    canonical = "results/evaluation/runs/contract-test/raw/boot-01/guest.log"
    assert bundle._campaign_artifact_relative(
        canonical, PurePosixPath(expected.as_posix()), "micro guest log",
        artifact_root="results/evaluation/runs/contract-test",
    ) == expected.as_posix()
    assert bundle._campaign_artifact_relative(
        "custom/evaluation-output/runs/contract-test/raw/boot-01/guest.log",
        PurePosixPath(expected.as_posix()), "custom micro guest log",
        artifact_root="custom/evaluation-output/runs/contract-test",
    ) == expected.as_posix()
    for invalid in (
        "../../outside/raw/boot-01/guest.log",
        "other/prefix/raw/boot-01/guest.log",
        "results/evaluation/runs/other/raw/boot-01/guest.log",
        "C:/results/evaluation/runs/contract-test/raw/boot-01/guest.log",
        "results\\evaluation\\runs\\contract-test\\raw\\boot-01\\guest.log",
    ):
        expect_rejected(
            lambda invalid=invalid: bundle._campaign_artifact_relative(
                invalid, PurePosixPath(expected.as_posix()),
                "micro guest log",
                artifact_root="results/evaluation/runs/contract-test",
            ),
            "canonical",
        )
    expect_rejected(
        lambda: bundle._campaign_artifact_relative(
            canonical, PurePosixPath(expected.as_posix()), "micro guest log",
            artifact_root="../../outside",
        ),
        "canonical relative",
    )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        assert_committed_contract_root_gate(root)
        profile_run = root / "profile-binding"
        (profile_run / "full-verification").mkdir(parents=True)
        original_full_verifier = bundle.verify_full_verification_payload
        try:
            for campaign_profile, payload_profile in (
                ("local-e3", "none"), ("none", "local-e3")
            ):
                bundle.verify_full_verification_payload = (
                    lambda *_args, profile=payload_profile, **_kwargs: (
                        {"agent_test_duration_profile": profile}, {"receipt.json"}
                    )
                )
                expect_rejected(
                    lambda profile=campaign_profile: bundle._verify_full_verification(
                        profile_run, expected_commit=COMMIT, profile="formal",
                        expected_duration_platform={
                            "duration_profile": {"name": profile}
                        }, contract_root=ROOT,
                    ),
                    "duration profile differs from campaign",
                )
        finally:
            bundle.verify_full_verification_payload = original_full_verifier
        assert_archive_safety(root)
        assert_formal_kernel_cost_contract(root)
        development_run = make_run(
            root / "development",
            artifact_root="custom/evaluation-output/runs/contract-test",
        )
        original_campaign = json.loads(
            (development_run / "campaign.json").read_text(encoding="utf-8")
        )
        original_plan = json.loads(
            (development_run / "run-plan.json").read_text(encoding="utf-8")
        )
        micro_preflight_path = development_run / "preflight.log"
        original_micro_preflight = micro_preflight_path.read_bytes()
        forged_micro_preflight = json.loads(original_micro_preflight)
        forged_micro_preflight["binding_sha256"] = "0" * 64
        write_strict(micro_preflight_path, forged_micro_preflight)
        expect_rejected(
            lambda: bundle._verify_micro_campaign(development_run, SUITE_PATH),
            "preflight receipt differs from its campaign",
        )
        micro_preflight_path.write_bytes(
            b"x" * (bundle.PREFLIGHT_RECEIPT_MAX_BYTES + 1)
        )
        expect_rejected(
            lambda: bundle._verify_micro_campaign(development_run, SUITE_PATH),
            "preflight receipt is invalid",
        )
        micro_preflight_path.write_bytes(original_micro_preflight)
        forged_sample_count = json.loads(json.dumps(original_campaign))
        forged_sample_count["protocol"]["expected_samples_per_boot"] -= 1
        for boot in forged_sample_count["boots"]:
            boot["sample_count"] -= 1
        write_strict(
            development_run / "campaign.json",
            forged_sample_count,
        )
        micro_preflight_path.write_text(
            format_preflight_receipt(forged_sample_count),
            encoding="ascii",
            newline="\n",
        )
        expect_rejected(
            lambda: bundle._verify_micro_campaign(development_run, SUITE_PATH),
            "sample count differs from the packaged suite",
        )
        write_strict(
            development_run / "campaign.json",
            original_campaign,
        )
        micro_preflight_path.write_bytes(original_micro_preflight)
        swapped_suite_value = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
        swapped_suite_value["experiments"][0]["claim_gate"][
            "minimum_relative_improvement_percent"
        ] = 1
        swapped_suite = root / "swapped-suite.json"
        write_strict(swapped_suite, swapped_suite_value)
        swapped_sha256 = digest(swapped_suite)
        forged_campaign = json.loads(json.dumps(original_campaign))
        forged_campaign["protocol"]["suite_sha256"] = swapped_sha256
        forged_plan = json.loads(json.dumps(original_plan))
        forged_plan["suite_sha256"] = swapped_sha256
        forged_campaign_path = root / "forged-campaign.json"
        write_strict(forged_campaign_path, forged_campaign)
        forged_plan["campaign_sha256"] = digest(forged_campaign_path)
        expect_rejected(
            lambda: bundle._verify_measurement_source_receipt(
                development_run,
                forged_campaign,
                forged_plan,
                COMMIT,
                source_tree=ROOT,
                suite_path=swapped_suite,
            ),
            "suite differs from the versioned source policy inventory",
        )
        receipt_path = development_run / "measurement-source-receipt.json"
        hidden_receipt = development_run / "measurement-source-receipt.hidden"
        receipt_path.rename(hidden_receipt)
        expect_rejected(
            lambda: bundle.create_bundle(
                run_dir=development_run,
                suite_path=SUITE_PATH,
                output=root / "missing-source-receipt",
                contract_root=ROOT,
                profile="development",
            ),
            "measurement source receipt is missing or unsafe",
        )
        hidden_receipt.rename(receipt_path)
        source_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        source_receipt["sources"][0]["sha256"] = "0" * 64
        write_strict(receipt_path, source_receipt)
        expect_rejected(
            lambda: bundle.create_bundle(
                run_dir=development_run,
                suite_path=SUITE_PATH,
                output=root / "mismatched-source-receipt",
                contract_root=ROOT,
                profile="development",
            ),
            "differs from the campaign or run plan",
        )
        campaign = json.loads(
            (development_run / "campaign.json").read_text(encoding="utf-8")
        )
        write_strict(receipt_path, campaign["measurement_source_receipt"])
        forged_campaign = json.loads(json.dumps(campaign))
        forged_receipt = forged_campaign["measurement_source_receipt"]
        forged_receipt["sources"][0]["sha256"] = "0" * 64
        forged_plan = json.loads(
            (development_run / "run-plan.json").read_text(encoding="utf-8")
        )
        forged_plan["measurement_source_receipt"] = forged_receipt
        write_strict(receipt_path, forged_receipt)
        expect_rejected(
            lambda: bundle._verify_measurement_source_receipt(
                development_run,
                forged_campaign,
                forged_plan,
                COMMIT,
                source_tree=ROOT,
                suite_path=SUITE_PATH,
            ),
            "snapshot cannot replay its contracts",
        )
        write_strict(receipt_path, campaign["measurement_source_receipt"])
        expect_rejected(
            lambda: bundle.create_bundle(
                run_dir=development_run, suite_path=SUITE_PATH,
                output=root / "formal-missing-scenario",
                contract_root=ROOT,
            ),
            "formal evidence requires",
        )
        development = root / "development-bundle"
        manifest = bundle.create_bundle(
            run_dir=development_run, suite_path=SUITE_PATH,
            output=development, contract_root=ROOT, profile="development",
        )
        assert manifest["profile"] == {
            "name": "development", "formal": False,
            "warning": bundle.DEVELOPMENT_WARNING,
        }
        assert manifest["delivery"] is None
        assert manifest["full_verification"] == bundle.DEVELOPMENT_FULL_VERIFICATION
        assert manifest["archive_summary"]["archive_count"] == 7
        assert not (development / "run" / "raw").exists()
        assert bundle.verify_bundle(
            development, contract_root=ROOT
        ) == manifest
        assert_packaged_contract_code_is_never_executed(development, root)
        bundle_manifest_path = development / "manifest.json"
        original_bundle_manifest = bundle_manifest_path.read_bytes()
        for invalid_schema in (5, 6.0, True, "6"):
            forged_bundle_manifest = json.loads(original_bundle_manifest)
            forged_bundle_manifest["schema_version"] = invalid_schema
            write_strict(bundle_manifest_path, forged_bundle_manifest)
            expect_rejected(
                lambda: bundle.verify_bundle(development, contract_root=ROOT),
                "bundle manifest schema is unsupported",
            )
        bundle_manifest_path.write_bytes(original_bundle_manifest)
        make_full_verification_payload(
            development_run / "full-verification", commit=COMMIT
        )
        expect_rejected(
            lambda: bundle.create_bundle(
                run_dir=development_run,
                suite_path=SUITE_PATH,
                output=root / "development-with-full-verification",
                contract_root=ROOT,
                profile="development",
            ),
            "must declare full-verification unavailable",
        )
        shutil.rmtree(development_run / "full-verification")
        missing_snapshot = next(
            development.glob("run/measurement-sources/**/*.*")
        )
        missing_snapshot.unlink()
        expect_rejected(
            lambda: bundle.verify_bundle(development, contract_root=ROOT),
            "bundle inventory differs",
        )

        extra = development_run / "raw" / "boot-01" / "extra.log"
        extra.write_text("unplanned\n", encoding="utf-8")
        failed_output = root / "must-not-publish"
        expect_rejected(
            lambda: bundle.create_bundle(
                run_dir=development_run, suite_path=SUITE_PATH,
                output=failed_output, contract_root=ROOT, profile="development",
            ),
            "raw inventory differs",
        )
        assert not failed_output.exists()
        extra.unlink()
        guest = development_run / "raw" / "boot-01" / "guest.log"
        guest.write_bytes(guest.read_bytes() + b"tamper\n")
        expect_rejected(
            lambda: bundle.create_bundle(
                run_dir=development_run, suite_path=SUITE_PATH,
                output=failed_output, contract_root=ROOT, profile="development",
            ),
            "hash differs",
        )

        formal_run = make_run(root / "formal")
        add_formal_scenario(formal_run, agentos_advantage_ms=-5)
        formal_summary = json.loads(
            (formal_run / "summary.json").read_text(encoding="utf-8")
        )
        formal_task6 = next(
            item for item in formal_summary["scenarios"] if item["task"] == "task6"
        )
        assert formal_task6["performance_status"] == "regressed"
        assert formal_task6["performance"]["sign_test"]["losses"] == 7
        assert formal_task6["performance"]["regression_mcid_sign_test"]["losses"] == 7
        assert formal_summary["acceptance"]["scientific_evidence"]["status"] == "publishable"
        assert formal_summary["acceptance"]["competition_ready"]
        assert formal_summary["acceptance"]["tasks"]["task6"] == "pass"
        bundle._verify_formal_summary(formal_summary)
        for retired_version in (2, 3, 4):
            retired_summary = copy.deepcopy(formal_summary)
            retired_summary["schema_version"] = retired_version
            retired_summary["run"]["suite_id"] = (
                f"agentos-evaluation-v{retired_version}"
            )
            expect_rejected(
                lambda: bundle._verify_formal_summary(retired_summary),
                "acceptance policy binding is invalid",
            )
        missing_task6_performance = copy.deepcopy(formal_summary)
        missing_task6 = next(
            item for item in missing_task6_performance["scenarios"]
            if item["task"] == "task6"
        )
        missing_task6["performance_status"] = "unavailable"
        missing_task6["performance"] = None
        expect_rejected(
            lambda: bundle._verify_formal_summary(missing_task6_performance),
            "measured Task 6 performance conclusion",
        )
        expect_rejected(
            lambda: bundle.create_bundle(
                run_dir=formal_run,
                suite_path=SUITE_PATH,
                output=root / "formal-without-full-verification",
                contract_root=ROOT,
            ),
            "requires a full-verification payload",
        )
        make_full_verification_payload(
            formal_run / "full-verification", commit=COMMIT
        )
        original_full_verifier = bundle.verify_full_verification_payload
        semantic_contract_roots: list[Path] = []

        def structural_full_verifier(
            root: Path, *, expected_commit: str | None = None, contract_root: Path
        ) -> tuple[dict[str, object], set[str]]:
            replay = full_verification_contract._replay_semantics
            def structural_replay(
                _raw: Path, _summary: Path, _commit: str, source_root: Path
            ) -> None:
                resolved = source_root.resolve(strict=True)
                assert (resolved / "scripts/capture-final-evidence.py").is_file()
                semantic_contract_roots.append(resolved)

            full_verification_contract._replay_semantics = structural_replay
            try:
                return full_verification_contract.verify_payload(
                    root,
                    expected_commit=expected_commit,
                    contract_root=contract_root,
                )
            finally:
                full_verification_contract._replay_semantics = replay

        bundle.verify_full_verification_payload = structural_full_verifier
        micro_campaign = json.loads(
            (formal_run / "campaign.json").read_text(encoding="utf-8")
        )
        scenario_plan_path = formal_run / "scenario/scenario-plan.json"
        scenario_plan = json.loads(scenario_plan_path.read_text(encoding="utf-8"))
        scenario_preflight_path = formal_run / "scenario-preflight.log"
        original_scenario_preflight = scenario_preflight_path.read_bytes()
        forged_scenario_preflight = json.loads(original_scenario_preflight)
        forged_scenario_preflight["binding_sha256"] = "0" * 64
        write_strict(scenario_preflight_path, forged_scenario_preflight)
        expect_rejected(
            lambda: bundle._verify_scenario_campaign(
                formal_run,
                micro_campaign,
                profile="formal",
                source_tree=ROOT,
                trusted_contract_root=ROOT,
            ),
            "preflight receipt differs from its campaign",
        )
        scenario_preflight_path.write_bytes(original_scenario_preflight)
        forged_scenario = json.loads(json.dumps(scenario_plan))
        forged_scenario["measurement_source_receipt"]["sources"][0][
            "sha256"
        ] = "0" * 64
        write_strict(scenario_plan_path, forged_scenario)
        scenario_preflight_path.write_text(
            format_preflight_receipt(forged_scenario),
            encoding="ascii",
            newline="\n",
        )
        expect_rejected(
            lambda: bundle._verify_scenario_campaign(
                formal_run,
                micro_campaign,
                profile="formal",
                source_tree=ROOT,
                trusted_contract_root=ROOT,
            ),
            "identity differs from the micro campaign",
        )
        forged_scenario = json.loads(json.dumps(scenario_plan))
        forged_scenario["platform"]["hardware"]["cpu_model"] += " alternate"
        forged_scenario["run"]["platform_sha256"] = _canonical_sha256(
            forged_scenario["platform"]
        )
        write_strict(scenario_plan_path, forged_scenario)
        scenario_preflight_path.write_text(
            format_preflight_receipt(forged_scenario),
            encoding="ascii",
            newline="\n",
        )
        expect_rejected(
            lambda: bundle._verify_scenario_campaign(
                formal_run,
                micro_campaign,
                profile="formal",
                source_tree=ROOT,
                trusted_contract_root=ROOT,
            ),
            "identity differs from the micro campaign",
        )
        write_strict(scenario_plan_path, scenario_plan)
        scenario_preflight_path.write_bytes(original_scenario_preflight)
        # Exercise the real extractor for both filesystem layouts once.  The
        # mutation case below then proves the image, rather than self-consistent
        # sidecar hashes, is authoritative.  Remaining bundle tests replace the
        # expensive image walk with a call recorder; they test unrelated archive
        # and delivery contracts and would otherwise re-read dozens of images.
        bundle._verify_scenario_image_state(
            formal_run,
            "boot-01",
            "agentos",
            host_state_names=HOST_STATE_NAMES,
        )
        assert_image_replay_rejects_self_consistent_state_forgery(
            formal_run, micro_campaign
        )
        assert_image_replay_rejects_summary_type_confusion(formal_run)
        assert_image_budget_precedes_extraction(formal_run, micro_campaign)
        image_verifier = bundle._verify_scenario_image_state
        image_verifier_calls: list[tuple[str, str]] = []

        def record_image_verification(
            _run_root: Path,
            boot_id: str,
            target: str,
            *,
            host_state_names: set[str],
        ) -> None:
            assert host_state_names == HOST_STATE_NAMES
            image_verifier_calls.append((boot_id, target))

        bundle._verify_scenario_image_state = record_image_verification
        source_snapshot = root / "scenario-source-snapshot"
        source_receipt = json.loads(
            (formal_run / "measurement-source-receipt.json").read_text(
                encoding="utf-8"
            )
        )
        for source_record in source_receipt["sources"]:
            relative = Path(source_record["path"])
            destination = source_snapshot / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)

        original_payload_builder = scenario_evidence.task6_artifact_payloads
        snapshot_corpus_calls: list[Path] = []

        def snapshot_payload_builder(
            challenge: str,
            fixture_path: Path,
        ) -> tuple[bytes, bytes]:
            resolved = fixture_path.resolve(strict=True)
            resolved.relative_to(source_snapshot.resolve(strict=True))
            snapshot_corpus_calls.append(resolved)
            return original_payload_builder(challenge, fixture_path=fixture_path)

        scenario_evidence.task6_artifact_payloads = snapshot_payload_builder
        bundle._verify_scenario_campaign(
            formal_run,
            micro_campaign,
            profile="formal",
            source_tree=source_snapshot,
            trusted_contract_root=ROOT,
        )
        scenario_evidence.task6_artifact_payloads = original_payload_builder
        assert snapshot_corpus_calls

        corpus = (
            source_snapshot
            / "evaluation_guest"
            / "fixtures"
            / "task6-count-corpus.csv"
        )
        original_corpus = corpus.read_bytes()
        corpus.write_bytes(original_corpus.replace(b"0,37,63", b"0,38,63", 1))
        expect_rejected(
            lambda: bundle._verify_scenario_campaign(
                formal_run,
                micro_campaign,
                profile="formal",
                source_tree=source_snapshot,
                trusted_contract_root=ROOT,
            ),
            "source-C semantic replay failed",
        )
        corpus.write_bytes(original_corpus)

        manifest_relatives = (
            "user/include/rp_program_manifest.h",
            "baseline_ucore/user/include/rp_program_manifest.h",
        )
        manifests = tuple(source_snapshot / relative for relative in manifest_relatives)
        compile_contract_relative = "host_tools/functional_acceptance_compile_contract.py"
        compile_contract_path = source_snapshot / compile_contract_relative
        original_manifests = [path.read_bytes() for path in manifests]
        original_compile_contract = compile_contract_path.read_bytes()
        original_receipt = micro_campaign["measurement_source_receipt"]
        original_fingerprint = functional_compile_contract.COMPILE_CLOSURE_FINGERPRINT
        programs, _roles = read_expected_programs(source_snapshot)
        original_program = programs[0]
        mutant_program = "rp_" + "x" * (len(original_program) - 3)
        assert mutant_program != original_program and mutant_program not in programs
        needle = f'APPLY("{original_program}")'.encode("ascii")
        replacement = f'APPLY("{mutant_program}")'.encode("ascii")
        try:
            for path, original in zip(manifests, original_manifests):
                mutated = original.replace(needle, replacement, 1)
                assert mutated != original
                path.write_bytes(mutated)

            forged_fingerprint = functional_compile_contract.compile_closure_fingerprint(
                functional_compile_contract.load_compile_dependency_texts(source_snapshot)
            )
            assert forged_fingerprint != original_fingerprint
            fingerprint_bytes = original_fingerprint.encode("ascii")
            assert original_compile_contract.count(fingerprint_bytes) == 1
            compile_contract_path.write_bytes(
                original_compile_contract.replace(
                    fingerprint_bytes, forged_fingerprint.encode("ascii"), 1
                )
            )
            functional_compile_contract.COMPILE_CLOSURE_FINGERPRINT = forged_fingerprint

            forged_receipt = copy.deepcopy(original_receipt)
            changed_sources = {*manifest_relatives, compile_contract_relative}
            for record in forged_receipt["sources"]:
                if record["path"] not in changed_sources:
                    continue
                raw = (source_snapshot / record["path"]).read_bytes()
                record["bytes"] = len(raw)
                record["sha256"] = hashlib.sha256(raw).hexdigest()
                changed_sources.remove(record["path"])
            assert not changed_sources
            micro_campaign["measurement_source_receipt"] = forged_receipt
            forged_scenario_plan = copy.deepcopy(scenario_plan)
            forged_scenario_plan["measurement_source_receipt"] = forged_receipt
            write_strict(scenario_plan_path, forged_scenario_plan)
            scenario_preflight_path.write_text(
                format_preflight_receipt(forged_scenario_plan),
                encoding="ascii",
                newline="\n",
            )

            expect_rejected(
                lambda: bundle._verify_scenario_campaign(
                    formal_run,
                    micro_campaign,
                    profile="formal",
                    source_tree=source_snapshot,
                    trusted_contract_root=ROOT,
                ),
                "source-C program manifests",
            )
        finally:
            write_strict(scenario_plan_path, scenario_plan)
            scenario_preflight_path.write_bytes(original_scenario_preflight)
            micro_campaign["measurement_source_receipt"] = original_receipt
            functional_compile_contract.COMPILE_CLOSURE_FINGERPRINT = original_fingerprint
            compile_contract_path.write_bytes(original_compile_contract)
            for path, original in zip(manifests, original_manifests):
                path.write_bytes(original)

        formal = root / "formal-bundle"
        formal_manifest = bundle.create_bundle(
            run_dir=formal_run,
            suite_path=SUITE_PATH,
            output=formal,
            contract_root=ROOT,
        )
        assert formal_manifest["profile"] == {
            "name": "formal", "formal": True, "warning": None,
        }
        assert formal_manifest["delivery"]["release"] == {
            "name": "formal-bundle",
            "path": "evidence/releases/formal-bundle",
        }
        assert formal_manifest["full_verification"]["status"] == "verified"
        assert formal_manifest["full_verification"]["source_commit"] == COMMIT
        assert any(
            item["path"] == "run/full-verification/receipt.json"
            for item in formal_manifest["files"]
        )
        source_receipt = next(
            item
            for item in formal_manifest["artifacts"]
            if item["artifact_id"] == "campaign/measurement-source-receipt"
        )
        assert source_receipt["path"] == "run/measurement-source-receipt.json"
        assert any(
            item["path"] == "run/measurement-source-receipt.json"
            for item in formal_manifest["files"]
        )
        assert all(
            member["path"] != "run/measurement-source-receipt.json"
            for archive in formal_manifest["archives"]
            for member in archive["members"]
        )
        snapshot_records = [
            item
            for item in formal_manifest["files"]
            if item["path"].startswith("run/measurement-sources/")
        ]
        assert len(snapshot_records) == len(
            json.loads(
                (formal / "run/measurement-source-receipt.json").read_text(
                    encoding="utf-8"
                )
            )["sources"]
        )
        assert any(
            item["artifact_id"] == "micro/boot-01/kernel"
            for item in formal_manifest["artifacts"]
        )
        assert any(
            item["artifact_id"] == "scenario/boot-01/host-summary"
            for item in formal_manifest["artifacts"]
        )
        assert formal_manifest["archive_summary"]["archive_count"] == 28
        assert not (formal / "run" / "raw").exists()
        assert not (formal / "run" / "scenario" / "raw").exists()
        materialized_call_start = len(image_verifier_calls)
        assert bundle.verify_bundle(
            formal, contract_root=ROOT
        ) == formal_manifest
        assert_image_verification_delta(image_verifier_calls, materialized_call_start)
        unexpected_scenario = (
            formal_run / "scenario" / "raw" / "boot-01" / "plain" / "temporary.log"
        )
        unexpected_scenario.write_text("not part of the report\n", encoding="ascii")
        expect_rejected(
            lambda: bundle.create_bundle(
                run_dir=formal_run, suite_path=SUITE_PATH,
                output=root / "unknown-scenario-bundle",
                contract_root=ROOT,
            ),
            "not explicitly bound",
        )
        unexpected_scenario.unlink()
        weakened = json.loads(
            (formal_run / "summary.json").read_text(encoding="utf-8")
        )
        next(
            item for item in weakened["scenarios"] if item["task"] == "task1"
        )["functional_status"] = "unavailable"
        expect_rejected(
            lambda: bundle._verify_formal_summary(weakened),
            "Task 1-6 functional acceptance",
        )
        unsupported_task4 = json.loads(
            (formal_run / "summary.json").read_text(encoding="utf-8")
        )
        task4_benchmark_id = unsupported_task4["methodology"][
            "competition_claims"
        ]["task4"]["benchmark_id"]
        next(
            item for item in unsupported_task4["claims"]
            if item["benchmark_id"] == task4_benchmark_id
        )["status"] = "not_supported"
        unsupported_task4["acceptance"] = derive_acceptance_gates(
            unsupported_task4["scenarios"],
            unsupported_task4["claims"],
            unsupported_task4["methodology"]["competition_claims"],
        )
        bundle._verify_formal_summary(unsupported_task4)
        assert unsupported_task4["acceptance"]["scientific_evidence"]["status"] == "publishable"
        assert not unsupported_task4["acceptance"]["competition_ready"]
        assert unsupported_task4["acceptance"]["tasks"]["task4"] == "not_ready"
        next(
            item for item in unsupported_task4["claims"]
            if item["benchmark_id"] == task4_benchmark_id
        )["status"] = "unavailable"
        unsupported_task4["acceptance"] = derive_acceptance_gates(
            unsupported_task4["scenarios"],
            unsupported_task4["claims"],
            unsupported_task4["methodology"]["competition_claims"],
        )
        expect_rejected(
            lambda: bundle._verify_formal_summary(unsupported_task4),
            "requires a measured registered Task 4 claim",
        )
        unsupported_task4["claims"] = [
            item for item in unsupported_task4["claims"]
            if item["benchmark_id"] != task4_benchmark_id
        ]
        expect_rejected(
            lambda: bundle._verify_formal_summary(unsupported_task4),
            "exactly one registered Task 4 claim",
        )

        assert_git_source_blob_tamper_rejected(root)
        assert_committed_delivery_roundtrip(root, image_verifier_calls)

        link_parent = root / "linked-parent"
        try:
            os.symlink(root / "real-parent", link_parent, target_is_directory=True)
        except (OSError, NotImplementedError):
            pass
        else:
            expect_rejected(
                lambda: bundle.create_bundle(
                    run_dir=formal_run, suite_path=SUITE_PATH,
                    output=link_parent / "bundle",
                    contract_root=ROOT,
                ),
                "link-backed",
            )
        assert ("boot-01", "plain") in image_verifier_calls
        assert ("boot-01", "agentos") in image_verifier_calls
        assert semantic_contract_roots
        assert ROOT.resolve(strict=True) in semantic_contract_roots
        assert all(
            bundle.MEASUREMENT_SOURCE_SNAPSHOT_ROOT not in root.parts
            for root in semantic_contract_roots
        )
        bundle._verify_scenario_image_state = image_verifier
        bundle.verify_full_verification_payload = original_full_verifier
    print("test_evaluation_bundle: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
