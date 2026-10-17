#!/usr/bin/env python3
"""Post-Stage0 fixes: current dual backends and fixed-seed Stage1/2 behavior.

These cases intentionally differ from the immutable Stage0 bug behavior. They
supplement, rather than relax or replace, its existing projection corpus.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests._safe_test_env import clean_test_environment
from tests.test_bootstrap_smoke import checked, extract_stage0
from lex import KEYWORDS, lex
from parse import HOST_RESERVED_NAMES, ParseError, parse


class CompilerRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="latent-fixes-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        seed = cls.root / "seed"
        extract_stage0(seed)
        compiler = seed / "latent.py"
        cls.compilers = [("current-py", ROOT / "latent.py", "py")]
        if shutil.which("java") and shutil.which("javac"):
            cls.compilers.append(("current-java", ROOT / "latent.py", "java"))
        else:
            print("SKIP Java regression leg: java/javac unavailable", flush=True)
        for stage in (1, 2):
            output = cls.root / f"stage{stage}"
            checked([sys.executable, str(compiler),
                     str(ROOT / "bootstrap/compiler.lt"), "-t", "py",
                     "-o", str(output)], cwd=cls.root)
            shutil.copyfile(ROOT / "bootstrap/latent_host.py", output / "latent_host.py")
            compiler = output / "compiler.py"
            cls.compilers.append((f"stage{stage}", compiler, "py"))
        cls.counter = 0

    def check_source(self, source, *, expected=None, marker=None, location=None):
        for label, compiler, target in self.compilers:
            with self.subTest(compiler=label, source=source):
                type(self).counter += 1
                case = self.root / f"case{self.counter}.lt"
                case.write_text(source, encoding="utf-8")
                output = self.root / f"out{self.counter}"
                result = subprocess.run(
                    [sys.executable, str(compiler), str(case), "-t", target,
                     "-o", str(output)],
                    cwd=self.root, env=clean_test_environment(),
                    text=True, capture_output=True, timeout=90)
                if marker is None:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if target == "py":
                        command = [sys.executable, str(output / (case.stem + ".py"))]
                    else:
                        command = ["java", "-cp", str(output), case.stem.capitalize()]
                    result = subprocess.run(command, cwd=output,
                                            env=clean_test_environment(),
                                            text=True, capture_output=True, timeout=90)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)
                    self.assertEqual(result.stderr, "")
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(marker, result.stderr)
                    if location:
                        self.assertIn(location, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertFalse(output.exists(), result.stderr)

    def test_keyword_bindings_are_source_errors(self):
        for source, location in (
            ("fn is(a, b):\n    return a\nsay is(1, 2)\n", ":1:4:"),
            ("lambda = 1\nsay lambda\n", ":1:1:"),
            ("fn take(pass):\n    return pass\n", ":1:9:"),
            ("class with:\n    fn init(self):\n        return nil\n", ":1:7:"),
            ("fn take(*with):\n    return nil\n", ":1:10:"),
            ("fn take(**yield):\n    return nil\n", ":1:11:"),
            ("for async in []:\n    say 1\n", ":1:5:"),
            ("try:\n    throw 1\ncatch await:\n    say 2\n", ":3:7:"),
            ('import "missing.lt" as def\n', ":1:24:"),
        ):
            self.check_source(source, marker="reserved", location=location)

    def test_safe_suffixes_builtin_and_host_names_remain_usable(self):
        self.check_source('is_ = 7\nis__ = 8\nnew_ = 9\n'
                          'say [is_, is__, new_, int(2.5)]\n'
                          'items = java "java.util.ArrayList"\n'
                          'say items.new().size()\n',
                          expected="[7, 8, 9, 2]\n0\n")

    def test_java_keyword_names_do_not_collide(self):
        self.check_source('new = 1\nnew_ = 2\nstatic = 3\nstatic_ = 4\n'
                          'public = 5\n_ = 6\nvar = 7\nvar_ = 8\n'
                          '_lt_user_6e6577 = 9\n'
                          'fn take(default, final):\n    return default + final\n'
                          'say [new, new_, static, static_, public, _, var, var_, '
                          '_lt_user_6e6577, take(10, 11)]\n',
                          expected="[1, 2, 3, 4, 5, 6, 7, 8, 9, 21]\n")

    def test_all_target_reserved_bindings_reject_without_suffix_collisions(self):
        # Independent Python hard-keyword oracle, not the implementation table.
        expected = frozenset("False None True and as assert async await break class "
                             "continue def del elif else except finally for from "
                             "global if import in is lambda nonlocal not or pass "
                             "raise return try while with yield".split())
        self.assertEqual(HOST_RESERVED_NAMES, expected)
        for name in sorted(HOST_RESERVED_NAMES - KEYWORDS):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ParseError, "1:1:.*reserved"):
                    parse(lex(f"{name} = 1\n"))
                parse(lex(f"{name}_ = 1\n{name}__ = 2\n"))
        for declaration, location in (("global is", ":2:12:"), ("nonlocal lambda", ":2:14:")):
            self.check_source(f"fn outer():\n    {declaration}\n    return nil\n",
                              marker="reserved", location=location)

    def test_nested_builtin_static_priority_and_alias(self):
        self.check_source('fn outer():\n'
                          '    say len([1, 2])\n'
                          '    fn len(a, b):\n        return a + b\n'
                          '    alias = len\n    say alias(4, 5)\n'
                          '    return len([1, 2, 3])\nsay outer()\n',
                          expected="2\n9\n3\n")

    def test_shadowed_builtin_call_rejections(self):
        for call, marker in (
            ("len(1, 2)", "len() takes 1..1 args, got 2"),
            ("len()", "len() takes 1..1 args, got 0"),
            ("len(a=[1])", "does not accept named arguments"),
            ("len(*[1])", "does not support argument unpacking"),
            ('len(**{"a": 1})', "does not support argument unpacking"),
        ):
            self.check_source('fn outer():\n    fn len(a, b):\n'
                              f'        return a\n    return {call}\nsay outer()\n',
                              marker=marker, location=":4:15:")

    def test_plain_local_read_before_assignment_still_fails(self):
        self.check_source('fn outer():\n    value = len\n'
                          '    fn len(a):\n        return a\n    return value\n',
                          marker="local 'len' read before assignment", location=":2:13:")

    def test_power_overflow_sign_and_underflow(self):
        self.check_source('say 10.0 ** 400.0\n'
                          'say (-10.0) ** 401.0\n'
                          'say (-10.0) ** 400.0\n'
                          'say 10.0 ** (-400.0)\n'
                          'say (-10.0) ** (-401.0)\n'
                          'say (-2.0) ** 0.5\n',
                          expected="inf\n-inf\ninf\n0\n0\nnan\n")

    def test_exponent_notation_is_not_part_of_decimal_grammar(self):
        for value in ("1e308", "1e+2", "1E-2", "1e", "1e+", "1e--2"):
            self.check_source(f"say {value}\n", marker="expected NEWLINE", location=":1:6")


if __name__ == "__main__":
    unittest.main(verbosity=2)
