#!/usr/bin/env python3
"""Experimental, checkout-local launcher for the Latent Stage1 compiler.

The Stage1 compiler is built on demand from the fixed local Stage0 Git object.
This launcher only validates the host, manages a private user cache, and passes
argv through to the resulting Latent-implemented compiler. It never falls back
to latent.py.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
from typing import Any, Callable

STAGE0_COMMIT = "9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = 1
BUILD_PREFIX = ".latent-stage1-build-"
_ALLOWED_ARTIFACT_NAME = re.compile(r"[A-Za-z0-9_.$+-]+")


class Stage1Error(RuntimeError):
    """An actionable error that should be reported without a traceback."""


def _repository_root() -> Path:
    script = Path(__file__).resolve()
    root = script.parent.parent
    if (not (root / ".git").exists() or
            not (root / "bootstrap" / "compiler.lt").is_file() or
            not (root / "bootstrap" / "latent_host.py").is_file()):
        raise Stage1Error(
            "tools/latent_stage1.py must run from an intact Latent source checkout")
    return root


def _read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


def _host_support_error(*, platform_name: str, implementation: str,
                        version: tuple[int, int], ucd: str,
                        os_id: str, os_version: str) -> str | None:
    expected = {(3, 11): "14.0.0", (3, 12): "15.0.0"}
    if platform_name != "linux" or os_id != "ubuntu" or os_version != "24.04":
        return "requires Ubuntu 24.04 on Linux"
    if implementation != "cpython":
        return "requires CPython 3.11/UCD 14.0.0 or CPython 3.12/UCD 15.0.0"
    expected_ucd = expected.get(version)
    if expected_ucd is None or ucd != expected_ucd:
        return "requires CPython 3.11/UCD 14.0.0 or CPython 3.12/UCD 15.0.0"
    return None


def _check_supported_host() -> None:
    os_release = _read_os_release()
    version = (sys.version_info.major, sys.version_info.minor)
    reason = _host_support_error(
        platform_name=sys.platform,
        implementation=sys.implementation.name,
        version=version,
        ucd=unicodedata.unidata_version,
        os_id=os_release.get("ID", ""),
        os_version=os_release.get("VERSION_ID", ""),
    )
    if reason is not None:
        os_label = (f"{os_release.get('ID', 'unknown')} "
                    f"{os_release.get('VERSION_ID', 'unknown')}")
        python_label = (f"{sys.implementation.name} "
                        f"{version[0]}.{version[1]} / "
                        f"Unicode {unicodedata.unidata_version}")
        raise Stage1Error(
            f"unsupported host ({os_label}, {python_label}); {reason}. "
            "The preview is verified only on Ubuntu 24.04 with CPython "
            "3.11/UCD 14.0.0 or CPython 3.12/UCD 15.0.0")


def _check_working_directory(root: Path, cwd: str | os.PathLike[str] | None = None) -> Path:
    try:
        working = Path.cwd() if cwd is None else Path(cwd)
        working = working.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise Stage1Error(f"cannot resolve working directory: {exc}") from exc
    if not working.is_dir():
        raise Stage1Error(f"working directory is not a directory: {working}")
    if working != root and root not in working.parents:
        raise Stage1Error(
            "working directory must be the Latent checkout root or a directory "
            "inside that checkout; change to the checkout and retry")
    return working


def _source_digest(root: Path, wrapper_path: Path | None = None) -> str:
    """Hash every bootstrap input (excluding interpreter bytecode) and launcher."""
    bootstrap = root / "bootstrap"
    if not bootstrap.is_dir():
        raise Stage1Error(f"bootstrap source directory is missing: {bootstrap}")
    digest = hashlib.sha256()
    paths: list[tuple[str, Path]] = []
    try:
        for path in bootstrap.rglob("*"):
            relative = path.relative_to(bootstrap)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            if path.is_symlink():
                raise Stage1Error(f"bootstrap input must not be a symlink: {path}")
            if path.is_file():
                paths.append(("bootstrap/" + relative.as_posix(), path))
        launcher = wrapper_path or Path(__file__).resolve()
        if launcher.is_symlink() or not launcher.is_file():
            raise Stage1Error(f"Stage1 launcher source is not a regular file: {launcher}")
        paths.append(("tools/latent_stage1.py", launcher))
        for name, path in sorted(paths, key=lambda item: item[0]):
            payload = path.read_bytes()
            name_bytes = name.encode("utf-8")
            digest.update(len(name_bytes).to_bytes(8, "big"))
            digest.update(name_bytes)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    except OSError as exc:
        raise Stage1Error(f"cannot read Stage1 build input: {exc}") from exc
    return digest.hexdigest()


def _cache_identity(root: Path, *, stage0_commit: str = STAGE0_COMMIT,
                    version: tuple[int, int] | None = None,
                    ucd: str | None = None,
                    platform_id: str | None = None,
                    wrapper_path: Path | None = None) -> dict[str, Any]:
    py_version = version or (sys.version_info.major, sys.version_info.minor)
    return {
        "schema": MANIFEST_SCHEMA,
        "stage0_commit": stage0_commit,
        # Stage0 embeds the absolute bootstrap source root in compiler.py.
        # A user cache may serve multiple clones, so isolate those artifacts.
        "checkout_root": str(root.resolve()),
        "bootstrap_source_sha256": _source_digest(root, wrapper_path),
        "python": f"{py_version[0]}.{py_version[1]}",
        "ucd": ucd or unicodedata.unidata_version,
        "platform": platform_id or f"{sys.platform}-{platform.machine()}",
        # Stage0 best-effort compiles Java sidecars into Stage1; their bytecode
        # can otherwise survive a JDK switch because the compiler trusts mtimes.
        "javac": _javac_identity(),
    }


def _cache_key(identity: dict[str, Any]) -> str:
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _javac_identity() -> dict[str, str] | None:
    executable = shutil.which("javac")
    if executable is None:
        return None
    try:
        result = subprocess.run([executable, "-version"], text=True,
                                encoding="utf-8", errors="replace",
                                capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Stage1Error(f"cannot determine javac version for cache identity: {exc}") from exc
    version = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or not version:
        raise Stage1Error(
            "javac is present but its version cannot be determined; "
            "refusing to use a potentially stale Stage1 cache")
    return {"path": os.path.realpath(executable), "version": version[:512]}


def _cache_root_from_environment(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("XDG_CACHE_HOME", "")
    if configured:
        candidate = Path(configured).expanduser()
        base = candidate if candidate.is_absolute() else Path.home() / ".cache"
    else:
        base = Path.home() / ".cache"
    return base / "latent" / "stage1"


def _ensure_private_cache_root(path: Path) -> Path:
    if not path.is_absolute():
        raise Stage1Error(f"cache path must be absolute: {path}")
    uid = os.getuid()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise Stage1Error(f"cannot create user cache directory {current}: {exc}") from exc
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise Stage1Error(f"cannot inspect user cache directory {current}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise Stage1Error(f"cache path component is not a real directory: {current}")
        mode = stat.S_IMODE(info.st_mode)
        safe_sticky_tmp = (info.st_uid == 0 and bool(mode & stat.S_ISVTX)
                           and bool(mode & 0o002))
        if info.st_uid != uid and mode & 0o022 and not safe_sticky_tmp:
            raise Stage1Error(
                f"cache path has a group/world-writable parent: {current}")
        if info.st_uid == uid and mode & 0o022:
            raise Stage1Error(
                f"cache path has unsafe writable permissions: {current}")
    try:
        leaf = os.lstat(path)
        if leaf.st_uid != uid:
            raise Stage1Error(f"cache directory is not owned by this user: {path}")
        if stat.S_IMODE(leaf.st_mode) & 0o022:
            raise Stage1Error(f"cache directory is group/world writable: {path}")
        os.chmod(path, 0o700)
    except Stage1Error:
        raise
    except OSError as exc:
        raise Stage1Error(f"cannot secure user cache directory {path}: {exc}") from exc
    return path


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_cache_path(path: Path, *, uid: int) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise Stage1Error(f"cannot inspect invalid cache entry {path}: {exc}") from exc
    if info.st_uid != uid:
        raise Stage1Error(f"refusing to remove cache entry owned by another user: {path}")
    try:
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except OSError as exc:
        raise Stage1Error(f"cannot remove invalid cache entry {path}: {exc}") from exc


def _artifact_hashes(directory: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            info = os.lstat(path)
            if (not stat.S_ISREG(info.st_mode) or
                    not _ALLOWED_ARTIFACT_NAME.fullmatch(path.name) or
                    path.name in (MANIFEST_NAME, ".", "..")):
                raise Stage1Error(f"unexpected Stage1 cache artifact: {path.name}")
            entries.append({"name": path.name,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "size": info.st_size})
    except OSError as exc:
        raise Stage1Error(f"cannot inspect Stage1 build output: {exc}") from exc
    names = {entry["name"] for entry in entries}
    if not {"compiler.py", "latent_host.py"}.issubset(names):
        raise Stage1Error(
            "fixed Stage0 build did not produce both compiler.py and latent_host.py")
    return entries


def _write_manifest(directory: Path, key: str, identity: dict[str, Any],
                    files: list[dict[str, Any]]) -> None:
    manifest = {"schema": MANIFEST_SCHEMA, "key": key,
                "identity": identity, "files": files}
    payload = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path = directory / MANIFEST_NAME
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def _validate_cache_entry(path: Path, key: str,
                          identity: dict[str, Any]) -> bool:
    uid = os.getuid()
    try:
        directory_info = os.lstat(path)
        if (not stat.S_ISDIR(directory_info.st_mode) or
                stat.S_ISLNK(directory_info.st_mode) or
                directory_info.st_uid != uid or
                stat.S_IMODE(directory_info.st_mode) & 0o077):
            return False
        children = list(path.iterdir())
        by_name: dict[str, Path] = {}
        for child in children:
            info = os.lstat(child)
            if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or
                    info.st_uid != uid or stat.S_IMODE(info.st_mode) & 0o077):
                return False
            by_name[child.name] = child
        manifest_path = by_name.get(MANIFEST_NAME)
        if manifest_path is None:
            return False
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or
                set(manifest) != {"schema", "key", "identity", "files"} or
                manifest["schema"] != MANIFEST_SCHEMA or
                manifest["key"] != key or manifest["identity"] != identity):
            return False
        files = manifest["files"]
        if not isinstance(files, list):
            return False
        expected_names: set[str] = set()
        for item in files:
            if (not isinstance(item, dict) or
                    set(item) != {"name", "sha256", "size"} or
                    not isinstance(item["name"], str) or
                    not _ALLOWED_ARTIFACT_NAME.fullmatch(item["name"]) or
                    item["name"] == MANIFEST_NAME or
                    not isinstance(item["sha256"], str) or
                    len(item["sha256"]) != 64 or
                    not isinstance(item["size"], int) or item["size"] < 0):
                return False
            name = item["name"]
            if name in expected_names or name not in by_name:
                return False
            expected_names.add(name)
            payload = by_name[name].read_bytes()
            if len(payload) != item["size"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
                return False
        if set(by_name) != expected_names | {MANIFEST_NAME}:
            return False
        return {"compiler.py", "latent_host.py"}.issubset(expected_names)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def _extract_stage0(root: Path, destination: Path) -> None:
    command = ["git", "-C", str(root), "archive", STAGE0_COMMIT]
    try:
        result = subprocess.run(command, capture_output=True, check=False)
    except OSError as exc:
        raise Stage1Error(f"cannot run local Git to read fixed Stage0: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise Stage1Error(
            f"fixed Stage0 commit {STAGE0_COMMIT} is unavailable in this local "
            "checkout; fetch the repository history and retry (no network fetch "
            "is performed automatically)" + (f": {detail}" if detail else ""))
    destination.mkdir(mode=0o700)
    import io
    try:
        with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
            for member in archive.getmembers():
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise Stage1Error(f"unsafe path in fixed Stage0 archive: {member.name}")
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise Stage1Error(f"cannot read fixed Stage0 file: {member.name}")
                    with stream, target.open("xb") as output:
                        shutil.copyfileobj(stream, output)
                else:
                    raise Stage1Error(
                        f"unsupported link or special file in fixed Stage0 archive: {member.name}")
    except (tarfile.TarError, OSError) as exc:
        raise Stage1Error(f"cannot extract fixed Stage0 archive safely: {exc}") from exc


def _build_stage1(root: Path, output: Path, work: Path) -> None:
    stage0 = work / "stage0"
    _extract_stage0(root, stage0)
    output.mkdir(mode=0o700)
    command = [sys.executable, "-I", str(stage0 / "latent.py"),
               str(root / "bootstrap" / "compiler.lt"), "-t", "py",
               "-o", str(output)]
    try:
        result = subprocess.run(
            command, cwd=root, text=True, encoding="utf-8", errors="replace",
            capture_output=True, check=False)
    except OSError as exc:
        raise Stage1Error(f"cannot start fixed Stage0 to build Stage1: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if len(detail) > 4000:
            detail = detail[-4000:]
        raise Stage1Error(
            f"fixed Stage0 failed to build Stage1 (exit {result.returncode})" +
            (f":\n{detail}" if detail else ""))
    try:
        shutil.copyfile(root / "bootstrap" / "latent_host.py",
                        output / "latent_host.py")
    except OSError as exc:
        raise Stage1Error(f"cannot stage the approved Stage1 host adapter: {exc}") from exc


def _assert_current_build_inputs(root: Path, identity: dict[str, Any]) -> None:
    expected_root = identity.get("checkout_root")
    if expected_root is not None and str(root.resolve()) != expected_root:
        raise Stage1Error("checkout root changed after computing the Stage1 cache key")
    expected_digest = identity.get("bootstrap_source_sha256")
    if expected_digest is None:
        return
    launcher = root / "tools" / "latent_stage1.py"
    if not launcher.is_file():
        launcher = Path(__file__).resolve()
    if _source_digest(root, wrapper_path=launcher) != expected_digest:
        raise Stage1Error(
            "Stage1 build inputs changed after computing the cache key; "
            "retry after source edits finish")


class Stage1Cache:
    """Private, content-addressed cache with lock-serialized atomic publishing."""

    def __init__(self, cache_root: Path, identity: dict[str, Any]):
        self.cache_root = cache_root
        self.identity = identity
        self.key = _cache_key(identity)

    def ensure(self, root: Path,
               builder: Callable[[Path, Path, Path], None] = _build_stage1) -> Path:
        cache_root = _ensure_private_cache_root(self.cache_root)
        entry = cache_root / self.key
        lock_path = cache_root / ".lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_fd = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise Stage1Error(f"cannot open Stage1 cache lock: {exc}") from exc
        try:
            lock_info = os.fstat(lock_fd)
            if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid() or
                    stat.S_IMODE(lock_info.st_mode) & 0o077):
                raise Stage1Error("Stage1 cache lock has unsafe type, owner, or permissions")
            try:
                import fcntl
            except ImportError as exc:
                raise Stage1Error(
                    "Stage1 cache requires POSIX file locking; this host is unsupported") from exc
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            _assert_current_build_inputs(root, self.identity)
            self._clean_orphan_builds(cache_root)
            if _validate_cache_entry(entry, self.key, self.identity):
                return entry
            if os.path.lexists(entry):
                _remove_cache_path(entry, uid=os.getuid())
            staging = Path(tempfile.mkdtemp(prefix=BUILD_PREFIX, dir=cache_root))
            output = staging / "stage1"
            try:
                builder(root, output, staging)
                _assert_current_build_inputs(root, self.identity)
                if not output.is_dir() or output.is_symlink():
                    raise Stage1Error("Stage1 builder did not create a regular output directory")
                files = _artifact_hashes(output)
                for item in files:
                    os.chmod(output / item["name"], 0o600)
                _write_manifest(output, self.key, self.identity, files)
                _fsync_directory(output)
                if os.path.lexists(entry):
                    raise Stage1Error("cache destination appeared during locked publication")
                os.rename(output, entry)
                _fsync_directory(cache_root)
            except Stage1Error:
                raise
            except (OSError, ValueError, TypeError) as exc:
                raise Stage1Error(f"failed to build or publish Stage1 cache: {exc}") from exc
            finally:
                _remove_cache_path(staging, uid=os.getuid())
            if not _validate_cache_entry(entry, self.key, self.identity):
                _remove_cache_path(entry, uid=os.getuid())
                raise Stage1Error("published Stage1 cache failed its integrity check")
            return entry
        finally:
            try:
                os.close(lock_fd)
            except OSError:
                pass

    @staticmethod
    def _clean_orphan_builds(cache_root: Path) -> None:
        for path in cache_root.iterdir():
            if path.name.startswith(BUILD_PREFIX):
                _remove_cache_path(path, uid=os.getuid())


def _launch_stage1(entry: Path, arguments: list[str], cwd: Path) -> int:
    # Do not let importing the cached host adapter create unmanifested .pyc
    # files inside the cache entry. Generated programs run separately and
    # retain their normal Python bytecode behavior.
    command = [sys.executable, "-B", str(entry / "compiler.py"), *arguments]
    try:
        result = subprocess.run(command, cwd=cwd, check=False)
    except OSError as exc:
        raise Stage1Error(f"cannot start Stage1 compiler: {exc}") from exc
    return result.returncode


def _print_preview_help_notice() -> None:
    print(
        "latent-stage1 is an experimental preview (not the default compiler).\n"
        "Python target only; verified on Ubuntu 24.04 with CPython 3.11/UCD "
        "14.0.0 and CPython 3.12/UCD 15.0.0.\n"
        "Its default output directory is build/py (latent.py uses a different "
        "default). No Java target, REPL, diagnostics, or editor metadata.\n"
        "Review this preview within 90 days after its implementation reaches "
        "main, and before any first public release containing it.",
        file=sys.stdout,
    )


def main(arguments: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if arguments is None else arguments)
    try:
        root = _repository_root()
        _check_supported_host()
        cwd = _check_working_directory(root)
        identity = _cache_identity(root)
        cache = Stage1Cache(_cache_root_from_environment(), identity)
        entry = cache.ensure(root)
        if args in (["-h"], ["--help"]):
            _print_preview_help_notice()
        # Preserve argv exactly. All CLI policy and diagnostics remain in Stage1.
        return _launch_stage1(entry, args, cwd)
    except Stage1Error as exc:
        print(f"latent-stage1: error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"latent-stage1: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
