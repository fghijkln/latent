"""Deterministic differential scan for the N6 number-format boundaries."""
from __future__ import annotations

from decimal import Decimal
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import test_p18_callbacks as p18

INTERVALS = ((1e-6, 1e-4), (1e16, 1e17))


def _scan_values():
    values = set()
    for lower, upper in INTERVALS:
        for i in range(257):
            value = lower + (upper - lower) * i / 256
            values.update((value, -value))
        for boundary in (lower, upper):
            values.update((boundary, -boundary))
            for direction in (-math.inf, math.inf):
                adjacent = math.nextafter(boundary, direction)
                values.update((adjacent, -adjacent))
    return sorted(values)


def _literal(value):
    # Latent source has no exponent notation; expanding Python's shortest
    # round-trip decimal preserves the same binary float when parsed again.
    return format(Decimal(repr(value)), "f")


def test_n6_number_format_matches_across_both_notation_intervals():
    values = _scan_values()
    assert len(values) >= 1000
    chunks = [values[i:i + 64] for i in range(0, len(values), 64)]
    source_text = "\n".join(
        "say [" + ", ".join(_literal(value) for value in chunk) + "]"
        for chunk in chunks) + "\n"

    with tempfile.TemporaryDirectory(prefix="latent-n6-number-format-scan-") as tmp:
        root = Path(tmp)
        source = root / "n6_number_format_scan.lt"
        source.write_text(source_text, encoding="utf-8")
        python_output = None
        for target in ("py", "java"):
            output = root / target
            output.mkdir()
            result = p18.compile_and_run(source, target, output)
            assert result.returncode == 0, f"{target}: {result.stderr[-1600:]}"
            assert result.stderr == ""
            assert len(result.stdout.splitlines()) == len(chunks)
            if target == "py":
                python_output = result.stdout
            else:
                assert result.stdout == python_output, "Java formatting differs from Python"
