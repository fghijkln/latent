#!/usr/bin/env python3
"""Focused regression tests for the Python-target projection ledger checker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checker
import pending_snapshot_gate
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

    def test_p18_delta_pins_old_stage0_rejection_and_stage1_stage2_trace(self):
        delta = {
            "fixed_stage0": {
                "phase": "run", "rc": 1, "stdout": "",
                "stderr_contains": "Latent callable values cannot be passed",
                "output_exists": True,
            },
            "stage1_stage2": {
                "phase": "run", "rc": 1, "stdout": "",
                "stderr": ("Latent runtime error: TypeError: callbacks cannot return "
                           "Latent callable values\n"
                           "Latent traceback (most recent call last):\n"
                           "  at p14_negative/module_callback.lt:4 in main\n"),
                "output_exists": True,
            },
        }
        stage1_stderr = delta["stage1_stage2"]["stderr"]
        results = {
            "stage0": {"phase": "run", "rc": 1, "stdout": "",
                       "stderr": "Latent runtime error: TypeError: "
                                 "Latent callable values cannot be passed to "
                                 "Python/Java interop calls", "output_exists": True},
            "stage1": {"phase": "run", "rc": 1, "stdout": "",
                       "stderr": stage1_stderr, "output_exists": True},
            "stage2": {"phase": "run", "rc": 1, "stdout": "",
                       "stderr": stage1_stderr, "output_exists": True},
        }
        self.assertEqual(project_stage0.behavior_delta_failures(
            "p14_negative/module_callback", delta, results, HERE,
            project_stage0.TESTS), [])
        results["stage0"]["rc"] = 0
        results["stage2"]["stderr"] = "different\n"
        failures = project_stage0.behavior_delta_failures(
            "p14_negative/module_callback", delta, results, HERE,
            project_stage0.TESTS)
        self.assertTrue(any("stage0 P18 delta exit status" in item
                            for item in failures))
        self.assertIn("Stage1 and Stage2 P18 delta behavior differs", failures)

    def test_p18_feature_contract_is_exact_and_inventory_is_complete(self):
        manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
        feature = manifest["p18_stage1_feature"]
        self.assertEqual(feature["source"], "tests/p18_callbacks.lt")
        self.assertEqual(feature["expected"]["phase"], "run")
        self.assertEqual(feature["expected"]["rc"], 0)
        self.assertEqual(
            feature["expected"]["stdout"],
            "#1:a #2:bb\n#3:c\n#4:dd\nconversion-ok\none!\n2\n"
            "x!xx!x\n32\ncompiled!\nextracted!\n"
            "positional:star-arg\npositional:partial\n"
            "default! | one! | two?\n0,1,2\n")
        self.assertEqual(feature["expected"]["stderr"], "")
        self.assertIn("test_p18_callbacks.py", manifest["runner_auxiliary_paths"])
        self.assertIn("test_p18_callbacks.py", manifest["inventories"]["test_module"])
        self.assertIn("p18_callbacks.lt", manifest["inventories"]["tests_lt"])
        self.assertIn("p18_callbacks_host.py", manifest["inventories"]["python_fixture"])

    def test_p18_stage1_inventory_pins_callback_boundaries_and_host_star_cases(self):
        manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
        cases = {case["name"]: case for case in manifest["p18_stage1_cases"]}
        self.assertEqual(set(cases), {
            "p18_host_error_caught", "p18_deferred_host_result",
            "p18_async_host_result", "p18_callback_returns_deferred",
            "p18_callback_returns_callable", "p18_host_star_argument",
            "p18_host_starstar_argument", "p18_callback_wrong_arity"})
        self.assertIn("save_then_fail", cases["p18_host_error_caught"]["source"])
        self.assertIn("catch error", cases["p18_host_error_caught"]["source"])
        self.assertIn("async or deferred results",
                      cases["p18_callback_returns_deferred"]["expected"]["stderr"])
        self.assertIn("callable values",
                      cases["p18_callback_returns_callable"]["expected"]["stderr"])
        self.assertIn("*[]", cases["p18_host_star_argument"]["source"])
        self.assertIn("**{}", cases["p18_host_starstar_argument"]["source"])
        wrong_arity = cases["p18_callback_wrong_arity"]
        self.assertIn("host.invoke_two(callback)", wrong_arity["source"])
        self.assertEqual(wrong_arity["expected"]["stderr"],
                         "Latent runtime error: ArgumentError: callback() takes 1 args, got 2\n"
                         "Latent traceback (most recent call last):\n"
                         "  at p18_callback_wrong_arity.lt:4 in main\n")

    def test_java_host_callback_cases_remain_runtime_negative_and_delta_is_narrow(self):
        manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
        references = {name: list(items)
                      for name, items in manifest["runner_references"].items()}
        references["NEG_RUNTIME"].remove("p14_negative/static_callback")
        errors = checker.compare(
            manifest, references, manifest["inventories"],
            manifest["runner_auxiliary_paths"],
            list(manifest["p14_protocol_operations"]), root=checker.ROOT)
        self.assertTrue(any("callback boundary must remain in NEG_RUNTIME: "
                            "p14_negative/static_callback" in error
                            for error in errors))

        references["NEG_RUNTIME"].append("p14_negative/static_callback")
        references["POSITIVE"].append("p14_negative/static_callback")
        errors = checker.compare(
            manifest, references, manifest["inventories"],
            manifest["runner_auxiliary_paths"],
            list(manifest["p14_protocol_operations"]), root=checker.ROOT)
        self.assertTrue(any("must remain a runtime-negative Java-host case" in error
                            for error in errors))

        broadened = json.loads(json.dumps(manifest))
        broadened["p18_stage0_deltas"]["p14_negative/static_callback"] = \
            broadened["p18_stage0_deltas"]["p14_negative/module_callback"]
        errors = checker.compare(
            broadened, manifest["runner_references"], manifest["inventories"],
            manifest["runner_auxiliary_paths"],
            list(manifest["p14_protocol_operations"]), root=checker.ROOT)
        self.assertIn(
            "P18 Stage0 behavior delta is not permitted: p14_negative/static_callback",
            errors)

    def test_pending_snapshot_excludes_only_root_metadata_and_ignored_build_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="pending-snapshot-test-") as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitignore").write_text(
                "out/\n__pycache__/\n*.pyc\n*.class\n", encoding="utf-8")
            (root / "cache").mkdir()
            (root / "cache" / "tracked.py").write_text("tracked\n", encoding="utf-8")
            (root / "cache" / "pending.py").write_text("pending\n", encoding="utf-8")
            (root / "out").mkdir()
            (root / "out" / "tracked.lt").write_text("tracked output path\n", encoding="utf-8")
            (root / "out" / "generated.lt").write_text("generated\n", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "generated.pyc").write_bytes(b"bytecode")
            (root / "generated.class").write_bytes(b"class")
            nested_git = root / "nested" / ".git"
            nested_git.mkdir(parents=True)
            (nested_git / "ordinary-source").write_text("keep\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "cache/tracked.py"],
                           check=True)
            subprocess.run(["git", "-C", str(root), "add", "-f", "out/tracked.lt"],
                           check=True)
            tracked = pending_snapshot_gate.git_tracked_paths(root)
            expected = pending_snapshot_gate.source_entries(root, tracked)
            snapshot = Path(temporary) / "snapshot"
            pending_snapshot_gate.copy_pending_source(root, snapshot, tracked)
            observed = pending_snapshot_gate.source_entries(snapshot, tracked)
            self.assertEqual(expected, observed)
            for included in ("cache/tracked.py", "cache/pending.py",
                             "out/tracked.lt", "nested/.git/ordinary-source"):
                self.assertIn(included, observed)
            for excluded in (".git", "out/generated.lt",
                             "__pycache__/generated.pyc", "generated.class"):
                self.assertNotIn(excluded, observed)

    def test_current_repository_inventory_is_classified(self):
        self.assertEqual(checker.audit(), [])
        pending = pending_snapshot_gate.source_entries(checker.ROOT)
        for path in ("tests/bootstrap/pending_snapshot_gate.py",
                     "tests/bootstrap/manifest.json",
                     "tests/p18_callbacks.lt",
                     "tests/python/p18_callbacks_host.py",
                     "tests/test_p18_callbacks.py"):
            with self.subTest(path=path):
                self.assertIn(path, pending)

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
