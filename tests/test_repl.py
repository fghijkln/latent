#!/usr/bin/env python3
"""CLI regression tests for the Latent Python-backed REPL."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests._safe_test_env import clean_test_environment, isolate_test_environment

isolate_test_environment()


class ReplCliTests(unittest.TestCase):
    def invoke(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "latent.py"), "repl"],
            input=source,
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=ROOT,
            env=clean_test_environment(),
            check=False,
            timeout=30,
        )

    def test_cli_starts_prints_banner_and_quits_cleanly(self):
        result = self.invoke(":quit\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Latent REPL (python backend).", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_expression_definition_and_reset(self):
        result = self.invoke(
            "fn twice(value) => value * 2\n"
            "twice(11)\n"
            ":reset\n"
            "twice(11)\n"
            ":quit\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("22\n", result.stdout)
        self.assertIn("(state cleared)", result.stdout)
        self.assertIn("semant error:", result.stderr)
        self.assertIn("twice", result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_parse_error_reports_and_repl_continues(self):
        result = self.invoke(
            "fn broken(:\n"
            "\n"
            "6 * 7\n"
            ":quit\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<repl>:", result.stderr)
        self.assertIn("parse error:", result.stderr)
        self.assertIn("42\n", result.stdout)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_runtime_names_are_isolated_across_chunks_and_reset(self):
        result = self.invoke(
            'fn _wv_non_handle_type(value) => nil\n'
            'type = 13\n'
            'isinstance = 17\n'
            '_lt_runtime = 19\n'
            '_lt_host_builtins = 23\n'
            '__builtins__ = 29\n'
            'if false:\n    object = nil\n    _os = nil\n\n'
            'say [type, isinstance, object, _os]\n'
            'try:\n    say "".__class__\n'
            'catch error:\n    say error\n\n'
            'try:\n    say object.__class__\n'
            'catch error:\n    say error\n\n'
            ':reset\n'
            'say [1, 2]\n'
            ':quit\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn('[13, 17, nil, nil]\n', result.stdout)
        self.assertIn('attribute access on non-handle value: str', result.stdout)
        self.assertIn('attribute access on non-handle value: nil', result.stdout)
        self.assertIn('[1, 2]\n', result.stdout)

    def test_runtime_error_in_earlier_definition_is_mapped_and_repl_recovers(self):
        result = self.invoke(
            'fn broken(value):\n    return value + "bad"\n\n'
            '_wv_report_uncaught = 1\n'
            '_WV_SOURCE_MAP = 2\n'
            'broken(1)\n'
            'say "recovered"\n'
            ':quit\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Latent runtime error: TypeError: bad + operands: 1, bad',
                      result.stderr)
        self.assertIn('at <repl>:2 in broken', result.stderr)
        self.assertNotIn('Traceback (most recent call last)', result.stderr)
        self.assertNotIn('_lt_user_', result.stderr)
        self.assertIn('recovered\n', result.stdout)


if __name__ == "__main__":
    unittest.main()
