#!/usr/bin/env python3
"""No-QEMU checks for the compiled observation layout contract."""

from __future__ import annotations

import importlib.util
import json
import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check-agent-observe-disk-format.py"
CONTRACT = ROOT / "ci" / "agent-observe-disk-format.json"
PROBE = ROOT / "scripts" / "probes" / "agent-observe-disk-layout.c"
spec = importlib.util.spec_from_file_location("agent_observe_layout_checker", CHECKER)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class ObservationLayoutContractTests(unittest.TestCase):
    def test_contract_and_probe_are_versioned(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        source = PROBE.read_text(encoding="utf-8")
        self.assertEqual(contract["schema"], 2)
        self.assertEqual(contract["descriptor"]["version"], 2)
        self.assertEqual(contract["observation"]["version"], 7)
        self.assertIn("LAYOUT_DESCRIPTOR_VERSION 2U", source)
        self.assertIn(".agent_observe_layout", source)

    def test_descriptor_rejects_short_magic_and_version_mutations(self) -> None:
        raw = bytearray(module.WORDS.size)
        struct.pack_into("<QQQ", raw, 0, module.DESCRIPTOR_MAGIC, module.DESCRIPTOR_VERSION, len(raw))
        for label, mutation in (
            ("short", bytes(raw[:-8])),
            ("magic", bytes(bytearray(raw[:1]) + raw[1:])),
        ):
            if label == "magic":
                changed = bytearray(raw)
                changed[0] ^= 1
                mutation = bytes(changed)
            with self.subTest(label=label), self.assertRaises(module.ProbeError):
                module.descriptor_contract(mutation)
        changed = bytearray(raw)
        struct.pack_into("<Q", changed, 8, module.DESCRIPTOR_VERSION + 1)
        with self.assertRaises(module.ProbeError):
            module.descriptor_contract(bytes(changed))


if __name__ == "__main__":
    unittest.main()
