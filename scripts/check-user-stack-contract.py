#!/usr/bin/env python3
"""验证共享用户栈与 exec argv 布局契约。"""

import argparse
import re
import sys
from pathlib import Path


EXPECTED = {
    "USER_STACK_SIZE_BYTES": 4096,
    "USER_STACK_ARGV_LAYOUT_BYTES": 1024,
    "USER_STACK_CALL_PATH_BYTES": 3072,
    "USER_STACK_ALIGNMENT_BYTES": 16,
    "USER_STACK_POINTER_BYTES": 8,
}
MARKER = (
    "usersafety_ucore: argv_layout_budget=1024 boundary_accept=1 "
    "over_limit_rejected=1 caller_live=1"
)
DEFINE_RE = re.compile(
    r"^\s*#define\s+([A-Z0-9_]+)\s+([0-9]+)(?:U|UL|ULL|L|LL)?\s*$",
    re.MULTILINE,
)
FUNCTION_DEFINITION_RE = re.compile(
    r"\b[A-Za-z_]\w*\s*\([^;{}]*\)\s*\{", re.DOTALL
)


def compact(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\r\n]*", "", text)
    return re.sub(r"\s+", "", text)


def function_body(text, name):
    match = re.search(
        rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{", text, re.DOTALL
    )
    if match is None:
        raise ValueError(f"missing function definition: {name}")
    start = text.find("{", match.start())
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index]
    raise ValueError(f"unterminated function definition: {name}")


def require_contains(text, needle, description):
    if needle not in text:
        raise ValueError(f"missing {description}")


def read(root, relative):
    path = root / relative
    if not path.is_file():
        raise ValueError(f"missing contract file: {relative}")
    return path.read_text(encoding="utf-8")


def parse_contract(text):
    values = {}
    for name, raw in DEFINE_RE.findall(text):
        if name in EXPECTED:
            if name in values:
                raise ValueError(f"duplicate stack policy constant: {name}")
            values[name] = int(raw)
    missing = set(EXPECTED) - set(values)
    if missing:
        raise ValueError("missing stack policy constants: " + ", ".join(sorted(missing)))
    drift = [
        f"{name}={values[name]} expected={expected}"
        for name, expected in EXPECTED.items()
        if values[name] != expected
    ]
    if drift:
        raise ValueError("stack policy constant drift: " + ", ".join(drift))
    if (
        values["USER_STACK_ARGV_LAYOUT_BYTES"]
        + values["USER_STACK_CALL_PATH_BYTES"]
        != values["USER_STACK_SIZE_BYTES"]
    ):
        raise ValueError("stack policy partitions do not cover the stack")
    return values


def aligned(value, alignment):
    return (value + alignment - 1) & ~(alignment - 1)


def check_layout_model(values, user_source):
    modes = {}
    for name in ("EXEC_LAYOUT_BOUNDARY_MODE", "EXEC_LAYOUT_OVERFLOW_MODE"):
        match = re.search(rf'^#define\s+{name}\s+"([^"]+)"$', user_source, re.MULTILINE)
        if match is None:
            raise ValueError(f"missing usersafety mode: {name}")
        modes[name] = len(match.group(1).encode("ascii")) + 1
    sizes = {}
    for name in (
        "EXEC_LAYOUT_BOUNDARY_ARG_BYTES",
        "EXEC_LAYOUT_OVERFLOW_ARG_BYTES",
    ):
        match = re.search(rf"^#define\s+{name}\s+([0-9]+)$", user_source, re.MULTILINE)
        if match is None:
            raise ValueError(f"missing usersafety argument size: {name}")
        sizes[name] = int(match.group(1))
    pointer_bytes = 3 * values["USER_STACK_POINTER_BYTES"]

    def layout(mode, argument):
        return (
            aligned(mode, values["USER_STACK_ALIGNMENT_BYTES"])
            + aligned(argument, values["USER_STACK_ALIGNMENT_BYTES"])
            + aligned(pointer_bytes, values["USER_STACK_ALIGNMENT_BYTES"])
        )

    boundary = layout(
        modes["EXEC_LAYOUT_BOUNDARY_MODE"], sizes["EXEC_LAYOUT_BOUNDARY_ARG_BYTES"]
    )
    overflow = layout(
        modes["EXEC_LAYOUT_OVERFLOW_MODE"], sizes["EXEC_LAYOUT_OVERFLOW_ARG_BYTES"]
    )
    overflow_raw = (
        modes["EXEC_LAYOUT_OVERFLOW_MODE"]
        + sizes["EXEC_LAYOUT_OVERFLOW_ARG_BYTES"]
        + pointer_bytes
    )
    if boundary != values["USER_STACK_ARGV_LAYOUT_BYTES"]:
        raise ValueError(f"usersafety boundary layout is {boundary}, not 1024")
    if overflow <= values["USER_STACK_ARGV_LAYOUT_BYTES"]:
        raise ValueError("usersafety overflow layout does not exceed the argv budget")
    if overflow_raw > values["USER_STACK_ARGV_LAYOUT_BYTES"]:
        raise ValueError("usersafety overflow does not isolate alignment/pointer accounting")


def check(root):
    policy = read(root, "user_stack_policy.h")
    values = parse_contract(policy)
    helper_source = read(root, "os/user_stack_layout.h")
    loader = compact(read(root, "os/loader.h"))
    proc_source = read(root, "os/proc.c")
    syscall_source = read(root, "os/syscall.c")
    user_source = read(root, "user/src/usersafety_ucore.c")
    user_make = compact(read(root, "user/Makefile"))
    data_only_source = read(root, "user/lib/research_platform_state.c")
    runner = read(root, "scripts/run-agent-tests.sh")
    validator = read(root, "scripts/validate-kernel-test-log.py")
    root_make = read(root, "Makefile")
    user_wrapper = compact(read(root, "user/include/user_stack_policy.h"))
    read(root, "scripts/test-check-user-stack-contract.py")

    require_contains(loader, '#include"../user_stack_policy.h"', "loader policy include")
    require_contains(loader, "#defineUSTACK_SIZE(USER_STACK_SIZE_BYTES)", "shared stack size")
    require_contains(loader, "_Static_assert(USTACK_SIZE==PAGE_SIZE", "one-page assertion")
    require_contains(
        user_wrapper,
        '#include"../../user_stack_policy.h"',
        "user policy wrapper include",
    )

    align_body = compact(function_body(helper_source, "user_stack_layout_align"))
    add_body = compact(
        function_body(helper_source, "user_stack_argv_layout_add_string")
    )
    finish_body = compact(
        function_body(helper_source, "user_stack_argv_layout_finish")
    )
    require_contains(
        align_body,
        "(value+USER_STACK_ALIGNMENT_BYTES-1)&~(USER_STACK_ALIGNMENT_BYTES-1)",
        "16-byte layout alignment",
    )
    require_contains(
        add_body,
        "bytes>USER_STACK_ARGV_LAYOUT_BYTES-layout->used",
        "string addition overflow boundary",
    )
    require_contains(
        add_body,
        "user_stack_layout_align(layout->used+bytes)",
        "per-string alignment accounting",
    )
    require_contains(add_body, "layout->argc++", "argv count accounting")
    require_contains(
        finish_body,
        "pointer_bytes=(layout->argc+1)*USER_STACK_POINTER_BYTES",
        "argv pointer-vector accounting",
    )
    require_contains(
        finish_body,
        "pointer_bytes>USER_STACK_ARGV_LAYOUT_BYTES-layout->used",
        "pointer-vector budget boundary",
    )
    require_contains(
        finish_body,
        "user_stack_layout_align(layout->used+pointer_bytes)",
        "pointer-vector alignment accounting",
    )

    proc_body = compact(function_body(proc_source, "push_argv_image"))
    for needle, description in (
        ("user_stack_argv_layout_init(&layout)", "loader layout initialization"),
        ("user_stack_argv_layout_add_string(&layout,n)", "loader string accounting"),
        ("user_stack_argv_layout_finish(&layout,&layout_bytes)", "loader final accounting"),
        ("argp[argc]=stack_top-layout.used", "precomputed string placement"),
        ("argv_sp=stack_top-layout_bytes", "precomputed argv placement"),
        ("user_range_check(pagetable,argv_sp,layout_bytes,PTE_W)", "stack range preflight"),
        ("stack_base&(USER_STACK_ALIGNMENT_BYTES-1)", "stack alignment preflight"),
    ):
        require_contains(proc_body, needle, description)
    finish_at = proc_body.index("user_stack_argv_layout_finish")
    range_at = proc_body.index("user_range_check")
    copy_at = proc_body.index("copyout")
    if not finish_at < range_at < copy_at:
        raise ValueError("push_argv_image writes before complete layout/range preflight")

    syscall_body = compact(function_body(syscall_source, "copy_exec_args"))
    require_contains(
        syscall_body,
        "user_stack_argv_layout_add_string(&layout,(uint64)len+1)",
        "syscall string layout accounting",
    )
    require_contains(
        syscall_body,
        "USER_STACK_ARGV_LAYOUT_BYTES-storage_used",
        "syscall bounded argument copy",
    )
    if syscall_body.count("user_stack_argv_layout_finish(") < 3:
        raise ValueError("copy_exec_args does not validate empty, terminal, and incremental layouts")
    if "USTACK_SIZE-storage_used" in syscall_body or "stack_left" in syscall_body:
        raise ValueError("copy_exec_args retains an independent whole-stack budget")

    require_contains(user_make, "USER_STACK_CONTRACT:=../user_stack_policy.h", "Make policy source")
    require_contains(
        user_make,
        "USER_STACK_CONTRACT_WRAPPER:=include/user_stack_policy.h",
        "user policy wrapper dependency",
    )
    require_contains(
        user_make,
        "--contract-header$(USER_STACK_CONTRACT)",
        "call-path checker policy wiring",
    )
    require_contains(
        user_make,
        "COMPAT_BENCH_REPO_SOURCE:=evaluation_guest/compatbench.c"
        "COMPAT_BENCH_SOURCE:=../$(COMPAT_BENCH_REPO_SOURCE)",
        "canonical compatibility benchmark source",
    )
    require_contains(
        user_make,
        "STACK_USAGE_ALL_LIBRARY_SRCS:=$(addprefixuser/,$(sort$(LIB_C)))",
        "complete stack library inventory",
    )
    require_contains(
        user_make,
        "STACK_USAGE_DATA_ONLY_LIBRARY_SRCS:=user/lib/research_platform_state.c",
        "data-only stack library inventory",
    )
    require_contains(
        user_make,
        "STACK_USAGE_FUNCTION_LIBRARY_SRCS:="
        "$(filter-out$(STACK_USAGE_DATA_ONLY_LIBRARY_SRCS),"
        "$(STACK_USAGE_ALL_LIBRARY_SRCS))",
        "function/data-only stack inventory partition",
    )
    require_contains(
        user_make,
        "STACK_USAGE_SUPPORT_SRCS:="
        "$(addprefixuser/src/,$(addsuffix.c,$(WORKER_BATCH_PROGRAMS)))",
        "complete worker support stack inventory",
    )
    require_contains(
        user_make,
        "RETIRED_GUEST_APPS:=agentmetacrash_ucoreagentmetarecover_ucore\\"
        "agentmetaeio_ucoreagentmetalarge_ucoreagentmetatransient_ucore\\"
        "agentobsreboot_ucore",
        "retired recovery Guest inventory",
    )
    require_contains(
        user_make,
        "RETIRED_GUEST_SRCS:=$(addprefixuser/$(app_dir)/,"
        "$(addsuffix.c,$(RETIRED_GUEST_APPS)))"
        "SRCS:=$(wildcard$(app_dir)/*.c)"
        "APPS:=$(filter-out$(RETIRED_GUEST_APPS),"
        "$(patsubst$(app_dir)/%.c,%,$(SRCS)))",
        "retired recovery Guest build exclusion",
    )
    require_contains(
        user_make,
        "ifneq($(filter$(CHAPTER),metadata_recoveryobserve_recovery),)"
        "$(errorCHAPTER=$(CHAPTER)isretired;usethelive-queryandworkflow-fence"
        "Agenttests)endif",
        "retired recovery chapter fail-closed gate",
    )
    require_contains(
        user_make,
        "$(filter-out$(STACK_USAGE_SUPPORT_SRCS)$(RETIRED_GUEST_SRCS),"
        "$(addprefixuser/,$(sort$(SRCS))))\\$(COMPAT_BENCH_REPO_SOURCE)"
        "STACK_USAGE_SRCS:="
        "$(STACK_USAGE_FUNCTION_LIBRARY_SRCS)$(STACK_USAGE_SUPPORT_SRCS)"
        "$(STACK_USAGE_APPLICATION_SRCS)",
        "complete stack application inventory",
    )
    for retired_fragment in (
        "METADATA_RECOVERY_TESTS:=",
        "OBSERVE_RECOVERY_TESTS:=",
        "agentobsreboot_ucore.o:STACK_USAGE_PROFILE_CFLAGS:=",
    ):
        if retired_fragment in user_make:
            raise ValueError("retired recovery Guest remains in formal build")
    require_contains(
        user_make,
        "$(foreachsrc,$(STACK_USAGE_SUPPORT_SRCS),--library-unit=$(src))",
        "worker support stack library wiring",
    )
    require_contains(
        user_make,
        "STACK_USAGE_DATA_ONLY_OBJS="
        "$(patsubst%.c,$(STACK_USAGE_DIR)/data-only/%.o,"
        "$(STACK_USAGE_DATA_ONLY_LIBRARY_SRCS))",
        "data-only object inventory",
    )
    require_contains(
        user_make,
        "grep-Eq'[[:space:]]F[[:space:]]'",
        "data-only object function-symbol rejection",
    )
    require_contains(
        user_make,
        "stack-usage-build:$(STACK_USAGE_OBJS)$(STACK_USAGE_DATA_ONLY_OBJS)",
        "data-only object validation wiring",
    )
    uncommented_data = re.sub(
        r"/\*.*?\*/|//[^\r\n]*", "", data_only_source, flags=re.DOTALL
    )
    if FUNCTION_DEFINITION_RE.search(uncommented_data):
        raise ValueError("data-only stack library contains a function definition")
    for symbol in ("rp_state_buf", "rp_host_seed_buf", "rp_host_seed_loaded"):
        if re.search(rf"\b{symbol}\b", uncommented_data) is None:
            raise ValueError(f"data-only stack storage definition missing: {symbol}")
    require_contains(
        user_make,
        "$(STACK_USAGE_DIR)/evaluation_guest/compatbench.o:",
        "shared compatibility benchmark stack build",
    )
    require_contains(
        user_make,
        "-fstack-usage-fcallgraph-info=su-c$(COMPAT_BENCH_REPO_SOURCE)",
        "canonical compatibility benchmark stack compilation",
    )
    require_contains(
        user_make,
        "-I$(abspath$(generated_dir))",
        "absolute generated-header stack include",
    )
    require_contains(
        user_make,
        "CFLAGS:=$(COMMON_CFLAGS)-Iinclude-Ilib-I$(arch_dir)-I$(generated_dir)"
        "CFLAGS+=$(USER_EXTRA_CFLAGS)",
        "canonical application compiler flags",
    )
    require_contains(
        user_make,
        "STACK_USAGE_CFLAGS:=$(COMMON_CFLAGS)\\-Iuser/include-Iuser/lib"
        "-Iuser/$(arch_dir)-I$(abspath$(generated_dir))"
        "STACK_USAGE_CFLAGS+=$(USER_EXTRA_CFLAGS)",
        "stack compiler flag parity",
    )
    require_contains(
        user_make,
        "$(CC_CMD)$(CFLAGS)$(LDFLAGS)$(CRT_OBJ)$(LIB_OBJS)"
        "\\$(COMPAT_BENCH_SOURCE)-o$@",
        "canonical compatibility benchmark link",
    )
    require_contains(
        user_make,
        "--usage-dir\"$$scratch/usage\"--source-dir..",
        "repository-rooted stack source inventory",
    )
    require_contains(
        user_make,
        "$(PYTHON_CMD)$(USER_STACK_CONTRACT_CHECKER)--root..",
        "exec argv contract checker wiring",
    )
    if "--stack-size" in user_make or "--frame-budget" in user_make:
        raise ValueError("user Makefile duplicates stack policy numbers")

    check_layout_model(values, user_source)
    user_compact = compact(user_source)
    marker_at = user_source.find(MARKER)
    live_at = user_source.find('check_live("exec argv layout budget")')
    overflow_at = user_compact.find(
        'exec("usersafety_ucore",exec_layout_overflow_argv)==-1'
    )
    if overflow_at < 0 or live_at < 0 or marker_at < 0 or live_at > marker_at:
        raise ValueError("usersafety lacks reject-and-live argv evidence")
    require_contains(runner, MARKER, "runner exact argv marker")
    require_contains(validator, f'"{MARKER}"', "validator exact argv marker")
    require_contains(
        root_make,
        "scripts/test-check-user-stack-contract.py",
        "stack contract mutation test wiring",
    )

    print(
        "user stack contract: stack=4096 argv=1024 call_path=3072 "
        "boundary=1024 overflow=1040"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    try:
        check(Path(args.root))
    except (OSError, ValueError) as error:
        print(f"user stack contract check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
