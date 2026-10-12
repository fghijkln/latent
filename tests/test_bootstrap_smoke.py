"""Fixed-Stage0 differential and fixed-point tests for bounded bootstrap slices.

Coverage includes Stage0/Stage1 behavior, explicit module-root selection, and
Stage1-to-Stage3 reproducibility. These gates do not imply full Python-target
parity; unsupported features remain fail-closed and are tracked in the plan.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_MANIFEST_REPORT = os.environ.get("P15_BOOTSTRAP_MANIFEST_REPORT")
sys.path.insert(0, str(ROOT))
from tests._safe_test_env import clean_test_environment, isolate_test_environment

isolate_test_environment()
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
    child_env = clean_test_environment() if env is None else env
    result = subprocess.run(command, cwd=cwd, env=child_env, text=True,
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


def run_stage_program(script: Path, cwd: Path,
                      env: dict[str, str] | None = None) -> tuple[int, str, str]:
    execution_env = clean_test_environment()
    if env is not None:
        execution_env.update(env)
    result = subprocess.run(
        [sys.executable, str(script)], cwd=cwd,
        env=execution_env, text=True, encoding="utf-8",
        capture_output=True, check=False)
    return result.returncode, result.stdout, result.stderr


class BootstrapEmitterTests(unittest.TestCase):
    def test_stage0_generates_deterministic_stage1_python_emitter(self):
        with tempfile.TemporaryDirectory(prefix="p15-emitter-") as temporary:
            root = Path(temporary)
            stage0 = root / "stage0"
            extract_stage0(stage0)
            clean_env = clean_test_environment()

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
            missing_sidecar = stage_dirs[0] / "LtRt.java"
            self.assertTrue(missing_sidecar.is_file())
            sidecar_bytes = missing_sidecar.read_bytes()
            missing_sidecar.unlink()
            sidecar_failure_out = root / "missing-sidecar-out"
            sidecar_failure = subprocess.run(
                [sys.executable, str(compiler_a), str(fixture), "-o",
                 str(sidecar_failure_out)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True,
                check=False)
            missing_sidecar.write_bytes(sidecar_bytes)
            self.assertEqual(sidecar_failure.returncode, 1)
            self.assertIn("runtime output error: not-found",
                          sidecar_failure.stderr)
            self.assertTrue(sidecar_failure_out.is_dir())
            self.assertEqual(list(sidecar_failure_out.iterdir()), [])

            symlink_out = root / "sidecar-symlink-out"
            symlink_out.mkdir()
            outside_runtime = root / "outside-runtime.java"
            outside_runtime.write_text("preserve\n", encoding="utf-8")
            (symlink_out / "LtRt.java").symlink_to(outside_runtime)
            symlink_result = subprocess.run(
                [sys.executable, str(compiler_a), str(fixture), "-o",
                 str(symlink_out)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True,
                check=False)
            self.assertEqual(symlink_result.returncode, 0,
                             symlink_result.stderr)
            self.assertEqual(outside_runtime.read_text(), "preserve\n")
            self.assertFalse((symlink_out / "LtRt.java").is_symlink())
            self.assertEqual((symlink_out / "LtRt.java").read_bytes(),
                             (stage_dirs[0] / "LtRt.java").read_bytes())

            blocked_out = root / "blocked-output"
            blocked_out.mkdir()
            (blocked_out / "fixture.py").mkdir()
            blocked_result = subprocess.run(
                [sys.executable, str(compiler_a), str(fixture), "-o",
                 str(blocked_out)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True,
                check=False)
            self.assertEqual(blocked_result.returncode, 1)
            self.assertIn("output error: is-directory", blocked_result.stderr)
            self.assertEqual(
                sorted(path.name for path in blocked_out.iterdir()),
                ["fixture.py"])

            blocked_symlink_out = root / "blocked-symlink-output"
            blocked_symlink_out.mkdir()
            outside_output_dir = root / "outside-output-dir"
            outside_output_dir.mkdir()
            (outside_output_dir / "keep.txt").write_text(
                "preserve\n", encoding="utf-8")
            (blocked_symlink_out / "fixture.py").symlink_to(
                outside_output_dir, target_is_directory=True)
            blocked_symlink_result = subprocess.run(
                [sys.executable, str(compiler_a), str(fixture), "-o",
                 str(blocked_symlink_out)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True,
                check=False)
            self.assertEqual(blocked_symlink_result.returncode, 1)
            self.assertIn("output error: is-directory",
                          blocked_symlink_result.stderr)
            self.assertTrue((blocked_symlink_out / "fixture.py").is_symlink())
            self.assertEqual(
                sorted(path.name for path in blocked_symlink_out.iterdir()),
                ["fixture.py"])
            self.assertEqual(
                sorted(path.name for path in outside_output_dir.iterdir()),
                ["keep.txt"])
            self.assertEqual((outside_output_dir / "keep.txt").read_text(),
                             "preserve\n")

            output_path_file = root / "output-path-file"
            output_path_file.write_text("preserve output path\n",
                                        encoding="utf-8")
            output_path_result = subprocess.run(
                [sys.executable, str(compiler_a), str(fixture),
                 "-o", str(output_path_file)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True,
                check=False)
            self.assertEqual(output_path_result.returncode, 1)
            self.assertEqual(
                output_path_result.stderr,
                f"{output_path_file}: output error: not-a-directory\n")
            self.assertEqual(output_path_file.read_text(),
                             "preserve output path\n")

            linked_main_out = root / "linked-main-output"
            linked_main_out.mkdir()
            outside_main = root / "outside-main.py"
            outside_main.write_text("preserve main\n", encoding="utf-8")
            linked_main = linked_main_out / "fixture.py"
            linked_main.symlink_to(outside_main)
            checked([sys.executable, str(compiler_a), str(fixture),
                     "-o", str(linked_main_out)], cwd=root, env=clean_env)
            self.assertFalse(linked_main.is_symlink())
            self.assertEqual(outside_main.read_text(), "preserve main\n")
            self.assertEqual(run_stage_program(linked_main, root),
                             (0, EXPECTED_STDOUT, ""))

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

            invalid_source = root / "invalid-utf8.lt"
            invalid_source.write_bytes(b"\xff\xfe")
            invalid_out = root / "invalid-utf8-out"
            invalid_result = subprocess.run(
                [sys.executable, str(compiler_a), str(invalid_source),
                 "-o", str(invalid_out)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True,
                check=False)
            self.assertEqual(invalid_result.returncode, 1)
            self.assertEqual(invalid_result.stdout, "")
            self.assertEqual(
                invalid_result.stderr,
                f"{invalid_source}: input error: invalid-utf8\n")
            self.assertFalse(invalid_out.exists())

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
            self.assertIn("--source-root ROOT", help_result.stdout)
            self.assertEqual(help_result.stderr, "")

            argument_errors = (
                ([], "usage: latent source.lt [-t py] [-o OUTDIR] "
                     "[--source-root ROOT] [--run]\n"),
                ([str(fixture), "-o"], "error: -o requires a directory\n"),
                ([str(fixture), "-t"], "error: -t requires a target\n"),
                ([str(fixture), "extra.lt"],
                 "error: unexpected argument extra.lt\n"),
                ([str(fixture), "--show-ast"],
                 "error: diagnostic options are not supported by the bootstrap compiler\n"),
            )
            for arguments, expected_error in argument_errors:
                argument_result = subprocess.run(
                    [sys.executable, str(compiler_a)] + arguments,
                    cwd=root, env=clean_env, text=True, encoding="utf-8",
                    capture_output=True, check=False)
                self.assertEqual(argument_result.returncode, 2)
                self.assertEqual(argument_result.stdout, "")
                self.assertEqual(argument_result.stderr, expected_error)

            missing_root = subprocess.run(
                [sys.executable, str(compiler_a), str(fixture),
                 "--source-root"], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True,
                check=False)
            self.assertEqual(missing_root.returncode, 2)
            self.assertIn("--source-root requires a directory",
                          missing_root.stderr)

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

            runtime_failure = root / "runtime-failure.lt"
            runtime_failure.write_text("say 1 / 0\n", encoding="utf-8",
                                       newline="\n")
            runtime_out = root / "runtime-failure-out"
            runtime_result = subprocess.run(
                [sys.executable, str(compiler_a), str(runtime_failure),
                 "--run", "-o", str(runtime_out)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True,
                check=False)
            self.assertEqual(runtime_result.returncode, 1)
            self.assertEqual(
                runtime_result.stdout,
                f"wrote {runtime_out / 'runtime-failure.py'}\n")
            self.assertIn("ZeroDivisionError", runtime_result.stderr)
            self.assertIn("division by zero", runtime_result.stderr)
            self.assertTrue((runtime_out / "runtime-failure.py").is_file())

            bad_source = root / "compile-failure.lt"
            bad_source.write_text("say missing\n", encoding="utf-8",
                                  newline="\n")
            stage0_bad = subprocess.run(
                [sys.executable, str(stage0 / "latent.py"), str(bad_source),
                 "-t", "py", "-o", str(root / "stage0-bad-out")],
                cwd=root, env=clean_env, text=True, encoding="utf-8",
                capture_output=True, check=False)
            stage1_bad = subprocess.run(
                [sys.executable, str(compiler_a), str(bad_source),
                 "-o", str(root / "stage1-bad-out")],
                cwd=root, env=clean_env, text=True, encoding="utf-8",
                capture_output=True, check=False)
            self.assertEqual(stage0_bad.returncode, 1)
            self.assertEqual(stage1_bad.returncode, 1)
            self.assertEqual(stage1_bad.stderr, stage0_bad.stderr)

            dotted_source = root / "multi.part.name.lt"
            dotted_source.write_text('say "dotted stem"\n',
                                     encoding="utf-8", newline="\n")
            dotted_out = root / "dotted-out"
            checked([sys.executable, str(compiler_a), str(dotted_source),
                     "-o", str(dotted_out)], cwd=root, env=clean_env)
            self.assertTrue((dotted_out / "multi.part.name.py").is_file())

            standalone_project = root / "standalone-project"
            standalone_project.mkdir()
            (standalone_project / "main.lt").write_text(
                'import "lib.lt" as lib\nsay lib.value()\n',
                encoding="utf-8", newline="\n")
            (standalone_project / "lib.lt").write_text(
                'fn value():\n    return "standalone module"\n',
                encoding="utf-8", newline="\n")
            invocation_dirs = [root / "invoke-a", root / "invoke-b"]
            output_dirs = [root / "standalone-out-a", root / "standalone-out-b"]
            for invocation_dir, output_dir in zip(invocation_dirs, output_dirs):
                invocation_dir.mkdir()
                checked(
                    [sys.executable, str(compiler_a),
                     str(standalone_project / "main.lt"),
                     "--source-root", str(standalone_project),
                     "-o", str(output_dir)],
                    cwd=invocation_dir, env=clean_env)
            standalone_snapshots = [
                {path.name: path.read_bytes() for path in output_dir.iterdir()}
                for output_dir in output_dirs]
            self.assertEqual(standalone_snapshots[0], standalone_snapshots[1])
            self.assertEqual(
                run_stage_program(output_dirs[0] / "main.py", invocation_dirs[0]),
                (0, "standalone module\n", ""))

            if os.name == "posix":
                source_root_alias = root / "standalone-root-alias"
                source_root_alias.symlink_to(
                    standalone_project, target_is_directory=True)
                alias_out = root / "standalone-root-alias-out"
                checked(
                    [sys.executable, str(compiler_a),
                     str(source_root_alias / "main.lt"),
                     "--source-root", str(source_root_alias),
                     "-o", str(alias_out)],
                    cwd=invocation_dirs[0], env=clean_env)
                self.assertEqual(
                    run_stage_program(alias_out / "main.py", invocation_dirs[0]),
                    (0, "standalone module\n", ""))

            java_run_out = root / "java-run-out"
            java_run_source = ROOT / "tests" / "java_basic.lt"
            java_run_result = subprocess.run(
                [sys.executable, str(compiler_a), str(java_run_source),
                 "--run", "-o", str(java_run_out)],
                cwd=invocation_dirs[0], env=clean_env, text=True,
                encoding="utf-8", capture_output=True, check=False)
            java_run_program = run_stage_program(
                java_run_out / "java_basic.py", invocation_dirs[0])
            self.assertEqual(java_run_result.returncode, 0,
                             java_run_result.stdout + java_run_result.stderr)
            self.assertTrue(java_run_result.stdout.startswith(
                f"wrote {java_run_out / 'java_basic.py'}\n"))
            self.assertEqual(java_run_result.stdout,
                             f"wrote {java_run_out / 'java_basic.py'}\n"
                             + java_run_program[1])
            self.assertEqual(java_run_result.stderr, java_run_program[2])
            self.assertEqual(java_run_program[0], 0,
                             java_run_program[1] + java_run_program[2])
            self.assertTrue((java_run_out / "LtJavaDaemon.class").is_file())

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
            self.assertIn("Latent runtime error: ZeroDivisionError: float division by zero",
                          run_failure.stderr)
            self.assertIn("runtime-failure.lt:1 in main", run_failure.stderr)
            self.assertNotIn("Traceback (most recent call last):",
                             run_failure.stderr)

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
                    env=clean_test_environment())

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
                    env=clean_test_environment())
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

    def test_java_class_repr_matches_fixed_stage0_and_invalid_super_fails_closed(self):
        cases = (
            ('say java "java.lang.String"\n', True,
             ""),
            ("class Plain:\n    fn method(self):\n        return super.method(self)\n",
             False, "class 'Plain' has no parent for super"),
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
                    env=clean_test_environment(), text=True,
                    encoding="utf-8", capture_output=True, check=False)
                if stage0_accepts:
                    self.assertEqual(stage1_result.returncode, 0,
                                     stage1_result.stdout + stage1_result.stderr)
                    stage0_program = run_stage_program(
                        stage0_out / f"unsupported-{index}.py", root)
                    stage1_program = run_stage_program(
                        stage1_out / f"unsupported-{index}.py", root)
                    self.assertEqual(stage1_program, stage0_program,
                                     stage1_program[2] or stage1_program[1])
                    for runtime_name in ("LtJavaDaemon.java", "JReflect.java",
                                         "LtRt.java"):
                        self.assertTrue((stage1_out / runtime_name).is_file())
                else:
                    self.assertNotEqual(stage1_result.returncode, 0)
                    self.assertIn(expected_error, stage1_result.stderr)
                    self.assertFalse(stage1_out.exists())

    def test_java_interop_runtime_and_numeric_helpers_match_fixed_stage0(self):
        java_dir = ROOT / "tests" / "java"
        tests_dir = ROOT / "tests"
        cookbook_dir = ROOT / "cookbook"
        cases = (
            (tests_dir / "loop.lt", None, False),
            (tests_dir / "phase1_semantics.lt",
             java_dir / "Stage1Overloads.java", False),
            (tests_dir / "java_basic.lt", None, False),
            (tests_dir / "java_lazy.lt", None, False),
            (tests_dir / "mixed.lt", None, False),
            (tests_dir / "try_basic.lt", None, False),
            (tests_dir / "p13_interop_bound_methods.lt",
             java_dir / "P13InteropFixture.java", True),
            (tests_dir / "p14_module_and_static_values.lt",
             java_dir / "P14InteropFixture.java", True),
            (tests_dir / "p14_static_method_extraction.lt",
             java_dir / "P14InteropFixture.java", True),
            (cookbook_dir / "java_strings.lt", None, False),
            (cookbook_dir / "java_collections.lt", None, False),
            (cookbook_dir / "java_time.lt", None, False),
            (cookbook_dir / "java_nio.lt", None, False),
            (cookbook_dir / "java_bigdecimal.lt", None, False),
            (cookbook_dir / "mixed_io.lt", None, False),
            (cookbook_dir / "pipeline.lt", None, False),
            (cookbook_dir / "interop_bound_methods.lt", None, False),
            (cookbook_dir / "interop_function_values.lt", None, False),
        )
        with tempfile.TemporaryDirectory(prefix="p15-java-runtime-") as temporary:
            root = Path(temporary)
            clean_env = clean_test_environment()
            clean_env.pop("LATENT_JAVAD", None)
            stage0 = root / "stage0"
            extract_stage0(stage0)
            stage1_dir = root / "stage1"
            stage1_dir.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(COMPILER_SOURCE), "-t", "py", "-o",
                     str(stage1_dir)], cwd=root, env=clean_env)
            shutil.copy2(ROOT / "bootstrap" / "latent_host.py",
                         stage1_dir / "latent_host.py")
            compiler = stage1_dir / "compiler.py"

            for index, (source, java_fixture, needs_python_fixture) in enumerate(cases):
                with self.subTest(case=source.stem):
                    stage0_out = root / f"stage0-{index}"
                    stage1_out = root / f"stage1-{index}"
                    stage0_out.mkdir()
                    checked([sys.executable, str(stage0 / "latent.py"),
                             str(source), "-t", "py", "-o", str(stage0_out)],
                            cwd=root, env=clean_env)
                    checked([sys.executable, str(compiler), str(source),
                             "-o", str(stage1_out)], cwd=root, env=clean_env)
                    if java_fixture is not None:
                        for output in (stage0_out, stage1_out):
                            checked(["javac", "-cp", str(output), "-d",
                                     str(output), str(java_fixture)],
                                    cwd=root, env=clean_env)
                    runtime_env = dict(clean_env)
                    if needs_python_fixture:
                        runtime_env["PYTHONPATH"] = str(ROOT / "tests" / "python")
                    stage0_result = run_stage_program(
                        stage0_out / f"{source.stem}.py", root, runtime_env)
                    stage1_result = run_stage_program(
                        stage1_out / f"{source.stem}.py", root, runtime_env)
                    self.assertEqual(
                        stage1_result, stage0_result,
                        f"{source.name} differs from fixed Stage0\\n"
                        f"Stage0: {stage0_result!r}\\nStage1: {stage1_result!r}")

    def test_host_attribute_write_fails_closed_at_runtime(self):
        source = ('types = py "types"\n'
                  'obj = types.SimpleNamespace()\n'
                  'obj.value = 1\n')
        with tempfile.TemporaryDirectory(prefix="p15-host-write-") as temporary:
            root = Path(temporary)
            clean_env = clean_test_environment()
            stage0 = root / "stage0"
            extract_stage0(stage0)
            stage1_dir = root / "stage1"
            stage1_dir.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(COMPILER_SOURCE), "-t", "py", "-o",
                     str(stage1_dir)], cwd=root, env=clean_env)
            (stage1_dir / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())
            fixture = root / "host-write.lt"
            fixture.write_text(source, encoding="utf-8", newline="\n")
            stage0_out = root / "stage0-out"
            stage1_out = root / "stage1-out"
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(fixture), "-t", "py", "-o", str(stage0_out)],
                    cwd=root, env=clean_env)
            checked([sys.executable, str(stage1_dir / "compiler.py"),
                     str(fixture), "-o", str(stage1_out)], cwd=root,
                    env=clean_env)
            stage0_result = run_stage_program(stage0_out / "host-write.py", root)
            stage1_result = run_stage_program(stage1_out / "host-write.py", root)
            self.assertEqual(stage0_result[0], 0,
                             stage0_result[1] + stage0_result[2])
            self.assertNotEqual(stage1_result[0], 0)
            self.assertIn(
                "attribute assignment on non-Latent objects is not supported",
                stage1_result[2])

    def test_stage1_compiler_links_simple_module_graph_like_fixed_stage0(self):
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
            clean_env = clean_test_environment()

            module_project = root / "module-project"
            (module_project / "sub").mkdir(parents=True)
            (module_project / "main.lt").write_text(
                'import "sub/lib.lt" as lib\n'
                'import "core.lt" as core\n'
                'fn _ltm_m0_label():\n    return "user symbol"\n'
                'say "root init"\n'
                'say lib.label()\n'
                'say core.label()\n'
                'say core.shadow()\n'
                'say _ltm_m0_label()\n',
                encoding="utf-8", newline="\n")
            (module_project / "core.lt").write_text(
                'PREFIX = "core"\n'
                'say "core init"\n'
                'fn label():\n    return PREFIX + "-label"\n'
                'fn shadow():\n    PREFIX = "local"\n    return PREFIX\n',
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
                'import "../core.lt" as core\n'
                'say "lib init"\n'
                'fn label():\n    return core.label() + "-lib"\n',
                encoding="utf-8", newline="\n")
            stage0_output = root / "module-stage0-output"
            stage1_output = root / "module-stage1-output"
            stage1_repeat = root / "module-stage1-repeat"
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(module_project / "main.lt"), "-t", "py", "-o",
                     str(stage0_output)], cwd=root, env=clean_env)
            checked([sys.executable, str(compiler),
                     str(module_project / "main.lt"), "-o",
                     str(stage1_output)], cwd=root, env=clean_env)
            checked([sys.executable, str(compiler),
                     str(module_project / "main.lt"), "-o",
                     str(stage1_repeat)], cwd=root, env=clean_env)
            stage0_result = run_stage_program(
                stage0_output / "main.py", root)
            stage1_result = run_stage_program(
                stage1_output / "main.py", root)
            self.assertEqual(stage0_result[0], 0,
                             stage0_result[1] + stage0_result[2])
            self.assertEqual(stage1_result, stage0_result)
            self.assertEqual(stage1_result[1],
                             "core init\nlib init\nroot init\n"
                             "core-label-lib\ncore-label\nlocal\n"
                             "user symbol\n")
            generated = (stage1_output / "main.py").read_bytes()
            self.assertEqual(generated,
                             (stage1_repeat / "main.py").read_bytes())
            self.assertNotIn(str(module_project).encode("utf-8"), generated)

            rooted_outputs = []
            for index, label in enumerate(
                    ("rooted-checkout-a", "rooted-checkout-b")):
                project = root / label
                modules = project / "modules"
                app = modules / "app"
                library = modules / "lib"
                app.mkdir(parents=True)
                library.mkdir(parents=True)
                main = app / "main.lt"
                main.write_text(
                    'import "../lib/shared.lt" as shared\n'
                    'say shared.value()\n',
                    encoding="utf-8", newline="\n")
                (library / "shared.lt").write_text(
                    'say "rooted lib init"\n'
                    'fn value():\n    return 42\n',
                    encoding="utf-8", newline="\n")
                stage0_rooted_out = root / (label + "-stage0-out")
                stage1_rooted_out = root / (label + "-stage1-out")
                checked([sys.executable, str(stage0 / "latent.py"),
                         str(main), "-t", "py", "-o", str(stage0_rooted_out)],
                        cwd=root, env=clean_env)
                checked([sys.executable, str(compiler), str(main),
                         "--source-root",
                         "modules" if index == 0 else str(modules), "-o",
                         str(stage1_rooted_out)],
                        cwd=project if index == 0 else root, env=clean_env)
                stage0_rooted = run_stage_program(
                    stage0_rooted_out / "main.py", root)
                stage1_rooted = run_stage_program(
                    stage1_rooted_out / "main.py", root)
                self.assertEqual(stage1_rooted, stage0_rooted)
                self.assertEqual(stage1_rooted,
                                 (0, "rooted lib init\n42\n", ""))
                rooted_bytes = (stage1_rooted_out / "main.py").read_bytes()
                self.assertNotIn(str(project).encode("utf-8"), rooted_bytes)
                rooted_outputs.append(rooted_bytes)
            self.assertEqual(rooted_outputs[0], rooted_outputs[1])

            private = module_project / "private.lt"
            private.write_text('import "core.lt" as core\nsay core._hidden\n',
                               encoding="utf-8", newline="\n")
            private_out = root / "private-out"
            private_result = subprocess.run(
                [sys.executable, str(compiler), str(private), "-o",
                 str(private_out)], cwd=root, env=clean_env,
                text=True, encoding="utf-8", capture_output=True, check=False)
            self.assertEqual(private_result.returncode, 1,
                             private_result.stdout + private_result.stderr)
            self.assertIn("module member '_hidden' is private",
                          private_result.stderr)
            self.assertFalse(private_out.exists())

            try_module = module_project / "try-module.lt"
            try_module.write_text(
                'import "core.lt" as core\n'
                'fn guarded():\n'
                '    try:\n'
                '        return core.label()\n'
                '    catch error:\n'
                '        return "fallback"\n'
                'say guarded()\n'
                'try:\n'
                '    throw "module failure"\n'
                'catch error:\n'
                '    say error\n',
                encoding="utf-8", newline="\n")
            try_stage0_out = root / "try-stage0-out"
            try_out = root / "try-module-out"
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(try_module), "-t", "py", "-o",
                     str(try_stage0_out)], cwd=root, env=clean_env)
            checked([sys.executable, str(compiler), str(try_module), "-o",
                     str(try_out)], cwd=root, env=clean_env)
            try_stage0_result = run_stage_program(try_stage0_out / "try-module.py", root)
            try_stage1_result = run_stage_program(try_out / "try-module.py", root)
            self.assertEqual(try_stage0_result,
                             (0, "core init\ncore-label\nmodule failure\n", ""))
            self.assertEqual(try_stage1_result, try_stage0_result)

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

    def test_multi_module_classes_closures_and_scope_match_fixed_stage0(self):
        fixtures = (
            "modules/app/main", "modules/same/main",
            "modules/inheritance/main", "modules/bound_methods/main",
            "modules/default_arguments/main", "modules/keyword_only/main",
            "modules/function_values/main", "modules/nonlocal/main",
        )
        with tempfile.TemporaryDirectory(prefix="p15-module-linker-") as temporary:
            root = Path(temporary)
            clean_env = clean_test_environment()
            stage0 = root / "stage0"
            extract_stage0(stage0)
            stage1_dir = root / "stage1"
            stage1_dir.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(COMPILER_SOURCE), "-t", "py", "-o",
                     str(stage1_dir)], cwd=root, env=clean_env)
            (stage1_dir / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())

            for name in fixtures:
                with self.subTest(fixture=name):
                    source = ROOT / "tests" / (name + ".lt")
                    stage0_out = root / (name.replace("/", "-") + "-stage0")
                    stage1_out = root / (name.replace("/", "-") + "-stage1")
                    checked([sys.executable, str(stage0 / "latent.py"),
                             str(source), "-t", "py", "-o", str(stage0_out)],
                            cwd=root, env=clean_env)
                    checked([sys.executable, str(stage1_dir / "compiler.py"),
                             str(source), "--source-root",
                             str(ROOT / "tests" / "modules"), "-o",
                             str(stage1_out)], cwd=root, env=clean_env)
                    stage0_result = run_stage_program(
                        stage0_out / "main.py", Path(tempfile.gettempdir()))
                    stage1_result = run_stage_program(
                        stage1_out / "main.py", Path(tempfile.gettempdir()))
                    self.assertEqual(stage0_result[0], 0,
                                     stage0_result[1] + stage0_result[2])
                    self.assertEqual(stage1_result, stage0_result,
                                     name + ": " + stage1_result[2] +
                                     stage1_result[1])
                    self.assertNotIn(str(ROOT).encode("utf-8"),
                                     (stage1_out / "main.py").read_bytes())

    def test_module_interpolation_globals_match_fixed_stage0(self):
        with tempfile.TemporaryDirectory(prefix="p15-module-interpolation-") as temporary:
            root = Path(temporary)
            clean_env = clean_test_environment()
            stage0 = root / "stage0"
            extract_stage0(stage0)
            stage1_dir = root / "stage1"
            stage1_dir.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(COMPILER_SOURCE), "-t", "py", "-o",
                     str(stage1_dir)], cwd=root, env=clean_env)
            (stage1_dir / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())
            source = ROOT / "cookbook" / "modules.lt"
            stage0_out = root / "stage0-out"
            stage1_out = root / "stage1-out"
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(source), "-t", "py", "-o", str(stage0_out)],
                    cwd=root, env=clean_env)
            checked([sys.executable, str(stage1_dir / "compiler.py"),
                     str(source), "--source-root", str(ROOT / "cookbook"),
                     "-o", str(stage1_out)], cwd=root, env=clean_env)
            stage0_result = run_stage_program(
                stage0_out / "modules.py", Path(tempfile.gettempdir()))
            stage1_result = run_stage_program(
                stage1_out / "modules.py", Path(tempfile.gettempdir()))
            self.assertEqual(stage0_result[0], 0,
                             stage0_result[1] + stage0_result[2])
            self.assertEqual(stage1_result, stage0_result,
                             stage1_result[2] + stage1_result[1])
            self.assertNotIn(str(ROOT).encode("utf-8"),
                             (stage1_out / "modules.py").read_bytes())

    def test_python_interop_and_try_match_fixed_stage0(self):
        source = (
            'math = py "math"\n'
            'say math\n'
            'say math.sqrt(9)\n'
            'host = py "latent_host"\n'
            'say host.path_basename("root/leaf")\n'
            'try:\n'
            '    throw "expected"\n'
            'catch problem:\n'
            '    say "caught: $problem"\n'
            'try:\n'
            '    say "normal"\n'
            'catch untouched:\n'
            '    say "unreachable"\n'
            'say untouched\n')
        with tempfile.TemporaryDirectory(prefix="p15-interop-try-") as temporary:
            root = Path(temporary)
            stage0 = root / "stage0"
            extract_stage0(stage0)
            stage1_dir = root / "stage1"
            stage1_dir.mkdir()
            clean_env = clean_test_environment()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(COMPILER_SOURCE), "-t", "py", "-o",
                     str(stage1_dir)], cwd=root)
            (stage1_dir / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())
            fixture = root / "interop-try.lt"
            fixture.write_text(source, encoding="utf-8", newline="\n")
            stage0_out = root / "stage0-out"
            stage1_out = root / "stage1-out"
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(fixture), "-t", "py", "-o", str(stage0_out)],
                    cwd=root, env=clean_env)
            checked([sys.executable, str(stage1_dir / "compiler.py"),
                     str(fixture), "-o", str(stage1_out)], cwd=root,
                    env=clean_env)
            for output in (stage0_out, stage1_out):
                (output / "latent_host.py").write_bytes(
                    (ROOT / "bootstrap" / "latent_host.py").read_bytes())
            stage0_result = run_stage_program(stage0_out / "interop-try.py", root)
            stage1_result = run_stage_program(stage1_out / "interop-try.py", root)
            self.assertEqual(stage0_result,
                             (0, "<module math>\n3\nleaf\ncaught: expected\nnormal\nnil\n", ""))
            self.assertEqual(stage1_result, stage0_result)

    def test_single_file_class_basics_match_fixed_stage0(self):
        source = r'''fn _lt_bootstrap_make_class_Point():
    return "user factory"

class Point:
    fn init(self, x=1, y=2):
        self.x = x
        self.y = y

    fn move(self, dx, dy):
        self.x = self.x + dx
        self.y = self.y + dy
        return self

    fn total(self):
        return self.x + self.y

class Empty:
    fn ping(self):
        return "pong"

p = Point.new(3, 4)
q = Point.new()
say Point
say p
say p.x
say p.y
say p == p
say p == q
bound = p.move
say bound
say bound(2, 3)
say p.total()
try:
    bound()
catch err:
    say err
try:
    say p.missing
catch err:
    say err
try:
    p.missing()
catch err:
    say err
p.extra = 99
say p.extra
e = Empty.new()
say e.ping()
say Empty
say _lt_bootstrap_make_class_Point()
'''
        with tempfile.TemporaryDirectory(prefix="p15-class-basics-") as temporary:
            root = Path(temporary)
            clean_env = clean_test_environment()
            stage0 = root / "stage0"
            extract_stage0(stage0)
            stage1_dir = root / "stage1"
            stage1_dir.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(COMPILER_SOURCE), "-t", "py", "-o",
                     str(stage1_dir)], cwd=root, env=clean_env)
            (stage1_dir / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())
            fixture = root / "classes.lt"
            fixture.write_text(source, encoding="utf-8", newline="\n")
            stage0_out = root / "stage0-out"
            stage1_out = root / "stage1-out"
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(fixture), "-t", "py", "-o", str(stage0_out)],
                    cwd=root, env=clean_env)
            checked([sys.executable, str(stage1_dir / "compiler.py"),
                     str(fixture), "-o", str(stage1_out)], cwd=root,
                    env=clean_env)
            stage0_result = run_stage_program(stage0_out / "classes.py", root)
            stage1_result = run_stage_program(stage1_out / "classes.py", root)
            self.assertEqual(stage0_result[0], 0,
                             stage0_result[1] + stage0_result[2])
            self.assertEqual(stage1_result, stage0_result,
                             stage1_result[2] or stage1_result[1])
            self.assertEqual(stage0_result[1],
                             "<class Point>\n<Point object>\n3\n4\ntrue\n"
                             "false\n<bound method Point.move>\n<Point object>\n"
                             "12\nmove() takes 2 args, got 0\n"
                             "no field 'missing'\nno method 'missing'\n"
                             "99\npong\n<class Empty>\nuser factory\n")

            repository_fixture = ROOT / "tests" / "class_basic.lt"
            repository_stage0_out = root / "repository-stage0-out"
            repository_stage1_out = root / "repository-stage1-out"
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(repository_fixture), "-t", "py", "-o",
                     str(repository_stage0_out)], cwd=root, env=clean_env)
            checked([sys.executable, str(stage1_dir / "compiler.py"),
                     str(repository_fixture), "-o",
                     str(repository_stage1_out)], cwd=root, env=clean_env)
            repository_stage0_result = run_stage_program(
                repository_stage0_out / "class_basic.py", root)
            repository_stage1_result = run_stage_program(
                repository_stage1_out / "class_basic.py", root)
            self.assertEqual(repository_stage0_result[0], 0,
                             repository_stage0_result[1] +
                             repository_stage0_result[2])
            self.assertEqual(repository_stage1_result,
                             repository_stage0_result,
                             repository_stage1_result[2] or
                             repository_stage1_result[1])

            semantic_cases = (
                ("duplicate-method", "class A:\n    fn init(self):\n        return nil\n\n"
                 "    fn init(self, value):\n        return value\n"),
                ("reserved-new", "class A:\n    fn new(self):\n        return nil\n"),
                ("missing-receiver", "class A:\n    fn broken():\n        return nil\n"),
                ("default-receiver", "class A:\n    fn broken(self=nil):\n        return nil\n"),
                ("constructor-arity", "class Empty:\n    fn ping(self):\n        return 'pong'\n\nEmpty.new(1)\n"),
            )
            for name, invalid_source in semantic_cases:
                invalid = root / f"{name}.lt"
                invalid.write_text(invalid_source, encoding="utf-8",
                                   newline="\n")
                stage0_error = subprocess.run(
                    [sys.executable, str(stage0 / "latent.py"), str(invalid),
                     "-t", "py", "-o", str(root / (name + "-stage0"))],
                    cwd=root, env=clean_env, text=True, encoding="utf-8",
                    capture_output=True, check=False)
                stage1_error = subprocess.run(
                    [sys.executable, str(stage1_dir / "compiler.py"),
                     str(invalid), "-o", str(root / (name + "-stage1"))],
                    cwd=root, env=clean_env, text=True, encoding="utf-8",
                    capture_output=True, check=False)
                self.assertEqual(stage0_error.returncode, 1,
                                 stage0_error.stdout + stage0_error.stderr)
                self.assertEqual(stage1_error.returncode,
                                 stage0_error.returncode)
                self.assertEqual(stage1_error.stderr.splitlines()[0],
                                 stage0_error.stderr.splitlines()[0])

    def test_single_file_inheritance_super_and_override_match_fixed_stage0(self):
        with tempfile.TemporaryDirectory(prefix="p15-inheritance-") as temporary:
            root = Path(temporary)
            clean_env = clean_test_environment()
            stage0 = root / "stage0"
            extract_stage0(stage0)
            stage1_dir = root / "stage1"
            stage1_dir.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(COMPILER_SOURCE), "-t", "py", "-o",
                     str(stage1_dir)], cwd=root, env=clean_env)
            (stage1_dir / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())

            for name in ("inheritance", "p12_override_compatibility"):
                fixture = ROOT / "tests" / f"{name}.lt"
                stage0_out = root / f"{name}-stage0"
                stage1_out = root / f"{name}-stage1"
                checked([sys.executable, str(stage0 / "latent.py"),
                         str(fixture), "-t", "py", "-o", str(stage0_out)],
                        cwd=root, env=clean_env)
                checked([sys.executable, str(stage1_dir / "compiler.py"),
                         str(fixture), "-o", str(stage1_out)], cwd=root,
                        env=clean_env)
                stage0_result = run_stage_program(
                    stage0_out / f"{name}.py", root)
                stage1_result = run_stage_program(
                    stage1_out / f"{name}.py", root)
                self.assertEqual(stage0_result[0], 0,
                                 stage0_result[1] + stage0_result[2])
                self.assertEqual(stage1_result, stage0_result,
                                 stage1_result[2] or stage1_result[1])

            negative_fixtures = (
                sorted((ROOT / "tests" / "inheritance_negative").glob("*.lt"))
                + sorted((ROOT / "tests" / "p12_negative").glob("*.lt"))
                + [ROOT / "tests" / "inheritance_bad_init_arity.lt",
                   ROOT / "tests" / "p8_negative" / "super_unknown.lt",
                   ROOT / "tests" / "p11_negative" / "super_missing.lt"]
            )
            self.assertEqual(len(negative_fixtures), 21)
            for fixture in negative_fixtures:
                label = fixture.relative_to(ROOT / "tests").as_posix().replace(
                    "/", "_").removesuffix(".lt")
                stage0_out = root / f"negative-{label}-stage0"
                stage1_out = root / f"negative-{label}-stage1"
                stage0_error = subprocess.run(
                    [sys.executable, str(stage0 / "latent.py"), str(fixture),
                     "-t", "py", "-o", str(stage0_out)], cwd=root,
                    env=clean_env, text=True, encoding="utf-8",
                    capture_output=True, check=False)
                stage1_error = subprocess.run(
                    [sys.executable, str(stage1_dir / "compiler.py"),
                     str(fixture), "-o", str(stage1_out)], cwd=root,
                    env=clean_env, text=True, encoding="utf-8",
                    capture_output=True, check=False)
                self.assertEqual(stage0_error.returncode, 1,
                                 stage0_error.stdout + stage0_error.stderr)
                self.assertEqual(stage1_error.returncode, 1,
                                 stage1_error.stdout + stage1_error.stderr)
                self.assertEqual(stage1_error.stderr.splitlines()[0],
                                 stage0_error.stderr.splitlines()[0], label)
                self.assertFalse(stage0_out.exists())
                self.assertFalse(stage1_out.exists())

    def test_scope_and_known_method_negative_diagnostics_match_fixed_stage0(self):
        with tempfile.TemporaryDirectory(prefix="p15-negative-semantic-") as temporary:
            root = Path(temporary)
            clean_env = clean_test_environment()
            stage0 = root / "stage0"
            extract_stage0(stage0)
            stage1_dir = root / "stage1"
            stage1_dir.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(COMPILER_SOURCE), "-t", "py", "-o",
                     str(stage1_dir)], cwd=root, env=clean_env)
            (stage1_dir / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())

            fixtures = [
                ROOT / "tests" / "err_exprstmt.lt",
                ROOT / "tests" / "err_p6_nonlocal_duplicate.lt",
                ROOT / "tests" / "err_p6_nonlocal_global.lt",
                ROOT / "tests" / "err_p6_nonlocal_missing.lt",
                ROOT / "tests" / "err_p6_nonlocal_parameter.lt",
                ROOT / "tests" / "err_p6_nonlocal_top.lt",
                ROOT / "tests" / "p8_negative" / "method_missing.lt",
                ROOT / "tests" / "p8_negative" / "method_unknown.lt",
                ROOT / "tests" / "p11_negative" / "method_missing.lt",
            ]
            for fixture in fixtures:
                label = fixture.relative_to(ROOT / "tests").as_posix().replace(
                    "/", "_").removesuffix(".lt")
                stage0_out = root / f"negative-{label}-stage0"
                stage1_out = root / f"negative-{label}-stage1"
                stage0_error = subprocess.run(
                    [sys.executable, str(stage0 / "latent.py"), str(fixture),
                     "-t", "py", "-o", str(stage0_out)], cwd=root,
                    env=clean_env, text=True, encoding="utf-8",
                    capture_output=True, check=False)
                stage1_error = subprocess.run(
                    [sys.executable, str(stage1_dir / "compiler.py"),
                     str(fixture), "-o", str(stage1_out)], cwd=root,
                    env=clean_env, text=True, encoding="utf-8",
                    capture_output=True, check=False)
                self.assertEqual(stage0_error.returncode, 1,
                                 stage0_error.stdout + stage0_error.stderr)
                self.assertEqual(stage1_error.returncode, 1,
                                 stage1_error.stdout + stage1_error.stderr)
                self.assertEqual(stage1_error.stderr.splitlines()[0],
                                 stage0_error.stderr.splitlines()[0], label)
                self.assertFalse(stage0_out.exists())
                self.assertFalse(stage1_out.exists())

    def test_function_scope_global_nonlocal_and_closures_match_fixed_stage0(self):
        with tempfile.TemporaryDirectory(prefix="p15-function-scope-") as temporary:
            root = Path(temporary)
            clean_env = clean_test_environment()
            stage0 = root / "stage0"
            extract_stage0(stage0)
            stage1_dir = root / "stage1"
            stage1_dir.mkdir()
            checked([sys.executable, str(stage0 / "latent.py"),
                     str(COMPILER_SOURCE), "-t", "py", "-o",
                     str(stage1_dir)], cwd=root, env=clean_env)
            (stage1_dir / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())

            fixtures = [ROOT / "tests" / f"{name}.lt" for name in (
                "scope", "p5_global", "p5_closure_capture",
                "p5_shared_capture", "p5_recursion", "p5_shadowing",
                "p6_nonlocal_nearest", "p6_nonlocal_skip",
                "p6_nonlocal_shared", "p6_nonlocal_returned",
                "p6_nonlocal_nested_scope", "p8_named_arguments",
                "p9_default_parameters", "p10_variadic_arguments",
                "p11_keyword_only_parameters", "loop", "p5_invalid_calls",
                "py_numpy")]
            fixtures.extend((ROOT / "cookbook" / "py_math.lt",
                             ROOT / "cookbook" / "py_os.lt"))
            fixtures.append(ROOT / "cookbook" / "functions.lt")
            for fixture in fixtures:
                label = fixture.relative_to(ROOT).as_posix().replace(
                    "/", "_").removesuffix(".lt")
                stage0_out = root / f"{label}-stage0"
                stage1_out = root / f"{label}-stage1"
                checked([sys.executable, str(stage0 / "latent.py"),
                         str(fixture), "-t", "py", "-o", str(stage0_out)],
                        cwd=root, env=clean_env)
                checked([sys.executable, str(stage1_dir / "compiler.py"),
                         str(fixture), "-o", str(stage1_out)], cwd=root,
                        env=clean_env)
                stage0_result = run_stage_program(
                    stage0_out / f"{fixture.stem}.py", root)
                stage1_result = run_stage_program(
                    stage1_out / f"{fixture.stem}.py", root)
                self.assertEqual(stage0_result[0], 0,
                                 stage0_result[1] + stage0_result[2])
                self.assertEqual(stage1_result, stage0_result,
                                 f"{label}: {stage1_result[2] or stage1_result[1]}")

    def test_stage1_compiles_stage2_and_stage3_to_a_dual_root_fixed_point(self):
        with tempfile.TemporaryDirectory(prefix="p15-fixed-point-") as temporary:
            root = Path(temporary)
            clean_env = clean_test_environment()
            clean_env.pop("P15_BOOTSTRAP_MANIFEST_REPORT", None)
            no_javac = root / "no-javac"
            no_javac.mkdir()
            stage0_env = {**clean_env, "PATH": str(no_javac)}
            declared = ("compiler.py", "latent_host.py", "LtJavaDaemon.java",
                        "JReflect.java", "LtRt.java")
            source_commit = checked(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                cwd=ROOT).stdout.strip()
            tracked_paths = [
                path for path in checked(
                    ["git", "-C", str(ROOT), "ls-files", "-z"],
                    cwd=ROOT).stdout.split("\0") if path
            ]

            def source_tree_snapshot(source_root: Path, *,
                                     include_mtime: bool = True,
                                     include_ctime: bool = True) -> dict[str, str]:
                entries = {}
                for path in (source_root, *source_root.rglob("*")):
                    relative = path.relative_to(source_root)
                    # Omit only this checkout's Git metadata; a nested .git
                    # path can be ordinary source data and must be snapshotted.
                    if relative.parts and relative.parts[0] == ".git":
                        continue
                    metadata = path.lstat()
                    if stat.S_ISLNK(metadata.st_mode):
                        entry_type = b"symlink"
                        content = os.readlink(path).encode("utf-8")
                    elif stat.S_ISREG(metadata.st_mode):
                        entry_type = b"file"
                        content = path.read_bytes()
                    elif stat.S_ISDIR(metadata.st_mode):
                        entry_type = b"directory"
                        content = b""
                    else:
                        entry_type = (
                            f"special-{stat.S_IFMT(metadata.st_mode)}".encode("ascii"))
                        content = b""
                    mode = f"{stat.S_IMODE(metadata.st_mode):o}".encode("ascii")
                    parts = [entry_type, mode]
                    if include_mtime:
                        parts.append(str(metadata.st_mtime_ns).encode("ascii"))
                    # On POSIX, ctime is inode change time; unlike mtime it
                    # still exposes a write whose original bytes/mtime return.
                    if include_ctime and os.name != "nt":
                        parts.append(str(metadata.st_ctime_ns).encode("ascii"))
                    parts.append(content)
                    payload = b"\0".join(parts)
                    entries[relative.as_posix()] = hashlib.sha256(payload).hexdigest()
                return entries

            # Prove the snapshot distinguishes an ordinary file from a directory
            # even when the file bytes equal the old directory marker.
            snapshot_probe = root / "snapshot-probe"
            snapshot_probe.mkdir()
            root_git_config = snapshot_probe / ".git" / "config"
            root_git_config.parent.mkdir()
            root_git_config.write_bytes(b"root metadata")
            nested_git_payload = snapshot_probe / "nested" / ".git" / "payload"
            nested_git_payload.parent.mkdir(parents=True)
            nested_git_payload.write_bytes(b"nested source data")
            probe_paths = source_tree_snapshot(snapshot_probe)
            self.assertNotIn(".git/config", probe_paths,
                             "snapshots must omit root Git metadata")
            self.assertIn("nested/.git/payload", probe_paths,
                          "snapshots must retain nested .git source data")
            probe_entry = snapshot_probe / "entry"
            probe_entry.write_bytes(b"<directory>")
            file_snapshot = source_tree_snapshot(snapshot_probe)
            metadata = probe_entry.lstat()
            os.utime(probe_entry, ns=(metadata.st_atime_ns,
                                      metadata.st_mtime_ns + 1_000_000_000))
            touched_snapshot = source_tree_snapshot(snapshot_probe)
            self.assertNotEqual(file_snapshot, touched_snapshot,
                                "source snapshots must detect persistent touches")

            # A transient write can restore both bytes and mtime. POSIX ctime
            # must still make that source-tree mutation observable.
            if os.name != "nt":
                restored_baseline = source_tree_snapshot(snapshot_probe)
                original_metadata = probe_entry.lstat()
                original_bytes = probe_entry.read_bytes()
                time.sleep(1.1)
                probe_entry.write_bytes(b"temporarily modified")
                probe_entry.write_bytes(original_bytes)
                os.utime(probe_entry, ns=(original_metadata.st_atime_ns,
                                          original_metadata.st_mtime_ns))
                after_restore = probe_entry.lstat()
                self.assertEqual(after_restore.st_mtime_ns,
                                 original_metadata.st_mtime_ns)
                self.assertNotEqual(after_restore.st_ctime_ns,
                                    original_metadata.st_ctime_ns)
                self.assertEqual(probe_entry.read_bytes(), original_bytes)
                self.assertNotEqual(
                    restored_baseline, source_tree_snapshot(snapshot_probe),
                    "source snapshots must detect writes after bytes/mtime restore")

            probe_entry.unlink()
            probe_entry.mkdir()
            directory_snapshot = source_tree_snapshot(snapshot_probe)
            self.assertNotEqual(file_snapshot, directory_snapshot,
                                "source snapshots must preserve entry type")
            shutil.rmtree(snapshot_probe)

            def create_full_checkout(name: str) -> tuple[Path, str]:
                checkout = root / name
                # Start from a complete repository clone, not a copied bootstrap/
                # subtree, so repository-wide relative dependencies are present.
                checked(["git", "clone", "--quiet", "--no-hardlinks",
                         str(ROOT), str(checkout)], cwd=ROOT)
                commit = checked(
                    ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                    cwd=ROOT).stdout.strip()
                status = checked(
                    ["git", "-C", str(checkout), "status", "--porcelain=v1",
                     "--untracked-files=all"], cwd=ROOT).stdout
                if status:
                    raise AssertionError(
                        f"{name} is not a clean checkout: {status[:1000]}")
                required_paths = (
                    ".github/workflows/tests.yml", "README.md", "latent.py",
                    "bootstrap/compiler.lt", "bootstrap/latent_host.py",
                    "tests/test_bootstrap_smoke.py", "tests/bootstrap/manifest.json",
                    "cookbook/pipeline.lt", ".gitignore")
                missing = [path for path in required_paths
                           if not (checkout / path).is_file()]
                if missing:
                    raise AssertionError(
                        f"{name} is not a complete repository checkout; "
                        f"missing {missing}")
                missing_tracked = [
                    path for path in tracked_paths
                    if not os.path.lexists(checkout / path)
                ]
                if missing_tracked:
                    raise AssertionError(
                        f"{name} is missing tracked repository paths: "
                        f"{missing_tracked[:20]}")
                return checkout, commit

            def snapshot(stage: Path) -> dict[str, object]:
                files = {}
                other_entries = []
                for path in sorted(stage.rglob("*"),
                                   key=lambda item: item.relative_to(stage).as_posix()):
                    relative = path.relative_to(stage).as_posix()
                    if path.is_symlink() or not path.is_file():
                        other_entries.append(relative)
                    else:
                        files[relative] = path.read_bytes()
                return {"files": files, "other_entries": other_entries}

            def manifest(stage_snapshot: dict[str, object]) -> list[dict[str, str]]:
                files = stage_snapshot["files"]
                return [{"path": name,
                         "sha256": hashlib.sha256(content).hexdigest()}
                        for name, content in sorted(files.items())]

            def manifest_diff(left: dict[str, object],
                              right: dict[str, object]) -> list[dict[str, str | None]]:
                left_files = left["files"]
                right_files = right["files"]
                differences = []
                for name in sorted(set(left_files) | set(right_files)):
                    left_hash = (hashlib.sha256(left_files[name]).hexdigest()
                                 if name in left_files else None)
                    right_hash = (hashlib.sha256(right_files[name]).hexdigest()
                                  if name in right_files else None)
                    if left_hash != right_hash:
                        differences.append({"path": name,
                                            "left_sha256": left_hash,
                                            "right_sha256": right_hash})
                return differences

            def build_chain(label: str,
                            source_root: Path) -> dict[str, object]:
                stage0 = root / (label + "-stage0")
                extract_stage0(stage0)
                stage1 = root / (label + "-stage1")
                stage1.mkdir()
                source = source_root / "bootstrap" / "compiler.lt"
                adapter = (source_root / "bootstrap" / "latent_host.py").read_bytes()
                checked([sys.executable, str(stage0 / "latent.py"),
                         str(source), "-t", "py", "-o", str(stage1)],
                        cwd=root, env=stage0_env)
                (stage1 / "latent_host.py").write_bytes(adapter)
                stage1_snapshot = snapshot(stage1)

                # Stage1 must compile with no Stage0 files or Python compiler
                # modules present in its environment.
                shutil.rmtree(stage0)
                stage2 = root / (label + "-stage2")
                checked([sys.executable, str(stage1 / "compiler.py"),
                         str(source), "-o", str(stage2)], cwd=root,
                        env=clean_env)
                (stage2 / "latent_host.py").write_bytes(adapter)
                stage3 = root / (label + "-stage3")
                checked([sys.executable, str(stage2 / "compiler.py"),
                         str(source), "-o", str(stage3)], cwd=root,
                        env=clean_env)
                (stage3 / "latent_host.py").write_bytes(adapter)

                return {"stage1": stage1_snapshot,
                        "stage2": snapshot(stage2),
                        "stage3": snapshot(stage3)}

            source_a, commit_a = create_full_checkout("checkout-a")
            source_b, commit_b = create_full_checkout("checkout-b")
            self.assertEqual(commit_a, source_commit)
            self.assertEqual(commit_b, source_commit)
            source_content_mode_a = source_tree_snapshot(
                source_a, include_mtime=False, include_ctime=False)
            source_content_mode_b = source_tree_snapshot(
                source_b, include_mtime=False, include_ctime=False)
            self.assertEqual(source_content_mode_a, source_content_mode_b,
                             "the full checkout content/modes differ before builds")
            # Git checkout mtimes/ctimes naturally differ; retain a separate
            # timestamp baseline per root and require it to remain unchanged.
            source_before_a = source_tree_snapshot(source_a)
            source_before_b = source_tree_snapshot(source_b)
            source_tree_digest = hashlib.sha256(
                json.dumps(source_content_mode_a, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")).hexdigest()

            first = build_chain("checkout-a", source_a)
            second = build_chain("checkout-b", source_b)
            self.assertEqual(source_tree_snapshot(source_a), source_before_a,
                             "Stage0/1/2/3 build wrote into checkout-a")
            self.assertEqual(source_tree_snapshot(source_b), source_before_b,
                             "Stage0/1/2/3 build wrote into checkout-b")

            def write_report() -> None:
                report_name = BOOTSTRAP_MANIFEST_REPORT
                if not report_name:
                    return
                report_path = Path(report_name)
                chains = {"checkout-a": first, "checkout-b": second}
                serialized = {}
                for root_name, chain in chains.items():
                    serialized[root_name] = {}
                    for stage_name in ("stage1", "stage2", "stage3"):
                        stage = chain[stage_name]
                        serialized[root_name][stage_name] = {
                            "manifest": manifest(stage),
                            "missing_declared": sorted(
                                set(declared) - set(stage["files"])),
                            "unexpected_files": sorted(
                                set(stage["files"]) - set(declared)),
                            "non_file_entries": stage["other_entries"]}
                stage_comparisons = {}
                for root_name, chain in chains.items():
                    stage2 = chain["stage2"]
                    stage3 = chain["stage3"]
                    stage_comparisons[root_name] = {
                        "stage2_stage3_manifest_equal":
                            manifest(stage2) == manifest(stage3),
                        "stage2_stage3_bytes_equal":
                            stage2["files"] == stage3["files"] and
                            set(stage2["files"]) == set(declared) and
                            set(stage3["files"]) == set(declared) and
                            not stage2["other_entries"] and
                            not stage3["other_entries"],
                        "stage2_stage3_file_differences":
                            manifest_diff(stage2, stage3)}
                cross_root = {}
                for stage_name in ("stage2", "stage3"):
                    left = first[stage_name]
                    right = second[stage_name]
                    cross_root[stage_name] = {
                        "manifest_equal": manifest(left) == manifest(right),
                        "bytes_equal": left["files"] == right["files"] and
                            set(left["files"]) == set(declared) and
                            set(right["files"]) == set(declared) and
                            not left["other_entries"] and not right["other_entries"],
                        "file_differences": manifest_diff(left, right)}
                payload = {
                    "source_commit": source_commit,
                    "source_checkout_manifest": {
                        "checkout_head_commits": {
                            "checkout-a": commit_a,
                            "checkout-b": commit_b},
                        "tracked_file_count": len(tracked_paths),
                        "entry_count": len(source_before_a),
                        "sha256": source_tree_digest,
                        "both_checkouts_content_mode_identical":
                            source_content_mode_a == source_content_mode_b,
                        "both_git_worktrees_clean": True,
                        "working_tree_write_free": True},
                    "stage0_commit": STAGE0_COMMIT,
                    "declared_files": sorted(declared),
                    "checkouts": serialized,
                    "within_root_fixed_point": stage_comparisons,
                    "cross_root_stage2_stage3": cross_root,
                    "stage1_cross_root_note":
                        "Stage1 is reported but may differ due to fixed Stage0 path behavior."}
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")

            write_report()

            expected = set(declared)
            for root_name, chain in (("checkout-a", first),
                                     ("checkout-b", second)):
                for stage_name, stage in chain.items():
                    with self.subTest(root=root_name, stage=stage_name):
                        self.assertEqual(set(stage["files"]), expected)
                        self.assertEqual(stage["other_entries"], [])
                self.assertEqual(manifest(chain["stage2"]),
                                 manifest(chain["stage3"]))
                self.assertEqual(chain["stage2"]["files"],
                                 chain["stage3"]["files"])
            for stage_name in ("stage2", "stage3"):
                self.assertEqual(manifest(first[stage_name]),
                                 manifest(second[stage_name]))
                self.assertEqual(first[stage_name]["files"],
                                 second[stage_name]["files"])


if __name__ == "__main__":
    unittest.main()
