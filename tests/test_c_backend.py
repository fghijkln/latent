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
    "p8_named_arguments", "p9_default_parameters", "p10_variadic_arguments",
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


def address_sanitizer_available(cc: str, directory: Path) -> bool:
    source = directory / "asan_probe.c"
    executable = directory / "asan_probe"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    compiled = subprocess.run([cc, *flags, str(source), "-o", str(executable)],
                              capture_output=True, text=True, timeout=30, check=False)
    if compiled.returncode != 0:
        return False
    result = subprocess.run([str(executable)], capture_output=True, text=True,
                            env=clean_env(ASAN_OPTIONS="detect_leaks=0"),
                            timeout=30, check=False)
    return result.returncode == 0


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

    def test_json_length_apis_reject_truncated_utf8_without_overread(self):
        if os.name != "posix":
            self.skipTest("AddressSanitizer C boundary probe requires POSIX")
        cc = shutil.which(os.environ.get("CC", "cc"))
        if not cc:
            self.skipTest("C compiler cc is required")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            if not address_sanitizer_available(cc, root):
                self.skipTest("AddressSanitizer is unavailable")
            harness = root / "json_length_boundary.c"
            harness.write_text(r'''\
#include "lt_json.h"
#include <stdlib.h>
#include <string.h>

static int rejects_string(const unsigned char *bytes, size_t length) {
    char *copy = (char *)malloc(length);
    if (!copy) return 0;
    memcpy(copy, bytes, length);
    LtJson *value = lt_json_string_n(copy, length);
    free(copy);
    if (value) { lt_json_free(value); return 0; }
    return 1;
}

int main(void) {
    static const unsigned char two[] = {0xc2};
    static const unsigned char three[] = {0xe2, 0x82};
    static const unsigned char four[] = {0xf0, 0x9f, 0x98};
    if (!rejects_string(two, sizeof(two)) || !rejects_string(three, sizeof(three))
        || !rejects_string(four, sizeof(four))) return 1;
    LtJson *object = lt_json_object();
    LtJson *item = lt_json_null();
    if (!object || !item || lt_json_object_set_n(object, (const char *)two,
                                                  sizeof(two), item)) return 2;
    lt_json_free(item);
    lt_json_free(object);
    return 0;
}
''', encoding="utf-8")
            flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
            compiled = subprocess.run(
                [cc, "-std=c11", "-Wall", "-Wextra", *flags, str(harness),
                 str(ROOT / "runtime" / "lt_json.c"), "-I", str(ROOT / "runtime"),
                 "-o", str(root / "json_length_boundary")],
                capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(root / "json_length_boundary")], capture_output=True, text=True,
                env=clean_env(ASAN_OPTIONS="detect_leaks=0"), timeout=30, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_malformed_sidecar_json_error_path_is_memory_safe(self):
        if os.name != "posix":
            self.skipTest("C sidecar process management requires POSIX")
        cc = shutil.which(os.environ.get("CC", "cc"))
        if not cc:
            self.skipTest("C compiler cc is required")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            if not address_sanitizer_available(cc, root):
                self.skipTest("AddressSanitizer is unavailable")
            source = root / "malformed_sidecar.lt"
            source.write_text('host = py "math"\nsay host.sqrt(4)\n', encoding="utf-8")
            outdir = root / "out"
            generated = invoke(source, "c", outdir, run=False)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            bad_sidecar = root / "bad-sidecar.sh"
            bad_sidecar.write_text("#!/bin/sh\nprintf '{bad\\n'\n", encoding="utf-8")
            bad_sidecar.chmod(0o755)
            flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
            compiled = subprocess.run(
                [cc, "-std=c11", "-Wall", "-Wextra", "-O0", *flags,
                 str(outdir / "malformed_sidecar.c"),
                 str(outdir / "runtime" / "lt_runtime.c"),
                 str(outdir / "runtime" / "lt_json.c"), "-I", str(outdir),
                 "-lm", "-o", str(outdir / "malformed_sidecar")],
                capture_output=True, text=True, env=clean_env(), timeout=60, check=False)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(outdir / "malformed_sidecar")], capture_output=True, text=True,
                env=clean_env(LATENT_PYTHON=str(bad_sidecar),
                              ASAN_OPTIONS="detect_leaks=0"),
                cwd=outdir, timeout=30, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid Python host interop JSON", result.stderr)
            self.assertNotIn("AddressSanitizer", result.stderr)

    def test_embedded_nul_string_semantics_match_python_backend(self):
        source_text = r'''
text = "a\0b"
say len(text)
say text[0]
say text[1]
say text[2]
say text[-1]
say text + "c"
say text == "a\0b"
say text == "a"
try:
    throw "x\0y"
catch message:
    say len(message)
    say message[2]
if "\0":
    say "truthy"
for char in text:
    say char
unicode = "é\0😀z"
say len(unicode)
say unicode[1]
say unicode[2]
items = {"a": 1, "a\0b": 2, "\0": 3}
say len(items)
say items["a"]
say items["a\0b"]
say items["\0"]
items["a\0b"] = 4
say items["a\0b"]
say items == {"a": 1, "a\0b": 4, "\0": 3}
'''
        expected = """\
3
a
<NUL>
b
b
a<NUL>bc
true
false
3
y
truthy
a
<NUL>
b
4
<NUL>
😀
3
1
2
3
4
true
""".replace("<NUL>", chr(0))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "embedded_nul.lt"
            source.write_text(source_text, encoding="utf-8")
            py = invoke(source, "py", root / "py")
            c = invoke(source, "c", root / "c")
            self.assertEqual(py.returncode, 0, py.stderr)
            self.assertEqual(c.returncode, 0,
                             f"C embedded-NUL fixture failed:\n{c.stderr}\n{c.stdout}")
            self.assertEqual(program_output(c), program_output(py))
            self.assertEqual(program_output(c), expected)
            self.assertEqual(c.stderr, "")

    def test_embedded_nul_roundtrips_through_python_host_sidecar(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "nul_host.py").write_text('''\
def echo(value):
    return value

def echo_map(value):
    return value
''', encoding="utf-8")
            source = root / "python_host_nul.lt"
            source.write_text(r'''
text = "a\0b"
host = py "nul_host"
result = host.echo(text)
say len(result)
say result[2]
say result == text
mapped = host.echo_map({"a": 1, "a\0b": text})
say mapped["a"]
say len(mapped["a\0b"])
say mapped["a\0b"][2]
''', encoding="utf-8")
            env = clean_env(PYTHONPATH=str(root))
            py = invoke(source, "py", root / "py", env=env)
            c = invoke(source, "c", root / "c", env=env)
            self.assertEqual(py.returncode, 0, py.stderr)
            self.assertEqual(c.returncode, 0, f"{c.stderr}\n{c.stdout}")
            self.assertEqual(program_output(c), program_output(py))
            self.assertEqual(program_output(c), "3\nb\ntrue\n1\n3\nb\n")

    def test_host_json_values_and_error_responses_are_memory_safe_under_asan(self):
        if os.name != "posix":
            self.skipTest("AddressSanitizer/LeakSanitizer host probe requires POSIX")
        cc = shutil.which(os.environ.get("CC", "cc"))
        if not cc:
            self.skipTest("C compiler cc is required")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            if not address_sanitizer_available(cc, root):
                self.skipTest("AddressSanitizer is unavailable")
            (root / "ownership_host.py").write_text(
                "def echo(value):\n"
                "    return value\n\n"
                "def fail(value):\n"
                "    raise ValueError('failure:' + str(value))\n",
                encoding="utf-8")
            source = root / "host_ownership.lt"
            source.write_text(
                'host = py "ownership_host"\n'
                'fn local(value):\n'
                '    return value\n'
                'data = {"": ["a\\0b", "é😀"], '
                '"nested": {"items": [1, 2, 3, 4]}}\n'
                'for i in range(8):\n'
                '    result = host.echo(data)\n'
                '    say result[""][0] == "a\\0b"\n'
                '    say result[""][1] == "é😀"\n'
                '    say result["nested"]["items"][3]\n'
                '    try:\n'
                '        host.fail(i)\n'
                '    catch message:\n'
                '        say len(message) > 0\n'
                '    try:\n'
                '        host.echo(local, [1, {"nested": [2, local]}])\n'
                '    catch message:\n'
                '        say len(message) > 0\n'
                '    say host.echo(data) == data\n',
                encoding="utf-8")
            outdir = root / "out"
            suppression = root / "lsan.supp"
            # Latent values intentionally live for the runtime process lifetime;
            # keep this probe focused on separately-owned host JSON trees.
            suppression.write_text("leak:lt_alloc\n", encoding="utf-8")
            env = clean_env(
                PYTHONPATH=str(root), ASAN_OPTIONS="detect_leaks=1",
                LSAN_OPTIONS=f"suppressions={suppression}:print_suppressions=0")
            generated = invoke(source, "c", outdir, run=False, env=env)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            executable = outdir / "host_ownership"
            compiled = subprocess.run(
                [cc, "-std=c11", "-Wall", "-Wextra", "-O0", "-g",
                 "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                 str(outdir / "host_ownership.c"),
                 str(outdir / "runtime" / "lt_runtime.c"),
                 str(outdir / "runtime" / "lt_json.c"), "-I", str(outdir),
                 "-lm", "-o", str(executable)],
                capture_output=True, text=True, env=env, cwd=outdir,
                timeout=60, check=False)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, env=env,
                cwd=outdir, timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "true\ntrue\n4\ntrue\ntrue\ntrue\n" * 8)
            self.assertEqual(result.stderr, "")

    def test_embedded_nul_roundtrips_through_java_host_sidecar(self):
        if not shutil.which("java") or not shutil.which("javac"):
            self.skipTest("Java host interop requires java and javac")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            java_fixture = root / "NulInteropFixture.java"
            java_fixture.write_text('''\
public class NulInteropFixture {
    public static String echo(String value) { return value; }
}
''', encoding="utf-8")
            source = root / "java_host_nul.lt"
            source.write_text(r'''
text = "a\0b"
NulInterop = java "NulInteropFixture"
result = NulInterop.echo(text)
say len(result)
say result[2]
say result == text
''', encoding="utf-8")
            py = run_java_interop_fixture(source, "py", root / "py",
                                          (java_fixture,))
            c = run_java_interop_fixture(source, "c", root / "c",
                                         (java_fixture,))
            self.assertEqual(py.returncode, 0, py.stderr)
            self.assertEqual(c.returncode, 0, f"{c.stderr}\n{c.stdout}")
            self.assertEqual(c.stdout, py.stdout)
            self.assertEqual(c.stdout, "3\nb\ntrue\n")

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

    def test_python_host_smallest_subnormal_float_roundtrips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "subnormal.lt"
            source.write_text('math = py "math"\nsay math.ldexp(1.0, -1074)\n',
                              encoding="utf-8")
            py = invoke(source, "py", root / "py")
            c = invoke(source, "c", root / "c")
            self.assertEqual(py.returncode, 0, py.stderr)
            self.assertEqual(c.returncode, 0, f"{c.stderr}\n{c.stdout}")
            self.assertEqual(program_output(c), program_output(py))
            self.assertEqual(program_output(c), "5e-324\n")

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

    def test_python_callback_rejects_positional_keyword_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "callback_positional_keyword_conflict.lt"
            source.write_text('''\
host = py "p18_callbacks_host"
fn callback(value):
    say "MUST-NOT-RUN"
    return value
say host.call_conflicting_kwargs(callback)
''', encoding="utf-8")
            env = clean_env(PYTHONPATH=str(TESTS / "python"))
            for target in ("py", "c"):
                with self.subTest(target=target):
                    result = invoke(source, target, root / target, env=env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("multiple values for argument 'value'",
                                  result.stderr)
                    self.assertNotIn("MUST-NOT-RUN", program_output(result))

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
