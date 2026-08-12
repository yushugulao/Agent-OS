#!/usr/bin/env python3
"""Static mutation tests for workflow lifecycle ABI-sized copy clamping."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "os" / "agent_lifecycle.c"


class ContractError(RuntimeError):
    pass


def compact(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def function(source: str, name: str) -> str:
    marker = f"{name}("
    start = source.find(marker)
    if start < 0:
        raise ContractError(f"missing function: {name}")
    opening = source.find("{", start + len(marker))
    if opening < 0:
        raise ContractError(f"missing body: {name}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise ContractError(f"unterminated function: {name}")


def validate(source: str) -> None:
    body = compact(function(source, "sys_agent_workflow_lifecycle_info"))
    size = (
        "copy_size=project_v3?sizeof(info):"
        "(uint64)AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE;"
    )
    if size not in body:
        raise ContractError("lifecycle ABI copy size is not selected before clamping")
    if "if(user_size<copy_size)copy_size=user_size;" not in body:
        raise ContractError("lifecycle ABI copy size does not clamp oversized input")
    if "user_range_check(p->pagetable,addr,copy_size,PTE_W)<0" not in body:
        raise ContractError("lifecycle ABI validates an unclamped user range")
    if "copyout(p->pagetable,addr,(char*)&info,copy_size)<0" not in body:
        raise ContractError("lifecycle ABI copies an unclamped user range")


class LifecycleCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE.read_text(encoding="utf-8")

    def rejected(self, source: str) -> None:
        with self.assertRaises(ContractError):
            validate(source)

    def test_repository_copy_contract_is_valid(self) -> None:
        validate(self.source)

    def test_rejects_raw_oversized_range_validation(self) -> None:
        old = "user_range_check(p->pagetable, addr, copy_size, PTE_W)"
        self.assertIn(old, self.source, "range validation anchor drifted")
        self.rejected(self.source.replace(old, old.replace("copy_size", "user_size"), 1))

    def test_rejects_raw_oversized_copyout(self) -> None:
        old = "copyout(p->pagetable, addr, (char *)&info, copy_size)"
        self.assertIn(old, self.source, "copyout anchor drifted")
        self.rejected(self.source.replace(old, old.replace("copy_size", "user_size"), 1))

    def test_rejects_missing_oversized_clamp(self) -> None:
        old = "if (user_size < copy_size)\n\t\tcopy_size = user_size;"
        self.assertIn(old, self.source, "clamp anchor drifted")
        self.rejected(self.source.replace(old, "if (0)\n\t\tcopy_size = user_size;", 1))


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    raise SystemExit(0 if result.wasSuccessful() else 1)
