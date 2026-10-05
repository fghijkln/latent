#!/usr/bin/env python3
"""Latent dual-backend differential test: same .lt -> py & java must agree."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LATENTC = os.path.join(ROOT, "latent.py")
TESTS = os.path.join(ROOT, "tests")
OUT = os.path.join(ROOT, "out")

POSITIVE = ["hello", "fib", "loop", "data", "truthy", "scope",
            "str_interp", "py_basic", "py_numpy", "py_lazy",
            "java_basic", "java_lazy", "mixed", "indexing",
            "class_basic", "setassign", "try_basic"]
NEG_COMPILE = ["err_undef", "err_arity", "err_readbefore", "err_exprstmt",
               "err_break", "err_dupmethod", "err_assign_target"]
NEG_RUNTIME = ["py_lazy_use", "java_lazy_use", "err_index_range",
               "err_index_key", "err_nomethod", "err_nofield"]

COOKBOOK_DIR = os.path.join(ROOT, "cookbook")
COOKBOOK = ["py_math", "py_datetime", "py_json", "py_re", "py_os",
            "java_strings", "java_collections", "java_time", "java_nio",
            "java_bigdecimal", "mixed_io", "classes"]


def run(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return "timeout", "", ""


def compile_and_run(name, target, srcdir=TESTS):
    src = os.path.join(srcdir, name + ".lt")
    outdir = os.path.join(OUT, "t_" + name + "_" + target)
    os.makedirs(outdir, exist_ok=True)
    rc, so, se = run([sys.executable, LATENTC, src, "-t", target, "-o", outdir])
    if rc != 0:
        return ("compile-fail", so, se)
    if target == "py":
        rc, so, se = run([sys.executable, os.path.join(outdir, name + ".py")])
    else:
        rc, so, se = run(["java", "-cp", outdir, cap(name)], cwd=outdir)
    return (rc, so, se)


def cap(name):
    s = "".join(c if (c.isalnum() or c == "_") else "_" for c in name)
    return (s[0].upper() + s[1:]) if s else "_"


def main():
    fails = 0

    print("== positive: py vs java must produce identical stdout, exit 0 ==")
    for name in POSITIVE:
        py = compile_and_run(name, "py")
        jv = compile_and_run(name, "java")
        ok = (py[0] == 0 and jv[0] == 0 and py[1] == jv[1])
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={py[0]} out={py[1]!r} err={py[2][-500:]}")
            print(f"  java: rc={jv[0]} out={jv[1]!r} err={jv[2][-500:]}")

    print("== negative compile: latent must reject ==")
    for name in NEG_COMPILE:
        src = os.path.join(TESTS, name + ".lt")
        rc, so, se = run([sys.executable, LATENTC, src, "-t", "py",
                          "-o", os.path.join(OUT, "t_" + name)])
        ok = rc != 0
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else " (compiled!)"))
        if not ok:
            fails += 1
        else:
            print(f"  msg: {(so + se).strip().splitlines()[0]}")

    print("== negative runtime: must fail on both backends ==")
    for name in NEG_RUNTIME:
        py = compile_and_run(name, "py")
        jv = compile_and_run(name, "java")
        ok = (py[0] not in (0, "compile-fail", "timeout") and py[0] != 0 and
              jv[0] not in (0, "compile-fail", "timeout") and jv[0] != 0)
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={py[0]} err={py[2][-300:]}")
            print(f"  java: rc={jv[0]} err={jv[2][-300:]}")

    print("== cookbook: py vs java must produce identical stdout, exit 0 ==")
    for name in COOKBOOK:
        py = compile_and_run(name, "py", COOKBOOK_DIR)
        jv = compile_and_run(name, "java", COOKBOOK_DIR)
        ok = (py[0] == 0 and jv[0] == 0 and py[1] == jv[1])
        print(("PASS " if ok else "FAIL ") + "cookbook/" + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={py[0]} out={py[1]!r} err={py[2][-500:]}")
            print(f"  java: rc={jv[0]} out={jv[1]!r} err={jv[2][-500:]}")

    total = len(POSITIVE) + len(NEG_COMPILE) + len(NEG_RUNTIME) + len(COOKBOOK)
    print(f"\n{total - fails} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
