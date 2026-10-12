"""Minimal environment for test subprocesses that execute compiler output."""
from __future__ import annotations

import os

_SAFE_ENV_NAMES = {
    "APPDATA", "COMSPEC", "HOME", "JAVA_HOME", "LANG", "LC_ALL",
    "LC_CTYPE", "LOCALAPPDATA", "PATH", "PATHEXT", "SYSTEMROOT",
    "TEMP", "TMP", "TMPDIR", "USERPROFILE", "WINDIR",
}


def clean_test_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return only runtime essentials plus explicit caller overrides."""
    environment = {
        name: value for name, value in os.environ.items()
        if name.upper() in _SAFE_ENV_NAMES
    }
    environment.setdefault("PATH", os.defpath)
    environment.update({
        "PYTHONPATH": "",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "enable_mcp": "false",
    })
    if extra is not None:
        environment.update(extra)
    return environment


def isolate_test_environment() -> dict[str, str]:
    """Replace this dedicated test runner's environment with the allowlist."""
    environment = clean_test_environment()
    os.environ.clear()
    os.environ.update(environment)
    return environment
