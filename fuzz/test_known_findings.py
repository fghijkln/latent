"""Hard regression tests for the fixed R1/F1 compiler findings."""
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


@pytest.mark.parametrize(("name", "message"), [
    ("r1_bool_add.lt", "bad + operands: true, false"),
    ("r1_neg_bool.lt", "bad unary - operand: bool"),
])
def test_r1_bool_arithmetic_is_rejected(name, message):
    r = app.run_program(_src(name))
    assert r.get("ok") is False, f"{name} ran: {r.get('stdout')!r}"
    assert message in (r.get("error", "") + r.get("stderr", ""))


def test_r1_escape_probe_cannot_read_host_uid():
    r = app.run_program(_src("r1_escape_probe.lt"))
    output = "".join(str(r.get(key, "")) for key in ("stdout", "stderr", "error"))
    assert r.get("ok") is False
    assert "uid=" not in output


@pytest.mark.parametrize("name", ["f1_if_fn.lt", "f1_for_fn.lt"])
def test_f1_java_codegen_handles_block_nested_fn(name):
    prog, err = latent.compile_src(_src(name), "main.lt")
    assert err is None
    gen_java.generate(prog, "Main", source_path="main.lt")
