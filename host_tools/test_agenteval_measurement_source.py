#!/usr/bin/env python3
"""Mutation tests for real-operation Agent evaluation duration provenance."""
from __future__ import annotations

import json
import importlib.util
import shutil
import tempfile
from pathlib import Path

import functional_acceptance_compile_contract as compile_contract
from agenteval_measurement_source_contract import (
    CONTRACT_VERSION,
    EVALUATION_SUITE_SOURCE_PATH,
    FORMAL_BOOT_COUNT,
    POLICY_INVENTORY_SCHEMA,
    ROOT,
    SOURCE,
    _write_receipt,
    build_measurement_source_receipt,
    validate_measurement_source_receipt_shape,
    validate_source_text,
    measurement_source_policy_inventory,
    verify_measurement_source_receipt,
)


def _function_span(source: str, name: str) -> tuple[int, int]:
    signature = source.index(f"{name}(")
    opening = source.index("{", signature)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return opening + 1, index
    raise AssertionError(f"unterminated fixture function: {name}")


def _mutate(source: str, name: str, old: str, new: str) -> str:
    start, end = _function_span(source, name)
    body = source[start:end]
    if body.count(old) != 1:
        raise AssertionError(f"mutation anchor differs in {name}: {old}")
    return source[:start] + body.replace(old, new, 1) + source[end:]


def _reject(source: str) -> None:
    try:
        validate_source_text(source)
    except ValueError:
        return
    raise AssertionError("accepted formulaic, constant, or unmeasured headline duration")


def _reject_compile(
    texts: dict[str, str], *, refresh_fingerprint: bool = False
) -> None:
    saved = compile_contract.COMPILE_CLOSURE_FINGERPRINT
    if refresh_fingerprint:
        compile_contract.COMPILE_CLOSURE_FINGERPRINT = (
            compile_contract.compile_closure_fingerprint(texts)
        )
    try:
        compile_contract.validate_functional_compile_source_texts(texts)
    except ValueError:
        return
    finally:
        compile_contract.COMPILE_CLOSURE_FINGERPRINT = saved
    raise AssertionError("accepted a forged functional compile dependency")


def _replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise AssertionError(f"compile mutation anchor differs: {old!r}")
    return text.replace(old, new, 1)


def _copy_compile_fixture(destination: Path) -> None:
    paths = set(compile_contract.COMPILE_DEPENDENCY_PATHS)
    paths.add("user/src/agenteval_ucore.c")
    for relative in sorted(paths):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)


def _reject_compile_repo(repo: Path) -> None:
    try:
        compile_contract.validate_functional_compile_sources(repo)
    except ValueError:
        return
    raise AssertionError("accepted a forged repository compile closure")


def _inject_directive(source: str, directive: str) -> str:
    anchor = "#define FNV_PRIME 1099511628211ULL\n"
    if source.count(anchor) != 1:
        raise AssertionError("preprocessor mutation anchor differs")
    return source.replace(anchor, f"{anchor}{directive}\n", 1)


def _mutate_nth(
    source: str, name: str, old: str, new: str, occurrence: int
) -> str:
    start, end = _function_span(source, name)
    body = source[start:end]
    positions = []
    cursor = 0
    while (position := body.find(old, cursor)) >= 0:
        positions.append(position)
        cursor = position + len(old)
    if occurrence < 0 or occurrence >= len(positions):
        raise AssertionError(f"mutation occurrence differs in {name}: {old}")
    position = positions[occurrence]
    changed = body[:position] + new + body[position + len(old):]
    return source[:start] + changed + source[end:]


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    validate_source_text(source)
    dependency_texts = compile_contract.load_compile_dependency_texts(ROOT)
    validator_path = ROOT / "scripts" / "validate-functional-review-flags.py"
    validator_spec = importlib.util.spec_from_file_location(
        "functional_review_flags", validator_path
    )
    if validator_spec is None or validator_spec.loader is None:
        raise AssertionError("cannot load functional review flag validator")
    validator = importlib.util.module_from_spec(validator_spec)
    validator_spec.loader.exec_module(validator)
    showcase_context = (
        "agent", "labdemo_ucore labdemo_execprobe_ucore", "labdemo_ucore"
    )
    good_showcase_flags = (
        "-Werror "
        "-DLABDEMO_RUN_NONCE=0x0123456789abcdefULL "
        "-DLABDEMO_SAMPLE_ID=8 -DLABDEMO_NATIVE_FIRST=0"
    )
    assert validator.validate(good_showcase_flags, *showcase_context)
    assert validator.validate("", "", "", "")
    assert validator.validate(validator.LEGACY_PROFILE, "", "", "")
    for bad_flags in (
        good_showcase_flags.replace("0123456789abcdef", "1234"),
        good_showcase_flags.replace("abcdef", "abcdeF"),
        good_showcase_flags.replace("SAMPLE_ID=8", "SAMPLE_ID=0"),
        good_showcase_flags.replace("SAMPLE_ID=8", "SAMPLE_ID=65"),
        good_showcase_flags.replace("-Werror ", "-Werror  "),
        good_showcase_flags.replace(
            "-Werror -DLABDEMO_RUN_NONCE=0x0123456789abcdefULL",
            "-DLABDEMO_RUN_NONCE=0x0123456789abcdefULL -Werror",
        ),
        f"{good_showcase_flags} -DFORGED=1",
    ):
        assert not validator.validate(bad_flags, *showcase_context)
    for bad_context in (
        ("agent_eval", showcase_context[1], showcase_context[2]),
        (showcase_context[0], "labdemo_ucore", showcase_context[2]),
        (showcase_context[0], showcase_context[1], "usershell"),
    ):
        assert not validator.validate(good_showcase_flags, *bad_context)
    continuation = (
        '"${PYTHON_BIN}" -I -S -B tool.py \\\n'
        '\t--python-bin "${PYTHON_BIN}" --shell-bin "${BASH_BIN}"'
    )
    assert len(compile_contract._isolated_python_invocation_lines(continuation)) == 1
    try:
        compile_contract._isolated_python_invocation_lines('"${PYTHON_BIN}" tool.py')
    except ValueError:
        pass
    else:
        raise AssertionError("accepted an unisolated Python command")
    compile_contract.validate_functional_compile_source_texts(dependency_texts)

    header_redirect = dict(dependency_texts)
    header_redirect["user/include/agent.h"] += "\n#define agent_info(info) 0\n"
    _reject_compile(header_redirect)
    _reject_compile(header_redirect, refresh_fingerprint=True)

    wrapper_forge = dict(dependency_texts)
    wrapper_forge["user/lib/syscall.c"] = wrapper_forge[
        "user/lib/syscall.c"
    ].replace(
        "return syscall(SYS_agent_info, info);",
        "return 0;",
        1,
    )
    _reject_compile(wrapper_forge)
    _reject_compile(wrapper_forge, refresh_fingerprint=True)

    arch_forge = dict(dependency_texts)
    arch_forge["user/lib/arch/riscv/syscall_arch.h"] = arch_forge[
        "user/lib/arch/riscv/syscall_arch.h"
    ].replace('"ecall\\n\\t"', '"nop\\n\\t"', 1)
    _reject_compile(arch_forge)
    _reject_compile(arch_forge, refresh_fingerprint=True)

    digraph_redirect = dict(dependency_texts)
    digraph_redirect["user/include/agent.h"] += (
        "\n%:define agent_info(info) 0\n"
    )
    _reject_compile(digraph_redirect)
    _reject_compile(digraph_redirect, refresh_fingerprint=True)

    dispatcher_redefinition = dict(dependency_texts)
    dispatcher_redefinition["user/lib/syscall.h"] += (
        "\n#undef __syscall\n#define __syscall(...) 0\n"
    )
    _reject_compile(dispatcher_redefinition)
    _reject_compile(dispatcher_redefinition, refresh_fingerprint=True)

    context_mirror_forge = dict(dependency_texts)
    context_mirror_forge["user/lib/syscall.c"] = _replace_once(
        context_mirror_forge["user/lib/syscall.c"],
        "\treturn copied;\n}\n\nint context_detail",
        "\treturn 0;\n}\n\nint context_detail",
    )
    _reject_compile(context_mirror_forge)
    _reject_compile(context_mirror_forge, refresh_fingerprint=True)

    for label, old in (
        ("getppid", "return syscall(SYS_getppid);"),
        ("pipe", "return syscall(SYS_pipe2, p);"),
        (
            "agent_unwatch",
            "return syscall(SYS_agent_unwatch, event_type, filter);",
        ),
    ):
        wrapper = dict(dependency_texts)
        wrapper["user/lib/syscall.c"] = _replace_once(
            wrapper["user/lib/syscall.c"], old, "return 0;"
        )
        _reject_compile(wrapper)
        _reject_compile(wrapper, refresh_fingerprint=True)

    build_redirect = dict(dependency_texts)
    build_redirect["Makefile"] = _replace_once(
        build_redirect["Makefile"],
        "$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -rR -C user -f Makefile "
        "CHAPTER=$(CHAPTER) BASE=$(BASE)",
        "$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -rR -C forged_user -f Makefile "
        "CHAPTER=$(CHAPTER) BASE=$(BASE)",
    )
    _reject_compile(build_redirect)
    _reject_compile(build_redirect, refresh_fingerprint=True)

    dependency_include = dict(dependency_texts)
    dependency_include["Makefile"] = _replace_once(
        dependency_include["Makefile"],
        "ifneq ($(FUNCTIONAL_REVIEW_BUILD),1)\n"
        "-include $(HEADER_DEP)\n"
        "endif",
        "-include $(HEADER_DEP)",
    )
    _reject_compile(dependency_include)
    _reject_compile(dependency_include, refresh_fingerprint=True)

    for make_path, injection in (
        ("Makefile", "CFLAGS += -include forged.h"),
        ("Makefile", "CFLAGS += -Dprintf=forged_printf"),
        ("Makefile", "OBJS += forged.o"),
        ("user/Makefile", "LDFLAGS += forged.o"),
    ):
        compile_escape = dict(dependency_texts)
        compile_escape[make_path] += f"\n{injection}\n"
        _reject_compile(compile_escape)
        _reject_compile(compile_escape, refresh_fingerprint=True)

    for old, new in (
        ("([0-9a-f]{16})", "([0-9a-f]{4,16})"),
        ("([1-9]|[1-5][0-9]|6[0-4])", "([0-9]|[1-6][0-9])"),
        ('tests == SHOWCASE_TESTS', 'tests != ""'),
    ):
        flag_escape = dict(dependency_texts)
        flag_escape["scripts/validate-functional-review-flags.py"] = _replace_once(
            flag_escape["scripts/validate-functional-review-flags.py"], old, new
        )
        _reject_compile(flag_escape)
        _reject_compile(flag_escape, refresh_fingerprint=True)

    make_flag_escape = dict(dependency_texts)
    make_flag_escape["nfs/Makefile"] = _replace_once(
        make_flag_escape["nfs/Makefile"],
        "python3 -I -S -B ../scripts/validate-functional-review-flags.py",
        "printf ok",
    )
    _reject_compile(make_flag_escape)
    _reject_compile(make_flag_escape, refresh_fingerprint=True)

    runner_env_escape = dict(dependency_texts)
    runner_env_escape["scripts/run-agent-tests.sh"] = _replace_once(
        runner_env_escape["scripts/run-agent-tests.sh"],
        "\tHOSTCC CC AS LD OBJCOPY OBJDUMP NM SIZE CFLAGS CPPFLAGS LDFLAGS ASFLAGS",
        "\tCC AS LD OBJCOPY OBJDUMP NM SIZE CFLAGS CPPFLAGS LDFLAGS ASFLAGS",
    )
    _reject_compile(runner_env_escape)
    _reject_compile(runner_env_escape, refresh_fingerprint=True)

    python_shadow = dict(dependency_texts)
    python_shadow["scripts/run-agent-tests.sh"] = _replace_once(
        python_shadow["scripts/run-agent-tests.sh"],
        '"${PYTHON_BIN}" -I -S -B scripts/agent_test_runner.py',
        '"${PYTHON_BIN}" scripts/agent_test_runner.py',
    )
    _reject_compile(python_shadow)
    _reject_compile(python_shadow, refresh_fingerprint=True)

    duration_profile_shadow = dict(dependency_texts)
    duration_profile_shadow["scripts/run-agent-tests.sh"] = _replace_once(
        duration_profile_shadow["scripts/run-agent-tests.sh"],
        '"${HOST_CC}" --duration-profile local-e3 >/dev/null',
        '"${HOST_CC}" --duration-profile none >/dev/null',
    )
    _reject_compile(duration_profile_shadow)
    _reject_compile(duration_profile_shadow, refresh_fingerprint=True)

    usershell_prefix = dict(dependency_texts)
    usershell_prefix["user/Makefile"] = _replace_once(
        usershell_prefix["user/Makefile"],
        "CH_TESTS := $(EVALUATION_TESTS)",
        "CH_TESTS := usershell $(EVALUATION_TESTS)",
    )
    _reject_compile(usershell_prefix)
    _reject_compile(usershell_prefix, refresh_fingerprint=True)

    app_directory_override = dict(dependency_texts)
    app_directory_override["user/Makefile"] += "\noverride app_dir := forged_src\n"
    _reject_compile(app_directory_override)
    _reject_compile(app_directory_override, refresh_fingerprint=True)

    kernel_console_forge = dict(dependency_texts)
    kernel_console_forge["os/console.c"] += (
        '\nstatic const char *forged = "agenteval_ucore: worker passed\\n";\n'
    )
    # Kernel output and syscall result producers are part of the byte-pinned
    # review closure; they are not treated as an independent Guest oracle.
    _reject_compile(kernel_console_forge)

    actual_include_closure = (
        compile_contract.resolve_functional_include_closure(ROOT)
    )
    assert actual_include_closure == compile_contract.EXPECTED_INCLUDE_CLOSURE
    kernel_include_closure = compile_contract.resolve_kernel_include_closure(ROOT)
    assert compile_contract.REQUIRED_KERNEL_RESULT_PATHS <= set(
        kernel_include_closure
    )
    assert not {
        path for path in kernel_include_closure
        if path not in compile_contract.COMPILE_DEPENDENCY_PATHS
        and not path.startswith(compile_contract.TOOLCHAIN_INCLUDE_PREFIX)
    }
    with tempfile.TemporaryDirectory() as temporary:
        fixture = Path(temporary)
        _copy_compile_fixture(fixture)
        compile_contract.validate_functional_compile_sources(fixture)

        functional_source = fixture / "user" / "src" / "agenteval_ucore.c"
        original_functional_source = functional_source.read_text(encoding="utf-8")
        functional_source.write_text(
            _replace_once(
                original_functional_source,
                "#include <stdio.h>",
                "#include/**/<stdio .h>",
            ),
            encoding="utf-8",
        )
        obscured_header = fixture / "user" / "include" / "stdio .h"
        obscured_header.write_text(
            '#include <stdio.h>\n#define printf(...) exit(0)\n',
            encoding="ascii",
        )
        _reject_compile_repo(fixture)
        obscured_header.unlink()
        functional_source.write_text(original_functional_source, encoding="utf-8")

        shadow_seed = fixture / "user" / "include" / "agenteval_seed.h"
        shadow_seed.write_text("#define AGENTEVAL_CHALLENGE 7\n", encoding="ascii")
        _reject_compile_repo(fixture)
        shadow_seed.unlink()

        shadow_arch = fixture / "user" / "lib" / "syscall_arch.h"
        shadow_arch.write_text("static long __syscall0(long n) { return 0; }\n",
                               encoding="ascii")
        _reject_compile_repo(fixture)
        shadow_arch.unlink()

        forged_kernel = fixture / "os" / "forged_console.c"
        forged_kernel.write_text(
            'void forged(void) { /* console receipt forgery */ }\n',
            encoding="ascii",
        )
        _reject_compile_repo(fixture)
        forged_kernel.unlink()

        kernel_pch = fixture / "os" / "defs.h.gch"
        kernel_pch.write_bytes(b"forged precompiled header")
        _reject_compile_repo(fixture)
        kernel_pch.unlink()

        root_pch = fixture / "agent_lifecycle_abi.h.gch"
        root_pch.mkdir()
        (root_pch / "forged.gch").write_bytes(b"forged precompiled header")
        _reject_compile_repo(fixture)
        shutil.rmtree(root_pch)

        for directory in (fixture, fixture / "user", fixture / "nfs"):
            for name in ("gnumakefile", "mAkEfIlE.sh"):
                alternate_make = directory / name
                alternate_make.write_text("all:\n\t@true\n", encoding="ascii")
                _reject_compile_repo(fixture)
                alternate_make.unlink()
    for binding in (
        "now_us", "elapsed_us", "sys_get_time", "agent_file_query",
        "agent_run", "context_query", "copy_context_volatile", "printf",
        "pair_runs_ab", "open", "read", "fstat", "close",
    ):
        _reject(_inject_directive(source, f"#define {binding} forged_{binding}"))
    _reject(_inject_directive(source, "#undef now_us"))
    _reject(_inject_directive(source, '#include "forged_clock.h"'))
    _reject(source.replace(
        "#define EVAL_FILE_QUERIES 16",
        "#define EVAL_FILE_QUERIES 0",
        1,
    ))
    for directive, replacement in (
        ("#define EVAL_PATH_LOADS 4", "#define EVAL_PATH_LOADS 2"),
        ("#define EVAL_PATH_MAX_QUERIES 8", "#define EVAL_PATH_MAX_QUERIES 7"),
        ("#define EVAL_UNION_LOADS 5", "#define EVAL_UNION_LOADS 4"),
        (
            "#define EVAL_FILE_RECORD_SCHEMA 1",
            "#define EVAL_FILE_RECORD_SCHEMA 2",
        ),
        ("#define TASK5_DELAY_TICKS 8", "#define TASK5_DELAY_TICKS 1"),
        ("#define TASK5_TICK_MSEC 10", "#define TASK5_TICK_MSEC 1"),
        ("#define TASK5_MAX_WAIT_LOOPS 3", "#define TASK5_MAX_WAIT_LOOPS 30"),
        ("#define TASK5_RECEIPT_VALUES 28", "#define TASK5_RECEIPT_VALUES 19"),
    ):
        _reject(source.replace(directive, replacement, 1))
    _reject(_inject_directive(source, "%:define now_us forged_now_us"))
    _reject(_inject_directive(source, "??=define now_us forged_now_us"))
    _reject(_inject_directive(
        source, "#define no\\\nw_us forged_now_us"
    ))
    _reject(_mutate(
        source,
        "run_evaluation",
        "run_functional_task1();",
        "/*\n*\\\x00\n/\nexit(0);\n/*\n*/\n\t"
        "run_functional_task1();",
    ))
    functions = (
        "time_file_contest_variant",
        "time_file_table_variant",
        "time_tool_variant",
        "time_context_variant",
    )
    measured = "measurement->duration_us = elapsed_us(start, now_us());"
    for name in functions:
        _reject(_mutate(source, name, measured, "measurement->duration_us = 7;"))
        _reject(
            _mutate(
                source, name, measured,
                "measurement->duration_us = (uint64)(load * 100 + pair);",
            )
        )
        _reject(
            _mutate(
                source, name, measured,
                measured + "\n\tmeasurement->duration_us += 1;",
            )
        )
        _reject(
            _mutate(
                source, name, "start = now_us();",
                "start = now_us();\n\tstart = 0;",
            )
        )

    _reject(_mutate(
        source, "now_us",
        "return now.sec * 1000000ULL + now.usec;",
        "now.sec = 7;\n\treturn now.sec * 1000000ULL + now.usec;",
    ))
    _reject(_mutate(
        source, "elapsed_us", "return end - start;",
        "start = 0;\n\treturn end - start;",
    ))

    for old, new in (
        ("open(name, O_RDONLY)", "forged_open(name, O_RDONLY)"),
        ("read(fd, &record, sizeof(record))",
         "forged_read(fd, &record, sizeof(record))"),
        ("fstat(fd, &status)", "forged_fstat(fd, &status)"),
        ("close(fd)", "forged_close(fd)"),
        ("eval_file_record_valid(&record, item)",
         "forged_record_valid(&record, item)"),
        ("eval_file_record_matches_query(",
         "forged_file_record_matches_query("),
    ):
        _reject(_mutate(source, "time_file_contest_variant", old, new))
    _reject(_mutate(
        source, "time_file_contest_variant",
        "for (int item = 0; item < load; item++) {",
        "for (int item = 0; item < load - 1; item++) {",
    ))
    _reject(_mutate(
        source, "time_file_contest_variant",
        "observation->scanned_records++;",
        "observation->scanned_records++;\n\t\t\t\tbreak;",
    ))
    _reject(_mutate(
        source, "time_file_contest_variant",
        "path_walk ? 0 : AGENT_FILE_QUERY_USE_INDEX",
        "0 ? 0 : AGENT_FILE_QUERY_USE_INDEX",
    ))
    _reject(_mutate(
        source, "time_file_contest_variant",
        "int result = agent_file_query(",
        "int result = forged_file_query(",
    ))
    _reject(_mutate(
        source, "time_file_table_variant",
        "agent_file_query(&prepared_file_queries[operation]",
        "forged_file_query(&prepared_file_queries[operation]",
    ))
    _reject(_mutate(
        source, "time_file_table_variant",
        "use_index ?\n\t\tAGENT_FILE_QUERY_USE_INDEX : AGENT_FILE_QUERY_SCAN",
        "0 ?\n\t\tAGENT_FILE_QUERY_USE_INDEX : AGENT_FILE_QUERY_SCAN",
    ))
    _reject(
        _mutate(
            source,
            "time_tool_variant",
            "agent_run(&tool_ops[completed]",
            "forged_agent_run(&tool_ops[completed]",
        )
    )
    _reject(
        _mutate(
            source,
            "time_tool_variant",
            "batch ? load - completed : 1",
            "0 ? load - completed : 1",
        )
    )
    for old, new in (
        ("copy_context_volatile(&results[i]", "forged_context_read(&results[i]"),
        ("context_query(target_sequence", "forged_context_query(target_sequence"),
    ):
        _reject(_mutate(source, "time_context_variant", old, new))
    _reject(
        _mutate(
            source, "time_context_variant", "if (direct) {", "if (0) {"
        )
    )

    _reject(_mutate_nth(
        source, "run_file_query_path_index",
        "time_file_contest_variant(load, operations, 0,",
        'check(1, "forged inter-window validation");\n\t'
        "time_file_contest_variant(load, operations, 0,",
        0,
    ))

    direct_duration = "(unsigned long long)measurement->duration_us"
    _reject(_mutate(
        source, "print_sample", direct_duration, "7ULL"
    ))
    _reject(_mutate(
        source, "print_sample", direct_duration,
        "(unsigned long long)(measurement->duration_us + 1)",
    ))
    _reject(_mutate(
        source, "print_sample", "sample schema=2", "sample schema=1"
    ))
    _reject(_mutate(
        source, "finalize_agent_file_variant",
        "measurement->work_units = 0;",
        "measurement->duration_us += 1;\n\tmeasurement->work_units = 0;",
    ))
    for finalize_name in (
        "finalize_agent_file_variant", "finalize_path_file_variant"
    ):
        _reject(_mutate(
            source, finalize_name,
            "measurement->records_examined += observation->candidate_records;",
            "measurement->records_examined += (uint64)(uint)load;",
        ))
    _reject(_mutate_nth(
        source, "run_file_query_path_index", '"path_walk",', '"index",', 0
    ))
    _reject(_mutate_nth(
        source, "run_file_query_path_index",
        "time_file_contest_variant(load, operations, 1,",
        "time_file_contest_variant(load, operations, 0,", 0,
    ))
    _reject(_mutate_nth(
        source, "run_file_query_table_ablation", '"scan",', '"index",', 0
    ))
    _reject(_mutate(
        source, "run_file_query_experiment",
        "seed_file_metadata(seeded, load);",
        "seed_file_metadata(seeded, load - 1);",
    ))
    _reject(_mutate(
        source, "run_file_query_experiment",
        "after_seed = census_visible_file_records();",
        "after_seed = ambient_file_records + load;",
    ))
    _reject(_mutate(
        source, "run_file_query_experiment",
        "expected_visible_file_records = after_seed;",
        "expected_visible_file_records = load;",
    ))
    _reject(_mutate(
        source, "census_visible_file_records",
        "file_result.scanned_records == AGENT_FILE_META_MAX",
        "file_result.scanned_records > 0",
    ))
    _reject(_mutate_nth(
        source, "run_tool_batch_experiment", "if (pair_runs_ab(pair)) {",
        "if (!pair_runs_ab(pair)) {", 1,
    ))
    _reject(_mutate(
        source, "run_context_access_experiment",
        'const char *order = pair_runs_ab(pair) ? "AB" : "BA";',
        'const char *order = pair_runs_ab(pair) ? "AB" : "BA";\n'
        '\t\t\torder = "AB";',
    ))

    commit = "a" * 40
    receipt = build_measurement_source_receipt(ROOT, source_commit=commit)
    assert CONTRACT_VERSION == "agenteval-measurement-source-v11"
    assert FORMAL_BOOT_COUNT == 7
    assert receipt["formal_boot_count"] == FORMAL_BOOT_COUNT
    assert receipt["contract_versions"]["functional"] == (
        "agentos-functional-acceptance-source-v4"
    )
    assert receipt["contract_versions"]["functional_compile"] == (
        "agentos-functional-compile-closure-v3"
    )
    assert receipt["stop_rule"] == "fixed_7_boots_per_source_commit"
    validate_measurement_source_receipt_shape(receipt, expected_commit=commit)
    verify_measurement_source_receipt(receipt, ROOT, expected_commit=commit)
    assert receipt["policy_inventory"] == measurement_source_policy_inventory()
    assert receipt["policy_inventory"]["schema"] == POLICY_INVENTORY_SCHEMA
    policy_entries = {
        (entry["role"], entry["path"])
        for entry in receipt["policy_inventory"]["entries"]
    }
    policy_paths = {path for _role, path in policy_entries}
    assert EVALUATION_SUITE_SOURCE_PATH in policy_paths
    assert "host_tools/evaluation_bundle.py" in policy_paths
    assert "evaluation_guest/compatbench.c" in policy_paths
    assert "host_tools/contest_demo.py" in policy_paths
    assert "host_tools/committed_source_identity.py" in policy_paths
    assert "host_tools/full_verification_payload.py" in policy_paths
    assert "host_tools/full_verification_metrics.py" in policy_paths
    assert "host_tools/agenteval_measurement_source_policy.py" in policy_paths
    assert "host_tools/agenteval_measurement_source_receipt.py" in policy_paths
    assert "host_tools/agenteval_measurement_source_validator.py" in policy_paths
    assert "host_tools/functional_acceptance_source_contract.py" in policy_paths
    assert "scripts/capture-final-evidence.py" in policy_paths
    assert "scripts/run-full-verification.sh" in policy_paths
    assert "host_tools/evidence_toolchain_attestation.py" in policy_paths
    assert "host_tools/formal_temp_binding.py" in policy_paths
    semantic_replay_dependencies = {
        "host_tools/evidence_semantic_registry.py",
        "host_tools/evidence_semantic_profiles.py",
        "host_tools/measured_experiments.py",
        "host_tools/check_host_platform_alignment.py",
        "host_tools/research_state_manifest.py",
        "scripts/fs-allocator-evidence.py",
        "scripts/fs-allocator-image.py",
        "ci/agent-metadata-disk-format.json",
        "ci/agent-observe-disk-format.json",
        "user/src/agentbench_ucore.c",
    }
    assert semantic_replay_dependencies <= policy_paths
    for prefix in ("baseline_ucore/user/src", "user/src"):
        expected = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / prefix).glob("rp_*.c")
        }
        assert expected <= policy_paths
    assert "host_tools/git_history_contract.py" in policy_paths
    assert "host_tools/render_evaluation_dashboard.py" in policy_paths
    assert "host_tools/assets/evaluation-dashboard.css" in policy_paths
    assert "host_tools/assets/evaluation-dashboard.js" in policy_paths
    compatibility_execution_policy = {
        ("compatibility-producer", "host_tools/compatibility_overhead.py"),
        (
            "compatibility-contract",
            "host_tools/compatibility_overhead_contract.py",
        ),
    }
    assert compatibility_execution_policy <= policy_entries
    micro_execution_policy = {
        ("micro-runner", "scripts/run-agent-tests.sh"),
        ("micro-evidence-wiring", "scripts/evidence-wiring.sh"),
        ("micro-qemu-runner", "scripts/agent_test_runner.py"),
        ("micro-guest-failure-classifier", "scripts/guest_failure_classifier.py"),
        ("micro-preflight", "scripts/test-sync-owner-wiring.py"),
        ("micro-preflight", "scripts/test-wait-atomic-wiring.py"),
        ("micro-preflight", "scripts/check-wait-queue-contract.py"),
    }
    assert micro_execution_policy <= policy_entries
    split_source_policy = {
        ("source-contract", "host_tools/agenteval_measurement_source_policy.py"),
        ("source-contract", "host_tools/agenteval_measurement_source_receipt.py"),
        ("source-contract", "host_tools/agenteval_measurement_source_validator.py"),
        ("source-contract", "host_tools/functional_acceptance_compile_contract.py"),
        ("source-contract", "host_tools/functional_acceptance_source_contract.py"),
    }
    assert split_source_policy <= policy_entries
    assert set(compile_contract.COMPILE_DEPENDENCY_PATHS) <= policy_paths
    assert "scripts/run-evaluation-suite.sh" in policy_paths
    assert "scripts/package-evaluation-evidence.sh" in policy_paths
    assert "host_tools/plain_ucore_fs_extract.py" in policy_paths
    assert "host_tools/research_state_manifest.py" in policy_paths
    assert "ci/research-state-manifest.json" in policy_paths
    assert "evaluation_guest/fixtures/task6-count-corpus.csv" in policy_paths
    assert "user/Makefile" in policy_paths
    assert "user/include/rp_program_manifest.h" in policy_paths
    assert "baseline_ucore/user/include/rp_program_manifest.h" in policy_paths
    assert "baseline_ucore/user/Makefile" in policy_paths
    assert "scripts/initproc.py" in policy_paths
    assert "baseline_ucore/scripts/initproc.py" in policy_paths
    assert "nfs/Makefile" in policy_paths
    assert "baseline_ucore/nfs/Makefile" in policy_paths
    assert "host_tools/agent_metadata_journal.py" in policy_paths
    assert "user/include/research_platform_state.h" in policy_paths
    assert "baseline_ucore/user/include/research_platform_state.h" in policy_paths
    forged = json.loads(json.dumps(receipt))
    forged["sources"][0]["sha256"] = "0" * 64
    try:
        verify_measurement_source_receipt(forged, ROOT, expected_commit=commit)
    except ValueError:
        pass
    else:
        raise AssertionError("accepted forged measurement source receipt")
    for _role, path in (
        micro_execution_policy | compatibility_execution_policy
        | split_source_policy
    ):
        forged_source = json.loads(json.dumps(receipt))
        matches = [
            record for record in forged_source["sources"]
            if record["path"] == path
        ]
        if len(matches) != 1:
            raise AssertionError(f"policy source is not uniquely receipted: {path}")
        matches[0]["sha256"] = "0" * 64
        try:
            verify_measurement_source_receipt(
                forged_source, ROOT, expected_commit=commit
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted forged policy source receipt: {path}")
    forged_policy = json.loads(json.dumps(receipt))
    forged_policy["policy_inventory"]["entries"][0]["path"] = "ci/other-suite.json"
    try:
        validate_measurement_source_receipt_shape(
            forged_policy, expected_commit=commit
        )
    except ValueError:
        pass
    else:
        raise AssertionError("accepted a rewritten evaluation policy inventory")
    forged_functional_version = json.loads(json.dumps(receipt))
    forged_functional_version["contract_versions"].pop("functional")
    try:
        validate_measurement_source_receipt_shape(
            forged_functional_version, expected_commit=commit
        )
    except ValueError:
        pass
    else:
        raise AssertionError("accepted a missing functional source-contract version")
    forged_compile_version = json.loads(json.dumps(receipt))
    forged_compile_version["contract_versions"].pop("functional_compile")
    try:
        validate_measurement_source_receipt_shape(
            forged_compile_version, expected_commit=commit
        )
    except ValueError:
        pass
    else:
        raise AssertionError("accepted a missing functional compile-contract version")
    forged_stop_rule = json.loads(json.dumps(receipt))
    forged_stop_rule["stop_rule"] = "minimum_7_boots_per_source_commit"
    try:
        validate_measurement_source_receipt_shape(
            forged_stop_rule, expected_commit=commit
        )
    except ValueError:
        pass
    else:
        raise AssertionError("accepted an open-ended formal stopping rule")
    for forged_count in (8, True):
        forged_boot_count = json.loads(json.dumps(receipt))
        forged_boot_count["formal_boot_count"] = forged_count
        try:
            validate_measurement_source_receipt_shape(
                forged_boot_count, expected_commit=commit
            )
        except ValueError:
            pass
        else:
            raise AssertionError("accepted a rewritten formal boot count")
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "measurement-source-receipt.json"
        _write_receipt(path, receipt)
        original = path.read_bytes()
        try:
            _write_receipt(path, forged)
        except ValueError:
            pass
        else:
            raise AssertionError("overwrote an existing measurement source receipt")
        if path.read_bytes() != original:
            raise AssertionError("changed an existing measurement source receipt")

    print("test_agenteval_measurement_source: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
