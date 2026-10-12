"""Contract tests for the allowlisted bootstrap OS adapter."""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests._safe_test_env import isolate_test_environment

isolate_test_environment()
SPEC = importlib.util.spec_from_file_location(
    "latent_host", ROOT / "bootstrap" / "latent_host.py")
host = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(host)


class HostContractTests(unittest.TestCase):
    def test_public_api_matches_declared_allowlist(self):
        expected = {
            "API_VERSION", "argv", "stdout", "stderr", "exit_process",
            "path_join", "path_dirname", "runtime_source_dir", "path_basename",
            "path_abspath", "path_realpath", "path_relpath", "path_norm",
            "path_exists", "path_is_file", "path_is_dir",
            "read_utf8", "write_utf8", "make_dirs", "make_temp_dir",
            "remove_temp_dir", "copy_explicit_files", "run_python",
        }
        self.assertEqual(set(host.__all__), expected)
        self.assertEqual(len(host.__all__), len(expected))
        public_functions = {
            name for name, value in vars(host).items()
            if callable(value) and getattr(value, "__module__", None) == host.__name__
            and not name.startswith("_")
        }
        self.assertEqual(public_functions, expected - {"API_VERSION"})
        public_constants = {
            name for name in vars(host)
            if name.isupper() and not name.startswith("_")
        }
        self.assertEqual(public_constants, {"API_VERSION"})

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
        self.assertEqual(Path(host.runtime_source_dir()),
                         (ROOT / "bootstrap").resolve())

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

    def test_invalid_utf8_maps_to_stable_error(self):
        with tempfile.TemporaryDirectory() as root:
            invalid = Path(root, "invalid.lt")
            invalid.write_bytes(b"\xff\xfe")
            self.assertEqual(host.read_utf8(str(invalid)), {
                "ok": False, "value": "", "error": "invalid-utf8"})

    @unittest.skipUnless(os.name == "posix", "ENOTDIR mapping is POSIX-specific")
    def test_non_directory_parent_maps_to_stable_error(self):
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root, "plain-file")
            parent.write_text("not a directory\n", encoding="utf-8")
            self.assertEqual(host.read_utf8(str(parent / "child.lt")), {
                "ok": False, "value": "", "error": "not-a-directory"})

    def test_relpath_preserves_cross_drive_absolute_candidate(self):
        candidate = r"C:\outside\main.lt"
        project_root = r"D:\project"
        with patch.object(
                host.os.path, "relpath",
                side_effect=ValueError("paths are on different drives")):
            self.assertEqual(host.path_relpath(candidate, project_root),
                             os.path.normpath(candidate))

    def test_write_utf8_replaces_final_symlink_without_following_it(self):
        with tempfile.TemporaryDirectory() as root:
            outside = Path(root, "outside.txt")
            outside.write_text("keep me\n", encoding="utf-8")
            output = Path(root, "generated.py")
            output.symlink_to(outside)

            result = host.write_utf8(str(output), "generated\n")

            self.assertTrue(result["ok"], result)
            self.assertEqual(outside.read_text(), "keep me\n")
            self.assertFalse(output.is_symlink())
            self.assertEqual(output.read_text(), "generated\n")

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
            windows_path = host.copy_explicit_files(
                source, output, [r"..\secret"])
            self.assertFalse(windows_path["ok"])
            self.assertEqual(windows_path["error"], "invalid-name")

    def test_copy_stages_all_sources_before_changing_destinations(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, "runtime")
            output = Path(root, "out")
            source.mkdir()
            output.mkdir()
            (source / "first.py").write_text("new\n", encoding="utf-8")
            (output / "first.py").write_text("old\n", encoding="utf-8")

            result = host.copy_explicit_files(
                str(source), str(output), ["first.py", "missing.py"])

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "not-found")
            self.assertEqual(result["value"], [])
            self.assertEqual((output / "first.py").read_text(), "old\n")
            self.assertEqual(sorted(path.name for path in output.iterdir()),
                             ["first.py"])

    def test_copy_directory_conflict_does_not_partially_replace_files(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, "runtime")
            output = Path(root, "out")
            source.mkdir()
            output.mkdir()
            (source / "first.py").write_text("new\n", encoding="utf-8")
            (source / "second.py").write_text("new\n", encoding="utf-8")
            (output / "first.py").write_text("old\n", encoding="utf-8")
            (output / "second.py").mkdir()

            result = host.copy_explicit_files(
                str(source), str(output), ["first.py", "second.py"])

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "is-directory")
            self.assertEqual(result["value"], [])
            self.assertEqual((output / "first.py").read_text(), "old\n")
            self.assertTrue((output / "second.py").is_dir())
            self.assertEqual(sorted(path.name for path in output.iterdir()),
                             ["first.py", "second.py"])

    def test_copy_replaces_destination_symlink_without_following_it(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, "runtime")
            output = Path(root, "out")
            source.mkdir()
            output.mkdir()
            (source / "fixed.py").write_text("trusted runtime\n",
                                              encoding="utf-8")
            outside = Path(root, "outside.txt")
            outside.write_text("keep me\n", encoding="utf-8")
            (output / "fixed.py").symlink_to(outside)

            result = host.copy_explicit_files(
                str(source), str(output), ["fixed.py"])

            self.assertTrue(result["ok"], result)
            self.assertEqual(outside.read_text(), "keep me\n")
            self.assertFalse((output / "fixed.py").is_symlink())
            self.assertEqual((output / "fixed.py").read_text(),
                             "trusted runtime\n")

    @unittest.skipUnless(os.name == "posix", "hard-link semantics are POSIX-specific")
    def test_copy_replaces_destination_hardlink_without_mutating_its_peer(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, "runtime")
            output = Path(root, "out")
            source.mkdir()
            output.mkdir()
            (source / "fixed.py").write_text("trusted runtime\n",
                                              encoding="utf-8")
            outside = Path(root, "outside.txt")
            outside.write_text("keep me\n", encoding="utf-8")
            os.link(outside, output / "fixed.py")

            result = host.copy_explicit_files(
                str(source), str(output), ["fixed.py"])

            self.assertTrue(result["ok"], result)
            self.assertEqual(outside.read_text(), "keep me\n")
            self.assertNotEqual(os.stat(outside).st_ino,
                                os.stat(output / "fixed.py").st_ino)
            self.assertEqual((output / "fixed.py").read_text(),
                             "trusted runtime\n")

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
            script = os.path.join(root, "program with spaces.py")
            Path(script).write_text(
                "import os, sys\nprint('ok')\nprint(os.getcwd())\n"
                "print('problem', file=sys.stderr)\n"
                "raise SystemExit(7)\n", encoding="utf-8")
            result = host.run_python(script, cwd=root)
            self.assertEqual(result["returncode"], 7)
            self.assertEqual(result["stdout"], f"ok\n{root}\n")
            self.assertEqual(result["stderr"], "problem\n")
            self.assertTrue(result["ok"])

    def test_run_python_maps_spawn_errors_to_stable_results(self):
        with tempfile.TemporaryDirectory() as root:
            script = Path(root, "program.py")
            script.write_text("pass\n", encoding="utf-8")
            result = host.run_python(str(script), cwd=str(Path(root, "missing")))
            self.assertEqual(result, {
                "ok": False, "returncode": 127, "stdout": "", "stderr": "",
                "error": "not-found"})

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
