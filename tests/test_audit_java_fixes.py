#!/usr/bin/env python3
"""Focused cross-backend regressions for Java-backend audit fixes."""
from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import gen_java
import latent
from playground import app as playground_app
from tests import test_p18_callbacks as p18

TARGETS = ("py", "java")
SOURCE = '''\
fn audit_java_return(flag):
    if flag:
        return "return-true"
    else:
        return "return-false"
    say "UNREACHABLE-RETURN"
fn audit_java_try(flag):
    try:
        if flag:
            return "try-return"
        throw "try-throw"
    catch error:
        return "catch-return"
        say "UNREACHABLE-HANDLER"
    say "UNREACHABLE-TRY"
fn audit_java_throw():
    throw "explicit-throw"
    say "UNREACHABLE-THROW"
say audit_java_return(true)
say audit_java_return(false)
say audit_java_try(true)
say audit_java_try(false)
try:
    throw "top-level-throw"
    say "UNREACHABLE-TRY-BODY"
catch _lt_e:
    say _lt_e
say "after-top-level-catch"
try:
    audit_java_throw()
catch audit_java_error:
    say audit_java_error
say "after-function-throw"
'''
EXPECTED_STDOUT = (
    "return-true\n"
    "return-false\n"
    "try-return\n"
    "catch-return\n"
    "top-level-throw\n"
    "after-top-level-catch\n"
    "explicit-throw\n"
    "after-function-throw\n"
)
NESTED_FUNCTION_SOURCE = '''\
if true:
    fn module_if():
        return 1
for item in [0]:
    fn module_for():
        return 2
while false:
    fn module_while():
        return 3
try:
    fn module_try():
        return 4
catch error:
    fn module_catch():
        return 5
fn top_level():
    return 2
fn exercise():
    value = 0
    if true:
        fn from_if():
            nonlocal value
            value = value + 1
    for item in [0]:
        fn from_for():
            nonlocal value
            value = value + 1
    while false:
        fn from_while():
            return 100
    try:
        fn from_try():
            nonlocal value
            value = value + 1
    catch error:
        fn from_catch():
            return 100
    from_if()
    from_for()
    from_try()
    return value
class C:
    fn run(self):
        value = 10
        if true:
            fn method_inner():
                nonlocal value
                value = value + 5
        method_inner()
        return value
say 9
say top_level()
say exercise()
say C.new().run()
'''
NESTED_FUNCTION_STDOUT = "9\n2\n3\n15\n"
HIDDEN_MODULE_FUNCTION_SOURCE = '''\
if true:
    fn hidden():
        return 1
say hidden()
'''


def run_case(temp: Path, target: str):
    source = temp / "audit_java_terminal_and_catch.lt"
    source.write_text(SOURCE, encoding="utf-8")
    outdir = temp / ("audit_java-" + target)
    outdir.mkdir()
    return p18.compile_and_run(source, target, outdir)


class TestAuditJavaFixes(unittest.TestCase):
    def test_abrupt_control_flow_and_catch_name_isolation(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-java-") as tmp:
            temp = Path(tmp)
            for target in TARGETS:
                with self.subTest(target=target):
                    result = run_case(temp, target)
                    self.assertEqual(
                        result.returncode, 0,
                        f"{target}: stdout={result.stdout!r}, "
                        f"stderr={result.stderr[-1600:]!r}")
                    self.assertEqual(result.stdout, EXPECTED_STDOUT)
                    self.assertEqual(result.stderr, "")

    def test_nested_functions_in_module_blocks_and_closures_compile_on_both_targets(
            self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-nested-fns-") as tmp:
            temp = Path(tmp)
            source = temp / "audit_nested_functions.lt"
            source.write_text(NESTED_FUNCTION_SOURCE, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / ("nested-" + target)
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertEqual(
                        result.returncode, 0,
                        f"{target}: stdout={result.stdout!r}, "
                        f"stderr={result.stderr[-1600:]!r}")
                    self.assertEqual(result.stdout, NESTED_FUNCTION_STDOUT)
                    self.assertEqual(result.stderr, "")

    def test_module_block_function_names_remain_semantically_inaccessible(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-hidden-fn-") as tmp:
            temp = Path(tmp)
            source = temp / "audit_hidden_module_fn.lt"
            source.write_text(HIDDEN_MODULE_FUNCTION_SOURCE, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / ("hidden-" + target)
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("semant error: undefined name 'hidden'",
                                  result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def test_module_init_block_functions_compile_on_both_targets(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-module-fn-") as tmp:
            temp = Path(tmp)
            source = temp / "main.lt"
            source.write_text('import "dep.lt" as dep\nsay 9\n',
                              encoding="utf-8")
            (temp / "dep.lt").write_text(
                "if true:\n"
                "    fn hidden():\n"
                "        return 1\n"
                "say 4\n",
                encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / ("module-" + target)
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertEqual(
                        result.returncode, 0,
                        f"{target}: stdout={result.stdout!r}, "
                        f"stderr={result.stderr[-1600:]!r}")
                    self.assertEqual(result.stdout, "4\n9\n")
                    self.assertEqual(result.stderr, "")

    def test_java_cli_reports_codegen_failures_without_host_traceback(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-codegen-cli-") as tmp:
            temp = Path(tmp)
            source = temp / "cli_codegen.lt"
            source.write_text("say 1\n", encoding="utf-8")
            outdir = temp / "out"
            stderr = io.StringIO()
            argv = ["latent.py", str(source), "-t", "java", "-o", str(outdir)]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(gen_java, "generate",
                                      side_effect=KeyError("missing helper")), \
                    contextlib.redirect_stderr(stderr):
                result = latent.main()
            self.assertEqual(result, 1)
            self.assertIn("codegen error: KeyError: 'missing helper'",
                          stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_playground_keeps_nested_function_result_structured(self):
        result = playground_app.run_program(
            "if true:\n    fn m():\n        return 1\nsay 9\n")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stdout"], "9\n")
        self.assertNotIn("Traceback", str(result))
        with mock.patch.object(playground_app.gen_py, "generate",
                               side_effect=KeyError("missing helper")):
            failure = playground_app.run_program("say 9\n")
        self.assertFalse(failure["ok"])
        self.assertEqual(failure["stage"], "codegen")
        self.assertIn("missing helper", failure["error"])
        self.assertNotIn("Traceback", str(failure))


if __name__ == "__main__":
    unittest.main()
