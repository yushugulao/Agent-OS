#!/usr/bin/env python3
"""Check the closed worker-batch grouping, build, and wire contracts."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


class ContractError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def macro_lines(text: str, name: str) -> list[str]:
    lines = text.splitlines()
    marker = f"#define {name}(APPLY)"
    for start, line in enumerate(lines):
        if line.startswith(marker):
            body: list[str] = []
            current = start
            while lines[current].rstrip().endswith("\\"):
                current += 1
                require(current < len(lines), f"unterminated macro {name}")
                body.append(lines[current].strip().rstrip("\\").strip())
            return body
    raise ContractError(f"missing macro {name}")


def applied_tokens(text: str, name: str, arity: int) -> list[tuple[str, ...]]:
    result: list[tuple[str, ...]] = []
    for line in macro_lines(text, name):
        match = re.fullmatch(r"APPLY\((.*)\)", line)
        require(match is not None, f"malformed {name} line: {line}")
        fields = tuple(part.strip().strip('"') for part in match.group(1).split(","))
        require(len(fields) == arity, f"wrong arity in {name}: {line}")
        result.append(fields)
    return result


def make_words(text: str, variable: str) -> list[str]:
    match = re.search(rf"^{re.escape(variable)}\s*:=\s*(.*)$", text, re.MULTILINE)
    require(match is not None, f"missing Makefile variable {variable}")
    return match.group(1).split()


def check(root: Path) -> None:
    manifest_path = root / "user/include/rp_program_manifest.h"
    makefile_path = root / "user/Makefile"
    protocol_path = root / "user/include/rp_worker_batch.h"
    data_only_path = root / "user/lib/research_platform_state.c"
    manifest = manifest_path.read_text(encoding="utf-8")
    makefile = makefile_path.read_text(encoding="utf-8")
    protocol = protocol_path.read_text(encoding="utf-8")
    data_only = data_only_path.read_text(encoding="utf-8")

    platform = [entry[0] for entry in applied_tokens(manifest, "RP_PLATFORM_PROGRAMS", 1)]
    roles = [entry[0] for entry in applied_tokens(manifest, "RP_AGENTOS_ROLE_PROGRAMS", 2)]
    direct = [entry[0] for entry in applied_tokens(manifest, "RP_WORKER_DIRECT_PROGRAMS", 1)]
    groups = applied_tokens(manifest, "RP_WORKER_BATCH_GROUPS", 3)
    require(len(platform) == 70 and len(set(platform)) == 70,
            "platform manifest must contain 70 unique programs")
    require(len(roles) == 10 and len(set(roles)) == 10,
            "role manifest must contain 10 unique programs")
    require(direct == ["rp_compare_plain", "rp_test_suite"],
            "direct support set must remain compare_plain then test_suite")
    require(groups == [("0", "rp_wbatch0", "32"),
                       ("1", "rp_wbatch1", "9"),
                       ("2", "rp_wbatch2", "17")],
            "worker batch group descriptors changed")
    require("#define RP_WORKER_BATCH_GROUP_COUNT 3" in manifest,
            "worker batch group count must be 3")
    require("#define RP_WORKER_BATCH_PROGRAM_COUNT 58" in manifest,
            "worker batch program count must be 58")
    require("#define RP_WORKER_DIRECT_PROGRAM_COUNT 2" in manifest,
            "worker direct program count must be 2")

    flattened: list[str] = []
    for group, runner, count_text in groups:
        entries = applied_tokens(manifest, f"RP_WORKER_BATCH_{group}_PROGRAMS", 2)
        indices = [int(entry[0]) for entry in entries]
        programs = [entry[1] for entry in entries]
        require(indices == list(range(len(entries))),
                f"batch {group} indices must be contiguous from zero")
        require(len(entries) == int(count_text), f"batch {group} count mismatch")
        require(make_words(makefile, f"WORKER_BATCH_{group}_PROGRAMS") == programs,
                f"Makefile batch {group} differs from canonical manifest")
        require(len(runner) <= 14, f"runner {runner} exceeds DIRSIZ")
        source = root / f"user/src/{runner}.c"
        require(source.is_file(), f"missing dispatcher {source.relative_to(root)}")
        dispatcher = source.read_text(encoding="utf-8")
        require(f"RP_WORKER_BATCH_{group}_PROGRAMS(RP_BATCH_CASE)" in dispatcher,
                f"dispatcher {runner} lacks direct switch expansion")
        require(f"rp_worker_batch_start({group}, {count_text})" in dispatcher,
                f"dispatcher {runner} has wrong group/count")
        require("switch (index)" in dispatcher and "(*run)" not in dispatcher,
                f"dispatcher {runner} must use direct switch calls")
        require("rp_worker_batch_next()" in dispatcher and
                "rp_worker_batch_report(rp_worker_run" in dispatcher,
                f"dispatcher {runner} does not implement the shared state machine")
        flattened.extend(programs)

    expected_batched = [program for program in platform
                        if program not in set(roles) | set(direct)]
    require(flattened == expected_batched,
            "batch union/order must equal platform minus roles and two direct programs")
    require(len(flattened) == 58 and len(set(flattened)) == 58,
            "batch union must contain 58 unique programs")
    require(not (set(flattened) & set(roles)) and
            not (set(flattened) & set(direct)) and
            not (set(roles) & set(direct)),
            "batch, role, and direct support sets must be disjoint")
    require(set(flattened) | set(roles) | set(direct) == set(platform),
            "batch, role, and direct support sets must close over all 70 programs")

    require(make_words(makefile, "WORKER_BATCH_APPS") ==
            [runner for _, runner, _ in groups],
            "Makefile runner list differs from canonical manifest")
    require(make_words(makefile, "WORKER_BATCH_DIRECT_PROGRAMS") == direct,
            "Makefile direct support list differs from canonical manifest")
    require("CH_TESTS := rp_agentos_orch rp_resource_probe $(PLATFORM_TESTS) $(WORKER_BATCH_APPS)" in makefile,
            "platform_agentos image must include all worker runners")
    require("WORKER_BATCH_FLAT_MAX := 258048" in makefile and
            "exceeds reserved MAXFILE limit" in makefile,
            "build must enforce MAXFILE minus the 16 KiB reserve")
    require("$(foreach group,0 1 2,$(eval $(call RP_WORKER_BATCH_LINK_RULE,$(group))))" in makefile,
            "link rule must instantiate exactly three runners")
    require("-Dmain=$*_worker_entry" in makefile,
            "batch application objects need unique entry symbols")
    for token in ("STACK_USAGE_ALL_LIBRARY_SRCS := $(addprefix user/,$(sort $(LIB_C)))",
                  "STACK_USAGE_DATA_ONLY_LIBRARY_SRCS := user/lib/research_platform_state.c",
                  "STACK_USAGE_FUNCTION_LIBRARY_SRCS := $(filter-out $(STACK_USAGE_DATA_ONLY_LIBRARY_SRCS),$(STACK_USAGE_ALL_LIBRARY_SRCS))",
                  "grep -Eq '[[:space:]]F[[:space:]]'"):
        require(token in makefile, f"data-only stack classification missing: {token}")
    uncommented_data = re.sub(r"/\*.*?\*/|//[^\r\n]*", "", data_only,
                              flags=re.DOTALL)
    require(re.search(r"\b[A-Za-z_]\w*\s*\([^;{}]*\)\s*\{",
                      uncommented_data) is None,
            "research platform scratch storage must remain data-only")
    for symbol in ("rp_state_buf", "rp_host_seed_buf", "rp_host_seed_loaded"):
        require(re.search(rf"\b{symbol}\b", uncommented_data) is not None,
                f"data-only scratch definition missing: {symbol}")

    require("#define RP_WORKER_BATCH_MAX_FD 15" in protocol,
            "wire argv must reject descriptors outside the 16-slot table")
    overflow_guard = "value > (RP_WORKER_BATCH_MAX_FD - digit) / 10"
    accumulation = "value = value * 10 + digit"
    require(overflow_guard in protocol and accumulation in protocol and
            protocol.index(overflow_guard) < protocol.index(accumulation),
            "fd parser must reject overflow before decimal accumulation")
    for token in ("RP_WORKER_BATCH_MAGIC 0x52505742U",
                  "RP_WORKER_BATCH_VERSION 1U",
                  "rp_worker_batch_read_exact", "rp_worker_batch_write_exact",
                  "sizeof(struct rp_worker_batch_frame) == 32",
                  "frame->guard == rp_worker_batch_guard(frame)",
                  "runtime->expected < runtime->count",
                  "runtime->expected == runtime->count",
                  "memset(rp_state_buf, 0, sizeof(rp_state_buf))",
                  "if (status != 0)\n\t\trp_worker_batch_finish();"):
        require(token in protocol, f"protocol invariant missing: {token}")
    require("rp_host_seed_buf" not in protocol and "(*run)" not in protocol,
            "protocol must preserve host seed cache and avoid indirect entries")
    require(re.search(r"\bagent_[A-Za-z0-9_]*\s*\(", protocol) is None,
            "non-Agent dispatcher protocol must not call an Agent API")

    for stale in (root / "user/src/rp_wbatch3.c", root / "user/src/rp_wbatch4.c"):
        require(not stale.exists(), f"one-shot runner must not exist: {stale.name}")
    for _, runner, _ in groups:
        binary = root / f"user/build/bin/{runner}"
        if binary.exists():
            require(binary.stat().st_size <= 258048,
                    f"{runner} flat binary exceeds 258048 bytes")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except ContractError as error:
        print(f"worker-batch contract: FAIL: {error}")
        return 1
    print("worker-batch contract: PASS (58 batched, 2 direct, 10 role)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
