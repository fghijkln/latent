#!/usr/bin/env python3
"""Check P14 typed daemon refs are isolated from ordinary opaque handles."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNTIME = os.path.join(ROOT, "runtime")


def main():
    requests = (
        '{"op":"call_module_function","id":999,"args":[]}\n'
        '{"op":"quit"}\n')
    py = subprocess.run(
        [sys.executable, os.path.join(RUNTIME, "ltpy.py")],
        input=requests, capture_output=True, text=True, timeout=10)
    py_lines = [line for line in py.stdout.splitlines() if line]
    py_error = json.loads(py_lines[0]).get("error", "") if py_lines else ""
    py_ok = (py.returncode == 0 and
             "stale Python module-function ref: 999" in py_error)

    with tempfile.TemporaryDirectory(prefix="latent-p14-protocol-") as out:
        sources = [os.path.join(RUNTIME, name) for name in
                   ("LtJavaDaemon.java", "JReflect.java", "LtRt.java")]
        compiled = subprocess.run(
            ["javac", "-d", out] + sources,
            capture_output=True, text=True, timeout=60)
        if compiled.returncode != 0:
            print(compiled.stdout + compiled.stderr, file=sys.stderr)
            return 1
        java_requests = (
            '{"op":"call_static_method","id":999,"args":[]}\n'
            '{"op":"quit"}\n')
        java = subprocess.run(
            ["java", "-cp", out, "LtJavaDaemon"], input=java_requests,
            capture_output=True, text=True, timeout=10)
        java_lines = [line for line in java.stdout.splitlines() if line]
        java_error = (json.loads(java_lines[0]).get("error", "")
                      if java_lines else "")
        java_ok = (java.returncode == 0 and
                   "stale Java static-method ref: 999" in java_error)

    if not py_ok:
        print("FAIL Python module-function typed ref")
        print(f"  rc={py.returncode} stdout={py.stdout!r} stderr={py.stderr!r}")
    if not java_ok:
        print("FAIL Java static-method typed ref")
        print(f"  rc={java.returncode} stdout={java.stdout!r} stderr={java.stderr!r}")
    if py_ok and java_ok:
        print("P14 daemon typed refs: 2 protocol checks passed")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
