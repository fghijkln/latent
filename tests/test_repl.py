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


if __name__ == "__main__":
    unittest.main()
