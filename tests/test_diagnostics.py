#!/usr/bin/env python3
"""Unit and CLI regression tests for read-only AST diagnostics."""
import contextlib
import hashlib
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
        self.assertEqual(document["version"], 4)
        self.assertEqual(document["phase"], "parsed")
        self.assertEqual(document["source"], os.path.realpath(SAMPLE))
        tree_digest = hashlib.sha256(json.dumps(
            document["tree"], ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(
            tree_digest,
            "488f03fb04b840ae8eaa32efa559ba1161bbbfc34760fd49bf936590dad3c750")
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

    def test_global_statement_is_read_only_ast_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "global_scope.lt"
            source.write_text(
                "counter = 0\nfn bump():\n    global counter\n"
                "    counter = counter + 1\n", encoding="utf-8")
            parsed = self.invoke(str(source), "--show-ast")
            desugared = self.invoke(str(source), "--show-desugar")
        for result, output, error in (parsed, desugared):
            self.assertEqual((result, error), (0, ""))
            nodes_in_tree = list(_nodes(json.loads(output)["tree"]))
            global_nodes = [node for node in nodes_in_tree
                            if node["node"] == "GlobalStmt"]
            self.assertEqual(len(global_nodes), 1)
            self.assertEqual(global_nodes[0]["fields"], {"names": ["counter"]})

    def test_nonlocal_statement_retains_stable_ast_shape_in_v4(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "nonlocal_scope.lt"
            source.write_text(
                "fn outer():\n    value = 1\n    fn inner():\n"
                "        nonlocal value, other\n        value = 2\n",
                encoding="utf-8")
            parsed = self.invoke(str(source), "--show-ast")
            desugared = self.invoke(str(source), "--show-desugar")
        for result, output, error in (parsed, desugared):
            self.assertEqual((result, error), (0, ""))
            document = json.loads(output)
            self.assertEqual(document["version"], 4)
            nodes_in_tree = list(_nodes(document["tree"]))
            declarations = [node for node in nodes_in_tree
                            if node["node"] == "NonlocalStmt"]
            self.assertEqual(len(declarations), 1)
            self.assertEqual(declarations[0]["fields"],
                             {"names": ["value", "other"]})
            self.assertEqual(declarations[0]["position"],
                             {"line": 4, "column": 9})

    def test_named_argument_schema_v4_keeps_existing_call_field_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "named_call.lt"
            source.write_text(
                "fn pair(left, right):\n    return left + right\n"
                "pair(1, right=2)\n", encoding="utf-8")
            parsed = self.invoke(str(source), "--show-ast")
            desugared = self.invoke(str(source), "--show-desugar")
        for result, output, error in (parsed, desugared):
            self.assertEqual((result, error), (0, ""))
            document = json.loads(output)
            self.assertEqual(document["version"], 4)
            nodes_in_tree = list(_nodes(document["tree"]))
            calls = [node for node in nodes_in_tree if node["node"] == "Call"]
            named = [node for node in nodes_in_tree
                     if node["node"] == "NamedArg"]
            self.assertEqual(len(calls), 1)
            self.assertEqual(set(calls[0]["fields"]), {"function", "arguments"})
            self.assertEqual(len(named), 1)
            self.assertEqual(named[0]["fields"],
                             {"name": "right",
                              "value": {"node": "Num",
                                        "position": {"line": 3, "column": 15},
                                        "fields": {"value": 2.0}}})

    def test_default_parameter_schema_v4_preserves_legacy_parameter_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "default_parameter.lt"
            source.write_text(
                "fn old(left, right):\n    return left + right\n"
                "fn scale(x, factor=2):\n    return x * factor\n",
                encoding="utf-8")
            parsed = self.invoke(str(source), "--show-ast")
            desugared = self.invoke(str(source), "--show-desugar")
        for result, output, error in (parsed, desugared):
            self.assertEqual((result, error), (0, ""))
            document = json.loads(output)
            self.assertEqual(document["version"], 4)
            fn_nodes = [node for node in _nodes(document["tree"])
                        if node["node"] == "FnDef"]
            old = next(node for node in fn_nodes
                       if node["fields"]["name"] == "old")
            scale = next(node for node in fn_nodes
                         if node["fields"]["name"] == "scale")
            self.assertEqual(old["fields"]["parameters"], ["left", "right"])
            self.assertEqual(scale["fields"]["parameters"][0], "x")
            default = scale["fields"]["parameters"][1]
            self.assertEqual(default["node"], "DefaultParam")
            self.assertEqual(default["fields"]["name"], "factor")
            self.assertEqual(default["fields"]["default"]["fields"],
                             {"value": 2.0})

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
