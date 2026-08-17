#!/usr/bin/env python3
"""Real toolchain and QEMU integration for the generic Nexus development broker."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_nexus_dev as dev


_RELATIVE = "user/src/nexus_harness_probe_ucore.c"
_TARGET = "nexus_harness_probe_ucore"


def _field(content: str, name: str) -> str:
    header = content.split("\ncontent_begin\n", 1)[0]
    if name == "kind":
        first = header.splitlines()[0] if header else ""
        if not first:
            raise RuntimeError("integration_field_missing:kind")
        return first
    prefix = f"{name}="
    matches = [line[len(prefix) :] for line in header.splitlines()
               if line.startswith(prefix)]
    if len(matches) != 1 or not matches[0]:
        raise RuntimeError(f"integration_field_missing:{name}")
    return matches[0]


def _revision(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def integration(workspace: Path, qemu: str) -> int:
    root = workspace.resolve(strict=True)
    source = root / _RELATIVE
    if source.exists() or source.is_symlink():
        raise RuntimeError("integration_probe_path_already_exists")
    broken = (
        "#include <stdio.h>\n"
        "#include <unistd.h>\n\n"
        "int main(void)\n"
        "{\n"
        "\tchar input[32];\n"
        "\tint count = read(0, input, sizeof(input));\n"
        "\tif (count <= 0) {\n"
        "\t\tprintf(\"error=empty\\n\");\n"
        "\t\treturn 2;\n"
        "\t}\n"
        "\treturn nexus_missing_handler(input, count);\n"
        "}\n"
    )
    fixed = (
        "#include <stdio.h>\n"
        "#include <unistd.h>\n\n"
        "int main(void)\n"
        "{\n"
        "\tchar input[32];\n"
        "\tint count = read(0, input, sizeof(input));\n"
        "\tif (count <= 0) {\n"
        "\t\tprintf(\"error=empty\\n\");\n"
        "\t\treturn 2;\n"
        "\t}\n"
        "\tif (count >= 2 && input[0] == 'o' && input[1] == 'k') {\n"
        "\t\tprintf(\"result=42\\n\");\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\tif (count >= 3 && input[0] == 'b' && input[1] == 'a' && input[2] == 'd') {\n"
        "\t\tprintf(\"error=invalid\\n\");\n"
        "\t\treturn 2;\n"
        "\t}\n"
        "\tprintf(\"error=failure\\n\");\n"
        "\treturn 3;\n"
        "}\n"
    )
    patch = (
        f"--- a/{_RELATIVE}\n"
        f"+++ b/{_RELATIVE}\n"
        f"@@ -1,{len(broken.splitlines())} +1,{len(fixed.splitlines())} @@\n"
        + "".join(f"-{line}\n" for line in broken.splitlines())
        + "".join(f"+{line}\n" for line in fixed.splitlines())
    )
    broker = dev.NexusDevelopmentBroker(root, qemu=qemu)
    try:
        created = broker.write_file(_RELATIVE, broken, dev.MISSING_REVISION)
        if _field(created.content, "kind") != "workspace_write":
            raise RuntimeError("integration_write_failed")
        broken_revision = _revision(broken)
        failed = broker.build_ucore_program(_RELATIVE, broken_revision, _TARGET)
        if (_field(failed.content, "kind") != "ucore_build"
                or _field(failed.content, "status") != "failed"
                or "nexus_missing_handler" not in failed.content):
            raise RuntimeError("integration_expected_build_failure_missing")
        repaired = broker.apply_patch(_RELATIVE, patch, broken_revision)
        if _field(repaired.content, "kind") != "workspace_patch":
            raise RuntimeError("integration_patch_failed")
        fixed_revision = _revision(fixed)
        if repaired.workspace_generation != fixed_revision:
            raise RuntimeError("integration_patch_revision_mismatch")
        built = broker.build_ucore_program(_RELATIVE, fixed_revision, _TARGET)
        if (_field(built.content, "kind") != "ucore_build"
                or _field(built.content, "status") != "passed"):
            raise RuntimeError("integration_rebuild_failed")
        build_id = _field(built.content, "build_id")
        suite = broker.run_ucore_program(
            build_id,
            [
                {
                    "name": "normal",
                    "stdin": "ok\n",
                    "expected_output": "result=42",
                    "expected_exit": 0,
                    "case_kind": "normal",
                },
                {
                    "name": "invalid",
                    "stdin": "bad\n",
                    "expected_output": "error=invalid",
                    "expected_exit": 2,
                    "case_kind": "invalid",
                },
                {
                    "name": "failure",
                    "stdin": "fail\n",
                    "expected_output": "error=failure",
                    "expected_exit": 3,
                    "case_kind": "failure",
                },
            ],
        )
        try:
            suite_valid = (
                _field(suite.content, "kind") == "ucore_run_suite"
                and _field(suite.content, "status") == "passed"
                and _field(suite.content, "passed_count") == "3"
                and _field(suite.content, "independent_guest_count") == "3"
            )
        except RuntimeError:
            suite_valid = False
        if not suite_valid:
            raise RuntimeError(
                "integration_guest_suite_failed:"
                + suite.content.replace("\n", "|")[:1000]
            )
        print(
            "agentos-nexus-dev-integration: PASS "
            f"failed_build=1 patched=1 build_id={build_id[:16]} "
            "guests=3 cases=normal,invalid,failure"
        )
        return 0
    finally:
        broker.close()
        source.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--qemu", default=dev.QEMU_BINARY)
    args = parser.parse_args()
    return integration(args.workspace, args.qemu)


if __name__ == "__main__":
    raise SystemExit(main())
