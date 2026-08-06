#!/usr/bin/env python3
"""Focused source and behavior tests for the rate-lease free ring."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "os/resource_controller.c"
HEADER = ROOT / "os/resource_controller.h"


def function_body(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"missing function: {signature}")
    opening = source.find("{", start + len(signature))
    if opening < 0:
        raise AssertionError(f"missing function body: {signature}")
    depth = 0
    state = "code"
    index = opening
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "line-comment":
            if char == "\n":
                state = "code"
        elif state == "block-comment":
            if char == "*" and following == "/":
                state = "code"
                index += 1
        elif state == "string":
            if char == "\\":
                index += 1
            elif char == '"':
                state = "code"
        elif char == "/" and following == "/":
            state = "line-comment"
            index += 1
        elif char == "/" and following == "*":
            state = "block-comment"
            index += 1
        elif char == '"':
            state = "string"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
        index += 1
    raise AssertionError(f"unterminated function: {signature}")


def compiler_command() -> list[str] | None:
    configured = os.environ.get("HOST_CC") or os.environ.get("HOSTCC")
    if configured:
        return shlex.split(configured, posix=os.name != "nt")
    compiler = shutil.which("cc") or shutil.which("gcc")
    return [compiler] if compiler else None


class ResourceRateLeaseFreeRingTests(unittest.TestCase):
    def test_allocator_and_release_paths_are_constant_time(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        header = HEADER.read_text(encoding="utf-8")

        self.assertIn("#define RESOURCE_RATE_LEASE_CAP 2049U", header)
        self.assertRegex(
            header,
            r"struct resource_rate_lease_handle\s*\{\s*uint slot;\s*"
            r"uint generation;\s*\};",
        )
        for token in (
            "RESOURCE_RATE_LEASE_FREE",
            "RESOURCE_RATE_LEASE_LIVE",
            "RESOURCE_RATE_LEASE_RETIRED",
            "uint16 free_ring[RESOURCE_RATE_LEASE_CAP];",
            "uint free_head;",
            "uint free_tail;",
            "uint free_count;",
            "sizeof(struct resource_rate_lease) == 128U",
        ):
            self.assertIn(token, source)
        self.assertNotIn("resource_rate_lease_generations", source)
        self.assertNotIn("resource_rate_lease_generation_exhausted", source)

        allocate = function_body(source, "static int resource_rate_lease_allocate(")
        release = function_body(source, "static void resource_rate_lease_release(")
        self.assertNotRegex(allocate, r"\b(?:for|while)\s*\(")
        self.assertNotRegex(release, r"\b(?:for|while)\s*\(")
        for token in (
            "free_ring[",
            "free_head",
            "free_count--",
            "RESOURCE_RATE_LEASE_FREE",
            "RESOURCE_RATE_LEASE_LIVE",
            "lease->generation + 1",
        ):
            self.assertIn(token, allocate)
        for token in (
            "lease - resource_rate_leases",
            "generation == (uint)-1",
            "RESOURCE_RATE_LEASE_RETIRED",
            "free_ring[",
            "free_tail",
            "free_count++",
        ):
            self.assertIn(token, release)

        lookup = function_body(source, "resource_rate_lease_lookup(")
        self.assertIn("lease->tag != RESOURCE_RATE_LEASE_LIVE", lookup)
        self.assertIn("lease->generation != handle.generation", lookup)
        self.assertEqual(source.count("resource_rate_lease_release(lease);"), 2)

    def test_capacity_reuse_stale_handles_and_retirement(self) -> None:
        compiler = compiler_command()
        if compiler is None:
            self.skipTest("no host C compiler available")

        harness = textwrap.dedent(
            r"""
            #include <stdio.h>
            #include "resource_controller.c"

            #define CHECK(condition) do {                                      \
                if (!(condition)) {                                            \
                    fprintf(stderr, "check failed at line %d: %s\n",          \
                            __LINE__, #condition);                              \
                    return 1;                                                   \
                }                                                               \
            } while (0)

            static int configure_global(void)
            {
                struct resource_rate_profile profile = {
                    .burst = RESOURCE_RATE_LEASE_CAP + 1,
                    .refill = 1,
                };
                return resource_rate_global_configure(0, &profile);
            }

            static int reserve_one(struct resource_rate_lease_handle *handle)
            {
                struct resource_rate_endpoint endpoint = {
                    .scope = RESOURCE_RATE_GLOBAL,
                    .index = 0,
                    .amount = 1,
                };
                return resource_rate_reserve_many(&endpoint, 1, handle);
            }

            static int check_capacity_and_stale_handles(void)
            {
                struct resource_rate_lease_handle handles[RESOURCE_RATE_LEASE_CAP];
                struct resource_rate_lease_handle recycled[RESOURCE_RATE_LEASE_CAP];
                struct resource_rate_lease_handle overflow, replacement, stale;
                unsigned char seen[RESOURCE_RATE_LEASE_CAP + 1] = {0};
                uint64 applied = 0;
                uint victim = RESOURCE_RATE_LEASE_CAP / 2;

                resource_controller_init();
                CHECK(resource_rate_lease_allocator.free_count ==
                      RESOURCE_RATE_LEASE_CAP);
                for (uint i = 0; i < RESOURCE_RATE_LEASE_CAP; i++) {
                    CHECK(resource_rate_lease_allocator.free_ring[i] == i);
                    CHECK(resource_rate_leases[i].tag == RESOURCE_RATE_LEASE_FREE);
                    CHECK(resource_rate_leases[i].generation == 0);
                }
                CHECK(configure_global() == 0);
                for (uint i = 0; i < RESOURCE_RATE_LEASE_CAP; i++) {
                    CHECK(reserve_one(&handles[i]) == 0);
                    CHECK(handles[i].slot == i + 1);
                    CHECK(handles[i].generation == 1);
                }
                CHECK(resource_rate_lease_allocator.free_count == 0);
                overflow.slot = 99;
                overflow.generation = 99;
                CHECK(reserve_one(&overflow) < 0);
                CHECK(overflow.slot == 0 && overflow.generation == 0);

                stale = handles[victim];
                resource_rate_lease_cancel(stale);
                CHECK(!resource_rate_lease_valid(stale));
                CHECK(resource_rate_lease_allocator.free_count == 1);
                CHECK(reserve_one(&replacement) == 0);
                CHECK(replacement.slot == stale.slot);
                CHECK(replacement.generation == stale.generation + 1);
                CHECK(resource_rate_lease_commit(stale) < 0);
                resource_rate_lease_cancel(stale);
                CHECK(resource_rate_lease_valid(replacement));
                resource_rate_lease_cancel(replacement);
                for (uint i = 0; i < RESOURCE_RATE_LEASE_CAP; i++)
                    if (i != victim)
                        resource_rate_lease_cancel(handles[i]);
                CHECK(resource_rate_lease_allocator.free_count ==
                      RESOURCE_RATE_LEASE_CAP);

                CHECK(reserve_one(&replacement) == 0);
                CHECK(resource_rate_lease_commit(replacement) == 0);
                CHECK(!resource_rate_lease_valid(replacement));
                CHECK(resource_rate_lease_allocator.free_count ==
                      RESOURCE_RATE_LEASE_CAP);
                CHECK(resource_rate_global_refill(0, &applied) == 0);
                CHECK(applied == 1);

                for (uint i = 0; i < RESOURCE_RATE_LEASE_CAP; i++) {
                    CHECK(reserve_one(&recycled[i]) == 0);
                    CHECK(recycled[i].slot > 0 &&
                          recycled[i].slot <= RESOURCE_RATE_LEASE_CAP);
                    CHECK(!seen[recycled[i].slot]);
                    seen[recycled[i].slot] = 1;
                }
                CHECK(reserve_one(&overflow) < 0);
                for (uint i = 0; i < RESOURCE_RATE_LEASE_CAP; i++)
                    resource_rate_lease_cancel(recycled[i]);
                CHECK(resource_rate_lease_allocator.free_count ==
                      RESOURCE_RATE_LEASE_CAP);
                return 0;
            }

            static int check_generation_retirement(void)
            {
                struct resource_rate_lease_handle retired;
                struct resource_rate_lease_handle live[RESOURCE_RATE_LEASE_CAP];
                struct resource_rate_lease_handle overflow;

                resource_controller_init();
                CHECK(configure_global() == 0);
                resource_rate_leases[0].generation = (uint)-2;
                CHECK(reserve_one(&retired) == 0);
                CHECK(retired.slot == 1);
                CHECK(retired.generation == (uint)-1);
                resource_rate_lease_cancel(retired);
                CHECK(!resource_rate_lease_valid(retired));
                CHECK(resource_rate_leases[0].tag ==
                      RESOURCE_RATE_LEASE_RETIRED);
                CHECK(resource_rate_lease_allocator.free_count ==
                      RESOURCE_RATE_LEASE_CAP - 1);

                for (uint i = 0; i < RESOURCE_RATE_LEASE_CAP - 1; i++) {
                    CHECK(reserve_one(&live[i]) == 0);
                    CHECK(live[i].slot != retired.slot);
                }
                CHECK(reserve_one(&overflow) < 0);
                CHECK(resource_rate_lease_commit(retired) < 0);
                for (uint i = 0; i < RESOURCE_RATE_LEASE_CAP - 1; i++)
                    resource_rate_lease_cancel(live[i]);
                CHECK(resource_rate_lease_allocator.free_count ==
                      RESOURCE_RATE_LEASE_CAP - 1);
                CHECK(resource_rate_leases[0].tag ==
                      RESOURCE_RATE_LEASE_RETIRED);
                return 0;
            }

            int main(void)
            {
                CHECK(check_capacity_and_stale_handles() == 0);
                CHECK(check_generation_retirement() == 0);
                return 0;
            }
            """
        )
        types = textwrap.dedent(
            """
            #ifndef TYPES_H
            #define TYPES_H
            typedef unsigned int uint;
            typedef unsigned short ushort;
            typedef unsigned char uchar;
            typedef unsigned char uint8;
            typedef unsigned short uint16;
            typedef unsigned int uint32;
            typedef unsigned long long uint64;
            #endif
            """
        )
        defs = textwrap.dedent(
            """
            #ifndef DEFS_H
            #define DEFS_H
            #include <stdlib.h>
            #include <string.h>
            #define MIN(a, b) ((a) < (b) ? (a) : (b))
            static inline _Noreturn void panic(const char *message)
            {
                (void)message;
                abort();
            }
            #endif
            """
        )
        riscv = textwrap.dedent(
            """
            #ifndef RISCV_H
            #define RISCV_H
            static inline int intr_save(void) { return 1; }
            static inline void intr_restore(int enabled) { (void)enabled; }
            #endif
            """
        )

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            shutil.copy2(SOURCE, directory / "resource_controller.c")
            shutil.copy2(HEADER, directory / "resource_controller.h")
            (directory / "types.h").write_text(types, encoding="utf-8")
            (directory / "defs.h").write_text(defs, encoding="utf-8")
            (directory / "riscv.h").write_text(riscv, encoding="utf-8")
            harness_path = directory / "harness.c"
            harness_path.write_text(harness, encoding="utf-8")
            executable = directory / ("harness.exe" if os.name == "nt" else "harness")
            compile_result = subprocess.run(
                compiler + [
                    "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    str(harness_path), "-o", str(executable),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)], text=True, capture_output=True, check=False
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)


if __name__ == "__main__":
    unittest.main()
