#!/usr/bin/env python3
"""Focused cross-backend regressions for Java-backend audit fixes."""
from __future__ import annotations

import tempfile
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
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


if __name__ == "__main__":
    unittest.main()
