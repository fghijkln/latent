#!/usr/bin/env python3
"""Focused cross-backend regressions for Java-backend audit fixes."""
from __future__ import annotations

import contextlib
import io
import re
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
MODULE_FUNCTION_REBIND_SOURCE = '''\
fn h():
    return 0
if true:
    fn h():
        return 1
say h()
x = 5
for item in [0]:
    fn x():
        return 2
say x
'''
MODULE_FUNCTION_REBIND_STDOUT = "1\n<function x>\n"
CLASS_FUNCTION_COLLISION_SOURCE = '''\
if true:
    fn A():
        return 1
class A:
    fn value(self):
        return 2
say A()
'''
NUMERIC_UNICODE_SOURCE = '''\
math = py "math"
tiny = math.ulp(0.0)
say 1 % tiny
say -1 % tiny
say math.nan % 2
say 2 % math.nan
say 1 % math.inf
say -1 % math.inf
say 1 % -math.inf
say -1 % -math.inf
say 1 ** math.nan
say tiny
say -tiny
Double = java "java.lang.Double"
say Double.valueOf(tiny)
try:
    say int(math.inf)
catch error:
    say error
try:
    say int(-math.inf)
catch error:
    say error
try:
    say int(math.nan)
catch error:
    say error
say "\ue000" < "😀"
say "😀" < "\ue000"
'''
NUMERIC_UNICODE_STDOUT = (
    "0\n0\nnan\nnan\n1\ninf\n-inf\n-1\nnan\n5e-324\n-5e-324\n"
    "5e-324\n"
    "int() of inf\nint() of -inf\nint() of nan\ntrue\nfalse\n")
RANGE_LIMIT_SOURCE = '''\
math = py "math"
say len(range(1000000))
say len(range(2 ** 63, 2 ** 63 + 2048))
try:
    values = range(1000001)
catch error:
    say error
try:
    values = range(2 ** 63)
catch error:
    say error
try:
    values = range(math.inf)
catch error:
    say error
'''
RANGE_LIMIT_STDOUT = (
    "1000000\n2048\n"
    "range() result too large (maximum 1000000 items)\n"
    "range() result too large (maximum 1000000 items)\n"
    "range() arguments must be finite\n")


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

    def test_module_block_function_names_bind_in_module_scope(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-hidden-fn-") as tmp:
            temp = Path(tmp)
            source = temp / "audit_hidden_module_fn.lt"
            source.write_text(HIDDEN_MODULE_FUNCTION_SOURCE, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / ("hidden-" + target)
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "1\n")
                    self.assertEqual(result.stderr, "")

    def test_module_block_functions_rebind_existing_module_names(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-fn-rebind-") as tmp:
            temp = Path(tmp)
            source = temp / "audit_module_fn_rebind.lt"
            source.write_text(MODULE_FUNCTION_REBIND_SOURCE, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / ("rebind-" + target)
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, MODULE_FUNCTION_REBIND_STDOUT)
                    self.assertEqual(result.stderr, "")

    def test_module_block_function_cannot_reuse_top_level_class_name(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-class-fn-collision-") as tmp:
            temp = Path(tmp)
            source = temp / "audit_class_fn_collision.lt"
            source.write_text(CLASS_FUNCTION_COLLISION_SOURCE, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / ("collision-" + target)
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("semant error: duplicate definition 'A'",
                                  result.stderr)
                    self.assertNotIn("already defined", result.stderr)

            module_main = temp / "main_collision.lt"
            module_main.write_text('import "dep.lt" as dep\nsay 1\n',
                                   encoding="utf-8")
            (temp / "dep.lt").write_text(CLASS_FUNCTION_COLLISION_SOURCE,
                                         encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target, imported_module=True):
                    outdir = temp / ("module-collision-" + target)
                    outdir.mkdir()
                    result = p18.compile_and_run(module_main, target, outdir)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("semant error: duplicate definition 'A'",
                                  result.stderr)
                    self.assertNotIn("already defined", result.stderr)

    def test_top_level_bindings_cannot_reuse_top_level_class_names(self):
        class_source = (
            "class A:\n"
            "    fn value(self):\n"
            "        return 1\n")
        cases = {
            "assignment": class_source + "A = 5\n",
            "for target": class_source + "for A in [5]:\n    say A\n",
            "catch target": class_source +
                            'try:\n    throw "x"\ncatch A:\n    say A\n',
            "unreachable assignment": class_source + "if false:\n    A = 1\n",
            "assignment before class": "A = 5\n" + class_source,
        }
        with tempfile.TemporaryDirectory(
                prefix="latent-audit-class-binding-collision-") as tmp:
            temp = Path(tmp)
            for case, text in cases.items():
                source = temp / (case.replace(" ", "_") + ".lt")
                source.write_text(text, encoding="utf-8")
                for target in TARGETS:
                    with self.subTest(case=case, target=target):
                        outdir = temp / (source.stem + "-" + target)
                        outdir.mkdir()
                        result = p18.compile_and_run(source, target, outdir)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(
                            "semant error: duplicate definition 'A'",
                            result.stderr)
                        self.assertNotIn("already defined", result.stderr)

            root = temp / "main_class_binding.lt"
            root.write_text('import "dep.lt" as dep\nsay 1\n',
                            encoding="utf-8")
            (temp / "dep.lt").write_text(class_source + "A = 5\n",
                                         encoding="utf-8")
            for target in TARGETS:
                with self.subTest(case="imported module", target=target):
                    outdir = temp / ("module-" + target)
                    outdir.mkdir()
                    result = p18.compile_and_run(root, target, outdir)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(
                        "semant error: duplicate definition 'A'",
                        result.stderr)

    def test_global_cannot_rebind_class_names_but_function_locals_can(self):
        class_source = (
            "class A:\n"
            "    fn value(self):\n"
            "        return 1\n")
        invalid = class_source + (
            "fn replace_class():\n"
            "    global A\n"
            "    A = 5\n"
            "replace_class()\n")
        valid = class_source + (
            "fn parameter(A):\n"
            "    return A\n"
            "fn local():\n"
            "    A = 5\n"
            "    return A\n"
            "say parameter(6)\n"
            "say local()\n")
        with tempfile.TemporaryDirectory(prefix="latent-audit-global-class-") as tmp:
            temp = Path(tmp)
            bad_source = temp / "global_class.lt"
            bad_source.write_text(invalid, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target, case="global class"):
                    outdir = temp / ("bad-" + target)
                    outdir.mkdir()
                    result = p18.compile_and_run(bad_source, target, outdir)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(
                        "class name 'A' cannot be declared global",
                        result.stderr)
                    self.assertNotIn("incompatible types", result.stderr)

            module_main = temp / "main_global_class.lt"
            module_main.write_text(
                'import "dep.lt" as dep\nsay dep.replace_class()\n',
                encoding="utf-8")
            (temp / "dep.lt").write_text(invalid, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target, case="module global class"):
                    outdir = temp / ("module-bad-" + target)
                    outdir.mkdir()
                    result = p18.compile_and_run(module_main, target, outdir)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(
                        "class name 'A' cannot be declared global",
                        result.stderr)

            good_source = temp / "local_class.lt"
            good_source.write_text(valid, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target, case="local shadowing"):
                    outdir = temp / ("good-" + target)
                    outdir.mkdir()
                    result = p18.compile_and_run(good_source, target, outdir)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "6\n5\n")

    def test_nonfinite_numeric_and_unicode_ordering_match_both_targets(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-numeric-unicode-") as tmp:
            temp = Path(tmp)
            source = temp / "audit_numeric_unicode.lt"
            source.write_text(NUMERIC_UNICODE_SOURCE, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / ("numeric-" + target)
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, NUMERIC_UNICODE_STDOUT)
                    self.assertEqual(result.stderr, "")

    def test_python_float_notation_matches_in_values_and_containers(self):
        source_text = (
            "say 12345678.5\n"
            "say -12345678.5\n"
            "say 0.0005\n"
            "say -0.0005\n"
            "say str(12345678.5)\n"
            "say str(0.0005)\n"
            "say [12345678.5, 0.0005]\n"
            'say {"large": 12345678.5, "small": 0.0005}\n'
            "say [0.00001, 0.000015, -0.000001]\n"
            "say 12345678901234568\n")
        expected = (
            "12345678.5\n"
            "-12345678.5\n"
            "0.0005\n"
            "-0.0005\n"
            "12345678.5\n"
            "0.0005\n"
            "[12345678.5, 0.0005]\n"
            '{"large": 12345678.5, "small": 0.0005}\n'
            "[1e-05, 1.5e-05, -1e-06]\n"
            "1.2345678901234568e+16\n")
        with tempfile.TemporaryDirectory(prefix="latent-audit-float-notation-") as tmp:
            temp = Path(tmp)
            source = temp / "float_notation.lt"
            source.write_text(source_text, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / target
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)
                    self.assertEqual(result.stderr, "")

    def test_small_subnormal_float_uses_shortest_round_trip_spelling(self):
        value = "(2 ** -1074) / 0.5"
        triple = "(2 ** -1074) * 3"
        ninefold = "(2 ** -1074) * 9"
        source_text = (f"say {value}\n"
                       f"say [-{value}]\n"
                       f"say {triple}\n"
                       f"say {ninefold}\n"
                       f'say {{"tiny": {value}}}\n')
        expected = ("1e-323\n[-1e-323]\n1.5e-323\n4.4e-323\n"
                    "{\"tiny\": 1e-323}\n")
        with tempfile.TemporaryDirectory(prefix="latent-audit-subnormal-") as tmp:
            temp = Path(tmp)
            source = temp / "subnormal.lt"
            source.write_text(source_text, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / target
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)
                    self.assertEqual(result.stderr, "")

    def test_deep_control_nesting_is_rejected_consistently(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-nesting-limit-") as tmp:
            temp = Path(tmp)
            for depth, should_compile in ((32, True), (98, False)):
                source_text = (
                    "".join("    " * index + "if true:\n"
                            for index in range(depth)) +
                    "    " * depth + "say 1\n")
                source = temp / f"nested_{depth}.lt"
                source.write_text(source_text, encoding="utf-8")
                for target in TARGETS:
                    with self.subTest(depth=depth, target=target):
                        outdir = temp / f"{depth}-{target}"
                        outdir.mkdir()
                        result = p18.compile_and_run(source, target, outdir)
                        if should_compile:
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(result.stdout, "1\n")
                        else:
                            self.assertNotEqual(result.returncode, 0)
                            self.assertIn(
                                "source nesting exceeds maximum supported depth (32)",
                                result.stderr)
                            self.assertNotIn("IndentationError", result.stderr)
                            self.assertNotIn("javac", result.stderr)

    def test_nesting_limit_tracks_elif_nested_functions_and_expressions(self):
        elif_chain = "x = 300\nif x == 0:\n    say 0\n" + "".join(
            f"elif x == {i}:\n    say {i}\n" for i in range(1, 301))
        nested_fns = "".join("    " * i + f"fn f{i}():\n" for i in range(40))
        nested_fns += "    " * 40 + "say 1\nsay 2\n"
        cases = (
            ("elif", elif_chain, "300\n", None),
            ("fns", nested_fns, None, "source nesting exceeds maximum supported depth (32)"),
            ("neg", "say " + "-" * 200 + "1\n", None,
             "expression nesting exceeds maximum supported depth (160)"),
        )
        with tempfile.TemporaryDirectory(prefix="latent-audit-nesting-shapes-") as tmp:
            temp = Path(tmp)
            for name, source_text, stdout, error in cases:
                source = temp / f"{name}.lt"
                source.write_text(source_text, encoding="utf-8")
                for target in TARGETS:
                    with self.subTest(case=name, target=target):
                        outdir = temp / f"{name}-{target}"
                        outdir.mkdir()
                        result = p18.compile_and_run(source, target, outdir)
                        if stdout is not None:
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(result.stdout, stdout)
                        else:
                            self.assertNotEqual(result.returncode, 0)
                            self.assertIn("semant error: " + error, result.stderr)

    def test_java_large_string_literals_are_chunked_without_constant_folding(self):
        value = "a" * 65_536 + "🙂" * 2
        with tempfile.TemporaryDirectory(prefix="latent-audit-long-java-string-") as tmp:
            temp = Path(tmp)
            source = temp / "long_string.lt"
            source.write_text('say "' + value + '"\n', encoding="utf-8")
            expected = value + "\n"
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / target
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertEqual(result.returncode, 0, result.stderr[-1000:])
                    self.assertEqual(result.stdout, expected)
                    self.assertEqual(result.stderr, "")

    def test_java_error_trace_skips_synthetic_lambda_frames(self):
        source_text = (
            "if true:\n"
            "    fn hidden():\n"
            '        throw "boom"\n'
            "hidden()\n")
        with tempfile.TemporaryDirectory(prefix="latent-audit-lambda-frame-") as tmp:
            temp = Path(tmp)
            source = temp / "lambda_frame.lt"
            source.write_text(source_text, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / target
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("Latent runtime error:", result.stderr)
                    self.assertNotIn("lambda$", result.stderr)
                    self.assertIn("lambda_frame.lt:3", result.stderr)

    def test_range_limit_and_large_endpoints_match_both_targets(self):
        with tempfile.TemporaryDirectory(
                prefix="latent-audit-range-limit-") as tmp:
            temp = Path(tmp)
            source = temp / "range_limit.lt"
            source.write_text(RANGE_LIMIT_SOURCE, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / target
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, RANGE_LIMIT_STDOUT)
                    self.assertEqual(result.stderr, "")
            uncaught = temp / "range_uncaught.lt"
            uncaught.write_text("values = range(2 ** 63)\n",
                                encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target, uncaught=True):
                    outdir = temp / (target + "-uncaught")
                    outdir.mkdir()
                    result = p18.compile_and_run(uncaught, target, outdir)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn(
                        "Latent runtime error: ArgumentError: "
                        "range() result too large (maximum 1000000 items)",
                        result.stderr)
                    self.assertRegex(result.stderr, r"range_uncaught\.lt:1 in main$")

    def test_many_sibling_try_blocks_have_linear_java_output_growth(self):
        def generated_length(count):
            source = "".join(
                f'try:\n    throw "{i}"\ncatch error:\n    say error\n'
                for i in range(count))
            program, error = latent.compile_src(source, "try_growth.lt")
            self.assertIsNone(error)
            return gen_java.generate(program, "TryGrowth")

        short = generated_length(100)
        long = generated_length(200)
        self.assertLess(len(long), len(short) * 2.3)

        nested, error = latent.compile_src(
            'try:\n    try:\n        throw "x"\n    catch inner:\n'
            '        say inner\ncatch outer:\n    say outer\n',
            "nested_try.lt")
        self.assertIsNone(error)
        catches = re.findall(r"catch \(Exception (_*lt_e)\)",
                             gen_java.generate(nested, "NestedTry"))
        self.assertEqual(len(catches), 2)
        self.assertNotEqual(catches[0], catches[1])

    def test_1808_top_level_try_blocks_compile_and_run_on_java(self):
        source_text = "".join(
            'try:\n    say "ok"\ncatch error:\n    say error\n'
            for _ in range(1808))
        with tempfile.TemporaryDirectory(prefix="latent-audit-many-top-tries-") as tmp:
            temp = Path(tmp)
            source = temp / "many_top_tries.lt"
            source.write_text(source_text, encoding="utf-8")
            outdir = temp / "java"
            outdir.mkdir()
            result = p18.compile_and_run(source, "java", outdir)
            self.assertEqual(result.returncode, 0,
                             f"stdout={result.stdout[-200:]!r}, "
                             f"stderr={result.stderr[-1600:]!r}")
            self.assertEqual(result.stdout, "ok\n" * 1808)
            self.assertEqual(result.stderr, "")

    def test_large_nested_block_is_split_and_runs_on_both_targets(self):
        source_text = ("fn large_nested():\n    if true:\n" +
                       "        say 1\n" * 8191 + "large_nested()\n")
        with tempfile.TemporaryDirectory(
                prefix="latent-audit-large-nested-block-") as tmp:
            temp = Path(tmp)
            source = temp / "large_nested_block.lt"
            source.write_text(source_text, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / target
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertEqual(result.returncode, 0,
                                     f"{target}: {result.stderr[-1600:]!r}")
                    self.assertEqual(result.stdout, "1\n" * 8191)
                    self.assertEqual(result.stderr, "")

    def test_nested_block_chunks_preserve_control_flow_and_scope(self):
        lines = ["fn flow():", "    total = 0", "    for i in range(3):"]
        lines.extend("        total = total + 1" for _ in range(65))
        lines.extend(("        if i == 0:", "            continue",
                      "        if i == 1:", "            break", "    try:"))
        for _ in range(64):
            lines.extend(("        if true:", '            throw "caught"'))
        lines.extend(("    catch error:", "        total = total + 1",
                      "    return total", "fn outer():", "    count = 0",
                      "    fn bump():", "        nonlocal count"))
        lines.extend("        count = count + 1" for _ in range(65))
        lines.extend(("    bump()", "    return count", "say flow()",
                      "say outer()"))
        source_text = "\n".join(lines) + "\n"
        with tempfile.TemporaryDirectory(
                prefix="latent-audit-nested-control-chunks-") as tmp:
            temp = Path(tmp)
            source = temp / "nested_control_chunks.lt"
            source.write_text(source_text, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    outdir = temp / target
                    outdir.mkdir()
                    result = p18.compile_and_run(source, target, outdir)
                    self.assertEqual(result.returncode, 0,
                                     f"{target}: {result.stderr[-1600:]!r}")
                    self.assertEqual(result.stdout, "131\n65\n")
                    self.assertEqual(result.stderr, "")

    def test_chunked_java_tracebacks_use_latent_function_names(self):
        cases = (
            ("direct_traceback.lt",
             'fn direct():\n    throw "boom"\ndirect()\n', "direct", 2),
            ("chunked_traceback.lt",
             'fn chunked():\n' +
             '    if true:\n        throw "boom"\n' * 64 +
             'chunked()\n', "chunked", 3),
        )
        with tempfile.TemporaryDirectory(
                prefix="latent-audit-chunked-tracebacks-") as tmp:
            temp = Path(tmp)
            for filename, source_text, function_name, source_line in cases:
                source = temp / filename
                source.write_text(source_text, encoding="utf-8")
                for target in TARGETS:
                    with self.subTest(filename=filename, target=target):
                        outdir = temp / (Path(filename).stem + "-" + target)
                        outdir.mkdir()
                        result = p18.compile_and_run(source, target, outdir)
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertRegex(
                            result.stderr,
                            rf"{re.escape(filename)}:{source_line} in "
                            rf"{re.escape(function_name)}$")
                        self.assertNotIn("_lt_block_", result.stderr)

    def test_large_module_init_uses_java_helpers(self):
        module_source = "".join(
            'try:\n    say "module"\ncatch error:\n    say error\n'
            for _ in range(65))
        with tempfile.TemporaryDirectory(prefix="latent-audit-large-module-init-") as tmp:
            temp = Path(tmp)
            source = temp / "main.lt"
            source.write_text('import "dep.lt" as dep\nsay 9\n',
                              encoding="utf-8")
            (temp / "dep.lt").write_text(module_source, encoding="utf-8")
            outdir = temp / "java"
            outdir.mkdir()
            result = p18.compile_and_run(source, "java", outdir)
            self.assertEqual(result.returncode, 0,
                             f"stdout={result.stdout[-200:]!r}, "
                             f"stderr={result.stderr[-1600:]!r}")
            self.assertEqual(result.stdout, "module\n" * 65 + "9\n")
            self.assertEqual(result.stderr, "")

    def test_chunked_top_level_errors_use_python_frame_names(self):
        entry = ("".join(f"say {i}\n" for i in range(64)) +
                 'throw "entry-boundary"\n')
        module_body = ("".join(f"say {i}\n" for i in range(64)) +
                       'throw "module-boundary"\n')
        with tempfile.TemporaryDirectory(
                prefix="latent-audit-top-helper-frames-") as tmp:
            temp = Path(tmp)
            entry_source = temp / "entry_frame.lt"
            entry_source.write_text(entry, encoding="utf-8")
            module_source = temp / "module_frame.lt"
            module_source.write_text(
                'import "dep.lt" as dep\nsay 1\n', encoding="utf-8")
            (temp / "dep.lt").write_text(module_body, encoding="utf-8")
            cases = ((entry_source, "entry_frame.lt", r"in main$"),
                     (module_source, "dep.lt",
                      r"in _lt_module_init_[0-9a-f]+$"))
            for source, source_name, frame_pattern in cases:
                for target in TARGETS:
                    with self.subTest(source=source.name, target=target):
                        outdir = temp / (source.stem + "-" + target)
                        outdir.mkdir()
                        result = p18.compile_and_run(source, target, outdir)
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertRegex(
                            result.stderr,
                            rf"{re.escape(source_name)}:65 {frame_pattern}")
                        self.assertNotIn("_lt_top_", result.stderr)

    def test_module_init_block_functions_compile_on_both_targets(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-module-fn-") as tmp:
            temp = Path(tmp)
            source = temp / "main.lt"
            source.write_text('import "dep.lt" as dep\nsay dep.hidden()\nsay 9\n',
                              encoding="utf-8")
            (temp / "dep.lt").write_text(
                "if true:\n"
                "    fn hidden():\n"
                "        return 1\n",
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
                    self.assertEqual(result.stdout, "1\n9\n")
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

    def test_java_cli_reports_size_limits_without_raw_javac_output(self):
        diagnostics = (
            "Example.java:1: error: code too large\n",
            "Example.java:1: error: too many constants\n",
        )
        expected = ("error: generated Java program is too large for javac "
                    "(bytecode or constant-pool limit)\n")
        with tempfile.TemporaryDirectory(
                prefix="latent-audit-java-size-diagnostic-") as tmp:
            temp = Path(tmp)
            source = temp / "size_diagnostic.lt"
            source.write_text("say 1\n", encoding="utf-8")
            for diagnostic in diagnostics:
                with self.subTest(diagnostic=diagnostic.strip()):
                    outdir = temp / str(len(diagnostic))
                    stderr = io.StringIO()
                    argv = ["latent.py", str(source), "-t", "java", "-o",
                            str(outdir)]
                    result = mock.Mock(returncode=1, stdout="",
                                       stderr=diagnostic)
                    with mock.patch.object(sys, "argv", argv), \
                            mock.patch.object(latent.subprocess, "run",
                                              return_value=result), \
                            contextlib.redirect_stderr(stderr):
                        status = latent.main()
                    self.assertEqual(status, 1)
                    self.assertEqual(stderr.getvalue(), expected)
                    self.assertNotIn("Example.java", stderr.getvalue())

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
