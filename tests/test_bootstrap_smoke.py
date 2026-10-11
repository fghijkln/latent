"""End-to-end M2 smoke test for the fixed-seed self-hosting slice.

The fixture is deliberately restricted and is not the full v0.13.5 Python
projection. This test demonstrates the stage mechanics only; later P15 gates
must broaden the compiler and its differential fixture projection.
"""
from __future__ import annotations

import hashlib
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
FIXTURE = 'say "bootstrap smoke"\nsay 40 + 2\n'


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


def build_chain(root: Path) -> dict[str, bytes]:
    checkout = root / "checkout"
    checkout.mkdir(parents=True)
    (checkout / "compiler.lt").write_bytes(
        (ROOT / "bootstrap" / "compiler.lt").read_bytes())
    (checkout / "fixture.lt").write_text(FIXTURE, encoding="utf-8", newline="\n")
    stage0 = root / "stage0"
    extract_stage0(stage0)

    outputs: dict[str, bytes] = {}
    stage_dirs = {name: root / name for name in ("stage1", "stage2", "stage3")}
    for path in stage_dirs.values():
        path.mkdir()

    # Fixed Stage0 compiles the Latent compiler source into a separate Stage1.
    checked([sys.executable, str(stage0 / "latent.py"),
             str(checkout / "compiler.lt"), "-t", "py", "-o",
             str(stage_dirs["stage1"])], cwd=root)
    (stage_dirs["stage1"] / "latent_host.py").write_bytes(
        (ROOT / "bootstrap" / "latent_host.py").read_bytes())

    clean_env = {**os.environ, "PYTHONPATH": ""}
    # Stage1, Stage2, and Stage3 compile the exact same Latent compiler source;
    # no stage output directory is reused.
    for stage_name, next_name in (("stage1", "stage2"),
                                  ("stage2", "stage3")):
        compiler = stage_dirs[stage_name] / "compiler.py"
        checked([sys.executable, str(compiler), str(checkout / "compiler.lt"),
                 "-o", str(stage_dirs[next_name])], cwd=root, env=clean_env)
        (stage_dirs[next_name] / "latent_host.py").write_bytes(
            (ROOT / "bootstrap" / "latent_host.py").read_bytes())

    # Verify Stage1 can compile a fixture in an environment that has only the
    # generated compiler's own directory on sys.path (PYTHONPATH is empty).
    fixture_outputs: dict[str, tuple[int, str, str]] = {}
    stage0_target = root / "stage0-target"
    stage0_target.mkdir()
    checked([sys.executable, str(stage0 / "latent.py"),
             str(checkout / "fixture.lt"), "-t", "py", "-o",
             str(stage0_target)], cwd=root)
    fixture_outputs["stage0"] = run_stage_program(stage0_target / "fixture.py", root)
    for name in ("stage1", "stage2", "stage3"):
        target = root / (name + "-target")
        target.mkdir()
        checked([sys.executable, str(stage_dirs[name] / "compiler.py"),
                 str(checkout / "fixture.lt"), "-o", str(target)],
                cwd=root, env=clean_env)
        fixture_outputs[name] = run_stage_program(target / "fixture.py", root)

    missing_dir = root / "missing-target"
    failed = subprocess.run(
        [sys.executable, str(stage_dirs["stage1"] / "compiler.py"),
         str(checkout / "missing.lt"), "-o", str(missing_dir)],
        cwd=root, env=clean_env, text=True, encoding="utf-8",
        capture_output=True, check=False)
    if failed.returncode == 0 or "input error: not-found" not in failed.stderr:
        raise AssertionError(
            f"missing source did not produce stable failure: {failed!r}")
    if missing_dir.exists() or (missing_dir / "missing.py").exists():
        raise AssertionError("failed Stage1 compile left a success-shaped output")

    # Manifest consists only of the two explicitly declared compiler files.
    for name in ("stage2", "stage3"):
        for relative in ("compiler.py", "latent_host.py"):
            data = (stage_dirs[name] / relative).read_bytes()
            outputs[f"{name}/{relative}"] = data
            outputs[f"{name}/{relative}.sha256"] = hashlib.sha256(data).hexdigest().encode()
        actual = sorted(p.name for p in stage_dirs[name].iterdir()
                        if p.name != "__pycache__" and p.suffix != ".pyc")
        if actual != ["compiler.py", "latent_host.py"]:
            raise AssertionError(f"unexpected {name} files: {actual}")

    stage1_source = (stage_dirs["stage1"] / "compiler.py").read_text(
        encoding="utf-8")
    if "latent.py" in stage1_source or "import lex" in stage1_source:
        raise AssertionError("Stage1 output unexpectedly references Stage0 modules")
    return {**outputs, **{f"fixture/{name}": value
                          for name, value in fixture_outputs.items()}}


class BootstrapSmokeTests(unittest.TestCase):
    def test_stage_chain_fixed_point_and_cross_root_reproducibility(self):
        with tempfile.TemporaryDirectory(prefix="p15-left-") as left_raw, \
                tempfile.TemporaryDirectory(prefix="p15-right-") as right_raw:
            left = build_chain(Path(left_raw))
            right = build_chain(Path(right_raw))
            for stage in ("stage1", "stage2", "stage3"):
                self.assertEqual(left[f"fixture/{stage}"], left["fixture/stage0"])
                self.assertEqual(right[f"fixture/{stage}"], right["fixture/stage0"])
            self.assertEqual(left["fixture/stage0"], (0, "bootstrap smoke\n42\n", ""))
            self.assertEqual(left["stage2/compiler.py"], left["stage3/compiler.py"])
            self.assertEqual(left["stage2/latent_host.py"], left["stage3/latent_host.py"])
            self.assertEqual(left["stage2/compiler.py"], right["stage2/compiler.py"])
            self.assertEqual(left["stage2/latent_host.py"], right["stage2/latent_host.py"])
            self.assertEqual(left["stage2/compiler.py.sha256"],
                             right["stage2/compiler.py.sha256"])


if __name__ == "__main__":
    unittest.main()
