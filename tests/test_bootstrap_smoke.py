"""Fixed-Stage0 smoke test for the first Latent Python-emitter slice.

The compiler subset is intentionally limited to the current M4 parser/AST. The
module source root is fixed for this slice because current module identities
and source maps include normalized absolute paths. Stage1-to-Stage2 self-hosting
is deferred until the Latent compiler can parse and resolve its own modules.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
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
            self.assertIn("--source-root ROOT", help_result.stdout)
            self.assertEqual(help_result.stderr, "")

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

    def test_unimplemented_interop_and_invalid_super_fail_closed(self):
        cases = (
            ('say java "java.lang.String"\n', True,
             "Java interop requires the unavailable target runtime"),
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
                    env={**os.environ, "PYTHONPATH": ""}, text=True,
                    encoding="utf-8", capture_output=True, check=False)
                self.assertNotEqual(stage1_result.returncode, 0)
                self.assertIn(expected_error, stage1_result.stderr)
                self.assertFalse(stage1_out.exists())

    def test_host_attribute_write_fails_closed_at_runtime(self):
        source = ('types = py "types"\n'
                  'obj = types.SimpleNamespace()\n'
                  'obj.value = 1\n')
        with tempfile.TemporaryDirectory(prefix="p15-host-write-") as temporary:
            root = Path(temporary)
            clean_env = {**os.environ, "PYTHONPATH": ""}
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
            clean_env = {**os.environ, "PYTHONPATH": ""}

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
            clean_env = {**os.environ, "PYTHONPATH": "",
                         "PYTHONDONTWRITEBYTECODE": "1"}
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
            clean_env = {**os.environ, "PYTHONPATH": "",
                         "PYTHONDONTWRITEBYTECODE": "1"}
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
            clean_env = {**os.environ, "PYTHONPATH": "",
                         "PYTHONDONTWRITEBYTECODE": "1"}
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

    def test_function_scope_global_nonlocal_and_closures_match_fixed_stage0(self):
        with tempfile.TemporaryDirectory(prefix="p15-function-scope-") as temporary:
            root = Path(temporary)
            clean_env = {**os.environ, "PYTHONPATH": "",
                         "PYTHONDONTWRITEBYTECODE": "1"}
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
                "p11_keyword_only_parameters", "loop", "p5_invalid_calls")]
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
                                 stage1_result[2] or stage1_result[1])

    def test_stage1_compiles_stage2_and_stage3_to_a_dual_root_fixed_point(self):
        with tempfile.TemporaryDirectory(prefix="p15-fixed-point-") as temporary:
            root = Path(temporary)
            clean_env = {**os.environ, "PYTHONPATH": "",
                         "PYTHONDONTWRITEBYTECODE": "1"}
            adapter = (ROOT / "bootstrap" / "latent_host.py").read_bytes()
            declared = ("compiler.py", "latent_host.py")

            def manifest(stage: Path) -> tuple[tuple[str, str], ...]:
                self.assertEqual(sorted(path.name for path in stage.iterdir()),
                                 sorted(declared))
                return tuple(
                    (name, hashlib.sha256((stage / name).read_bytes()).hexdigest())
                    for name in sorted(declared))

            def build_chain(label: str, source_root: Path) -> tuple[tuple[tuple[str, str], ...], ...]:
                stage0 = root / (label + "-stage0")
                extract_stage0(stage0)
                stage1 = root / (label + "-stage1")
                stage1.mkdir()
                source = source_root / "bootstrap" / "compiler.lt"
                checked([sys.executable, str(stage0 / "latent.py"),
                         str(source), "-t", "py", "-o", str(stage1)],
                        cwd=root, env=clean_env)
                (stage1 / "latent_host.py").write_bytes(adapter)

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

                stage2_manifest = manifest(stage2)
                stage3_manifest = manifest(stage3)
                self.assertEqual(stage2_manifest, stage3_manifest)
                stage2_bytes = tuple((stage2 / name).read_bytes()
                                     for name in declared)
                stage3_bytes = tuple((stage3 / name).read_bytes()
                                     for name in declared)
                self.assertEqual(stage2_bytes, stage3_bytes)
                return (stage2_manifest, stage3_manifest,
                        stage2_bytes, stage3_bytes)

            source_a = ROOT
            source_b = root / "checkout-b"
            source_b.mkdir()
            shutil.copytree(ROOT / "bootstrap", source_b / "bootstrap",
                            ignore=shutil.ignore_patterns("__pycache__"))
            first = build_chain("checkout-a", source_a)
            second = build_chain("checkout-b", source_b)
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
