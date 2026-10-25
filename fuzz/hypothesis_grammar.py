"""Property-based fuzzing of the Latent compiler with Hypothesis.

Run as pytest. Profiles:
  ci       - fixed derandomized seed, bounded examples (PR gate, ~1 min)
  nightly  - many random examples, longer deadline
Select with HYPOTHESIS_PROFILE=ci|nightly (default: ci).

Oracles:
  test_compile_never_crashes - both backends compile without crashing
  test_backends_agree        - clean programs match in status and output
Fixed R1/F1/F2 findings are hard regression tests in test_known_findings.py.
"""
import os
import subprocess
import sys
import tempfile

import pytest
from hypothesis import given, settings, HealthCheck, strategies as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "playground"))

import gen_py
import gen_java
import latent
import app  # playground

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
        raise AssertionError(f"Java runner received rejected source: {err}\n{src}")
    try:
        code = gen_java.generate(prog, "Main", source_path="main.lt")
    except Exception as e:
        raise AssertionError(
            f"Java code generation failed: {type(e).__name__}: {e}\n{src}") from e
    rt = os.path.join(ROOT, "runtime")
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "Main.java"), "w").write(code)
        jc = subprocess.run(
            [JAVAC, "-d", d, os.path.join(d, "Main.java"),
             f"{rt}/LtRt.java", f"{rt}/JReflect.java"],
            check=False, capture_output=True, text=True, timeout=120)
        if jc.returncode != 0:
            raise AssertionError(
                f"javac exited {jc.returncode}\n{jc.stdout}{jc.stderr}"
                f"\n--- source ---\n{src}")
        jr = subprocess.run([JAVA, "-cp", d, "Main"],
                            check=False, capture_output=True, text=True, timeout=60)
        return jr


# ---------------- properties ----------------

@given(program)
def test_compile_never_crashes(src):
    _compile(src)


@settings(max_examples=int(os.environ.get("FUZZ_DIFF_EXAMPLES", "60")))
@given(program)
def test_backends_agree(src):
    py, java = _compile(src)
    if py != java:
        raise AssertionError(
            f"backend compile-state divergence: py={py}, java={java}\n{src}")
    if py != "ok":
        return
    ok, py_out, py_err = _run_py(src)
    if not ok:
        raise AssertionError(
            f"Python runner failed after successful codegen\n"
            f"stdout={py_out}\nstderr={py_err}\n--- source ---\n{src}")
    res = _run_java(src)
    if (res.returncode, res.stdout, res.stderr) != (0, py_out, py_err):
        raise AssertionError(
            f"backend outcome divergence\n--- PY ---\n"
            f"status=0\nstdout={py_out}\nstderr={py_err}\n--- JAVA ---\n"
            f"status={res.returncode}\nstdout={res.stdout}\n"
            f"stderr={res.stderr}\n--- source ---\n{src}")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
