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

say "bootstrap smoke"
say add(40, 2)
say sum_positive([-2, 3, 6])
say 1 / 2
say [true, nil, "line\nnext"]
say ["quote: \"x\""]
say {"items": [1, 2]}
'''
EXPECTED_STDOUT = r'''bootstrap smoke
42
9
0.5
[true, nil, "line\nnext"]
["quote: \"x\""]
{"items": [1, 2]}
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


if __name__ == "__main__":
    unittest.main()
