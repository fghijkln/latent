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
# Temporary build directories created by this adapter use this fixed prefix;
# remove_temp_dir rejects paths outside that explicit temp-root namespace.
TEMP_DIR_PREFIX = "latent-build-"
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
    return os.path.relpath(path, start)


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
    except OSError as exc:
        return {"ok": False, "value": "", "error": _error_code(exc)}


def make_temp_dir() -> dict:
    """Create an isolated temporary build directory with a fixed prefix."""
    try:
        path = os.path.abspath(tempfile.mkdtemp(prefix=TEMP_DIR_PREFIX))
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
            not os.path.basename(absolute).startswith(TEMP_DIR_PREFIX)):
        return {"ok": False, "value": "", "error": "invalid-temp-dir"}
    try:
        shutil.rmtree(absolute)
        _TEMP_DIRS.remove(absolute)
        return {"ok": True, "value": absolute, "error": ""}
    except OSError as exc:
        return {"ok": False, "value": "", "error": _error_code(exc)}


def copy_explicit_files(source_dir: str, destination_dir: str,
                        names: list[str]) -> dict:
    """Copy only caller-named runtime files; does not scan source_dir."""
    copied = []
    for name in names:
        # Reject path traversal and directory selection: the API is for files
        # explicitly listed by the Latent compiler, not arbitrary trees.
        if not name or os.path.basename(name) != name:
            return {"ok": False, "value": copied, "error": "invalid-name"}
        source = os.path.join(source_dir, name)
        destination = os.path.join(destination_dir, name)
        try:
            shutil.copyfile(source, destination)
        except OSError as exc:
            return {"ok": False, "value": copied, "error": _error_code(exc)}
        copied.append(name)
    return {"ok": True, "value": copied, "error": ""}


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
