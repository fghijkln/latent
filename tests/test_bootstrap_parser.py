"""Compile and run the Latent parser fixture with the pinned Stage0 only."""
from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "bootstrap_parser.lt"
STAGE0_COMMIT = "9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9"


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


class BootstrapParserTests(unittest.TestCase):
    def test_latent_parser_fixture(self):
        with tempfile.TemporaryDirectory(prefix="p15-parser-") as temporary:
            temporary = Path(temporary)
            stage0 = temporary / "stage0"
            extract_stage0(stage0)
            output = temporary / "output"
            output.mkdir()
            compiled = subprocess.run(
                [sys.executable, str(stage0 / "latent.py"), str(FIXTURE),
                 "-t", "py", "-o", str(output)],
                cwd=ROOT, text=True, encoding="utf-8", capture_output=True,
                check=False)
            self.assertEqual(compiled.returncode, 0,
                             compiled.stdout + compiled.stderr)
            generated = output / "bootstrap_parser.py"
            executed = subprocess.run(
                [sys.executable, str(generated)], cwd=ROOT,
                text=True, encoding="utf-8", capture_output=True, check=False)
            self.assertEqual(executed.returncode, 0,
                             executed.stdout + executed.stderr)
            self.assertEqual(executed.stdout, "bootstrap parser ok\n")
            self.assertEqual(executed.stderr, "")


if __name__ == "__main__":
    unittest.main()
