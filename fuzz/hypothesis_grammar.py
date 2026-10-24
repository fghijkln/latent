"""Property-based fuzzing of the Latent compiler with Hypothesis.

Run as pytest. Profiles:
  ci       - fixed derandomized seed, bounded examples (PR gate, ~1 min)
  nightly  - many random examples, longer deadline
Select with HYPOTHESIS_PROFILE=ci|nightly (default: ci).

Oracles:
  test_compile_never_crashes      - no undeclared exception from the compiler
  test_backends_agree             - py-clean programs match java stdout
  test_no_sandbox_escape          - no os/uid escape marker via run_program
Known-open findings (R1/F1/F2) are pinned xfail; see fuzz/README.md.
"""
import os
import subprocess
import sys
import tempfile

import pytest
from hypothesis import given, reject, settings, HealthCheck, strategies as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "playground"))

import lex
import parse as parse_mod
import desugar
import semant
import gen_py
import gen_java
import latent
import app  # playground

DECLARED = (lex.LexError, parse_mod.ParseError, desugar.DesugarError,
            semant.SemantError)

settings.register_profile(
    "ci", max_examples=int(os.environ.get("FUZZ_CI_EXAMPLES", "400")),
    deadline=None, derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                           HealthCheck.filter_too_much])
settings.register_profile(
    "nightly", max_examples=int(os.environ.get("FUZZ_NIGHTLY_EXAMPLES", "20000")),
    deadline=None, derandomize=False,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                           HealthCheck.filter_too_much])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

# ---------------- program-shaped strategy ----------------

names = st.sampled_from(["a", "b", "c", "x", "y", "n", "obj", "s", "m", "xs"])
attrs = st.sampled_from(
    ["x", "owner", "balance", "__class__", "__base__", "__dict__",
     "__globals__", "__subclasses__", "__name__", "new", "show"])
literals = st.sampled_from(
    ["0", "1", "-1", "2.5", "true", "false", "nil", '"a"', '"${x}"',
     "[]", "[1, 2]", "{}", '{"k": 1}', '""', "999999999999999999999"])
binops = st.sampled_from(
    ["+", "-", "*", "/", "%", "**", "==", "!=", "<", ">", "and", "or"])
builtins_ = st.sampled_from(["len", "str", "int", "range", "keys"])


def _expr():
    base = st.one_of(literals, names)
    return st.recursive(
        base,
        lambda c: st.one_of(
            st.builds(lambda a, o, b: f"({a} {o} {b})", c, binops, c),
            st.builds(lambda a: f"(-{a})", c),
            st.builds(lambda a: f"(not {a})", c),
            st.builds(lambda f, a: f"{f}({a})", builtins_, c),
            st.builds(lambda a, at: f"{a}.{at}", c, attrs),
            st.builds(lambda a, b: f"{a}[{b}]", c, c),
            st.builds(lambda a, at, b: f"{a}.{at}({b})", c, attrs, c),
        ),
        max_leaves=20,
    )


expr = _expr()


def _indent(block, pad="    "):
    return "\n".join(pad + line for line in block.split("\n"))


def _stmt():
    simple = st.one_of(
        st.builds(lambda n, e: f"{n} = {e}", names, expr),
        st.builds(lambda e: f"say {e}", expr),
    )
    return st.recursive(
        simple,
        lambda c: st.one_of(
            st.builds(lambda e, b: f"if {e}:\n{_indent(b)}", expr, c),
            st.builds(lambda n, e, b: f"for {n} in {e}:\n{_indent(b)}",
                      names, expr, c),
            st.builds(lambda b, h: f"try:\n{_indent(b)}\ncatch e:\n{_indent(h)}",
                      c, c),
            st.builds(lambda n, b: f"fn {n}():\n{_indent(b)}", names, c),
        ),
        max_leaves=8,
    )


# Bind every generator name up front so programs get past semant's
# undefined-name check and actually reach codegen and execution.
PRELUDE = ('a = 1\nb = 2.5\nc = "s"\nx = [1, 2]\ny = {"k": 1}\nn = nil\n'
           'obj = true\ns = "h\u00e9llo"\nm = {"k": 1}\nxs = [3, 1, 2]\n')

def _guard(stmt):
    # Each top-level statement in its own try so one runtime error does not end
    # the program; both backends must then agree on every line, ERR included.
    return f"try:\n{_indent(stmt)}\ncatch err:\n    say \"ERR\""


program = st.lists(_stmt(), min_size=1, max_size=5).map(
    lambda ss: PRELUDE + "\n".join(_guard(x) for x in ss) + "\n")


# ---------------- helpers ----------------

JAVA = os.environ.get("LATENT_JAVA", "java")
JAVAC = os.environ.get("LATENT_JAVAC", "javac")


def _compile(src):
    """(py_outcome, java_outcome); outcome is 'ok'/'declined'/'recursion'.
    Raises AssertionError on an undeclared compiler/codegen exception."""
    prog, err = latent.compile_src(src, "main.lt")
    if err:
        return "declined", "declined"
    out = {}
    for be, gen in (("py", lambda p: gen_py.generate(p, source_path="main.lt")),
                    ("java", lambda p: gen_java.generate(p, "Main",
                                                         source_path="main.lt"))):
        try:
            gen(prog)
            out[be] = "ok"
        except RecursionError:
            out[be] = "recursion"
        except DECLARED:
            out[be] = "declined"
        except Exception as e:  # pragma: no cover - this is the finding
            raise AssertionError(
                f"{be} backend raised undeclared {type(e).__name__}: {e}\n"
                f"--- source ---\n{src}") from e
    return out["py"], out["java"]


def _run_py(src):
    r = app._run_program(src)
    out = r.get("stdout", "")
    if isinstance(out, (bytes, bytearray)):
        out = bytes(out).decode("utf-8", "ignore")
    return r.get("ok"), out, (str(r.get("stderr", "")) + str(r.get("error", "")))


def _run_java(src):
    prog, err = latent.compile_src(src, "main.lt")
    if err:
        return None
    try:
        code = gen_java.generate(prog, "Main", source_path="main.lt")
    except Exception:
        return None
    rt = os.path.join(ROOT, "runtime")
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "Main.java"), "w").write(code)
        jc = subprocess.run(
            [JAVAC, "-d", d, os.path.join(d, "Main.java"),
             f"{rt}/LtRt.java", f"{rt}/JReflect.java"],
            capture_output=True, text=True, timeout=120)
        if jc.returncode != 0:
            return ("javac_err", jc.stderr)
        jr = subprocess.run([JAVA, "-cp", d, "Main"],
                            capture_output=True, text=True, timeout=60)
        return ("ran", jr.stdout, jr.stderr)


def _is_f1(err):
    return "java backend raised undeclared KeyError" in str(err)


# ---------------- properties ----------------

@given(program)
def test_compile_never_crashes(src):
    try:
        _compile(src)
    except AssertionError as e:
        # F1 (known-open, pinned in test_known_findings.py): skip the sample
        # and keep exploring instead of stopping the run at the first hit.
        if _is_f1(e):
            reject()
        raise


@settings(max_examples=int(os.environ.get("FUZZ_DIFF_EXAMPLES", "60")))
@given(program)
def test_backends_agree(src):
    try:
        py, java = _compile(src)
    except AssertionError as e:
        if _is_f1(e):
            reject()
        raise
    if py != "ok" or java != "ok":
        return
    ok, py_out, _ = _run_py(src)
    if not ok:
        return
    res = _run_java(src)
    if res is None or res[0] != "ran":
        return
    if res[1] != py_out:
        # Known class F2/R1 only: the Java backend rejects bool arithmetic that
        # the Python backend lets through. Any other divergence is a new bug.
        if "operand: bool" in res[2]:
            pytest.xfail("F2/R1: bool-arithmetic backend divergence")
        raise AssertionError(
            f"backend divergence\n--- PY ---\n{py_out}\n--- JAVA ---\n"
            f"{res[1]}{res[2]}\n--- source ---\n{src}")


@given(program)
def test_no_sandbox_escape(src):
    prog, err = latent.compile_src(src, "main.lt")
    if err:
        return
    try:
        gen_py.generate(prog, source_path="main.lt")
    except Exception:
        return
    ok, out, errtext = _run_py(src)
    assert "PWNED" not in (out + errtext) and "uid=" not in (out + errtext), \
        f"sandbox escape marker in output\n--- source ---\n{src}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
