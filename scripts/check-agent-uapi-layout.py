#!/usr/bin/env python3
"""编译两种 Agent UAPI 视图并要求公共布局完全一致。"""

import argparse
import json
import re
import subprocess
from pathlib import Path


PREFIX = "agent_uapi_layout_"

SHARED_HEADER_PROBES = {
    "workflow-fence": (
        "agent_workflow_fence_abi.h",
        "sizeof(struct agent_workflow_fence_receipt)",
    ),
    "execution-contract": (
        "agent_execution_contract_abi.h",
        "sizeof(struct agent_execution_contract_result)",
    ),
    "provenance": (
        "agent_provenance_abi.h",
        "sizeof(struct agent_provenance_manifest)",
    ),
    "task-channel": (
        "agent_task_channel_abi.h",
        "sizeof(struct agent_task_channel_enter_result)",
    ),
}


class LayoutError(RuntimeError):
    pass


def run(command, context, cwd=None):
    try:
        result = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            cwd=cwd,
        )
    except OSError as error:
        raise LayoutError(f"{context} failed: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise LayoutError(f"{context} failed: {detail}")
    return result.stdout


def compile_probe(root, build_dir, cc, view, include_dir):
    output = build_dir / f"agent-uapi-{view}.o"
    try:
        output_arg = output.relative_to(root).as_posix()
        include_arg = include_dir.relative_to(root).as_posix()
    except ValueError as error:
        raise LayoutError(
            "Agent UAPI build paths must stay below the source root"
        ) from error
    command = [
        cc,
        "-std=gnu11",
        "-Wall",
        "-Werror",
        "-O",
        "-ffreestanding",
        "-fno-common",
        "-fno-stack-protector",
        "-nostdlib",
        "-march=rv64imac_zicsr_zifencei",
        "-mabi=lp64",
        "-mcmodel=medany",
        "-mno-relax",
        f"-I{include_arg}",
        "-I.",
        "-c",
        "scripts/probes/agent-uapi-layout.c",
        "-o",
        output_arg,
    ]
    run(command, f"{view} Agent UAPI probe compile", cwd=root)
    return output


def compile_shared_header_probes(root, build_dir, cc):
    for label, (header, expression) in SHARED_HEADER_PROBES.items():
        source = build_dir / f"agent-{label}-header.c"
        output = build_dir / f"agent-{label}-header.o"
        function = label.replace("-", "_")
        try:
            source_arg = source.relative_to(root).as_posix()
            output_arg = output.relative_to(root).as_posix()
            source.write_text(
                f'#include "{header}"\n'
                f"int agent_{function}_header_probe(void)\n"
                "{\n"
                f"\treturn {expression};\n"
                "}\n",
                encoding="utf-8",
            )
        except (OSError, ValueError) as error:
            raise LayoutError(
                f"cannot create standalone {label} ABI probe: {error}"
            ) from error
        command = [
            cc,
            "-std=gnu11",
            "-Wall",
            "-Werror",
            "-O",
            "-ffreestanding",
            "-fno-common",
            "-fno-stack-protector",
            "-nostdlib",
            "-march=rv64imac_zicsr_zifencei",
            "-mabi=lp64",
            "-mcmodel=medany",
            "-mno-relax",
            "-I.",
            "-c",
            source_arg,
            "-o",
            output_arg,
        ]
        run(command, f"standalone {label} ABI header compile", cwd=root)


def symbols(nm, obj, root):
    try:
        obj_arg = obj.relative_to(root).as_posix()
    except ValueError as error:
        raise LayoutError(
            "Agent UAPI object must stay below the source root"
        ) from error
    output = run(
        [nm, "-S", "--defined-only", obj_arg],
        f"nm {obj.name}",
        cwd=root,
    )
    found = {}
    pattern = re.compile(
        r"^[0-9A-Fa-f]+\s+([0-9A-Fa-f]+)\s+[A-Za-z]\s+(\S+)$"
    )
    for line in output.splitlines():
        match = pattern.match(line.strip())
        if not match or not match.group(2).startswith(PREFIX):
            continue
        found[match.group(2)] = int(match.group(1), 16)
    if not found:
        raise LayoutError(f"{obj.name} contains no Agent UAPI layout symbols")
    return found


def compare(kernel, user):
    if kernel == user:
        return
    missing_kernel = sorted(set(user) - set(kernel))
    missing_user = sorted(set(kernel) - set(user))
    mismatched = sorted(
        name for name in set(kernel) & set(user) if kernel[name] != user[name]
    )
    parts = []
    if missing_kernel:
        parts.append(f"kernel missing {missing_kernel!r}")
    if missing_user:
        parts.append(f"user missing {missing_user!r}")
    if mismatched:
        detail = ", ".join(
            f"{name}: kernel={kernel[name]} user={user[name]}" for name in mismatched
        )
        parts.append(f"layout mismatch ({detail})")
    raise LayoutError("; ".join(parts))


def load_golden(path):
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LayoutError(f"invalid Agent UAPI golden file: {error}") from error
    if document.get("version") != 1 or not isinstance(
        document.get("symbols"), dict
    ):
        raise LayoutError("invalid Agent UAPI golden schema")
    golden = document["symbols"]
    if not golden or any(
        not isinstance(name, str)
        or not name.startswith(PREFIX)
        or not isinstance(value, int)
        or value <= 0
        for name, value in golden.items()
    ):
        raise LayoutError("invalid Agent UAPI golden symbol map")
    return golden


def compare_golden(actual, golden):
    try:
        compare(actual, golden)
    except LayoutError as error:
        raise LayoutError(f"frozen ABI drift: {error}") from error


def validate_compatibility_tombstones(root):
    paths = (
        root / "agent_observe_abi.h",
        root / "os" / "agent.h",
        root / "user" / "include" / "agent.h",
    )
    try:
        observe, kernel, user = (
            path.read_text(encoding="utf-8") for path in paths
        )
    except OSError as error:
        raise LayoutError(f"cannot read Agent compatibility ABI: {error}") from error

    if not re.search(
        r"^#define\s+AGENT_OBSERVE_RECOVERY_COMPAT_TOMBSTONE\s+1U$",
        observe,
        re.MULTILINE,
    ) or not re.search(
        r"Compatibility tombstone:.*recovery endpoint is unsupported",
        observe,
        re.DOTALL,
    ):
        raise LayoutError("observation recovery lacks an explicit ABI tombstone")

    for label, source in (("kernel", kernel), ("user", user)):
        for name, value in (
            ("AGENT_FILE_META_F_PERSIST", 2),
            ("AGENT_FILE_META_F_AUTOSCAN", 4),
        ):
            if not re.search(
                rf"^#define\s+{name}\s+{value}$", source, re.MULTILINE
            ):
                raise LayoutError(
                    f"{label} Agent UAPI changed compatibility value {name}"
                )
        if "AGENT_FILE_META_F_UNSUPPORTED_MASK" not in source or not re.search(
            r"Compatibility tombstones\..*agent_file_meta_set\(\) rejects both bits",
            source,
            re.DOTALL,
        ):
            raise LayoutError(
                f"{label} metadata compatibility flags are not explicitly unsupported"
            )

    if not re.search(
        r"Compatibility tombstone: retained for source ABI; always unsupported\.\s*\*/"
        r"\s*int\s+agent_observe_recovery\s*\(",
        user,
        re.DOTALL,
    ):
        raise LayoutError("user recovery declaration is not marked unsupported")


def define_value(source, name):
    match = re.search(
        rf"^\s*#define\s+{re.escape(name)}\s+(\d+)U?\s*$",
        source,
        re.MULTILINE,
    )
    if not match:
        raise LayoutError(f"Agent tool ABI lacks numeric {name}")
    return int(match.group(1))


def require_numeric_define(source, name, expected, owner):
    match = re.search(
        rf"^\s*#define\s+{re.escape(name)}\s+"
        r"(-?(?:0[xX][0-9A-Fa-f]+|\d+))(?:U|UL|ULL)?\s*$",
        source,
        re.MULTILINE,
    )
    if not match or int(match.group(1), 0) != expected:
        raise LayoutError(
            f"{owner} must define {name} as the frozen value {expected}"
        )


def require_shift_define(source, name, expected_bit, owner):
    match = re.search(
        rf"^\s*#define\s+{re.escape(name)}\s+"
        r"\(1(?:U|ULL)\s*<<\s*(\d+)\)\s*$",
        source,
        re.MULTILINE,
    )
    if not match or int(match.group(1)) != expected_bit:
        raise LayoutError(
            f"{owner} must define {name} as the frozen bit {expected_bit}"
        )


def validate_feature_abi_constants(root):
    expected = {
        "agent_execution_contract_abi.h": {
            "AGENT_EXECUTION_CONTRACT_VERSION": 1,
            "AGENT_EXECUTION_CONTRACT_NODE_VERSION": 1,
            "AGENT_EXECUTION_CONTRACT_MAX_NODES": 24,
            "AGENT_EXECUTION_DIGEST_SIZE": 32,
            "AGENT_EXECUTION_NODE_NONE": 0xFFFFFFFF,
            "AGENT_EXECUTION_CONTRACT_CREATE": 1,
            "AGENT_EXECUTION_CONTRACT_QUERY": 2,
            "AGENT_EXECUTION_CONTRACT_RETIRE": 3,
            "AGENT_EXECUTION_CONTRACT_EMPTY": 0,
            "AGENT_EXECUTION_CONTRACT_FROZEN": 1,
            "AGENT_EXECUTION_CONTRACT_RETIRING": 2,
            "AGENT_EXECUTION_CONTRACT_RECLAIMED": 3,
            "AGENT_EXECUTION_NODE_BLOCKED": 1,
            "AGENT_EXECUTION_NODE_READY": 2,
            "AGENT_EXECUTION_NODE_RUNNING": 3,
            "AGENT_EXECUTION_NODE_SUCCEEDED": 4,
            "AGENT_EXECUTION_NODE_FAILED": 5,
            "AGENT_EXECUTION_NODE_CANCELLED": 6,
            "AGENT_EXECUTION_CANCEL_DENY": 0,
            "AGENT_EXECUTION_CANCEL_ALLOW": 1,
            "AGENT_ARTIFACT_NONE": 0,
            "AGENT_ARTIFACT_BYTES": 1,
            "AGENT_ARTIFACT_UTF8": 2,
            "AGENT_ARTIFACT_JSON": 3,
            "AGENT_ARTIFACT_FILE": 4,
            "AGENT_ARTIFACT_MESSAGE": 5,
            "AGENT_ARTIFACT_TASK": 6,
            "AGENT_ARTIFACT_OPAQUE_HANDLE": 7,
            "AGENT_ARTIFACT_TYPE_COUNT": 8,
            "AGENT_EXECUTION_REASON_NONE": 0,
            "AGENT_EXECUTION_REASON_CONTRACT_REQUIRED": 1,
            "AGENT_EXECUTION_REASON_STALE_LIFECYCLE": 2,
            "AGENT_EXECUTION_REASON_STALE_CONTRACT": 3,
            "AGENT_EXECUTION_REASON_CONTRACT_RETIRING": 4,
            "AGENT_EXECUTION_REASON_UNKNOWN_NODE": 5,
            "AGENT_EXECUTION_REASON_TOOL_MISMATCH": 6,
            "AGENT_EXECUTION_REASON_SCHEMA_MISMATCH": 7,
            "AGENT_EXECUTION_REASON_ILLEGAL_PREDECESSOR": 8,
            "AGENT_EXECUTION_REASON_PREDECESSOR_PENDING": 9,
            "AGENT_EXECUTION_REASON_CAPABILITY_MISSING": 10,
            "AGENT_EXECUTION_REASON_DEADLINE_EXPIRED": 11,
            "AGENT_EXECUTION_REASON_ATTEMPT_INVALID": 12,
            "AGENT_EXECUTION_REASON_ATTEMPT_CONFLICT": 13,
            "AGENT_EXECUTION_REASON_NODE_BUSY": 14,
            "AGENT_EXECUTION_REASON_NODE_COMPLETE": 15,
            "AGENT_EXECUTION_REASON_PHASE_CREDIT": 16,
            "AGENT_EXECUTION_REASON_CANCEL_DISALLOWED": 17,
            "AGENT_EXECUTION_REASON_CONTRACT_INVALID": 18,
            "AGENT_EXECUTION_REASON_SOURCE_SEQUENCE": 19,
            "AGENT_EXECUTION_REASON_DEPENDENCY_FAILED": 20,
            "AGENT_CALL_VERSION_V3": 3,
        },
        "agent_provenance_abi.h": {
            "AGENT_PROVENANCE_FINGERPRINT_SIZE": 32,
            "AGENT_CONTEXT_PROVENANCE_SHIFT": 16,
            "AGENT_PROVENANCE_DENY_NONE": 0,
            "AGENT_PROVENANCE_DENY_BAD_REQUEST": 1,
            "AGENT_PROVENANCE_DENY_STALE_LIFECYCLE": 2,
            "AGENT_PROVENANCE_DENY_MISSING_CONTRACT": 3,
            "AGENT_PROVENANCE_DENY_ILLEGAL_PREDECESSOR": 4,
            "AGENT_PROVENANCE_DENY_CAPABILITY_MISSING": 5,
            "AGENT_PROVENANCE_DENY_UNKNOWN_PROVENANCE": 6,
            "AGENT_PROVENANCE_DENY_PROVENANCE_NOT_ACCEPTED": 7,
            "AGENT_PROVENANCE_DENY_EFFECT_MISMATCH": 8,
            "AGENT_PROVENANCE_DENY_EVIDENCE_UNAVAILABLE": 9,
        },
        "agent_task_channel_abi.h": {
            "AGENT_TASK_CHANNEL_SETUP_SYSCALL": 563,
            "AGENT_TASK_CHANNEL_ENTER_SYSCALL": 564,
            "AGENT_TASK_CHANNEL_RESOURCE_SYSCALL": 565,
            "AGENT_TASK_CHANNEL_VERSION": 1,
            "AGENT_TASK_CHANNEL_ENTRY_VERSION": 1,
            "AGENT_TASK_CHANNEL_CAPACITY": 16,
            "AGENT_TASK_CHANNEL_SCHEMA_SIZE": 32,
            "AGENT_TASK_CHANNEL_OP_SUBMIT": 1,
            "AGENT_TASK_CHANNEL_OP_CANCEL": 2,
            "AGENT_TASK_RESOURCE_IMPORT": 1,
            "AGENT_TASK_RESOURCE_RELEASE": 2,
            "AGENT_TASK_RESOURCE_QUERY": 3,
            "AGENT_TASK_RESOURCE_STATE_NONE": 0,
            "AGENT_TASK_RESOURCE_STATE_LIVE": 1,
            "AGENT_TASK_RESOURCE_STATE_IN_FLIGHT": 2,
            "AGENT_TASK_CHANNEL_OK": 0,
            "AGENT_TASK_CHANNEL_RETRY": -1,
            "AGENT_TASK_CHANNEL_BAD_REQUEST": -2,
            "AGENT_TASK_CHANNEL_STALE": -3,
            "AGENT_TASK_CHANNEL_RESYNC_REQUIRED": -4,
            "AGENT_TASK_CHANNEL_NO_SPACE": -5,
            "AGENT_TASK_CHANNEL_DENIED": -6,
            "AGENT_TASK_CHANNEL_EVIDENCE": -7,
        },
        "agent_lifecycle_abi.h": {
            "AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION": 3,
            "AGENT_WORKFLOW_LIFECYCLE_INFO_V2_VERSION": 2,
            "AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE": 64,
            "AGENT_WORKFLOW_WAKE_BUCKET_COUNT": 4,
            "AGENT_WORKFLOW_SCHED_MODE_NONE": 0,
            "AGENT_WORKFLOW_SCHED_MODE_EEVDF": 1,
            "AGENT_WORKFLOW_SCHED_MODE_FALLBACK": 2,
            "AGENT_WORKFLOW_LATENCY_URGENT": 0,
            "AGENT_WORKFLOW_LATENCY_INTERACTIVE": 1,
            "AGENT_WORKFLOW_LATENCY_NORMAL": 2,
            "AGENT_WORKFLOW_LATENCY_BATCH": 3,
            "AGENT_WORKFLOW_WAKE_BUCKET_LE_1_TICK": 0,
            "AGENT_WORKFLOW_WAKE_BUCKET_LE_2_TICKS": 1,
            "AGENT_WORKFLOW_WAKE_BUCKET_LE_8_TICKS": 2,
            "AGENT_WORKFLOW_WAKE_BUCKET_GT_8_TICKS": 3,
        },
    }
    for relative, definitions in expected.items():
        try:
            source = (root / relative).read_text(encoding="utf-8")
        except OSError as error:
            raise LayoutError(f"cannot read {relative}: {error}") from error
        for name, value in definitions.items():
            require_numeric_define(source, name, value, relative)

    shifted = {
        "agent_execution_contract_abi.h": {
            "AGENT_EXECUTION_CONTRACT_F_ENFORCE": 0,
            "AGENT_EXECUTION_RETRY_FAILURE": 0,
            "AGENT_EXECUTION_RETRY_TIMEOUT": 1,
            "AGENT_EXECUTION_RETRY_CANCELLED": 2,
            "AGENT_RESPONSE_V3_F_CACHED": 0,
        },
        "agent_provenance_abi.h": {
            "AGENT_PROVENANCE_KERNEL_FACT": 0,
            "AGENT_PROVENANCE_TRUSTED_USER_CONTROL": 1,
            "AGENT_PROVENANCE_AGENT_DERIVED": 2,
            "AGENT_PROVENANCE_UNTRUSTED_FILE_DATA": 3,
            "AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT": 4,
            "AGENT_PROVENANCE_CROSS_AGENT_DATA": 5,
            "AGENT_SIDE_EFFECT_FILE": 0,
            "AGENT_SIDE_EFFECT_METADATA": 1,
            "AGENT_SIDE_EFFECT_IPC": 2,
            "AGENT_SIDE_EFFECT_PROCESS": 3,
            "AGENT_SIDE_EFFECT_PERMISSION": 4,
            "AGENT_SIDE_EFFECT_ARTIFACT": 5,
            "AGENT_SIDE_EFFECT_WATCH": 6,
            "AGENT_PROVENANCE_AUTH_F_BOUND_CONTRACT": 0,
            "AGENT_PROVENANCE_AUTH_F_EDGE_AUTHORIZED": 1,
            "AGENT_CONTEXT_RECORD_F_SECURITY_DENIAL": 32,
        },
        "agent_task_channel_abi.h": {
            "AGENT_TASK_CHANNEL_RING_F_ACTIVE": 0,
            "AGENT_TASK_CHANNEL_RING_F_RESYNC": 1,
            "AGENT_TASK_CHANNEL_RING_F_CQ_FULL": 2,
            "AGENT_TASK_CHANNEL_RING_F_RECLAIMING": 3,
            "AGENT_TASK_CHANNEL_RING_F_DEADLINE_DUE": 4,
            "AGENT_TASK_CHANNEL_SETUP_F_SINGLE_ISSUER": 0,
            "AGENT_TASK_CHANNEL_ENTER_F_RESYNC": 0,
            "AGENT_TASK_CHANNEL_ENTER_F_DRAIN": 1,
            "AGENT_TASK_SQE_F_LINK": 0,
            "AGENT_TASK_SQE_F_CANCEL": 1,
            "AGENT_TASK_SQE_F_HARD_DEADLINE": 2,
            "AGENT_TASK_CQE_F_CANCELLED": 0,
            "AGENT_TASK_CQE_F_DEADLINE": 1,
            "AGENT_TASK_CQE_F_DENIED": 2,
            "AGENT_TASK_CQE_F_LINK_FAILED": 3,
            "AGENT_TASK_HANDLE_F_OWNED": 0,
            "AGENT_TASK_HANDLE_F_BORROWED": 1,
        },
        "agent_lifecycle_abi.h": {
            "AGENT_WORKFLOW_SCHED_F_ACTIVE": 0,
            "AGENT_WORKFLOW_SCHED_F_RUNNABLE": 1,
            "AGENT_WORKFLOW_SCHED_F_ELIGIBLE": 2,
            "AGENT_WORKFLOW_SCHED_F_SLEEPING": 3,
            "AGENT_WORKFLOW_SCHED_F_FALLBACK": 4,
            "AGENT_WORKFLOW_LIFECYCLE_INFO_F_MATCH_CURRENT": 0,
        },
    }
    for relative, definitions in shifted.items():
        source = (root / relative).read_text(encoding="utf-8")
        for name, bit in definitions.items():
            require_shift_define(source, name, bit, relative)

    execution = (root / "agent_execution_contract_abi.h").read_text(
        encoding="utf-8"
    )
    task = (root / "agent_task_channel_abi.h").read_text(encoding="utf-8")
    lifecycle = (root / "agent_lifecycle_abi.h").read_text(encoding="utf-8")
    for owner, source, include in (
        ("execution contract", execution, '"agent_lifecycle_abi.h"'),
        ("Task Channel", task, '"agent_execution_contract_abi.h"'),
    ):
        if not re.search(
            rf"^#include\s+{re.escape(include)}\s*$", source, re.MULTILINE
        ):
            raise LayoutError(f"{owner} ABI does not own dependency {include}")
    if not re.search(
        r"offsetof\s*\(struct agent_workflow_lifecycle_info,\s*"
        r"scheduler_mode\)\s*==\s*\n?\s*"
        r"AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE",
        lifecycle,
    ):
        raise LayoutError("workflow lifecycle v3 no longer preserves its v2 prefix")
    for path in (root / "os" / "agent.h", root / "user" / "include" / "agent.h"):
        public = path.read_text(encoding="utf-8")
        for header in (
            "agent_execution_contract_abi.h",
            "agent_provenance_abi.h",
        ):
            if header not in public:
                raise LayoutError(f"{path} does not expose shared ABI {header}")


def parse_syscall_definitions(source):
    definitions = {}
    for prefix, name, value in re.findall(
        r"^\s*#define\s+(SYS_|__NR_)([A-Za-z0-9_]+)\s+(\d+)\s*$",
        source,
        re.MULTILINE,
    ):
        definitions[prefix + name] = int(value)
    return definitions


def validate_agent_syscall_numbers(root):
    expected = {
        "agent_execution_contract": 562,
        "agent_task_channel_setup": 563,
        "agent_task_channel_enter": 564,
        "agent_task_channel_resource": 565,
    }
    paths = (
        root / "os" / "syscall_ids.h",
        root / "user" / "lib" / "syscall_ids.h",
        root / "user" / "lib" / "arch" / "riscv" / "syscall_ids.h.in",
    )
    parsed = []
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as error:
            raise LayoutError(f"cannot read syscall mirror {path}: {error}") from error
        definitions = parse_syscall_definitions(source)
        parsed.append((path, definitions))
        for symbol, value in definitions.items():
            if value in expected.values():
                bare = symbol.removeprefix("SYS_").removeprefix("__NR_")
                if expected.get(bare) != value:
                    raise LayoutError(
                        f"{path} assigns reserved Agent syscall {value} to {symbol}"
                    )

    # 562 is already wired in the two generated public mirrors. The arch input
    # and all Task Channel entries become mandatory atomically once any Task
    # Channel mirror is added by the production integration change.
    for path, definitions in parsed[:2]:
        prefix = "SYS_"
        if definitions.get(prefix + "agent_execution_contract") != 562:
            raise LayoutError(f"{path} does not preserve Agent contract syscall 562")
    task_wiring_started = any(
        any(
            symbol.endswith(name) or value in {563, 564, 565}
            for symbol, value in definitions.items()
            for name in expected
            if name.startswith("agent_task_channel_")
        )
        for _path, definitions in parsed
    )
    if task_wiring_started:
        for path, definitions in parsed:
            prefix = "__NR_" if path.name.endswith(".in") else "SYS_"
            for name, value in expected.items():
                if definitions.get(prefix + name) != value:
                    raise LayoutError(
                        f"{path} must atomically mirror {prefix}{name}={value}"
                    )
        for path in (root / "os" / "agent.h", root / "user" / "include" / "agent.h"):
            source = path.read_text(encoding="utf-8")
            if not re.search(
                r'^#include\s+"[^"]*agent_task_channel_abi\.h"\s*$',
                source,
                re.MULTILINE,
            ):
                raise LayoutError(
                    f"{path} does not expose the wired Task Channel shared ABI"
                )


def validate_tool_protocol_schema(root):
    abi_path = root / "agent_tool_abi.h"
    protocol_path = root / "os" / "agent_tool_protocol.c"
    try:
        abi = abi_path.read_text(encoding="utf-8")
        protocol = protocol_path.read_text(encoding="utf-8")
    except OSError as error:
        raise LayoutError(f"cannot read Agent tool schema owner: {error}") from error

    key_size = define_value(abi, "AGENT_PARAM_KEY_SIZE")
    name_size = define_value(abi, "AGENT_TOOL_NAME_SIZE")
    params_size = define_value(abi, "AGENT_TOOL_PARAMS_SIZE")
    desc_size = define_value(abi, "AGENT_TOOL_DESC_SIZE")
    param_max = define_value(abi, "AGENT_TOOL_PARAM_MAX")
    tool_count = define_value(abi, "AGENT_TOOL_COUNT")
    try:
        key_block = abi[
            abi.index("#define AGENT_PARAM_KEY_REGISTRY(X)") :
            abi.index("#define AGENT_PARAM_KEY_ASSERT")
        ]
        tool_block = protocol[
            protocol.index("#define AGENT_TOOL_REGISTRY(X)") :
            protocol.index("#define ASSERT_TOOL_STRINGS")
        ]
        security_block = protocol[
            protocol.index("#define AGENT_TOOL_SECURITY_REGISTRY(X)") :
            protocol.index("#define SECURITY_ENTRY")
        ]
    except ValueError as error:
        raise LayoutError(
            "Agent tool schema lacks its declarative protocol/security registry"
        ) from error

    key_assert = re.search(
        r"#define\s+AGENT_PARAM_KEY_ASSERT\s*\(\s*symbol\s*,\s*literal\s*\)"
        r"\s*\\\s*\n\s*_Static_assert\s*\(\s*sizeof\s*\(\s*literal\s*\)"
        r"\s*<=\s*AGENT_PARAM_KEY_SIZE",
        abi,
    )
    if not key_assert or not re.search(
        r"AGENT_PARAM_KEY_REGISTRY\s*\(\s*AGENT_PARAM_KEY_ASSERT\s*\)", abi
    ):
        raise LayoutError("Agent parameter keys lack their compile-time capacity guard")

    key_entries = re.findall(
        r'\bX\(\s*([A-Z][A-Z0-9_]*)\s*,\s*"([^"\n]*)"\s*\)',
        key_block,
    )
    if not key_entries:
        raise LayoutError("Agent parameter key registry is empty")
    key_map = dict(key_entries)
    if len(key_map) != len(key_entries) or len(set(key_map.values())) != len(key_map):
        raise LayoutError("Agent parameter key registry contains duplicates")
    for symbol, literal in key_entries:
        try:
            encoded = literal.encode("ascii")
        except UnicodeEncodeError as error:
            raise LayoutError(f"Agent parameter key {symbol} is not ASCII") from error
        if not literal or len(encoded) + 1 > key_size:
            raise LayoutError(
                f"Agent parameter key {symbol} is not NUL-terminated within "
                f"AGENT_PARAM_KEY_SIZE={key_size}"
            )

    tool_entries = re.findall(
        r'\bX\(\s*(AGENT_TOOL_[A-Z0-9_]+)\s*,\s*'
        r'(AGENT_TOOL_F_[A-Z0-9_]+)\s*,\s*"([^"\n]*)"\s*,\s*'
        r'"([^"\n]*)"\s*\)',
        tool_block,
    )
    if len(tool_entries) != tool_count:
        raise LayoutError(
            f"Agent tool registry has {len(tool_entries)} entries, expected {tool_count}"
        )
    numeric_ids = {}
    for symbol, value in re.findall(
        r"^\s*#define\s+(AGENT_TOOL_[A-Z0-9_]+)\s+(\d+)\s*$",
        abi,
        re.MULTILINE,
    ):
        if symbol not in {"AGENT_TOOL_COUNT", "AGENT_TOOL_PARAM_MAX"}:
            numeric_ids[symbol] = int(value)
    registered_ids = [numeric_ids.get(entry[0]) for entry in tool_entries]
    if registered_ids != list(range(1, tool_count + 1)):
        raise LayoutError("Agent tool registry ids are not exact and ordered")
    names = [entry[2] for entry in tool_entries]
    if len(set(names)) != len(names):
        raise LayoutError("Agent tool registry contains duplicate names")
    for tool_id, flags, name, description in tool_entries:
        if flags not in {"AGENT_TOOL_F_CALLABLE", "AGENT_TOOL_F_SYSCALL_ONLY"}:
            raise LayoutError(f"Agent tool {tool_id} has an invalid flag")
        if not name or len(name.encode("ascii")) + 1 > name_size:
            raise LayoutError(f"Agent tool {tool_id} name exceeds its wire field")
        if not description or len(description.encode("ascii")) + 1 > desc_size:
            raise LayoutError(
                f"Agent tool {tool_id} description exceeds its wire field"
            )

    security_rows = []
    for raw in re.findall(r"\bX\(([^)\n]*)\)", security_block):
        fields = tuple(field.strip() for field in raw.split(","))
        if len(fields) != 4 or any(not field for field in fields):
            raise LayoutError("Agent tool security registry has an invalid row")
        security_rows.append(fields)
    if len(security_rows) != tool_count:
        raise LayoutError(
            "Agent tool protocol/security registries are not one-to-one: "
            f"{len(tool_entries)} protocol rows, {len(security_rows)} security rows"
        )

    capability_names = set(
        re.findall(r"^#define\s+(AGENT_CAP_[A-Z0-9_]+)\b", protocol, re.MULTILINE)
    )
    if not capability_names:
        capability_names = set(
            re.findall(r"^#define\s+(AGENT_CAP_[A-Z0-9_]+)\b", abi, re.MULTILINE)
        )
    # Capability definitions live in the including Agent UAPI rather than the
    # tool ABI. Read both mirrors and accept only names common to both views.
    kernel_agent = (root / "os" / "agent.h").read_text(encoding="utf-8")
    user_agent = (root / "user" / "include" / "agent.h").read_text(
        encoding="utf-8"
    )
    kernel_caps = set(
        re.findall(r"^#define\s+(AGENT_CAP_[A-Z0-9_]+)\b", kernel_agent, re.MULTILINE)
    )
    user_caps = set(
        re.findall(r"^#define\s+(AGENT_CAP_[A-Z0-9_]+)\b", user_agent, re.MULTILINE)
    )
    capability_names |= kernel_caps & user_caps
    provenance_names = set(
        re.findall(
            r"^#define\s+((?:AGENT_PROVENANCE|PROV_)[A-Z0-9_]+)\b",
            protocol,
            re.MULTILINE,
        )
    )
    provenance_names |= set(
        re.findall(
            r"^#define\s+(AGENT_PROVENANCE_[A-Z0-9_]+)\b",
            (root / "agent_provenance_abi.h").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    effect_names = set(
        re.findall(
            r"^#define\s+(AGENT_SIDE_EFFECT_[A-Z0-9_]+)\b",
            (root / "agent_provenance_abi.h").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )

    def validate_security_expression(expression, allowed, label, tool_id):
        if re.search(r"\b[1-9][0-9A-Fa-fxX]*", expression):
            raise LayoutError(
                f"Agent tool {tool_id} {label} uses an unowned numeric mask"
            )
        identifiers = set(re.findall(r"\b[A-Z][A-Z0-9_]+\b", expression))
        unknown = sorted(identifiers - allowed)
        residue = re.sub(r"\b[A-Z][A-Z0-9_]+\b|\b0(?:U|UL|ULL)?\b|[\s|&~()]", "", expression)
        if unknown or residue:
            detail = unknown if unknown else residue
            raise LayoutError(
                f"Agent tool {tool_id} {label} contains invalid mask terms {detail!r}"
            )

    for tool, (caps, accepted, output, effects) in zip(tool_entries, security_rows):
        tool_id = tool[0]
        validate_security_expression(caps, capability_names, "capabilities", tool_id)
        validate_security_expression(
            accepted, provenance_names, "accepted provenance", tool_id
        )
        validate_security_expression(
            output, provenance_names, "output provenance", tool_id
        )
        validate_security_expression(effects, effect_names, "side effects", tool_id)

    required_binding_fragments = (
        "AGENT_TOOL_SECURITY_REGISTRY(SECURITY_ENTRY)",
        "sizeof(agent_tool_security) / sizeof(agent_tool_security[0]) ==\n"
        "\t       AGENT_TOOL_COUNT",
        "tool_by_id(tool_id) != 0 ? &agent_tool_security[tool_id - 1] : 0",
        "manifest->provenance.accepted_input_labels =\n"
        "\t\tsecurity->accepted_input_labels",
        "manifest->provenance.output_add_labels = security->output_add_labels",
        "manifest->provenance.required_capabilities =\n"
        "\t\tsecurity->required_capabilities",
        "manifest->provenance.side_effect_mask = security->side_effect_mask",
    )
    for fragment in required_binding_fragments:
        if fragment not in protocol:
            raise LayoutError(
                "Agent tool manifest no longer combines the ordered protocol and "
                "security registries by their common tool id"
            )
    digest_start = protocol.find("agent_tool_protocol_schema_digest(")
    digest_end = protocol.find("agent_tool_protocol_manifest_query(", digest_start)
    if digest_start < 0 or digest_end < 0:
        raise LayoutError("Agent tool manifest digest owner is missing")
    digest = protocol[digest_start:digest_end]
    for field in (
        "required_capabilities",
        "accepted_input_labels",
        "output_add_labels",
        "side_effect_mask",
    ):
        if f"security->{field}" not in digest:
            raise LayoutError(
                f"Agent tool manifest digest does not bind security field {field}"
            )

    if re.search(r'\bR\(\s*"', protocol):
        raise LayoutError("Agent tool rule bypasses the canonical key registry")
    try:
        rules_start = protocol.index(
            "static const struct param_rule rules[] = {"
        )
        rules_end = protocol.index("};", rules_start)
        offsets_start = protocol.index(
            "static const unsigned char rule_offsets[AGENT_TOOL_COUNT + 1] = {"
        )
        offsets_end = protocol.index("};", offsets_start)
    except ValueError as error:
        raise LayoutError("Agent tool schema lacks its compact CSR table") from error

    rules_block = protocol[rules_start:rules_end]
    rules = re.findall(
        r"\bR\(\s*([A-Z][A-Z0-9_]*)\s*,\s*"
        r"(AGENT_PARAM_(?:STRING|UINT64))\s*,\s*"
        r"(PARAM_(?:ARG0|ARG1|PAYLOAD))\s*,\s*([01])\s*\)",
        rules_block,
    )
    if len(rules) != len(re.findall(r"\bR\(", rules_block)):
        raise LayoutError("Agent tool compact rule table contains an invalid rule")

    offsets_block = protocol[offsets_start:offsets_end]
    offsets_body = offsets_block[offsets_block.index("{") + 1 :]
    offsets_body = re.sub(r"/\*.*?\*/|//[^\n]*", "", offsets_body, flags=re.DOTALL)
    residue = re.sub(r"\d+|[\s,]", "", offsets_body)
    if residue:
        raise LayoutError("Agent tool CSR offsets contain a non-numeric entry")
    offsets = [int(value) for value in re.findall(r"\d+", offsets_body)]
    if len(offsets) != tool_count + 1:
        raise LayoutError("Agent tool CSR offset count does not match the ABI")
    if (
        offsets[0] != 0
        or offsets[-1] != len(rules)
        or any(left > right for left, right in zip(offsets, offsets[1:]))
    ):
        raise LayoutError("Agent tool CSR offsets do not cover the compact rules")

    count_match = re.search(
        r"^\s*#define\s+AGENT_TOOL_RULE_COUNT\s+(\d+)U?\s*$",
        protocol,
        re.MULTILINE,
    )
    if not count_match or int(count_match.group(1)) != len(rules):
        raise LayoutError("Agent tool compact rule count is inconsistent")

    used_keys = set()
    for index, (tool_id, _flags, _name, _description) in enumerate(tool_entries):
        tool_rules = rules[offsets[index] : offsets[index + 1]]
        if len(tool_rules) > param_max:
            raise LayoutError(f"Agent tool {tool_id} exceeds its parameter bound")
        targets = set()
        schema = []
        for key, value_type, target, required in tool_rules:
            if key not in key_map:
                raise LayoutError(f"Agent tool {tool_id} uses unregistered key {key}")
            if target in targets:
                raise LayoutError(f"Agent tool {tool_id} repeats target {target}")
            targets.add(target)
            used_keys.add(key)
            if (target == "PARAM_PAYLOAD") != (value_type == "AGENT_PARAM_STRING"):
                raise LayoutError(f"Agent tool {tool_id} has a key/type mismatch")
            suffix = "string" if value_type == "AGENT_PARAM_STRING" else "uint64"
            schema.append(f"{key_map[key]}{'?' if required == '0' else ''}:{suffix}")
        rendered = ",".join(schema) if schema else "none"
        if len(rendered.encode("ascii")) + 1 > params_size:
            raise LayoutError(f"Agent tool {tool_id} schema exceeds its wire field")
    if used_keys != set(key_map):
        raise LayoutError("Agent parameter key registry and rule table have stale entries")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--build-dir", default="build/ci")
    parser.add_argument("--cc", required=True)
    parser.add_argument("--nm", required=True)
    parser.add_argument("--golden", default="ci/agent-uapi-layout.json")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    validate_compatibility_tombstones(root)
    validate_feature_abi_constants(root)
    validate_agent_syscall_numbers(root)
    validate_tool_protocol_schema(root)
    build_dir = (root / args.build_dir).resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    compile_shared_header_probes(root, build_dir, args.cc)
    kernel_obj = compile_probe(root, build_dir, args.cc, "kernel", root / "os")
    user_obj = compile_probe(
        root, build_dir, args.cc, "user", root / "user" / "include"
    )
    kernel = symbols(args.nm, kernel_obj, root)
    user = symbols(args.nm, user_obj, root)
    compare(kernel, user)
    golden = load_golden((root / args.golden).resolve())
    compare_golden(kernel, golden)
    print(
        f"[agent-uapi] {len(kernel)} kernel/user/frozen size/offset "
        "contracts match"
    )


if __name__ == "__main__":
    try:
        main()
    except LayoutError as error:
        raise SystemExit(f"[agent-uapi] failed: {error}")
