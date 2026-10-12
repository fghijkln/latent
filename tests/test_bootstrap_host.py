"""Contract tests for the allowlisted bootstrap OS adapter."""
from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "latent_host", ROOT / "bootstrap" / "latent_host.py")
host = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(host)


class HostContractTests(unittest.TestCase):
    def test_version_and_path_primitives(self):
        self.assertEqual(host.API_VERSION, 1)
        with tempfile.TemporaryDirectory() as root:
            child = host.path_join(root, "child")
            self.assertEqual(host.path_basename(child), "child")
            self.assertEqual(host.path_dirname(child), root)
            self.assertEqual(host.path_relpath(child, root), "child")
            self.assertEqual(host.path_realpath(root), os.path.realpath(root))
            self.assertEqual(host.path_norm("a" + os.sep + "b"), "a/b")
            self.assertTrue(host.path_is_dir(root))
            self.assertFalse(host.path_is_file(root))

    def test_utf8_round_trip_and_stable_errors(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "source.lt")
            self.assertTrue(host.write_utf8(path, "say \"東京\"\r\n")["ok"])
            result = host.read_utf8(path)
            self.assertEqual(result, {
                "ok": True, "value": "say \"東京\"\r\n", "error": ""})
            missing = host.read_utf8(os.path.join(root, "missing.lt"))
            self.assertEqual(missing, {
                "ok": False, "value": "", "error": "not-found"})
            self.assertEqual(host.read_utf8(root)["error"], "is-directory")
            self.assertTrue(host.make_dirs(os.path.join(root, "nested"))["ok"])

    def test_copy_is_explicit_and_rejects_paths(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "runtime")
            output = os.path.join(root, "out")
            os.mkdir(source)
            os.mkdir(output)
            Path(source, "fixed.py").write_text("fixed\n", encoding="utf-8")
            result = host.copy_explicit_files(source, output, ["fixed.py"])
            self.assertEqual(result["value"], ["fixed.py"])
            self.assertEqual(Path(output, "fixed.py").read_text(), "fixed\n")
            rejected = host.copy_explicit_files(source, output, ["../secret"])
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["error"], "invalid-name")

    def test_temporary_build_directory_is_scoped_and_removable(self):
        created = host.make_temp_dir()
        self.assertTrue(created["ok"], created)
        path = Path(created["value"])
        self.assertTrue(path.is_dir())
        self.assertEqual(path.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(path.name.startswith("latent-build-"))

        alias = path.parent / (path.name + "-alias")
        alias.symlink_to(path, target_is_directory=True)
        linked = host.remove_temp_dir(str(alias))
        self.assertEqual(linked["error"], "invalid-temp-dir")
        self.assertTrue(path.is_dir())
        alias.unlink()

        removed = host.remove_temp_dir(created["value"])
        self.assertTrue(removed["ok"], removed)
        self.assertFalse(path.exists())

        foreign = Path(tempfile.mkdtemp(prefix="latent-build-"))
        rejected_foreign = host.remove_temp_dir(str(foreign))
        self.assertEqual(rejected_foreign["error"], "invalid-temp-dir")
        self.assertTrue(foreign.is_dir())
        shutil.rmtree(foreign)

        rejected = host.remove_temp_dir(tempfile.gettempdir())
        self.assertEqual(rejected, {
            "ok": False, "value": "", "error": "invalid-temp-dir"})

    def test_run_python_captures_exit_and_streams(self):
        with tempfile.TemporaryDirectory() as root:
            script = os.path.join(root, "program.py")
            Path(script).write_text(
                "import sys\nprint('ok')\nprint('problem', file=sys.stderr)\n"
                "raise SystemExit(7)\n", encoding="utf-8")
            result = host.run_python(script, cwd=root)
            self.assertEqual(result["returncode"], 7)
            self.assertEqual(result["stdout"], "ok\n")
            self.assertEqual(result["stderr"], "problem\n")
            self.assertTrue(result["ok"])

    def test_only_selected_files_are_copied(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "runtime")
            output = os.path.join(root, "out")
            os.mkdir(source)
            os.mkdir(output)
            Path(source, "declared.py").write_text("x", encoding="utf-8")
            Path(source, "unlisted.py").write_text("y", encoding="utf-8")
            host.copy_explicit_files(source, output, ["declared.py"])
            self.assertEqual(os.listdir(output), ["declared.py"])


if __name__ == "__main__":
    unittest.main()
