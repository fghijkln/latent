#!/usr/bin/env python3
"""Focused regression tests for the Python-target projection ledger checker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checker
import project_stage0


class ProjectionLedgerTests(unittest.TestCase):
    def test_unallowlisted_parent_variables_are_not_forwarded(self):
        with patch.dict(os.environ, {"P15_SECRET_SENTINEL": "not-for-child"}):
            environment = project_stage0.clean_test_environment()
        self.assertNotIn("P15_SECRET_SENTINEL", environment)
        self.assertIn("PATH", environment)
        self.assertEqual(environment["PYTHONPATH"], "")

    def test_projection_subprocess_cannot_read_parent_sentinel(self):
        with patch.dict(os.environ, {"P15_SECRET_SENTINEL": "not-for-child"}):
            result = project_stage0.command_result(
                [sys.executable, "-c",
                 "import os; print('P15_SECRET_SENTINEL' in os.environ)"],
                cwd=HERE)
        self.assertEqual(result, {
            "rc": 0, "stdout": "False\n", "stderr": ""})

    def test_full_runner_does_not_forward_parent_sentinel(self):
        with patch.dict(os.environ, {"P15_SECRET_SENTINEL": "not-for-child"}):
            result = project_stage0.runner.run(
                [sys.executable, "-c",
                 "import os; print('P15_SECRET_SENTINEL' in os.environ)"],
                cwd=HERE)
        self.assertEqual(result, (0, "False\n", ""))

    def test_property_runner_does_not_forward_parent_sentinel(self):
        with patch.dict(os.environ, {"P15_SECRET_SENTINEL": "not-for-child"}):
            result = project_stage0.properties._run(
                [sys.executable, "-c",
                 "import os; print('P15_SECRET_SENTINEL' in os.environ)"],
                str(project_stage0.ROOT))
        self.assertEqual(result, (0, "False\n", ""))

    def test_direct_test_modules_clear_parent_sentinel(self):
        environment = project_stage0.clean_test_environment(
            {"P15_SECRET_SENTINEL": "not-for-child"})
        for module_name in ("tests.test_editor_metadata",
                            "tests.test_p14_protocol"):
            with self.subTest(module=module_name):
                probe = (
                    "import importlib, os; "
                    f"importlib.import_module({module_name!r}); "
                    "print('P15_SECRET_SENTINEL' in os.environ)")
                result = subprocess.run(
                    [sys.executable, "-c", probe],
                    cwd=project_stage0.ROOT, env=environment,
                    text=True, encoding="utf-8", capture_output=True,
                    check=False)
                self.assertEqual(
                    (result.returncode, result.stdout, result.stderr),
                    (0, "False\n", ""))

    def test_smoke_runner_captures_ci_report_path_without_forwarding_it(self):
        report_path = "/tmp/p15-report-path-sentinel"
        environment = project_stage0.clean_test_environment({
            "P15_BOOTSTRAP_MANIFEST_REPORT": report_path})
        probe = (
            "import os; import tests.test_bootstrap_smoke as smoke; "
            "print(smoke.BOOTSTRAP_MANIFEST_REPORT); "
            "print('P15_BOOTSTRAP_MANIFEST_REPORT' in os.environ)"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=project_stage0.ROOT,
            env=environment, text=True, encoding="utf-8",
            capture_output=True, check=False)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, report_path + "\nFalse\n", ""))

    def test_explicit_empty_subprocess_environment_stays_empty(self):
        result = project_stage0.command_result(
            [sys.executable, "-c", "import os; print('PATH' in os.environ)"],
            cwd=HERE, env={})
        self.assertEqual(result, {
            "rc": 0, "stdout": "False\n", "stderr": ""})

    def test_projection_timeout_preserves_partial_text_output(self):
        script = (
            "import sys, time; "
            "print('started', flush=True); "
            "print('warning', file=sys.stderr, flush=True); "
            "time.sleep(8)"
        )
        result = project_stage0.command_result(
            [sys.executable, "-c", script], cwd=HERE, timeout=2)
        self.assertEqual(result, {
            "rc": "timeout", "stdout": "started\n", "stderr": "warning\n"})

    def test_full_runner_timeout_preserves_partial_text_output(self):
        script = (
            "import sys, time; "
            "print('started', flush=True); "
            "print('warning', file=sys.stderr, flush=True); "
            "time.sleep(8)"
        )
        result = project_stage0.runner.run(
            [sys.executable, "-c", script], cwd=HERE, timeout=2)
        self.assertEqual(result, ("timeout", "started\n", "warning\n"))

    def test_projection_spawn_error_is_structured(self):
        missing = HERE / "missing-command-for-projection-test"
        self.assertFalse(missing.exists())
        result = project_stage0.command_result([str(missing)], cwd=HERE)
        self.assertEqual(result["rc"], "error")
        self.assertEqual(result["stdout"], "")
        self.assertTrue(result["stderr"])

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
