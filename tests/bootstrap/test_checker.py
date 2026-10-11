#!/usr/bin/env python3
"""Focused regression tests for the Python-target projection ledger checker."""
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checker


class ProjectionLedgerTests(unittest.TestCase):
    def test_current_repository_inventory_is_classified(self):
        self.assertEqual(checker.audit(), [])

    def test_synthetic_new_runner_case_is_rejected(self):
        errors = checker.audit(injected_refs={"POSITIVE": ["synthetic/new_case"]})
        self.assertIn(
            "unclassified runner item POSITIVE: synthetic/new_case", errors)
        self.assertIn(
            "runner case has no Python-target decision: synthetic/new_case", errors)

    def test_every_current_runner_category_is_recorded_in_manifest(self):
        manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("POSITIVE", manifest["runner_references"])
        self.assertIn("MODULE_POSITIVE", manifest["runner_references"])
        for category in ("NEG_RUNTIME", "COOKBOOK", "P8_NEG_COMPILE",
                         "P9_NEG_COMPILE", "P10_NEG_COMPILE",
                         "P11_NEG_COMPILE", "P12_NEG_COMPILE",
                         "NEG_INHERITANCE_COMPILE", "NEG_MODULE_COMPILE"):
            self.assertIn(category, manifest["runner_references"])
        self.assertTrue(any(name.startswith("p14_negative/")
                            for name in manifest["runner_cases"]))
        self.assertIn("call_module_function",
                      manifest["p14_protocol_operations"])
        self.assertIn("call_static_method",
                      manifest["p14_protocol_operations"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
