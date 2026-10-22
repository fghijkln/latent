#!/usr/bin/env python3
"""Focused dual-target tests for P19-B outbound Python named arguments.

Python-to-Latent callback kwargs are intentionally outside this suite (P19-C).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))
from tests import test_p18_callbacks as p18

TARGETS = ("py", "java")

POSITIVE_CASES = {
    "module_and_extracted_named": (
        'host = py "p19_named_host"\n'
        'say host.scale(factor=4, value=3)\n'
        'scale = host.scale\n'
        'say scale(value=5, factor=2)\n'
        'old = py "p14_host"\n'
        'captured = old.scale\n'
        'old.rebind_scale()\n'
        'say captured(value=2)\n'
        'say old.scale(value=2)\n',
        "12\n10\n4\n6\n",
    ),
    "instance_and_extracted_bound_named": (
        'host = py "p13_host"\n'
        'item = host.Sample()\n'
        'say item.push(value="direct")\n'
        'saved_method = item.push\n'
        'say saved_method(value="extracted")\n'
        'say item.items\n',
        "1\n2\n[\"direct\", \"extracted\"]\n",
    ),
    "extracted_bound_method_survives_class_rebind": (
        'host = py "p19_named_host"\n'
        'item = host.Rebindable()\n'
        'saved = item.method\n'
        'host.rebind_class_method()\n'
        'say saved(value="x")\n'
        'say item.method(value="x")\n',
        "old:x\nnew:x\n",
    ),
    "python_mutable_default_lifetime_and_explicit_nil": (
        'host = py "p19_named_host"\n'
        'say host.mutable_default()\n'
        'say host.mutable_default()\n'
        'say host.mutable_default(value=nil)\n',
        "['x']\n['x', 'x']\nNone\n",
    ),
    "native_builtin_sort_direct_and_extracted": (
        'host = py "p19_named_host"\n'
        'items = host.sortable()\n'
        'items.sort(reverse=true)\n'
        'say items\n'
        'sort = items.sort\n'
        'sort(reverse=false)\n'
        'say items\n',
        "[3, 2, 1]\n[1, 2, 3]\n",
    ),
    "native_module_named_signature_delegation": (
        'math = py "math"\n'
        'say math.isclose(1, 1, rel_tol=0.001)\n'
        'close = math.isclose\n'
        'say close(1, 2, abs_tol=2)\n',
        "true\ntrue\n",
    ),
    "python_signature_and_keyword_order": (
        'host = py "p19_named_host"\n'
        'say host.positional_only(1, a=7, flag=true, rest=8, extra=9, z=4)\n'
        'say host.positional_only(1, 3, 4, flag=false, tail=nil, extra=6)\n'
        'say host.strict(value=nil, flag=true)\n',
        "1|2|()|True|9|[('a', 7), ('rest', 8), ('extra', 9), ('z', 4)]\n"
        "1|3|(4,)|False|None|[('extra', 6)]\nNone|True\n",
    ),
    "receiver_argument_order_once": (
        'host = py "p19_named_host"\n'
        'say host.receiver().record(first=host.mark("first", "A"), '
        'second=host.mark("second", "B"))\n'
        'say host.events()\n',
        "receiver|A|B\nreceiver,first,second\n",
    ),
    "returned_extracted_callee_mixed_args_evaluate_once": (
        'host = py "p19_named_host"\n'
        'scale = host.scale\n'
        'fn get_scale():\n'
        '    host.mark("callee", nil)\n'
        '    return scale\n'
        'host.reset()\n'
        'say get_scale()(host.mark("pos", 2), factor=host.mark("factor", 3))\n'
        'say host.events()\n',
        "6\ncallee,pos,factor\n",
    ),
    "named_callbacks_direct_and_multi_slot": (
        'host = py "p19_named_host"\n'
        'fn callback(value):\n'
        '    return value + "!"\n'
        'say host.invoke_named(callback=callback, value="one")\n'
        'say host.invoke_two_named(first=callback, second=callback, value="two")\n',
        "one!\ntwo!|two!\n",
    ),
    "same_callback_in_positional_and_named_slots": (
        'host = py "p19_named_host"\n'
        'fn callback(value):\n'
        '    return value + "!"\n'
        'say host.invoke_pos_and_named(callback, alias=callback, value="same")\n',
        "same!|same!\n",
    ),
    "python_binder_failure_skips_body_and_recovers": (
        'host = py "p19_named_host"\n'
        'try:\n'
        '    host.strict(1, value=2, flag=true)\n'
        'catch error:\n'
        '    say "binder-failed"\n'
        'say host.body_calls()\n'
        'say host.strict(value="ok", flag=true)\n',
        "binder-failed\n0\nok|True\n",
    ),
    "named_callback_binder_failure_recovers": (
        'host = py "p19_named_host"\n'
        'fn callback(value):\n'
        '    return value\n'
        'try:\n'
        '    host.invoke_named(callback=callback)\n'
        'catch error:\n'
        '    say "binder-failed"\n'
        'say host.invoke_named(callback=callback, value="ok")\n',
        "binder-failed\nok\n",
    ),
    "kwargs_value_conversion_and_opaque_handle": (
        'host = py "p19_named_host"\n'
        'say host.describe(value={"k": [1, true, nil]})\n'
        'say host.describe(value=-0.0)\n'
        'say host.opaque_label(value=host.opaque())\n',
        "dict:{'k': [1, True, None]}\nfloat:-0.0\nopaque-ok\n",
    ),
    "recovery_after_python_type_error": (
        'host = py "p19_named_host"\n'
        'try:\n'
        '    host.strict(1, value=2, flag=true)\n'
        'catch error:\n'
        '    say "caught-type-error"\n'
        'say host.scale(value=3, factor=4)\n',
        "caught-type-error\n12\n",
    ),
}

# Errors raised by Python's own binder must remain TypeError and point back to
# the outer .lt call line; the bridge must not pre-bind or retry positionally.
PYTHON_BINDING_ERRORS = {
    "native_positional_only_rejection_is_python_type_error": (
        'math = py "math"\n'
        'say math.sqrt(value=4)\n', 2,
        ("TypeError", "takes no keyword arguments",
         "native_positional_only_rejection_is_python_type_error.lt:2")),
    "positional_and_named_conflict": (
        'host = py "p19_named_host"\n'
        'say host.strict(1, value=2, flag=true)\n', 2,
        ("TypeError", "positional_and_named_conflict.lt:2")),
    "unknown_and_missing_signature": (
        'host = py "p19_named_host"\n'
        'say host.strict(1, unknown=2)\n', 2,
        ("TypeError", "unknown_and_missing_signature.lt:2")),
    "missing_keyword_only": (
        'host = py "p19_named_host"\n'
        'say host.strict(value=1)\n', 2,
        ("TypeError", "missing_keyword_only.lt:2")),
    "keyword_only_passed_positionally": (
        'host = py "p19_named_host"\n'
        'say host.strict(1, true)\n', 2,
        ("TypeError", "keyword_only_passed_positionally.lt:2")),
    "missing_positional_only": (
        'host = py "p19_named_host"\n'
        'say host.positional_only(a=1, flag=true)\n', 2,
        ("TypeError", "missing_positional_only.lt:2")),
}

ERROR_CASES = {
    "throw_short_circuits_later_argument_and_host": (
        'host = py "p19_named_host"\n'
        'try:\n'
        '    host.record(first=host.explode("boom"), '
        'second=host.mark("later", 2))\n'
        'catch error:\n'
        '    say "caught-evaluation-error"\n'
        'say host.events()\n'
        'say host.body_calls()\n',
        "caught-evaluation-error\nboom\n0\n", (),
    ),
    "duplicate_names_evaluate_both_then_skip_host": (
        'host = py "p19_named_host"\n'
        'host.reset()\n'
        'try:\n'
        '    host.record(value=host.mark("first", 1), '
        'value=host.mark("second", 2))\n'
        'catch error:\n'
        '    say error\n'
        'say host.events()\n'
        'say host.body_calls()\n',
        "got duplicate named argument 'value'\nfirst,second\n0\n",
        ("duplicate named argument",),
    ),
    "uncaught_duplicate_name_is_argument_error_at_callsite": (
        'host = py "p19_named_host"\n'
        'host.record(value=host.mark("first", 1), value=host.mark("second", 2))\n',
        None, ("ArgumentError", "duplicate named argument 'value'",
              "uncaught_duplicate_name_is_argument_error_at_callsite.lt:2"),
    ),
    "named_callback_body_error_keeps_body_and_outer_line": (
        'host = py "p19_named_host"\n'
        'fn callback(value):\n'
        '    if value == "x":\n'
        '        throw "callback-body-failure"\n'
        '    return value\n'
        'say host.invoke_named(callback=callback, value="x")\n',
        None, ("callback-body-failure",
               "named_callback_body_error_keeps_body_and_outer_line.lt:6"),
    ),
    "named_callback_binder_error_is_at_outer_line_before_body": (
        'host = py "p19_named_host"\n'
        'fn callback(value):\n'
        '    if value == "x":\n'
        '        throw "MUST-NOT-RUN"\n'
        '    return value\n'
        'say host.invoke_named(callback=callback)\n',
        None, ("TypeError",
               "named_callback_binder_error_is_at_outer_line_before_body.lt:6"),
    ),
    "named_callback_expired_after_success": (
        'host = py "p19_named_host"\n'
        'fn callback(value):\n'
        '    if true:\n'
        '        throw "MUST-NOT-RUN"\n'
        'say host.save(callback=callback)\n'
        'say host.call_saved()\n',
        None, ("callback", "expired", "named_callback_expired_after_success.lt:6"),
    ),
    "named_callback_expired_after_host_failure": (
        'host = py "p19_named_host"\n'
        'fn callback(value):\n'
        '    if true:\n'
        '        throw "MUST-NOT-RUN"\n'
        'try:\n'
        '    host.save_then_fail(callback=callback)\n'
        'catch error:\n'
        '    say "caught-host-error"\n'
        'say host.call_saved()\n',
        None, ("callback", "expired", "named_callback_expired_after_host_failure.lt:9"),
    ),
    "named_callback_cross_thread": (
        'host = py "p19_named_host"\n'
        'fn callback(value):\n'
        '    if true:\n'
        '        throw "MUST-NOT-RUN"\n'
        'say host.call_on_worker(callback=callback)\n',
        None, ("thread", "callback", "named_callback_cross_thread.lt:5"),
    ),
    "nested_callback_in_named_value_rejected": (
        'host = py "p19_named_host"\n'
        'fn callback(value):\n'
        '    return value\n'
        'say host.invoke_named(callback=callback, value=[callback])\n',
        None, ("callable",),
    ),
    "nested_map_callback_in_named_value_rejected": (
        'host = py "p19_named_host"\n'
        'fn callback(value):\n'
        '    return value\n'
        'say host.invoke_named(callback=callback, value={"hidden": callback})\n',
        None, ("callable",),
    ),
    "expanded_star_still_rejected": (
        'host = py "p19_named_host"\n'
        'say host.scale(*[])\n', None, ("argument unpacking",),
    ),
    "expanded_starstar_still_rejected": (
        'host = py "p19_named_host"\n'
        'say host.scale(**{})\n', None, ("argument unpacking",),
    ),
    "expanded_star_extracted_callable_still_rejected": (
        'host = py "p19_named_host"\n'
        'scale = host.scale\n'
        'say scale(*[])\n', None, ("argument unpacking",),
    ),
    "expanded_starstar_extracted_callable_still_rejected": (
        'host = py "p19_named_host"\n'
        'scale = host.scale\n'
        'say scale(**{})\n', None, ("argument unpacking",),
    ),
    "opaque_callable_not_promoted": (
        'host = py "p19_named_host"\n'
        'value = host.callable_object\n'
        'say value(value=1)\n', None, ("call on non-function value",),
    ),
    "returned_callable_not_promoted": (
        'host = py "p19_named_host"\n'
        'value = host.dynamic_callable()\n'
        'say value(value=1)\n', None, ("call on non-function value",),
    ),
    "property_callable_not_promoted": (
        'host = py "p19_named_host"\n'
        'value = host.property_value()\n'
        'say value(value=1)\n', None, ("call on non-function value",),
    ),
    "java_host_named_call_stays_rejected": (
        'Fixture = java "P14InteropFixture"\n'
        'say Fixture.classify(value="named")\n', None,
        ("named arguments are not supported for Python/Java interop calls",),
    ),
    "java_host_expansion_stays_rejected": (
        'Fixture = java "P14InteropFixture"\n'
        'say Fixture.classify(*[])\n', None, ("argument unpacking",),
    ),
    "java_builtin_named_call_stays_rejected": (
        'say len(value=[1])\n', None, ("does not accept named arguments",),
    ),
}


def _source_file(name: str, text: str, directory: Path) -> Path:
    path = directory / (name + ".lt")
    path.write_text(text, encoding="utf-8")
    return path


def run_positive(name: str, source_text: str, expected: str) -> list[bool]:
    outcomes = []
    with tempfile.TemporaryDirectory(prefix="latent-p19-positive-") as tmp:
        source = _source_file(name, source_text, Path(tmp))
        for target in TARGETS:
            outdir = Path(tmp) / ("out-" + target)
            outdir.mkdir()
            result = p18.compile_and_run(source, target, outdir)
            ok = result.returncode == 0 and result.stdout == expected and result.stderr == ""
            if not ok:
                print(f"FAIL {target} {name}: rc={result.returncode}")
                print(f"  stdout={result.stdout[-900:]!r}")
                print(f"  stderr={result.stderr[-1400:]!r}")
            else:
                print(f"PASS {target} {name}")
            outcomes.append(ok)
    return outcomes


def run_error(name: str, source_text: str, markers: tuple[str, ...],
              expected_stdout: str | None = None,
              absent: tuple[str, ...] = ()) -> list[bool]:
    outcomes = []
    with tempfile.TemporaryDirectory(prefix="latent-p19-negative-") as tmp:
        source = _source_file(name, source_text, Path(tmp))
        for target in TARGETS:
            outdir = Path(tmp) / ("out-" + target)
            outdir.mkdir()
            result = p18.compile_and_run(source, target, outdir)
            diagnostics = result.stderr + result.stdout
            status_ok = (result.returncode == 0 if expected_stdout is not None
                         else result.returncode not in (0, "timeout"))
            ok = (status_ok and result.returncode != "timeout" and
                  p18.has_all(diagnostics, markers) and
                  (expected_stdout is None or result.stdout == expected_stdout) and
                  all(marker not in diagnostics for marker in absent))
            if not ok:
                print(f"FAIL {target} {name}: rc={result.returncode}")
                print(f"  stdout={result.stdout[-900:]!r}")
                print(f"  stderr={result.stderr[-1400:]!r}")
            else:
                print(f"PASS {target} {name}")
            outcomes.append(ok)
    return outcomes


def test_parent_protocol_suite() -> bool:
    """Run the independently-owned ltpy framed/legacy kwargs protocol suite."""
    result = p18.run([sys.executable, str(TESTS / "test_p19_protocol.py")], timeout=30)
    ok = result.returncode == 0
    if not ok:
        print("FAIL P19 ltpy framed/legacy protocol suite")
        print(f"  stdout={result.stdout[-1800:]!r}")
        print(f"  stderr={result.stderr[-1800:]!r}")
    else:
        print("PASS P19 ltpy framed/legacy protocol suite")
    return ok


def test_java_builtin_and_static_rejection() -> list[bool]:
    cases = {
        "java_static_method_named_stays_rejected": (
            'Fixture = java "P14InteropFixture"\n'
            'say Fixture.classify(value="x")\n'),
        "java_instance_method_named_stays_rejected": (
            'Fixture = java "java.util.ArrayList"\n'
            'item = Fixture.new()\n'
            'say item.add(value="x")\n'),
    }
    outcomes = []
    for name, source_text in cases.items():
        outcomes.extend(run_error(name, source_text,
                                  ("named arguments are not supported for Python/Java interop calls",)))
    return outcomes


def main() -> int:
    checks: list[tuple[str, bool]] = []
    print("== P19-B outbound named arguments: same .lt on Python and Java targets ==")
    for name, (source_text, expected) in POSITIVE_CASES.items():
        for index, passed in enumerate(run_positive(name, source_text, expected)):
            checks.append((f"{TARGETS[index]} {name}", passed))

    for name, (source_text, line, markers) in PYTHON_BINDING_ERRORS.items():
        for index, passed in enumerate(run_error(name, source_text, markers)):
            checks.append((f"{TARGETS[index]} {name}", passed))

    print("== P19-B ordering, duplicate-name, callback lifetime, and rejection boundaries ==")
    for name, (source_text, expected, markers) in ERROR_CASES.items():
        absent = ("MUST-NOT-RUN",) if "MUST-NOT-RUN" in source_text else ()
        for index, passed in enumerate(run_error(name, source_text, markers, expected, absent)):
            checks.append((f"{TARGETS[index]} {name}", passed))

    print("== P19-B Java host and builtin named-call boundaries ==")
    checks.extend((f"Java host reject {i + 1}", passed)
                  for i, passed in enumerate(test_java_builtin_and_static_rejection()))

    checks.append(("P19 protocol suite", test_parent_protocol_suite()))
    passed = sum(ok for _, ok in checks)
    failed = len(checks) - passed
    print(f"P19-B named-host tests: {passed}/{len(checks)} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
