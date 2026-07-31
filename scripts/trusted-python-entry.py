#!/usr/bin/env python3
"""Isolated launcher for repository-owned formal evaluation entrypoints."""

from __future__ import annotations

import os
import runpy
import stat
import sys
import tempfile
import types
from pathlib import Path, PurePosixPath


ALLOWED_ENTRYPOINTS = frozenset(
    {
        "host_tools/agenteval_measurement_source_contract.py",
        "host_tools/compatibility_overhead.py",
        "host_tools/contest_demo.py",
        "host_tools/evaluation_bundle.py",
        "host_tools/evaluation_campaign.py",
        "host_tools/evaluation_contract.py",
        "host_tools/evidence_delivery_contract.py",
        "host_tools/evaluation_kernel_build.py",
        "host_tools/evaluation_kernel_cost.py",
        "host_tools/evaluation_platform.py",
        "host_tools/evaluation_scenario.py",
        "host_tools/full_verification_payload.py",
        "host_tools/render_evaluation_dashboard.py",
        "host_tools/scenario_timing_source_contract.py",
        "scripts/capture-final-evidence.py",
        "scripts/fs-allocator-evidence.py",
        "scripts/fs-allocator-image.py",
    }
)


def _fail(message: str) -> "NoReturn":
    print(f"trusted-python-entry: {message}", file=sys.stderr)
    raise SystemExit(2)


def _safe_startup_path() -> bool:
    if getattr(sys, "_agentos_safe_path", False) is True:
        return bool(sys.flags.isolated and sys.flags.no_site)
    flag = getattr(sys.flags, "safe_path", None)
    if flag is not None:
        return bool(flag)
    blocked = {Path.cwd().resolve(), Path(__file__).resolve().parent}
    if not sys.flags.isolated or any(not value for value in sys.path):
        return False
    return all(Path(value).resolve() not in blocked for value in sys.path)


def _reset_import_paths() -> None:
    retained = getattr(sys, "_agentos_stdlib_path", None)
    if retained is None:
        retained = tuple(sys.path)
        sys._agentos_stdlib_path = retained
    if (
        not isinstance(retained, tuple) or not retained
        or any(not isinstance(value, str) or not value for value in retained)
        or any(
            marker in value.replace("\\", "/").casefold()
            for value in retained for marker in ("site-packages", "dist-packages")
        )
    ):
        _fail("interpreter-owned standard-library path is unavailable")
    sys.path[:] = list(retained)


def main() -> None:
    if not sys.flags.isolated or not _safe_startup_path() or not sys.flags.no_site:
        _fail("Python must use isolated no-site safe-path mode (-I -S)")
    _reset_import_paths()
    if len(sys.argv) < 2:
        _fail("repository entrypoint is required")
    value = sys.argv[1].replace("\\", "/")
    relative = PurePosixPath(value)
    if (
        value not in ALLOWED_ENTRYPOINTS
        or relative.is_absolute()
        or relative.as_posix() != value
    ):
        _fail("repository entrypoint is not allowlisted")

    repository = Path(__file__).resolve().parents[1]
    target = repository.joinpath(*relative.parts)
    try:
        info = target.lstat()
        resolved = target.resolve(strict=True)
    except OSError as error:
        _fail(f"repository entrypoint is unavailable: {error}")
    if not stat.S_ISREG(info.st_mode) or target.is_symlink() or resolved != target:
        _fail("repository entrypoint is link-backed or not regular")

    # Default in-tree __pycache__ files are untrusted inputs.  Local modules are
    # reachable only after the standard library and use a fresh external cache
    # namespace for this invocation.
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(
        Path(tempfile.gettempdir()) / f"agentos-pycache-{os.urandom(16).hex()}"
    )
    host_tools = repository / "host_tools"
    package = types.ModuleType("host_tools")
    package.__path__ = [str(host_tools)]
    package.__package__ = "host_tools"
    sys.modules["host_tools"] = package
    sys.path.append(str(host_tools))

    sys.argv = [str(target), *sys.argv[2:]]
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
