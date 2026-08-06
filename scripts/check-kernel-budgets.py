#!/usr/bin/env python3
"""Enforce reproducible kernel growth and Agent test-suite budgets."""

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
from decimal import Decimal, ROUND_CEILING
from pathlib import Path


class BudgetError(ValueError):
    pass


CALIBRATION_SECONDS_TOLERANCE = 0.001
AGENT_TEST_CALIBRATION_READY = "calibrated_full_suite"
AGENT_TEST_CALIBRATION_PROVISIONAL = "provisional_requires_full_suite"
AGENT_TEST_CALIBRATION_STATUSES = frozenset(
    (AGENT_TEST_CALIBRATION_READY, AGENT_TEST_CALIBRATION_PROVISIONAL)
)
AGENT_TEST_CALIBRATED_FIELDS = frozenset(
    (
        "baseline_seconds",
        "max_seconds",
        "calibration_samples",
        "source_fingerprint_sha256",
        "calibration_source_commit",
        "calibration_source_tree",
        "calibration_manifest_file",
        "calibration_manifest_sha256",
        "calibration_profile_id",
    )
)
AGENT_TEST_SOURCE_FINGERPRINT_VERSION = "agent-test-source-contract-v4"
AGENT_TEST_CALIBRATION_LIMIT_HEADROOM = 0.05
AGENT_TEST_CALIBRATION_LIMIT_POLICY = (
    "ceil(max(max_observed, median * 1.05) * 1000) / 1000"
)
AGENT_TEST_SOURCE_REQUIRED_PATHS = (
    "Makefile",
    "user/Makefile",
    "nfs/Makefile",
    "scripts/run-agent-tests.sh",
    "scripts/evidence-wiring.sh",
    "scripts/agent_test_runner.py",
    "scripts/guest_failure_classifier.py",
    "scripts/agent_test_calibration.py",
    "scripts/validate-kernel-test-log.py",
    "scripts/initproc.py",
    "scripts/test-sync-owner-wiring.py",
    "scripts/test-wait-atomic-wiring.py",
    "scripts/check-wait-queue-contract.py",
    "scripts/check-kernel-budgets.py",
)
AGENT_TEST_SOURCE_GLOBS = (
    "os/**/*.c",
    "os/**/*.h",
    "os/**/*.S",
    "os/**/*.ld",
    "os/**/*.inc",
    "os/**/*.py",
    "user/src/**/*.c",
    "user/src/**/*.h",
    "user/src/**/*.S",
    "user/include/**/*.h",
    "user/lib/**/*.c",
    "user/lib/**/*.h",
    "user/lib/**/*.S",
    "user/lib/**/*.ld",
    "user/lib/**/*.inc",
    "nfs/**/*.c",
    "nfs/**/*.h",
    "nfs/**/*.S",
    "nfs/**/*.ld",
    "nfs/**/*.inc",
    "*_abi.h",
    "*_policy.h",
    "*_policy.inc",
)
AGENT_TEST_SOURCE_EXCLUDES = frozenset(("os/initproc.S",))
REQUIRED_KERNEL_SOURCE_GLOBS = frozenset(
    (
        "os/**/*.c",
        "os/**/*.h",
        "os/**/*.S",
        "os/**/*.ld",
        "os/**/*.inc",
        "*_abi.h",
        "*_policy.h",
        "*_policy.inc",
    )
)
GENERATED_KERNEL_SOURCE_EXCLUDES = frozenset(("os/initproc.S",))
REQUIRED_TEST_ONLY_SUPPORTS = (
    {
        "name": "metadata_crash_profile",
        "source_path": "os/agent_metadata_test.c",
        "header_path": "os/metadata_crash_test.h",
        "required_macro": "AGENT_METADATA_CRASH_PHASE",
        "production_object_excluded": True,
        "allowed_profile_symbols": (
            "agent_metadata_test_init",
            "agent_metadata_test_bind",
            "agent_metadata_test_checkpoint",
            "agent_metadata_test_eio_start",
            "agent_metadata_test_eio_cancel",
            "agent_metadata_test_eio_pre_io",
            "agent_metadata_test_eio_commit",
            "sys_agent_metadata_test",
        ),
    },
    {
        "name": "metadata_boot_recovery_profile",
        "source_path": "os/agent_metadata_recovery_test.c",
        "header_path": "os/agent_metadata_recovery_test.h",
        "required_macro": "AGENT_METADATA_BOOT_READ_FAULT",
        "production_object_excluded": True,
        "allowed_profile_symbols": (
            "agent_metadata_recovery_test_init",
            "agent_metadata_recovery_test_fault",
            "agent_metadata_recovery_test_retry",
            "agent_metadata_recovery_test_admission",
        ),
    },
    {
        "name": "observe_recovery_profile",
        "source_path": "os/agent_observe_test.c",
        "header_path": "os/agent_observe_test.h",
        "required_macro": "AGENT_OBSERVE_TEST_PROFILE",
        "production_object_excluded": True,
        "allowed_profile_symbols": (
            "agent_observe_test_operation",
            "agent_observe_test_execute",
        ),
    },
    {
        "name": "wait_atomic_profile",
        "source_path": "os/wait_atomic_test.c",
        "header_path": "os/wait_atomic_test.h",
        "required_macro": "WAIT_ATOMIC_TEST_PROFILE",
        "production_object_excluded": True,
        "allowed_profile_symbols": (
            "sys_wait_atomic_test",
            "wait_atomic_test_begin",
            "wait_atomic_test_complete",
            "wait_atomic_test_agent_wait",
            "agent_ipc_wait_test_publish",
        ),
    },
    {
        "name": "fs_allocator_fault_profile",
        "source_path": "os/fs_allocator_test.c",
        "header_path": "os/fs_allocator_test.h",
        "required_macro": "FS_ALLOCATOR_FAULT_TEST_PROFILE",
        "production_object_excluded": True,
        "allowed_profile_symbols": (
            "fs_allocator_test_bind_boot_init",
            "fs_allocator_test_authorized",
            "fs_allocator_test_arm",
            "fs_allocator_test_disarm",
            "fs_allocator_test_snapshot",
            "fs_allocator_test_before",
            "fs_allocator_test_after",
            "fs_allocator_test_storage_snapshot",
        ),
    },
    {
        "name": "physical_page_profile",
        "source_path": "os/physical_page_test.c",
        "header_path": "os/physical_page_test.h",
        "required_macro": "PHYSICAL_PAGE_TEST_HOOKS",
        "production_object_excluded": True,
        "allowed_profile_symbols": (
            "physical_page_test_bind_boot_init",
            "sys_physical_page_test",
        ),
    },
)
REQUIRED_AGENT_TEST_CASES = (
    "agentfinal_ucore",
    "agentfs_ucore",
    "agentscan_ucore",
    "agentloop_ucore",
    "agentsched_ucore",
    "agentconflict_ucore",
    "agentllm_ucore",
    "agentbench_ucore",
    "ch8_cow_ucore",
    "labdemo_ucore",
    "agentsecurity_ucore",
    "agenttoolabi_ucore",
    "agentscope_ucore",
    "agenttrust_ucore",
    "agentvfs_ucore",
    "iobudget_ucore",
    "usersafety_ucore",
    "blocking_semantics_ucore",
)
CONTROLLED_AGENT_SYMBOL_PREFIXES = (
    "agent_",
    "sys_agent_",
    "sys_context_",
    "sys_tool_",
    "resource_",
    "workflow_lifecycle_",
)
CONTROLLED_AGENT_EXACT_SYMBOLS = frozenset(("agentinit",))
REQUIRED_AGENT_MAX_SCC_SIZE = 3
REQUIRED_AGENT_ALLOWED_SCCS = frozenset(
    (
        frozenset(("context", "observe", "observe_timeline")),
        frozenset(("observe_ledger", "observe_store")),
        frozenset(("ipc", "metadata_prefetch")),
    )
)
REQUIRED_AGENT_INTEGRATION_ALLOWED_SCCS = frozenset(
    (
        frozenset(("context", "observe", "observe_timeline")),
        frozenset(("observe_ledger", "observe_store")),
        frozenset(("core", "facade", "proc")),
        frozenset(("ipc", "metadata_prefetch")),
    )
)
REQUIRED_AGENT_AGGREGATES = {
    "metadata_control_plane": frozenset(
        (
            "metadata",
            "file_state",
            "metadata_actions",
            "metadata_objects",
            "metadata_prefetch",
            "metadata_catalog",
            "metadata_directory",
            "metadata_journal",
            "metadata_query",
            "metadata_probe",
            "metadata_recovery",
            "metadata_scan",
            "metadata_store",
            "metadata_store_format",
            "metadata_store_io",
            "ipc",
        )
    )
}
REQUIRED_AGENT_AGGREGATE_HEADERS = {
    "metadata_control_plane": frozenset(
        (
            "agent_metadata_disk_abi.h",
            "os/agent_file_name_policy.h",
            "os/agent_file_state_internal.h",
            "os/agent_metadata_actions.h",
            "os/agent_metadata_internal.h",
            "os/agent_metadata_journal.h",
            "os/agent_metadata_catalog.h",
            "os/agent_metadata_directory.h",
            "os/agent_metadata_disk.h",
            "os/agent_metadata_probe.h",
            "os/agent_metadata_recovery.h",
            "os/agent_metadata_recovery_test.h",
            "os/agent_metadata_store_format.h",
            "os/agent_metadata_store_io.h",
            "os/agent_metadata_query.h",
            "os/agent_metadata_scan.h",
            "os/agent_metadata_prefetch.h",
            "os/agent_observe_persist_context.h",
        )
    )
}
REQUIRED_AGENT_AGGREGATE_HEADER_GLOBS = {
    "metadata_control_plane": (
        "agent_metadata_disk_abi.h",
        "os/agent_metadata*.h",
        "os/agent_file*.h",
        "os/agent_query*.h",
        "os/agent_scan*.h",
        "os/agent_directory*.h",
        "os/agent_observe_persist_context.h",
    )
}
REQUIRED_AGENT_AGGREGATE_SHARED_HEADERS = frozenset(
    (
        "os/agent.h",
        "os/agent_internal.h",
        "os/agent_context.h",
        "os/agent_durable_section.h",
        "os/agent_lifecycle.h",
    )
)
REQUIRED_AGENT_MODULE_CFLAGS = {
    "context_path": ("-Os",),
    "file_state": ("-Os",),
    "ipc": ("-Os",),
    "metadata": ("-Os",),
    "metadata_actions": ("-Os",),
    "metadata_catalog": ("-Os",),
    "metadata_directory": ("-Os",),
    "metadata_journal": ("-Os",),
    "metadata_objects": ("-Os",),
    "metadata_prefetch": ("-Os",),
    "metadata_probe": ("-Os",),
    "metadata_query": ("-Os",),
    "metadata_recovery": ("-Os",),
    "metadata_scan": ("-Os",),
    "metadata_store": ("-Os",),
    "metadata_store_format": ("-Os",),
    "metadata_store_io": ("-Os",),
    "observe_capacity": ("-Os",),
    "observe_ledger": ("-Os",),
    "observe_recovery": ("-Os",),
    "observe_store": ("-Os",),
}
REQUIRED_AGENT_DISCARDED_SECTIONS = (".eh_frame",)
REQUIRED_METADATA_DIRECTORY_STORE_SYMBOLS = frozenset(
    ("agent_metadata_store_loaded", "agent_metadata_store_mark_dirty")
)
REQUIRED_AGENT_SOURCE_BUDGET_POLICY = (
    "5% for fixed module contract overhead; loaded text and BSS remain no-growth"
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--check",
        choices=(
            "kernel",
            "agent-modules",
            "agent-test-policy",
            "agent-tests",
            "agent-test-timing-inventory",
            "config",
        ),
        default="kernel",
    )
    parser.add_argument("--kernel", default="build/kernel")
    parser.add_argument(
        "--struct-probe", default="build/ci/struct-proc-size.o"
    )
    parser.add_argument(
        "--agent-core-probe", default="build/ci/agent-core-boundary.o"
    )
    parser.add_argument("--objcopy")
    parser.add_argument("--objdump")
    parser.add_argument("--ld")
    parser.add_argument("--nm")
    parser.add_argument("--cc")
    parser.add_argument("--size")
    parser.add_argument("--callgraph-dir", default="build/os")
    parser.add_argument("--object-dir", default="build/os")
    parser.add_argument(
        "--stack-build-config", default="build/.kernel-stack-config"
    )
    parser.add_argument(
        "--stack-checker", default="scripts/check-kernel-stack-usage.py"
    )
    parser.add_argument("--agent-test-timing-file")
    parser.add_argument("--agent-test-calibration", action="store_true")
    return parser.parse_args()


def require_mapping(value, name):
    if not isinstance(value, dict):
        raise BudgetError(f"{name} must be an object")
    return value


def require_positive_number(mapping, name, integer=False):
    value = mapping.get(name)
    valid_type = isinstance(value, int) if integer else isinstance(value, (int, float))
    if (
        not valid_type
        or isinstance(value, bool)
        or value <= 0
        or (not integer and not math.isfinite(value))
    ):
        raise BudgetError(f"{name} must be a positive number")
    return value


def require_string_list(mapping, name):
    value = mapping.get(name)
    if not isinstance(value, list) or not value:
        raise BudgetError(f"{name} must be a non-empty string array")
    if any(not isinstance(item, str) or not item for item in value):
        raise BudgetError(f"{name} must be a non-empty string array")
    return value


def require_string_array(mapping, name):
    value = mapping.get(name)
    if not isinstance(value, list):
        raise BudgetError(f"{name} must be a string array")
    if any(not isinstance(item, str) or not item for item in value):
        raise BudgetError(f"{name} must be a string array")
    return value


def calibrated_agent_test_limit(totals):
    values = tuple(Decimal(str(value)) for value in totals)
    median = statistics.median(values)
    candidate = max(
        max(values),
        median
        * (
            Decimal(1)
            + Decimal(str(AGENT_TEST_CALIBRATION_LIMIT_HEADROOM))
        ),
    )
    return float(candidate.quantize(Decimal("0.001"), rounding=ROUND_CEILING))


def collect_agent_test_source_paths(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise BudgetError(f"Agent test source root is not a directory: {root}")

    paths = set()
    for relative in AGENT_TEST_SOURCE_REQUIRED_PATHS:
        path = root / relative
        if not path.is_file():
            raise BudgetError(
                "Agent test source/contract input is missing: " + relative
            )
        paths.add(path)
    for pattern in AGENT_TEST_SOURCE_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative not in AGENT_TEST_SOURCE_EXCLUDES:
                paths.add(path)

    relative_paths = tuple(
        sorted(path.relative_to(root).as_posix() for path in paths)
    )
    if not relative_paths:
        raise BudgetError("Agent test source/contract inventory is empty")
    return relative_paths


def agent_test_source_fingerprint(root, config):
    root = Path(root).resolve()
    paths = collect_agent_test_source_paths(root)
    tests = config["agent_test_suite"]
    contract = {
        "canonical_toolchain": config["canonical_toolchain"],
        "local_kernel_budget_toolchains": config.get(
            "local_kernel_budget_toolchains", []
        ),
        "expected_cases": tests["expected_cases"],
        "local_calibration_profile": tests["local_calibration_profile"],
    }
    contract_bytes = json.dumps(
        contract,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")

    digest = hashlib.sha256()

    def add_record(label, data):
        label_bytes = label.encode("utf-8")
        digest.update(len(label_bytes).to_bytes(8, "big"))
        digest.update(label_bytes)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)

    add_record("version", AGENT_TEST_SOURCE_FINGERPRINT_VERSION.encode("ascii"))
    add_record("contract", contract_bytes)
    for relative in paths:
        try:
            data = (root / relative).read_bytes()
        except OSError as error:
            raise BudgetError(
                f"cannot read Agent test source/contract input {relative}: "
                f"{error}"
            ) from error
        add_record(relative, data)
    return digest.hexdigest(), paths


def check_agent_test_source_fingerprint(root, config):
    tests = config["agent_test_suite"]
    if tests["calibration_status"] != AGENT_TEST_CALIBRATION_READY:
        return None
    actual, paths = agent_test_source_fingerprint(root, config)
    expected = tests["source_fingerprint_sha256"]
    if actual != expected:
        raise BudgetError(
            "Agent test source/contract fingerprint mismatch: "
            f"expected={expected}, actual={actual}; return duration policy "
            "to provisional and recalibrate the complete suite"
        )
    print(
        "[kernel-budget] Agent source/contract fingerprint: "
        f"sha256={actual}, inputs={len(paths)}"
    )
    return actual, len(paths)


def validate_pair(
    section, baseline_name, maximum_name, integer=False, max_headroom=0.05
):
    baseline = require_positive_number(section, baseline_name, integer)
    maximum = require_positive_number(section, maximum_name, integer)
    if maximum < baseline:
        raise BudgetError(f"{maximum_name} must not be below {baseline_name}")
    if not isinstance(max_headroom, (int, float)) or not 0 <= max_headroom <= 0.10:
        raise BudgetError("budget headroom policy is invalid")
    allowed = baseline * (1 + max_headroom)
    if integer:
        allowed = math.ceil(allowed)
    if maximum > allowed + 1e-9:
        raise BudgetError(
            f"{maximum_name} leaves more than {max_headroom * 100:g}% "
            "growth headroom"
        )
    return baseline, maximum


def validate_agent_aggregate_budgets(modules, module_names):
    groups = modules.get("aggregate_budgets")
    if not isinstance(groups, list) or not groups:
        raise BudgetError("agent_modules.aggregate_budgets must be a non-empty array")
    names = set()
    claimed_members = set()
    for index, group in enumerate(groups):
        label = f"agent_modules.aggregate_budgets[{index}]"
        group = require_mapping(group, label)
        name = group.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]+", name):
            raise BudgetError(f"{label}.name must be an aggregate identifier")
        if name in names:
            raise BudgetError(f"duplicate Agent aggregate budget name: {name}")
        names.add(name)
        members = require_string_list(group, "members")
        if len(set(members)) != len(members):
            raise BudgetError(f"{label}.members contains duplicates")
        unknown = set(members) - module_names
        if unknown:
            raise BudgetError(f"{label}.members has unknown modules: {sorted(unknown)!r}")
        duplicated = claimed_members & set(members)
        if duplicated:
            raise BudgetError(
                f"Agent aggregate members appear in multiple groups: {sorted(duplicated)!r}"
            )
        claimed_members.update(members)
        headers = require_string_list(group, "contract_headers")
        if len(set(headers)) != len(headers):
            raise BudgetError(f"{label}.contract_headers contains duplicates")
        if any(Path(header).is_absolute() or ".." in Path(header).parts for header in headers):
            raise BudgetError(f"{label}.contract_headers must be relative paths")
        header_globs = tuple(require_string_list(group, "contract_header_globs"))
        if header_globs != REQUIRED_AGENT_AGGREGATE_HEADER_GLOBS.get(name):
            raise BudgetError(f"{label}.contract_header_globs inventory drift")
        if group.get("source_budget_policy") != REQUIRED_AGENT_SOURCE_BUDGET_POLICY:
            raise BudgetError(f"{label}.source_budget_policy must explain the 5% source allowance")
        for metric in (
            "source_lines",
            "source_bytes",
            "loaded_text_bytes",
            "bss_bytes",
        ):
            baseline = require_positive_number(group, f"baseline_{metric}", integer=True)
            maximum = require_positive_number(group, f"max_{metric}", integer=True)
            if maximum < baseline:
                raise BudgetError(f"{label}.max_{metric} is below its baseline")
            # Module contracts have fixed source cost; runtime footprint may not grow.
            allowed_ratio = 1.05 if metric in ("source_lines", "source_bytes") else 1.0
            if maximum > baseline * allowed_ratio:
                raise BudgetError(
                    f"{label}.max_{metric} exceeds its no-bloat allowance"
                )
        discarded = tuple(require_string_list(group, "discarded_sections"))
        if discarded != REQUIRED_AGENT_DISCARDED_SECTIONS:
            raise BudgetError(
                f"{label}.discarded_sections must match the linker discard inventory"
            )
    if names != set(REQUIRED_AGENT_AGGREGATES):
        raise BudgetError("Agent aggregate budget inventory drift")
    for group in groups:
        required = REQUIRED_AGENT_AGGREGATES[group["name"]]
        if set(group["members"]) != required:
            raise BudgetError(
                f"Agent aggregate {group['name']} member inventory drift: "
                f"missing={sorted(required - set(group['members']))!r}, "
                f"extra={sorted(set(group['members']) - required)!r}"
            )
        required_headers = REQUIRED_AGENT_AGGREGATE_HEADERS[group["name"]]
        if set(group["contract_headers"]) != required_headers:
            raise BudgetError(
                f"Agent aggregate {group['name']} contract header inventory drift: "
                f"missing={sorted(required_headers - set(group['contract_headers']))!r}, "
                f"extra={sorted(set(group['contract_headers']) - required_headers)!r}"
            )


def validate_config(config):
    require_mapping(config, "config")
    if config.get("schema_version") != 1:
        raise BudgetError("unsupported kernel budget schema")

    toolchain = require_mapping(
        config.get("canonical_toolchain"), "canonical_toolchain"
    )
    for name in (
        "profile_id",
        "prefix",
        "gcc_version",
        "gcc_package",
        "gcc_package_version",
        "binutils_version",
        "binutils_package",
        "binutils_package_version",
        "init_proc",
        "log_level",
    ):
        if not isinstance(toolchain.get(name), str) or not toolchain[name]:
            raise BudgetError(f"canonical_toolchain.{name} must be a string")
    require_string_list(toolchain, "cflags")
    require_string_list(toolchain, "ldflags")

    local_toolchains = config.get("local_kernel_budget_toolchains")
    if not isinstance(local_toolchains, list) or not local_toolchains:
        raise BudgetError(
            "local_kernel_budget_toolchains must be a non-empty array"
        )
    local_profile_ids = set()
    local_prefixes = set()
    expected_local_fields = {
        "profile_id",
        "prefix",
        "gcc_version",
        "binutils_version",
        "executable_sha256",
    }
    expected_local_tools = {
        "gcc",
        "cc1",
        "as",
        "ld",
        "objcopy",
        "objdump",
        "nm",
        "size",
    }
    for index, profile in enumerate(local_toolchains):
        label = f"local_kernel_budget_toolchains[{index}]"
        profile = require_mapping(profile, label)
        if set(profile) != expected_local_fields:
            raise BudgetError(f"{label} fields mismatch")
        profile_id = profile.get("profile_id")
        if (
            not isinstance(profile_id, str)
            or re.fullmatch(r"[A-Za-z0-9_.-]+", profile_id) is None
        ):
            raise BudgetError(f"{label}.profile_id is invalid")
        if profile_id in local_profile_ids:
            raise BudgetError(f"duplicate local kernel budget profile: {profile_id}")
        local_profile_ids.add(profile_id)
        prefix = profile.get("prefix")
        if (
            not isinstance(prefix, str)
            or re.fullmatch(r"[A-Za-z0-9_.+-]+-", prefix) is None
        ):
            raise BudgetError(f"{label}.prefix is invalid")
        if prefix == toolchain["prefix"] or prefix in local_prefixes:
            raise BudgetError(f"{label}.prefix is not unique")
        local_prefixes.add(prefix)
        for name in ("gcc_version", "binutils_version"):
            if not isinstance(profile.get(name), str) or not profile[name]:
                raise BudgetError(f"{label}.{name} must be a string")
        executable_sha256 = require_mapping(
            profile.get("executable_sha256"), f"{label}.executable_sha256"
        )
        if set(executable_sha256) != expected_local_tools:
            raise BudgetError(f"{label}.executable_sha256 inventory mismatch")
        for name, digest in executable_sha256.items():
            if (
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise BudgetError(
                    f"{label}.executable_sha256.{name} must be a lowercase SHA-256"
                )

    source = require_mapping(config.get("kernel_source"), "kernel_source")
    include_globs = require_string_list(source, "include_globs")
    missing_source_globs = REQUIRED_KERNEL_SOURCE_GLOBS - set(include_globs)
    if missing_source_globs:
        raise BudgetError(
            "kernel_source.include_globs omits required source inventory: "
            + ", ".join(sorted(missing_source_globs))
        )
    exclude_paths = source.get("exclude_paths")
    if not isinstance(exclude_paths, list) or any(
        not isinstance(item, str) or not item for item in exclude_paths
    ):
        raise BudgetError("exclude_paths must be a string array")
    validate_pair(source, "baseline_lines", "max_lines", integer=True)

    image = require_mapping(config.get("kernel_image"), "kernel_image")
    validate_pair(
        image,
        "baseline_stripped_elf_bytes",
        "max_stripped_elf_bytes",
        integer=True,
    )
    validate_pair(
        image,
        "baseline_raw_binary_bytes",
        "max_raw_binary_bytes",
        integer=True,
    )

    runtime = require_mapping(config.get("kernel_runtime"), "kernel_runtime")
    for section in ("text", "data", "bss", "total"):
        validate_pair(
            runtime,
            f"baseline_{section}_bytes",
            f"max_{section}_bytes",
            integer=True,
        )

    proc = require_mapping(config.get("struct_proc"), "struct_proc")
    if not isinstance(proc.get("symbol"), str) or not proc["symbol"]:
        raise BudgetError("struct_proc.symbol must be a non-empty string")
    validate_pair(proc, "baseline_bytes", "max_bytes", integer=True)

    trapframes = require_mapping(
        config.get("trapframe_pages"), "trapframe_pages"
    )
    for name in ("per_thread", "admitted_pool", "reserved_pool"):
        symbol_name = f"{name}_symbol"
        if (
            not isinstance(trapframes.get(symbol_name), str)
            or not trapframes[symbol_name]
        ):
            raise BudgetError(
                f"trapframe_pages.{symbol_name} must be a string"
            )
        validate_pair(
            trapframes,
            f"baseline_{name}_bytes",
            f"max_{name}_bytes",
            integer=True,
        )

    legacy_mail = require_mapping(
        config.get("legacy_mail_sidecar"), "legacy_mail_sidecar"
    )
    for name in (
        "per_process",
        "pool",
        "ordinary_pool",
        "reserved_pool",
        "domain_ordinary",
        "domain_reserved",
    ):
        symbol_name = f"{name}_symbol"
        if (
            not isinstance(legacy_mail.get(symbol_name), str)
            or not legacy_mail[symbol_name]
        ):
            raise BudgetError(
                f"legacy_mail_sidecar.{symbol_name} must be a string"
            )
        validate_pair(
            legacy_mail,
            f"baseline_{name}_bytes",
            f"max_{name}_bytes",
            integer=True,
        )

    sidecar = require_mapping(
        config.get("agent_context_sidecar"), "agent_context_sidecar"
    )
    for name in (
        "per_process",
        "pool",
        "ordinary_pool",
        "reserved_pool",
        "domain_ordinary",
        "domain_reserved",
    ):
        symbol_name = f"{name}_symbol"
        if (
            not isinstance(sidecar.get(symbol_name), str)
            or not sidecar[symbol_name]
        ):
            raise BudgetError(
                f"agent_context_sidecar.{symbol_name} must be a string"
            )
        validate_pair(
            sidecar,
            f"baseline_{name}_bytes",
            f"max_{name}_bytes",
            integer=True,
        )

    agent_state = require_mapping(
        config.get("agent_state_pages"), "agent_state_pages"
    )
    for name in (
        "per_process",
        "pool",
        "ordinary_pool",
        "reserved_pool",
        "domain_ordinary",
        "domain_reserved",
    ):
        symbol_name = f"{name}_symbol"
        if (
            not isinstance(agent_state.get(symbol_name), str)
            or not agent_state[symbol_name]
        ):
            raise BudgetError(
                f"agent_state_pages.{symbol_name} must be a string"
            )
        validate_pair(
            agent_state,
            f"baseline_{name}_bytes",
            f"max_{name}_bytes",
            integer=True,
        )

    tests = require_mapping(config.get("agent_test_suite"), "agent_test_suite")
    require_string_list(tests, "expected_cases")
    if len(set(tests["expected_cases"])) != len(tests["expected_cases"]):
        raise BudgetError("agent_test_suite.expected_cases contains duplicates")
    if tuple(tests["expected_cases"]) != REQUIRED_AGENT_TEST_CASES:
        raise BudgetError(
            "agent_test_suite.expected_cases must match the required "
            f"{len(REQUIRED_AGENT_TEST_CASES)}-case regression contract"
        )
    calibration_status = tests.get("calibration_status")
    if calibration_status not in AGENT_TEST_CALIBRATION_STATUSES:
        raise BudgetError(
            "agent_test_suite.calibration_status is not recognized"
        )
    if "runner_tag" in tests or "runner_profile" in tests:
        raise BudgetError(
            "agent_test_suite remote runner fields are obsolete"
        )
    local_profile = require_mapping(
        tests.get("local_calibration_profile"),
        "agent_test_suite.local_calibration_profile",
    )
    expected_profile_fields = {
        "schema_version",
        "profile_id",
        "cpu",
        "runtime",
        "toolchain_prefix",
        "tool_versions",
    }
    if set(local_profile) != expected_profile_fields:
        raise BudgetError(
            "agent_test_suite.local_calibration_profile fields mismatch"
        )
    if local_profile.get("schema_version") != 1:
        raise BudgetError(
            "agent_test_suite.local_calibration_profile schema is unsupported"
        )
    profile_id = local_profile.get("profile_id")
    if (
        not isinstance(profile_id, str)
        or re.fullmatch(r"[A-Za-z0-9_.-]+", profile_id) is None
    ):
        raise BudgetError(
            "agent_test_suite.local_calibration_profile.profile_id is invalid"
        )
    for name in ("cpu", "runtime"):
        if not isinstance(local_profile.get(name), str) or not local_profile[name]:
            raise BudgetError(
                "agent_test_suite.local_calibration_profile."
                f"{name} must be a string"
            )
    toolchain_prefix = local_profile.get("toolchain_prefix")
    if (
        not isinstance(toolchain_prefix, str)
        or re.fullmatch(r"[A-Za-z0-9_.+-]+-", toolchain_prefix) is None
    ):
        raise BudgetError(
            "agent_test_suite.local_calibration_profile.toolchain_prefix "
            "is invalid"
        )
    tool_versions = require_mapping(
        local_profile.get("tool_versions"),
        "agent_test_suite.local_calibration_profile.tool_versions",
    )
    expected_profile_tools = {
        "qemu",
        "toolchain_cc",
        "toolchain_ld",
        "toolchain_objcopy",
        "toolchain_objdump",
        "toolchain_as",
        "host_cc",
        "python",
        "bash",
        "make",
        "git",
    }
    if set(tool_versions) != expected_profile_tools:
        raise BudgetError(
            "agent_test_suite.local_calibration_profile.tool_versions "
            "inventory mismatch"
        )
    for name, version in tool_versions.items():
        if not isinstance(version, str) or not version:
            raise BudgetError(
                "agent_test_suite.local_calibration_profile.tool_versions."
                f"{name} must be a string"
            )
    matching_budget_profiles = [
        entry
        for entry in local_toolchains
        if entry["profile_id"] == profile_id
    ]
    if len(matching_budget_profiles) != 1:
        raise BudgetError(
            "the local calibration profile must have one kernel budget "
            "toolchain identity"
        )
    budget_profile = matching_budget_profiles[0]
    if budget_profile["prefix"] != toolchain_prefix:
        raise BudgetError(
            "the local kernel budget toolchain prefix differs from the "
            "calibration profile"
        )
    version_bindings = {
        "toolchain_cc": budget_profile["gcc_version"],
        "toolchain_ld": budget_profile["binutils_version"],
        "toolchain_objcopy": budget_profile["binutils_version"],
        "toolchain_objdump": budget_profile["binutils_version"],
        "toolchain_as": budget_profile["binutils_version"],
    }
    if any(
        tool_versions[name] != expected
        for name, expected in version_bindings.items()
    ):
        raise BudgetError(
            "the local kernel budget toolchain versions differ from the "
            "calibration profile"
        )
    if calibration_status == AGENT_TEST_CALIBRATION_READY:
        if tests.get("calibration_profile_id") != profile_id:
            raise BudgetError(
                "calibrated Agent duration must bind the local profile id"
            )
        fingerprint = tests.get("source_fingerprint_sha256")
        if (
            not isinstance(fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
        ):
            raise BudgetError(
                "calibrated Agent duration requires a lowercase SHA-256 "
                "source_fingerprint_sha256"
            )
        source_commit = tests.get("calibration_source_commit")
        if (
            not isinstance(source_commit, str)
            or not re.fullmatch(r"[0-9a-f]{40}", source_commit)
        ):
            raise BudgetError(
                "calibrated Agent duration requires a full lowercase Git "
                "calibration_source_commit"
            )
        source_tree = tests.get("calibration_source_tree")
        if (
            not isinstance(source_tree, str)
            or not re.fullmatch(r"[0-9a-f]{40}", source_tree)
        ):
            raise BudgetError(
                "calibrated Agent duration requires a full lowercase Git "
                "calibration_source_tree"
            )
        commit_prefix = source_commit[:12]
        manifest_file = tests.get("calibration_manifest_file")
        expected_manifest_file = (
            f"evidence/calibrations/{commit_prefix}/manifest.json"
        )
        if manifest_file != expected_manifest_file:
            raise BudgetError(
                "agent_test_suite.calibration_manifest_file must be "
                f"{expected_manifest_file!r}"
            )
        manifest_sha256 = tests.get("calibration_manifest_sha256")
        if (
            not isinstance(manifest_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256)
        ):
            raise BudgetError(
                "calibrated Agent duration requires a lowercase SHA-256 "
                "calibration_manifest_sha256"
            )
        validate_pair(
            tests,
            "baseline_seconds",
            "max_seconds",
            max_headroom=0.10,
        )
        samples = tests.get("calibration_samples")
        if not isinstance(samples, list) or len(samples) != 3:
            raise BudgetError(
                "calibrated Agent duration requires exactly three samples"
            )
        sample_ids = set()
        timing_files = set()
        totals = []
        for index, sample in enumerate(samples):
            sample_name = f"agent_test_suite.calibration_samples[{index}]"
            sample = require_mapping(sample, sample_name)
            sample_fields = {
                "sample_id",
                "total_seconds",
                "timing_file",
                "timing_file_sha256",
                "attestation_digest_sha256",
            }
            if set(sample) != sample_fields:
                raise BudgetError(
                    f"{sample_name} fields must be {sorted(sample_fields)!r}"
                )
            sample_id = sample.get("sample_id")
            if (
                not isinstance(sample_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", sample_id)
            ):
                raise BudgetError(
                    f"{sample_name}.sample_id must be an audit identifier"
                )
            if sample_id in sample_ids:
                raise BudgetError("Agent calibration sample ids must be unique")
            sample_ids.add(sample_id)
            ordinal = index + 1
            expected_sample_id = (
                f"agent{len(tests['expected_cases'])}-"
                f"{commit_prefix}-{ordinal:02d}"
            )
            if sample_id != expected_sample_id:
                raise BudgetError(
                    f"{sample_name}.sample_id must be "
                    f"{expected_sample_id!r}"
                )
            timing_file = sample.get("timing_file")
            expected_timing_file = (
                f"evidence/calibrations/{commit_prefix}/"
                f"{ordinal:02d}.timing"
            )
            if timing_file != expected_timing_file:
                raise BudgetError(
                    f"{sample_name}.timing_file must be "
                    f"{expected_timing_file!r}"
                )
            if timing_file in timing_files:
                raise BudgetError(
                    "Agent calibration timing files must be unique"
                )
            timing_files.add(timing_file)
            timing_sha256 = sample.get("timing_file_sha256")
            if (
                not isinstance(timing_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", timing_sha256)
            ):
                raise BudgetError(
                    f"{sample_name}.timing_file_sha256 requires a "
                    "lowercase SHA-256"
                )
            attestation_digest = sample.get("attestation_digest_sha256")
            if (
                not isinstance(attestation_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", attestation_digest)
            ):
                raise BudgetError(
                    f"{sample_name}.attestation_digest_sha256 requires a "
                    "lowercase SHA-256"
                )
            totals.append(
                require_positive_number(sample, "total_seconds")
            )
        median = statistics.median(totals)
        if not math.isclose(
            tests["baseline_seconds"],
            median,
            rel_tol=1e-6,
            abs_tol=CALIBRATION_SECONDS_TOLERANCE,
        ):
            raise BudgetError(
                "Agent duration baseline must match the calibration median"
            )
        if tests["max_seconds"] < max(totals):
            raise BudgetError(
                "Agent duration limit does not cover every calibration sample"
            )
        if tests["max_seconds"] > tests["baseline_seconds"] * 1.10:
            raise BudgetError(
                "Agent duration limit exceeds 110% of the calibration median"
            )
        expected_max = calibrated_agent_test_limit(totals)
        if not math.isclose(
            tests["max_seconds"],
            expected_max,
            rel_tol=1e-9,
            abs_tol=CALIBRATION_SECONDS_TOLERANCE / 10.0,
        ):
            raise BudgetError(
                "Agent duration limit must follow the calibrated 5% "
                "headroom policy"
            )
    else:
        stale_fields = sorted(
            field
            for field in AGENT_TEST_CALIBRATED_FIELDS
            if field in tests
        )
        if stale_fields:
            raise BudgetError(
                "provisional Agent duration must not carry stale calibrated "
                f"fields: {stale_fields!r}"
            )
    stack = require_mapping(config.get("kernel_stack"), "kernel_stack")
    validate_pair(
        stack, "baseline_required_bytes", "max_required_bytes", integer=True
    )
    validate_pair(
        stack,
        "baseline_boot_required_bytes",
        "max_boot_required_bytes",
        integer=True,
    )
    for name in (
        "boot_root",
        "boot_stack_start_symbol",
        "boot_stack_end_symbol",
    ):
        if (
            not isinstance(stack.get(name), str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stack[name])
        ):
            raise BudgetError(f"kernel_stack.{name} must be a symbol name")
    if stack["boot_stack_start_symbol"] == stack["boot_stack_end_symbol"]:
        raise BudgetError("kernel stack boundary symbols must be distinct")
    if (
        not isinstance(stack.get("virtual_capacity_symbol"), str)
        or not stack["virtual_capacity_symbol"]
    ):
        raise BudgetError(
            "kernel_stack.virtual_capacity_symbol must be a string"
        )
    validate_pair(
        stack,
        "baseline_virtual_capacity_bytes",
        "max_virtual_capacity_bytes",
        integer=True,
    )
    if (
        not isinstance(stack.get("reserved_physical_pool_symbol"), str)
        or not stack["reserved_physical_pool_symbol"]
    ):
        raise BudgetError(
            "kernel_stack.reserved_physical_pool_symbol must be a string"
        )
    validate_pair(
        stack,
        "baseline_reserved_physical_pool_bytes",
        "max_reserved_physical_pool_bytes",
        integer=True,
    )
    for name in (
        "stack_size_bytes",
        "boot_stack_size_bytes",
        "guard_size_bytes",
        "safety_margin_bytes",
        "interrupt_entry_bytes",
    ):
        require_positive_number(stack, name, integer=True)
    require_string_list(stack, "stack_boundaries")
    require_string_list(stack, "allowed_indirect_callers")
    require_string_list(stack, "indirect_call_edges")
    require_string_list(stack, "recursion_bounds")
    if stack["max_required_bytes"] > stack["stack_size_bytes"]:
        raise BudgetError("kernel stack growth limit exceeds the configured stack")
    if stack["max_boot_required_bytes"] > stack["boot_stack_size_bytes"]:
        raise BudgetError(
            "boot stack growth limit exceeds the configured stack"
        )

    modules = require_mapping(config.get("agent_modules"), "agent_modules")
    entries = modules.get("modules")
    if not isinstance(entries, list) or not entries:
        raise BudgetError("agent_modules.modules must be a non-empty array")
    names = set()
    paths = set()
    object_paths = set()
    for index, entry in enumerate(entries):
        entry_name = f"agent_modules.modules[{index}]"
        entry = require_mapping(entry, entry_name)
        name = entry.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]+", name):
            raise BudgetError(f"{entry_name}.name must be a module identifier")
        if name in names:
            raise BudgetError(f"duplicate Agent module name: {name}")
        names.add(name)
        for field in ("source_path", "object_path"):
            value = entry.get(field)
            if (
                not isinstance(value, str)
                or not value
                or Path(value).is_absolute()
                or ".." in Path(value).parts
            ):
                raise BudgetError(f"{entry_name}.{field} must be a relative path")
        if entry["source_path"] in paths:
            raise BudgetError(
                f"duplicate Agent module source: {entry['source_path']}"
            )
        paths.add(entry["source_path"])
        if entry["object_path"] in object_paths:
            raise BudgetError(
                f"duplicate Agent module object: {entry['object_path']}"
            )
        object_paths.add(entry["object_path"])
        validate_pair(
            entry, "baseline_lines", "max_lines", integer=True
        )
        max_bss = entry.get("max_bss_bytes")
        if (
            isinstance(max_bss, bool)
            or not isinstance(max_bss, int)
            or max_bss < 0
        ):
            raise BudgetError(
                f"{entry_name}.max_bss_bytes must be a non-negative integer"
            )
        prefixes = require_string_array(entry, "allowed_global_prefixes")
        symbols = require_string_array(entry, "allowed_global_symbols")
        readonly = require_string_array(entry, "allowed_readonly_symbols")
        dependencies = require_string_array(entry, "allowed_dependencies")
        bridge_dependencies = require_string_array(
            entry, "allowed_bridge_dependencies"
        )
        required_cflags = entry.get("required_cflags")
        if required_cflags is not None:
            required_cflags = require_string_list(entry, "required_cflags")
            if len(set(required_cflags)) != len(required_cflags):
                raise BudgetError(
                    f"{entry_name}.required_cflags contains duplicates"
                )
        if not prefixes and not symbols:
            raise BudgetError(
                f"{entry_name} must allow at least one global symbol"
            )
        if any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value)
            for value in prefixes + symbols
        ):
            raise BudgetError(
                f"{entry_name} global symbol policy is not an identifier"
            )
        if any(
            not is_controlled_agent_symbol(value)
            for value in prefixes + symbols
        ):
            raise BudgetError(
                f"{entry_name} global symbols must use a controlled namespace"
            )
        namespace_roots = {
            "resource_controller": ("resource_",),
            "workflow_lifecycle": ("workflow_lifecycle_",),
        }.get(name, ("agent_", "sys_"))
        if any(
            not prefix.endswith("_")
            or not prefix.startswith(namespace_roots)
            for prefix in prefixes
        ):
            raise BudgetError(
                f"{entry_name} global prefixes do not belong to the module"
            )
        if (
            len(set(prefixes)) != len(prefixes)
            or len(set(symbols)) != len(symbols)
            or len(set(readonly)) != len(readonly)
            or len(set(dependencies)) != len(dependencies)
            or len(set(bridge_dependencies)) != len(bridge_dependencies)
        ):
            raise BudgetError(f"{entry_name} contains duplicate policy entries")
        if any(symbol not in symbols for symbol in readonly):
            raise BudgetError(
                f"{entry_name} read-only symbols must be exact global symbols"
            )

    exact_owners = {}
    prefix_owners = []
    for entry in entries:
        owner = entry["name"]
        for symbol in entry["allowed_global_symbols"]:
            previous_owner = exact_owners.get(symbol)
            if previous_owner is not None:
                raise BudgetError(
                    "duplicate Agent module exact export owner: "
                    f"{previous_owner}:{symbol} and {owner}:{symbol}"
                )
            exact_owners[symbol] = owner
        for prefix in entry["allowed_global_prefixes"]:
            for previous_prefix, previous_owner in prefix_owners:
                if (
                    prefix.startswith(previous_prefix)
                    or previous_prefix.startswith(prefix)
                ):
                    raise BudgetError(
                        "overlapping Agent module export prefixes: "
                        f"{previous_owner}:{previous_prefix} and "
                        f"{owner}:{prefix}"
                    )
            prefix_owners.append((prefix, owner))

    for symbol, exact_owner in exact_owners.items():
        for prefix, prefix_owner in prefix_owners:
            if exact_owner != prefix_owner and symbol.startswith(prefix):
                raise BudgetError(
                    "ambiguous Agent module export owner: "
                    f"{exact_owner}:{symbol} is covered by "
                    f"{prefix_owner}:{prefix}"
                )

    for index, entry in enumerate(entries):
        unknown = set(entry["allowed_dependencies"]) - names
        if entry["name"] in entry["allowed_dependencies"]:
            raise BudgetError(
                f"Agent module {entry['name']} depends on itself"
            )
        if unknown:
            raise BudgetError(
                f"Agent module {entry['name']} has unknown dependencies: "
                f"{sorted(unknown)!r}"
            )
    required_modules = {
        "background",
        "facade",
        "core",
        "context",
        "context_path",
        "durable_section",
        "file_state",
        "identity",
        "identity_lease",
        "ipc",
        "lifecycle",
        "metadata",
        "metadata_actions",
        "metadata_catalog",
        "metadata_directory",
        "metadata_journal",
        "metadata_objects",
        "metadata_prefetch",
        "metadata_query",
        "metadata_probe",
        "metadata_recovery",
        "metadata_scan",
        "metadata_store",
        "metadata_store_format",
        "metadata_store_io",
        "observe",
        "observe_audit_query",
        "observe_capacity",
        "observe_ledger",
        "observe_recovery",
        "observe_store",
        "observe_timeline",
        "resource_observer",
        "resource_controller",
        "tool_protocol",
        "workflow_lifecycle",
    }
    if names != required_modules:
        raise BudgetError(
            "agent_modules.modules inventory drift: "
            f"missing={sorted(required_modules - names)!r}, "
            f"extra={sorted(names - required_modules)!r}"
        )
    required_cflags = {
        entry["name"]: tuple(entry["required_cflags"])
        for entry in entries
        if "required_cflags" in entry
    }
    if required_cflags != REQUIRED_AGENT_MODULE_CFLAGS:
        raise BudgetError(
            "Agent module optimization policy drift: "
            f"expected={REQUIRED_AGENT_MODULE_CFLAGS!r}, "
            f"actual={required_cflags!r}"
        )
    expected_sources = {
        "background": "os/agent_background.c",
        "facade": "os/agent.c",
        "core": "os/agent_core.c",
        "context": "os/agent_context.c",
        "context_path": "os/agent_context_path.c",
        "durable_section": "os/agent_durable_section.c",
        "file_state": "os/agent_file_state.c",
        "identity": "os/agent_identity.c",
        "identity_lease": "os/agent_identity_lease.c",
        "ipc": "os/agent_ipc.c",
        "lifecycle": "os/agent_lifecycle.c",
        "metadata": "os/agent_metadata.c",
        "metadata_actions": "os/agent_metadata_actions.c",
        "metadata_catalog": "os/agent_metadata_catalog.c",
        "metadata_directory": "os/agent_metadata_directory.c",
        "metadata_journal": "os/agent_metadata_journal.c",
        "metadata_objects": "os/agent_metadata_objects.c",
        "metadata_prefetch": "os/agent_metadata_prefetch.c",
        "metadata_query": "os/agent_metadata_query.c",
        "metadata_probe": "os/agent_metadata_probe.c",
        "metadata_recovery": "os/agent_metadata_recovery.c",
        "metadata_scan": "os/agent_metadata_scan.c",
        "metadata_store": "os/agent_metadata_store.c",
        "metadata_store_format": "os/agent_metadata_store_format.c",
        "metadata_store_io": "os/agent_metadata_store_io.c",
        "observe": "os/agent_observe.c",
        "observe_audit_query": "os/agent_observe_audit_query.c",
        "observe_capacity": "os/agent_observe_capacity.c",
        "observe_ledger": "os/agent_observe_ledger.c",
        "observe_recovery": "os/agent_observe_recovery.c",
        "observe_store": "os/agent_observe_store.c",
        "observe_timeline": "os/agent_observe_timeline.c",
        "resource_observer": "os/agent_resource.c",
        "resource_controller": "os/resource_controller.c",
        "tool_protocol": "os/agent_tool_protocol.c",
        "workflow_lifecycle": "os/workflow_lifecycle.c",
    }
    for entry in entries:
        expected_source = expected_sources[entry["name"]]
        expected_object = "build/" + expected_source.removesuffix(".c") + ".o"
        if (
            entry["source_path"] != expected_source
            or entry["object_path"] != expected_object
        ):
            raise BudgetError(
                f"Agent module {entry['name']} path drift: expected "
                f"{expected_source} and {expected_object}"
            )

    test_only = modules.get("test_only_sources")
    if not isinstance(test_only, list) or len(test_only) != len(
        REQUIRED_TEST_ONLY_SUPPORTS
    ):
        raise BudgetError(
            "agent_modules.test_only_sources must contain the exact profile "
            "owner inventory"
        )
    for index, (raw_support, expected_support) in enumerate(
        zip(test_only, REQUIRED_TEST_ONLY_SUPPORTS)
    ):
        support = require_mapping(
            raw_support, f"agent_modules.test_only_sources[{index}]"
        )
        for field, expected in expected_support.items():
            actual = support.get(field)
            if field == "allowed_profile_symbols" and isinstance(actual, list):
                actual = tuple(actual)
            if actual != expected:
                raise BudgetError(
                    f"profile owner {support.get('name', index)} {field} "
                    f"drift: expected {expected!r}"
                )
        validate_pair(support, "baseline_lines", "max_lines", integer=True)
        if support["source_path"] in paths:
            raise BudgetError(
                "test-only source was registered as a production module"
            )

    production_excludes = GENERATED_KERNEL_SOURCE_EXCLUDES | {
        support["source_path"] for support in test_only
    }
    if (
        len(exclude_paths) != len(production_excludes)
        or set(exclude_paths) != production_excludes
    ):
        raise BudgetError(
            "kernel_source.exclude_paths must contain only generated source "
            "and the exact test-only owner inventory"
        )

    directory = next(entry for entry in entries if entry["name"] == "metadata_directory")
    if directory.get("max_bss_bytes") != 0:
        raise BudgetError("metadata_directory must preserve a zero-BSS ownership boundary")

    validate_agent_aggregate_budgets(modules, names)

    bridges = modules.get("integration_bridges")
    if not isinstance(bridges, list) or not bridges:
        raise BudgetError(
            "agent_modules.integration_bridges must be a non-empty array"
        )
    bridge_names = set()
    bridge_paths = set()
    for index, bridge in enumerate(bridges):
        label = f"agent_modules.integration_bridges[{index}]"
        bridge = require_mapping(bridge, label)
        name = bridge.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]+", name):
            raise BudgetError(f"{label}.name must be a bridge identifier")
        if name in names or name in bridge_names:
            raise BudgetError(f"duplicate Agent integration node name: {name}")
        bridge_names.add(name)
        object_path = bridge.get("object_path")
        if (
            not isinstance(object_path, str)
            or not object_path
            or Path(object_path).is_absolute()
            or ".." in Path(object_path).parts
            or Path(object_path).parent.as_posix() != "build/os"
            or Path(object_path).suffix != ".o"
        ):
            raise BudgetError(
                f"{label}.object_path must be a build/os object path"
            )
        if object_path in object_paths or object_path in bridge_paths:
            raise BudgetError(
                f"duplicate Agent integration object: {object_path}"
            )
        bridge_paths.add(object_path)
        symbols = require_string_array(bridge, "allowed_global_symbols")
        readonly = require_string_array(bridge, "allowed_readonly_symbols")
        dependencies = require_string_array(bridge, "allowed_dependencies")
        if (
            len(set(symbols)) != len(symbols)
            or len(set(readonly)) != len(readonly)
            or len(set(dependencies)) != len(dependencies)
        ):
            raise BudgetError(f"{label} contains duplicate policy entries")
        if any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol)
            or not is_controlled_agent_symbol(symbol)
            for symbol in symbols
        ):
            raise BudgetError(
                f"{label} global symbols must use a controlled namespace"
            )
        if any(symbol not in symbols for symbol in readonly):
            raise BudgetError(
                f"{label} read-only symbols must be exact global symbols"
            )

    for entry in entries:
        unknown = set(entry["allowed_bridge_dependencies"]) - bridge_names
        if unknown:
            raise BudgetError(
                f"Agent module {entry['name']} has unknown integration "
                f"dependencies: {sorted(unknown)!r}"
            )
    integration_names = names | bridge_names
    for bridge in bridges:
        dependencies = set(bridge["allowed_dependencies"])
        unknown = dependencies - integration_names
        if bridge["name"] in dependencies:
            raise BudgetError(
                f"Agent integration bridge {bridge['name']} depends on itself"
            )
        if unknown:
            raise BudgetError(
                f"Agent integration bridge {bridge['name']} has unknown "
                f"dependencies: {sorted(unknown)!r}"
            )

    max_scc_size = modules.get("max_scc_size")
    if (
        isinstance(max_scc_size, bool)
        or not isinstance(max_scc_size, int)
        or max_scc_size < 1
    ):
        raise BudgetError("agent_modules.max_scc_size must be a positive integer")
    if max_scc_size != REQUIRED_AGENT_MAX_SCC_SIZE:
        raise BudgetError(
            "agent_modules.max_scc_size must preserve the architectural "
            f"limit {REQUIRED_AGENT_MAX_SCC_SIZE}"
        )
    allowed_sccs = modules.get("allowed_sccs")
    if not isinstance(allowed_sccs, list):
        raise BudgetError("agent_modules.allowed_sccs must be an array")
    normalized_sccs = []
    members = set()
    for index, component in enumerate(allowed_sccs):
        label = f"agent_modules.allowed_sccs[{index}]"
        if (
            not isinstance(component, list)
            or len(component) < 2
            or any(not isinstance(name, str) for name in component)
            or component != sorted(component)
            or len(set(component)) != len(component)
        ):
            raise BudgetError(
                f"{label} must be a sorted array of distinct module names"
            )
        unknown = set(component) - names
        if unknown:
            raise BudgetError(f"{label} has unknown modules: {sorted(unknown)!r}")
        overlap = members & set(component)
        if overlap:
            raise BudgetError(
                f"{label} overlaps another allowed SCC: {sorted(overlap)!r}"
            )
        members.update(component)
        normalized_sccs.append(frozenset(component))
    if len(set(normalized_sccs)) != len(normalized_sccs):
        raise BudgetError("agent_modules.allowed_sccs contains duplicates")
    if set(normalized_sccs) != REQUIRED_AGENT_ALLOWED_SCCS:
        raise BudgetError(
            "agent_modules.allowed_sccs must match the reviewed "
            "architectural cycles"
        )
    expected_graph = {
        entry["name"]: set(entry["allowed_dependencies"]) for entry in entries
    }
    validate_module_dependency_graph(
        expected_graph,
        expected_graph,
        normalized_sccs,
        max_scc_size,
        "configured Agent module graph",
    )

    integration_max_scc_size = modules.get("integration_max_scc_size")
    if (
        isinstance(integration_max_scc_size, bool)
        or not isinstance(integration_max_scc_size, int)
        or integration_max_scc_size < 1
    ):
        raise BudgetError(
            "agent_modules.integration_max_scc_size must be a positive integer"
        )
    if integration_max_scc_size != REQUIRED_AGENT_MAX_SCC_SIZE:
        raise BudgetError(
            "agent_modules.integration_max_scc_size must preserve the "
            f"architectural limit {REQUIRED_AGENT_MAX_SCC_SIZE}"
        )
    integration_allowed_sccs = modules.get("integration_allowed_sccs")
    if not isinstance(integration_allowed_sccs, list):
        raise BudgetError(
            "agent_modules.integration_allowed_sccs must be an array"
        )
    normalized_integration_sccs = []
    integration_members = set()
    for index, component in enumerate(integration_allowed_sccs):
        label = f"agent_modules.integration_allowed_sccs[{index}]"
        if (
            not isinstance(component, list)
            or len(component) < 2
            or any(not isinstance(name, str) for name in component)
            or component != sorted(component)
            or len(set(component)) != len(component)
        ):
            raise BudgetError(
                f"{label} must be a sorted array of distinct node names"
            )
        unknown = set(component) - integration_names
        if unknown:
            raise BudgetError(
                f"{label} has unknown nodes: {sorted(unknown)!r}"
            )
        overlap = integration_members & set(component)
        if overlap:
            raise BudgetError(
                f"{label} overlaps another allowed SCC: {sorted(overlap)!r}"
            )
        integration_members.update(component)
        normalized_integration_sccs.append(frozenset(component))
    if len(set(normalized_integration_sccs)) != len(
        normalized_integration_sccs
    ):
        raise BudgetError(
            "agent_modules.integration_allowed_sccs contains duplicates"
        )
    if (
        set(normalized_integration_sccs)
        != REQUIRED_AGENT_INTEGRATION_ALLOWED_SCCS
    ):
        raise BudgetError(
            "agent_modules.integration_allowed_sccs must match the "
            "reviewed architectural cycles"
        )
    expected_integration_graph = {
        entry["name"]: set(entry["allowed_dependencies"])
        | set(entry["allowed_bridge_dependencies"])
        for entry in entries
    }
    expected_integration_graph.update(
        {
            bridge["name"]: set(bridge["allowed_dependencies"])
            for bridge in bridges
        }
    )
    validate_module_dependency_graph(
        expected_integration_graph,
        expected_integration_graph,
        normalized_integration_sccs,
        integration_max_scc_size,
        "configured Agent integration graph",
    )

    prefixes = require_string_list(
        modules, "forbidden_core_authority_symbol_prefixes"
    )
    symbols = require_string_array(
        modules, "forbidden_core_authority_symbols"
    )
    allowlist = require_string_array(
        modules, "allowed_core_facade_symbols"
    )
    if (
        len(set(prefixes)) != len(prefixes)
        or len(set(symbols)) != len(symbols)
        or len(set(allowlist)) != len(allowlist)
    ):
        raise BudgetError("agent_modules core symbol policy contains duplicates")
    if any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value)
        for value in prefixes + symbols + allowlist
    ):
        raise BudgetError("agent_modules core symbol policy is not an identifier")
    if any(
        not prefix.endswith("_")
        or not prefix.startswith(("agent_", "sys_"))
        for prefix in prefixes
    ):
        raise BudgetError(
            "agent_modules forbidden prefixes must be Agent namespaces"
        )
    if any(
        not (
            any(symbol.startswith(prefix) for prefix in prefixes)
            or symbol in symbols
        )
        for symbol in allowlist
    ):
        raise BudgetError(
            "agent_modules facade allowlist must name forbidden authority"
        )


def check_agent_test_calibration_evidence(
    root, config, expected_source_inputs=None
):
    tests = config["agent_test_suite"]
    if tests["calibration_status"] != AGENT_TEST_CALIBRATION_READY:
        return

    calibration_path = Path(__file__).with_name("agent_test_calibration.py")
    spec = importlib.util.spec_from_file_location(
        "agentos_test_calibration_contract", calibration_path
    )
    if spec is None or spec.loader is None:
        raise BudgetError("cannot load Agent calibration contract")
    calibration = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(calibration)
        calibration.verify_calibration_package(
            root, tests, expected_source_inputs, config
        )
    except (OSError, ValueError) as error:
        raise BudgetError(f"Agent calibration evidence rejected: {error}") from error
    print(
        "[kernel-budget] Agent calibration evidence: "
        "local E3, commit/tree/attestations verified"
    )
    return

def load_config(path):
    def reject_constant(value):
        raise BudgetError(f"non-finite JSON number is forbidden: {value}")

    try:
        with Path(path).open(encoding="utf-8") as stream:
            config = json.load(stream, parse_constant=reject_constant)
    except (OSError, json.JSONDecodeError) as error:
        raise BudgetError(f"cannot load budget config: {error}") from error
    validate_config(config)
    return config


def physical_line_count(data):
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def measure_source_lines(root, include_globs, exclude_paths):
    root = Path(root).resolve()
    excluded = {Path(path).as_posix() for path in exclude_paths}
    paths = set()
    for pattern in include_globs:
        for path in root.glob(pattern):
            relative = path.relative_to(root).as_posix()
            if path.is_file() and relative not in excluded:
                paths.add(path)
    if not paths:
        raise BudgetError("kernel source globs matched no files")
    total = 0
    for path in sorted(paths):
        try:
            total += physical_line_count(path.read_bytes())
        except OSError as error:
            raise BudgetError(f"cannot read kernel source {path}: {error}") from error
    return total, len(paths)


def measure_file_source(root, relative_path):
    path = (Path(root).resolve() / relative_path).resolve()
    try:
        path.relative_to(Path(root).resolve())
    except ValueError as error:
        raise BudgetError(f"source path escapes repository: {relative_path}") from error
    try:
        data = path.read_bytes()
    except OSError as error:
        raise BudgetError(f"cannot read source {path}: {error}") from error
    # Git content is LF-normalized; checkout policy must not consume budget.
    normalized = data.replace(b"\r\n", b"\n")
    return physical_line_count(normalized), len(normalized)


def measure_file_lines(root, relative_path):
    return measure_file_source(root, relative_path)[0]


def read_agent_timing_file(path, expected_cases):
    try:
        records = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) != 2:
                raise ValueError(f"invalid timing row: {line!r}")
            records.append((fields[0], float(fields[1])))
    except (OSError, ValueError) as error:
        raise BudgetError(f"cannot read Agent timing file: {error}") from error
    if not records:
        raise BudgetError("Agent timing file is empty")
    if any(not math.isfinite(value) or value <= 0 for _, value in records):
        raise BudgetError("Agent timing rows must be finite and positive")
    actual_cases = [name for name, _ in records]
    if actual_cases != expected_cases:
        raise BudgetError(
            f"Agent timing cases {actual_cases!r}, expected {expected_cases!r}"
        )
    return records, sum(value for _, value in records)


def native_tool_argument(path):
    path = Path(path).resolve()
    cwd = Path.cwd().resolve()
    try:
        relative = path.relative_to(cwd)
    except ValueError:
        return str(path)
    return relative.as_posix() or "."


def run_tool(command, description):
    try:
        result = subprocess.run(
            command, check=False, capture_output=True
        )
    except OSError as error:
        raise BudgetError(f"cannot run {description}: {error}") from error
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        details = stderr.strip() or stdout.strip()
        raise BudgetError(f"{description} failed: {details}")
    return stdout


def measure_kernel_images(kernel, objcopy):
    kernel = Path(kernel)
    if not kernel.is_file():
        raise BudgetError(f"kernel ELF is missing: {kernel}")
    with tempfile.TemporaryDirectory(prefix="agentos-kernel-budget-") as temp:
        temp_dir = Path(temp)
        stripped = temp_dir / "kernel.stripped.elf"
        raw = temp_dir / "kernel.bin"
        kernel_arg = native_tool_argument(kernel)
        run_tool(
            [
                objcopy,
                "--strip-all",
                "--remove-section=.comment",
                "--remove-section=.note.gnu.build-id",
                kernel_arg,
                str(stripped),
            ],
            "ELF normalization",
        )
        run_tool(
            [objcopy, "-O", "binary", kernel_arg, str(raw)],
            "raw kernel extraction",
        )
        return stripped.stat().st_size, raw.stat().st_size


def parse_size_output(output):
    matches = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 6:
            continue
        try:
            text_size, data_size, bss_size, total_size = (
                int(value, 10) for value in fields[:4]
            )
        except ValueError:
            continue
        if text_size + data_size + bss_size != total_size:
            raise BudgetError("target size output has an inconsistent total")
        matches.append((text_size, data_size, bss_size, total_size))
    if len(matches) != 1:
        raise BudgetError(f"expected one target size row, found {len(matches)}")
    return matches[0]


def measure_kernel_runtime(kernel, size):
    output = run_tool(
        [size, "-B", native_tool_argument(kernel)],
        "kernel runtime size inspection",
    )
    return parse_size_output(output)


def parse_object_size_sections(output):
    sections = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 3 or not fields[0].startswith("."):
            continue
        try:
            section_size = int(fields[1], 10)
            int(fields[2], 0)
        except ValueError:
            continue
        if fields[0] in sections:
            raise BudgetError(f"duplicate object section in size output: {fields[0]}")
        sections[fields[0]] = section_size
    if ".text" not in sections:
        raise BudgetError("object size output is missing .text")
    return sections


def agent_aggregate_private_header(path):
    return (
        path.startswith("os/agent")
        and path.endswith(".h")
        and path not in REQUIRED_AGENT_AGGREGATE_SHARED_HEADERS
    )


def quoted_include_closure(root, source_paths):
    root = Path(root).resolve()
    pending = []
    for source_path in source_paths:
        path = (root / source_path).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise BudgetError(f"aggregate source escapes repository: {source_path}") from error
        if not path.is_file():
            raise BudgetError(f"aggregate source is missing: {source_path}")
        pending.append(path)

    closure = set()
    include_pattern = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)
    while pending:
        path = pending.pop()
        relative = path.relative_to(root).as_posix()
        if relative in closure:
            continue
        closure.add(relative)
        text = path.read_text(encoding="utf-8")
        for include in include_pattern.findall(text):
            candidates = (path.parent / include, root / include)
            resolved = next(
                (candidate.resolve() for candidate in candidates if candidate.is_file()),
                None,
            )
            if resolved is None:
                continue
            try:
                resolved.relative_to(root)
            except ValueError as error:
                raise BudgetError(
                    f"quoted include escapes repository: {relative}: {include}"
                ) from error
            if resolved.relative_to(root).as_posix() not in closure:
                pending.append(resolved)
    return closure


def validate_agent_aggregate_header_inventory(root, entries, group):
    root = Path(root).resolve()
    registered = set(group["contract_headers"])
    discovered = set()
    for pattern in group.get("contract_header_globs", ()):
        discovered.update(
            path.relative_to(root).as_posix()
            for path in root.glob(pattern)
            if path.is_file()
        )
    if discovered != registered:
        raise BudgetError(
            f"Agent aggregate {group['name']} private header inventory drift: "
            f"unregistered={sorted(discovered - registered)!r}, "
            f"stale={sorted(registered - discovered)!r}"
        )

    by_name = {entry["name"]: entry for entry in entries}
    seeds = [by_name[member]["source_path"] for member in group["members"]]
    seeds.extend(group["contract_headers"])
    closure = quoted_include_closure(root, seeds)
    unregistered = sorted(
        path
        for path in closure
        if agent_aggregate_private_header(path) and path not in registered
    )
    if unregistered:
        raise BudgetError(
            f"Agent aggregate {group['name']} include closure has unregistered "
            f"private headers: {unregistered!r}"
        )


def validate_agent_discarded_sections(root, expected):
    linker = Path(root).resolve() / "os" / "kernel.ld"
    if not linker.is_file():
        raise BudgetError(f"kernel linker script is missing: {linker}")
    text = linker.read_text(encoding="utf-8")
    blocks = re.findall(r"/DISCARD/\s*:\s*\{(.*?)\}", text, re.DOTALL)
    if len(blocks) != 1:
        raise BudgetError("kernel linker script must have one /DISCARD/ block")
    discarded = set(re.findall(r"\*\(\s*(\.[A-Za-z0-9_.-]+)\s*\)", blocks[0]))
    if discarded != set(expected):
        raise BudgetError(
            "Agent aggregate discarded section inventory does not match kernel.ld: "
            f"expected={sorted(expected)!r}, actual={sorted(discarded)!r}"
        )


def measure_agent_object_residency(size_tool, object_path, discarded_sections):
    object_arg = native_tool_argument(object_path)
    totals = parse_size_output(
        run_tool(
            [size_tool, "-B", object_arg],
            "Agent aggregate object residency inspection",
        )
    )
    text_size, data_size, bss_size, _ = totals
    if data_size != 0:
        raise BudgetError(
            f"Agent aggregate object has initialized writable data: {object_path} "
            f"({data_size} bytes)"
        )
    sections = parse_object_size_sections(
        run_tool(
            [size_tool, "-A", object_arg],
            "Agent aggregate discarded section inspection",
        )
    )
    discarded_size = sum(sections.get(name, 0) for name in discarded_sections)
    if discarded_size > text_size:
        raise BudgetError(f"discarded sections exceed object text size: {object_path}")
    return text_size - discarded_size, bss_size


def resolve_agent_object_path(root, object_dir, configured_path):
    root = Path(root).resolve()
    configured = Path(configured_path)
    if (
        configured.is_absolute()
        or ".." in configured.parts
        or configured.parent.as_posix() != "build/os"
        or configured.suffix != ".o"
    ):
        raise BudgetError(
            f"Agent object identity must be a build/os object: {configured_path}"
        )
    directory = Path(object_dir)
    if not directory.is_absolute():
        directory = root / directory
    directory = directory.resolve()
    try:
        directory.relative_to(root)
    except ValueError as error:
        raise BudgetError(f"Agent object directory escapes repository: {object_dir}") from error
    return directory / configured.name


def check_agent_aggregate_budgets(
    root, entries, groups, size_tool, object_dir="build/os"
):
    if not size_tool:
        raise BudgetError("--size is required for Agent aggregate budgets")
    by_name = {entry["name"]: entry for entry in entries}
    for group in groups:
        validate_agent_aggregate_header_inventory(root, entries, group)
        validate_agent_discarded_sections(root, group["discarded_sections"])
        source_paths = list(group["contract_headers"])
        source_paths.extend(
            by_name[member]["source_path"] for member in group["members"]
        )
        source_measurements = [
            measure_file_source(root, path) for path in source_paths
        ]
        source_lines = sum(lines for lines, _ in source_measurements)
        source_bytes = sum(size for _, size in source_measurements)
        loaded_text = 0
        bss = 0
        for member in group["members"]:
            entry = by_name[member]
            object_path = resolve_agent_object_path(
                root, object_dir, entry["object_path"]
            )
            if not object_path.is_file():
                raise BudgetError(f"Agent aggregate object is missing: {object_path}")
            object_loaded, object_bss = measure_agent_object_residency(
                size_tool,
                object_path,
                group["discarded_sections"],
            )
            loaded_text += object_loaded
            bss += object_bss
        for metric, actual, unit in (
            ("source_lines", source_lines, " lines"),
            ("source_bytes", source_bytes, " bytes"),
            ("loaded_text_bytes", loaded_text, " bytes"),
            ("bss_bytes", bss, " bytes"),
        ):
            check_limit(
                f"Agent aggregate {group['name']} {metric}",
                actual,
                group[f"baseline_{metric}"],
                group[f"max_{metric}"],
                unit,
            )


def parse_nm_symbol_size(output, symbol):
    matches = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[-1] == symbol:
            try:
                matches.append(int(fields[-3], 16))
            except ValueError as error:
                raise BudgetError(f"invalid nm size for {symbol}") from error
    if len(matches) != 1:
        raise BudgetError(f"expected one {symbol} symbol, found {len(matches)}")
    return matches[0]


def parse_nm_symbol_address(output, symbol):
    matches = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-1] == symbol:
            try:
                matches.append(int(fields[0], 16))
            except ValueError as error:
                raise BudgetError(f"invalid nm address for {symbol}") from error
    if len(matches) != 1:
        raise BudgetError(f"expected one {symbol} symbol, found {len(matches)}")
    return matches[0]


def parse_nm_defined_symbols(output):
    return set(parse_nm_defined_records(output))


def parse_nm_defined_records(output):
    symbols = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 3 and len(fields[-2]) == 1:
            symbols[fields[-1]] = fields[-2]
    return symbols


def forbidden_symbols(symbols, prefixes, allowlist):
    allowed = set(allowlist)
    return sorted(
        symbol
        for symbol in symbols
        if any(symbol.startswith(prefix) for prefix in prefixes)
        and symbol not in allowed
    )


def parse_nm_undefined_symbols(output):
    return {
        fields[-1]
        for line in output.splitlines()
        if (fields := line.split())
    }


def symbol_allowed(symbol, prefixes, exact_symbols):
    return symbol in exact_symbols or any(
        symbol.startswith(prefix) for prefix in prefixes
    )


def invalid_global_object_exports(records, allowed_readonly):
    readonly = set(allowed_readonly)
    return sorted(
        f"{symbol} ({kind})"
        for symbol, kind in records.items()
        if kind.upper() not in ("T", "W")
        and not (kind.upper() == "R" and symbol in readonly)
    )


def test_only_symbol_leaks(symbols, supports):
    profile_symbols = {
        symbol
        for support in supports
        for symbol in support["allowed_profile_symbols"]
    }
    return sorted(profile_symbols & set(symbols))


def is_controlled_agent_symbol(symbol):
    return (
        symbol in CONTROLLED_AGENT_EXACT_SYMBOLS
        or symbol.startswith(CONTROLLED_AGENT_SYMBOL_PREFIXES)
    )


def controlled_defined_records(records):
    return {
        symbol: kind
        for symbol, kind in records.items()
        if is_controlled_agent_symbol(symbol)
    }


def controlled_undefined_symbols(symbols):
    return {symbol for symbol in symbols if is_controlled_agent_symbol(symbol)}


def validate_integration_bridge_exports(
    name, records, allowed_symbols, allowed_readonly=()
):
    controlled = controlled_defined_records(records)
    writable = invalid_global_object_exports(controlled, allowed_readonly)
    if writable:
        raise BudgetError(
            f"Agent integration bridge {name} exports writable controlled "
            "data: " + ", ".join(writable)
        )
    actual = set(controlled)
    expected = set(allowed_symbols)
    if actual != expected:
        raise BudgetError(
            f"Agent integration bridge {name} controlled export drift: "
            f"missing={sorted(expected - actual)!r}, "
            f"unexpected={sorted(actual - expected)!r}"
        )
    return actual


def validate_integration_bridge_inventory(discovered_paths, configured_paths):
    discovered = set(discovered_paths)
    configured = set(configured_paths)
    unregistered = sorted(discovered - configured)
    stale = sorted(configured - discovered)
    if unregistered or stale:
        raise BudgetError(
            "Agent integration bridge inventory drift: "
            f"unregistered={unregistered!r}, stale={stale!r}"
        )


def build_controlled_dependency_graph(defined_by_node, undefined_by_node):
    if set(defined_by_node) != set(undefined_by_node):
        raise BudgetError("controlled dependency graph node inventory drift")
    symbol_owner = {}
    for node, symbols in defined_by_node.items():
        for symbol in symbols:
            if not is_controlled_agent_symbol(symbol):
                continue
            previous = symbol_owner.get(symbol)
            if previous is not None:
                raise BudgetError(
                    f"controlled Agent symbol {symbol} is owned by both "
                    f"{previous} and {node}"
                )
            symbol_owner[symbol] = node
    graph = {}
    for node, symbols in undefined_by_node.items():
        dependencies = set()
        for symbol in controlled_undefined_symbols(symbols):
            owner = symbol_owner.get(symbol)
            if owner is None:
                raise BudgetError(
                    f"controlled Agent symbol {symbol} referenced by {node} "
                    "has no registered owner"
                )
            if owner != node:
                dependencies.add(owner)
        graph[node] = dependencies
    return graph


def strongly_connected_components(graph):
    index = 0
    indices = {}
    lowlinks = {}
    stack = []
    on_stack = set()
    components = []

    def visit(node):
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(graph[node]):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        component = set()
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.add(member)
            if member == node:
                break
        components.append(frozenset(component))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return components


def validate_module_dependency_graph(
    actual, expected, allowed_sccs, max_scc_size, label
):
    if set(actual) != set(expected):
        raise BudgetError(f"{label} module inventory does not match policy")
    for name in sorted(expected):
        if set(actual[name]) != set(expected[name]):
            missing = sorted(set(expected[name]) - set(actual[name]))
            stale = sorted(set(actual[name]) - set(expected[name]))
            raise BudgetError(
                f"{label} dependency drift for {name}: "
                f"missing={missing!r}, unexpected={stale!r}"
            )
    cyclic = {
        component
        for component in strongly_connected_components(actual)
        if len(component) > 1
    }
    oversized = sorted(
        sorted(component)
        for component in cyclic
        if len(component) > max_scc_size
    )
    if oversized:
        raise BudgetError(
            f"{label} exceeds max_scc_size={max_scc_size}: {oversized!r}"
        )
    allowed = set(allowed_sccs)
    if cyclic != allowed:
        raise BudgetError(
            f"{label} cyclic dependency drift: "
            f"actual={sorted(sorted(c) for c in cyclic)!r}, "
            f"allowed={sorted(sorted(c) for c in allowed)!r}"
        )


def measure_probe_symbol(probe, nm, symbol):
    probe = Path(probe)
    if not probe.is_file():
        raise BudgetError(f"kernel layout probe is missing: {probe}")
    output = run_tool(
        [nm, "-S", "--defined-only", native_tool_argument(probe)],
        "kernel layout probe inspection",
    )
    return parse_nm_symbol_size(output, symbol)


def measure_kernel_symbol_span(kernel, nm, start_symbol, end_symbol):
    kernel = Path(kernel)
    if not kernel.is_file():
        raise BudgetError(f"kernel ELF is missing: {kernel}")
    output = run_tool(
        [nm, "-n", "--defined-only", native_tool_argument(kernel)],
        "kernel symbol boundary inspection",
    )
    start = parse_nm_symbol_address(output, start_symbol)
    end = parse_nm_symbol_address(output, end_symbol)
    if end <= start:
        raise BudgetError(
            f"kernel symbol span is invalid: {start_symbol}={start:#x}, "
            f"{end_symbol}={end:#x}"
        )
    return end - start


def measure_boot_entry_target(entry_source):
    try:
        text = Path(entry_source).read_text(encoding="utf-8")
    except OSError as error:
        raise BudgetError(f"cannot read boot entry source: {error}") from error
    targets = re.findall(
        r"^[ \t]*call[ \t]+([A-Za-z_][A-Za-z0-9_]*)"
        r"(?:[ \t]*(?:#.*)?)?$",
        text,
        flags=re.MULTILINE,
    )
    if len(targets) != 1:
        raise BudgetError(
            f"expected one direct boot entry call, found {len(targets)}"
        )
    return targets[0]


def check_limit(name, actual, baseline, maximum, unit, ratchet=False):
    def display(value):
        if isinstance(value, int):
            return str(value)
        return f"{value:.3f}".rstrip("0").rstrip(".")

    for value in (actual, baseline, maximum):
        if isinstance(value, float) and not math.isfinite(value):
            raise BudgetError(f"{name} contains a non-finite measurement")

    print(
        f"[kernel-budget] {name}: actual={display(actual)}{unit} "
        f"baseline={display(baseline)}{unit} limit={display(maximum)}{unit}"
    )
    if actual > maximum:
        raise BudgetError(
            f"{name} exceeded: {display(actual)}{unit} > "
            f"{display(maximum)}{unit}"
        )
    if ratchet and actual < baseline * 0.98:
        raise BudgetError(
            f"{name} shrank materially: {display(actual)}{unit} < "
            f"98% of baseline {display(baseline)}{unit}; "
            "tighten the baseline and limit"
        )


def read_stack_build_config(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise BudgetError(f"cannot read kernel stack build config: {error}") from error
    actual = {}
    for line in lines:
        if "=" in line:
            name, value = line.split("=", 1)
            actual[name] = value
    return actual


def validate_stack_build_config(path, stack):
    actual = read_stack_build_config(path)
    expected = {
        "KSTACK_SIZE_BYTES": str(stack["stack_size_bytes"]),
        "KSTACK_BOOT_SIZE_BYTES": str(stack["boot_stack_size_bytes"]),
        "KSTACK_BOOT_ROOT": stack["boot_root"],
        "KSTACK_GUARD_SIZE_BYTES": str(stack["guard_size_bytes"]),
        "KSTACK_SAFETY_MARGIN": str(stack["safety_margin_bytes"]),
        "KERNELVEC_FRAME_SIZE_BYTES": str(stack["interrupt_entry_bytes"]),
        "KSTACK_STACK_BOUNDARIES": " ".join(stack["stack_boundaries"]),
        "KSTACK_INDIRECT_CALLERS": " ".join(
            stack["allowed_indirect_callers"]
        ),
        "KSTACK_INDIRECT_CALL_EDGES": " ".join(
            stack["indirect_call_edges"]
        ),
        "KSTACK_RECURSION_BOUNDS": " ".join(stack["recursion_bounds"]),
    }
    mismatches = [
        f"{name}={actual.get(name)!r}, expected {value!r}"
        for name, value in expected.items()
        if actual.get(name) != value
    ]
    if mismatches:
        raise BudgetError("kernel stack build config drift: " + "; ".join(mismatches))


def validate_canonical_defines(cflags, config, expected_log):
    tokens = shlex.split(cflags)
    defines = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-D":
            index += 1
            if index >= len(tokens):
                raise BudgetError("kernel CFLAGS ends with an incomplete -D")
            defines.add(tokens[index])
        elif token.startswith("-D"):
            defines.add(token[2:])
        index += 1
    allowed_defines = {
        expected_log,
        f"KSTACK_SIZE={config['kernel_stack']['stack_size_bytes']}",
        f"KSTACK_GUARD_SIZE={config['kernel_stack']['guard_size_bytes']}",
        "KERNELVEC_FRAME_SIZE="
        f"{config['kernel_stack']['interrupt_entry_bytes']}",
    }
    if defines != allowed_defines:
        raise BudgetError(
            f"non-canonical kernel preprocessor defines: {sorted(defines)!r}, "
            f"expected {sorted(allowed_defines)!r}"
        )


def normalized_tool_name(path):
    name = Path(path).name
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name


def resolve_executable_once(path, description):
    requested = str(path)
    windows_drive = re.fullmatch(r"([A-Za-z]):[\\/](.*)", requested)
    if os.name == "posix" and windows_drive is not None:
        converted = run_tool(
            ["cygpath", "-u", "-a", requested],
            f"{description} MSYS path conversion",
        ).strip()
        if not converted or "\n" in converted or "\r" in converted:
            raise BudgetError(f"cannot convert {description} to an MSYS path")
        candidate = Path(converted)
    else:
        candidate = Path(requested)
    if not candidate.is_absolute() and candidate.parent == Path("."):
        resolved = shutil.which(requested)
        if resolved is None:
            raise BudgetError(f"cannot resolve {description}: {requested}")
        candidate = Path(resolved)
    try:
        candidate = candidate.resolve(strict=True)
    except OSError as error:
        raise BudgetError(f"cannot resolve {description}: {error}") from error
    if not candidate.is_file():
        raise BudgetError(f"{description} is not a regular file: {candidate}")
    return candidate


def sha256_file(path, description):
    path = Path(path)
    if not path.is_file():
        raise BudgetError(f"{description} is not a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise BudgetError(f"cannot hash {description}: {error}") from error
    return digest.hexdigest()


def select_kernel_budget_toolchain(config, tools):
    suffixes = {
        "gcc": "gcc",
        "ld": "ld",
        "objcopy": "objcopy",
        "objdump": "objdump",
        "nm": "nm",
        "size": "size",
    }
    canonical = config["canonical_toolchain"]
    profiles = [("canonical", canonical)] + [
        ("local", profile)
        for profile in config["local_kernel_budget_toolchains"]
    ]
    matches = []
    for kind, profile in profiles:
        if all(
            normalized_tool_name(tools[name]) == profile["prefix"] + suffix
            for name, suffix in suffixes.items()
        ):
            matches.append((kind, profile))
    if len(matches) != 1:
        expected = ", ".join(profile["prefix"] for _, profile in profiles)
        raise BudgetError(
            "kernel budget toolchain identity does not match exactly one "
            f"approved prefix ({expected})"
        )
    return matches[0]


def validate_binutils_version(tool, expected, description):
    first_line = run_tool([tool, "--version"], description).splitlines()
    if not first_line:
        raise BudgetError(f"{description} output is empty")
    version_match = re.search(r"([0-9]+(?:\.[0-9]+)+)\s*$", first_line[0])
    version = version_match.group(1) if version_match else ""
    if version != expected:
        raise BudgetError(
            f"{description} version {version!r}, expected {expected!r}"
        )


def resolve_gcc_subprogram(gcc, name):
    output = run_tool(
        [str(gcc), f"-print-prog-name={name}"],
        f"GCC {name} path discovery",
    )
    reported = output.strip()
    if not reported or "\n" in reported or "\r" in reported:
        raise BudgetError(f"GCC {name} path discovery returned an invalid path")
    candidate = Path(reported)
    windows_drive = re.fullmatch(r"[A-Za-z]:[\\/].*", reported)
    if (
        windows_drive is None
        and not candidate.is_absolute()
        and candidate.parent != Path(".")
    ):
        candidate = Path(gcc).parent / candidate
    return resolve_executable_once(candidate, f"GCC {name} subprogram")


def attest_local_kernel_budget_tools(profile, tools):
    for name, tool in tools.items():
        actual = sha256_file(tool, f"local kernel budget {name}")
        expected = profile["executable_sha256"][name]
        if actual != expected:
            raise BudgetError(
                f"local kernel budget {name} SHA-256 differs: "
                f"expected={expected}, actual={actual}"
            )


def dpkg_tool_owner(path, description):
    output = run_tool(
        ["dpkg-query", "-S", str(path)], f"{description} package ownership"
    )
    owners = set()
    for line in output.splitlines():
        if ": " not in line:
            continue
        package = line.split(": ", 1)[0].split(":", 1)[0]
        if package:
            owners.add(package)
    if not owners:
        raise BudgetError(f"{description} has no dpkg package owner")
    return owners


def validate_canonical_kernel_budget_tools(profile, tools):
    suffixes = {
        "gcc": "gcc",
        "ld": "ld",
        "objcopy": "objcopy",
        "objdump": "objdump",
        "nm": "nm",
        "size": "size",
    }
    for name, suffix in suffixes.items():
        expected = resolve_executable_once(
            Path("/usr/bin") / f"{profile['prefix']}{suffix}",
            f"canonical {name}",
        )
        if tools[name] != expected:
            raise BudgetError(
                f"canonical {name} is not the approved /usr/bin executable"
            )

    ownership = {
        "gcc": profile["gcc_package"],
        "cc1": profile["gcc_package"],
        "as": profile["binutils_package"],
        "ld": profile["binutils_package"],
        "objcopy": profile["binutils_package"],
        "objdump": profile["binutils_package"],
        "nm": profile["binutils_package"],
        "size": profile["binutils_package"],
    }
    for name, package in ownership.items():
        owners = dpkg_tool_owner(tools[name], f"canonical {name}")
        if package not in owners:
            raise BudgetError(
                f"canonical {name} is owned by {sorted(owners)!r}, "
                f"expected {package!r}"
            )
    for package in (profile["gcc_package"], profile["binutils_package"]):
        verification = run_tool(
            ["dpkg", "--verify", package], f"{package} package integrity"
        )
        if verification.strip():
            raise BudgetError(
                f"{package} package integrity verification reported drift"
            )


def validate_kernel_budget_toolchain(
    config, cc, ld, objcopy, objdump, nm, size, build_config, initproc
):
    canonical = config["canonical_toolchain"]
    requested_tools = {
        "gcc": cc,
        "ld": ld,
        "objcopy": objcopy,
        "objdump": objdump,
        "nm": nm,
        "size": size,
    }
    tools = {
        name: resolve_executable_once(tool, f"kernel budget {name}")
        for name, tool in requested_tools.items()
    }
    profile_kind, profile = select_kernel_budget_toolchain(config, tools)
    tools["cc1"] = resolve_gcc_subprogram(tools["gcc"], "cc1")
    tools["as"] = resolve_gcc_subprogram(tools["gcc"], "as")
    if profile_kind == "local":
        attest_local_kernel_budget_tools(profile, tools)
    gcc_version = run_tool(
        [str(tools["gcc"]), "-dumpfullversion", "-dumpversion"],
        "GCC version check",
    ).strip()
    if gcc_version != profile["gcc_version"]:
        raise BudgetError(
            f"GCC version {gcc_version!r}, expected {profile['gcc_version']!r}"
        )
    for name in ("as", "ld", "objcopy", "objdump", "nm", "size"):
        validate_binutils_version(
            str(tools[name]),
            profile["binutils_version"],
            f"{name} version check",
        )
    if profile_kind == "canonical":
        gcc_package_version = run_tool(
            [
                "dpkg-query",
                "-W",
                "-f=${Version}",
                canonical["gcc_package"],
            ],
            "GCC package version check",
        ).strip()
        if gcc_package_version != canonical["gcc_package_version"]:
            raise BudgetError(
                f"GCC package version {gcc_package_version!r}, "
                f"expected {canonical['gcc_package_version']!r}"
            )
        binutils_package_version = run_tool(
            [
                "dpkg-query",
                "-W",
                "-f=${Version}",
                canonical["binutils_package"],
            ],
            "binutils package version check",
        ).strip()
        if binutils_package_version != canonical["binutils_package_version"]:
            raise BudgetError(
                f"binutils package version {binutils_package_version!r}, "
                f"expected {canonical['binutils_package_version']!r}"
            )
        validate_canonical_kernel_budget_tools(canonical, tools)
    profile_id = profile["profile_id"]
    print(f"[kernel-budget] toolchain profile: {profile_id}")

    actual_build = read_stack_build_config(build_config)
    for name, field in (
        ("gcc", "CC"),
        ("cc1", "CC1"),
        ("as", "AS_SUBPROGRAM"),
        ("ld", "LD"),
        ("objdump", "OBJDUMP"),
    ):
        recorded = actual_build.get(field)
        if not recorded:
            raise BudgetError(f"kernel build config omits {field}")
        recorded_path = resolve_executable_once(
            recorded, f"kernel build config {field}"
        )
        if recorded_path != tools[name]:
            raise BudgetError(
                f"kernel build {field} differs from the attested {name}"
            )
    actual_cflags = shlex.split(actual_build.get("CFLAGS", ""))
    if actual_cflags != canonical["cflags"]:
        raise BudgetError(
            f"kernel CFLAGS drift: {actual_cflags!r}, "
            f"expected {canonical['cflags']!r}"
        )
    actual_ldflags = shlex.split(actual_build.get("LDFLAGS", ""))
    if actual_ldflags != canonical["ldflags"]:
        raise BudgetError(
            f"kernel LDFLAGS drift: {actual_ldflags!r}, "
            f"expected {canonical['ldflags']!r}"
        )
    expected_log = f"LOG_LEVEL_{canonical['log_level']}"
    if expected_log not in actual_build.get("CFLAGS", "").split():
        raise BudgetError(f"kernel was not built with {expected_log}")
    validate_canonical_defines(
        actual_build.get("CFLAGS", ""), config, expected_log
    )
    try:
        initproc_text = Path(initproc).read_text(encoding="utf-8")
    except OSError as error:
        raise BudgetError(f"cannot read generated init process: {error}") from error
    expected_init = f'.string "{canonical["init_proc"]}"'
    if expected_init not in initproc_text:
        raise BudgetError(
            f"kernel was not built with {canonical['init_proc']} init process"
        )
    return profile_kind, profile, tools


def production_translation_units(root, config):
    source_dir = Path(root) / "os"
    all_units = {path.stem for path in source_dir.glob("*.c")}
    test_units = {
        Path(support["source_path"]).stem
        for support in config["agent_modules"]["test_only_sources"]
    }
    missing = sorted(test_units - all_units)
    if missing:
        raise BudgetError(
            "test-only translation-unit source is missing: "
            + ", ".join(missing)
        )
    production = sorted(all_units - test_units)
    if not production:
        raise BudgetError("production translation-unit inventory is empty")
    return production


def stack_check_command(
    root, config, callgraph_dir, checker, translation_units=()
):
    stack = config["kernel_stack"]
    command = [
        sys.executable,
        str(root / checker),
        "--callgraph-dir",
        str(root / callgraph_dir),
        "--source-dir",
        str(root / "os"),
        "--stack-size",
        str(stack["stack_size_bytes"]),
        "--guard-size",
        str(stack["guard_size_bytes"]),
        "--safety-margin",
        str(stack["safety_margin_bytes"]),
        "--interrupt-entry",
        str(stack["interrupt_entry_bytes"]),
        "--required-limit",
        str(stack["max_required_bytes"]),
        "--required-baseline",
        str(stack["baseline_required_bytes"]),
        "--boot-root",
        stack["boot_root"],
        "--boot-stack-size",
        str(stack["boot_stack_size_bytes"]),
        "--boot-required-limit",
        str(stack["max_boot_required_bytes"]),
        "--boot-required-baseline",
        str(stack["baseline_boot_required_bytes"]),
    ]
    for boundary in stack["stack_boundaries"]:
        command.extend(("--stack-boundary", boundary))
    for caller in stack["allowed_indirect_callers"]:
        command.extend(("--allow-indirect-from", caller))
    for edge in stack["indirect_call_edges"]:
        command.extend(("--indirect-call-edge", edge))
    for bound in stack["recursion_bounds"]:
        command.extend(("--recursion-bound", bound))
    for unit in translation_units:
        command.extend(("--translation-unit", unit))
    return command


def check_kernel(args, config):
    if not all(
        (args.cc, args.ld, args.objcopy, args.objdump, args.nm, args.size)
    ):
        raise BudgetError(
            "--cc, --ld, --objcopy, --objdump, --nm, and --size are required"
        )
    root = Path(args.root).resolve()
    build_config = root / args.stack_build_config
    profile_kind, profile, tools = validate_kernel_budget_toolchain(
        config,
        args.cc,
        args.ld,
        args.objcopy,
        args.objdump,
        args.nm,
        args.size,
        build_config,
        root / "os" / "initproc.S",
    )
    source = config["kernel_source"]
    lines, file_count = measure_source_lines(
        root, source["include_globs"], source["exclude_paths"]
    )
    check_limit(
        f"kernel source ({file_count} files)",
        lines,
        source["baseline_lines"],
        source["max_lines"],
        " lines",
        ratchet=True,
    )

    image = config["kernel_image"]
    stripped_size, raw_size = measure_kernel_images(
        root / args.kernel, str(tools["objcopy"])
    )
    check_limit(
        "stripped kernel ELF",
        stripped_size,
        image["baseline_stripped_elf_bytes"],
        image["max_stripped_elf_bytes"],
        " bytes",
        ratchet=True,
    )
    check_limit(
        "raw kernel image",
        raw_size,
        image["baseline_raw_binary_bytes"],
        image["max_raw_binary_bytes"],
        " bytes",
        ratchet=True,
    )

    runtime = config["kernel_runtime"]
    text_size, data_size, bss_size, total_size = measure_kernel_runtime(
        root / args.kernel, str(tools["size"])
    )
    for name, actual in (
        ("text", text_size),
        ("data", data_size),
        ("bss", bss_size),
        ("total", total_size),
    ):
        check_limit(
            f"kernel runtime {name}",
            actual,
            runtime[f"baseline_{name}_bytes"],
            runtime[f"max_{name}_bytes"],
            " bytes",
            ratchet=True,
        )

    proc = config["struct_proc"]
    proc_size = measure_probe_symbol(
        root / args.struct_probe, str(tools["nm"]), proc["symbol"]
    )
    check_limit(
        "struct proc",
        proc_size,
        proc["baseline_bytes"],
        proc["max_bytes"],
        " bytes",
        ratchet=True,
    )

    trapframes = config["trapframe_pages"]
    for name, label in (
        ("per_thread", "trapframe per admitted thread"),
        ("admitted_pool", "trapframe admitted-thread capacity"),
        ("reserved_pool", "trapframe reserved-thread capacity"),
    ):
        actual = measure_probe_symbol(
            root / args.struct_probe,
            str(tools["nm"]),
            trapframes[f"{name}_symbol"],
        )
        check_limit(
            label,
            actual,
            trapframes[f"baseline_{name}_bytes"],
            trapframes[f"max_{name}_bytes"],
            " bytes",
            ratchet=True,
        )

    legacy_mail = config["legacy_mail_sidecar"]
    for name, label in (
        ("per_process", "legacy mail sidecar per process"),
        ("pool", "legacy mail sidecar global pool"),
        ("ordinary_pool", "legacy mail sidecar ordinary pool"),
        ("reserved_pool", "legacy mail sidecar reserved pool"),
        ("domain_ordinary", "legacy mail sidecar ordinary domain"),
        ("domain_reserved", "legacy mail sidecar reserved domain"),
    ):
        actual = measure_probe_symbol(
            root / args.struct_probe,
            str(tools["nm"]),
            legacy_mail[f"{name}_symbol"],
        )
        check_limit(
            label,
            actual,
            legacy_mail[f"baseline_{name}_bytes"],
            legacy_mail[f"max_{name}_bytes"],
            " bytes",
            ratchet=True,
        )

    sidecar = config["agent_context_sidecar"]
    for name, label in (
        ("per_process", "Agent context sidecar per process"),
        ("pool", "Agent context sidecar global pool"),
        ("ordinary_pool", "Agent context sidecar ordinary pool"),
        ("reserved_pool", "Agent context sidecar reserved pool"),
        ("domain_ordinary", "Agent context sidecar ordinary domain"),
        ("domain_reserved", "Agent context sidecar reserved domain"),
    ):
        actual = measure_probe_symbol(
            root / args.struct_probe,
            str(tools["nm"]),
            sidecar[f"{name}_symbol"],
        )
        check_limit(
            label,
            actual,
            sidecar[f"baseline_{name}_bytes"],
            sidecar[f"max_{name}_bytes"],
            " bytes",
            ratchet=True,
        )

    agent_state = config["agent_state_pages"]
    for name, label in (
        ("per_process", "Agent total state per process"),
        ("pool", "Agent total state global pool"),
        ("ordinary_pool", "Agent total state ordinary pool"),
        ("reserved_pool", "Agent total state reserved pool"),
        ("domain_ordinary", "Agent total state ordinary domain"),
        ("domain_reserved", "Agent total state reserved domain"),
    ):
        actual = measure_probe_symbol(
            root / args.struct_probe,
            str(tools["nm"]),
            agent_state[f"{name}_symbol"]
        )
        check_limit(
            label,
            actual,
            agent_state[f"baseline_{name}_bytes"],
            agent_state[f"max_{name}_bytes"],
            " bytes",
            ratchet=True,
        )

    stack = config["kernel_stack"]
    stack_virtual_capacity = measure_probe_symbol(
        root / args.struct_probe,
        str(tools["nm"]),
        stack["virtual_capacity_symbol"],
    )
    check_limit(
        "kernel stack virtual capacity",
        stack_virtual_capacity,
        stack["baseline_virtual_capacity_bytes"],
        stack["max_virtual_capacity_bytes"],
        " bytes",
        ratchet=True,
    )
    stack_reserved_physical_pool = measure_probe_symbol(
        root / args.struct_probe,
        str(tools["nm"]),
        stack["reserved_physical_pool_symbol"],
    )
    check_limit(
        "kernel stack reserved physical pool",
        stack_reserved_physical_pool,
        stack["baseline_reserved_physical_pool_bytes"],
        stack["max_reserved_physical_pool_bytes"],
        " bytes",
        ratchet=True,
    )
    boot_stack_capacity = measure_kernel_symbol_span(
        root / args.kernel,
        str(tools["nm"]),
        stack["boot_stack_start_symbol"],
        stack["boot_stack_end_symbol"],
    )
    print(
        "[kernel-budget] boot stack capacity: "
        f"actual={boot_stack_capacity} bytes "
        f"configured={stack['boot_stack_size_bytes']} bytes"
    )
    if boot_stack_capacity != stack["boot_stack_size_bytes"]:
        raise BudgetError(
            f"boot stack capacity drifted: {boot_stack_capacity} bytes != "
            f"{stack['boot_stack_size_bytes']} bytes"
        )
    boot_entry_target = measure_boot_entry_target(root / "os" / "entry.S")
    print(
        "[kernel-budget] boot entry root: "
        f"actual={boot_entry_target} configured={stack['boot_root']}"
    )
    if boot_entry_target != stack["boot_root"]:
        raise BudgetError(
            f"boot entry root drifted: {boot_entry_target} != "
            f"{stack['boot_root']}"
        )

    validate_stack_build_config(build_config, stack)
    command = stack_check_command(
        root,
        config,
        Path(args.callgraph_dir),
        Path(args.stack_checker),
        production_translation_units(root, config),
    )
    output = run_tool(command, "kernel stack budget check")
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    if profile_kind == "local":
        attest_local_kernel_budget_tools(profile, tools)


def source_function_body(source, signature):
    start = source.find(signature)
    end = source.find("\n}\n", start)
    if start < 0 or end < 0:
        raise BudgetError(f"required source function is missing: {signature}")
    return source[start : end + 2]


def require_source_order(body, tokens, context):
    cursor = -1
    for token in tokens:
        cursor = body.find(token, cursor + 1)
        if cursor < 0:
            raise BudgetError(f"{context} lost ordered operation: {token}")


def require_source_tokens(body, tokens, context):
    missing = [token for token in tokens if token not in body]
    if missing:
        raise BudgetError(f"{context} lost operations: {missing!r}")


def validate_metadata_scan_boundary_text(objects, scan):
    state = (
        r"\bscan_(?:ctl|control)\b|\bscan\.(?:offset|seen|next_tick|last_step_tick|"
        r"started_tick|runs|entries|added|updated|removed|failures|deferred|"
        r"retry|sweep_uncertain|failed_scopes|failed_scope_count)\b|"
        r"\broot_dir\s*\("
    )
    reverse = (
        r"agent_file_maintain|agent_metadata_note_catalog_changes|"
        r"agent_file_store_load|agent_metadata_query_|"
        r"bio_background_|agent_metadata_txn_try_external|"
        r"agent_metadata_objects\.h|\(\s*\*\s*[A-Za-z_]\w*\s*\)\s*\("
    )
    if re.search(state, objects):
        raise BudgetError("metadata objects retained scan-owned state or traversal")
    if re.search(reverse, scan):
        raise BudgetError("metadata scan acquired a reverse dependency or callback")
    for name, value in (
        ("SCAN_INTERVAL", 20),
        ("SCAN_STEP", 16),
        ("SCAN_REST_MULTIPLIER", 4),
    ):
        pattern = rf"(?m)^#define {name} {value}$"
        if len(re.findall(pattern, scan)) != 1:
            raise BudgetError(f"metadata scan fairness constant changed: {name}")

    sync = source_function_body(
        objects, "agent_file_catalog_sync(const struct agent_catalog_delta *delta)"
    )
    if sync.count("agent_metadata_txn_projection_ack()") != 1:
        raise BudgetError("metadata catalog sync must ACK its projection once")
    require_source_order(
        sync,
        (
            "agent_metadata_query_invalidate_locked(",
            "agent_metadata_scan_catalog_sync(delta)",
            "agent_metadata_txn_projection_ack()",
        ),
        "metadata catalog projection",
    )
    scan_sync = source_function_body(
        scan,
        "agent_metadata_scan_catalog_sync(const struct agent_catalog_delta *delta)",
    )
    require_source_tokens(
        scan_sync,
        ("i < AGENT_META_STALE_BYTES",
         "scan.seen[i] |= delta->applied_slots[i]"),
        "scan delta sync",
    )
    if "agent_metadata_txn_projection_ack" in scan_sync:
        raise BudgetError("metadata scan must not ACK the catalog projection")

    background = source_function_body(
        objects, "agent_metadata_background_maintain(void)"
    )
    if background.count("agent_metadata_scan_plan(now)") != 2:
        raise BudgetError("metadata background must revalidate its scan plan")
    require_source_order(
        background,
        (
            "agent_metadata_store_background_maintain()",
            "agent_metadata_scan_plan(now)",
            "bio_background_begin(FS_OWNER_SYSTEM)",
            "agent_metadata_txn_try_external()",
            "agent_metadata_scan_plan(now)",
            "agent_file_store_load()",
            "agent_metadata_scan_step(now, plan, load_ok)",
            "agent_metadata_note_catalog_changes(changes)",
            "agent_metadata_txn_unlock()",
            "bio_background_end()",
        ),
        "metadata background coordinator",
    )
    step = source_function_body(scan, "agent_metadata_scan_step(uint64 now")
    require_source_tokens(
        step,
        (
            "root_dir_status(&root_status)",
            "root_status != FS_LOOKUP_FOUND",
            "readi(",
            "inode_get(",
            "agent_metadata_scan_index_inode(ip, name, &bind_failed)",
            "if (bind_failed) {\n"
            "\t\t\tscan.failures++;\n"
            "\t\t\tscan.retry = 1;",
            "scan_pause(1, 1)",
            "scan.offset = off;\n\t\t\tscan_pause(1, 1)",
            "scan_scope_failed(view.scope_id, 0)",
            "steps < SCAN_STEP",
        ),
        "metadata scan step",
    )
    bind = source_function_body(
        scan, "agent_metadata_scan_index_inode(struct inode *ip"
    )
    if "if (failed)" in bind:
        raise BudgetError("metadata scan bind failure output became optional")
    require_source_order(
        bind,
        (
            "SCAN_NOTE(slot)",
            "agent_metadata_catalog_edit_begin_scan(",
            "agent_metadata_catalog_edit_commit(&edit, changes)",
            "agent_metadata_store_mark_dirty(ip->vfs_scope_id)",
            "agent_file_state_set_index(ip, slot + 1, persist, 0)",
            "return changes",
        ),
        "metadata scan bind fail-stop protocol",
    )
    require_source_tokens(
        bind,
        ("SCAN_NOTE(slot);\n\tif (agent_metadata_catalog_edit_begin_scan",),
        "metadata scan mutation visibility",
    )
    for operation in (
        "agent_metadata_catalog_edit_begin_scan(",
        "agent_metadata_catalog_edit_commit(&edit, changes)",
        "agent_file_state_set_index(ip, slot + 1, persist, 0)",
    ):
        start = bind.find(operation)
        if start < 0 or "goto retry" not in bind[start : start + 500]:
            raise BudgetError(
                f"metadata scan bind failure is not propagated: {operation}"
            )
    require_source_tokens(
        bind,
        ("agent_file_state_set_index(ip, slot + 1, persist, 0) < 0)\n"
         "\t\t\tgoto retry;",),
        "metadata scan inode sidecar failure",
    )
    require_source_order(
        step,
        (
            "agent_metadata_scan_index_inode(ip, name, &bind_failed)",
            "if (bind_failed)",
            "scan.retry = 1",
            "scan_scope_failed(ip->vfs_scope_id, 1)",
            "if (!scan_ctl.active)",
            "if (scan.offset >= root->size)",
            "scan_scope_failed(view.scope_id, 0)",
            "scan_pause(scan.retry, 0)",
        ),
        "metadata scan isolated retry protocol",
    )
    if ("scan_failed" in step or "seen[AGENT_FILE_META_MAX]" in scan or
            "uchar seen[AGENT_META_STALE_BYTES]" not in scan):
        raise BudgetError("metadata scan restored global abort or oversized seen state")
    plan = source_function_body(scan, "agent_metadata_scan_plan(uint64 now)")
    require_source_tokens(
        plan,
        ("scan.last_step_tick != now", "now >= scan.next_tick"),
        "metadata scan tick budget",
    )
    request = source_function_body(scan, "agent_file_request_scan(void)")
    require_source_tokens(
        request,
        ("!scan_ctl.pending", "scan_rest_deadline(now, now)"),
        "metadata scan request coalescing",
    )
    if "agent_file_request_scan(void)" in objects:
        raise BudgetError("metadata objects retained a scan request wrapper")
    if scan.count("agent_file_request_scan(void)") != 1:
        raise BudgetError("metadata scan must own one scan request entry point")


def validate_metadata_scan_boundary_sources(root):
    try:
        objects = (root / "os" / "agent_metadata_objects.c").read_text(encoding="utf-8")
        scan = (root / "os" / "agent_metadata_scan.c").read_text(encoding="utf-8")
    except OSError as error:
        raise BudgetError(f"cannot read metadata scan boundary source: {error}") from error
    validate_metadata_scan_boundary_text(objects, scan)


def validate_metadata_directory_boundary_text(objects, directory):
    hooks = (
        "agent_fs_note_create",
        "agent_fs_note_write",
        "agent_fs_note_truncate",
        "agent_fs_note_delete",
    )
    for hook in hooks:
        definition = rf"(?m)^void\s+{hook}\s*\("
        if re.search(definition, objects):
            raise BudgetError(f"metadata objects retained directory hook: {hook}")
        if len(re.findall(definition, directory)) != 1:
            raise BudgetError(f"metadata directory must own one hook: {hook}")
    if 'agent_metadata_directory.h' in objects:
        raise BudgetError("metadata objects acquired a directory dependency")
    if re.search(
        r"agent_metadata_query_|agent_file_store_load|bio_background_|"
        r"agent_metadata_store_persist\s*\(|agent_metadata_txn_lock\s*\(|"
        r"\bscan_(?:ctl|control)\b|\bscan\.(?:offset|seen|next_tick|last_step_tick|"
        r"started_tick|runs|entries|added|updated|removed)\b",
        directory,
    ):
        raise BudgetError("metadata directory acquired forbidden coordination work")
    if "agent_dependency_generation" in directory:
        raise BudgetError("metadata directory bypassed the catalog-change operation")
    if re.search(r"\(\s*\*\s*[A-Za-z_]\w*\s*\)\s*\(", directory):
        raise BudgetError("metadata directory introduced an indirect callback")
    writable_state = re.compile(
        r"(?m)^static\s+(?:char|short|int|long|uint|uint64|"
        r"struct\s+[A-Za-z_]\w*)\s+[A-Za-z_]\w*(?:\[[^\n]*\])?\s*(?:=[^\n]*)?;"
    )
    if writable_state.search(directory):
        raise BudgetError("metadata directory acquired writable file-scope state")
    require_source_tokens(
        objects,
        (
            "agent_metadata_inode_trackable(struct inode *ip)",
            "agent_metadata_note_catalog_changes(uint changes)",
        ),
        "metadata directory leaf operations",
    )

    indexable = source_function_body(directory, "static int fs_create_indexable(")
    require_source_tokens(
        indexable,
        ("agent_metadata_inode_trackable(ip)",
         "agent_scope_valid(ip->vfs_scope_id)",
         "ip->agent_meta_slot <= 0"),
        "metadata directory create predicate",
    )
    create = source_function_body(directory, "agent_fs_note_create(struct inode *ip")
    require_source_order(
        create,
        (
            "fs_dirent_canonicalize(path, key)",
            "agent_metadata_txn_try_external()",
            "agent_metadata_store_loaded()",
            "agent_metadata_scan_index_inode(ip, key, &failed)",
            "agent_metadata_note_catalog_changes(changes)",
            "agent_metadata_txn_unlock()",
        ),
        "metadata directory create hook",
    )
    require_source_tokens(
        create,
        ("fs_create_indexable(ip)", "agent_file_request_scan()"),
        "metadata directory create revalidation",
    )
    if create.count("agent_metadata_txn_unlock()") != 1:
        raise BudgetError("metadata directory create must unlock exactly once")
    update = source_function_body(directory, "static void agent_fs_publish_content(")
    require_source_order(
        update,
        (
            "agent_file_state_content_publish(ip, &receipt)",
            "agent_metadata_store_mark_dirty(ip->vfs_scope_id)",
            "agent_file_request_scan()",
        ),
        "metadata directory content overlay",
    )
    require_source_tokens(
        update,
        ("!agent_file_state_content_publish(ip, &receipt)",
         "AGENT_FILE_META_F_PERSIST",
         "AGENT_FILE_META_F_AUTOSCAN", "agent_file_request_scan()"),
        "metadata directory content fallback",
    )
    if "agent_metadata_txn_" in update or re.search(
        r"agent_metadata_catalog_(?!journal_note_content\b)", update
    ):
        raise BudgetError("ordinary content publication entered the catalog gate")
    remove = source_function_body(directory, "static void agent_fs_remove_inode(")
    delete = source_function_body(directory, "agent_fs_note_delete(struct inode *ip")
    require_source_tokens(delete, ("agent_fs_remove_inode(ip)",),
                          "metadata directory delete delegation")
    require_source_order(
        remove,
        (
            "agent_file_state_content_bump(ip)",
            "agent_metadata_txn_try_external()",
            "agent_metadata_catalog_borrow(0, slot, &view)",
            "view.scope_id != scope_id",
            "view.meta->dev != ip->dev",
            "view.meta->inum != ip->inum",
            "view.meta->incarnation != ip->vfs_incarnation",
            "agent_metadata_catalog_clear_slot(slot)",
            "agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_ALL)",
            "agent_metadata_store_mark_dirty(scope_id)",
            "agent_metadata_txn_unlock()",
        ),
        "metadata directory delete hook",
    )
    if re.search(r"ip->agent_meta_(?:slot|flags|version)\s*=|iupdate\s*\(", remove):
        raise BudgetError(
            "metadata directory delete must unbind through the catalog API"
        )
    if delete.count("agent_metadata_txn_unlock()") != 0:
        raise BudgetError("metadata directory delete bypassed the shared event state machine")


def validate_metadata_directory_store_symbols(undefined):
    actual = frozenset(
        symbol for symbol in undefined
        if symbol.startswith("agent_metadata_store_")
    )
    if actual != REQUIRED_METADATA_DIRECTORY_STORE_SYMBOLS:
        raise BudgetError(
            "metadata directory store API boundary mismatch: "
            f"expected={sorted(REQUIRED_METADATA_DIRECTORY_STORE_SYMBOLS)!r}, "
            f"actual={sorted(actual)!r}"
        )


def validate_metadata_directory_callgraph(callgraph):
    if 'targetname: "__indirect_call"' in callgraph:
        raise BudgetError("metadata directory introduced an indirect call")


def validate_metadata_directory_boundary_sources(root):
    try:
        objects = (root / "os" / "agent_metadata_objects.c").read_text(
            encoding="utf-8"
        )
        directory = (root / "os" / "agent_metadata_directory.c").read_text(
            encoding="utf-8"
        )
    except OSError as error:
        raise BudgetError(
            f"cannot read metadata directory boundary source: {error}"
        ) from error
    validate_metadata_directory_boundary_text(objects, directory)


def check_agent_modules(args, config):
    modules = config["agent_modules"]
    if not all(
        (args.cc, args.ld, args.objcopy, args.objdump, args.nm, args.size)
    ):
        raise BudgetError(
            "--cc, --ld, --objcopy, --objdump, --nm, and --size are required"
        )
    root = Path(args.root).resolve()
    object_dir = Path(args.object_dir)
    if not object_dir.is_absolute():
        object_dir = root / object_dir
    object_dir = object_dir.resolve()
    try:
        object_dir.relative_to(root)
    except ValueError as error:
        raise BudgetError(
            f"Agent object directory escapes repository: {args.object_dir}"
        ) from error
    profile_kind, profile, tools = validate_kernel_budget_toolchain(
        config,
        args.cc,
        args.ld,
        args.objcopy,
        args.objdump,
        args.nm,
        args.size,
        root / args.stack_build_config,
        root / "os" / "initproc.S",
    )
    nm = str(tools["nm"])
    size = str(tools["size"])
    validate_metadata_scan_boundary_sources(root)
    validate_metadata_directory_boundary_sources(root)
    directory_callgraph = (
        root / args.callgraph_dir / "agent_metadata_directory.ci"
    ).resolve()
    try:
        directory_callgraph_text = directory_callgraph.read_text(encoding="utf-8")
    except OSError as error:
        raise BudgetError(
            f"cannot read metadata directory callgraph: {error}"
        ) from error
    validate_metadata_directory_callgraph(directory_callgraph_text)
    entries = modules["modules"]
    defined_by_module = {}
    symbol_owner = {}
    for support in modules["test_only_sources"]:
        lines = measure_file_lines(root, support["source_path"])
        check_limit(
            f"Agent test-only source {support['name']}",
            lines,
            support["baseline_lines"],
            support["max_lines"],
            " lines",
            ratchet=True,
        )
    kernel_path = root / args.kernel
    if not kernel_path.is_file():
        raise BudgetError(f"production kernel is missing: {kernel_path}")
    production_symbols = parse_nm_defined_symbols(
        run_tool(
            [nm, "-g", "--defined-only", native_tool_argument(kernel_path)],
            "production test-only symbol inspection",
        )
    )
    leaked_test_symbols = test_only_symbol_leaks(
        production_symbols, modules["test_only_sources"]
    )
    if leaked_test_symbols:
        raise BudgetError(
            "production kernel exports test-only symbols: "
            + ", ".join(leaked_test_symbols)
        )
    print("[kernel-budget] production test-only symbols: absent")
    for entry in entries:
        lines = measure_file_lines(root, entry["source_path"])
        check_limit(
            f"Agent module {entry['name']}",
            lines,
            entry["baseline_lines"],
            entry["max_lines"],
            " lines",
            ratchet=True,
        )
        object_path = resolve_agent_object_path(
            root, object_dir, entry["object_path"]
        )
        if not object_path.is_file():
            raise BudgetError(
                f"Agent module object is missing: {object_path}"
            )
        max_bss = entry.get("max_bss_bytes")
        if max_bss is not None:
            _, _, object_bss, _ = parse_size_output(
                run_tool(
                    [size, "-B", native_tool_argument(object_path)],
                    f"Agent module {entry['name']} BSS inspection",
                )
            )
            print(
                f"[kernel-budget] Agent module {entry['name']} BSS: "
                f"actual={object_bss} max={max_bss} bytes"
            )
            if object_bss > max_bss:
                raise BudgetError(
                    f"Agent module {entry['name']} BSS exceeds budget: "
                    f"{object_bss} > {max_bss} bytes"
                )
        output = run_tool(
            [nm, "-g", "--defined-only", native_tool_argument(object_path)],
            f"Agent module {entry['name']} export inspection",
        )
        records = parse_nm_defined_records(output)
        defined = set(records)
        writable_exports = invalid_global_object_exports(
            records, entry["allowed_readonly_symbols"]
        )
        if writable_exports:
            raise BudgetError(
                f"Agent module {entry['name']} exports writable data: "
                + ", ".join(writable_exports)
            )
        bad_exports = sorted(
            symbol
            for symbol in defined
            if not symbol_allowed(
                symbol,
                entry["allowed_global_prefixes"],
                entry["allowed_global_symbols"],
            )
        )
        if bad_exports:
            raise BudgetError(
                f"Agent module {entry['name']} exports unowned symbols: "
                + ", ".join(bad_exports)
            )
        defined_by_module[entry["name"]] = defined
        for symbol in defined:
            previous = symbol_owner.get(symbol)
            if previous is not None:
                raise BudgetError(
                    f"Agent global symbol {symbol} is owned by both "
                    f"{previous} and {entry['name']}"
                )
            symbol_owner[symbol] = entry["name"]

    check_agent_aggregate_budgets(
        root, entries, modules["aggregate_budgets"], size, object_dir
    )

    actual_graph = {}
    undefined_by_module = {}
    expected_graph = {
        entry["name"]: set(entry["allowed_dependencies"]) for entry in entries
    }
    for entry in entries:
        object_path = resolve_agent_object_path(
            root, object_dir, entry["object_path"]
        )
        output = run_tool(
            [nm, "-u", native_tool_argument(object_path)],
            f"Agent module {entry['name']} dependency inspection",
        )
        undefined = parse_nm_undefined_symbols(output)
        if entry["name"] == "metadata_directory":
            validate_metadata_directory_store_symbols(undefined)
        undefined_by_module[entry["name"]] = undefined
        dependencies = set()
        for symbol in undefined:
            owner = symbol_owner.get(symbol)
            if owner is not None and owner != entry["name"]:
                dependencies.add(owner)
        actual_graph[entry["name"]] = dependencies
    validate_module_dependency_graph(
        actual_graph,
        expected_graph,
        [frozenset(component) for component in modules["allowed_sccs"]],
        modules["max_scc_size"],
        "registered Agent module graph",
    )
    print(
        "[kernel-budget] registered Agent module graph: exact dependencies, "
        f"max SCC={max(len(c) for c in strongly_connected_components(actual_graph))}"
    )

    callgraph_dir = (root / args.callgraph_dir).resolve()
    if not callgraph_dir.is_dir():
        raise BudgetError(
            f"Agent integration object directory is missing: {callgraph_dir}"
        )
    registered_paths = {
        resolve_agent_object_path(root, object_dir, entry["object_path"])
        for entry in entries
    }
    discovered = {}
    for object_path in sorted(object_dir.glob("*.o")):
        resolved = object_path.resolve()
        if resolved in registered_paths:
            continue
        defined_output = run_tool(
            [nm, "-g", "--defined-only", native_tool_argument(object_path)],
            f"Agent integration object {object_path.name} export inspection",
        )
        undefined_output = run_tool(
            [nm, "-u", native_tool_argument(object_path)],
            f"Agent integration object {object_path.name} dependency inspection",
        )
        records = parse_nm_defined_records(defined_output)
        undefined = parse_nm_undefined_symbols(undefined_output)
        if not controlled_defined_records(records) and not (
            controlled_undefined_symbols(undefined)
        ):
            continue
        canonical_path = (Path("build/os") / resolved.name).as_posix()
        discovered[canonical_path] = (records, undefined)

    bridges = modules["integration_bridges"]
    bridge_by_path = {
        Path(bridge["object_path"]).as_posix(): bridge for bridge in bridges
    }
    validate_integration_bridge_inventory(
        discovered, bridge_by_path
    )
    integration_defined = {
        name: controlled_defined_records(
            {symbol: "T" for symbol in symbols}
        )
        for name, symbols in defined_by_module.items()
    }
    integration_undefined = dict(undefined_by_module)
    for relative_path, bridge in bridge_by_path.items():
        records, undefined = discovered[relative_path]
        integration_defined[bridge["name"]] = (
            validate_integration_bridge_exports(
                bridge["name"],
                records,
                bridge["allowed_global_symbols"],
                bridge["allowed_readonly_symbols"],
            )
        )
        integration_undefined[bridge["name"]] = undefined
    integration_graph = build_controlled_dependency_graph(
        integration_defined, integration_undefined
    )
    expected_integration_graph = {
        entry["name"]: set(entry["allowed_dependencies"])
        | set(entry["allowed_bridge_dependencies"])
        for entry in entries
    }
    expected_integration_graph.update(
        {
            bridge["name"]: set(bridge["allowed_dependencies"])
            for bridge in bridges
        }
    )
    validate_module_dependency_graph(
        integration_graph,
        expected_integration_graph,
        [
            frozenset(component)
            for component in modules["integration_allowed_sccs"]
        ],
        modules["integration_max_scc_size"],
        "Agent integration graph",
    )
    print(
        "[kernel-budget] Agent integration graph: exact controlled-symbol "
        "dependencies, "
        f"{len(bridges)} bridges, max SCC="
        f"{max(len(c) for c in strongly_connected_components(integration_graph))}"
    )

    probe = root / args.agent_core_probe
    if not probe.is_file():
        raise BudgetError(f"Agent core boundary probe is missing: {probe}")
    output = run_tool(
        [nm, "--defined-only", native_tool_argument(probe)],
        "Agent core boundary inspection",
    )
    violations = forbidden_symbols(
        parse_nm_defined_symbols(output),
        modules["forbidden_core_authority_symbol_prefixes"],
        modules["allowed_core_facade_symbols"],
    )
    defined = parse_nm_defined_symbols(output)
    forbidden_exact = set(modules["forbidden_core_authority_symbols"])
    allowed_facades = set(modules["allowed_core_facade_symbols"])
    violations.extend(sorted((defined & forbidden_exact) - allowed_facades))
    violations = sorted(set(violations))
    if violations:
        shown = ", ".join(violations[:12])
        if len(violations) > 12:
            shown += f", ... ({len(violations)} total)"
        raise BudgetError(
            "agent_core.c still defines migrated authority symbols: " + shown
        )
    print("[kernel-budget] Agent core migrated authority symbols: absent")
    if profile_kind == "local":
        attest_local_kernel_budget_tools(profile, tools)


def check_agent_tests(args, config):
    if (
        getattr(args, "agent_test_seconds", None) is not None
        or getattr(args, "agent_test_start_ns", None) is not None
    ):
        raise BudgetError(
            "Agent duration requires a complete per-case timing file; "
            "summary-only inputs are forbidden"
        )
    if args.agent_test_timing_file is None:
        raise BudgetError(
            "Agent duration requires a complete per-case timing file"
        )
    _, elapsed = read_agent_timing_file(
        args.agent_test_timing_file,
        config["agent_test_suite"]["expected_cases"],
    )
    if not math.isfinite(elapsed) or elapsed <= 0:
        raise BudgetError("Agent test duration must be finite and positive")
    tests = config["agent_test_suite"]
    calibration = getattr(args, "agent_test_calibration", False)
    if calibration:
        if args.agent_test_timing_file is None:
            raise BudgetError(
                "Agent calibration requires a persisted per-case timing file"
            )
        if tests["calibration_status"] != AGENT_TEST_CALIBRATION_PROVISIONAL:
            raise BudgetError(
                "Agent calibration mode is only valid while the budget "
                "is provisional"
            )
        print(
            "[kernel-budget] Agent test suite calibration: "
            f"actual={elapsed:.3f} seconds"
        )
        fingerprint, paths = agent_test_source_fingerprint(
            getattr(args, "root", "."), config
        )
        print(
            "[kernel-budget] Agent calibration source/contract: "
            f"sha256={fingerprint}, inputs={len(paths)}"
        )
        print(
            "[kernel-budget] Agent test calibration captured; "
            "review repeated dedicated-runner samples before marking calibrated"
        )
        return
    check_agent_test_policy(config)
    check_limit(
        "Agent test suite",
        elapsed,
        tests["baseline_seconds"],
        tests["max_seconds"],
        " seconds",
    )


def check_agent_test_timing_inventory(args, config):
    if args.agent_test_timing_file is None:
        raise BudgetError("Agent timing inventory requires a timing file")
    rows, elapsed = read_agent_timing_file(
        args.agent_test_timing_file,
        config["agent_test_suite"]["expected_cases"],
    )
    if not math.isfinite(elapsed) or elapsed <= 0:
        raise BudgetError("Agent timing inventory total must be positive")
    print(
        "[kernel-budget] Agent timing inventory: "
        f"cases={len(rows)} total={elapsed:.3f} seconds threshold=not-applied"
    )


def check_agent_test_policy(config):
    tests = config["agent_test_suite"]
    if tests["calibration_status"] != AGENT_TEST_CALIBRATION_READY:
        raise BudgetError(
            "Agent test duration is provisional; run the complete "
            f"{len(tests['expected_cases'])}-case "
            "suite as exactly three serialized, attested rounds through "
            "scripts/agent_test_calibration.py on the pinned runner before "
            "final acceptance"
        )
    print(
        "[kernel-budget] Agent duration policy: "
        "calibrated local profile="
        f"{tests['local_calibration_profile']['profile_id']}"
    )


def main():
    args = parse_args()
    try:
        root = Path(args.root).resolve()
        if not root.is_dir():
            raise BudgetError(f"kernel budget root is not a directory: {root}")
        try:
            os.chdir(root)
        except OSError as error:
            raise BudgetError(
                f"cannot enter kernel budget root {root}: {error}"
            ) from error
        # All repository-relative CLI paths and native PE tool arguments now
        # share one explicit execution root, even when invoked from elsewhere.
        args.root = str(root)
        if args.agent_test_calibration and args.check != "agent-tests":
            raise BudgetError(
                "--agent-test-calibration requires --check agent-tests"
            )
        config = load_config(args.config)
        source_contract = check_agent_test_source_fingerprint(args.root, config)
        check_agent_test_calibration_evidence(
            args.root,
            config,
            None if source_contract is None else source_contract[1],
        )
        if args.check == "kernel":
            check_kernel(args, config)
        elif args.check == "agent-modules":
            print("[kernel-budget] agent-modules checks begin")
            check_agent_modules(args, config)
        elif args.check == "agent-tests":
            check_agent_tests(args, config)
        elif args.check == "agent-test-timing-inventory":
            check_agent_test_timing_inventory(args, config)
        elif args.check == "agent-test-policy":
            check_agent_test_policy(config)
        else:
            print("[kernel-budget] configuration is valid")
    except BudgetError as error:
        print(f"[kernel-budget] failed: {error}", file=sys.stderr)
        return 1
    print(f"[kernel-budget] {args.check} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
