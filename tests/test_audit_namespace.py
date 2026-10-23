#!/usr/bin/env python3
"""Cross-backend regressions for generated Python runtime namespace collisions."""
from __future__ import annotations

import tempfile
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import test_p18_callbacks as p18
import gen_py

TARGETS = ("py", "java")

SHADOWED_BUILTINS_SOURCE = '''\
list = 17
float = 23
print = 29
id = 31
_math = 37
say list
say float
say print
say id
say 2 ** 3
say range(3)
say keys({"x": 1})
host = py "p18_callbacks_host"
fn wrap(value):
    return [value]
say host.invoke_one(wrap)
'''
SHADOWED_BUILTINS_STDOUT = "17\n23\n29\n31\n8\n[0, 1, 2]\n[\"x\"]\n[\"one\"]\n"

SHADOWED_GUARD_SOURCE = '''\
_wv_wsetattr = 44
try:
    __wsetattr([], "field", 1)
catch err:
    say err
say _wv_wsetattr
'''
SHADOWED_GUARD_STDOUT = "cannot set attribute on list\n44\n"

SHADOWED_RUNTIME_NAMES_SOURCE = '''\
_wv_non_handle_type = 41
type = 42
isinstance = 43
_os = 44
_sys = 45
_traceback = 46
if false:
    object = nil
say _wv_non_handle_type
say type
say isinstance
say _os
say _sys
say _traceback
say object
'''
SHADOWED_RUNTIME_NAMES_STDOUT = "41\n42\n43\n44\n45\n46\nnil\n"

CONDITIONAL_RUNTIME_ALIASES_SOURCE = '''\
if false:
    _wv_non_handle_type = nil
    type = nil
    isinstance = nil
    _os = nil
    _sys = nil
    _traceback = nil
    object = nil
say _wv_non_handle_type
say type
say isinstance
say _os
say _sys
say _traceback
say object
'''
CONDITIONAL_RUNTIME_ALIASES_STDOUT = "nil\n" * 7

SHADOWED_SCOPES_SOURCE = '''\
fn echo(object, type, isinstance, _wv_non_handle_type, _os, _sys):
    return [object, type, isinstance, _wv_non_handle_type, _os, _sys]
say echo(1, 2, 3, 4, 5, 6)
fn local_names():
    if false:
        object = nil
        _wv_non_handle_type = nil
    return [object, _wv_non_handle_type]
say local_names()
if false:
    object = nil
fn read_global():
    global object
    return object
say read_global()
class Box:
    fn init(self, object):
        self.object = object
    fn get(self):
        return self.object
box = Box.new(17)
say box.get()
'''
SHADOWED_SCOPES_STDOUT = "[1, 2, 3, 4, 5, 6]\n[nil, nil]\nnil\n17\n"

SHADOWED_DIAGNOSTIC_SOURCE = '''\
if true:
    _os = 41
    _subprocess = 42
    _wv_report_uncaught = 43
    _WV_SOURCE_FILE = 44
_WV_SOURCE_MAP = 43
say 1 + "bad"
'''

FUNCTION_DIAGNOSTIC_SOURCE = '''\
fn readable():
    return 1 + "bad"
readable()
'''

JAVA_INTEROP_SOURCE = '''\
if true:
    _subprocess = 42
Builder = java "java.lang.StringBuilder"
builder = Builder.new()
builder.append("namespace-ok")
say builder.toString()
'''

ALL_RUNTIME_ALIASES_SOURCE = '''\
_builtins = 1
_importlib = 2
_atexit = 3
_json = 4
_math = 5
_os = 6
_shutil = 7
_subprocess = 8
_inspect = 9
_threading = 10
_sys = 11
_traceback = 12
_types = 13
__name__ = 14
__file__ = 15
__builtins__ = 16
object = 17
say [_builtins, _importlib, _atexit, _json, _math, _os, _shutil, _subprocess, _inspect, _threading, _sys, _traceback, _types, __name__, __file__, __builtins__, object]
say 2 + 3
say range(2)
'''
ALL_RUNTIME_ALIASES_STDOUT = (
    "[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]\n"
    "5\n[0, 1]\n")

ATTRIBUTE_GUARD_SOURCE = '''\
fn _wv_non_handle_type(value):
    return nil
fn type(value):
    if value == nil:
        return 0
    return 1
fn isinstance(value, klass):
    return false
fn inspect(_wv_non_handle_type, type, isinstance):
    return "".__class__
try:
    say inspect(1, 2, 3)
catch err:
    say err
if false:
    object = nil
try:
    say object.__class__
catch err:
    say err
say object
for _subprocess in []:
    say _subprocess
say _subprocess
'''
ATTRIBUTE_GUARD_STDOUT = (
    "attribute access on non-handle value: str\n"
    "attribute access on non-handle value: nil\n"
    "nil\nnil\n")

UNICODE_AND_SCOPE_SOURCE = '''\
K = 1
K = 2
_lt_user_4b = 3
say [K, K, _lt_user_4b]
counter = 5
fn increment():
    global counter
    counter = counter + 1
    return counter
fn closure(value):
    fn update():
        nonlocal value
        value = value + 1
        return value
    update()
    return value
say increment()
say closure(7)
'''
UNICODE_AND_SCOPE_STDOUT = "[1, 2, 3]\n6\n8\n"


def run_case(temp: Path, stem: str, source_text: str, target: str):
    source = temp / (stem + ".lt")
    source.write_text(source_text, encoding="utf-8")
    outdir = temp / (stem + "-" + target)
    outdir.mkdir()
    return p18.compile_and_run(source, target, outdir)


class AuditNamespaceTests(unittest.TestCase):
    def run_both_successfully(self, stem: str, source: str, expected: str):
        with tempfile.TemporaryDirectory(prefix="latent-audit-namespace-") as name:
            temp = Path(name)
            for target in TARGETS:
                with self.subTest(target=target, case=stem):
                    result = run_case(temp, stem, source, target)
                    self.assertEqual(
                        result.returncode, 0,
                        f"stdout={result.stdout!r}; stderr={result.stderr[-1200:]!r}")
                    self.assertEqual(
                        result.stdout, expected,
                        f"stderr={result.stderr[-1200:]!r}")
                    self.assertEqual(result.stderr, "")

    def test_user_bindings_do_not_shadow_runtime_builtins_or_aliases(self):
        self.run_both_successfully("namespace_builtins", SHADOWED_BUILTINS_SOURCE,
                                   SHADOWED_BUILTINS_STDOUT)

    def test_user_binding_does_not_shadow_wsetattr_guard(self):
        self.run_both_successfully("namespace_guard", SHADOWED_GUARD_SOURCE,
                                   SHADOWED_GUARD_STDOUT)

    def test_runtime_builtin_names_and_false_branch_globals_are_isolated(self):
        self.run_both_successfully(
            "namespace_shadowed_names", SHADOWED_RUNTIME_NAMES_SOURCE,
            SHADOWED_RUNTIME_NAMES_STDOUT)

    def test_false_branches_preinitialize_runtime_alias_names_without_fallback(self):
        self.run_both_successfully(
            "namespace_conditional_aliases", CONDITIONAL_RUNTIME_ALIASES_SOURCE,
            CONDITIONAL_RUNTIME_ALIASES_STDOUT)

    def test_runtime_namespace_isolated_across_language_scopes(self):
        self.run_both_successfully(
            "namespace_scopes", SHADOWED_SCOPES_SOURCE,
            SHADOWED_SCOPES_STDOUT)

    def test_every_prelude_module_and_special_global_name_is_isolated(self):
        self.run_both_successfully(
            "namespace_all_aliases", ALL_RUNTIME_ALIASES_SOURCE,
            ALL_RUNTIME_ALIASES_STDOUT)

    def test_shadowed_guard_dependencies_and_conditional_nil_are_safe(self):
        self.run_both_successfully(
            "namespace_attribute_guard", ATTRIBUTE_GUARD_SOURCE,
            ATTRIBUTE_GUARD_STDOUT)

    def test_ascii_identifier_encoding_is_injective_and_preserves_scopes(self):
        self.run_both_successfully(
            "namespace_unicode_scopes", UNICODE_AND_SCOPE_SOURCE,
            UNICODE_AND_SCOPE_STDOUT)

    def test_module_graph_namespaces_reject_host_attribute_access(self):
        library = '''\
fn _wv_non_handle_type(value):
    return nil
fn type(value):
    return 1
fn isinstance(value, klass):
    return false
__file__ = 1
__name__ = 2
__builtins__ = 3
if false:
    object = nil
for _os in []:
    say "must-not-run"
fn probe():
    say [object, _os]
    for value in ["", object]:
        try:
            say value.__class__
        catch error:
            say error
    return 7
fn broken():
    return 1 + "module-marker"
'''
        root_source = ('import "lib.lt" as lib\n'
                       '_wv_report_uncaught = nil\n'
                       '_WV_SOURCE_MAP = nil\n'
                       'say lib.probe()\n')
        expected = ('[nil, nil]\n'
                    'attribute access on non-handle value: str\n'
                    'attribute access on non-handle value: nil\n7\n')
        with tempfile.TemporaryDirectory(prefix="latent-audit-module-namespace-") as name:
            temp = Path(name)
            (temp / "lib.lt").write_text(library, encoding="utf-8")
            for target in TARGETS:
                with self.subTest(target=target):
                    result = run_case(temp, "namespace_module", root_source, target)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)
                    self.assertEqual(result.stderr, "")
                    result = run_case(temp, "namespace_module_error",
                                      root_source + 'lib.broken()\n', target)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('bad + operands: 1, module-marker', result.stderr)
                    self.assertIn('lib.lt:23', result.stderr)
                    self.assertNotIn('Traceback (most recent call last)', result.stderr)

    def test_native_non_handle_attribute_assignment_is_rejected(self):
        self.run_both_successfully(
            "namespace_setattr",
            'for value in [nil, true, 17, "text", [], {}]:\n'
            '    try:\n        __wsetattr(value, "field", 1)\n'
            '    catch err:\n        say err\n',
            "cannot set attribute on nil\n"
            "cannot set attribute on bool\n"
            "cannot set attribute on num\n"
            "cannot set attribute on str\n"
            "cannot set attribute on list\n"
            "cannot set attribute on map\n")

    def test_os_and_source_map_bindings_preserve_mapped_runtime_diagnostics(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-namespace-error-") as name:
            temp = Path(name)
            for target in TARGETS:
                with self.subTest(target=target):
                    result = run_case(temp, "namespace_diagnostic",
                                      SHADOWED_DIAGNOSTIC_SOURCE, target)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Latent runtime error:", result.stderr)
                    self.assertIn("bad + operands: 1, bad", result.stderr)
                    self.assertIn("namespace_diagnostic.lt:7", result.stderr)
                    self.assertNotIn("Traceback (most recent call last)", result.stderr)

    def test_diagnostic_decodes_source_function_name(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-function-error-") as name:
            temp = Path(name)
            for target in TARGETS:
                with self.subTest(target=target):
                    result = run_case(temp, "namespace_function_diagnostic",
                                      FUNCTION_DIAGNOSTIC_SOURCE, target)
                    self.assertNotEqual(result.returncode, 0)
                    if target == "py":
                        self.assertIn("in readable", result.stderr)
                    self.assertNotIn("_lt_user_", result.stderr)
                    self.assertNotIn("Traceback (most recent call last)", result.stderr)

    def test_unmapped_host_diagnostic_is_concise_and_traceback_free(self):
        error = RuntimeError("host-level marker")
        prelude_globals = {}
        exec(gen_py.PRELUDE, prelude_globals)
        report_uncaught = prelude_globals["_wv_report_uncaught"]
        stderr = StringIO()
        with redirect_stderr(stderr):
            report_uncaught(error, {}, "host.lt", "generated.py")
        rendered = stderr.getvalue()
        self.assertIn("Latent runtime error: RuntimeError: host-level marker", rendered)
        self.assertNotIn("Traceback", rendered)

        class FailingMap:
            called = False

            def get(self, _line):
                self.called = True
                raise RuntimeError("diagnostic-map marker")

        try:
            raise RuntimeError("host-level marker")
        except RuntimeError as raised:
            error = raised
        failing_map = FailingMap()
        stderr = StringIO()
        with redirect_stderr(stderr):
            report_uncaught(error, failing_map, "host.lt", __file__)
        rendered = stderr.getvalue()
        self.assertTrue(failing_map.called)
        self.assertIn("Latent runtime error: RuntimeError: host-level marker", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_subprocess_binding_does_not_break_python_backend_java_interop(self):
        self.run_both_successfully("namespace_interop", JAVA_INTEROP_SOURCE,
                                   "namespace-ok\n")


if __name__ == "__main__":
    unittest.main()
