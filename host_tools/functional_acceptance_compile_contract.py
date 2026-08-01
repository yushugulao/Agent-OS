#!/usr/bin/env python3
"""Trusted compile closure for the Task 1-5 Guest evidence producer."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

if __package__:
    from .benchmark_source_contract import _function_tokens, _lex
else:
    from benchmark_source_contract import _function_tokens, _lex


CONTRACT_VERSION = "agentos-functional-compile-closure-v1"

# These are the repository inputs that can redirect a reviewed call, fabricate
# a result before it reaches the Guest, replace the output sink, or bypass main.
# The generated challenge header is covered by the exact generator in
# user/Makefile and by the Host challenge check in every raw log.
USER_ARTIFACT_DEPENDENCY_PATHS = (
    "Makefile",
    "agent_lifecycle_abi.h",
    "agent_metadata_test_abi.h",
    "agent_observe_abi.h",
    "agent_resource_abi.h",
    "agent_tool_abi.h",
    "io_policy.h",
    "kernel_work_abi.h",
    "nfs/Makefile",
    "nfs/elf_compat.h",
    "nfs/fs.c",
    "nfs/fs.h",
    "nfs/host_image_snapshot.c",
    "nfs/host_image_snapshot.h",
    "nfs/host_windows_compat.h",
    "nfs/types.h",
    "scripts/agent_test_runner.py",
    "scripts/initproc.py",
    "scripts/run-agent-tests.sh",
    "scripts/trusted-python-entry.py",
    "agent_metadata_disk_abi.h",
    "exec_image_policy.h",
    "fs_storage_policy.h",
    "user/Makefile",
    "user/include/agent.h",
    "user/include/agent_observe_test_phase_abi.h",
    "user/include/agent_metadata_test_abi.h",
    "user/include/exec_policy_manifest.h",
    "user/include/fcntl.h",
    "user/include/fs_allocator_test_abi.h",
    "user/include/io_policy.h",
    "user/include/kernel_work_abi.h",
    "user/include/physical_page_test_abi.h",
    "user/include/research_platform_state.h",
    "user/include/rp_evidence.h",
    "user/include/rp_launch_attestation.h",
    "user/include/rp_program_manifest.h",
    "user/include/rp_resource_stability.h",
    "user/include/stddef.h",
    "user/include/stdio.h",
    "user/include/stdlib.h",
    "user/include/string.h",
    "user/include/unistd.h",
    "user/include/user_stack_policy.h",
    "user/include/virtio_test_abi.h",
    "user/include/wait_atomic_test_abi.h",
    "user/lib/arch/riscv/crt.S",
    "user/lib/arch/riscv/syscall_arch.h",
    "user/lib/arch/riscv/syscall_ids.h.in",
    "user/lib/arch/riscv/user.ld",
    "user/lib/main.c",
    "user/lib/stdio.c",
    "user/lib/stdlib.c",
    "user/lib/string.c",
    "user/lib/syscall.c",
    "user/lib/syscall.h",
    "user/lib/syscall_ids.h",
    "wait_atomic_test_abi.h",
)

# The Guest is not an independent oracle for kernel behavior: any compiled
# kernel object can forge syscall results or write a valid-looking receipt to
# the console.  Pin the complete wildcard-selected kernel source/header/linker
# closure, not only the Agent syscall implementations.  initproc.S is omitted
# because Makefile regenerates it unconditionally from the pinned generator.
KERNEL_RUNTIME_DEPENDENCY_PATHS = (
    "agent_observe_test_phase_abi.h",
    "file_resource_policy.h",
    "fs_allocator_test_abi.h",
    "physical_page_policy.h",
    "physical_page_test_abi.h",
    "scripts/check-kernel-stack-usage.py",
    "thread_resource_policy.h",
    "user_stack_policy.h",
    "virtio_test_abi.h",
    "os/agent_background.c",
    "os/agent_context.c",
    "os/agent_context.h",
    "os/agent_context_path.c",
    "os/agent_context_path.h",
    "os/agent_core.c",
    "os/agent_durable_section.c",
    "os/agent_durable_section.h",
    "os/agent_file_name_policy.h",
    "os/agent_file_state.c",
    "os/agent_file_state_internal.h",
    "os/agent_identity.c",
    "os/agent_identity_lease.c",
    "os/agent_identity_lease.h",
    "os/agent_internal.h",
    "os/agent_ipc.c",
    "os/agent_lifecycle.c",
    "os/agent_lifecycle.h",
    "os/agent_metadata.c",
    "os/agent_metadata_actions.c",
    "os/agent_metadata_actions.h",
    "os/agent_metadata_catalog.c",
    "os/agent_metadata_catalog.h",
    "os/agent_metadata_directory.c",
    "os/agent_metadata_directory.h",
    "os/agent_metadata_disk.h",
    "os/agent_metadata_internal.h",
    "os/agent_metadata_objects.c",
    "os/agent_metadata_prefetch.c",
    "os/agent_metadata_prefetch.h",
    "os/agent_metadata_probe.c",
    "os/agent_metadata_probe.h",
    "os/agent_metadata_query.c",
    "os/agent_metadata_query.h",
    "os/agent_metadata_recovery.c",
    "os/agent_metadata_recovery.h",
    "os/agent_metadata_recovery_test.c",
    "os/agent_metadata_recovery_test.h",
    "os/agent_metadata_scan.c",
    "os/agent_metadata_scan.h",
    "os/agent_metadata_store.c",
    "os/agent_metadata_store_format.c",
    "os/agent_metadata_store_format.h",
    "os/agent_metadata_store_io.c",
    "os/agent_metadata_store_io.h",
    "os/agent_metadata_test.c",
    "os/agent_observe.c",
    "os/agent_observe_audit_query.c",
    "os/agent_observe_capacity.c",
    "os/agent_observe_capacity.h",
    "os/agent_observe_internal.h",
    "os/agent_observe_ledger.c",
    "os/agent_observe_persist_context.h",
    "os/agent_observe_recovery.c",
    "os/agent_observe_recovery.h",
    "os/agent_observe_recovery_store.h",
    "os/agent_observe_store.c",
    "os/agent_observe_store.h",
    "os/agent_observe_test.c",
    "os/agent_observe_test.h",
    "os/agent_observe_timeline.c",
    "os/agent_resource.c",
    "os/agent_tool_protocol.c",
    "os/agent_tool_protocol.h",
    "os/agent.c",
    "os/agent.h",
    "os/bio.c",
    "os/bio.h",
    "os/console.c",
    "os/console.h",
    "os/const.h",
    "os/defs.h",
    "os/entry.S",
    "os/exec_policy.c",
    "os/exec_policy.h",
    "os/fcntl.h",
    "os/file.c",
    "os/file.h",
    "os/fs.c",
    "os/fs.h",
    "os/fs_allocator_test.c",
    "os/fs_allocator_test.h",
    "os/kalloc.c",
    "os/kalloc.h",
    "os/kernel.ld",
    "os/kernel_work.c",
    "os/kernel_work.h",
    "os/kernelvec.S",
    "os/loader.c",
    "os/loader.h",
    "os/log.h",
    "os/main.c",
    "os/metadata_crash_test.h",
    "os/physical_page_test.c",
    "os/physical_page_test.h",
    "os/pipe.c",
    "os/plic.c",
    "os/plic.h",
    "os/printf.c",
    "os/printf.h",
    "os/proc.c",
    "os/proc.h",
    "os/queue.c",
    "os/queue.h",
    "os/resource_controller.c",
    "os/resource_controller.h",
    "os/riscv.h",
    "os/sbi.c",
    "os/sbi.h",
    "os/string.c",
    "os/string.h",
    "os/switch.S",
    "os/sync.c",
    "os/sync.h",
    "os/syscall.c",
    "os/syscall.h",
    "os/syscall_ids.h",
    "os/timer.c",
    "os/timer.h",
    "os/trampoline.S",
    "os/trap.c",
    "os/trap.h",
    "os/types.h",
    "os/user_stack_layout.h",
    "os/vfs_security.c",
    "os/vfs_security.h",
    "os/virtio.h",
    "os/virtio_disk.c",
    "os/vm.c",
    "os/vm.h",
    "os/wait.c",
    "os/wait.h",
    "os/wait_atomic_test.c",
    "os/wait_atomic_test.h",
    "os/workflow_lifecycle.c",
    "os/workflow_lifecycle.h",
)

COMPILE_DEPENDENCY_PATHS = (
    USER_ARTIFACT_DEPENDENCY_PATHS + KERNEL_RUNTIME_DEPENDENCY_PATHS
)

# Byte-exact Merkle root of the complete reviewed closure above.  Keeping every
# translation phase input exact prevents line splices, directives, assembly,
# or linker syntax from disappearing during normalization.
COMPILE_CLOSURE_FINGERPRINT = (
    "667aef55182c14e5394b0e00a3c974e33221dd337879fe7a980078e68fdc5228"
)

USER_TRANSLATION_UNITS = (
    "user/src/agenteval_ucore.c",
    "user/lib/main.c",
    "user/lib/stdio.c",
    "user/lib/stdlib.c",
    "user/lib/string.c",
    "user/lib/syscall.c",
    "user/lib/arch/riscv/crt.S",
)
USER_INCLUDE_SEARCH_PATHS = (
    "user/include", "user/lib", "user/lib/arch/riscv",
)
GENERATED_CHALLENGE_HEADER = "@generated/agenteval_seed.h"
TOOLCHAIN_PROVIDED_HEADERS = frozenset({"stdarg.h"})
TOOLCHAIN_INCLUDE_PREFIX = "@attested-toolchain/"
EXPECTED_INCLUDE_CLOSURE = (
    "@generated/agenteval_seed.h",
    "agent_lifecycle_abi.h",
    "agent_metadata_test_abi.h",
    "agent_observe_abi.h",
    "agent_resource_abi.h",
    "agent_tool_abi.h",
    "io_policy.h",
    "kernel_work_abi.h",
    "user/include/agent.h",
    "user/include/agent_metadata_test_abi.h",
    "user/include/fcntl.h",
    "user/include/io_policy.h",
    "user/include/kernel_work_abi.h",
    "user/include/rp_launch_attestation.h",
    "user/include/stddef.h",
    "user/include/stdio.h",
    "user/include/stdlib.h",
    "user/include/string.h",
    "user/include/unistd.h",
    "user/include/wait_atomic_test_abi.h",
    "user/lib/arch/riscv/crt.S",
    "user/lib/arch/riscv/syscall_arch.h",
    "user/lib/main.c",
    "user/lib/stdio.c",
    "user/lib/stdlib.c",
    "user/lib/string.c",
    "user/lib/syscall.c",
    "user/lib/syscall.h",
    "user/lib/syscall_ids.h",
    "user/src/agenteval_ucore.c",
    "wait_atomic_test_abi.h",
)
KERNEL_TRANSLATION_UNIT_PATHS = tuple(
    path for path in KERNEL_RUNTIME_DEPENDENCY_PATHS
    if path.endswith((".c", ".S"))
)
EXPECTED_USER_LIBRARY_SOURCES = tuple(
    path for path in USER_TRANSLATION_UNITS
    if path.startswith("user/lib/") and path.endswith(".c")
)
EXPECTED_USER_INCLUDE_ROOT_FILES = (
    "user/include/agent.h",
    "user/include/agent_metadata_test_abi.h",
    "user/include/agent_observe_test_phase_abi.h",
    "user/include/exec_policy_manifest.h",
    "user/include/fcntl.h",
    "user/include/fs_allocator_test_abi.h",
    "user/include/io_policy.h",
    "user/include/kernel_work_abi.h",
    "user/include/physical_page_test_abi.h",
    "user/include/research_platform_state.h",
    "user/include/rp_evidence.h",
    "user/include/rp_launch_attestation.h",
    "user/include/rp_program_manifest.h",
    "user/include/rp_resource_stability.h",
    "user/include/stddef.h",
    "user/include/stdio.h",
    "user/include/stdlib.h",
    "user/include/string.h",
    "user/include/unistd.h",
    "user/include/user_stack_policy.h",
    "user/include/virtio_test_abi.h",
    "user/include/wait_atomic_test_abi.h",
    "user/lib/arch/riscv/crt.S",
    "user/lib/arch/riscv/syscall_arch.h",
    "user/lib/arch/riscv/syscall_ids.h.in",
    "user/lib/arch/riscv/user.ld",
    "user/lib/main.c",
    "user/lib/stdio.c",
    "user/lib/stdlib.c",
    "user/lib/string.c",
    "user/lib/syscall.c",
    "user/lib/syscall.h",
    "user/lib/syscall_ids.h",
)
REQUIRED_KERNEL_RESULT_PATHS = frozenset({
    "os/agent_context.c",
    "os/agent_core.c",
    "os/agent_ipc.c",
    "os/agent_metadata_objects.c",
    "os/console.c",
    "os/printf.c",
    "os/syscall.c",
})

SIMPLE_SYSCALL_WRAPPERS = {
    "open": ("SYS_openat", "path", "flags", "O_RDWR"),
    "read": ("SYS_read", "fd", "buf", "len"),
    "write": ("SYS_write", "fd", "buf", "len"),
    "getpid": ("SYS_getpid",),
    "getppid": ("SYS_getppid",),
    "sched_yield": ("SYS_sched_yield",),
    "sys_get_time": ("SYS_gettimeofday", "ts", "tz"),
    "waitpid": ("SYS_wait4", "pid", "code"),
    "fstat": ("SYS_fstat", "fd", "st"),
    "sys_unlinkat": ("SYS_unlinkat", "dirfd", "path", "flags"),
    "pipe": ("SYS_pipe2", "p"),
    "agent_scope_delegate_fd": ("SYS_agent_scope_delegate_fd", "fd"),
    "agent_info": ("SYS_agent_info", "info"),
    "agent_run": ("SYS_agent_run", "ops", "results", "count", "flags"),
    "sys_tool_call": ("SYS_tool_call", "req", "resp"),
    "context_push": ("SYS_context_push", "record"),
    "context_query": ("SYS_context_query", "start_sequence", "out", "max"),
    "context_snapshot": ("SYS_context_snapshot", "header", "records", "max"),
    "context_rollback": ("SYS_context_rollback", "sequence"),
    "context_clear": ("SYS_context_clear",),
    "agent_watch": ("SYS_agent_watch", "event_type", "filter"),
    "agent_unwatch": ("SYS_agent_unwatch", "event_type", "filter"),
    "agent_wait": ("SYS_agent_wait", "event", "timeout_ticks"),
    "sys_agent_heartbeat_set": ("SYS_agent_heartbeat_set", "interval_ticks"),
    "sys_agent_heartbeat_stop": ("SYS_agent_heartbeat_stop",),
    "agent_wake": ("SYS_agent_wake", "pid", "event"),
    "agent_file_meta_init": ("SYS_agent_file_meta_init",),
    "agent_file_meta_set": ("SYS_agent_file_meta_set", "meta"),
    "agent_file_query": ("SYS_agent_file_query", "query", "result"),
    "agent_route_config": (
        "SYS_agent_route_config", "source_pid", "target_pid", "event_mask",
        "operation",
    ),
}

DELEGATING_WRAPPERS = {
    "tool_call": ("sys_tool_call", "req", "resp"),
    "tool_list": ("sys_tool_list", "out", "max"),
    "agent_heartbeat_set": ("sys_agent_heartbeat_set", "interval_ticks"),
    "agent_heartbeat_stop": ("sys_agent_heartbeat_stop",),
    "unlink": ("sys_unlinkat", "AT_FDCWD", "path", "0"),
}

CRITICAL_LIBRARY_HELPER_FINGERPRINTS = {
    "context_mirror_hash_mix": "68bb038c79977166302829993fadfb69e5c488eeedc190fbfc8728cf622ebc45",
    "context_mirror_hash_bytes": "9406ede880c01e3a985538016deb4dee226a39e0e2026184fd712b078f82f392",
    "context_mirror_record_hash": "26b8dcc17795eb689d69a61840d52b9162ccb4142edf21b1e87e14316a6fbca9",
    "context_mirror_record": "2c269ae66429dd7c786b9c7891dc7bc38f948c81be59cd09060a020f1872867d",
    "context_mirror_active_record": "b6794e3c46cdd6d504c4d9a4b437a93a5eac40c785d9a4e4da49a6a0d20c8eaf",
    "context_mirror_active_query": "18a286ce9b8e546eb2231bfcbe5a901bfd70265f7ada0639f55cf532fa799b25",
}

CRITICAL_PUBLIC_NAMES = frozenset(
    tuple(SIMPLE_SYSCALL_WRAPPERS)
    + tuple(DELEGATING_WRAPPERS)
    + (
        "agent_create", "agent_create_role", "close", "exit", "get_mtime",
        "printf", "process_spawn_finish", "sleep", "sys_tool_list",
        "context_mirror_active_query",
    )
)


def resolve_functional_include_closure(repo: Path) -> tuple[str, ...]:
    root = repo.resolve(strict=True)
    include_roots = tuple((root / path).resolve(strict=True)
                          for path in USER_INCLUDE_SEARCH_PATHS)
    pending = list(USER_TRANSLATION_UNITS)
    resolved: set[str] = set()
    include_re = re.compile(
        r'^\s*#\s*include\s*([<"])([^>"\r\n]+)[>"]', re.MULTILINE
    )
    while pending:
        relative = pending.pop()
        if relative in resolved:
            continue
        path = (root / relative).resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError("functional include escapes the repository") from error
        resolved.add(relative)
        text = _canonical_preprocessor_text(
            relative, path.read_text(encoding="utf-8")
        )
        for match in include_re.finditer(text):
            delimiter, name = match.groups()
            if re.search(r"\s", name):
                raise ValueError(
                    f"functional include name contains whitespace: {name!r}"
                )
            candidates = []
            if delimiter == '"':
                candidates.append(path.parent / name)
            candidates.extend(directory / name for directory in include_roots)
            selected = next(
                (candidate.resolve(strict=True) for candidate in candidates
                 if candidate.is_file()),
                None,
            )
            if selected is None:
                if name == "agenteval_seed.h":
                    resolved.add(GENERATED_CHALLENGE_HEADER)
                    continue
                raise ValueError(f"unresolved functional compile include: {name}")
            try:
                selected_relative = selected.relative_to(root).as_posix()
            except ValueError as error:
                raise ValueError(
                    f"functional include resolves outside repository: {name}"
                ) from error
            pending.append(selected_relative)
    return tuple(sorted(resolved))


def resolve_kernel_include_closure(repo: Path) -> tuple[str, ...]:
    root = repo.resolve(strict=True)
    pending = list(KERNEL_TRANSLATION_UNIT_PATHS)
    resolved: set[str] = set()
    include_re = re.compile(
        r'^\s*#\s*include\s*([<"])([^>"\r\n]+)[>"]', re.MULTILINE
    )
    while pending:
        relative = pending.pop()
        if relative in resolved:
            continue
        path = (root / relative).resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError("kernel include escapes the repository") from error
        resolved.add(relative)
        text = _canonical_preprocessor_text(
            relative, path.read_text(encoding="utf-8")
        )
        for match in include_re.finditer(text):
            delimiter, name = match.groups()
            candidates = []
            if delimiter == '"':
                candidates.append(path.parent / name)
            candidates.extend((root / "os" / name, root / name))
            selected = next(
                (candidate.resolve(strict=True) for candidate in candidates
                 if candidate.is_file()),
                None,
            )
            if selected is None:
                if delimiter == "<" and name in TOOLCHAIN_PROVIDED_HEADERS:
                    resolved.add(f"{TOOLCHAIN_INCLUDE_PREFIX}{name}")
                    continue
                raise ValueError(f"unresolved kernel compile include: {name}")
            try:
                selected_relative = selected.relative_to(root).as_posix()
            except ValueError as error:
                raise ValueError(
                    f"kernel include resolves outside repository: {name}"
                ) from error
            pending.append(selected_relative)
    return tuple(sorted(resolved))


def _validate_source_inventories(repo: Path) -> None:
    root = repo.resolve(strict=True)
    precompiled_roots = (
        root,
        root / "os",
        root / "nfs",
        *(root / relative for relative in USER_INCLUDE_SEARCH_PATHS),
    )
    precompiled_candidates = list(root.glob("*.gch")) + list(root.glob("*.pch"))
    for directory in precompiled_roots[1:]:
        precompiled_candidates.extend(
            path for path in directory.rglob("*")
            if path.name.casefold().endswith((".gch", ".pch"))
        )
    if precompiled_candidates:
        raise ValueError("precompiled header can bypass reviewed source headers")
    actual_kernel = tuple(sorted(
        path.relative_to(root).as_posix()
        for pattern in ("*.c", "*.S")
        for path in (root / "os").glob(pattern)
        if path.name != "initproc.S"
    ))
    if actual_kernel != tuple(sorted(KERNEL_TRANSLATION_UNIT_PATHS)):
        raise ValueError("kernel wildcard translation-unit inventory differs")
    actual_libraries = tuple(sorted(
        path.relative_to(root).as_posix()
        for path in (root / "user" / "lib").glob("*.c")
    ))
    if actual_libraries != tuple(sorted(EXPECTED_USER_LIBRARY_SOURCES)):
        raise ValueError("user wildcard library inventory differs")
    actual_include_files = tuple(sorted({
        path.relative_to(root).as_posix()
        for relative in USER_INCLUDE_SEARCH_PATHS
        for path in (root / relative).rglob("*")
        if path.is_file()
    }))
    if actual_include_files != tuple(sorted(EXPECTED_USER_INCLUDE_ROOT_FILES)):
        raise ValueError("user include-search-root inventory differs")
    selected_apps = tuple(sorted(
        path.relative_to(root).as_posix()
        for path in (root / "user" / "src").glob("agenteval_ucore*.c")
    ))
    if selected_apps != ("user/src/agenteval_ucore.c",):
        raise ValueError("functional application selector inventory differs")
    for directory in (root, root / "user", root / "nfs"):
        names = {path.name for path in directory.iterdir()}
        folded = {name.casefold() for name in names}
        if (
            any(
                name.casefold() == "gnumakefile"
                or name.casefold().startswith("gnumakefile.")
                or name.casefold() == "s.gnumakefile"
                or (name.casefold() == "makefile" and name != "Makefile")
                or name.casefold().startswith("makefile.")
                or name.casefold() == "s.makefile"
                for name in names
            )
            or folded & {"rcs", "sccs"}
        ):
            raise ValueError("alternate GNU makefile would bypass reviewed build")


def load_compile_dependency_texts(repo: Path) -> dict[str, str]:
    root = repo.resolve(strict=True)
    texts: dict[str, str] = {}
    for relative in COMPILE_DEPENDENCY_PATHS:
        texts[relative] = (root / relative).read_text(encoding="utf-8")
    return texts


def _normalized_dependency(relative: str, text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def compile_closure_fingerprint(texts: dict[str, str]) -> str:
    if set(texts) != set(COMPILE_DEPENDENCY_PATHS):
        raise ValueError("functional compile dependency inventory differs")
    digest = hashlib.sha256()
    for relative in COMPILE_DEPENDENCY_PATHS:
        payload = _normalized_dependency(relative, texts[relative])
        digest.update(relative.encode("ascii"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _direct_return(name: str, callee: str, arguments: tuple[str, ...]) -> tuple[str, ...]:
    body = ["return", callee, "("]
    for index, argument in enumerate(arguments):
        if index:
            body.append(",")
        body.append(argument)
    body.extend((")", ";"))
    return tuple(body)


def _canonical_preprocessor_text(relative: str, text: str) -> str:
    if re.search(r"\?\?[=/'()!<>-]|%:", text):
        raise ValueError(
            f"functional compile dependency uses alternate preprocessing: {relative}"
        )
    spliced = re.sub(r"\\[ \t\v\f]*(?:\r\n|\r|\n)", "", text)
    without_blocks = re.sub(r"/\*.*?\*/", " ", spliced, flags=re.DOTALL)
    return re.sub(r"//[^\r\n]*", "", without_blocks)


def _validate_header_redirects(texts: dict[str, str]) -> None:
    macro = re.compile(
        r"^\s*#\s*define\s+(" + "|".join(
            re.escape(name) for name in sorted(CRITICAL_PUBLIC_NAMES)
        ) + r")(?:\s|\()",
        re.MULTILINE,
    )
    for relative, text in texts.items():
        if not relative.endswith((".c", ".h")):
            continue
        canonical = _canonical_preprocessor_text(relative, text)
        if relative.endswith(".h") and macro.search(canonical):
            raise ValueError(
                f"functional compile dependency redirects a critical call: {relative}"
            )
        undefs = set(re.findall(
            r"^\s*#\s*undef\s+([A-Za-z_][A-Za-z0-9_]*)",
            canonical,
            re.MULTILINE,
        ))
        if undefs & CRITICAL_PUBLIC_NAMES:
            raise ValueError(
                f"functional compile dependency undefines a critical call: {relative}"
            )


def _validate_wrappers(texts: dict[str, str]) -> None:
    tokens = _lex(texts["user/lib/syscall.c"])
    for name, arguments in SIMPLE_SYSCALL_WRAPPERS.items():
        expected = _direct_return(name, "syscall", arguments)
        if tuple(_function_tokens(tokens, name)) != expected:
            raise ValueError(f"functional syscall wrapper differs: {name}")
    for name, arguments in DELEGATING_WRAPPERS.items():
        expected = _direct_return(name, arguments[0], arguments[1:])
        if tuple(_function_tokens(tokens, name)) != expected:
            raise ValueError(f"functional delegating wrapper differs: {name}")

    tool_list = tuple(_function_tokens(tokens, "sys_tool_list"))
    expected_tool_list = (
        "return", "syscall", "(", "SYS_tool_list", ",", "out", ",", "max",
        ",", "sizeof", "(", "struct", "agent_tool_desc_v2", ")", ",",
        "AGENT_CALL_VERSION_V2", ")", ";",
    )
    if tool_list != expected_tool_list:
        raise ValueError("functional syscall wrapper differs: sys_tool_list")

    exceptional = {
        "process_spawn_finish": (
            "return", "__stdio_process_spawn_finish", "(", "locked", ",",
            "result", ")", ";",
        ),
        "agent_create": (
            "int", "locked", "=", "__stdio_process_spawn_prepare", "(", ")",
            ";", "if", "(", "locked", "<", "0", ")", "return", "-", "1",
            ";", "return", "process_spawn_finish", "(", "locked", ",",
            "syscall", "(", "SYS_agent_create", ")", ")", ";",
        ),
        "agent_create_role": (
            "int", "locked", "=", "__stdio_process_spawn_prepare", "(", ")",
            ";", "if", "(", "locked", "<", "0", ")", "return", "-", "1",
            ";", "return", "process_spawn_finish", "(", "locked", ",",
            "syscall", "(", "SYS_agent_create_role", ",", "role", ")", ")",
            ";",
        ),
        "close": (
            "if", "(", "fd", "==", "1", ")", "{", "__write_buffer", "(",
            ")", ";", "__clear_buffer", "(", ")", ";", "}", "return",
            "syscall", "(", "SYS_close", ",", "fd", ")", ";",
        ),
        "exit": (
            "__write_buffer", "(", ")", ";", "__clear_buffer", "(", ")",
            ";", "syscall", "(", "SYS_exit", ",", "code", ")", ";",
        ),
        "get_mtime": (
            "TimeVal", "time", ";", "int", "err", "=", "sys_get_time", "(",
            "&", "time", ",", "0", ")", ";", "if", "(", "err", "==", "0",
            ")", "{", "return", "(", "time", ".", "sec", "*", "1000", "+",
            "time", ".", "usec", "/", "1000", ")", ";", "}", "return", "-",
            "1", ";",
        ),
        "sleep": (
            "unsigned", "long", "long", "s", "=", "get_mtime", "(", ")", ";",
            "while", "(", "get_mtime", "(", ")", "<", "s", "+", "time", ")",
            "{", "sched_yield", "(", ")", ";", "}", "return", "0", ";",
        ),
    }
    for name, expected in exceptional.items():
        if tuple(_function_tokens(tokens, name)) != expected:
            raise ValueError(f"functional exceptional wrapper differs: {name}")
    for name, expected in CRITICAL_LIBRARY_HELPER_FINGERPRINTS.items():
        body = _function_tokens(tokens, name)
        actual = hashlib.sha256(
            json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode(
                "ascii"
            )
        ).hexdigest()
        if actual != expected:
            raise ValueError(f"functional library helper differs: {name}")


def _validate_syscall_architecture(texts: dict[str, str]) -> None:
    dispatch = _canonical_preprocessor_text(
        "user/lib/syscall.h", texts["user/lib/syscall.h"]
    )
    if len(re.findall(
        r"^\s*#\s*define\s+syscall\s*\(\.\.\.\)\s+__syscall\s*\(__VA_ARGS__\)\s*$",
        dispatch,
        re.MULTILINE,
    )) != 1:
        raise ValueError("functional syscall dispatcher differs")
    definitions = re.findall(
        r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)",
        dispatch,
        re.MULTILINE,
    )
    for name in ("syscall", "__syscall", "__syscall1", "__syscall2",
                 "__syscall3", "__syscall4", "__syscall5", "__syscall6"):
        if definitions.count(name) != 1:
            raise ValueError(f"functional syscall macro inventory differs: {name}")
    undefs = re.findall(
        r"^\s*#\s*undef\s+([A-Za-z_][A-Za-z0-9_]*)",
        dispatch,
        re.MULTILINE,
    )
    if any(name == "syscall" or name.startswith("__syscall") for name in undefs):
        raise ValueError("functional syscall dispatcher is undefined or replaced")
    architecture = texts["user/lib/arch/riscv/syscall_arch.h"]
    if (
        architecture.count('"ecall\\n\\t"') != 1
        or architecture.count("return a0;") != 1
        or architecture.count("register long a7") != 7
    ):
        raise ValueError("functional syscall architecture no longer uses reviewed ecall")


def _require_fragment_once(text: str, fragment: str, label: str) -> None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.count(fragment) != 1:
        raise ValueError(f"functional build selector differs: {label}")


def _require_single_assignment(
    text: str, name: str, expected: str, label: str
) -> None:
    assignments = re.findall(
        rf"(?m)^[ \t]*(?:override[ \t]+)?{re.escape(name)}"
        rf"[ \t]*(?::|\+|\?)?=.*$",
        text,
    )
    if assignments != [expected]:
        raise ValueError(f"functional build assignment differs: {label}")


def _validate_build_selectors(texts: dict[str, str]) -> None:
    user_make = texts["user/Makefile"].replace("\r\n", "\n")
    _require_single_assignment(user_make, "app_dir", "app_dir := src", "app source")
    _require_single_assignment(
        user_make, "LIB_C", "LIB_C := $(wildcard lib/*.c)", "user libraries"
    )
    _require_single_assignment(
        user_make, "SRCS", "SRCS := $(wildcard $(app_dir)/*.c)", "user sources"
    )
    _require_single_assignment(
        user_make,
        "EVALUATION_TESTS",
        "EVALUATION_TESTS := agenteval_ucore",
        "functional application",
    )
    _require_single_assignment(
        user_make,
        "SELECTED_APPS",
        "SELECTED_APPS = $(foreach t,$(CH_TESTS),$(filter $(t)%,$(APPS)))",
        "selected applications",
    )
    for fragment, label in (
        (
            "CFLAGS := $(COMMON_CFLAGS) -Iinclude -Ilib -I$(arch_dir) "
            "-I$(generated_dir)",
            "functional include order",
        ),
        (
            "else ifeq ($(CHAPTER), agent_eval)\n"
            "CH_TESTS := $(EVALUATION_TESTS)",
            "functional chapter",
        ),
        (
            "'#define AGENTEVAL_CHALLENGE "
            "0x$(AGENT_EVAL_CHALLENGE_HEX)ULL'",
            "challenge header",
        ),
        (
            "$(elf_dir)/agenteval_ucore: $(EVALUATION_HEADER)",
            "challenge dependency",
        ),
        (
            "$(elf_dir)/%: $(app_dir)/%.c $(LIB_OBJS) $(CRT_OBJ) "
            "$(GENERATED_HEADERS) $(arch_dir)/user.ld",
            "user link rule",
        ),
        (
            "binary: $(addprefix $(elf_dir)/,$(SELECTED_APPS))",
            "binary target",
        ),
        (
            "target: binary\n"
            "\t@rm -rf $(out_dir)\n"
            "\t@mkdir -p $(out_dir)/bin $(out_dir)/elf",
            "user image staging",
        ),
    ):
        _require_fragment_once(user_make, fragment, label)

    root_make = texts["Makefile"].replace("\r\n", "\n")
    for fragment, label in (
        ("C_SRCS = $(wildcard $K/*.c)", "kernel C wildcard"),
        ("AS_SRCS = $(wildcard $K/*.S)", "kernel assembly wildcard"),
        (
            "$(K)/initproc.S: scripts/initproc.py .FORCE\n"
            "\t@$(PYTHON_CMD) -I -S scripts/initproc.py $(INIT_PROC)",
            "init process generator",
        ),
        (
            "$(LD_CMD) $(LDFLAGS) -T os/kernel.ld -o "
            "$(BUILDDIR)/kernel $(OBJS)",
            "kernel link",
        ),
        (
            "ifneq ($(FUNCTIONAL_REVIEW_BUILD),1)\n"
            "-include $(HEADER_DEP)\n"
            "endif",
            "generated dependency isolation",
        ),
        (
            "$(F)/fs.img: user .FORCE\n"
            "\t$(MAKE) -rR -C $(F) -f Makefile",
            "filesystem user dependency",
        ),
        (
            "clean:\n"
            "\t$(MAKE) -rR -C $(U) -f Makefile clean\n"
            "\trm -rf $(BUILDDIR) os/initproc.S",
            "root clean delegation",
        ),
        (
            "user:\n"
            "\t$(MAKE) -rR -C user -f Makefile "
            "CHAPTER=$(CHAPTER) BASE=$(BASE) \\\n"
            "\t\tTOOLPREFIX=$(call shell_quote,$(TOOLPREFIX))",
            "root user delegation",
        ),
    ):
        _require_fragment_once(root_make, fragment, label)

    nfs_make = texts["nfs/Makefile"].replace("\r\n", "\n")
    for fragment, label in (
        ("USER_BINS := $(wildcard $(U)/$(USER_BIN_DIR)/*)", "image binaries"),
        (
            "fs.img: .FORCE $(FS_FUSE) $(USER_BINS) $(USER_ELFS) $(EXEC_POLICY)",
            "filesystem prerequisites",
        ),
        (
            "\t\t./$(FS_FUSE) \"$$tmp\" $(USER_BINS); \\\n",
            "filesystem population",
        ),
    ):
        _require_fragment_once(nfs_make, fragment, label)

    runner = texts["scripts/run-agent-tests.sh"].replace("\r\n", "\n")
    for fragment, label in (
        (
            'if [[ "${AGENT_TEST_CASE:-}" == "agenteval_ucore" ]]; then\n'
            '\tCHAPTER="${CHAPTER:-agent_eval}"',
            "functional runner selection",
        ),
        (
            '"${MAKE_TOOL}" -rR -f Makefile nfs/fs.img \\\n'
            '\t\tTOOLPREFIX="${TOOLPREFIX}" CHAPTER="${CHAPTER}" \\\n'
            '\t\tFUNCTIONAL_REVIEW_BUILD=1 \\\n'
            '\t\tUSER_EXTRA_CFLAGS="${user_extra_cflags}"',
            "functional image build",
        ),
        (
            '"${MAKE_TOOL}" -rR -f Makefile build \\\n'
            '\t\tTOOLPREFIX="${TOOLPREFIX}" \\\n'
            '\t\tLOG="${LOG}" \\\n'
            '\t\tINIT_PROC="${init_proc}" \\\n'
            '\t\tCHAPTER="${CHAPTER}"',
            "functional kernel build",
        ),
        (
            '"${PYTHON_BIN}" -I -S -B scripts/agent_test_runner.py \\\n'
            '\t\t--init-proc "${init_proc}" \\\n'
            '\t\t--marker "${marker}" \\\n'
            '\t\t--marker-mode exact-line',
            "QEMU result monitor",
        ),
    ):
        _require_fragment_once(runner, fragment, label)
    make_lines = [
        line.lstrip() for line in runner.splitlines()
        if '"${MAKE_TOOL}"' in line
    ]
    if len(make_lines) != 7 or any(
        not line.startswith('"${MAKE_TOOL}" -rR ') for line in make_lines
    ):
        raise ValueError("functional runner has an unpinned GNU make invocation")
    if runner.count("FUNCTIONAL_REVIEW_BUILD=1") != 5:
        raise ValueError("functional runner does not isolate generated dependencies")
    python_lines = [
        line for line in runner.splitlines() if '"${PYTHON_BIN}"' in line
    ]
    if len(python_lines) != 13 or any(
        '"${PYTHON_BIN}" -I -S -B' not in line for line in python_lines
    ):
        raise ValueError(
            "functional runner has a non-isolated or source-polluting Python invocation"
        )

    initproc = texts["scripts/initproc.py"].replace("\r\n", "\n")
    for fragment, label in (
        ("parser.add_argument('INIT_PROC', default=\"usershell\")", "init argument"),
        ('.string \\"{0}\\"', "init image selector"),
        ("'''.format(args.INIT_PROC)", "init argument binding"),
    ):
        _require_fragment_once(initproc, fragment, label)


def validate_functional_compile_source_texts(texts: dict[str, str]) -> None:
    if compile_closure_fingerprint(texts) != COMPILE_CLOSURE_FINGERPRINT:
        raise ValueError("reviewed functional compile closure differs")
    _validate_header_redirects(texts)
    _validate_wrappers(texts)
    _validate_syscall_architecture(texts)
    _validate_build_selectors(texts)


def validate_functional_compile_sources(repo: Path) -> None:
    _validate_source_inventories(repo)
    closure = resolve_functional_include_closure(repo)
    if closure != EXPECTED_INCLUDE_CLOSURE:
        raise ValueError("functional compiler include closure differs")
    kernel_closure = resolve_kernel_include_closure(repo)
    if not REQUIRED_KERNEL_RESULT_PATHS <= set(kernel_closure):
        raise ValueError("kernel functional result path is missing")
    unexpected = {
        path for path in kernel_closure
        if path not in COMPILE_DEPENDENCY_PATHS
        and not path.startswith(TOOLCHAIN_INCLUDE_PREFIX)
    }
    if unexpected:
        raise ValueError("kernel compile include closure differs")
    validate_functional_compile_source_texts(load_compile_dependency_texts(repo))
