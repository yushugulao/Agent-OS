#!/usr/bin/env python3
"""Cross-check the shared C and host Python executable-shape classifiers."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host_tools import plain_ucore_fs_extract as fs_extract  # noqa: E402
from host_probe_toolchain import (  # noqa: E402
    host_compiler,
    probe_environment,
    probe_mode,
    required_sanitizer_flags,
)


@dataclass(frozen=True)
class ShapeCase:
    name: str
    regular_file: int
    size: int
    flags: int
    generation: int
    role_mask: int
    layout_version: int
    rw_offset: int
    profile: int
    expected_class: int


TRUSTED = fs_extract.EXEC_FLAG_TRUSTED
IMMUTABLE = fs_extract.EXEC_FLAG_IMMUTABLE
BOOTSTRAP = fs_extract.EXEC_FLAG_BOOTSTRAP
DOMAIN_SAFE = fs_extract.EXEC_FLAG_DOMAIN_SAFE
SEALED = TRUSTED | IMMUTABLE | DOMAIN_SAFE
BOOT_SEALED = SEALED | BOOTSTRAP
WORKER = IMMUTABLE | DOMAIN_SAFE
GENERATION = fs_extract.EXEC_MANIFEST_VERSION
LAYOUT = fs_extract.EXEC_LAYOUT_VERSION
PAGE = fs_extract.USER_PAGE_SIZE
ROLE_SENTINEL = 1 << 1
ROLE_ORCHESTRATOR = 1 << 4
ROLE_ALL = fs_extract.EXEC_MANIFEST_ROLE_ALL
PROFILE_NONE = fs_extract.VFS_EXEC_PROFILE_NONE
PROFILE_WORKFLOW = fs_extract.VFS_EXEC_PROFILE_WORKFLOW
PROFILE_READ = fs_extract.VFS_EXEC_PROFILE_CONTENT_READ
PROFILE_WRITE = fs_extract.VFS_EXEC_PROFILE_ARTIFACT_WRITE

INVALID = fs_extract.EXEC_IMAGE_INVALID
COMPAT = fs_extract.EXEC_IMAGE_COMPAT
WORKER_CLASS = fs_extract.EXEC_IMAGE_WORKER
TRUSTED_ENDPOINT = fs_extract.EXEC_IMAGE_TRUSTED_ENDPOINT
TRUSTED_AGENT = fs_extract.EXEC_IMAGE_TRUSTED_AGENT


CASES = (
    ShapeCase(
        "compat-no-role",
        1,
        PAGE + 1,
        SEALED,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_NONE,
        COMPAT,
    ),
    ShapeCase(
        "compat-agent-downgrade",
        1,
        PAGE * 2,
        SEALED,
        GENERATION,
        ROLE_ALL,
        LAYOUT,
        PAGE,
        PROFILE_NONE,
        COMPAT,
    ),
    ShapeCase(
        "worker-workflow",
        1,
        PAGE * 2,
        WORKER,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_WORKFLOW,
        WORKER_CLASS,
    ),
    ShapeCase(
        "worker-content-read",
        1,
        PAGE * 2,
        WORKER,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_READ,
        WORKER_CLASS,
    ),
    ShapeCase(
        "worker-artifact-write",
        1,
        PAGE * 2,
        WORKER,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_WRITE,
        WORKER_CLASS,
    ),
    ShapeCase(
        "trusted-read-endpoint",
        1,
        PAGE * 2,
        SEALED,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_READ,
        TRUSTED_ENDPOINT,
    ),
    ShapeCase(
        "trusted-write-endpoint",
        1,
        PAGE * 2,
        SEALED,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_WRITE,
        TRUSTED_ENDPOINT,
    ),
    ShapeCase(
        "trusted-agent",
        1,
        PAGE * 2,
        SEALED,
        GENERATION,
        ROLE_ORCHESTRATOR,
        LAYOUT,
        PAGE,
        PROFILE_WORKFLOW,
        TRUSTED_AGENT,
    ),
    ShapeCase(
        "trusted-agent-narrow-ceiling",
        1,
        PAGE * 2,
        SEALED,
        GENERATION,
        ROLE_SENTINEL,
        LAYOUT,
        PAGE,
        PROFILE_READ,
        TRUSTED_AGENT,
    ),
    ShapeCase(
        "trusted-bootstrap-agent",
        1,
        PAGE * 2,
        BOOT_SEALED,
        GENERATION,
        ROLE_ALL,
        LAYOUT,
        PAGE,
        PROFILE_WORKFLOW,
        TRUSTED_AGENT,
    ),
    ShapeCase(
        "not-regular-file",
        0,
        PAGE * 2,
        SEALED,
        GENERATION,
        ROLE_ALL,
        LAYOUT,
        PAGE,
        PROFILE_WORKFLOW,
        INVALID,
    ),
    ShapeCase(
        "empty-flags",
        1,
        PAGE * 2,
        0,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "missing-immutable",
        1,
        PAGE * 2,
        TRUSTED | DOMAIN_SAFE,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "missing-domain-safe",
        1,
        PAGE * 2,
        TRUSTED | IMMUTABLE,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "unknown-flag",
        1,
        PAGE * 2,
        SEALED | 0x10,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "wrong-generation-zero",
        1,
        PAGE * 2,
        SEALED,
        0,
        0,
        LAYOUT,
        PAGE,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "wrong-generation-future",
        1,
        PAGE * 2,
        SEALED,
        GENERATION + 1,
        0,
        LAYOUT,
        PAGE,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "unknown-role",
        1,
        PAGE * 2,
        SEALED,
        GENERATION,
        ROLE_ALL | 0x40,
        LAYOUT,
        PAGE,
        PROFILE_WORKFLOW,
        INVALID,
    ),
    ShapeCase(
        "wrong-layout-zero",
        1,
        PAGE * 2,
        SEALED,
        GENERATION,
        0,
        0,
        PAGE,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "wrong-layout-future",
        1,
        PAGE * 2,
        SEALED,
        GENERATION,
        0,
        LAYOUT + 1,
        PAGE,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "rw-offset-below-page",
        1,
        PAGE * 2,
        SEALED,
        GENERATION,
        0,
        LAYOUT,
        PAGE - 1,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "rw-offset-misaligned",
        1,
        PAGE * 2,
        SEALED,
        GENERATION,
        0,
        LAYOUT,
        PAGE + 1,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "size-equals-rw-offset",
        1,
        PAGE,
        SEALED,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "size-below-rw-offset",
        1,
        PAGE - 1,
        SEALED,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "unknown-profile",
        1,
        PAGE * 2,
        SEALED,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        4,
        INVALID,
    ),
    ShapeCase(
        "untrusted-compat",
        1,
        PAGE * 2,
        WORKER,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_NONE,
        INVALID,
    ),
    ShapeCase(
        "untrusted-role-bearing-worker",
        1,
        PAGE * 2,
        WORKER,
        GENERATION,
        ROLE_SENTINEL,
        LAYOUT,
        PAGE,
        PROFILE_WORKFLOW,
        INVALID,
    ),
    ShapeCase(
        "untrusted-bootstrap-worker",
        1,
        PAGE * 2,
        WORKER | BOOTSTRAP,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_WORKFLOW,
        INVALID,
    ),
    ShapeCase(
        "bootstrap-endpoint",
        1,
        PAGE * 2,
        BOOT_SEALED,
        GENERATION,
        0,
        LAYOUT,
        PAGE,
        PROFILE_WORKFLOW,
        INVALID,
    ),
    ShapeCase(
        "bootstrap-compat",
        1,
        PAGE * 2,
        BOOT_SEALED,
        GENERATION,
        ROLE_ALL,
        LAYOUT,
        PAGE,
        PROFILE_NONE,
        INVALID,
    ),
)


def c_probe_source() -> str:
    rows = "\n".join(
        "    {"
        f"{case.regular_file}, {case.size}ULL, {case.flags}U, "
        f"{case.generation}U, {case.role_mask}U, "
        f"{case.layout_version}U, {case.rw_offset}U, {case.profile}U"
        "},"
        for case in CASES
    )
    return f"""
#include <stdio.h>

#include "nfs/fs.h"
#include "user/include/exec_policy_manifest.h"
#include "exec_image_policy.h"

_Static_assert(EXEC_MANIFEST_F_TRUSTED == EXEC_FLAG_TRUSTED,
               "trusted flag drift");
_Static_assert(EXEC_MANIFEST_F_IMMUTABLE == EXEC_FLAG_IMMUTABLE,
               "immutable flag drift");
_Static_assert(EXEC_MANIFEST_F_BOOTSTRAP == EXEC_FLAG_BOOTSTRAP,
               "bootstrap flag drift");
_Static_assert(EXEC_MANIFEST_F_DOMAIN_SAFE == EXEC_FLAG_DOMAIN_SAFE,
               "domain-safe flag drift");
_Static_assert(EXEC_MANIFEST_VFS_PROFILE_NONE == VFS_EXEC_PROFILE_NONE,
               "empty profile drift");
_Static_assert(EXEC_MANIFEST_VFS_PROFILE_WORKFLOW == VFS_EXEC_PROFILE_WORKFLOW,
               "workflow profile drift");
_Static_assert(EXEC_MANIFEST_VFS_PROFILE_CONTENT_READ ==
                       VFS_EXEC_PROFILE_CONTENT_READ,
               "content-read profile drift");
_Static_assert(EXEC_MANIFEST_VFS_PROFILE_ARTIFACT_WRITE ==
                       VFS_EXEC_PROFILE_ARTIFACT_WRITE,
               "artifact-write profile drift");
_Static_assert(EXEC_IMAGE_INVALID == 0 && EXEC_IMAGE_COMPAT == 1 &&
                       EXEC_IMAGE_WORKER == 2 &&
                       EXEC_IMAGE_TRUSTED_ENDPOINT == 3 &&
                       EXEC_IMAGE_TRUSTED_AGENT == 4,
               "classifier ABI drift");

struct shape_case {{
    int regular_file;
    unsigned long long size;
    unsigned int flags;
    unsigned int generation;
    unsigned int role_mask;
    unsigned int layout_version;
    unsigned int rw_offset;
    unsigned int profile;
}};

static const struct shape_case cases[] = {{
{rows}
}};

int main(void)
{{
    for (unsigned int i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {{
        const struct shape_case *item = &cases[i];
        enum exec_image_policy_class class_id =
            exec_image_protected_classify(
                item->regular_file, item->size, item->flags,
                item->generation, item->role_mask, item->layout_version,
                item->rw_offset, item->profile, 4096U);
        int valid = exec_image_protected_shape_valid(
            item->regular_file, item->size, item->flags,
            item->generation, item->role_mask, item->layout_version,
            item->rw_offset, item->profile, 4096U);

        printf("%u %d\\n", (unsigned int)class_id, valid);
    }}
    return 0;
}}
"""


def compile_and_run_probe(directory: Path) -> tuple[list[tuple[int, int]], str]:
    source = directory / "exec-image-policy-probe.c"
    output = directory / (
        "exec-image-policy-probe.exe" if os.name == "nt" else "exec-image-policy-probe"
    )
    source.write_text(c_probe_source(), encoding="utf-8")
    compiler = host_compiler()
    sanitizer_flags = required_sanitizer_flags(compiler, directory)
    compile_result = subprocess.run(
        compiler
        + [
            *sanitizer_flags,
            "-std=gnu11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(ROOT),
            str(source),
            "-o",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if compile_result.returncode != 0:
        raise SystemExit(
            "C classifier probe failed to compile:\n"
            + compile_result.stdout
            + compile_result.stderr
        )
    run_result = subprocess.run(
        [str(output)],
        cwd=ROOT,
        env=probe_environment(sanitizer_flags),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if run_result.returncode != 0:
        raise SystemExit(
            f"C classifier probe exited {run_result.returncode}:\n"
            + run_result.stdout
            + run_result.stderr
        )
    rows: list[tuple[int, int]] = []
    for line in run_result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise SystemExit(f"malformed C classifier output: {line!r}")
        rows.append((int(fields[0]), int(fields[1])))
    if len(rows) != len(CASES):
        raise SystemExit(
            f"C classifier returned {len(rows)} rows for {len(CASES)} cases"
        )
    return rows, probe_mode(sanitizer_flags)


def python_result(case: ShapeCase) -> tuple[int, int]:
    class_id = fs_extract.exec_image_protected_classify(
        fs_extract.T_FILE if case.regular_file else 0,
        case.size,
        case.flags,
        case.generation,
        case.role_mask,
        case.layout_version,
        case.rw_offset,
        case.profile,
    )
    valid = fs_extract.exec_image_protected_shape_valid(
        fs_extract.T_FILE if case.regular_file else 0,
        case.size,
        case.flags,
        case.generation,
        case.role_mask,
        case.layout_version,
        case.rw_offset,
        case.profile,
    )
    return class_id, int(valid)


def main() -> int:
    names = [case.name for case in CASES]
    if len(names) != len(set(names)):
        raise SystemExit("duplicate executable-shape case name")
    with tempfile.TemporaryDirectory(prefix="agentos-exec-image-") as directory:
        c_rows, sanitizer_mode = compile_and_run_probe(Path(directory))

    for case, c_result in zip(CASES, c_rows):
        expected = (case.expected_class, int(case.expected_class != INVALID))
        py_result = python_result(case)
        if c_result != expected:
            raise SystemExit(
                f"C classifier mismatch for {case.name}: "
                f"expected={expected} actual={c_result}"
            )
        if py_result != expected:
            raise SystemExit(
                f"Python classifier mismatch for {case.name}: "
                f"expected={expected} actual={py_result}"
            )
        if c_result != py_result:
            raise SystemExit(
                f"C/Python classifier drift for {case.name}: "
                f"c={c_result} python={py_result}"
            )

    print(
        "[exec-image-policy] C/Python classifier matrix passed: "
        f"{len(CASES)} cases; mode={sanitizer_mode}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
