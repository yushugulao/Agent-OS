#!/usr/bin/env python3
"""线程陷阱冷状态 accessor 的共享原始源码检查。"""

from __future__ import annotations

import re
from collections.abc import Iterable


class ThreadColdSourceError(ValueError):
    pass


_C_NOISE = re.compile(
    r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
    re.DOTALL,
)
_DISABLED = re.compile(
    r"^[ \t]*#[ \t]*if[ \t]+(?:0|\([ \t]*0[ \t]*\))[ \t]*(?:$|\n)",
    re.MULTILINE,
)
_ACCESSOR_MACRO = re.compile(
    r"^[ \t]*#[ \t]*define[ \t]+thread_trap_cold(?:_const)?(?:[ \t]|\()",
    re.MULTILINE,
)
_ACCESS = re.compile(
    r"\bthread_trap_cold(?:_const)?\(\s*([A-Za-z_]\w*)\s*\)\s*->"
)


def strip_c_noise(source: str, label: str = "source") -> str:
    """清空注释和字面量，同时保持源码位置与换行。"""

    def blank(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group())

    clean = _C_NOISE.sub(blank, source)
    if _DISABLED.search(clean):
        raise ThreadColdSourceError(f"{label}: disabled #if 0 source is forbidden")
    return clean


def _matching(source: str, opening: int, left: str, right: str) -> int:
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == left:
            depth += 1
        elif source[index] == right:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _function_bodies(source: str, name: str) -> list[str]:
    bodies: list[str] = []
    for match in re.finditer(rf"\b{re.escape(name)}\s*\(", source):
        opening = source.find("(", match.start())
        closing = _matching(source, opening, "(", ")")
        if closing < 0:
            continue
        brace_match = re.search(r"\S", source[closing + 1 :])
        if brace_match is None or brace_match.group() != "{":
            continue
        brace = closing + 1 + brace_match.start()
        end = _matching(source, brace, "{", "}")
        if end < 0:
            raise ThreadColdSourceError(f"unterminated function {name}")
        bodies.append(source[brace + 1 : end])
    return bodies


def _struct_body(source: str, name: str) -> str:
    matches = list(re.finditer(rf"\bstruct\s+{re.escape(name)}\s*\{{", source))
    if len(matches) != 1:
        raise ThreadColdSourceError(
            f"struct {name}: expected one definition, found {len(matches)}"
        )
    opening = source.find("{", matches[0].start())
    closing = _matching(source, opening, "{", "}")
    if closing < 0:
        raise ThreadColdSourceError(f"unterminated struct {name}")
    return source[opening + 1 : closing]


def verify_thread_cold_contract(proc_h: str, sources: Iterable[str] = ()) -> None:
    clean_proc = strip_c_noise(proc_h, "os/proc.h")
    if _ACCESSOR_MACRO.search(clean_proc):
        raise ThreadColdSourceError("os/proc.h: cold accessor must not be a macro")

    expected = {
        "thread_trap_cold": (
            "return(structthread_trap_cold*)((uchar*)t->trapframe+"
            "THREAD_TRAP_COLD_OFFSET);"
        ),
        "thread_trap_cold_const": (
            "return(conststructthread_trap_cold*)((constuchar*)t->trapframe+"
            "THREAD_TRAP_COLD_OFFSET);"
        ),
    }
    for name, required_body in expected.items():
        bodies = _function_bodies(clean_proc, name)
        if len(bodies) != 1:
            raise ThreadColdSourceError(
                f"os/proc.h: {name} expected one definition, found {len(bodies)}"
            )
        if re.sub(r"\s+", "", bodies[0]) != required_body:
            raise ThreadColdSourceError(f"os/proc.h: non-canonical {name} body")

    cold = _struct_body(clean_proc, "thread_trap_cold")
    hot = _struct_body(clean_proc, "thread")
    for field in (
        "kernel_work_depth",
        "kernel_resched_pending",
        "io_request_flags",
        "io_request_id",
        "io_request_depth",
        "io_request_owner",
        "io_request_class",
        "io_request_reservation",
        "io_request_device_reservation",
        "io_request_transfers",
        "bio_buffer_holds",
        "bio_fs_atomic_depth",
    ):
        if re.search(rf"\b{field}\b", cold) is None:
            raise ThreadColdSourceError(f"os/proc.h: cold state missing {field}")
        if re.search(rf"\b{field}\b", hot) is not None:
            raise ThreadColdSourceError(f"os/proc.h: hot thread duplicates {field}")

    for index, source in enumerate(sources):
        clean = strip_c_noise(source, f"source[{index}]")
        if _ACCESSOR_MACRO.search(clean):
            raise ThreadColdSourceError(
                f"source[{index}]: cold accessor macro shadow is forbidden"
            )


def normalize_thread_cold_access(source: str, label: str = "source") -> str:
    """清理源码，并以哨兵形式保留 accessor 来源。"""
    clean = strip_c_noise(source, label)
    return _ACCESS.sub(r"thread_cold(\1)->", clean)
