"""Fixed-Stage0 smoke test for the first Latent Python-emitter slice.

The compiler subset is intentionally limited to the current M4 parser/AST. The
module source root is fixed for this slice because current module identities
and source maps include normalized absolute paths. Stage1-to-Stage2 self-hosting
is deferred until the Latent compiler can parse and resolve its own modules.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
STAGE0_COMMIT = "9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9"
COMPILER_SOURCE = ROOT / "bootstrap" / "compiler.lt"
FIXTURE = r'''fn add(a, b):
    return a + b

fn sum_positive(values):
    total = 0
    for value in values:
        if value > 0:
            total = total + value
    return total

fn interpolate(name):
    return "Hi $name / ${1 + 2}"

fn read_last(values):
    values[-1]

fn mutate_first(values):
    values[0] = 9
    values[0]

say "bootstrap smoke"
say add(40, 2)
say sum_positive([-2, 3, 6])
say 1 / 2
say [true, nil, "line\nnext"]
say ["quote: \"x\""]
say {"items": [1, 2]}
say interpolate("world")
say read_last([5, 6])
say mutate_first([1, 2])
'''
EXPECTED_STDOUT = r'''bootstrap smoke
42
9
0.5
[true, nil, "line\nnext"]
["quote: \"x\""]
{"items": [1, 2]}
Hi world / 3
6
9
'''


def checked(command: list[str], *, cwd: Path, env: dict[str, str] | None = None):
    result = subprocess.run(command, cwd=cwd, env=env, text=True,
                            encoding="utf-8", capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {command!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


def extract_stage0(destination: Path) -> None:
    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", STAGE0_COMMIT],
        capture_output=True, check=False)
    if archive.returncode != 0:
        raise RuntimeError(
            f"fixed Stage0 commit {STAGE0_COMMIT} is unavailable in this checkout")
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
        for member in bundle.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"unsafe Stage0 archive member: {member.name}")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise RuntimeError(f"cannot read Stage0 file: {member.name}")
                target.write_bytes(stream.read())
            elif member.issym() or member.islnk():
                raise RuntimeError(f"unexpected link in Stage0 archive: {member.name}")


def run_stage_program(script: Path, cwd: Path) -> tuple[int, str, str]:
    result = subprocess.run(
        [sys.executable, str(script)], cwd=cwd,
        env={**os.environ, "PYTHONPATH": ""}, text=True, encoding="utf-8",
        capture_output=True, check=False)
    return result.returncode, result.stdout, result.stderr


class BootstrapEmitterTests(unittest.TestCase):
    def test_stage0_generates_deterministic_stage1_python_emitter(self):
        with tempfile.TemporaryDirectory(prefix="p15-emitter-") as temporary:
            root = Path(temporary)
            stage0 = root / "stage0"
            extract_stage0(stage0)
            clean_env = {**os.environ, "PYTHONPATH": ""}

            # Keep the normalized module root fixed, but build into two fresh
            # directories. Cross-checkout path independence is not claimed yet.
            stage_dirs = [root / "stage1-a", root / "stage1-b"]
            for stage_dir in stage_dirs:
                stage_dir.mkdir()
                checked([sys.executable, str(stage0 / "latent.py"),
                         str(COMPILER_SOURCE), "-t", "py", "-o",
                         str(stage_dir)], cwd=root)
                (stage_dir / "latent_host.py").write_bytes(
                    (ROOT / "bootstrap" / "latent_host.py").read_bytes())
            compiler_a = stage_dirs[0] / "compiler.py"
            compiler_b = stage_dirs[1] / "compiler.py"
            self.assertEqual(compiler_a.read_bytes(), compiler_b.read_bytes())

            compiler_source = compiler_a.read_text(encoding="utf-8")
            self.assertIn(str(ROOT / "bootstrap"), compiler_source)
            self.assertNotIn("import lex", compiler_source)

            fixture = root / "fixture.lt"
            fixture.write_text(FIXTURE, encoding="utf-8", newline="\n")
            stage0_target = root / "stage0-target"
            stage1_target = root / "stage1-target"
            stage1_target_repeat = root / "stage1-target-repeat"
            stage0_target.mkdir()
            stage1_target_repeat.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"), str(fixture),
                     "-t", "py", "-o", str(stage0_target)], cwd=root)
            checked([sys.executable, str(compiler_a), str(fixture),
                     "-o", str(stage1_target)], cwd=root, env=clean_env)
            checked([sys.executable, str(compiler_a), str(fixture),
                     "-o", str(stage1_target_repeat)], cwd=root, env=clean_env)

            stage0_result = run_stage_program(stage0_target / "fixture.py", root)
            stage1_result = run_stage_program(stage1_target / "fixture.py", root)
            self.assertEqual(stage0_result, (0, EXPECTED_STDOUT, ""))
            self.assertEqual(stage1_result, stage0_result)
            self.assertEqual(
                (stage1_target / "fixture.py").read_bytes(),
                (stage1_target_repeat / "fixture.py").read_bytes())

            missing_out = root / "missing-out"
            missing = subprocess.run(
                [sys.executable, str(compiler_a), str(root / "missing.lt"),
                 "-o", str(missing_out)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True, check=False)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("input error: not-found", missing.stderr)
            self.assertFalse(missing_out.exists())

            malformed = root / "malformed.lt"
            malformed.write_text("say 1 +\n", encoding="utf-8", newline="\n")
            malformed_out = root / "malformed-out"
            rejected = subprocess.run(
                [sys.executable, str(compiler_a), str(malformed), "-o",
                 str(malformed_out)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True, check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn(
                f"{malformed}:1:8: parse error: unexpected NEWLINE in expression",
                rejected.stderr)
            self.assertFalse(malformed_out.exists())

            semantic_cases = (
                ("undefined", "say missing_name\n", "undefined name 'missing_name'"),
                ("read-before", "fn bad():\n    say local\n    local = 1\n", "local 'local' read before assignment"),
                ("call-missing", "fn pair(left, right):\n    return left + right\nsay pair(left=1)\n", "pair() missing required argument 'right'"),
                ("call-duplicate", "fn pair(left, right):\n    return left + right\nsay pair(left=1, left=2)\n", "got duplicate named argument 'left'"),
                ("call-multiple", "fn pair(left, right):\n    return left + right\nsay pair(1, left=2)\n", "got multiple values for argument 'left'"),
                ("call-unknown", "fn pair(left, right):\n    return left + right\nsay pair(left=1, other=2)\n", "got unexpected named argument 'other'"),
                ("builtin-keyword", "say len(value=[])\n", "built-in function 'len' does not accept named arguments"),
                ("builtin-unpack", "say len(*[1])\n", "built-in function 'len' does not support argument unpacking"),
                ("builtin-arity", "say len()\n", "len() takes 1..1 args, got 0"),
                ("default-forward", "fn bad(first=later, later=2):\n    return first\nsay bad()\n", "default for parameter 'first' cannot reference parameter 'later' before it is bound"),
            )
            for name, source, marker in semantic_cases:
                semantic_fixture = root / f"semantic-{name}.lt"
                semantic_fixture.write_text(source, encoding="utf-8", newline="\n")
                stage0_semantic_out = root / f"stage0-semantic-{name}"
                stage1_semantic_out = root / f"stage1-semantic-{name}"
                stage0_error = subprocess.run(
                    [sys.executable, str(stage0 / "latent.py"),
                     str(semantic_fixture), "-t", "py", "-o",
                     str(stage0_semantic_out)], cwd=root, env=clean_env,
                    text=True, encoding="utf-8", capture_output=True,
                    check=False)
                stage1_error = subprocess.run(
                    [sys.executable, str(compiler_a), str(semantic_fixture),
                     "-o", str(stage1_semantic_out)], cwd=root, env=clean_env,
                    text=True, encoding="utf-8", capture_output=True,
                    check=False)
                self.assertEqual(stage0_error.returncode, 1)
                self.assertEqual(stage1_error.returncode, stage0_error.returncode)
                self.assertEqual(stage1_error.stderr.splitlines()[0],
                                 stage0_error.stderr.splitlines()[0])
                self.assertIn(marker, stage1_error.stderr)
                self.assertFalse(stage0_semantic_out.exists())
                self.assertFalse(stage1_semantic_out.exists())

            help_result = subprocess.run(
                [sys.executable, str(compiler_a), "--help"], cwd=root,
                env=clean_env, text=True, encoding="utf-8",
                capture_output=True, check=False)
            self.assertEqual(help_result.returncode, 0)
            self.assertIn("usage: latent source.lt", help_result.stdout)
            self.assertEqual(help_result.stderr, "")

            for target in ("java", "ruby"):
                rejected_target_out = root / f"target-{target}-out"
                target_result = subprocess.run(
                    [sys.executable, str(compiler_a), "-t", target,
                     str(fixture), "-o", str(rejected_target_out)], cwd=root,
                    env=clean_env, text=True, encoding="utf-8",
                    capture_output=True, check=False)
                self.assertEqual(target_result.returncode, 2)
                self.assertIn("target '" + target + "' is unsupported",
                              target_result.stderr)
                self.assertFalse(rejected_target_out.exists())

            run_out = root / "stage1-run-out"
            run_result = subprocess.run(
                [sys.executable, str(compiler_a), str(fixture), "-t", "py",
                 "--run", "-o", str(run_out)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True,
                check=False)
            self.assertEqual(run_result.returncode, 0, run_result.stderr)
            self.assertTrue(run_result.stdout.startswith("wrote "))
            self.assertTrue(run_result.stdout.endswith(EXPECTED_STDOUT))
            self.assertEqual(run_result.stderr, "")

            dotted_source = root / "multi.part.name.lt"
            dotted_source.write_text('say "dotted stem"\n',
                                     encoding="utf-8", newline="\n")
            dotted_out = root / "dotted-out"
            checked([sys.executable, str(compiler_a), str(dotted_source),
                     "-o", str(dotted_out)], cwd=root, env=clean_env)
            self.assertTrue((dotted_out / "multi.part.name.py").is_file())

            runtime_failure = root / "runtime-failure.lt"
            runtime_failure.write_text("say 1 / 0\n", encoding="utf-8",
                                      newline="\n")
            runtime_failure_out = root / "runtime-failure-out"
            run_failure = subprocess.run(
                [sys.executable, str(compiler_a), str(runtime_failure),
                 "--run", "-o", str(runtime_failure_out)], cwd=root,
                env=clean_env, text=True, encoding="utf-8",
                capture_output=True, check=False)
            self.assertEqual(run_failure.returncode, 1)
            self.assertIn("ZeroDivisionError", run_failure.stderr)

    def test_parameter_and_call_nodes_match_fixed_stage0(self):
        fixture_source = r'''fn describe(value, fallback=value + 1, *rest, required, option=fallback + 2, **extra):
    return [value, fallback, rest, required, option, extra]

fn invoke(target, named):
    return target(**named)

say describe
say describe(10, required=40)
say describe(10, 20, *[30, 31], required=41, **{"tag": 50})
say describe(**{"value": 60, "required": 61}, option=62, tag=63)
say invoke(describe, {"value": 70, "required": 71})
'''
        expected = (
            "<function describe>\n"
            "[10, 11, [], 40, 13, {}]\n"
            '[10, 20, [30, 31], 41, 22, {"tag": 50}]\n'
            '[60, 61, [], 61, 62, {"tag": 63}]\n'
            "[70, 71, [], 71, 73, {}]\n"
        )
        with tempfile.TemporaryDirectory(prefix="p15-emitter-params-") as temporary:
            root = Path(temporary)
            stage0 = root / "stage0"
            extract_stage0(stage0)
            stage1_dir = root / "stage1"
            stage1_dir.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(COMPILER_SOURCE), "-t", "py", "-o",
                     str(stage1_dir)], cwd=root)
            (stage1_dir / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())
            compiler = stage1_dir / "compiler.py"

            fixture = root / "parameters.lt"
            fixture.write_text(fixture_source, encoding="utf-8", newline="\n")
            stage0_out = root / "stage0-out"
            stage1_out = root / "stage1-out"
            stage0_out.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"), str(fixture),
                     "-t", "py", "-o", str(stage0_out)], cwd=root)
            checked([sys.executable, str(compiler), str(fixture),
                     "-o", str(stage1_out)], cwd=root,
                    env={**os.environ, "PYTHONPATH": ""})

            stage0_result = run_stage_program(stage0_out / "parameters.py", root)
            stage1_result = run_stage_program(stage1_out / "parameters.py", root)
            self.assertEqual(stage0_result, (0, expected, ""))
            self.assertEqual(stage1_result, stage0_result)

            invalid_fixture = root / "missing-required.lt"
            invalid_fixture.write_text(
                "fn target(value, *, flag):\n    return value\n\n"
                "fn invoke(function):\n    return function(flag=1)\n\n"
                "say invoke(target)\n",
                encoding="utf-8", newline="\n")
            invalid_stage0_out = root / "invalid-stage0-out"
            invalid_stage1_out = root / "invalid-stage1-out"
            invalid_stage0_out.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(invalid_fixture), "-t", "py", "-o",
                     str(invalid_stage0_out)], cwd=root)
            checked([sys.executable, str(compiler), str(invalid_fixture),
                     "-o", str(invalid_stage1_out)], cwd=root,
                    env={**os.environ, "PYTHONPATH": ""})
            invalid_stage0_result = run_stage_program(
                invalid_stage0_out / "missing-required.py", root)
            invalid_stage1_result = run_stage_program(
                invalid_stage1_out / "missing-required.py", root)
            self.assertNotEqual(invalid_stage0_result[0], 0)
            self.assertEqual(invalid_stage1_result[0], invalid_stage0_result[0])
            self.assertIn("target() missing required argument 'value'",
                          invalid_stage0_result[2])
            self.assertIn("target() missing required argument 'value'",
                          invalid_stage1_result[2])

    def test_unimplemented_interop_and_super_nodes_fail_closed(self):
        cases = (
            ('say py "math"\n', True,
             "Python interop requires the unavailable target runtime"),
            ('say java "java.lang.String"\n', True,
             "Java interop requires the unavailable target runtime"),
            ("fn read(obj):\n    return obj.field\n", True,
             "attribute access requires the unavailable target runtime"),
            ("fn call(obj):\n    return obj.method()\n", True,
             "attribute access requires the unavailable target runtime"),
            ("fn write(obj):\n    obj.field = 1\n", True,
             "attribute access requires the unavailable target runtime"),
            ("say super.method(self)\n", False,
             "undefined name 'self'"),
        )
        with tempfile.TemporaryDirectory(prefix="p15-emitter-boundary-") as temporary:
            root = Path(temporary)
            stage0 = root / "stage0"
            extract_stage0(stage0)
            stage1_dir = root / "stage1"
            stage1_dir.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(COMPILER_SOURCE), "-t", "py", "-o",
                     str(stage1_dir)], cwd=root)
            (stage1_dir / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())
            compiler = stage1_dir / "compiler.py"
            for index, (source, stage0_accepts, expected_error) in enumerate(cases):
                fixture = root / f"unsupported-{index}.lt"
                fixture.write_text(source, encoding="utf-8", newline="\n")
                stage0_out = root / f"stage0-{index}"
                stage1_out = root / f"stage1-{index}"
                stage0_out.mkdir()
                stage0_result = subprocess.run(
                    [sys.executable, str(stage0 / "latent.py"), str(fixture),
                     "-t", "py", "-o", str(stage0_out)], cwd=root,
                    text=True, encoding="utf-8", capture_output=True,
                    check=False)
                self.assertEqual(stage0_result.returncode == 0, stage0_accepts,
                                 stage0_result.stdout + stage0_result.stderr)
                stage1_result = subprocess.run(
                    [sys.executable, str(compiler), str(fixture),
                     "-o", str(stage1_out)], cwd=root,
                    env={**os.environ, "PYTHONPATH": ""}, text=True,
                    encoding="utf-8", capture_output=True, check=False)
                self.assertNotEqual(stage1_result.returncode, 0)
                self.assertIn(expected_error, stage1_result.stderr)
                self.assertFalse(stage1_out.exists())

    def test_stage1_compiler_uses_module_graph_before_fail_closed_codegen(self):
        with tempfile.TemporaryDirectory(prefix="p15-module-compiler-") as temporary:
            root = Path(temporary)
            stage0 = root / "stage0"
            extract_stage0(stage0)
            stage1_dir = root / "stage1"
            stage1_dir.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(COMPILER_SOURCE), "-t", "py", "-o",
                     str(stage1_dir)], cwd=root)
            (stage1_dir / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())
            compiler = stage1_dir / "compiler.py"
            clean_env = {**os.environ, "PYTHONPATH": ""}

            module_project = root / "module-project"
            (module_project / "sub").mkdir(parents=True)
            (module_project / "main.lt").write_text(
                'import "sub/lib.lt" as lib\nsay "main"\n',
                encoding="utf-8", newline="\n")
            (module_project / "sub" / "lib.lt").write_text(
                'import "missing.lt" as missing\n',
                encoding="utf-8", newline="\n")
            output = root / "module-output"
            rejected = subprocess.run(
                [sys.executable, str(compiler), str(module_project / "main.lt"),
                 "-o", str(output)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True, check=False)
            self.assertEqual(rejected.returncode, 1,
                             rejected.stdout + rejected.stderr)
            self.assertIn(
                "sub/lib.lt:1:1: module error: cannot read module sub/missing.lt: not-found",
                rejected.stderr)
            self.assertFalse(output.exists())

            (module_project / "sub" / "lib.lt").write_text(
                'say "library"\n', encoding="utf-8", newline="\n")
            unsupported = subprocess.run(
                [sys.executable, str(compiler), str(module_project / "main.lt"),
                 "-o", str(output)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True, check=False)
            self.assertEqual(unsupported.returncode, 1,
                             unsupported.stdout + unsupported.stderr)
            self.assertIn(
                "multi-module Python emission is not yet supported",
                unsupported.stderr)
            self.assertFalse(output.exists())

            generated = []
            for label in ("checkout-a", "checkout-b"):
                project = root / label
                project.mkdir()
                source = project / "portable.lt"
                source.write_text('say "portable"\n',
                                  encoding="utf-8", newline="\n")
                out_dir = root / (label + "-out")
                checked([sys.executable, str(compiler), str(source),
                         "-o", str(out_dir)], cwd=root, env=clean_env)
                generated.append((out_dir / "portable.py").read_bytes())
            self.assertEqual(generated[0], generated[1])
            self.assertNotIn(str(root).encode("utf-8"), generated[0])


if __name__ == "__main__":
    unittest.main()
