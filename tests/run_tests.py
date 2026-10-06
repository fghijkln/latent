#!/usr/bin/env python3
"""Latent dual-backend differential test: same .lt -> py & java must agree."""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LATENTC = os.path.join(ROOT, "latent.py")
TESTS = os.path.join(ROOT, "tests")
OUT = os.path.join(ROOT, "out")

POSITIVE = ["hello", "fib", "loop", "data", "truthy", "scope",
            "str_interp", "py_basic", "py_numpy", "py_lazy",
            "java_basic", "java_lazy", "mixed", "indexing",
            "class_basic", "inheritance", "setassign", "try_basic"]
MODULE_POSITIVE = ["modules/app/main", "modules/same/main",
                   "modules/inheritance/main"]
NEG_COMPILE = ["err_undef", "err_arity", "err_readbefore", "err_exprstmt",
               "err_break", "err_dupmethod", "err_assign_target"]
NEG_INHERITANCE_COMPILE = [
    "inheritance_negative/unknown", "inheritance_negative/not_class",
    "inheritance_negative/self_cycle", "inheritance_negative/cycle",
    "inheritance_negative/super_top", "inheritance_negative/super_no_parent",
    "inheritance_negative/super_missing_method",
    "inheritance_negative/super_wrong_receiver",
    "inheritance_negative/override_arity",
]
INHERITANCE_COMPILE_MARKERS = {
    "inheritance_negative/unknown": "unknown parent class",
    "inheritance_negative/not_class": "is not a Latent class",
    "inheritance_negative/self_cycle": "inheritance cycle",
    "inheritance_negative/cycle": "inheritance cycle",
    "inheritance_negative/super_top": "super is only valid",
    "inheritance_negative/super_no_parent": "has no parent for super",
    "inheritance_negative/super_missing_method": "has no method 'missing'",
    "inheritance_negative/super_wrong_receiver": "pass the current receiver first",
    "inheritance_negative/override_arity": "override Child.run must take",
}
INHERITANCE_ERROR_LOCATIONS = {
    "inheritance_negative/unknown": ["unknown.lt:1:"],
    "inheritance_negative/not_class": ["not_class.lt:4:"],
    "inheritance_negative/self_cycle": ["self_cycle.lt:1:"],
    "inheritance_negative/cycle": ["cycle.lt:5:"],
    "inheritance_negative/super_top": ["super_top.lt:1:"],
    "inheritance_negative/super_no_parent": ["super_no_parent.lt:3:"],
    "inheritance_negative/super_missing_method": ["super_missing_method.lt:7:"],
    "inheritance_negative/super_wrong_receiver": ["super_wrong_receiver.lt:7:"],
    "inheritance_negative/override_arity": ["override_arity.lt:6:"],
}
NEG_MODULE_COMPILE = [
    "modules/negative/missing/main",
    "modules/negative/missing_export/main",
    "modules/negative/private/main",
    "modules/negative/late/main",
    "modules/negative/nested/main",
    "modules/negative/bad_extension/main",
    "modules/negative/readonly/main",
    "modules/negative/namespace_value/main",
    "modules/negative/cycle/main",
    "modules/negative/collision/main",
    "modules/negative/leak/main",
    "modules/negative/function_value/main",
    "modules/negative/inherit_private/main",
    "modules/negative/inherit_not_class/main",
]
MODULE_COMPILE_MARKERS = {
    "modules/negative/missing/main": "cannot read module",
    "modules/negative/missing_export/main": "no exported name 'no_such_name'",
    "modules/negative/private/main": "module member '_secret' is private",
    "modules/negative/late/main": "imports must precede module code",
    "modules/negative/nested/main": "imports must be top-level",
    "modules/negative/bad_extension/main": "must end in .lt",
    "modules/negative/readonly/main": "exports are read-only",
    "modules/negative/namespace_value/main": "undefined name 'lib'",
    "modules/negative/cycle/main": "module import cycle",
    "modules/negative/collision/main": "conflicts with a local binding",
    "modules/negative/leak/main": "undefined name 'value'",
    "modules/negative/function_value/main": "functions are not values",
    "modules/negative/inherit_private/main": "module member '_Hidden' is private",
    "modules/negative/inherit_not_class/main": "is not a Latent class",
}
MODULE_ERROR_LOCATIONS = {
    "modules/negative/missing/main": ["main.lt:1:"],
    "modules/negative/missing_export/main": ["main.lt:2:"],
    "modules/negative/private/main": ["main.lt:2:"],
    "modules/negative/late/main": ["main.lt:2:"],
    "modules/negative/nested/main": ["main.lt:2:"],
    "modules/negative/bad_extension/main": ["main.lt:1:"],
    "modules/negative/readonly/main": ["main.lt:2:"],
    "modules/negative/cycle/main": ["b.lt:1:"],
    "modules/negative/collision/main": ["main.lt:1:"],
    "modules/negative/function_value/main": ["main.lt:2:"],
    "modules/negative/inherit_private/main": ["main.lt:2:"],
    "modules/negative/inherit_not_class/main": ["main.lt:2:"],
}
NEG_RUNTIME = ["py_lazy_use", "java_lazy_use", "err_index_range",
               "err_index_key", "err_nomethod", "err_nofield",
               "err_location_top", "err_location_nested",
               "err_location_method", "err_location_loop",
               "err_location_throw", "err_location_try",
               "err_location_py_handle", "err_location_java_handle",
               "modules/runtime/main", "inheritance_bad_init_arity"]
RUNTIME_SOURCE_LINES = {
    "py_lazy_use": [2], "java_lazy_use": [2],
    "err_index_range": [2], "err_index_key": [2],
    "err_nomethod": [6], "err_nofield": [6],
    "err_location_top": [2], "err_location_nested": [2, 4, 5],
    "err_location_method": [3, 5], "err_location_loop": [2],
    "err_location_throw": [1], "err_location_try": [4],
    "err_location_py_handle": [2], "err_location_java_handle": [2],
    "inheritance_bad_init_arity": [9],
}
RUNTIME_SOURCE_MARKERS = {
    "modules/runtime/main": ["main.lt:2", "lib.lt:3"],
}

COOKBOOK_DIR = os.path.join(ROOT, "cookbook")
COOKBOOK = ["py_math", "py_datetime", "py_json", "py_re", "py_os",
            "java_strings", "java_collections", "java_time", "java_nio",
            "java_bigdecimal", "mixed_io", "classes", "inheritance",
            "pipeline", "modules"]

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
MODULE_APP_STDOUT = "factor init\nshared init\n42\n2\n9\n"
MODULE_SAME_STDOUT = "33\nleft\nright\nleft\nright\n"
INHERITANCE_STDOUT = ("root-init\nroot-init\nvalue:inherited\n"
                      "root-middle-leaf\nvalue:shared:field\nisolated\n"
                      "empty\ntrue\nfalse\n<Leaf object>\n")
MODULE_INHERITANCE_STDOUT = "base:module+derived\nright\n<class Derived>\n"
COOKBOOK_INHERITANCE_STDOUT = "animal:Milo (shiba)\n<Dog object>\n"


def run(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return "timeout", "", ""


def cap(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    s = "".join(c if (c.isalnum() or c == "_") else "_" for c in stem)
    return (s[0].upper() + s[1:]) if s else "_"


def is_module_case(name, srcdir):
    return (name.startswith("modules/") or
            (os.path.realpath(srcdir) == os.path.realpath(COOKBOOK_DIR) and
             name == "modules"))


def compile_and_run(name, target, srcdir=TESTS):
    src = os.path.join(srcdir, name + ".lt")
    outdir = os.path.join(OUT, "t_" + name + "_" + target)
    os.makedirs(outdir, exist_ok=True)
    rc, so, se = run([sys.executable, LATENTC, src, "-t", target, "-o", outdir])
    if rc != 0:
        return ("compile-fail", so, se)
    if target == "py":
        cwd = tempfile.gettempdir() if is_module_case(name, srcdir) else None
        rc, so, se = run([sys.executable, os.path.join(outdir, os.path.basename(src)[:-3] + ".py")],
                         cwd=cwd)
    else:
        cwd = tempfile.gettempdir() if is_module_case(name, srcdir) else outdir
        rc, so, se = run(["java", "-cp", outdir, cap(name)], cwd=cwd)
    return (rc, so, se)


def has_source_locations(name, result):
    """Require mapped source locations without coupling to backend wording."""
    _, _, stderr = result
    if name in RUNTIME_SOURCE_MARKERS:
        return ("Latent runtime error:" in stderr and
                all(marker in stderr for marker in RUNTIME_SOURCE_MARKERS[name]))
    return ("Latent runtime error:" in stderr and
            all(f"{name}.lt:{line}" in stderr
                for line in RUNTIME_SOURCE_LINES[name]))


def main():
    fails = 0

    print("== positive: py vs java must produce identical stdout, exit 0 ==")
    for name in POSITIVE + MODULE_POSITIVE:
        py = compile_and_run(name, "py")
        jv = compile_and_run(name, "java")
        ok = (py[0] == 0 and jv[0] == 0 and py[1] == jv[1] and
              not py[2] and not jv[2])
        if name == "try_basic":
            ok = ok and py[1] == TRY_BASIC_STDOUT and jv[1] == TRY_BASIC_STDOUT
        elif name == "modules/app/main":
            ok = ok and py[1] == MODULE_APP_STDOUT
        elif name == "modules/same/main":
            ok = ok and py[1] == MODULE_SAME_STDOUT
        elif name == "inheritance":
            ok = ok and py[1] == INHERITANCE_STDOUT
        elif name == "modules/inheritance/main":
            ok = ok and py[1] == MODULE_INHERITANCE_STDOUT
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

    print("== negative inheritance compile: parent graph and super rules ==")
    for name in NEG_INHERITANCE_COMPILE:
        src = os.path.join(TESTS, name + ".lt")
        rc, so, se = run([sys.executable, LATENTC, src, "-t", "py",
                          "-o", os.path.join(OUT, "t_" + name)])
        detail = so + se
        ok = (rc != 0 and INHERITANCE_COMPILE_MARKERS[name] in detail and
              all(marker in detail for marker in
                  INHERITANCE_ERROR_LOCATIONS[name]))
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  expected={INHERITANCE_COMPILE_MARKERS[name]!r} "
                  f"out={so!r} err={se[-500:]}")

    print("== negative module compile: paths, visibility, graph, and namespace rules ==")
    for name in NEG_MODULE_COMPILE:
        src = os.path.join(TESTS, name + ".lt")
        rc, so, se = run([sys.executable, LATENTC, src, "-t", "py",
                          "-o", os.path.join(OUT, "t_" + name)])
        detail = so + se
        ok = (rc != 0 and MODULE_COMPILE_MARKERS[name] in detail and
              all(marker in detail for marker in
                  MODULE_ERROR_LOCATIONS.get(name, [])))
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  rc={rc} expected={MODULE_COMPILE_MARKERS[name]!r} "
                  f"out={so!r} err={se[-500:]}")

    print("== negative runtime: must fail on both backends and map source ==")
    for name in NEG_RUNTIME:
        py = compile_and_run(name, "py")
        jv = compile_and_run(name, "java")
        ok = (py[0] not in (0, "compile-fail", "timeout") and
              jv[0] not in (0, "compile-fail", "timeout") and
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
        if name == "modules":
            ok = ok and py[1] == "Hello Latent\n"
        elif name == "inheritance":
            ok = ok and py[1] == COOKBOOK_INHERITANCE_STDOUT
        print(("PASS " if ok else "FAIL ") + "cookbook/" + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={py[0]} out={py[1]!r} err={py[2][-500:]}")
            print(f"  java: rc={jv[0]} out={jv[1]!r} err={jv[2][-500:]}")

    print("== read-only CLI diagnostics: AST / desugar and no runtime execution ==")
    rc, so, se = run([sys.executable, os.path.join(TESTS,
                                                   "test_diagnostics.py")])
    diagnostic_output = so + se
    count_match = re.search(r"Ran (\d+) tests?", diagnostic_output)
    diagnostic_total = int(count_match.group(1)) if count_match else 1
    print(("PASS " if rc == 0 else "FAIL ") +
          f"CLI diagnostics ({diagnostic_total} unit/regression tests)")
    if rc != 0:
        failure_match = re.search(
            r"FAILED \(failures=(\d+)(?:, errors=(\d+))?\)",
            diagnostic_output)
        diagnostic_failures = (sum(int(n or 0) for n in failure_match.groups())
                               if failure_match else diagnostic_total)
        fails += diagnostic_failures
        print(f"  {diagnostic_output[-1500:]}")

    total = (diagnostic_total + len(POSITIVE) + len(MODULE_POSITIVE) +
             len(NEG_COMPILE) +
             len(NEG_INHERITANCE_COMPILE) +
             len(NEG_MODULE_COMPILE) + len(NEG_RUNTIME) + len(COOKBOOK))
    print(f"\n{total - fails} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
