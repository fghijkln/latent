#!/usr/bin/env python3
"""Unit and CLI regression tests for read-only AST diagnostics."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import latent

SAMPLE = os.path.join(ROOT, "tests", "diagnostics_sample.lt")


def _nodes(value):
    if isinstance(value, dict):
        if "node" in value:
            yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


class DiagnosticTests(unittest.TestCase):
    def invoke(self, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", ["latent.py", *args]):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    result = latent.main()
                except SystemExit as exc:
                    result = exc.code
        return result, stdout.getvalue(), stderr.getvalue()

    def test_show_ast_has_attributes_control_flow_and_positions(self):
        result, output, error = self.invoke(SAMPLE, "--show-ast")
        self.assertEqual((result, error), (0, ""))
        document = json.loads(output)
        self.assertEqual(document["format"], "latent-ast")
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["phase"], "parsed")
        self.assertEqual(document["source"], os.path.realpath(SAMPLE))
        node_types = {node["node"] for node in _nodes(document["tree"])}
        self.assertTrue({"SetAttr", "Dot", "If", "While"} <= node_types)
        for node in _nodes(document["tree"]):
            if node["node"] == "Program":
                self.assertEqual(node["position"], {"line": None, "column": None})
                continue
            position = node["position"]
            self.assertIsInstance(position["line"], int)
            self.assertGreater(position["line"], 0)
            self.assertIsInstance(position["column"], int)
            self.assertGreater(position["column"], 0)

    def test_show_desugar_rewrites_nodes_and_preserves_positions(self):
        result, output, error = self.invoke(SAMPLE, "--show-desugar")
        self.assertEqual((result, error), (0, ""))
        document = json.loads(output)
        self.assertEqual(document["phase"], "desugared")
        nodes_in_tree = list(_nodes(document["tree"]))
        node_types = {node["node"] for node in nodes_in_tree}
        self.assertNotIn("SetAttr", node_types)
        self.assertNotIn("Dot", node_types)
        identifiers = {
            node["fields"].get("identifier") for node in nodes_in_tree
            if node["node"] == "Name"
        }
        self.assertTrue({"__wsetattr", "__wgetattr", "__say"} <= identifiers)
        for node in nodes_in_tree:
            if node["node"] != "Program":
                self.assertGreater(node["position"]["line"], 0)
                self.assertGreater(node["position"]["column"], 0)

    def test_output_is_stable_read_only_and_never_launches_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp) / "existing-output"
            outdir.mkdir()
            sentinel = outdir / "existing.generated"
            sentinel.write_text("keep this artifact\n", encoding="utf-8")
            before = {p.name: p.read_bytes() for p in outdir.iterdir()}
            missing_outdir = Path(tmp) / "must-not-be-created"

            with mock.patch.object(
                    latent.subprocess, "run",
                    side_effect=AssertionError("diagnostics must not spawn a process")) as run:
                first = self.invoke(SAMPLE, "--show-ast", "-o", str(outdir))
                second = self.invoke(SAMPLE, "--show-ast", "-o", str(outdir))
                third = self.invoke(SAMPLE, "--show-desugar", "-o",
                                    str(missing_outdir), "-t", "java")

            self.assertEqual(run.call_count, 0)
            for result, output, error in (first, second, third):
                self.assertEqual((result, error), (0, ""))
                self.assertTrue(output.endswith("\n"))
            self.assertEqual(first[1], second[1])
            self.assertEqual(before, {p.name: p.read_bytes() for p in outdir.iterdir()})
            self.assertFalse(missing_outdir.exists())
            self.assertNotIn("runtime.ltpy", sys.modules)

    def test_syntax_error_uses_structured_compile_error_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "broken.lt"
            source.write_text("say 1 +\n", encoding="utf-8")
            result, output, error = self.invoke(str(source), "--show-ast")
        self.assertEqual(result, 1)
        self.assertEqual(output, "")
        self.assertIn(f"{source}:1:", error)
        self.assertIn("parse error:", error)
        self.assertIn("1 | say 1 +", error)
        self.assertIn("^", error)

    def test_desugar_error_uses_structured_compile_error_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "bad_desugar.lt"
            source.write_text("say super.value\n", encoding="utf-8")
            result, output, error = self.invoke(str(source), "--show-desugar")
        self.assertEqual(result, 1)
        self.assertEqual(output, "")
        self.assertIn(f"{source}:1:", error)
        self.assertIn("desugar error:", error)
        self.assertIn("^", error)

    def test_diagnostic_flags_are_mutually_exclusive(self):
        result, output, error = self.invoke(
            SAMPLE, "--show-ast", "--show-desugar")
        self.assertEqual(result, 2)
        self.assertEqual(output, "")
        self.assertIn("not allowed with argument", error)

    def test_diagnostics_cannot_be_combined_with_run(self):
        result, output, error = self.invoke(SAMPLE, "--show-ast", "--run")
        self.assertEqual(result, 2)
        self.assertEqual(output, "")
        self.assertIn("cannot be combined with --run", error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
