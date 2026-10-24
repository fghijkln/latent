#!/usr/bin/env python3
"""Cross-backend regression for Java-generated identifier collisions."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import test_p18_callbacks as harness

SOURCE = '''\
args = "global-args"
args_ = "global-args-suffix"
LtRt = "global-LtRt"
LtRt_ = "global-LtRt-suffix"
System = "global-System"
System_ = "global-System-suffix"
_lt_user_args = "source-prefix-name"
fn inspect(args, LtRt, System):
    return args + ":" + LtRt + ":" + System
say args
say args_
say LtRt
say LtRt_
say System
say System_
say _lt_user_args
say inspect("parameter-args", "parameter-LtRt", "parameter-System")
'''
EXPECTED_STDOUT = (
    "global-args\n"
    "global-args-suffix\n"
    "global-LtRt\n"
    "global-LtRt-suffix\n"
    "global-System\n"
    "global-System-suffix\n"
    "source-prefix-name\n"
    "parameter-args:parameter-LtRt:parameter-System\n"
)
CLASS_SOURCE = '''\
class System:
    fn args(self, LtRt, System):
        self.args = LtRt
        self.System = System
        return self.args + ":" + self.System
class LtRt:
    fn System(self, args):
        self.LtRt = args
        return self.LtRt
class A_B:
    fn C(self):
        return "first-helper"
class A:
    fn B_C(self):
        return "second-helper"
system = System.new()
say system.args("attribute-args", "parameter-System")
runtime = LtRt.new()
say runtime.System("attribute-LtRt")
say A_B.new().C()
say A.new().B_C()
'''
EXPECTED_CLASS_STDOUT = (
    "attribute-args:parameter-System\n"
    "attribute-LtRt\n"
    "first-helper\n"
    "second-helper\n"
)
UNICODE_IDENTIFIER_SOURCE = '''\
x² = "value"
fn read²(x²):
    return x² + "!"
class Class²:
    fn value(self):
        return x²
say read²(x²)
say Class².new().value()
'''
EXPECTED_UNICODE_STDOUT = "value!\nvalue\n"


class TestAuditJavaIdentifiers(unittest.TestCase):
    def test_generated_java_reserved_names_work_on_both_backends(self):
        with tempfile.TemporaryDirectory(prefix="latent-java-identifiers-") as tmp:
            root = Path(tmp)
            source = root / "valid_identifiers.lt"
            source.write_text(SOURCE, encoding="utf-8")
            for target in ("py", "java"):
                with self.subTest(target=target):
                    outdir = root / target
                    outdir.mkdir()
                    result = harness.compile_and_run(source, target, outdir)
                    self.assertEqual(
                        result.returncode, 0,
                        f"{target}: stdout={result.stdout!r}, "
                        f"stderr={result.stderr[-1600:]!r}")
                    self.assertEqual(result.stdout, EXPECTED_STDOUT)
                    self.assertEqual(result.stderr, "")

    def test_class_methods_and_attributes_keep_source_names(self):
        with tempfile.TemporaryDirectory(
                prefix="latent-java-class-identifiers-") as tmp:
            root = Path(tmp)
            source = root / "class_identifiers.lt"
            source.write_text(CLASS_SOURCE, encoding="utf-8")
            for target in ("py", "java"):
                with self.subTest(target=target):
                    outdir = root / target
                    outdir.mkdir()
                    result = harness.compile_and_run(source, target, outdir)
                    self.assertEqual(
                        result.returncode, 0,
                        f"{target}: stdout={result.stdout!r}, "
                        f"stderr={result.stderr[-1600:]!r}")
                    self.assertEqual(result.stdout, EXPECTED_CLASS_STDOUT)
                    self.assertEqual(result.stderr, "")

    def test_non_java_identifier_names_are_mangled_without_language_change(self):
        with tempfile.TemporaryDirectory(
                prefix="latent-java-unicode-identifiers-") as tmp:
            root = Path(tmp)
            source = root / "unicode_identifiers.lt"
            source.write_text(UNICODE_IDENTIFIER_SOURCE, encoding="utf-8")
            for target in ("py", "java"):
                with self.subTest(target=target):
                    outdir = root / target
                    outdir.mkdir()
                    result = harness.compile_and_run(source, target, outdir)
                    self.assertEqual(
                        result.returncode, 0,
                        f"{target}: stdout={result.stdout!r}, "
                        f"stderr={result.stderr[-1600:]!r}")
                    self.assertEqual(result.stdout, EXPECTED_UNICODE_STDOUT)
                    self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
