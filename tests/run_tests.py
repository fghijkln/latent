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
               "err_index_key", "err_nomethod", "err_nofield",
               "err_location_top", "err_location_nested",
               "err_location_method", "err_location_loop",
               "err_location_throw", "err_location_try",
               "err_location_py_handle", "err_location_java_handle"]

RUNTIME_SOURCE_LINES = {
    "py_lazy_use": [2], "java_lazy_use": [2],
    "err_index_range": [2], "err_index_key": [2],
    "err_nomethod": [6], "err_nofield": [6],
    "err_location_top": [2], "err_location_nested": [2, 4, 5],
    "err_location_method": [3, 5], "err_location_loop": [2],
    "err_location_throw": [1], "err_location_try": [4],
    "err_location_py_handle": [2], "err_location_java_handle": [2],
}

COOKBOOK_DIR = os.path.join(ROOT, "cookbook")
COOKBOOK = ["py_math", "py_datetime", "py_json", "py_re", "py_os",
            "java_strings", "java_collections", "java_time", "java_nio",
            "java_bigdecimal", "mixed_io", "classes", "pipeline"]

TRY_BASIC_STDOUT = ("caught: index out of range: 5\n"
                    "k=key not found: zz\n"
                    "e=negative!\n"
                    "no error\n"
                    "42\n"
                    "5\n"
                    "-1\n"
                    "n=outer: inner\n"
                    "javaerr\n"
                    "pyerr\n")


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


def has_source_locations(name, result):
    """Require a useful mapped diagnostic without coupling to backend wording."""
    _, _, stderr = result
    return ("Latent runtime error:" in stderr and
            all(f"{name}.lt:{line}" in stderr
                for line in RUNTIME_SOURCE_LINES[name]))


def main():
    fails = 0

    print("== positive: py vs java must produce identical stdout, exit 0 ==")
    for name in POSITIVE:
        py = compile_and_run(name, "py")
        jv = compile_and_run(name, "java")
        ok = (py[0] == 0 and jv[0] == 0 and py[1] == jv[1] and
              not py[2] and not jv[2])
        if name == "try_basic":
            ok = ok and py[1] == TRY_BASIC_STDOUT and jv[1] == TRY_BASIC_STDOUT
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
              jv[0] not in (0, "compile-fail", "timeout") and jv[0] != 0 and
              has_source_locations(name, py) and has_source_locations(name, jv))
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={py[0]} mapped={has_source_locations(name, py)} "
                  f"err={py[2][-500:]}")
            print(f"  java: rc={jv[0]} mapped={has_source_locations(name, jv)} "
                  f"err={jv[2][-500:]}")

    print("== cookbook: py vs java must produce identical stdout, exit 0 ==")
    for name in COOKBOOK:
        py = compile_and_run(name, "py", COOKBOOK_DIR)
        jv = compile_and_run(name, "java", COOKBOOK_DIR)
        ok = (py[0] == 0 and jv[0] == 0 and py[1] == jv[1] and
              not py[2] and not jv[2])
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
