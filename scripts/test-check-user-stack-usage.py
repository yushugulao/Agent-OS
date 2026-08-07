#!/usr/bin/env python3
"""闭锁式用户栈调用路径检查器的变异测试。"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CHECKER = Path(__file__).with_name("check-user-stack-usage.py")
LIBRARY = "lib/runtime.c"
APPLICATION = "src/app.c"


class UserStackUsageCheckerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sources = self.root / "user"
        self.usage = self.root / "usage"
        self.contract = self.root / "user_stack_policy.h"
        self.write_contract()
        for unit in (LIBRARY, APPLICATION):
            source = self.sources / unit
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("int placeholder;\n", encoding="utf-8")
        self.write_usage(
            "lib/runtime.su",
            "lib/runtime.c:1:1:runtime\t64\tstatic\n"
            "lib/runtime.c:2:1:__start_main\t32\tstatic\n",
        )
        self.write_usage(
            "src/app.su",
            "src/app.c:2:1:main\t128\tstatic\n"
            "src/app.c:3:1:helper\t256\tstatic\n",
        )
        self.write_graph(
            "lib/runtime.c",
            [
                ("runtime", "runtime", 64, "static"),
                ("__start_main", "__start_main", 32, "static"),
                ("main", "main", None, None),
            ],
            [("__start_main", "main")],
        )
        self.write_graph(
            "src/app.c",
            [
                ("main", "main", 128, "static"),
                ("src/app.c:helper", "helper", 256, "static"),
                ("runtime", "runtime", None, None),
            ],
            [("main", "src/app.c:helper"), ("src/app.c:helper", "runtime")],
        )

    def tearDown(self):
        self.temporary.cleanup()

    def write_usage(self, relative, contents):
        path = self.usage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return path

    def write_contract(self, stack=4096, argv=1024, call_path=3072):
        self.contract.write_text(
            "#define USER_STACK_SIZE_BYTES " + str(stack) + "ULL\n"
            "#define USER_STACK_ARGV_LAYOUT_BYTES " + str(argv) + "ULL\n"
            "#define USER_STACK_CALL_PATH_BYTES " + str(call_path) + "ULL\n",
            encoding="utf-8",
        )

    def write_graph(self, unit, nodes, edges):
        lines = [f'graph: {{ title: "{unit}"']
        for title, name, size, kind in nodes:
            if size is None:
                lines.append(
                    f'node: {{ title: "{title}" label: "{name}\\nheader.h:1:1" '
                    "shape : ellipse }"
                )
            else:
                lines.append(
                    f'node: {{ title: "{title}" label: "{name}\\n{unit}:1:1\\n'
                    f'{size} bytes ({kind})" }}'
                )
        for source, target in edges:
            lines.append(
                f'edge: {{ sourcename: "{source}" targetname: "{target}" }}'
            )
        lines.append("}")
        return self.write_usage(Path(unit).with_suffix(".ci"), "\n".join(lines) + "\n")

    def run_checker(self, *extra):
        command = [
            sys.executable,
            str(CHECKER),
            "--usage-dir",
            str(self.usage),
            "--source-dir",
            str(self.sources),
            "--contract-header",
            str(self.contract),
            "--library-unit",
            LIBRARY,
            "--application-unit",
            APPLICATION,
        ]
        command.extend(extra)
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def assert_rejected(self, needle, *extra):
        result = self.run_checker(*extra)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(needle, result.stderr)

    def test_accepts_complete_static_callgraph(self):
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("max=480", result.stdout)
        self.assertIn("reserve=1024", result.stdout)

    def test_accepts_compiler_marked_fully_inlined_node_without_double_charge(self):
        path = self.usage / "src/app.ci"
        graph = path.read_text(encoding="utf-8")
        graph = graph.replace(
            'edge: { sourcename: "main" targetname: "src/app.c:helper" }',
            'node: { title: "src/app.c:inlined" '
            'label: "inlined\\nsrc/app.c:4:1" shape : triangle }\n'
            'edge: { sourcename: "main" targetname: "src/app.c:inlined" }\n'
            'edge: { sourcename: "src/app.c:inlined" '
            'targetname: "src/app.c:helper" }',
        )
        path.write_text(graph, encoding="utf-8")
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("max=480", result.stdout)

    def test_mutation_unbound_triangle_node_is_rejected(self):
        path = self.usage / "src/app.ci"
        graph = path.read_text(encoding="utf-8")
        graph = graph.replace(
            'edge: { sourcename: "main" targetname: "src/app.c:helper" }',
            'node: { title: "other.c:inlined" '
            'label: "inlined\\nother.c:4:1" shape : triangle }\n'
            'edge: { sourcename: "main" targetname: "other.c:inlined" }',
        )
        path.write_text(graph, encoding="utf-8")
        self.assert_rejected("invalid inlined callgraph node")

    def test_mutation_contract_drift_is_rejected(self):
        self.write_contract(argv=1008, call_path=3088)
        self.assert_rejected("user stack contract drift")

    def test_mutation_one_byte_over_frame_budget_is_rejected(self):
        self.write_usage(
            "src/app.su",
            "src/app.c:2:1:main\t128\tstatic\n"
            "src/app.c:3:1:helper\t3073\tstatic\n",
        )
        self.assert_rejected("frame budget exceeded")

    def test_mutation_small_frames_with_oversized_path_are_rejected(self):
        self.write_usage(
            "lib/runtime.su",
            "lib/runtime.c:1:1:runtime\t800\tstatic\n"
            "lib/runtime.c:2:1:__start_main\t32\tstatic\n",
        )
        self.write_usage(
            "src/app.su",
            "src/app.c:2:1:main\t1400\tstatic\n"
            "src/app.c:3:1:helper\t1000\tstatic\n",
        )
        self.write_graph(
            "lib/runtime.c",
            [
                ("runtime", "runtime", 800, "static"),
                ("__start_main", "__start_main", 32, "static"),
                ("main", "main", None, None),
            ],
            [("__start_main", "main")],
        )
        self.write_graph(
            "src/app.c",
            [
                ("main", "main", 1400, "static"),
                ("src/app.c:helper", "helper", 1000, "static"),
                ("runtime", "runtime", None, None),
            ],
            [("main", "src/app.c:helper"), ("src/app.c:helper", "runtime")],
        )
        self.assert_rejected("call-path budget exceeded")

    def test_mutation_dynamic_frame_is_rejected(self):
        self.write_usage(
            "src/app.su",
            "src/app.c:2:1:main\t128\tstatic\n"
            "src/app.c:3:1:helper\t256\tdynamic,bounded\n",
        )
        self.assert_rejected("unbounded user stack usage")

    def test_mutation_missing_or_stale_artifact_is_rejected(self):
        (self.usage / "src/app.ci").unlink()
        self.assert_rejected("missing: src/app.ci")
        self.write_graph(
            "src/app.c",
            [("main", "main", 128, "static"), ("src/app.c:helper", "helper", 256, "static")],
            [("main", "src/app.c:helper")],
        )
        self.write_usage("src/stale.su", "src/stale.c:1:1:stale\t16\tstatic\n")
        self.assert_rejected("stale: src/stale.su")

    def test_mutation_malformed_record_is_rejected(self):
        self.write_usage("src/app.su", "not-a-gcc-stack-record\n")
        self.assert_rejected("unsupported stack-usage record")

    def test_mutation_stack_usage_callgraph_mismatch_is_rejected(self):
        self.write_usage(
            "src/app.su",
            "src/app.c:2:1:main\t128\tstatic\n"
            "src/app.c:3:1:helper\t240\tstatic\n",
        )
        self.assert_rejected("stack-usage/callgraph mismatch")

    def test_mutation_missing_startup_edge_is_rejected(self):
        self.write_graph(
            "lib/runtime.c",
            [
                ("runtime", "runtime", 64, "static"),
                ("__start_main", "__start_main", 32, "static"),
                ("main", "main", None, None),
            ],
            [],
        )
        self.assert_rejected("startup chain does not reach")

    def test_mutation_unresolved_call_is_rejected_or_explicitly_audited(self):
        self.write_graph(
            "src/app.c",
            [
                ("main", "main", 128, "static"),
                ("src/app.c:helper", "helper", 256, "static"),
                ("mystery_leaf", "mystery_leaf", None, None),
            ],
            [("main", "src/app.c:helper"), ("src/app.c:helper", "mystery_leaf")],
        )
        self.assert_rejected("unresolved mystery_leaf")
        result = self.run_checker("--allow-unresolved", "mystery_leaf")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mutation_indirect_call_is_rejected(self):
        self.write_graph(
            "src/app.c",
            [
                ("main", "main", 128, "static"),
                ("src/app.c:helper", "helper", 256, "static"),
                ("__indirect_call", "__indirect_call", None, None),
            ],
            [("main", "src/app.c:helper"), ("src/app.c:helper", "__indirect_call")],
        )
        self.assert_rejected("indirect call from helper")
        result = self.run_checker(
            "--indirect-call-edge", "src/app.c:helper=runtime"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stale_indirect_call_declaration_is_rejected(self):
        self.assert_rejected(
            "unused indirect-call declarations",
            "--indirect-call-edge",
            "src/app.c:helper=runtime",
        )

    def test_mutation_recursion_requires_and_consumes_a_bound(self):
        self.write_graph(
            "src/app.c",
            [
                ("main", "main", 128, "static"),
                ("src/app.c:helper", "helper", 256, "static"),
                ("runtime", "runtime", None, None),
            ],
            [
                ("main", "src/app.c:helper"),
                ("src/app.c:helper", "src/app.c:helper"),
                ("src/app.c:helper", "runtime"),
            ],
        )
        self.assert_rejected("recursion has no audited bound")
        result = self.run_checker(
            "--recursion-bound", "src/app.c:helper=2"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_application_inventory_is_rejected(self):
        command = [
            sys.executable,
            str(CHECKER),
            "--usage-dir",
            str(self.usage),
            "--source-dir",
            str(self.sources),
            "--contract-header",
            str(self.contract),
            "--library-unit",
            LIBRARY,
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("application unit inventory is empty", result.stderr)

    def test_each_application_is_linked_with_libraries_independently(self):
        other = self.sources / "src/other.c"
        other.write_text("int placeholder;\n", encoding="utf-8")
        self.write_usage("src/other.su", "src/other.c:1:1:main\t96\tstatic\n")
        self.write_graph(
            "src/other.c",
            [
                ("main", "main", 96, "static"),
                ("runtime", "runtime", None, None),
            ],
            [("main", "runtime")],
        )
        result = self.run_checker("--application-unit", "src/other.c")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("apps=2", result.stdout)


if __name__ == "__main__":
    unittest.main()
