#!/usr/bin/env python3
"""Mutation self-test for the worker-batch static contract."""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "worker_batch_contract", ROOT / "scripts/check-rp-worker-batches.py")
assert SPEC is not None and SPEC.loader is not None
CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT)


def scratch_tree() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory(prefix="rp-worker-batch-")
    root = Path(temporary.name)
    (root / "user").mkdir()
    shutil.copy2(ROOT / "user/Makefile", root / "user/Makefile")
    (root / "user/include").mkdir()
    for name in ("rp_program_manifest.h", "rp_worker_batch.h"):
        shutil.copy2(ROOT / "user/include" / name, root / "user/include" / name)
    (root / "user/src").mkdir()
    for source in ROOT.glob("user/src/rp_wbatch*.c"):
        shutil.copy2(source, root / "user/src" / source.name)
    (root / "user/lib").mkdir()
    shutil.copy2(ROOT / "user/lib/research_platform_state.c",
                 root / "user/lib/research_platform_state.c")
    return temporary, root


def rejected(path: Path, old: str, new: str) -> None:
    original = path.read_text(encoding="utf-8")
    assert old in original, f"mutation source missing: {old}"
    path.write_text(original.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    temporary, root = scratch_tree()
    try:
        CONTRACT.check(root)
        mutations = [
            ("user/include/rp_worker_batch.h",
             "value > (RP_WORKER_BATCH_MAX_FD - digit) / 10",
             "value > RP_WORKER_BATCH_MAX_FD"),
            ("user/include/rp_worker_batch.h",
             "runtime->expected == runtime->count",
             "runtime->expected <= runtime->count"),
            ("user/include/rp_program_manifest.h",
             "APPLY(1, rp_state_catalog)", "APPLY(0, rp_state_catalog)"),
            ("user/Makefile", "WORKER_BATCH_FLAT_MAX := 258048",
             "WORKER_BATCH_FLAT_MAX := 258049"),
            ("user/lib/research_platform_state.c",
             "int rp_host_seed_loaded;",
             "int rp_host_seed_loaded;\nint forbidden_function(void) { return 0; }"),
        ]
        for relative, old, new in mutations:
            case_temp, case_root = scratch_tree()
            try:
                rejected(case_root / relative, old, new)
                try:
                    CONTRACT.check(case_root)
                except CONTRACT.ContractError:
                    continue
                raise AssertionError(f"mutation was accepted: {relative}: {old}")
            finally:
                case_temp.cleanup()
    finally:
        temporary.cleanup()
    print("worker-batch mutation tests: PASS (5/5 rejected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
