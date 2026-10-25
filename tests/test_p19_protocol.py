#!/usr/bin/env python3
"""P19 named-call and borrowed-callback kwargs wire contract tests."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests._safe_test_env import clean_test_environment

ENV = clean_test_environment({"PYTHONPATH": str(ROOT / "tests" / "python")})
MISSING = object()


def request(payload, ident=1):
    return {"kind": "request", "id": ident, "parent": 0, "payload": payload}


def call(kwargs=MISSING, **changes):
    payload = {"op": "call", "target": {"mod": "p18_callbacks_host"},
               "attr": "identity", "args": []}
    if kwargs is not MISSING:
        payload["kwargs"] = kwargs
    payload.update(changes)
    return payload


def invoke(frames):
    return subprocess.run(
        [sys.executable, str(ROOT / "runtime" / "ltpy.py")],
        check=False, input="".join(json.dumps(frame) + "\n" for frame in frames + [{"op": "quit"}]),
        capture_output=True, text=True, env=ENV, timeout=10)


class NamedWireTests(unittest.TestCase):
    def assert_fatal(self, payload, preceding=(), following=()):
        frames = list(preceding) + [request(payload, len(preceding) + 1)]
        frames.extend(request(item, len(frames) + 1) for item in following)
        result = invoke(frames)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("ProtocolAbort", result.stderr)
        # An invalid request must never be disguised as a normal host response.
        self.assertEqual(len(result.stdout.splitlines()), len(preceding), result.stdout)

    def _call_with_value(self, value, path):
        return call(args=[value]) if path == "args" else call([["value", value]])

    def _nested_ref(self, value, shape):
        if shape == "direct":
            return value
        if shape == "list":
            return [value]
        return {"__map": [["nested", value]]}

    def test_old_positional_and_new_empty_or_nonempty_named_calls(self):
        result = invoke([
            request(call(args=["old"]), 1),
            request(call([], args=["new-empty"]), 2),
            request(call([["value", "named"]]), 3),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        frames = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([f["payload"] for f in frames],
                         [{"value": "old"}, {"value": "new-empty"}, {"value": "named"}])

    def test_all_three_call_operations_accept_named_pairs(self):
        result = invoke([
            request({"op": "get_module_function", "target": {"mod": "p14_host"},
                     "attr": "scale"}, 1),
            request({"op": "call_module_function", "id": 1, "args": [],
                     "kwargs": [["value", 7]]}, 2),
            request({"op": "call", "target": {"mod": "p13_host"}, "attr": "Sample",
                     "args": [], "kwargs": []}, 3),
            request({"op": "get_bound_method", "target": {"id": 2}, "attr": "push"}, 4),
            request({"op": "call_bound_method", "id": 3, "args": [],
                     "kwargs": [["value", "named"]]}, 5),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        frames = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(frames[1]["payload"], {"value": 14})
        self.assertEqual(frames[4]["payload"], {"value": 1})
        self.assertEqual(result.stderr, "")

    def test_invalid_named_structure_is_fatal_for_every_call_operation(self):
        invalid = [None, {}, "bad", [None], [[]], [["value"]],
                   [["value", 1, 2]], [[1, 2]], [[True, 2]],
                   [["value", 1], ["value", 2]]]
        for op in ("call", "call_bound_method", "call_module_function"):
            for kwargs in invalid:
                with self.subTest(op=op, kwargs=kwargs):
                    payload = call(kwargs) if op == "call" else {
                        "op": op, "id": 1, "args": [], "kwargs": kwargs}
                    self.assert_fatal(payload)

    def test_extra_fields_and_noncall_kwargs_are_fatal(self):
        self.assert_fatal(call([], surprise=True))
        for op in ("call", "call_bound_method", "call_module_function"):
            payload = call([]) if op == "call" else {
                "op": op, "id": 1, "args": [], "kwargs": []}
            del payload["args"]
            self.assert_fatal(payload)
        self.assert_fatal({"op": "get", "target": {"mod": "p14_host"},
                           "attr": "scale", "kwargs": []})
        self.assert_fatal({"op": "binop", "name": "__add__", "args": [1, 2],
                           "kwargs": []})

    def test_invalid_named_codec_values_are_protocol_errors(self):
        for value in ({"__num": "unknown"}, {"__map": None},
                      {"__map": [["key"]]}, {"__ref": 0},
                      {"__ref": True}):
            with self.subTest(value=value):
                self.assert_fatal(call([["value", value]]))

    def test_unknown_and_structurally_invalid_refs_are_fatal_in_args_and_kwargs(self):
        invalid_refs = [
            {"__ref": 999999},
            {"__ref": 0},
            {"__ref": True},
            {"__ref": "not-an-id"},
            {"__ref": 1, "extra": "not-a-valid-envelope"},
        ]
        for path in ("args", "kwargs"):
            for shape in ("direct", "list", "map"):
                for ref in invalid_refs:
                    value = self._nested_ref(ref, shape)
                    with self.subTest(path=path, shape=shape, ref=ref):
                        self.assert_fatal(
                            self._call_with_value(value, path),
                            following=[call(args=["must-not-run"])])

    def test_valid_existing_refs_decode_in_both_call_paths(self):
        first = invoke([request(call(args=[], attr="opaque_value"), 1)])
        self.assertEqual(first.returncode, 0, first.stderr)
        ref = json.loads(first.stdout.splitlines()[0])["payload"]["value"]
        self.assertEqual(set(ref), {"__ref"})
        result = invoke([
            request(call(args=[], attr="opaque_value"), 1),
            request(call(args=[ref]), 2),
            request(call([["value", ref]]), 3),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        frames = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(frames), 3)
        self.assertTrue(all(set(frame["payload"]["value"]) == {"__ref"}
                            for frame in frames))

    def test_released_ref_reuse_is_fatal_in_args_and_kwargs(self):
        for path in ("args", "kwargs"):
            for shape in ("direct", "list", "map"):
                with self.subTest(path=path, shape=shape):
                    initial = request(call(args=[], attr="opaque_value"), 1)
                    release = request(call(args=["release"]), 2)
                    release["release_refs"] = [1]
                    stale = self._nested_ref({"__ref": 1}, shape)
                    self.assert_fatal(
                        self._call_with_value(stale, path),
                        preceding=[initial, release],
                        following=[call(args=["must-not-run"])])

    def test_named_callback_is_direct_and_positional_frame_has_empty_kwargs(self):
        result = invoke([
            request(call([["callback", {"__lt_callback__": 1}]], attr="invoke_one")),
            {"kind": "callback_result", "parent": 1, "id": 1, "value": "ok"},
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        frames = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(set(frames[0]),
                         {"kind", "parent", "id", "callback", "args", "kwargs"})
        self.assertEqual(frames[0]["args"], ["one"])
        self.assertEqual(frames[0]["kwargs"], [])
        self.assertEqual(frames[1]["payload"], {"value": "ok"})
        self.assertEqual(result.stderr, "")

    def test_callback_kwargs_are_emitted_as_ordered_pairs(self):
        result = invoke([
            request(call([["callback", {"__lt_callback__": 1}]],
                         attr="call_ordered_kwargs")),
            {"kind": "callback_result", "parent": 1, "id": 1,
             "value": "wire-ok"},
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        frames = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(frames[0], {
            "kind": "callback", "parent": 1, "id": 1, "callback": 1,
            "args": ["head", "tail"],
            "kwargs": [["flag", True], ["z", 1.0], ["if", 2.0], ["雪", 3.0]],
        })
        self.assertEqual(frames[1]["payload"], {"value": "wire-ok"})
        self.assertEqual(result.stderr, "")

    def test_nested_invalid_and_duplicate_tokens_are_fatal(self):
        descriptor = {"__lt_callback__": 1}
        for value in ([descriptor], {"__map": [["hidden", descriptor]]},
                      {"__lt_callback__": 0}, {"__lt_callback__": True}):
            with self.subTest(value=value):
                self.assert_fatal(call([["value", value]]))
        self.assert_fatal(call([["other", descriptor]], args=[descriptor]))
        self.assert_fatal(call([["value", descriptor], ["other", descriptor]]))

    def test_map_control_lookalikes_remain_data(self):
        value = {"__map": [["kind", "request"], ["kwargs", [["x", 1]]],
                           ["__lt_callback__", 99]]}
        result = invoke([request(call([["value", value]]))])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["payload"], {"value": value})

    def test_legacy_requests_do_not_gain_kwargs_even_when_empty(self):
        positional = invoke([call(args=["legacy-positional"])])
        self.assertEqual(positional.returncode, 0, positional.stderr)
        self.assertEqual(json.loads(positional.stdout), {"value": "legacy-positional"})
        for kwargs in ([], [["value", "must-not-run"]]):
            with self.subTest(kwargs=kwargs):
                result = invoke([call(kwargs)])
                if result.returncode == 0:
                    response = json.loads(result.stdout)
                    self.assertIn("error", response)
                    self.assertIn("kwargs", response["error"])
                else:
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("ProtocolAbort", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
