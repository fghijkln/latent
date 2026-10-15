"""Tests for the opt-in Stage1 launcher, user cache, and CLI boundary."""
from __future__ import annotations

import contextlib
import io
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tools" / "latent_stage1.py"
sys.path.insert(0, str(ROOT))
from tests._safe_test_env import clean_test_environment, isolate_test_environment

isolate_test_environment()

SPEC = importlib.util.spec_from_file_location("latent_stage1", LAUNCHER)
assert SPEC is not None and SPEC.loader is not None
latent_stage1 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = latent_stage1
SPEC.loader.exec_module(latent_stage1)


def _valid_fake_builder(root: Path, output: Path, work: Path) -> None:
    del root, work
    output.mkdir(mode=0o700)
    (output / "compiler.py").write_text("# Stage1 fake\n", encoding="utf-8")
    (output / "latent_host.py").write_text("# approved host\n", encoding="utf-8")


def _require_supported_stage1_host() -> None:
    try:
        latent_stage1._check_supported_host()
    except latent_stage1.Stage1Error as exc:
        raise unittest.SkipTest(
            f"real Stage1 CLI integration requires a supported host: {exc}") from exc


def _cache_worker(root: Path, cache_root: Path, marker: Path) -> int:
    identity = {"schema": 1, "source": "concurrency-test"}

    def builder(_root: Path, output: Path, _work: Path) -> None:
        with marker.open("x", encoding="utf-8") as stream:
            stream.write("built once\n")
        time.sleep(0.3)
        _valid_fake_builder(_root, output, _work)

    entry = latent_stage1.Stage1Cache(cache_root, identity).ensure(root, builder)
    print(entry)
    return 0


class HostAndScopeTests(unittest.TestCase):
    def test_only_approved_ubuntu_cpython_ucd_pairs_are_accepted(self):
        check = latent_stage1._host_support_error
        common = {"platform_name": "linux", "implementation": "cpython",
                  "os_id": "ubuntu", "os_version": "24.04"}
        # Synthetic host identities keep these policy tests independent of the
        # machine running the suite.
        self.assertIsNone(check(**common, version=(3, 11), ucd="14.0.0"))
        self.assertIsNone(check(**common, version=(3, 12), ucd="15.0.0"))
        rejected = (
            ({"os_id": "debian"}, "requires Ubuntu 24.04 on Linux"),
            ({"os_version": "22.04"}, "requires Ubuntu 24.04 on Linux"),
            ({"platform_name": "darwin"}, "requires Ubuntu 24.04 on Linux"),
            ({"implementation": "pypy"},
             "requires CPython 3.11/UCD 14.0.0 or CPython 3.12/UCD 15.0.0"),
            ({"version": (3, 12), "ucd": "14.0.0"},
             "requires CPython 3.11/UCD 14.0.0 or CPython 3.12/UCD 15.0.0"),
            ({"version": (3, 13), "ucd": "16.0.0"},
             "requires CPython 3.11/UCD 14.0.0 or CPython 3.12/UCD 15.0.0"),
        )
        for changes, expected_error in rejected:
            fields = dict(common, version=(3, 11), ucd="14.0.0")
            fields.update(changes)
            self.assertEqual(check(**fields), expected_error, fields)

    def test_unsupported_host_is_reported_as_an_integration_skip(self):
        reason = "unsupported host (debian 12, CPython 3.12 / Unicode 15.0.0)"
        with patch.object(latent_stage1, "_check_supported_host",
                          side_effect=latent_stage1.Stage1Error(reason)):
            with self.assertRaises(unittest.SkipTest) as skipped:
                _require_supported_stage1_host()
        self.assertIn("real Stage1 CLI integration requires a supported host",
                      str(skipped.exception))
        self.assertIn(reason, str(skipped.exception))

    def test_working_directory_must_be_inside_the_checkout(self):
        with tempfile.TemporaryDirectory(prefix="latent-stage1-outside-") as outside:
            with self.assertRaisesRegex(latent_stage1.Stage1Error,
                                        "inside that checkout"):
                latent_stage1._check_working_directory(ROOT, outside)
        with self.assertRaisesRegex(latent_stage1.Stage1Error,
                                    "cannot resolve working directory"):
            latent_stage1._check_working_directory(ROOT, ROOT / "missing-dir")

    def test_cache_key_covers_source_pin_python_ucd_and_platform(self):
        with tempfile.TemporaryDirectory(prefix="latent-stage1-identity-") as temporary:
            root = Path(temporary)
            bootstrap = root / "bootstrap"
            tools = root / "tools"
            bootstrap.mkdir()
            tools.mkdir()
            (bootstrap / "compiler.lt").write_text("version one\n", encoding="utf-8")
            (bootstrap / "runtime.dat").write_bytes(b"runtime one")
            wrapper = tools / "latent_stage1.py"
            wrapper.write_text("launcher one\n", encoding="utf-8")
            pycache = bootstrap / "__pycache__"
            pycache.mkdir()
            (pycache / "ignored.pyc").write_bytes(b"bytecode is not input")

            base = latent_stage1._cache_identity(
                root, wrapper_path=wrapper, version=(3, 12), ucd="15.0.0",
                platform_id="linux-x86_64")
            base_key = latent_stage1._cache_key(base)
            self.assertNotEqual(base_key, latent_stage1._cache_key(
                dict(base, stage0_commit="different-pin")))
            self.assertNotEqual(base_key, latent_stage1._cache_key(
                dict(base, python="3.11")))
            self.assertNotEqual(base_key, latent_stage1._cache_key(
                dict(base, ucd="14.0.0")))
            self.assertNotEqual(base_key, latent_stage1._cache_key(
                dict(base, platform="linux-aarch64")))
            self.assertNotEqual(base_key, latent_stage1._cache_key(
                dict(base, javac={"path": "/usr/bin/javac", "version": "javac 17"})))

            second_root = root / "another-checkout"
            (second_root / "bootstrap").mkdir(parents=True)
            (second_root / "tools").mkdir()
            (second_root / "bootstrap" / "compiler.lt").write_text(
                "version one\n", encoding="utf-8")
            (second_root / "bootstrap" / "runtime.dat").write_bytes(b"runtime one")
            second_wrapper = second_root / "tools" / "latent_stage1.py"
            second_wrapper.write_text("launcher one\n", encoding="utf-8")
            second_identity = latent_stage1._cache_identity(
                second_root, wrapper_path=second_wrapper, version=(3, 12),
                ucd="15.0.0", platform_id="linux-x86_64")
            self.assertEqual(base["bootstrap_source_sha256"],
                             second_identity["bootstrap_source_sha256"])
            self.assertNotEqual(base_key, latent_stage1._cache_key(second_identity))

            (bootstrap / "compiler.lt").write_text("version two\n", encoding="utf-8")
            changed_source = latent_stage1._cache_identity(
                root, wrapper_path=wrapper, version=(3, 12), ucd="15.0.0",
                platform_id="linux-x86_64")
            self.assertNotEqual(base_key, latent_stage1._cache_key(changed_source))
            wrapper.write_text("launcher two\n", encoding="utf-8")
            changed_wrapper = latent_stage1._cache_identity(
                root, wrapper_path=wrapper, version=(3, 12), ucd="15.0.0",
                platform_id="linux-x86_64")
            self.assertNotEqual(latent_stage1._cache_key(changed_source),
                                latent_stage1._cache_key(changed_wrapper))

    def test_wrapper_dispatches_to_stage1_and_propagates_failure_without_fallback(self):
        with tempfile.TemporaryDirectory(prefix="latent-stage1-dispatch-") as temporary:
            cache_entry = Path(temporary)
            identity = {"test": "dispatch"}
            completed = subprocess.CompletedProcess([], 23)
            with (patch.object(latent_stage1, "_repository_root", return_value=ROOT),
                  patch.object(latent_stage1, "_check_supported_host"),
                  patch.object(latent_stage1, "_check_working_directory", return_value=ROOT),
                  patch.object(latent_stage1, "_cache_identity", return_value=identity),
                  patch.object(latent_stage1.Stage1Cache, "ensure", return_value=cache_entry),
                  patch.object(latent_stage1.subprocess, "run", return_value=completed) as run):
                status = latent_stage1.main(["fixture.lt", "--run", "opaque-option"])
            self.assertEqual(status, 23)
            command = run.call_args.args[0]
            self.assertEqual(command[1], "-B")
            self.assertEqual(command[2], str(cache_entry / "compiler.py"))
            self.assertEqual(command[3:], ["fixture.lt", "--run", "opaque-option"])
            self.assertNotIn(str(ROOT / "latent.py"), command)

    def test_unsupported_host_is_a_clear_nonzero_error_before_cache_access(self):
        stderr = io.StringIO()
        with (patch.object(latent_stage1, "_repository_root", return_value=ROOT),
              patch.object(latent_stage1, "_check_supported_host",
                           side_effect=latent_stage1.Stage1Error(
                               "unsupported host (debian 12, CPython 3.12 / Unicode 15.0.0)")),
              patch.object(latent_stage1, "_cache_identity") as make_identity,
              contextlib.redirect_stderr(stderr)):
            status = latent_stage1.main(["--help"])
        self.assertEqual(status, 1)
        self.assertIn("latent-stage1: error: unsupported host", stderr.getvalue())
        make_identity.assert_not_called()


class CacheContractTests(unittest.TestCase):
    def test_changed_identity_builds_a_distinct_cache_entry(self):
        with tempfile.TemporaryDirectory(prefix="latent-stage1-key-change-") as temporary:
            root = Path(temporary)
            cache_root = root / "user-cache" / "latent" / "stage1"
            first_identity = {"schema": 1, "source": "first"}
            second_identity = {"schema": 1, "source": "second"}
            calls = []

            def builder(repo: Path, output: Path, work: Path) -> None:
                calls.append(1)
                _valid_fake_builder(repo, output, work)

            first_cache = latent_stage1.Stage1Cache(cache_root, first_identity)
            second_cache = latent_stage1.Stage1Cache(cache_root, second_identity)
            first = first_cache.ensure(ROOT, builder)
            second = second_cache.ensure(ROOT, builder)
            self.assertNotEqual(first, second)
            self.assertEqual(len(calls), 2)
            self.assertTrue(latent_stage1._validate_cache_entry(
                first, first_cache.key, first_identity))
            self.assertTrue(latent_stage1._validate_cache_entry(
                second, second_cache.key, second_identity))

    def test_source_changes_after_identity_or_during_build_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="latent-stage1-source-race-") as temporary:
            root = Path(temporary)
            bootstrap = root / "bootstrap"
            tools = root / "tools"
            bootstrap.mkdir()
            tools.mkdir()
            source = bootstrap / "compiler.lt"
            source.write_text("version one\n", encoding="utf-8")
            (bootstrap / "latent_host.py").write_text("# host\n", encoding="utf-8")
            wrapper = tools / "latent_stage1.py"
            wrapper.write_text("# launcher\n", encoding="utf-8")
            identity = latent_stage1._cache_identity(
                root, wrapper_path=wrapper, version=(3, 12), ucd="15.0.0",
                platform_id="test-platform")
            cache_root = root / "user-cache" / "latent" / "stage1"
            cache = latent_stage1.Stage1Cache(cache_root, identity)
            builds = []

            def builder(repo: Path, output: Path, work: Path) -> None:
                builds.append(1)
                _valid_fake_builder(repo, output, work)

            cache.ensure(root, builder)
            self.assertEqual(len(builds), 1)
            source.write_text("version two\n", encoding="utf-8")
            with self.assertRaisesRegex(
                    latent_stage1.Stage1Error, "changed after computing the cache key"):
                cache.ensure(root, builder)
            self.assertEqual(len(builds), 1)

            changed_identity = latent_stage1._cache_identity(
                root, wrapper_path=wrapper, version=(3, 12), ucd="15.0.0",
                platform_id="test-platform")
            changed_cache = latent_stage1.Stage1Cache(cache_root, changed_identity)

            def mutate_during_build(repo: Path, output: Path, work: Path) -> None:
                _valid_fake_builder(repo, output, work)
                source.write_text("version three\n", encoding="utf-8")

            with self.assertRaisesRegex(
                    latent_stage1.Stage1Error, "changed after computing the cache key"):
                changed_cache.ensure(root, mutate_during_build)
            self.assertFalse((cache_root / changed_cache.key).exists())
            self.assertEqual(
                [path for path in cache_root.iterdir()
                 if path.name.startswith(latent_stage1.BUILD_PREFIX)], [])

    def test_cache_rebuilds_partial_corrupt_and_interrupted_entries(self):
        with tempfile.TemporaryDirectory(prefix="latent-stage1-cache-") as temporary:
            root = Path(temporary)
            cache_root = root / "user-cache" / "latent" / "stage1"
            identity = {"schema": 1, "source": "corruption-test"}
            cache = latent_stage1.Stage1Cache(cache_root, identity)
            calls = []

            def builder(repo: Path, output: Path, work: Path) -> None:
                calls.append(1)
                _valid_fake_builder(repo, output, work)

            first = cache.ensure(ROOT, builder)
            self.assertEqual(len(calls), 1)
            self.assertTrue(latent_stage1._validate_cache_entry(
                first, cache.key, identity))

            orphan = cache_root / (latent_stage1.BUILD_PREFIX + "interrupted")
            orphan.mkdir()
            (orphan / "partial.tmp").write_text("half built", encoding="utf-8")
            (first / "compiler.py").write_text("corrupted\n", encoding="utf-8")
            second = cache.ensure(ROOT, builder)
            self.assertEqual(second, first)
            self.assertEqual(len(calls), 2)
            self.assertFalse(orphan.exists())
            self.assertTrue(latent_stage1._validate_cache_entry(
                second, cache.key, identity))

            shutil.rmtree(second)
            second.mkdir(mode=0o700)
            (second / "compiler.py").write_text("partial cache\n", encoding="utf-8")
            third = cache.ensure(ROOT, builder)
            self.assertEqual(third, first)
            self.assertEqual(len(calls), 3)
            self.assertTrue(latent_stage1._validate_cache_entry(
                third, cache.key, identity))

    def test_failed_build_leaves_no_published_or_staging_artifact(self):
        with tempfile.TemporaryDirectory(prefix="latent-stage1-failed-build-") as temporary:
            root = Path(temporary)
            cache_root = root / "cache"
            identity = {"schema": 1, "source": "failure-test"}
            cache = latent_stage1.Stage1Cache(cache_root, identity)

            def fail(_root: Path, _output: Path, _work: Path) -> None:
                raise latent_stage1.Stage1Error("injected build failure")

            with self.assertRaisesRegex(latent_stage1.Stage1Error,
                                        "injected build failure"):
                cache.ensure(ROOT, fail)
            self.assertFalse((cache_root / cache.key).exists())
            self.assertEqual(
                [path.name for path in cache_root.iterdir() if path.name.startswith(
                    latent_stage1.BUILD_PREFIX)], [])

    def test_symlink_cache_root_is_rejected_without_following_it(self):
        with tempfile.TemporaryDirectory(prefix="latent-stage1-cache-link-") as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            link = root / "cache-link"
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(latent_stage1.Stage1Error,
                                        "not a real directory"):
                latent_stage1._ensure_private_cache_root(link / "latent" / "stage1")
            self.assertEqual(list(outside.iterdir()), [])

    def test_concurrent_processes_publish_exactly_one_complete_entry(self):
        with tempfile.TemporaryDirectory(prefix="latent-stage1-concurrent-") as temporary:
            root = Path(temporary)
            cache_root = root / "user-cache" / "latent" / "stage1"
            marker = root / "builder-ran-once.txt"
            command = [sys.executable, str(Path(__file__).resolve()),
                       "--cache-worker", str(ROOT), str(cache_root), str(marker)]
            processes = [subprocess.Popen(
                command, cwd=ROOT, env=clean_test_environment(),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for _ in range(4)]
            results = [process.communicate(timeout=30) + (process.returncode,)
                       for process in processes]
            for stdout, stderr, returncode in results:
                self.assertEqual(returncode, 0, stderr)
                self.assertTrue(stdout.strip())
            self.assertEqual(marker.read_text(encoding="utf-8"), "built once\n")
            entries = [path for path in cache_root.iterdir()
                       if path.name != ".lock"]
            self.assertEqual(len(entries), 1)
            identity = {"schema": 1, "source": "concurrency-test"}
            self.assertTrue(latent_stage1._validate_cache_entry(
                entries[0], latent_stage1._cache_key(identity), identity))

    def test_unavailable_fixed_stage0_fails_without_network_or_output(self):
        with tempfile.TemporaryDirectory(prefix="latent-stage1-no-stage0-") as temporary:
            root = Path(temporary)
            destination = root / "stage0"
            with self.assertRaisesRegex(
                    latent_stage1.Stage1Error,
                    r"unavailable in this local checkout; fetch the repository "
                    r"history and retry \(no network fetch is performed automatically\)"):
                latent_stage1._extract_stage0(root, destination)
            self.assertFalse(destination.exists())


class Stage1CliIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _require_supported_stage1_host()
        cls.temporary = tempfile.TemporaryDirectory(
            prefix=".latent-stage1-tests-", dir=ROOT)
        cls.test_root = Path(cls.temporary.name)
        cls.work = cls.test_root / "work"
        cls.work.mkdir()
        cls.cache_home = cls.test_root / "xdg-cache"
        cls.env = clean_test_environment({"XDG_CACHE_HOME": str(cls.cache_home)})
        cls.source = cls.work / "hello.lt"
        cls.source.write_text('say "stage1 hello"\n', encoding="utf-8", newline="\n")
        cls.help_result = cls.invoke("--help")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def invoke(cls, *arguments: str, cwd: Path | None = None):
        return subprocess.run(
            [sys.executable, str(LAUNCHER), *arguments],
            cwd=cls.work if cwd is None else cwd, env=cls.env,
            text=True, encoding="utf-8", capture_output=True, check=False,
            timeout=120)

    def test_help_marks_preview_and_stage1_specific_limitations(self):
        self.assertEqual(self.help_result.returncode, 0, self.help_result.stderr)
        self.assertIn("latent-stage1 is an experimental preview", self.help_result.stdout)
        self.assertIn("Python target only", self.help_result.stdout)
        self.assertIn("Ubuntu 24.04", self.help_result.stdout)
        self.assertIn("build/py", self.help_result.stdout)
        self.assertIn("90 days", self.help_result.stdout)
        self.assertIn("usage: latent source.lt", self.help_result.stdout)
        self.assertTrue(self.cache_home.exists())
        cache_root = self.cache_home / "latent" / "stage1"
        cache_entries = [path for path in cache_root.iterdir()
                         if path.name != ".lock"]
        self.assertEqual(len(cache_entries), 1)
        identity = latent_stage1._cache_identity(ROOT)
        self.assertTrue(latent_stage1._validate_cache_entry(
            cache_entries[0], cache_entries[0].name, identity))
        self.assertFalse((cache_entries[0] / "__pycache__").exists())

    def test_normal_compile_explicit_output_and_stage1_program(self):
        output = self.work / "explicit-output"
        result = self.invoke(str(self.source), "--target", "py", "-o", str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        generated = output / "hello.py"
        self.assertTrue(generated.is_file())
        self.assertIn(str(generated), result.stdout)
        run = subprocess.run([sys.executable, str(generated)], cwd=self.work,
                             env=self.env, text=True, encoding="utf-8",
                             capture_output=True, check=False)
        self.assertEqual((run.returncode, run.stdout, run.stderr),
                         (0, "stage1 hello\n", ""))

    def test_run_option_executes_compiled_program_and_returns_success(self):
        output = self.work / "run-output"
        result = self.invoke(str(self.source), "-t", "py", "-o", str(output), "--run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"wrote {output / 'hello.py'}", result.stdout)
        self.assertTrue(result.stdout.endswith("stage1 hello\n"))
        self.assertEqual(result.stderr, "")

    def test_default_output_is_stage1_build_py_directory(self):
        source = self.work / "default-output.lt"
        source.write_text('say "default"\n', encoding="utf-8", newline="\n")
        result = self.invoke(str(source))
        self.assertEqual(result.returncode, 0, result.stderr)
        generated = self.work / "build" / "py" / "default-output.py"
        self.assertTrue(generated.is_file())
        self.assertIn("wrote build/py/default-output.py", result.stdout)

    def test_source_error_is_nonzero_and_creates_no_output_directory(self):
        output = self.work / "missing-source-output"
        result = self.invoke(str(self.work / "missing.lt"), "-o", str(output))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertTrue(result.stderr)

    def test_more_than_ten_invalid_cli_combinations_fail_without_success_artifacts(self):
        cases = [
            ["-t", "java"],
            ["--target", "java"],
            ["--show-ast"],
            ["--dump-ast"],
            ["--show-desugar"],
            ["--editor-metadata"],
            ["-o"],
            ["--outdir"],
            ["-t"],
            ["--target"],
            ["--source-root"],
            ["-o", "--run"],
            ["--source-root", "--run"],
            ["extra-source.lt"],
            [],
        ]
        for index, prefix in enumerate(cases):
            with self.subTest(arguments=prefix):
                case_root = self.work / f"invalid-{index:02d}"
                case_root.mkdir()
                source = case_root / "source.lt"
                source.write_text('say "must not compile"\n', encoding="utf-8")
                output = case_root / "success-output"
                args = list(prefix)
                if prefix == ["extra-source.lt"]:
                    args = [str(source), "surplus", "-o", str(output)]
                elif prefix == []:
                    args = ["-o", str(output)]
                elif prefix not in (["-o"], ["--outdir"], ["-t"],
                                    ["--target"], ["--source-root"]):
                    args.extend([str(source), "-o", str(output)])
                elif prefix == ["-o"]:
                    args.extend([])
                elif prefix == ["--outdir"]:
                    args.extend([])
                elif prefix == ["-t"]:
                    args.extend([])
                elif prefix == ["--target"]:
                    args.extend([])
                elif prefix == ["--source-root"]:
                    args.extend([])
                elif prefix in (["-o", "--run"], ["--source-root", "--run"]):
                    args.extend([str(source), str(output)])
                result = self.invoke(*args, cwd=case_root)
                self.assertNotEqual(result.returncode, 0,
                                    f"unexpected success: {args!r}\n{result.stdout}")
                self.assertFalse(output.exists(), f"unexpected output for {args!r}")
                self.assertFalse((case_root / "build" / "py").exists())

    def test_cache_hit_reuses_same_complete_cache_entry(self):
        before = sorted(path.name for path in self.cache_home.joinpath(
            "latent", "stage1").iterdir())
        again = self.invoke("--help")
        after = sorted(path.name for path in self.cache_home.joinpath(
            "latent", "stage1").iterdir())
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(before, after)
        self.assertEqual(len([name for name in after if name != ".lock"]), 1)
        cache_root = self.cache_home / "latent" / "stage1"
        entry = next(path for path in cache_root.iterdir() if path.name != ".lock")
        identity = latent_stage1._cache_identity(ROOT)
        self.assertTrue(latent_stage1._validate_cache_entry(
            entry, entry.name, identity))


def _run_cache_worker_cli() -> int:
    if len(sys.argv) != 5 or sys.argv[1] != "--cache-worker":
        return 2
    return _cache_worker(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))


if __name__ == "__main__":
    if sys.argv[1:] and sys.argv[1] == "--cache-worker":
        raise SystemExit(_run_cache_worker_cli())
    unittest.main()
