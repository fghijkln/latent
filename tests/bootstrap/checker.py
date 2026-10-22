#!/usr/bin/env python3
"""Validate the Python-target test projection ledger without importing tests."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("manifest.json")


def _case_constant(name: str) -> bool:
    return (name in {"POSITIVE", "MODULE_POSITIVE", "COOKBOOK"} or
            name.startswith("NEG_") or name.endswith("_COMPILE") or
            "_NEG_" in name or name.endswith("_MARKERS") or
            name.endswith("_LINES") or name.endswith("_STDOUT"))


def _literal_case_refs(tree: ast.Module) -> dict[str, list[str]]:
    """Extract static case names from runner declarations; never import runner."""
    refs: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets
                       if isinstance(target, ast.Name)]
            for name in targets:
                if not _case_constant(name):
                    continue
                values: set[str] = set()
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    values.update(item.value for item in node.value.elts
                                  if isinstance(item, ast.Constant) and
                                  isinstance(item.value, str))
                elif isinstance(node.value, ast.Dict):
                    values.update(key.value for key in node.value.keys
                                  if isinstance(key, ast.Constant) and
                                  isinstance(key.value, str))
                if values:
                    refs.setdefault(name, set()).update(values)
        elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and
              isinstance(node.value.func, ast.Attribute) and
              node.value.func.attr == "append" and
              isinstance(node.value.func.value, ast.Name) and
              _case_constant(node.value.func.value.id) and node.value.args and
              isinstance(node.value.args[0], ast.Constant) and
              isinstance(node.value.args[0].value, str)):
            refs.setdefault(node.value.func.value.id, set()).add(
                node.value.args[0].value)
    return {name: sorted(values) for name, values in sorted(refs.items())}


def _string_constants(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def _inventory(root: Path, directory: str, suffix: str) -> list[str]:
    base = root / directory
    return sorted(path.relative_to(base).as_posix()
                  for path in base.rglob("*" + suffix) if path.is_file())


def _test_modules(root: Path) -> list[str]:
    base = root / "tests"
    return sorted(path.relative_to(base).as_posix()
                  for path in base.rglob("test_*.py") if path.is_file())


def compare(manifest: dict, observed_refs: dict[str, list[str]],
            inventories: dict[str, list[str]],
            runner_auxiliary_paths: list[str] | None = None,
            protocol_operations: list[str] | None = None,
            root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    recorded_refs = manifest.get("runner_references", {})
    for name in sorted(set(observed_refs) | set(recorded_refs)):
        observed = set(observed_refs.get(name, []))
        recorded = set(recorded_refs.get(name, []))
        for item in sorted(observed - recorded):
            errors.append(f"unclassified runner item {name}: {item}")
        for item in sorted(recorded - observed):
            errors.append(f"stale runner ledger item {name}: {item}")

    negative_callbacks = {"p14_negative/module_callback",
                          "p14_negative/static_callback"}
    observed_positive = set(observed_refs.get("POSITIVE", []))
    observed_runtime_negative = set(observed_refs.get("NEG_RUNTIME", []))
    if "p14_negative/static_callback" in observed_positive:
        errors.append("p14_negative/static_callback must remain a runtime-negative Java-host case")
    for item in sorted(negative_callbacks - observed_runtime_negative):
        errors.append(f"P14 callback boundary must remain in NEG_RUNTIME: {item}")

    cases = manifest.get("runner_cases", {})
    all_cases = {item for values in observed_refs.values() for item in values}
    deltas = manifest.get("p18_stage0_deltas", {})
    permitted_deltas = {"p14_negative/module_callback"}
    for item in sorted(set(deltas) - permitted_deltas):
        errors.append(f"P18 Stage0 behavior delta is not permitted: {item}")
    for item in sorted(permitted_deltas - set(deltas)):
        errors.append(f"required P18 Stage0 behavior delta is missing: {item}")
    for item, delta in deltas.items():
        if item == "p14_negative/static_callback":
            errors.append("p14_negative/static_callback must retain strict Stage0 comparison")
        decision = cases.get(item)
        if decision is None:
            errors.append(f"P18 Stage0 behavior delta has no runner case: {item}")
        elif decision.get("disposition") != "include":
            errors.append(f"P18 Stage0 behavior delta must be an included runner case: {item}")
        if not isinstance(delta, dict):
            errors.append(f"P18 Stage0 behavior delta is not an object: {item}")
            continue
        reason = delta.get("reason", "")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"P18 Stage0 behavior delta has no reason: {item}")
        legacy = delta.get("fixed_stage0", {})
        if (legacy.get("phase") != "run" or
                not isinstance(legacy.get("rc"), int) or
                isinstance(legacy.get("rc"), bool) or legacy.get("rc") == 0 or
                not isinstance(legacy.get("stdout"), str) or
                not isinstance(legacy.get("stderr_contains"), str) or
                not legacy.get("stderr_contains", "").strip()):
            errors.append(f"P18 Stage0 behavior delta lacks an explicit fixed-Stage0 rejection: {item}")
        current = delta.get("stage1_stage2", {})
        if (current.get("phase") != "run" or
                not isinstance(current.get("rc"), int) or
                isinstance(current.get("rc"), bool) or
                not isinstance(current.get("stdout"), str) or
                not isinstance(current.get("stderr"), str)):
            errors.append(f"P18 Stage0 behavior delta lacks an exact Stage1/2 outcome: {item}")
        for label, expectation in (("fixed_stage0", legacy),
                                   ("stage1_stage2", current)):
            if ("output_exists" in expectation and
                    not isinstance(expectation["output_exists"], bool)):
                errors.append(
                    f"P18 Stage0 behavior delta has invalid {label} output expectation: {item}")
    p19_deltas = manifest.get("p19_stage0_deltas", {})
    permitted_p19_deltas = {"p13_negative/python_named",
                            "p14_negative/module_named",
                            "p8_negative/python_interop"}
    for item in sorted(set(p19_deltas) - permitted_p19_deltas):
        errors.append(f"P19 Stage0 behavior delta is not permitted: {item}")
    for item in sorted(permitted_p19_deltas - set(p19_deltas)):
        errors.append(f"required P19 Stage0 behavior delta is missing: {item}")
    p19_stage0_diagnostics = {
        "p13_negative/python_named": (
            "Latent runtime error: ArgumentError: named arguments are not supported "
            "for Python/Java interop calls\nLatent traceback (most recent call last):\n"
            "  at p13_negative/python_named.lt:4 in main\n"),
        "p14_negative/module_named": (
            "Latent runtime error: ArgumentError: named arguments are not supported "
            "for Python/Java interop calls\nLatent traceback (most recent call last):\n"
            "  at p14_negative/module_named.lt:3 in main\n"),
        "p8_negative/python_interop": (
            "Latent runtime error: ArgumentError: named arguments are not supported "
            "for Python/Java interop calls\nLatent traceback (most recent call last):\n"
            "  at p8_negative/python_interop.lt:2 in main\n"),
    }
    p19_stage1_stage2_diagnostics = {
        "p8_negative/python_interop": (
            "Latent runtime error: TypeError: math.sqrt() takes no keyword arguments\n"
            "Latent traceback (most recent call last):\n"
            "  at p8_negative/python_interop.lt:2 in main\n"),
    }
    for item in sorted(p19_deltas):
        delta = p19_deltas[item]
        if item not in all_cases:
            errors.append(f"P19 Stage0 behavior delta has no runner case: {item}")
        if item not in observed_runtime_negative:
            errors.append(f"P19 Stage0 behavior delta must retain its runtime-negative runner ID: {item}")
        decision = cases.get(item)
        if decision is None or decision.get("disposition") != "include":
            errors.append(f"P19 Stage0 behavior delta must be an included runner case: {item}")
        if not isinstance(delta, dict):
            errors.append(f"P19 Stage0 behavior delta is not an object: {item}")
            continue
        if not isinstance(delta.get("reason"), str) or not delta["reason"].strip():
            errors.append(f"P19 Stage0 behavior delta has no reason: {item}")
        legacy = delta.get("fixed_stage0", {})
        if (not isinstance(legacy, dict) or legacy.get("phase") != "run" or
                legacy.get("rc") != 1 or isinstance(legacy.get("rc"), bool) or
                legacy.get("stdout") != "" or
                legacy.get("stderr") != p19_stage0_diagnostics.get(item) or
                legacy.get("output_exists") is not True):
            errors.append(f"P19 Stage0 behavior delta lacks the exact pinned Stage0 rejection and source line: {item}")
        current = delta.get("stage1_stage2", {})
        expected_current_rc = 1 if item == "p8_negative/python_interop" else 0
        expected_current_stderr = p19_stage1_stage2_diagnostics.get(item, "")
        if (not isinstance(current, dict) or current.get("phase") != "run" or
                current.get("rc") != expected_current_rc or
                isinstance(current.get("rc"), bool) or
                current.get("stdout") != "" or
                current.get("stderr") != expected_current_stderr or
                current.get("output_exists") is not True):
            errors.append(f"P19 Stage0 behavior delta lacks its exact Stage1/2 outcome: {item}")
    if "p14_negative/static_named" in p19_deltas:
        errors.append("p14_negative/static_named must retain strict Stage0 comparison")
    feature = manifest.get("p18_stage1_feature")
    if feature is not None:
        source = feature.get("source")
        expected = feature.get("expected", {})
        if not isinstance(source, str) or not source.strip():
            errors.append("P18 Stage1 feature has no source path")
        elif not (root / source).is_file():
            errors.append(f"P18 Stage1 feature source does not exist: {source}")
        if (expected.get("phase") != "run" or expected.get("rc") != 0 or
                not isinstance(expected.get("stdout"), str) or
                not isinstance(expected.get("stderr"), str)):
            errors.append("P18 Stage1 feature lacks an exact successful outcome")
    feature_cases = manifest.get("p18_stage1_cases", [])
    if not isinstance(feature_cases, list):
        errors.append("P18 Stage1 case inventory must be a list")
        feature_cases = []
    seen_feature_cases: set[str] = set()
    for case in feature_cases:
        if not isinstance(case, dict):
            errors.append("P18 Stage1 case inventory contains a non-object entry")
            continue
        name = case.get("name")
        source = case.get("source")
        expected = case.get("expected", {})
        if not isinstance(name, str) or not name.startswith("p18_"):
            errors.append(f"P18 Stage1 case has invalid name: {name!r}")
        elif name in seen_feature_cases:
            errors.append(f"duplicate P18 Stage1 case: {name}")
        else:
            seen_feature_cases.add(name)
        if not isinstance(source, str) or not source.strip():
            errors.append(f"P18 Stage1 case has no source: {name}")
        if (not isinstance(expected, dict) or expected.get("phase") != "run" or
                not isinstance(expected.get("rc"), int) or
                isinstance(expected.get("rc"), bool) or
                not isinstance(expected.get("stdout"), str)):
            errors.append(f"P18 Stage1 case lacks an explicit run outcome: {name}")
            continue
        markers = expected.get("stderr_contains", [])
        absent = expected.get("stderr_not_contains", [])
        if (expected["rc"] == 0 and not isinstance(expected.get("stderr"), str)):
            errors.append(f"successful P18 Stage1 case lacks exact stderr: {name}")
        exact_error = expected.get("stderr")
        has_exact_error = isinstance(exact_error, str) and bool(exact_error.strip())
        has_error_markers = (isinstance(markers, list) and bool(markers) and
                             all(isinstance(item, str) and item for item in markers))
        if expected["rc"] != 0 and not (has_exact_error or has_error_markers):
            errors.append(f"failing P18 Stage1 case lacks stderr markers: {name}")
        if (not isinstance(absent, list) or
                any(not isinstance(item, str) or not item for item in absent)):
            errors.append(f"P18 Stage1 case has invalid forbidden stderr markers: {name}")
    p19_features = manifest.get("p19_stage1_features")
    if not isinstance(p19_features, list) or not p19_features:
        errors.append("P19 Stage1 feature ledger must contain at least one sample")
        p19_features = []
    seen_p19_features: set[str] = set()
    for feature in p19_features:
        if not isinstance(feature, dict):
            errors.append("P19 Stage1 feature ledger contains a non-object entry")
            continue
        name = feature.get("name")
        source = feature.get("source")
        expected = feature.get("expected", {})
        if not isinstance(name, str) or not name.startswith("p19_"):
            errors.append(f"P19 Stage1 feature has invalid name: {name!r}")
        elif name in seen_p19_features:
            errors.append(f"duplicate P19 Stage1 feature: {name}")
        else:
            seen_p19_features.add(name)
        if not isinstance(source, str) or not source.startswith("tests/bootstrap/"):
            errors.append(f"P19 Stage1 feature must use a tests/bootstrap fixture: {name}")
        elif not (root / source).is_file():
            errors.append(f"P19 Stage1 feature source does not exist: {source}")
        if (not isinstance(expected, dict) or expected.get("phase") != "run" or
                expected.get("rc") != 0 or isinstance(expected.get("rc"), bool) or
                not isinstance(expected.get("stdout"), str) or
                expected.get("stderr") != ""):
            errors.append(f"P19 Stage1 feature must pin successful output and empty stderr: {name}")
    callback_arity = next((case for case in feature_cases
                           if isinstance(case, dict) and
                           case.get("name") == "p18_callback_wrong_arity"), None)
    if callback_arity is None:
        errors.append("P18 Stage1 cases must pin the callback positional-arity boundary")
    else:
        source = callback_arity.get("source", "")
        expected = callback_arity.get("expected", {})
        stderr = expected.get("stderr", "") if isinstance(expected, dict) else ""
        if "host.invoke_two(callback)" not in source:
            errors.append("P18 callback positional-arity case must call the host with two actual arguments")
        if (not isinstance(stderr, str) or
                "callback() takes 1 args, got 2" not in stderr or
                "at p18_callback_wrong_arity.lt:4 in main" not in stderr):
            errors.append("P18 callback positional-arity case must pin its exact source-located binder error")
    for item in sorted(all_cases - set(cases)):
        errors.append(f"runner case has no Python-target decision: {item}")
    for item in sorted(set(cases) - all_cases):
        errors.append(f"stale Python-target runner case: {item}")
    for item in sorted(all_cases & set(cases)):
        decision = cases[item]
        disposition = decision.get("disposition")
        reason = decision.get("reason", "").strip()
        if disposition not in {"include", "exclude"} or not reason:
            errors.append(f"invalid decision or missing reason for runner case: {item}")
        if disposition == "exclude" and not re.search(
                r"AST|editor|Java-only", reason, re.IGNORECASE):
            errors.append(f"runner exclusion must state AST/editor or Java-only reason: {item}")

    recorded_inventories = manifest.get("inventories", {})
    decisions = manifest.get("fixture_decisions", {})
    for label, observed in inventories.items():
        expected = set(recorded_inventories.get(label, []))
        actual = set(observed)
        for item in sorted(actual - expected):
            errors.append(f"unclassified {label} path: {item}")
        for item in sorted(expected - actual):
            errors.append(f"stale {label} path: {item}")
        for item in sorted(actual & expected):
            record = decisions.get(f"{label}/{item}", {})
            if record.get("disposition") not in {"include", "exclude"}:
                errors.append(f"{label} path has no include/exclude decision: {item}")
            elif record["disposition"] == "exclude" and not record.get("reason", "").strip():
                errors.append(f"excluded {label} path has no reason: {item}")
            elif record["disposition"] == "exclude" and not re.search(
                    r"AST|editor|Java-only", record.get("reason", ""), re.IGNORECASE):
                errors.append(f"excluded {label} path must state AST/editor or Java-only reason: {item}")

    expected_decision_paths = {
        f"{label}/{item}" for label, items in recorded_inventories.items()
        for item in items
    }
    for key in sorted(set(decisions) - expected_decision_paths):
        errors.append(f"stale fixture decision: {key}")

    for name, decision in manifest.get("auxiliary_tests", {}).items():
        if decision.get("disposition") not in {"include", "exclude"}:
            errors.append(f"auxiliary test has no include/exclude decision: {name}")
        if not decision.get("reason", "").strip():
            errors.append(f"auxiliary test decision has no reason: {name}")
    for item in manifest.get("auxiliary_tests", {}):
        if item.startswith("test_"):
            path = root / "tests" / item
        elif item.startswith("python/"):
            path = root / "tests" / item
        else:
            path = root / item
        if not path.is_file():
            errors.append(f"auxiliary test/support path does not exist: {item}")
    expected_aux = set(manifest.get("runner_auxiliary_paths", []))
    actual_aux = set(runner_auxiliary_paths or [])
    for item in sorted(actual_aux - expected_aux):
        errors.append(f"unclassified runner auxiliary test path: {item}")
    for item in sorted(expected_aux - actual_aux):
        errors.append(f"stale runner auxiliary test path: {item}")
    expected_protocol = set(manifest.get("p14_protocol_operations", {}))
    actual_protocol = set(protocol_operations or [])
    for item in sorted(actual_protocol - expected_protocol):
        errors.append(f"unclassified P14 protocol operation: {item}")
    for item in sorted(expected_protocol - actual_protocol):
        errors.append(f"stale P14 protocol operation: {item}")
    for operation, decision in manifest.get("p14_protocol_operations", {}).items():
        if decision.get("disposition") not in {"include", "exclude"} or not decision.get("reason", "").strip():
            errors.append(f"P14 protocol operation lacks a disposition/reason: {operation}")
        elif decision["disposition"] == "exclude" and not re.search(
                r"AST|editor|Java-only", decision["reason"], re.IGNORECASE):
            errors.append(f"excluded P14 protocol operation must state AST/editor or Java-only reason: {operation}")
    return errors


def audit(root: Path = ROOT, manifest_path: Path = MANIFEST,
          injected_refs: dict[str, list[str]] | None = None) -> list[str]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tree = ast.parse((root / "tests" / "run_tests.py").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, SyntaxError) as exc:
        return [f"cannot read ledger inputs: {exc}"]
    refs = _literal_case_refs(tree)
    runner_aux = sorted({node.value for node in ast.walk(tree)
                         if isinstance(node, ast.Constant) and
                         isinstance(node.value, str) and
                         re.fullmatch(r"test_[A-Za-z0-9_]+\.py", node.value)})
    protocol_constants = _string_constants(root / "tests" / "test_p14_protocol.py")
    operations = set()
    for value in protocol_constants:
        for line in value.splitlines():
            if not line.startswith("{"):
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(request, dict) and isinstance(request.get("op"), str):
                operations.add(request["op"])
    operations = sorted(operations)
    if injected_refs:
        refs = {key: list(values) for key, values in refs.items()}
        for name, values in injected_refs.items():
            refs.setdefault(name, []).extend(values)
    inventories = {
        "tests_lt": _inventory(root, "tests", ".lt"),
        "cookbook_lt": _inventory(root, "cookbook", ".lt"),
        "java_fixture": _inventory(root, "tests/java", ".java"),
        "python_fixture": _inventory(root, "tests/python", ".py"),
        "test_module": _test_modules(root),
    }
    return compare(manifest, refs, inventories, runner_aux, operations, root)


def main() -> int:
    errors = audit()
    if errors:
        print("Python-target test projection ledger: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = manifest["runner_cases"]
    included = sum(row["disposition"] == "include" for row in cases.values())
    excluded = len(cases) - included
    print(f"Python-target test projection ledger: PASS "
          f"({included} included runner cases, {excluded} excluded runner cases; "
          f"{sum(len(v) for v in manifest['inventories'].values())} inventoried paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
