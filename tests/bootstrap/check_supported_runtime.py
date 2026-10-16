#!/usr/bin/env python3
"""Fail closed unless the process is on a supported CI Python/UCD pair."""
from __future__ import annotations

import platform
import sys
import unicodedata

EXPECTED_UCD = {
    (3, 11): "14.0.0",
    (3, 12): "15.0.0",
}


def main() -> int:
    version = (sys.version_info.major, sys.version_info.minor)
    expected_ucd = EXPECTED_UCD.get(version)
    actual_ucd = unicodedata.unidata_version
    os_release = platform.freedesktop_os_release()

    if (platform.system() != "Linux" or os_release.get("ID") != "ubuntu" or
            os_release.get("VERSION_ID") != "24.04"):
        print("FAIL supported CI runtime: requires Ubuntu 24.04 on Linux",
              file=sys.stderr)
        return 1
    if (platform.python_implementation() != "CPython" or
            expected_ucd is None or actual_ucd != expected_ucd):
        expected = (f"CPython {version[0]}.{version[1]}/UCD {expected_ucd}"
                    if expected_ucd is not None else
                    "CPython 3.11/UCD 14.0.0 or CPython 3.12/UCD 15.0.0")
        print("FAIL supported CI runtime: expected " + expected + ", got " +
              f"{platform.python_implementation()} {version[0]}.{version[1]}"
              f"/UCD {actual_ucd}", file=sys.stderr)
        return 1

    print("Supported CI runtime: Ubuntu 24.04 / "
          f"CPython {version[0]}.{version[1]}.{sys.version_info.micro} / "
          f"UCD {actual_ucd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
