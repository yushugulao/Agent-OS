#!/usr/bin/env python3
"""Compile both Agent UAPI views and require identical public layouts."""

import argparse
import json
import re
import subprocess
from pathlib import Path


PREFIX = "agent_uapi_layout_"


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
        "-c",
        "scripts/probes/agent-uapi-layout.c",
        "-o",
        output_arg,
    ]
    run(command, f"{view} Agent UAPI probe compile", cwd=root)
    return output


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


def validate_metadata_disk_abi_owner(root):
    abi = root / "agent_metadata_disk_abi.h"
    try:
        source = abi.read_text(encoding="utf-8")
        kernel = (root / "os" / "agent_metadata_disk.h").read_text(
            encoding="utf-8"
        )
        durable = (root / "os" / "agent_durable_section.h").read_text(
            encoding="utf-8"
        )
        durable_source = (root / "os" / "agent_durable_section.c").read_text(
            encoding="utf-8"
        )
        mkfs = (root / "nfs" / "fs.c").read_text(encoding="utf-8")
    except OSError as error:
        raise LayoutError(f"cannot read metadata disk ABI owner: {error}") from error
    for token in (
        "#define AGENT_META_STORE_MAGIC ",
        "struct agent_meta_store_header {",
        "struct agent_durable_arena {",
        "agent_meta_disk_init_genesis(",
    ):
        if token not in source:
            raise LayoutError(f"shared metadata disk ABI lacks {token.strip()}")
        owners = [
            path
            for path in (abi, *sorted((root / "os").glob("*.h")),
                         *sorted((root / "nfs").glob("*.h")))
            if token in path.read_text(encoding="utf-8")
        ]
        if owners != [abi]:
            raise LayoutError(
                f"metadata disk ABI token has multiple owners: {owners!r}"
            )
    include = '#include "../agent_metadata_disk_abi.h"'
    if include not in kernel or include not in durable or include not in mkfs:
        raise LayoutError("kernel and mkfs must include the shared metadata disk ABI")
    if "agent_meta_disk_init_genesis(&genesis)" not in mkfs:
        raise LayoutError("mkfs bypasses the canonical metadata genesis builder")
    if "agent_durable_disk_init_empty(arena)" not in durable_source:
        raise LayoutError("kernel bypasses the canonical durable arena builder")


def define_value(source, name):
    match = re.search(
        rf"^\s*#define\s+{re.escape(name)}\s+(\d+)U?\s*$",
        source,
        re.MULTILINE,
    )
    if not match:
        raise LayoutError(f"Agent tool ABI lacks numeric {name}")
    return int(match.group(1))


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
    except ValueError as error:
        raise LayoutError("Agent tool schema lacks its declarative registry") from error

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

    if re.search(r'\bR\(\s*"', protocol):
        raise LayoutError("Agent tool rule bypasses the canonical key registry")
    rule_rows = re.findall(
        r"\[(AGENT_TOOL_[A-Z0-9_]+)\s*-\s*1\]\s*=\s*\{([^}]*)\}",
        protocol,
    )
    if len({row[0] for row in rule_rows}) != len(rule_rows):
        raise LayoutError("Agent tool rule table contains duplicate rows")
    known_tools = {entry[0] for entry in tool_entries}
    used_keys = set()
    for tool_id, body in rule_rows:
        if tool_id not in known_tools:
            raise LayoutError(f"Agent tool rules reference unknown {tool_id}")
        rules = re.findall(
            r"\bR\(\s*([A-Z][A-Z0-9_]*)\s*,\s*"
            r"(AGENT_PARAM_(?:STRING|UINT64))\s*,\s*"
            r"(PARAM_(?:ARG0|ARG1|PAYLOAD))\s*,\s*([01])\s*\)",
            body,
        )
        if len(rules) != len(re.findall(r"\bR\(", body)):
            raise LayoutError(f"Agent tool {tool_id} contains an invalid rule")
        if len(rules) > param_max:
            raise LayoutError(f"Agent tool {tool_id} exceeds its parameter bound")
        targets = set()
        schema = []
        for key, value_type, target, required in rules:
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
    validate_metadata_disk_abi_owner(root)
    validate_tool_protocol_schema(root)
    build_dir = (root / args.build_dir).resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
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
