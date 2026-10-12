"""Versioned, deliberately thin OS adapter for the Latent bootstrap compiler.

This module must not tokenize, parse, inspect, transform, or emit Latent source.
All policy belongs to the compiler implemented in .lt modules. API v1 only
exposes argv, path/file primitives, explicit runtime copying, output streams,
and execution of a generated Python program for the compiler's --run option.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

API_VERSION = 1
__all__ = (
    "API_VERSION",
    "argv", "stdout", "stderr", "exit_process",
    "path_join", "path_dirname", "runtime_source_dir", "path_basename",
    "path_abspath", "path_realpath", "path_relpath", "path_norm",
    "path_exists", "path_is_file", "path_is_dir",
    "read_utf8", "write_utf8", "make_dirs", "make_temp_dir",
    "remove_temp_dir", "copy_explicit_files", "run_python",
)
# Temporary build directories created by this adapter use this fixed prefix;
# remove_temp_dir rejects paths outside that explicit temp-root namespace.
_TEMP_DIR_PREFIX = "latent-build-"
_TEMP_DIRS: set[str] = set()


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        return "not-found"
    if isinstance(exc, PermissionError):
        return "permission-denied"
    if isinstance(exc, IsADirectoryError):
        return "is-directory"
    if isinstance(exc, UnicodeDecodeError):
        return "invalid-utf8"
    if isinstance(exc, NotADirectoryError):
        return "not-a-directory"
    return "io-error"


def argv() -> list[str]:
    """Return the raw arguments following the executable name."""
    return list(sys.argv[1:])


def stdout(text: str) -> None:
    sys.stdout.write(text)


def stderr(text: str) -> None:
    sys.stderr.write(text)


def exit_process(status: int) -> None:
    raise SystemExit(status)


def path_join(left: str, right: str) -> str:
    return os.path.join(left, right)


def path_dirname(path: str) -> str:
    return os.path.dirname(path)


def runtime_source_dir() -> str:
    """Return this installed adapter's directory for explicitly bundled files."""
    return os.path.dirname(os.path.abspath(__file__))


def path_basename(path: str) -> str:
    return os.path.basename(path)


def path_abspath(path: str) -> str:
    return os.path.abspath(path)


def path_realpath(path: str) -> str:
    return os.path.realpath(path)


def path_relpath(path: str, start: str) -> str:
    try:
        return os.path.relpath(path, start)
    except ValueError:
        # Windows has no relative path across drives; retain the absolute
        # candidate so the Latent module graph can reject it as out-of-root.
        return os.path.normpath(path)


def path_norm(path: str) -> str:
    return os.path.normpath(path).replace(os.sep, "/")


def path_exists(path: str) -> bool:
    return os.path.exists(path)


def path_is_file(path: str) -> bool:
    return os.path.isfile(path)


def path_is_dir(path: str) -> bool:
    return os.path.isdir(path)


def read_utf8(path: str) -> dict:
    """Read one explicitly selected UTF-8 file; never enumerate directories."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as stream:
            return {"ok": True, "value": stream.read(), "error": ""}
    except (OSError, UnicodeDecodeError) as exc:
        return {"ok": False, "value": "", "error": _error_code(exc)}


def write_utf8(path: str, text: str) -> dict:
    """Write one explicit UTF-8 file with stable LF newline handling."""
    temporary = None
    try:
        parent = os.path.dirname(os.path.abspath(path))
        descriptor, temporary = tempfile.mkstemp(prefix=".latent-write-",
                                                  dir=parent)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return {"ok": True, "value": path, "error": ""}
    except OSError as exc:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        return {"ok": False, "value": "", "error": _error_code(exc)}


def make_dirs(path: str) -> dict:
    try:
        os.makedirs(path, exist_ok=True)
        return {"ok": True, "value": path, "error": ""}
    except FileExistsError:
        return {"ok": False, "value": "", "error": "not-a-directory"}
    except OSError as exc:
        return {"ok": False, "value": "", "error": _error_code(exc)}


def make_temp_dir() -> dict:
    """Create an isolated temporary build directory with a fixed prefix."""
    try:
        path = os.path.abspath(tempfile.mkdtemp(prefix=_TEMP_DIR_PREFIX))
        _TEMP_DIRS.add(path)
        return {"ok": True, "value": path, "error": ""}
    except OSError as exc:
        return {"ok": False, "value": "", "error": _error_code(exc)}


def remove_temp_dir(path: str) -> dict:
    """Remove only a registered temp dir created by this adapter instance."""
    if not isinstance(path, str):
        return {"ok": False, "value": "", "error": "invalid-temp-dir"}
    absolute = os.path.abspath(path)
    expected_parent = os.path.abspath(tempfile.gettempdir())
    if (absolute not in _TEMP_DIRS or os.path.islink(absolute) or
            os.path.dirname(absolute) != expected_parent or
            not os.path.basename(absolute).startswith(_TEMP_DIR_PREFIX)):
        return {"ok": False, "value": "", "error": "invalid-temp-dir"}
    try:
        shutil.rmtree(absolute)
        _TEMP_DIRS.remove(absolute)
        return {"ok": True, "value": absolute, "error": ""}
    except OSError as exc:
        return {"ok": False, "value": "", "error": _error_code(exc)}


def copy_explicit_files(source_dir: str, destination_dir: str,
                        names: list[str]) -> dict:
    """Stage caller-named runtime files, then atomically replace destinations."""
    if any(not isinstance(name, str) or not name or "/" in name or
           "\\" in name or os.path.basename(name) != name for name in names):
        return {"ok": False, "value": [], "error": "invalid-name"}
    if any(os.path.isdir(os.path.join(destination_dir, name)) and
           not os.path.islink(os.path.join(destination_dir, name))
           for name in names):
        return {"ok": False, "value": [], "error": "is-directory"}

    staged = []
    copied = []
    try:
        for name in names:
            source = os.path.join(source_dir, name)
            destination = os.path.join(destination_dir, name)
            descriptor = None
            try:
                descriptor, temporary = tempfile.mkstemp(
                    prefix=".latent-copy-", dir=destination_dir)
                staged.append((name, temporary, destination))
                os.close(descriptor)
                descriptor = None
                shutil.copyfile(source, temporary)
            except OSError as exc:
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                return {"ok": False, "value": copied,
                        "error": _error_code(exc)}
        for name, temporary, destination in staged:
            os.replace(temporary, destination)
            copied.append(name)
        return {"ok": True, "value": copied, "error": ""}
    except OSError as exc:
        return {"ok": False, "value": copied, "error": _error_code(exc)}
    finally:
        for _, temporary, _ in staged:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def run_python(script_path: str, cwd: str | None = None) -> dict:
    """Run a generated Python-target program and capture its observable result."""
    try:
        result = subprocess.run(
            [sys.executable, script_path], cwd=cwd, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
        return {
            "ok": True,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": "",
        }
    except OSError as exc:
        return {
            "ok": False,
            "returncode": 127,
            "stdout": "",
            "stderr": "",
            "error": _error_code(exc),
        }
