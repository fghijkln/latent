#!/usr/bin/env python3
"""Focused dual-target tests for P19-C borrowed callback keyword arguments."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))
from tests import test_p18_callbacks as p18

TARGETS = ("py", "java")

POSITIVE_CASES = {
    "host_keyword_callback": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        '    return "keyword:" + value\n'
        "say host.call_keyword(callback)\n",
        "keyword:keyword\n",
    ),
    "callback_star_kwargs": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        "say host.call_with_star_kwargs(callback)\n",
        "star-kwarg\n",
    ),
    "callback_ordered_mixed_and_extra_kwargs": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value, *rest, flag=false, **extra):\n"
        "    names = keys(extra)\n"
        '    if value == "head" and len(rest) == 1 and rest[0] == "tail" and flag == true and names[0] == "z" and names[1] == "if" and names[2] == "雪" and extra["z"] == 1 and extra["if"] == 2 and extra["雪"] == 3:\n'
        '        return "ordered-kwargs-ok"\n'
        '    return "bad-kwargs"\n'
        "say host.call_ordered_kwargs(callback)\n",
        "ordered-kwargs-ok\n",
    ),
    "callback_default_and_explicit_nil": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value=7, *, flag):\n"
        '    if value == 7:\n'
        '        return "default"\n'
        "    if value == nil:\n"
        '        return "explicit-nil"\n'
        '    return "unexpected"\n'
        "say host.call_keyword_defaults(callback)\n",
        '["default", "explicit-nil"]\n',
    ),
    "callback_kwargs_value_conversion": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(flag, missing, number, negative_zero, nested, opaque):\n"
        '    return [flag == true, missing == nil, number + 0.5, negative_zero, nested[1]["deep"][0], opaque.label()]\n'
        "say host.call_keyword_conversion(callback)\n",
        "callback-kwargs-conversion-ok\n",
    ),
}

ERROR_CASES = {
    "callback_positional_keyword_conflict": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        '    say "MUST-NOT-RUN"\n'
        "    return value\n"
        "say host.call_conflicting_kwargs(callback)\n",
        ("ArgumentError", "multiple values for argument 'value'",
         "callback_positional_keyword_conflict.lt:5"),
    ),
    "callback_unknown_keyword": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        '    say "MUST-NOT-RUN"\n'
        "    return value\n"
        "say host.call_unexpected_kwargs(callback)\n",
        ("ArgumentError", "unexpected named argument 'other'",
         "callback_unknown_keyword.lt:5"),
    ),
    "callback_missing_keyword_only": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value, *, flag):\n"
        '    say "MUST-NOT-RUN"\n'
        "    return value\n"
        "say host.call_missing_keyword_only(callback)\n",
        ("ArgumentError", "missing required keyword-only argument 'flag'",
         "callback_missing_keyword_only.lt:5"),
    ),
    "callback_duplicate_python_keyword_merge": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        '    say "MUST-NOT-RUN"\n'
        "    return value\n"
        "say host.call_duplicate_keyword_merge(callback)\n",
        ("TypeError", "value", "callback_duplicate_python_keyword_merge.lt:5"),
    ),
    "callback_kwargs_body_error_location": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        '    if value == "keyword":\n'
        '        throw "callback-kwargs-body"\n'
        "    return value\n"
        "say host.call_keyword(callback)\n",
        ("callback-kwargs-body", "callback_kwargs_body_error_location.lt:4",
         "callback_kwargs_body_error_location.lt:6"),
    ),
}


def run_source(name: str, source_text: str, target: str):
    with tempfile.TemporaryDirectory(prefix="latent-p19-callback-kwargs-") as tmp:
        source = Path(tmp) / (name + ".lt")
        source.write_text(source_text, encoding="utf-8")
        outdir = Path(tmp) / ("out-" + target)
        outdir.mkdir()
        return p18.compile_and_run(source, target, outdir)


def test_java_callback_frame_schema() -> bool:
    source_text = (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        '    say "CALLBACK_BODY_RAN"\n'
        '    return "callback-ok"\n'
        "say host.invoke_one(callback)\n"
    )
    faults = {
        "kwargs-null": "invalid callback kwargs schema",
        "kwargs-pair": "invalid callback kwargs pair",
        "kwargs-name": "invalid or duplicate callback kwargs name",
        "kwargs-duplicate": "invalid or duplicate callback kwargs name",
        "kwargs-value": "invalid callback value envelope",
        "kwargs-descriptor": "invalid callback value envelope",
        "extra-field": "invalid callback schema",
    }
    with tempfile.TemporaryDirectory(prefix="latent-p19-callback-wire-") as tmp:
        base = Path(tmp)
        source = base / "callback_frame.lt"
        source.write_text(source_text, encoding="utf-8")
        outdir = base / "out"
        compiled = p18.compile_java_fixture(source, outdir)
        if compiled.returncode != 0:
            print("FAIL Java callback frame fixture compile")
            print(f"  stderr={compiled.stderr[-1200:]!r}")
            return False

        daemon = base / "fault_daemon.py"
        daemon.write_text(
            "import os\nfrom pathlib import Path\n"
            "ns = {'__name__': 'p19_callback_fault_daemon'}\n"
            f"source = Path({str(ROOT / 'runtime' / 'ltpy.py')!r})\n"
            "exec(compile(source.read_text(), str(source), 'exec'), ns)\n"
            "original = ns['_protocol_send']\nfired = False\n"
            "fault = os.environ.get('P19_CALLBACK_FAULT', '')\n"
            "def send(frame):\n"
            "    global fired\n"
            "    if not fired and frame.get('kind') == 'callback':\n"
            "        fired = True\n"
            "        if fault == 'legacy-no-kwargs': frame.pop('kwargs')\n"
            "        elif fault == 'kwargs-null': frame['kwargs'] = None\n"
            "        elif fault == 'kwargs-pair': frame['kwargs'] = [['value']]\n"
            "        elif fault == 'kwargs-name': frame['kwargs'] = [[1, 'bad']]\n"
            "        elif fault == 'kwargs-duplicate': frame['kwargs'] = [['value', 1], ['value', 2]]\n"
            "        elif fault == 'kwargs-value': frame['kwargs'] = [['value', {'unknown': 'bad'}]]\n"
            "        elif fault == 'kwargs-descriptor': frame['kwargs'] = [['value', {'__lt_callback__': 7}]]\n"
            "        elif fault == 'extra-field': frame['surprise'] = True\n"
            "    original(frame)\n"
            "ns['_protocol_send'] = send\n"
            "ns['main']()\n",
            encoding="utf-8",
        )

        legacy_env = dict(p18.ENV, LATENT_PY=str(daemon),
                          P19_CALLBACK_FAULT="legacy-no-kwargs")
        legacy = p18.run(["java", "-cp", str(outdir),
                          p18.java_class_name(source.stem)], cwd=outdir,
                         timeout=20, env=legacy_env)
        legacy_ok = (legacy.returncode == 0 and
                     legacy.stdout == "CALLBACK_BODY_RAN\ncallback-ok\n" and
                     legacy.stderr == "")
        if legacy_ok:
            print("PASS Java legacy callback frame without kwargs")
        else:
            print(f"FAIL Java legacy callback frame: rc={legacy.returncode}")
            print(f"  stdout={legacy.stdout[-800:]!r}")
            print(f"  stderr={legacy.stderr[-1200:]!r}")

        results = [legacy_ok]
        for fault, marker in faults.items():
            env = dict(p18.ENV, LATENT_PY=str(daemon),
                       P19_CALLBACK_FAULT=fault)
            result = p18.run(["java", "-cp", str(outdir),
                              p18.java_class_name(source.stem)], cwd=outdir,
                             timeout=20, env=env)
            ok = (result.returncode not in (0, "timeout") and
                  marker in result.stderr and
                  "CALLBACK_BODY_RAN" not in result.stdout)
            results.append(ok)
            if ok:
                print(f"PASS Java malformed callback frame {fault}")
            else:
                print(f"FAIL Java malformed callback frame {fault}: rc={result.returncode}")
                print(f"  stdout={result.stdout[-800:]!r}")
                print(f"  stderr={result.stderr[-1200:]!r}")
        return all(results)


def main() -> int:
    checks = []
    print("== P19-C callback kwargs: same .lt on Python and Java targets ==")
    for name, (source_text, expected) in POSITIVE_CASES.items():
        for target in TARGETS:
            result = run_source(name, source_text, target)
            ok = (result.returncode == 0 and result.stdout == expected and
                  result.stderr == "")
            checks.append(ok)
            if ok:
                print(f"PASS {target} {name}")
            else:
                print(f"FAIL {target} {name}: rc={result.returncode}")
                print(f"  stdout={result.stdout[-900:]!r}")
                print(f"  stderr={result.stderr[-1400:]!r}")

    print("== P19-C callback binder, host binder, body, and source-map errors ==")
    for name, (source_text, markers) in ERROR_CASES.items():
        for target in TARGETS:
            result = run_source(name, source_text, target)
            diagnostics = result.stderr + result.stdout
            ok = (result.returncode not in (0, "timeout") and
                  p18.has_all(diagnostics, markers) and
                  "MUST-NOT-RUN" not in diagnostics)
            checks.append(ok)
            if ok:
                print(f"PASS {target} {name}")
            else:
                print(f"FAIL {target} {name}: rc={result.returncode}")
                print(f"  stdout={result.stdout[-900:]!r}")
                print(f"  stderr={result.stderr[-1400:]!r}")

    print("== P19-C Java callback-frame exact schema and codec validation ==")
    checks.append(test_java_callback_frame_schema())

    passed = sum(checks)
    failed = len(checks) - passed
    print(f"P19-C callback kwargs tests: {passed}/{len(checks)} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
