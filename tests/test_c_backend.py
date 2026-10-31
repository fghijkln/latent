#!/usr/bin/env python3
"""Integration checks for Latent's native C11 target."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LATENT = ROOT / "latent.py"
TESTS = ROOT / "tests"

CORE_CASES = (
    "hello", "fib", "loop", "scope", "data", "truthy", "str_interp", "indexing",
    "class_basic", "inheritance", "p5_shared_capture", "p6_nonlocal_returned",
    "p8_named_arguments", "p10_variadic_arguments",
    "p11_keyword_only_parameters", "p12_override_compatibility",
)
MODULE_CASES = (
    "modules/app/main", "modules/function_values/main",
    "modules/nonlocal/main", "modules/bound_methods/main",
    "modules/variadic/main",
)


def clean_env(**updates: str) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() in {
            "APPDATA", "COMSPEC", "HOME", "JAVA_HOME", "LANG", "LC_ALL",
            "LC_CTYPE", "LOCALAPPDATA", "PATH", "PATHEXT", "SYSTEMROOT",
            "TEMP", "TMP", "TMPDIR", "USERPROFILE", "WINDIR",
        }
    }
    env.setdefault("PATH", os.defpath)
    env.update({"PYTHONPATH": "", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1", "enable_mcp": "false"})
    env.update(updates)
    return env


def invoke(source: Path, target: str, outdir: Path, *, run: bool = True,
           env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(LATENT), str(source), "-t", target,
               "-o", str(outdir)]
    if run:
        command.append("--run")
    return subprocess.run(command, capture_output=True, text=True,
                          env=env or clean_env(), timeout=60, check=False)


def program_output(result: subprocess.CompletedProcess[str]) -> str:
    """Drop the compiler status line wherever stdio buffering placed it."""
    return "".join(line for line in result.stdout.splitlines(keepends=True)
                   if not line.startswith("wrote "))


def run_java_interop_fixture(source: Path, target: str, outdir: Path,
                             java_sources: tuple[Path, ...],
                             pythonpath: str = "") -> subprocess.CompletedProcess[str]:
    """Build a host fixture class, then run generated Python or C output."""
    env = clean_env(PYTHONPATH=pythonpath, LATENT_JAVAD=str(outdir))
    generated = invoke(source, target, outdir, run=False, env=env)
    if generated.returncode != 0:
        return generated
    javac = shutil.which("javac")
    if not javac:
        return subprocess.CompletedProcess(["javac"], 127, "", "javac is required")
    class_result = subprocess.run(
        [javac, "-cp", str(outdir), "-d", str(outdir),
         *(str(path) for path in java_sources)],
        capture_output=True, text=True, env=env, timeout=60, check=False)
    if class_result.returncode != 0:
        return class_result
    if target == "py":
        return subprocess.run(
            [sys.executable, str(outdir / (source.stem + ".py"))],
            capture_output=True, text=True, env=env, cwd=outdir,
            timeout=60, check=False)
    cc = shutil.which(os.environ.get("CC", "cc"))
    if not cc:
        return subprocess.CompletedProcess(["cc"], 127, "", "C compiler cc is required")
    runtime = outdir / "runtime"
    executable = outdir / source.stem
    c_compile = subprocess.run(
        [cc, "-std=c11", "-Wall", "-Wextra", "-O0",
         str(outdir / (source.stem + ".c")), str(runtime / "lt_runtime.c"),
         str(runtime / "lt_json.c"), "-I", str(outdir), "-lm", "-o", str(executable)],
        capture_output=True, text=True, env=env, cwd=outdir,
        timeout=60, check=False)
    if c_compile.returncode != 0:
        return c_compile
    return subprocess.run([str(executable)], capture_output=True, text=True,
                          env=env, cwd=outdir, timeout=60, check=False)


@unittest.skipUnless(shutil.which("cc"), "C11 compiler cc is required")
class CBackendDifferentialTests(unittest.TestCase):
    def test_core_and_module_fixtures_match_python_backend(self):
        for fixture in CORE_CASES + MODULE_CASES:
            source = TESTS / (fixture + ".lt")
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                py = invoke(source, "py", root / "py")
                self.assertEqual(py.returncode, 0, py.stderr)
                c = invoke(source, "c", root / "c")
                self.assertEqual(c.returncode, 0,
                                 f"C failed for {fixture}:\n{c.stderr}\n{c.stdout}")
                self.assertEqual(program_output(c), program_output(py), fixture)
                self.assertEqual(c.stderr, "", fixture)

    def test_int64_vertical_slice_matches_python_backend(self):
        source = TESTS / "bootstrap" / "p20_int64_feature.lt"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            py = invoke(source, "py", root / "py")
            c = invoke(source, "c", root / "c")
            self.assertEqual(py.returncode, 0, py.stderr)
            self.assertEqual(c.returncode, 0,
                             f"C int64 fixture failed:\n{c.stderr}\n{c.stdout}")
            self.assertEqual(program_output(c), program_output(py))
            self.assertEqual(c.stderr, "")

    def test_python_and_java_host_interop_fixtures_match_python_backend(self):
        if not shutil.which("java") or not shutil.which("javac"):
            self.skipTest("Java host interop requires java and javac")
        for fixture in ("py_basic", "py_numpy", "java_basic", "mixed"):
            if fixture in ("py_numpy", "mixed"):
                try:
                    import numpy  # noqa: F401
                except ImportError:
                    continue
            source = TESTS / (fixture + ".lt")
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                py = invoke(source, "py", root / "py")
                c = invoke(source, "c", root / "c")
                self.assertEqual(py.returncode, 0, py.stderr)
                self.assertEqual(c.returncode, 0,
                                 f"C failed for {fixture}:\n{c.stderr}\n{c.stdout}")
                self.assertEqual(program_output(c), program_output(py), fixture)

    def test_host_sidecars_start_when_parent_stdio_is_closed(self):
        if os.name != "posix":
            self.skipTest("C11 sidecar process management requires POSIX")
        if not shutil.which("java") or not shutil.which("javac"):
            self.skipTest("Java host interop requires java and javac")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "sidecars.lt"
            source.write_text('''\
m = py "math"
say m.sqrt(9)
A = java "java.util.ArrayList"
say A
''', encoding="utf-8")
            outdir = root / "out"
            generated = invoke(source, "c", outdir, run=False)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            cc = shutil.which(os.environ.get("CC", "cc"))
            self.assertIsNotNone(cc, "C compiler cc is required")
            env = clean_env()
            compiled = subprocess.run(
                [cc, "-std=c11", "-Wall", "-Wextra", "-O0",
                 str(outdir / "sidecars.c"),
                 str(outdir / "runtime" / "lt_runtime.c"),
                 str(outdir / "runtime" / "lt_json.c"), "-I", str(outdir),
                 "-lm", "-o", str(outdir / "sidecars")],
                capture_output=True, text=True, env=env, timeout=60, check=False)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)

            for descriptor in (0, 1):
                with self.subTest(closed_descriptor=descriptor):
                    result = subprocess.run(
                        [str(outdir / "sidecars")], capture_output=True,
                        text=True, env=env, cwd=outdir, timeout=60, check=False,
                        preexec_fn=lambda fd=descriptor: os.close(fd))
                    self.assertEqual(result.returncode, 0,
                                     f"closed fd {descriptor}: {result.stderr}")
                    self.assertEqual(result.stderr, "")
                    if descriptor == 0:
                        self.assertEqual(result.stdout,
                                         "3\n<class java.util.ArrayList>\n")

    def test_python_host_member_values_named_args_and_callbacks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "hostmod.py").write_text('''\
def scale(value=1):
    return value * 3

def apply(fn, value):
    return fn(value)

def apply_keyword(fn):
    return fn(value=4)

class Counter:
    def bump(self, value):
        return value + 10
''', encoding="utf-8")
            source = root / "callback.lt"
            source.write_text('''\
host = py "hostmod"
fn plus1(value):
    return value + 1
scale = host.scale
say scale(value=3)
say host.apply(plus1, 4)
say host.apply_keyword(plus1)
obj = host.Counter()
bump = obj.bump
say bump(2)
''', encoding="utf-8")
            env = clean_env(PYTHONPATH=str(root))
            py = invoke(source, "py", root / "py", env=env)
            c = invoke(source, "c", root / "c", env=env)
            self.assertEqual(py.returncode, 0, py.stderr)
            self.assertEqual(c.returncode, 0, f"{c.stderr}\n{c.stdout}")
            self.assertEqual(program_output(c), program_output(py))
            self.assertEqual(program_output(c), "9\n5\n5\n12\n")

    def test_extracted_java_bound_method_matches_python_backend(self):
        if not shutil.which("java") or not shutil.which("javac"):
            self.skipTest("Java host interop requires java and javac")
        source = TESTS / "p13_interop_bound_methods.lt"
        java_fixture = TESTS / "java" / "P13InteropFixture.java"
        pythonpath = str(TESTS / "python")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            py = run_java_interop_fixture(source, "py", root / "py",
                                          (java_fixture,), pythonpath)
            c = run_java_interop_fixture(source, "c", root / "c",
                                         (java_fixture,), pythonpath)
            self.assertEqual(py.returncode, 0, py.stderr)
            self.assertEqual(c.returncode, 0, f"{c.stderr}\n{c.stdout}")
            self.assertEqual(c.stdout, py.stdout)

    def test_phase1_mixed_host_interop_matches_python_backend(self):
        if not shutil.which("java") or not shutil.which("javac"):
            self.skipTest("Java host interop requires java and javac")
        source = TESTS / "phase1_semantics.lt"
        java_fixture = TESTS / "java" / "Stage1Overloads.java"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            py = run_java_interop_fixture(source, "py", root / "py",
                                          (java_fixture,))
            c = run_java_interop_fixture(source, "c", root / "c",
                                         (java_fixture,))
            self.assertEqual(py.returncode, 0, py.stderr)
            self.assertEqual(c.returncode, 0, f"{c.stderr}\n{c.stdout}")
            self.assertEqual(c.stdout, py.stdout)

    def test_extracted_python_module_and_java_static_methods_match(self):
        if not shutil.which("java") or not shutil.which("javac"):
            self.skipTest("Java host interop requires java and javac")
        source = TESTS / "p14_module_and_static_values.lt"
        java_fixture = TESTS / "java" / "P14InteropFixture.java"
        pythonpath = str(TESTS / "python")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            py = run_java_interop_fixture(source, "py", root / "py",
                                          (java_fixture,), pythonpath)
            c = run_java_interop_fixture(source, "c", root / "c",
                                         (java_fixture,), pythonpath)
            self.assertEqual(py.returncode, 0, py.stderr)
            self.assertEqual(c.returncode, 0, f"{c.stderr}\n{c.stdout}")
            self.assertEqual(c.stdout, py.stdout)

    def test_short_circuit_and_argument_evaluation_order(self):
        source_text = '''\
seen = []
fn record(x):
    push(seen, x)
    return x
ignored = false and record(9)
total = record(1) + record(2)
say seen
'''
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "order.lt"
            source.write_text(source_text, encoding="utf-8")
            c = invoke(source, "c", root / "out")
            self.assertEqual(c.returncode, 0, c.stderr)
            self.assertEqual(program_output(c), "[1, 2]\n")

    def test_try_catch_and_uncaught_runtime_error_location(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            caught = root / "caught.lt"
            caught.write_text('''\
try:
    say [1][4]
catch error:
    say "caught: $error"
''', encoding="utf-8")
            c = invoke(caught, "c", root / "caught-out")
            self.assertEqual(c.returncode, 0, c.stderr)
            self.assertEqual(program_output(c), "caught: index out of range: 4\n")

            uncaught = root / "uncaught.lt"
            uncaught.write_text("say [1][4]\n", encoding="utf-8")
            c = invoke(uncaught, "c", root / "uncaught-out")
            self.assertNotEqual(c.returncode, 0)
            self.assertIn("Latent runtime error", c.stderr)
            self.assertIn("uncaught.lt:1", c.stderr)


class CBackendCliTests(unittest.TestCase):
    def test_compile_only_writes_c_and_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "simple.lt"
            source.write_text("say 1 + 2\n", encoding="utf-8")
            outdir = root / "generated"
            result = invoke(source, "c", outdir, run=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((outdir / "simple.c").is_file())
            self.assertTrue((outdir / "runtime" / "lt_runtime.c").is_file())
            self.assertTrue((outdir / "runtime" / "lt_runtime.h").is_file())
            self.assertTrue((outdir / "runtime" / "lt_json.c").is_file())
            self.assertTrue((outdir / "runtime" / "lt_json.h").is_file())
            self.assertTrue((outdir / "runtime" / "ltpy.py").is_file())
            self.assertTrue((outdir / "runtime" / "LtJavaDaemon.java").is_file())
            self.assertIn("lt_runtime.h",
                          (outdir / "simple.c").read_text(encoding="utf-8"))

    def test_runtime_symlink_output_does_not_copy_files_onto_themselves(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "simple.lt"
            source.write_text("say 1\n", encoding="utf-8")
            outdir = root / "generated"
            outdir.mkdir()
            (outdir / "runtime").symlink_to(ROOT / "runtime", target_is_directory=True)
            result = invoke(source, "c", outdir, run=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((outdir / "simple.c").is_file())

    def test_missing_c_compiler_is_reported_for_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "simple.lt"
            source.write_text("say 1\n", encoding="utf-8")
            result = invoke(source, "c", root / "out",
                            env=clean_env(CC="latent-test-compiler-missing"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("C compiler not available", result.stderr)
            self.assertTrue((root / "out" / "simple.c").is_file())


if __name__ == "__main__":
    unittest.main()
