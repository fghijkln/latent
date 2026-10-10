#!/usr/bin/env python3
"""Bounded, reproducible property checks across Latent's Python and Java backends."""
import argparse
from dataclasses import dataclass
import json
import math
import os
import random
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LATENTC = os.path.join(ROOT, "latent.py")
DEFAULT_SEED = 0x5EED2026
NUMBER_PAIRS = 21  # includes five fixed edge pairs and sixteen seeded pairs
RANDOM_STRINGS = 8
RANDOM_LISTS = 8
COMPILE_LOCALIZE_GROUP_SIZE = 32
COMPILE_LOCALIZE_MAX_PROBES = 64


@dataclass(frozen=True)
class Case:
    index: int
    category: str
    label: str
    source: str
    expected: list


@dataclass(frozen=True)
class BackendResult:
    phase: str
    exit_code: object
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Failure:
    case: object
    detail: str
    source: str
    source_kind: str


def latent_string(value):
    """Use JSON's compatible quoted-string escapes for Latent source literals."""
    return json.dumps(value, ensure_ascii=False)


def numeric_literal(value):
    # All generated inputs are quarter steps; fixed decimals preserve that exactly
    # and avoid exponent notation, which the Latent lexer does not currently accept.
    return f"{value:.2f}"


def add_case(cases, category, label, expression, expected, setup="", index_error=None):
    index = len(cases)
    label_source = latent_string(label)
    if index_error is None:
        statement = (f'{setup}say [{index}, {label_source}, "ok", '
                     f"{expression}]\n")
        expected_row = [index, label, "ok", expected]
    else:
        statement = (
            f"{setup}try:\n"
            f'    say [{index}, {label_source}, "ok", {expression}]\n'
            "catch e:\n"
            f'    say [{index}, {label_source}, "error", e]\n'
        )
        expected_row = [index, label, "error", index_error]
    cases.append(Case(index, category, label, statement, expected_row))


def add_numeric_cases(cases, rng):
    pairs = [(-0.0, 1.0), (0.0, -1.0), (1.25, -2.75),
             (-999.75, 0.5), (1024.25, -16.0)]
    divisors = (-4.0, -2.0, -1.0, -0.5, 0.5, 1.0, 2.0, 4.0)
    while len(pairs) < NUMBER_PAIRS:
        x = rng.randint(-4000, 4000) / 4.0
        y = rng.choice(divisors)
        pairs.append((x, y))

    for pair_index, (x, y) in enumerate(pairs):
        xs, ys = numeric_literal(x), numeric_literal(y)
        left, right = f"({xs})", f"({ys})"
        operations = (
            ("add", f"{left} + {right}", x + y),
            ("subtract", f"{left} - {right}", x - y),
            ("multiply", f"{left} * {right}", x * y),
            ("divide", f"{left} / {right}", x / y),
            ("square", f"{left} ** 2", x * x),
            ("int_truncate", f"int({left})", float(math.trunc(x))),
            ("less_than", f"{left} < {right}", x < y),
            ("less_equal", f"{left} <= {right}", x <= y),
            ("equal", f"{left} == {right}", x == y),
            ("not_equal", f"{left} != {right}", x != y),
            ("add_zero_identity", f"{left} + 0.0 == {left}", x + 0.0 == x),
            ("multiply_one_identity", f"{left} * 1.0 == {left}", x * 1.0 == x),
        )
        for name, expression, expected in operations:
            add_case(cases, "number", f"numbers[{pair_index}].{name}",
                     expression, expected)


def add_index_case(cases, category, label, variable, literal, index, expected,
                   should_error=False):
    index_source = str(index)
    expression = f"{variable}[{index_source}]"
    error_message = f"index out of range: {index}" if should_error else None
    add_case(cases, category, label, expression, expected,
             setup=f"{variable} = {literal}\n", index_error=error_message)


def boundary_indices(length, rng):
    if length:
        candidates = [-length, -1, 0, length - 1,
                      rng.randint(-length, length - 1)]
    else:
        candidates = []
    valid = list(dict.fromkeys(candidates))
    invalid = list(dict.fromkeys((-length - 1, length)))
    return valid, invalid


def add_string_cases(cases, rng):
    edges = ["", "A", "😀", "e\u0301", "A😀B", "中𝄞🚀",
             "👩\u200d🚀", "✈️"]
    alphabet = ["a", "Z", "é", "中", "😀", "🚀", "𝄞",
                "\u0301", "\ufe0f", "\u200d"]
    samples = [(f"edge-{i}", value) for i, value in enumerate(edges)]
    for i in range(RANDOM_STRINGS):
        count = rng.randint(0, 10)
        value = "".join(rng.choice(alphabet) for _ in range(count))
        samples.append((f"seeded-{i}", value))

    for sample_name, value in samples:
        literal = latent_string(value)
        setup = f"text = {literal}\n"
        add_case(cases, "unicode", f"unicode.{sample_name}.length",
                 "len(text)", len(value), setup=setup)
        add_case(cases, "unicode", f"unicode.{sample_name}.iterate",
                 "chars", list(value),
                 setup=(setup + "chars = []\nfor ch in text:\n"
                        "    push(chars, ch)\n"))
        valid, invalid = boundary_indices(len(value), rng)
        for index in valid:
            add_index_case(cases, "unicode",
                           f"unicode.{sample_name}.index[{index}]", "text",
                           literal, index, value[index])
        for index in invalid:
            add_index_case(cases, "unicode",
                           f"unicode.{sample_name}.index[{index}]", "text",
                           literal, index, None, should_error=True)


def add_list_cases(cases, rng):
    samples = [[], [7.0], [-1.25, 0.0, 2.5],
               [float(i) for i in range(7)]]
    for _ in range(RANDOM_LISTS):
        count = rng.randint(0, 9)
        samples.append([rng.randint(-500, 500) / 4.0 for _ in range(count)])

    for sample_index, values in enumerate(samples):
        literal = "[" + ", ".join(numeric_literal(v) for v in values) + "]"
        valid, invalid = boundary_indices(len(values), rng)
        for index in valid:
            add_index_case(cases, "index", f"list[{sample_index}].index[{index}]",
                           "items", literal, index, values[index])
        for index in invalid:
            add_index_case(cases, "index", f"list[{sample_index}].index[{index}]",
                           "items", literal, index, None, should_error=True)


def generate_cases(seed):
    rng = random.Random(seed)
    cases = []
    add_numeric_cases(cases, rng)
    add_string_cases(cases, rng)
    add_list_cases(cases, rng)
    return cases


def program_source(cases, seed):
    body = "\n".join(case.source.rstrip("\n") for case in cases)
    return f"# bounded differential properties; seed={seed}\n{body}\n"


def compile_backend(source_path, outdir, backend):
    os.makedirs(outdir, exist_ok=True)
    return _run([sys.executable, LATENTC, source_path, "-t", backend,
                 "-o", outdir], ROOT)


def run_backend(source_path, outdir, backend):
    compile_result = compile_backend(source_path, outdir, backend)
    if compile_result[0] != 0:
        return BackendResult("compile", *compile_result)
    stem = os.path.splitext(os.path.basename(source_path))[0]
    if backend == "py":
        command = [sys.executable, os.path.join(outdir, stem + ".py")]
    else:
        class_name = stem[0].upper() + stem[1:]
        command = ["java", "-cp", outdir, class_name]
    return BackendResult("run", *_run(command, outdir))


def _run(command, cwd):
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", timeout=90, check=False)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        return "timeout", exc.stdout or "", exc.stderr or ""
    except OSError as exc:
        return "error", "", str(exc)


def records_from(result):
    records = {}
    parse_error = None
    parse_error_line = None
    for line_number, line in enumerate(result.stdout.splitlines(), 1):
        try:
            row = json.loads(line)
            if not isinstance(row, list) or not row:
                raise ValueError("expected a non-empty JSON array")
            index = int(row[0])
            if index in records:
                raise ValueError(f"duplicate case id {index}")
            records[index] = row
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            parse_error = f"stdout line {line_number}: {exc}; content={line!r}"
            parse_error_line = line_number
            break
    return records, parse_error, parse_error_line, result.exit_code, result.stderr


def json_equivalent(actual, expected):
    """Compare JSON values strictly, except integral floats print as integers."""
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual == expected
    if isinstance(expected, (int, float)):
        return (isinstance(actual, (int, float)) and
                not isinstance(actual, bool) and actual == expected)
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected) and
                all(json_equivalent(a, e) for a, e in zip(actual, expected)))
    return type(actual) is type(expected) and actual == expected


def run_case(source, tempdir, tag):
    source_path = os.path.join(tempdir, tag + ".lt")
    with open(source_path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(source)
    results = {}
    for backend in ("py", "java"):
        outdir = os.path.join(tempdir, tag + "_" + backend)
        results[backend] = run_backend(source_path, outdir, backend)
    return results


def is_environment_diagnostic(result):
    message = result.stdout + result.stderr
    return (result.exit_code in ("error", "timeout") or
            "javac not found" in message or
            "cannot start java" in message or
            "No such file or directory" in message)


class CompileProbe:
    """Compile only, with unique paths and a strict per-localization probe cap."""
    def __init__(self, tempdir, backend, seed, tag):
        self.tempdir = tempdir
        self.backend = backend
        self.seed = seed
        self.tag = tag
        self.calls = 0

    def compile(self, cases):
        if self.calls >= COMPILE_LOCALIZE_MAX_PROBES:
            return None, None
        probe_tag = f"{self.tag}_compile_probe_{self.calls:03d}"
        self.calls += 1
        source_path = os.path.join(self.tempdir, probe_tag + ".lt")
        with open(source_path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(program_source(cases, self.seed))
        result = compile_backend(
            source_path, os.path.join(self.tempdir, probe_tag + "_out"),
            self.backend)
        if is_environment_diagnostic(BackendResult("compile", *result)):
            return None, result
        return result[0] == 0, result


def localize_compile_failure(cases, backend, seed, tempdir, tag, original,
                             probe=None):
    """Find an isolated bad case, or return the smallest failing group found.

    The generated suite is searched in fixed-size ordered groups, then bisected.
    If failure depends on multiple cases, bounded single-case deletion minimizes
    that group. Environment/tool failures are never misattributed to a case.
    """
    if probe is None:
        probe = CompileProbe(tempdir, backend, seed, tag)
    selected = None
    last_failure = (original.stdout, original.stderr)
    for start in range(0, len(cases), COMPILE_LOCALIZE_GROUP_SIZE):
        group = cases[start:start + COMPILE_LOCALIZE_GROUP_SIZE]
        outcome, result = probe.compile(group)
        if outcome is False:
            selected = group
            last_failure = (result[1], result[2])
            break
        if outcome is None:
            break

    if selected is None:
        detail = (f"{backend} compiler failed, but no individual group could be "
                  f"isolated within {probe.calls} probes; compiler output: "
                  f"{(original.stdout + original.stderr)[-1500:]!r}")
        return None, program_source(cases, seed), detail

    while len(selected) > 1 and probe.calls < COMPILE_LOCALIZE_MAX_PROBES:
        middle = len(selected) // 2
        left, right = selected[:middle], selected[middle:]
        left_outcome, left_result = probe.compile(left)
        if left_outcome is False:
            selected = left
            last_failure = (left_result[1], left_result[2])
            continue
        if left_outcome is None:
            break

        right_outcome, right_result = probe.compile(right)
        if right_outcome is False:
            selected = right
            last_failure = (right_result[1], right_result[2])
            continue
        if right_outcome is None:
            break

        # Neither half fails alone: reduce an interaction-dependent group by
        # trying to remove one case at a time, within the remaining probe budget.
        reduced = False
        for position in range(len(selected)):
            candidate = selected[:position] + selected[position + 1:]
            if not candidate or probe.calls >= COMPILE_LOCALIZE_MAX_PROBES:
                break
            outcome, result = probe.compile(candidate)
            if outcome is False:
                selected = candidate
                last_failure = (result[1], result[2])
                reduced = True
                break
            if outcome is None:
                break
        if not reduced:
            break

    if len(selected) == 1:
        case = selected[0]
        detail = (f"{backend} compile failure localized to generated case "
                  f"{case.index} ({case.label}); isolated compiler output: "
                  f"{(last_failure[0] + last_failure[1])[-1500:]!r}")
    else:
        case = None
        indices = [item.index for item in selected]
        detail = (f"{backend} compile failure minimized to interacting cases "
                  f"{indices}; isolated compiler output: "
                  f"{(last_failure[0] + last_failure[1])[-1500:]!r}")
    return case, program_source(selected, seed), detail


def minimized_case_source(case, seed):
    return f"# minimized case; seed={seed}; case={case.index}\n{case.source}"


def find_failure(cases, results, seed, tempdir, tag):
    # Handle compiler failures before parsing stdout: a failed compilation has no
    # output rows, so it must never be mistaken for the first generated case.
    for backend in ("py", "java"):
        result = results[backend]
        if result.phase == "compile" and result.exit_code != 0:
            case, source, detail = localize_compile_failure(
                cases, backend, seed, tempdir, tag + "_" + backend, result)
            source_kind = ("case" if case is not None else
                           "batch" if "no individual group could be isolated" in detail
                           else "group")
            return Failure(case, detail, source, source_kind)

    parsed = {backend: records_from(result)
              for backend, result in results.items()}
    for position, case in enumerate(cases):
        expected = case.expected
        for backend in ("py", "java"):
            records, parse_error, parse_error_line, rc, error = parsed[backend]
            if parse_error_line == position + 1:
                detail = (f"{backend}: {parse_error}; output line "
                          f"{parse_error_line} maps to generated case {case.index}")
                return Failure(case, detail, minimized_case_source(case, seed),
                               "case")
            if case.index not in records:
                detail = (f"{backend}: missing output for case {case.index}; "
                          f"exit={rc}; stderr={error[-1000:]!r}")
                return Failure(case, detail, minimized_case_source(case, seed),
                               "case")
            if not json_equivalent(records[case.index], expected):
                detail = (f"{backend}: case {case.index} differed from oracle; "
                          f"actual={records[case.index]!r}, expected={expected!r}")
                return Failure(case, detail, minimized_case_source(case, seed),
                               "case")
        py_records = parsed["py"][0]
        java_records = parsed["java"][0]
        if not json_equivalent(py_records[case.index],
                               java_records[case.index]):
            detail = (f"backend mismatch: py={py_records[case.index]!r}, "
                      f"java={java_records[case.index]!r}")
            return Failure(case, detail, minimized_case_source(case, seed),
                           "case")
    for backend in ("py", "java"):
        records, parse_error, parse_error_line, rc, error = parsed[backend]
        if parse_error:
            detail = (f"{backend}: malformed extra output at line "
                      f"{parse_error_line}, beyond the generated cases: "
                      f"{parse_error}")
            return Failure(None, detail, program_source(cases, seed), "batch")
        if rc != 0 or error:
            case = cases[-1]
            detail = (f"{backend}: process exit={rc}; stderr={error[-1000:]!r}")
            return Failure(case, detail, minimized_case_source(case, seed), "case")
        if len(records) != len(cases):
            case = cases[-1]
            detail = (f"{backend}: expected {len(cases)} output rows, "
                      f"got {len(records)}")
            return Failure(case, detail, minimized_case_source(case, seed), "case")
    return None


def format_failure(seed, failure):
    case = failure.case
    if case is not None:
        location = f"case={case.index}, property={case.label}"
    elif failure.source_kind == "batch":
        location = "case=batch, property=stdout-output"
    else:
        location = "case=group, property=compile-error"
    reproduction_label = {
        "case": "minimal reproducer (.lt)",
        "group": "reduced case group (.lt)",
        "batch": "test batch input (.lt)",
    }[failure.source_kind]
    return (f"FAIL seeded cross-backend properties "
            f"(seed={seed} / 0x{seed:x}, {location})\n"
            f"  {failure.detail}\n"
            f"  {reproduction_label}:\n"
            "--- begin ---\n"
            f"{failure.source.rstrip()}\n"
            "--- end ---")


def compile_failure_localization_regression(seed, tempdir):
    marker = "__phase2_nonfirst_compile_regression_missing_name__"
    cases = [
        Case(0, "regression", "valid-before",
             'say [0, "valid-before", 1]\n', [0, "valid-before", "ok", 1]),
        Case(1, "regression", "invalid-second-case", f"say {marker}\n", []),
        Case(2, "regression", "valid-after",
             'say [2, "valid-after", 2]\n', [2, "valid-after", "ok", 2]),
    ]
    original_source = program_source(cases, seed)
    results = run_case(original_source, tempdir, "compile_localization_regression")
    actual_compile_failure = any(
        result.phase == "compile" and result.exit_code != 0
        for result in results.values())
    if not actual_compile_failure:
        report = (f"FAIL compile-error localization regression (seed={seed})\n"
                  f"expected a compile failure; source:\n{original_source}")
        return False, report

    failure = find_failure(
        cases, results, seed, tempdir, "compile_localization_regression")
    expected_source = program_source([cases[1]], seed)
    report = format_failure(seed, failure) if failure is not None else ""
    passed = (failure is not None and failure.case is not None and
              failure.case.index == 1 and failure.source == expected_source and
              f"seed={seed}" in report and marker in report and
              "valid-before" not in failure.source and
              "valid-after" not in failure.source)
    if not passed:
        return False, report
    return True, f"PASS compile-error localization regression (seed={seed}, case=1)"


def stdout_parse_failure_localization_regression(seed, tempdir):
    cases = [
        Case(0, "regression", "stdout-before",
             'say [0, "stdout-before", "ok", 0]\n',
             [0, "stdout-before", "ok", 0]),
        Case(1, "regression", "stdout-malformed-second",
             'say [1, "stdout-malformed-second", "ok", 1]\n',
             [1, "stdout-malformed-second", "ok", 1]),
        Case(2, "regression", "stdout-after",
             'say [2, "stdout-after", "ok", 2]\n',
             [2, "stdout-after", "ok", 2]),
    ]
    rows = [json.dumps(case.expected, ensure_ascii=False, separators=(",", ":"))
            for case in cases]
    py_stdout = f"{rows[0]}\n{{not-json\n{rows[2]}\n"
    java_stdout = "\n".join(rows) + "\n"
    results = {
        "py": BackendResult("run", 0, py_stdout, ""),
        "java": BackendResult("run", 0, java_stdout, ""),
    }
    failure = find_failure(cases, results, seed, tempdir,
                           "stdout_parse_localization_regression")
    report = format_failure(seed, failure) if failure is not None else ""
    batch_failure = Failure(
        None, "malformed extra output", program_source(cases, seed), "batch")
    batch_report = format_failure(seed, batch_failure)
    expected_source = minimized_case_source(cases[1], seed)
    passed = (failure is not None and failure.case is not None and
              failure.case.index == 1 and failure.source == expected_source and
              "stdout line 2" in report and f"seed={seed}" in report and
              "stdout-before" not in failure.source and
              "stdout-after" not in failure.source and
              "case=batch, property=stdout-output" in batch_report)
    if not passed:
        return False, report or f"FAIL stdout parse localization regression (seed={seed})"
    return True, f"PASS stdout-parse localization regression (seed={seed}, case=1)"


def reduced_case_group_diagnostic_regression(seed, tempdir):
    cases = [
        Case(0, "regression", "unrelated-before", 'say "before"\n', []),
        Case(1, "regression", "interaction-left", 'say "left"\n', []),
        Case(2, "regression", "interaction-right", 'say "right"\n', []),
        Case(3, "regression", "unrelated-after", 'say "after"\n', []),
    ]

    class PairOnlyFailureProbe:
        def __init__(self):
            self.calls = 0

        def compile(self, selected):
            self.calls += 1
            selected_ids = {case.index for case in selected}
            if {1, 2} <= selected_ids:
                return False, (1, "", "synthetic failure requires cases 1 and 2")
            return True, (0, "", "")

    original = BackendResult("compile", 1, "", "synthetic interaction failure")
    case, source, detail = localize_compile_failure(
        cases, "py", seed, tempdir, "group_diagnostic_regression", original,
        probe=PairOnlyFailureProbe())
    failure = Failure(case, detail, source, "group" if case is None else "case")
    report = format_failure(seed, failure)
    expected_source = program_source(cases[1:3], seed)
    passed = (case is None and source == expected_source and
              f"seed={seed}" in report and "cases [1, 2]" in report and
              "reduced case group (.lt)" in report and
              "minimal reproducer (.lt)" not in report and
              "unrelated-before" not in source and
              "unrelated-after" not in source)
    if not passed:
        return False, report
    return True, f"PASS reduced-case-group diagnostic regression (seed={seed}, cases=1,2)"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=lambda value: int(value, 0),
                        default=DEFAULT_SEED,
                        help="reproducible PRNG seed (decimal or 0x-prefixed)")
    args = parser.parse_args()
    cases = generate_cases(args.seed)
    if not cases:
        print("FAIL: no generated cases")
        return 1

    with tempfile.TemporaryDirectory(prefix="latent-properties-") as tempdir:
        regressions = (
            compile_failure_localization_regression(args.seed, tempdir),
            stdout_parse_failure_localization_regression(args.seed, tempdir),
            reduced_case_group_diagnostic_regression(args.seed, tempdir),
        )
        for regression_ok, regression_message in regressions:
            print(regression_message)
            if not regression_ok:
                return 1

        results = run_case(program_source(cases, args.seed), tempdir,
                           "property_cases")
        failure = find_failure(cases, results, args.seed, tempdir,
                               "property_failure")
        if failure is None:
            print(f"PASS seeded cross-backend properties "
                  f"(seed={args.seed} / 0x{args.seed:x}, cases={len(cases)})")
            return 0

        repro_results = run_case(failure.source, tempdir, "failure_repro")
        print(format_failure(args.seed, failure))
        for backend in ("py", "java"):
            result = repro_results[backend]
            print(f"  isolated {backend}: phase={result.phase}, "
                  f"exit={result.exit_code}, stdout={result.stdout!r}, "
                  f"stderr={result.stderr[-1000:]!r}")
        print("  rerun command: python3 tests/test_differential_properties.py "
              f"--seed {args.seed}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
