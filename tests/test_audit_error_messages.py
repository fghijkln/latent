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
class FieldError:
    fn present(self):
        return 1
try:
    say FieldError.missing
catch error:
    say error
'''
EXPECTED = ('bad + operands: 1, a\n'
            'bad + operands: nil, a\n'
            'bad + operands: [1, "a"], nil\n'
            'bad + operands: {"x": 1}, false\n'
            'class FieldError has no fields\n')


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

    def test_uncaught_error_names_tracebacks_and_range_messages_match(self):
        cases = (
            ("throw", 'fn explode():\n    throw "boom"\nsay explode()\n',
             "Latent runtime error: Exception: boom", "explode"),
            ("index", "fn read():\n    xs = [1]\n    return xs[3]\nsay read()\n",
             "Latent runtime error: IndexError: index out of range: 3", "read"),
            ("range_type", "say range(true)\n",
             "Latent runtime error: TypeError: number expected, got true", None),
            ("range_string", 'say range("x")\n',
             "Latent runtime error: TypeError: number expected, got x", None),
            ("index_type", "say [1][true]\n",
             "Latent runtime error: TypeError: index must be an integer", None),
            ("range_limit", "say range(1000001)\n",
             "Latent runtime error: ArgumentError: range() result too large "
             "(maximum 1000000 items)", None),
        )
        with tempfile.TemporaryDirectory(prefix="latent-audit-uncaught-errors-") as tmp:
            root = Path(tmp)
            for name, source_text, expected, function_name in cases:
                source = root / (name + ".lt")
                source.write_text(source_text, encoding="utf-8")
                for target in ("py", "java"):
                    with self.subTest(case=name, target=target):
                        output = root / (name + "-" + target)
                        output.mkdir()
                        result = p18.compile_and_run(source, target, output)
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertEqual(result.stderr.splitlines()[0], expected)
                        if function_name:
                            self.assertTrue(
                                any(line.endswith(" in " + function_name)
                                    for line in result.stderr.splitlines()),
                                result.stderr)


if __name__ == "__main__":
    unittest.main()
