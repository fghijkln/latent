#!/usr/bin/env python3
"""Focused cross-backend regressions for Python-backend audit fixes."""
from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import test_p18_callbacks as p18

TARGETS = ("py", "java")
POSITIVE_SOURCE = '''\
fn lambda(x):
    return x + 1
fn lambda_(x):
    return x + 2
fn pick(lambda, yield):
    return lambda + yield
yield = 5
say lambda(2)
say lambda_(2)
say pick(lambda=3, yield=4)
say yield
say 10.0 ** 20
say 10.0 ** 400
say (-10.0) ** 401.0
say 0.0 ** -1.0
math = py "math"
say math.isclose(1, 1, rel_tol=0.001)
say (-1.0) ** math.inf
say (-1.0) ** (-math.inf)
'''
POSITIVE_STDOUT = ("3\n4\n7\n5\n1e+20\ninf\n-inf\ninf\ntrue\n"
                  "nan\nnan\n")
ESCAPE_SOURCE = '''\
fn audit_py_callable(value):
    return value
value = 1
try:
    say value.__class__.__base__.__subclasses__()
catch err:
    say err
try:
    say audit_py_callable.__class__.__base__.__subclasses__()
catch err:
    say err
try:
    say "".__getattribute__("__class__")
catch err:
    say err
'''
ESCAPE_STDOUT = ("attribute access on non-handle value: num\n"
                 "attribute access on non-handle value: function\n"
                 "call on non-handle value: str\n")
NATIVE_ATTRIBUTE_SOURCE = '''\
string_value = "text"
list_value = []
map_value = {}
bool_value = true
nil_value = nil
try:
    say string_value.__class__
catch err:
    say err
try:
    say list_value.__class__
catch err:
    say err
try:
    say map_value.__class__
catch err:
    say err
try:
    say bool_value.__class__
catch err:
    say err
try:
    say nil_value.__class__
catch err:
    say err
'''
NATIVE_ATTRIBUTE_STDOUT = (
    "attribute access on non-handle value: str\n"
    "attribute access on non-handle value: list\n"
    "attribute access on non-handle value: map\n"
    "attribute access on non-handle value: bool\n"
    "attribute access on non-handle value: nil\n")
DIRECT_CALL_SOURCE = '''\
fn audit_py_callable(value):
    return value
string_value = "text"
list_value = []
map_value = {}
bool_value = true
nil_value = nil
try:
    say string_value.__getattribute__("__class__")
catch err:
    say err
try:
    say list_value.__getattribute__("__class__")
catch err:
    say err
try:
    say map_value.__getattribute__("__class__")
catch err:
    say err
try:
    say bool_value.__getattribute__("__class__")
catch err:
    say err
try:
    say nil_value.__getattribute__("__class__")
catch err:
    say err
try:
    say audit_py_callable.__getattribute__("__class__")
catch err:
    say err
'''
DIRECT_CALL_STDOUT = (
    "call on non-handle value: str\n"
    "call on non-handle value: list\n"
    "call on non-handle value: map\n"
    "call on non-handle value: bool\n"
    "call on non-handle value: nil\n"
    "call on non-handle value: function\n")
BOOLEAN_ARITHMETIC_SOURCE = '''\
try:
    say true + false
catch error:
    say error
try:
    say true - false
catch error:
    say error
try:
    say true * false
catch error:
    say error
try:
    say true / false
catch error:
    say error
try:
    say true % false
catch error:
    say error
try:
    say true ** false
catch error:
    say error
try:
    say -true
catch error:
    say error
'''
BOOLEAN_ARITHMETIC_STDOUT = (
    "bad + operands: true, false\n"
    "bad - operand: bool\n"
    "bad * operand: bool\n"
    "bad / operand: bool\n"
    "bad % operand: bool\n"
    "bad ** operand: bool\n"
    "bad unary - operand: bool\n")
HOST_ARITHMETIC_SOURCE = '''\
decimal = py "decimal"
a = decimal.Decimal("7")
b = decimal.Decimal("2")
say a + b
say a - b
say a * b
say a / b
say a % b
say a ** b
say -a
'''
HOST_ARITHMETIC_STDOUT = "9\n5\n14\n3.5\n1\n49\n-7\n"


def run_case(temp: Path, stem: str, source_text: str, target: str):
    source = temp / (stem + ".lt")
    source.write_text(source_text, encoding="utf-8")
    outdir = temp / (stem + "-" + target)
    outdir.mkdir()
    return p18.compile_and_run(source, target, outdir)


class AuditPythonFixes(unittest.TestCase):
    def run_both_targets(self, stem: str, source_text: str, expected: str):
        with tempfile.TemporaryDirectory(prefix="latent-audit-py-") as tmp:
            temp = Path(tmp)
            for target in TARGETS:
                with self.subTest(target=target, case=stem):
                    result = run_case(temp, stem, source_text, target)
                    self.assertEqual(
                        result.returncode, 0,
                        f"stderr={result.stderr[-1200:]!r}")
                    self.assertEqual(
                        result.stdout, expected,
                        f"stderr={result.stderr[-1200:]!r}")

    def test_numeric_keyword_identifiers_and_host_keywords(self):
        self.run_both_targets("audit_py_positive", POSITIVE_SOURCE,
                              POSITIVE_STDOUT)

    def test_native_latent_attribute_introspection_is_denied(self):
        self.run_both_targets("audit_py_escape", ESCAPE_SOURCE,
                              ESCAPE_STDOUT)

    def test_native_scalar_and_container_attributes_are_denied(self):
        self.run_both_targets("audit_py_native_attributes",
                              NATIVE_ATTRIBUTE_SOURCE,
                              NATIVE_ATTRIBUTE_STDOUT)

    def test_direct_method_calls_cannot_bypass_attribute_guard(self):
        self.run_both_targets("audit_py_direct_call", DIRECT_CALL_SOURCE,
                              DIRECT_CALL_STDOUT)

    def test_boolean_arithmetic_never_falls_through_to_python_operators(self):
        self.run_both_targets("audit_boolean_arithmetic",
                              BOOLEAN_ARITHMETIC_SOURCE,
                              BOOLEAN_ARITHMETIC_STDOUT)

    def test_python_host_handles_keep_all_arithmetic_operator_families(self):
        self.run_both_targets("audit_host_arithmetic",
                              HOST_ARITHMETIC_SOURCE,
                              HOST_ARITHMETIC_STDOUT)


if __name__ == "__main__":
    unittest.main()
