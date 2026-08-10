#!/usr/bin/env python3
"""Focused mutations for the traditional syscall class-selected route."""

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-traditional-io-fastpath.py")
ROOT = SCRIPT.parent.parent
SPEC = importlib.util.spec_from_file_location("traditional_io_fastpath", SCRIPT)
traditional_io_fastpath = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(traditional_io_fastpath)

SYSCALL = traditional_io_fastpath.compact(ROOT / "os" / "syscall.c")
ROUTE = traditional_io_fastpath.function(SYSCALL, "syscall")


class TraditionalIoFastpathTests(unittest.TestCase):
    def assert_route_rejected(self, route: str) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "registered syscall can bypass its class-selected dispatch path",
        ):
            traditional_io_fastpath.check_class_selected_dispatch(route)

    def test_current_route_is_class_selected(self) -> None:
        traditional_io_fastpath.check_class_selected_dispatch(ROUTE)

    def test_missing_direct_gate_is_rejected(self) -> None:
        anchor = (
            "ret=agent_execution_contract_gate_direct_syscall("
            "curr_proc(),id,direct_side_effects,&direct_guard);"
        )
        self.assertIn(anchor, ROUTE)
        self.assert_route_rejected(ROUTE.replace(anchor, "ret=0;", 1))

    def test_missing_ingress_merge_is_rejected(self) -> None:
        anchor = (
            "if(syscall_merge_ingress_provenance(id,0)<0){"
            "ret=-1;operation_denied=1;gotooperation_done;}"
        )
        self.assertIn(anchor, ROUTE)
        self.assert_route_rejected(ROUTE.replace(anchor, "", 1))

    def test_dispatch_before_ingress_is_rejected(self) -> None:
        ingress = (
            "if(syscall_merge_ingress_provenance(id,0)<0){"
            "ret=-1;operation_denied=1;gotooperation_done;}"
        )
        dispatch = "ret=syscall_dispatch(id,trapframe,0);"
        self.assertIn(ingress + dispatch, ROUTE)
        mutated = ROUTE.replace(
            ingress + dispatch, dispatch + ingress, 1
        )
        self.assert_route_rejected(mutated)

    def test_unconditional_direct_dispatch_is_rejected(self) -> None:
        condition = "if(syscall_needs_transaction(class))"
        operation_done = "operation_done:"
        start = ROUTE.index(condition)
        end = ROUTE.index(operation_done, start)
        mutated = (
            ROUTE[:start]
            + "ret=syscall_dispatch(id,trapframe,0);"
            + ROUTE[end:]
        )
        self.assert_route_rejected(mutated)

    def test_duplicate_fast_dispatch_is_rejected(self) -> None:
        condition = "if(syscall_needs_transaction(class))"
        self.assertIn(condition, ROUTE)
        mutated = ROUTE.replace(
            condition,
            "ret=syscall_dispatch(id,trapframe,0);" + condition,
            1,
        )
        self.assert_route_rejected(mutated)


if __name__ == "__main__":
    unittest.main()
