#!/usr/bin/env python3
"""Contract tests for parser-derived editor metadata."""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import editor_metadata
import lex
import parse


SOURCE = """class Box:
    fn init(self):
        self.value = 0

    fn add(self, amount=1) => amount
fn scale(value):
    if value:
        for item in value:
            say item
    elif false:
        say 0
    else:
        try:
            say \"x\"
        catch error:
            say error
"""


class EditorMetadataTests(unittest.TestCase):
    def test_metadata_uses_ast_declarations_and_nested_block_ranges(self):
        tree = parse.parse(lex.lex(SOURCE))
        metadata = editor_metadata.build(tree)

        self.assertEqual(metadata["format"], "latent-editor-metadata")
        self.assertEqual(metadata["version"], 1)
        self.assertEqual(
            [(symbol["kind"], symbol["name"], symbol["range"])
             for symbol in metadata["symbols"]],
            [("class", "Box", {"startLine": 1, "endLine": 5}),
             ("function", "scale", {"startLine": 6, "endLine": 16})],
        )
        box = metadata["symbols"][0]
        self.assertEqual(
            [(symbol["name"], symbol["selection"], symbol["range"])
             for symbol in box["children"]],
            [("init", {"line": 2, "column": 8},
              {"startLine": 2, "endLine": 3}),
             ("add", {"line": 5, "column": 8},
              {"startLine": 5, "endLine": 5})],
        )
        self.assertEqual(
            metadata["foldingRanges"],
            [{"startLine": 1, "endLine": 5},
             {"startLine": 2, "endLine": 3},
             {"startLine": 6, "endLine": 16},
             {"startLine": 7, "endLine": 16},
             {"startLine": 8, "endLine": 9},
             {"startLine": 10, "endLine": 16},
             {"startLine": 13, "endLine": 16}],
        )

    def test_json_contract_is_deterministic(self):
        tree = parse.parse(lex.lex(SOURCE))
        encoded = editor_metadata.dumps(tree)
        self.assertEqual(encoded, editor_metadata.dumps(tree))
        self.assertEqual(json.loads(encoded), editor_metadata.build(tree))
        self.assertTrue(encoded.endswith("\n"))

    def test_cli_reads_stdin_and_emits_only_metadata(self):
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "latent.py"),
             "--editor-metadata"],
            input="fn greet():\n    say \"hi\"\n",
            text=True,
            capture_output=True,
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "format": "latent-editor-metadata",
            "version": 1,
            "symbols": [{
                "kind": "function",
                "name": "greet",
                "range": {"startLine": 1, "endLine": 2},
                "selection": {"line": 1, "column": 4},
                "children": [],
            }],
            "foldingRanges": [{"startLine": 1, "endLine": 2}],
        })

    def test_cli_reports_invalid_source_without_partial_json(self):
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "latent.py"),
             "--editor-metadata"],
            input="fn broken(:\n",
            text=True,
            capture_output=True,
            cwd=ROOT,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("parse error:", result.stderr)


if __name__ == "__main__":
    unittest.main()
