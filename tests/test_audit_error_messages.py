"""Cross-backend regressions for user-visible caught addition diagnostics."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import test_p18_callbacks as p18

SOURCE = '''\
try:
    say 1 + "a"
catch error:
    say error
try:
    say nil + "a"
catch error:
    say error
try:
    say [1, "a"] + nil
catch error:
    say error
try:
    say {"x": 1} + false
catch error:
    say error
'''
EXPECTED = ('bad + operands: 1, a\n'
            'bad + operands: nil, a\n'
            'bad + operands: [1, "a"], nil\n'
            'bad + operands: {"x": 1}, false\n')


class CaughtErrorMessageTests(unittest.TestCase):
    def test_addition_errors_render_source_values_in_both_backends(self):
        with tempfile.TemporaryDirectory(prefix="latent-audit-errors-") as tmp:
            root = Path(tmp)
            source = root / "caught_addition.lt"
            source.write_text(SOURCE, encoding="utf-8")
            for target in ("py", "java"):
                with self.subTest(target=target):
                    output = root / target
                    output.mkdir()
                    result = p18.compile_and_run(source, target, output)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, EXPECTED)
                    self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
