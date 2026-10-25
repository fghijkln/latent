"""Unit tests for the independent Latent transition verifier (verifier.py).

These exercise the verifier on its own, with stand-in transitions, so the
trusted core is tested without trusting gen_py. The end-to-end cases at the
bottom confirm it is actually installed in generated programs.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import verifier
from tests import test_p18_callbacks as p18


class _Obj:
    pass


class _HostInt(int):
    pass


class _HostList(list):
    pass


class _HostDict(dict):
    pass


def _ns():
    # Deliberately naive "implementations": Python operators, no guards,
    # exactly the fallback that produced R1.
    return {
        "_wv_add": lambda a, b: a + b,
        "_wv_sub": lambda a, b: a - b,
        "_wv_mul": lambda a, b: a * b,
        "_wv_div": lambda a, b: a / b,
        "_wv_mod": lambda a, b: a % b,
        "_wv_pow": lambda a, b: a ** b,
        "_wv_neg": lambda a: -a,
        "_wv_wgetattr": lambda h, attr: getattr(h, attr),
        "_wv_index": lambda v, k: v[int(k)],
        "_wv_keys": lambda _m: [7],
        "_wv_push": lambda xs, x: xs.append(x),
        "_wv_wsetattr": lambda h, attr, value: setattr(h, attr, value),
        "_LtObj": _Obj,
    }


class VerifierContractTests(unittest.TestCase):
    def setUp(self):
        self.ns = verifier._lt_vf_install(_ns())

    def test_legal_transitions_pass_through(self):
        self.assertEqual(self.ns["_wv_add"](1.0, 2.0), 3.0)
        self.assertEqual(self.ns["_wv_add"]("a", "b"), "ab")
        self.assertEqual(self.ns["_wv_add"]([1.0], [2.0]), [1.0, 2.0])
        self.assertEqual(self.ns["_wv_neg"](2.5), -2.5)
        self.assertEqual(self.ns["_wv_pow"](2.0, 3.0), 8.0)

    def test_bool_arithmetic_is_rejected_r1(self):
        for name in ("_wv_add", "_wv_sub", "_wv_mul", "_wv_pow"):
            with self.assertRaises(verifier.LatentVerifierError, msg=name):
                self.ns[name](True, False)
        with self.assertRaises(verifier.LatentVerifierError):
            self.ns["_wv_neg"](True)
        with self.assertRaises(verifier.LatentVerifierError):
            self.ns["_wv_sub"](True, -0.0)

    def test_illegal_operands_keep_the_implementation_diagnostic(self):
        # Implementation rejects too: its own error surfaces unchanged.
        with self.assertRaises(TypeError) as cm:
            self.ns["_wv_add"]("a", 1.0)
        self.assertNotIsInstance(cm.exception, verifier.LatentVerifierError)

    def test_illegal_operands_the_implementation_accepts_are_rejected(self):
        # Python happily computes these; the Latent contract does not allow them.
        with self.assertRaises(verifier.LatentVerifierError):
            self.ns["_wv_add"](True, 1.0)
        with self.assertRaises(verifier.LatentVerifierError):
            self.ns["_wv_mul"](True, 3.0)

    def test_host_int_never_crosses_a_transition(self):
        with self.assertRaises(verifier.LatentVerifierError):
            self.ns["_wv_wgetattr"](1, "__class__")
        with self.assertRaises(verifier.LatentVerifierError):
            self.ns["_wv_index"]((7, 8), 0.0)  # host tuple yields a raw int

    def test_nested_host_ints_are_rejected_on_input_and_output(self):
        values = ([7], {"x": (7,)}, {"x": {"nested": [7]}},
                  {"x": {_HostInt(7)}}, {7: "host-int-key"},
                  _HostList([_HostInt(7)]),
                  _HostDict({"x": _HostList([7])}))
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(verifier.LatentVerifierError):
                    self.ns["_wv_add"](value, [])

        class _Host:
            value = {"nested": ([1.0, _HostInt(7)],)}

        with self.assertRaises(verifier.LatentVerifierError):
            self.ns["_wv_wgetattr"](_Host(), "value")
        with self.assertRaises(verifier.LatentVerifierError):
            self.ns["_wv_keys"](None)
        with self.assertRaises(verifier.LatentVerifierError):
            self.ns["_wv_push"]([], {"nested": (7,)})
        with self.assertRaises(verifier.LatentVerifierError):
            self.ns["_wv_wsetattr"](_Host(), "value", [7])

    def test_cyclic_native_container_walk_is_bounded(self):
        value = []
        value.append(value)
        result = self.ns["_wv_add"](value, [])
        self.assertIs(result[0], value)

        deep = []
        for _ in range(1500):
            deep = [deep]
        deep_result = self.ns["_wv_add"](deep, [])
        self.assertIs(deep_result[0], deep[0])

        deep_with_int = [7]
        for _ in range(1500):
            deep_with_int = [deep_with_int]
        with self.assertRaises(verifier.LatentVerifierError):
            self.ns["_wv_add"](deep_with_int, [])

    def test_verifier_is_self_contained(self):
        import ast
        with open(verifier.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imports = [n for n in ast.walk(tree)
                   if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual(imports, [])


class VerifierInstalledEndToEndTests(unittest.TestCase):
    def run_latent(self, src):
        sys.path.insert(0, os.path.join(ROOT, "playground"))
        from playground import app
        return app.run_program(src)

    def test_r1_payloads_fail_cleanly(self):
        for src in ("x = true + false\nsay x.__class__\n",
                    "x = -true\nsay x.__class__\n"):
            r = self.run_latent(src)
            self.assertFalse(r["ok"], src)
            self.assertIn("Latent runtime error:", r.get("stderr", ""), src)
            self.assertNotIn("<class 'int'>", r.get("stdout", ""), src)

    def test_violation_is_catchable_like_other_runtime_errors(self):
        r = self.run_latent('try:\n    say true + 1\ncatch e:\n    say "caught"\n')
        self.assertTrue(r["ok"])
        self.assertEqual(r["stdout"], "caught\n")

    def test_ordinary_arithmetic_unchanged(self):
        r = self.run_latent('say 1 + 2\nsay -3\nsay "a" + "b"\nsay [1] + [2]\n')
        self.assertTrue(r["ok"])
        self.assertEqual(r["stdout"], "3\n-3\nab\n[1, 2]\n")

    def test_python_host_integer_results_are_normalized_recursively(self):
        src = (
            'math = py "math"\n'
            'say math.factorial(10)\n'
            'json = py "json"\n'
            'data = json.loads("{\\"nested\\": {\\"count\\": 3}, '
            '\\"items\\": [4, 5], \\"flag\\": true}")\n'
            'say data\n'
            'collections = py "collections"\n'
            'counts = collections.Counter("aab")\n'
            'say counts.most_common(2)\n')
        expected = (
            '3628800\n{"nested": {"count": 3}, "items": [4, 5], '
            '"flag": true}\n[["a", 2], ["b", 1]]\n')
        with tempfile.TemporaryDirectory(prefix="latent-verifier-host-int-") as tmp:
            root = Path(tmp)
            source = root / "host_ints.lt"
            source.write_text(src, encoding="utf-8")
            for target in ("py", "java"):
                outdir = root / target
                outdir.mkdir()
                result = p18.compile_and_run(source, target, outdir)
                self.assertEqual(result.returncode, 0,
                                 f"{target}: {result.stderr}")
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
