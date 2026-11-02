"""End-to-end tests for the restricted Stage1 C11 target."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tools" / "latent_stage1.py"
sys.path.insert(0, str(ROOT))
from tests._safe_test_env import clean_test_environment


class Stage1CBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cc = shutil.which("cc")
        if cls.cc is None:
            raise unittest.SkipTest("Stage1 C11 integration requires cc")
        cls.temporary = tempfile.TemporaryDirectory(
            prefix=".latent-stage1-c-tests-", dir=ROOT)
        cls.work = Path(cls.temporary.name)
        cls.cache = cls.work / "cache"
        cls.env = clean_test_environment({"XDG_CACHE_HOME": str(cls.cache)})

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def invoke(self, *arguments: str, cwd: Path = ROOT):
        return subprocess.run(
            [sys.executable, str(LAUNCHER), *arguments],
            cwd=cwd, env=self.env, text=True, encoding="utf-8",
            capture_output=True, check=False, timeout=120)

    def run_program(self, command: list[str]):
        return subprocess.run(
            command, cwd=ROOT, env=self.env, text=True, encoding="utf-8",
            capture_output=True, check=False, timeout=30)

    def test_c_program_matches_stage1_python_output_and_exit_status(self):
        source = self.work / "vertical_slice.lt"
        source.write_text(
            'say "Stage1 C11"\n'
            "index = 0\n"
            "total = 0\n"
            "while index < 5:\n"
            "    total = total + index\n"
            "    index = index + 1\n"
            "if total == 10:\n"
            '    say "sum is ten"\n'
            "else:\n"
            '    say "wrong"\n'
            "say total\n"
            "product = total * 3\n"
            "difference = product - 20\n"
            "say difference\n"
            "say total == 10\n"
            "flag = total == 10\n"
            "say flag\n"
            "flag = 1\n"
            "say flag\n"
            "flag = false\n"
            "say flag\n"
            "if true:\n"
            "    branch_value = 7\n"
            "say branch_value\n"
            "say -9223372036854775808\n",
            encoding="utf-8", newline="\n")
        py_out = self.work / "python-out"
        c_out = self.work / "c-out"

        py_compile = self.invoke(str(source), "-t", "py", "-o", str(py_out))
        self.assertEqual((py_compile.returncode, py_compile.stderr), (0, ""))
        py_run = self.run_program([sys.executable, str(py_out / "vertical_slice.py")])

        c_compile = self.invoke(str(source), "--target", "c", "-o", str(c_out))
        self.assertEqual((c_compile.returncode, c_compile.stderr), (0, ""))
        c_source = c_out / "vertical_slice.c"
        self.assertTrue(c_source.is_file())
        generated_c = c_source.read_text(encoding="utf-8")
        includes = [line for line in generated_c.splitlines()
                    if line.startswith("#include")]
        self.assertEqual(includes, ["#include <stdint.h>", "#include <stdio.h>",
                                    "#include <stdlib.h>"])
        binary = self.work / "vertical_slice"
        native_compile = subprocess.run(
            [self.cc, "-std=c11", "-pedantic-errors", str(c_source), "-o", str(binary)],
            cwd=ROOT, env=self.env, text=True, encoding="utf-8",
            capture_output=True, check=False, timeout=30)
        self.assertEqual((native_compile.returncode, native_compile.stderr),
                         (0, ""), native_compile.stdout)
        c_run = self.run_program([str(binary)])

        self.assertEqual((py_run.returncode, py_run.stdout, py_run.stderr),
                         (0, "Stage1 C11\nsum is ten\n10\n10\ntrue\ntrue\n1\nfalse\n7\n-9223372036854775808\n", ""))
        self.assertEqual((c_run.returncode, c_run.stdout, c_run.stderr),
                         (py_run.returncode, py_run.stdout, py_run.stderr))

    def test_c_target_uses_its_build_c_default_directory(self):
        source = self.work / "default_output.lt"
        source.write_text('say "default C"\n', encoding="utf-8", newline="\n")
        result = self.invoke(str(source), "-t", "c", cwd=self.work)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        generated = self.work / "build" / "c" / "default_output.c"
        self.assertTrue(generated.is_file())
        self.assertIn("wrote build/c/default_output.c", result.stdout)

    def test_int64_overflow_is_checked_and_exits_nonzero_for_both_targets(self):
        for name, expression in (
                ("add_overflow", "9223372036854775807 + 1"),
                ("multiply_overflow", "9223372036854775807 * 2"),
                ("subtract_overflow", "-9223372036854775808 - 1"),
                ("negate_overflow", "-(-9223372036854775808)"),
                ("multiply_min_by_negative_one", "-9223372036854775808 * -1")):
            with self.subTest(operation=name):
                source = self.work / f"{name}.lt"
                source.write_text(f"say {expression}\n", encoding="utf-8",
                                  newline="\n")
                py_out = self.work / f"{name}-python"
                c_out = self.work / f"{name}-c"
                py_compile = self.invoke(str(source), "-t", "py", "-o", str(py_out))
                c_compile = self.invoke(str(source), "-t", "c", "-o", str(c_out))
                self.assertEqual((py_compile.returncode, py_compile.stderr), (0, ""))
                self.assertEqual((c_compile.returncode, c_compile.stderr), (0, ""))
                py_run = self.run_program(
                    [sys.executable, str(py_out / f"{name}.py")])
                native_compile = subprocess.run(
                    [self.cc, "-std=c11", "-pedantic-errors", str(c_out / f"{name}.c"), "-o",
                     str(self.work / name)], cwd=ROOT, env=self.env, text=True,
                    encoding="utf-8", capture_output=True, check=False, timeout=30)
                self.assertEqual((native_compile.returncode, native_compile.stderr),
                                 (0, ""), native_compile.stdout)
                c_run = self.run_program([str(self.work / name)])
                self.assertEqual(py_run.returncode, 1, py_run.stderr)
                self.assertEqual(c_run.returncode, py_run.returncode, c_run.stderr)
                self.assertEqual(c_run.stdout, py_run.stdout)
                self.assertIn("int64 overflow", py_run.stderr)
                self.assertIn("Latent runtime error: OverflowError: int64 overflow",
                              c_run.stderr)

    def test_unsupported_features_fail_closed_without_c_artifacts(self):
        cases = (
            ("for_loop", "for item in range(2):\n    say item\n",
             "unsupported statement in Stage1 C11 slice: For"),
            ("float", "say 1.5\n",
             "floating-point literals are outside the Stage1 C11 slice"),
            ("python_interop", 'module = py "math"\n',
             "Python/Java host interop is not supported by the Stage1 C11 target"),
            ("java_interop", 'klass = java "java.lang.Math"\n',
             "Python/Java host interop is not supported by the Stage1 C11 target"),
        )
        for name, program, marker in cases:
            with self.subTest(feature=name):
                source = self.work / f"{name}.lt"
                source.write_text(program, encoding="utf-8", newline="\n")
                output = self.work / f"{name}-out"
                result = self.invoke(str(source), "-t", "c", "-o", str(output))
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(marker, result.stderr)
                self.assertFalse(output.exists())

        loop = self.work / "for_loop.lt"
        py_out = self.work / "for-loop-python"
        py_compile = self.invoke(str(loop), "-t", "py", "-o", str(py_out))
        self.assertEqual((py_compile.returncode, py_compile.stderr), (0, ""))
        py_run = self.run_program([sys.executable, str(py_out / "for_loop.py")])
        self.assertEqual((py_run.returncode, py_run.stdout, py_run.stderr),
                         (0, "0\n1\n", ""))

    def test_imports_and_run_option_are_rejected_for_c_target(self):
        library = self.work / "library.lt"
        library.write_text('say "library"\n', encoding="utf-8", newline="\n")
        source = self.work / "with_import.lt"
        source.write_text(
            'import "library.lt" as library\nsay "main"\n',
            encoding="utf-8", newline="\n")
        output = self.work / "import-out"
        result = self.invoke(str(source), "-t", "c", "-o", str(output))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Stage1 C target supports a single file without imports",
                      result.stderr)
        self.assertFalse(output.exists())

        plain = self.work / "plain.lt"
        plain.write_text('say "plain"\n', encoding="utf-8", newline="\n")
        run_output = self.work / "run-out"
        run_result = self.invoke(str(plain), "-t", "c", "-o", str(run_output), "--run")
        self.assertEqual(run_result.returncode, 2)
        self.assertIn("--run is not supported for the Stage1 C target",
                      run_result.stderr)
        self.assertFalse(run_output.exists())

    def test_conditional_assignment_preserves_stage1_name_error(self):
        cases = (
            ("if", "if false:\n    late = 7\nsay late\n"),
            ("while", "while false:\n    late = 7\nsay late\n"),
        )
        for name, program in cases:
            with self.subTest(control_flow=name):
                source = self.work / f"uninitialized-{name}.lt"
                source.write_text(program, encoding="utf-8", newline="\n")
                py_out = self.work / f"uninitialized-{name}-python"
                c_out = self.work / f"uninitialized-{name}-c"
                py_compile = self.invoke(str(source), "-t", "py", "-o",
                                         str(py_out))
                c_compile = self.invoke(str(source), "-t", "c", "-o",
                                        str(c_out))
                self.assertEqual((py_compile.returncode, py_compile.stderr),
                                 (0, ""))
                self.assertEqual((c_compile.returncode, c_compile.stderr),
                                 (0, ""))
                py_run = self.run_program(
                    [sys.executable, str(py_out / source.with_suffix(".py").name)])
                binary = self.work / f"uninitialized-{name}"
                native_compile = subprocess.run(
                    [self.cc, "-std=c11", "-pedantic-errors",
                     str(c_out / source.with_suffix(".c").name), "-o",
                     str(binary)], cwd=ROOT, env=self.env, text=True,
                    encoding="utf-8", capture_output=True, check=False,
                    timeout=30)
                self.assertEqual((native_compile.returncode,
                                  native_compile.stderr), (0, ""),
                                 native_compile.stdout)
                c_run = self.run_program([str(binary)])
                self.assertEqual((py_run.returncode, py_run.stdout), (1, ""))
                self.assertEqual((c_run.returncode, c_run.stdout),
                                 (py_run.returncode, py_run.stdout))
                self.assertIn("NameError: name 'late' is not defined",
                              py_run.stderr)
                self.assertIn("NameError: local variable read before assignment",
                              c_run.stderr)


if __name__ == "__main__":
    unittest.main()
