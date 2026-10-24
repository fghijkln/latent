"""Pinned reproducers for known-open fuzz findings.

strict=True: when a fix lands the test XPASSes, which fails the run and tells
you to delete the xfail marker so the case becomes a hard regression test.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "playground"))
import latent
import gen_java
import app

CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus")


def _src(name):
    with open(os.path.join(CORPUS, name), encoding="utf-8") as f:
        return f.read()


@pytest.mark.xfail(strict=True, reason="R1: bool arithmetic yields native int")
@pytest.mark.parametrize("name", ["r1_bool_add.lt", "r1_neg_bool.lt"])
def test_r1_bool_arithmetic_is_rejected(name):
    r = app.run_program(_src(name))
    assert r.get("ok") is False, f"{name} ran: {r.get('stdout')!r}"


@pytest.mark.xfail(strict=True, reason="F1: block-nested fn crashes gen_java")
@pytest.mark.parametrize("name", ["f1_if_fn.lt", "f1_for_fn.lt"])
def test_f1_java_codegen_handles_block_nested_fn(name):
    prog, err = latent.compile_src(_src(name), "main.lt")
    assert err is None
    gen_java.generate(prog, "Main", source_path="main.lt")
