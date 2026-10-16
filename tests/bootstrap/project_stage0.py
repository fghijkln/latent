#!/usr/bin/env python3
"""Project every included Python-target runner case through Stage2 from fixed Stage0."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
COOKBOOK = ROOT / "cookbook"
MANIFEST = ROOT / "tests" / "bootstrap" / "manifest.json"
STAGE0_COMMIT = "9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9"
EXPECTED_PROJECTION = {
    "runner_case_ids": 243,
    "fixture_invocations": 244,
    "fixed_seed": 0x5EED2026,
    "fixed_seed_property_cases": 505,
}
sys.path.insert(0, str(ROOT))
from tests._safe_test_env import clean_test_environment, isolate_test_environment

CLEAN_ENV = isolate_test_environment()
MODULE_SYMBOL = re.compile(
    r"(?<![A-Za-z0-9])_ltm_(?:[0-9a-f]{8}|m[0-9]+)_([A-Za-z_][A-Za-z0-9_]*)"
)
MODULE_INIT_FRAME = re.compile(
    r"(?m)^(  at )([^:\n]+):(\d+) in _ltm_init_m[0-9]+$"
)
MISSING_MODULE = re.compile(
    r"cannot read module ['\"]([^'\"]+)['\"]: No such file or directory"
)

from tests.test_bootstrap_smoke import extract_stage0  # noqa: E402


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load test module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = load_module("p15_projection_run_tests", TESTS / "run_tests.py")
properties = load_module(
    "p15_projection_properties", TESTS / "test_differential_properties.py")


def command_result(command: list[str], *, cwd: Path,
                   env: dict[str, str] | None = None,
                   timeout: int = 90) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command, cwd=cwd,
            env=clean_test_environment() if env is None else env, text=True,
            encoding="utf-8", capture_output=True, timeout=timeout,
            check=False)
        return {"rc": result.returncode, "stdout": result.stdout,
                "stderr": result.stderr}
    except subprocess.TimeoutExpired as exc:
        def text(value):
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return value or ""
        return {"rc": "timeout", "stdout": text(exc.stdout),
                "stderr": text(exc.stderr)}
    except OSError as exc:
        return {"rc": "error", "stdout": "", "stderr": str(exc)}


def python_target_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    def add(name: str, kind: str, source_dir: Path,
            markers: tuple[str, ...] = ()) -> None:
        source_dir = source_dir.resolve()
        for current in cases:
            if current["name"] != name or current["source_dir"] != source_dir:
                continue
            if current["kind"] != kind:
                raise RuntimeError(f"case has conflicting runner groups: {name}")
            current["markers"] = sorted(set(current["markers"]) | set(markers))
            return
        cases.append({"name": name, "kind": kind, "source_dir": source_dir,
                      "markers": sorted(set(markers))})

    for name in runner.POSITIVE + runner.MODULE_POSITIVE:
        add(name, "positive", Path(runner.TESTS))
    for name in runner.COOKBOOK:
        add(name, "positive", Path(runner.COOKBOOK_DIR))

    def add_compile(names, marker_map=None, location_map=None):
        marker_map = marker_map or {}
        location_map = location_map or {}
        for name in names:
            markers = tuple(
                ([marker_map[name]] if name in marker_map else []) +
                list(location_map.get(name, ())))
            add(name, "compile-negative", Path(runner.TESTS), markers)

    add_compile(runner.NEG_COMPILE, runner.NONLOCAL_COMPILE_MARKERS)
    add_compile(runner.P8_NEG_COMPILE)
    add_compile(runner.P9_NEG_COMPILE)
    add_compile(runner.P10_NEG_COMPILE)
    add_compile(runner.P11_NEG_COMPILE)
    add_compile(runner.P12_NEG_COMPILE)
    add_compile(runner.NEG_INHERITANCE_COMPILE,
                runner.INHERITANCE_COMPILE_MARKERS,
                runner.INHERITANCE_ERROR_LOCATIONS)
    add_compile(runner.NEG_MODULE_COMPILE, runner.MODULE_COMPILE_MARKERS,
                runner.MODULE_ERROR_LOCATIONS)
    for name in runner.NEG_RUNTIME:
        add(name, "runtime-negative", Path(runner.TESTS))

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    included = {name for name, decision in manifest["runner_cases"].items()
                if decision["disposition"] == "include"}
    excluded = set(manifest["runner_cases"]) - included
    observed = {case["name"] for case in cases}
    kinds_by_name: dict[str, str] = {}
    for case in cases:
        previous = kinds_by_name.setdefault(case["name"], case["kind"])
        if previous != case["kind"]:
            raise RuntimeError(f"case has conflicting runner groups: {case['name']}")
    if observed != set(manifest["runner_cases"]):
        raise RuntimeError(
            "runner groups and ledger disagree: "
            f"missing={sorted(set(manifest['runner_cases']) - observed)}, "
            f"unledgered={sorted(observed - set(manifest['runner_cases']))}")
    return [case for case in cases if case["name"] in included]


def module_source_root(name: str, source_dir: Path) -> Path | None:
    if source_dir.resolve() == Path(runner.TESTS).resolve() and name.startswith("modules/"):
        return source_dir / "modules"
    return None


def diagnostic_root(name: str, source_dir: Path) -> Path | None:
    root = module_source_root(name, source_dir)
    if root is not None:
        return root
    return source_dir


def normalize_module_init_frames(text: str, name: str,
                                source_dir: Path) -> str:
    root = module_source_root(name, source_dir)
    if root is None:
        return text

    def replace(match: re.Match[str]) -> str:
        physical_path = (root / match.group(2)).resolve()
        digest = hashlib.sha1(str(physical_path).encode("utf-8")).hexdigest()[:8]
        return (f"{match.group(1)}{match.group(2)}:{match.group(3)} in "
                f"_lt_module_init_{digest}")

    return MODULE_INIT_FRAME.sub(replace, text)


def normalize_text(text: str, temporary: Path, name: str,
                   source_dir: Path) -> str:
    text = text.replace(str(temporary), "<TMP>")
    root = diagnostic_root(name, source_dir)
    if root is not None:
        text = text.replace(str(root.resolve()) + os.sep, "")
    # Stage0 reports the platform OSError text; the versioned host contract
    # returns the stable not-found code. Normalize only this exact file error.
    text = MISSING_MODULE.sub(r"cannot read module \1: not-found", text)
    # Module symbols are deliberately stage-local. Compare their source-level
    # suffix while retaining the identifier and all surrounding diagnostics.
    text = MODULE_SYMBOL.sub(r"_ltm_module_\1", text)
    return normalize_module_init_frames(text, name, source_dir)


def fixture_for(name: str) -> Path | None:
    if name == "phase1_semantics" or name.startswith("phase1_negative/"):
        return TESTS / "java" / "Stage1Overloads.java"
    if name == "p13_interop_bound_methods" or name.startswith("p13_negative/"):
        return TESTS / "java" / "P13InteropFixture.java"
    if (name in ("p14_module_and_static_values", "p14_static_method_extraction") or
            name.startswith("p14_negative/")):
        return TESTS / "java" / "P14InteropFixture.java"
    return None


def compile_and_run(label: str, compiler: Path, source: Path, name: str,
                    source_dir: Path, output: Path, temporary: Path) -> dict[str, Any]:
    command = [sys.executable, str(compiler), str(source)]
    if label == "stage0":
        command.extend(["-t", "py"])
    else:
        root = module_source_root(name, source_dir)
        if root is None:
            root = source_dir
        command.extend(["--source-root", str(root)])
    command.extend(["-o", str(output)])
    compiled = command_result(command, cwd=temporary)
    if compiled["rc"] != 0:
        return {"phase": "compile", **compiled,
                "output_exists": output.exists()}

    fixture = fixture_for(name)
    run_env = dict(CLEAN_ENV)
    if fixture is not None:
        fixture_result = command_result(
            ["javac", "-cp", str(output), "-d", str(output), str(fixture)],
            cwd=ROOT)
        if fixture_result["rc"] != 0:
            return {"phase": "fixture", **fixture_result,
                    "output_exists": output.exists()}
    if name == "p13_interop_bound_methods" or name.startswith("p13_negative/") or \
            name in ("p14_module_and_static_values", "p14_static_method_extraction") or \
            name.startswith("p14_negative/"):
        run_env["PYTHONPATH"] = str(TESTS / "python")

    cwd = Path(tempfile.gettempdir()) if runner.is_module_case(name, str(source_dir)) else ROOT
    program = output / f"{source.stem}.py"
    executed = command_result([sys.executable, str(program)], cwd=cwd,
                              env=run_env)
    return {"phase": "run", **executed,
            "output_exists": output.exists()}


def clear_output(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def stage_snapshot(directory: Path) -> tuple[dict[str, bytes], list[str]]:
    files: dict[str, bytes] = {}
    other_entries = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_file():
            files[path.name] = path.read_bytes()
        else:
            other_entries.append(path.name)
    return files, other_entries


def manifest_entries(files: dict[str, bytes]) -> list[dict[str, str]]:
    return [{"path": name, "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in sorted(files.items())]


def file_differences(left: dict[str, bytes],
                     right: dict[str, bytes]) -> list[dict[str, str | None]]:
    differences = []
    for name in sorted(set(left) | set(right)):
        left_hash = hashlib.sha256(left[name]).hexdigest() if name in left else None
        right_hash = hashlib.sha256(right[name]).hexdigest() if name in right else None
        if left_hash != right_hash:
            differences.append({"path": name, "left_sha256": left_hash,
                                "right_sha256": right_hash})
    return differences


def expected_runtime_marker(name: str) -> str | None:
    for mapping_name in (
            "RUNTIME_ERROR_MARKERS", "P8_RUNTIME_ERROR_MARKERS",
            "P9_RUNTIME_ERROR_MARKERS", "P10_RUNTIME_ERROR_MARKERS",
            "P11_RUNTIME_ERROR_MARKERS", "PHASE1_RUNTIME_ERROR_MARKERS",
            "P14_RUNTIME_ERROR_MARKERS"):
        marker = getattr(runner, mapping_name, {}).get(name)
        if marker is not None:
            return marker
    return None


def runtime_signature(name: str, result: dict[str, Any], temporary: Path,
                      source_dir: Path) -> dict[str, Any]:
    stderr = normalize_text(result["stderr"], temporary, name, source_dir)
    result_tuple = (result["rc"], result["stdout"], stderr)
    header = next((line for line in stderr.splitlines()
                   if line.startswith("Latent runtime error:")), "")
    marker = expected_runtime_marker(name)
    locations_ok = runner.has_source_locations(name, result_tuple)
    return {"header": header, "stderr": stderr,
            "marker_ok": marker is None or marker in stderr,
            "locations_ok": locations_ok}


def summarize(record: dict[str, Any], temporary: Path,
              name: str, source_dir: Path) -> dict[str, Any]:
    return {"phase": record["phase"], "rc": record["rc"],
            "stdout": normalize_text(record["stdout"], temporary, name, source_dir)[:1200],
            "stderr": normalize_text(record["stderr"], temporary, name, source_dir)[:2000],
            "output_exists": record.get("output_exists", False)}


def project_runner_cases(stage0: Path, stage1: Path, stage2: Path,
                         temporary: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    cases = python_target_cases()
    counts: dict[str, int] = {}
    mismatches: list[dict[str, Any]] = []
    compilers = (("stage0", stage0 / "latent.py"),
                 ("stage1", stage1 / "compiler.py"),
                 ("stage2", stage2 / "compiler.py"))
    for index, spec in enumerate(cases, 1):
        name = spec["name"]
        kind = spec["kind"]
        source_dir: Path = spec["source_dir"]
        source = source_dir / f"{name}.lt"
        if not source.is_file():
            mismatches.append({"case": name, "kind": kind,
                               "source": source.relative_to(ROOT).as_posix(),
                               "source_dir": source_dir.relative_to(ROOT).as_posix(),
                               "failures": [f"missing source: {source}"]})
            continue
        counts[kind] = counts.get(kind, 0) + 1
        results = {}
        for label, compiler in compilers:
            output = temporary / f"case-{index}"
            clear_output(output)
            results[label] = compile_and_run(
                label, compiler, source, name, source_dir, output, temporary)
        failures: list[str] = []

        for label, record in results.items():
            if record["rc"] in ("timeout", "error"):
                failures.append(f"{label} command ended with {record['rc']}")
            if kind == "positive" and (record["phase"] != "run" or record["rc"] != 0):
                failures.append(f"{label} positive case did not run successfully")
            if kind == "compile-negative":
                if record["phase"] != "compile" or record["rc"] == 0:
                    failures.append(f"{label} did not reject at compile time")
                if record.get("output_exists"):
                    failures.append(f"{label} left an output directory after compile failure")
            if kind == "runtime-negative" and (
                    record["phase"] != "run" or record["rc"] in (0, "timeout", "error")):
                failures.append(f"{label} did not produce a runtime failure")

        if kind == "compile-negative":
            normalized = []
            for label, record in results.items():
                diagnostic = record["stdout"] + record["stderr"]
                for marker in spec["markers"]:
                    if marker not in diagnostic:
                        failures.append(f"{label} diagnostic missing expected marker: {marker}")
                normalized.append((record["rc"],
                                   normalize_text(record["stdout"], temporary, name, source_dir),
                                   normalize_text(record["stderr"], temporary, name, source_dir)))
            if any(item != normalized[0] for item in normalized[1:]):
                failures.append("normalized compile status/diagnostic differs")
        elif kind == "runtime-negative":
            signatures = {}
            for label, record in results.items():
                signature = runtime_signature(name, record, temporary, source_dir)
                signatures[label] = signature
                if not signature["locations_ok"]:
                    failures.append(f"{label} runtime source-location contract failed")
                if not signature["marker_ok"]:
                    failures.append(f"{label} runtime error marker contract failed")
            outcomes = [(record["rc"], record["stdout"], signatures[label]["stderr"])
                        for label, record in results.items()]
            if any(item != outcomes[0] for item in outcomes[1:]):
                failures.append("runtime status/stdout/stderr differs")
        else:
            outcomes = [(record["phase"], record["rc"],
                         normalize_text(record["stdout"], temporary, name, source_dir),
                         normalize_text(record["stderr"], temporary, name, source_dir))
                        for record in results.values()]
            if any(item != outcomes[0] for item in outcomes[1:]):
                failures.append("normalized positive behavior differs")

        if failures:
            mismatches.append({"case": name, "kind": kind,
                               "source_dir": source_dir.relative_to(ROOT).as_posix(),
                               "failures": failures,
                               **{label: summarize(record, temporary, name, source_dir)
                                  for label, record in results.items()}})
        if index % 20 == 0 or index == len(cases):
            print(f"[runner projection] {index}/{len(cases)}; mismatches={len(mismatches)}",
                  flush=True)
    return mismatches, counts


def project_seeded_properties(stage0: Path, stage1: Path, stage2: Path,
                              temporary: Path) -> tuple[dict[str, Any] | None, int]:
    seed = properties.DEFAULT_SEED
    cases = properties.generate_cases(seed)
    source = temporary / "property-cases.lt"
    source.write_text(properties.program_source(cases, seed), encoding="utf-8",
                      newline="\n")
    fixture = TESTS / "java" / "Stage1Overloads.java"
    compilers = (("stage0", stage0 / "latent.py"),
                 ("stage1", stage1 / "compiler.py"),
                 ("stage2", stage2 / "compiler.py"))
    records = {}
    for label, compiler in compilers:
        output = temporary / "properties-output"
        clear_output(output)
        output.mkdir()
        command = [sys.executable, str(compiler), str(source)]
        if label == "stage0":
            command.extend(["-t", "py"])
        command.extend(["-o", str(output)])
        compiled = command_result(command, cwd=temporary)
        if compiled["rc"] != 0:
            records[label] = {"phase": "compile", **compiled}
            continue
        fixture_result = command_result(
            ["javac", "-cp", str(output), "-d", str(output), str(fixture)],
            cwd=ROOT)
        if fixture_result["rc"] != 0:
            records[label] = {"phase": "fixture", **fixture_result}
            continue
        executed = command_result([sys.executable, str(output / "property-cases.py")],
                                 cwd=output)
        records[label] = {"phase": "run", **executed,
                          "rows": len(executed["stdout"].splitlines())}
    failures: list[str] = []
    for label, record in records.items():
        if record["phase"] != "run" or record["rc"] != 0:
            failures.append(f"{label} property corpus did not run successfully")
        if record.get("rows") != len(cases):
            failures.append(f"{label} emitted {record.get('rows', 0)} of {len(cases)} rows")
    normalized = [(record["phase"], record["rc"],
                   record.get("stdout", "").replace(str(temporary), "<TMP>"),
                   record.get("stderr", ""))
                  for record in records.values()]
    if normalized and any(item != normalized[0] for item in normalized[1:]):
        failures.append("fixed-seed property output/status differs")
    if not failures:
        return None, len(cases)
    summary = {}
    for label in ("stage0", "stage1", "stage2"):
        record = records[label]
        summary[label] = {"phase": record["phase"], "rc": record["rc"],
                          "rows": record.get("rows", 0),
                          "stdout": record.get("stdout", "")[:1200],
                          "stderr": record.get("stderr", "")[:2000]}
    return {"kind": "fixed-seed-properties", "seed": seed,
            "property_cases": len(cases), "failures": failures,
            **summary}, len(cases)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path,
                        help="write a normalized JSON summary for CI artifacts")
    args = parser.parse_args()
    try:
        cases = python_target_cases()
    except Exception as exc:
        print(f"P15 Stage0/1/2 projection setup failed: {exc}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="p15-stage-projection-") as name:
        temporary = Path(name)
        stage0_seed = temporary / "stage0-seed"
        stage1 = temporary / "stage1"
        stage1.mkdir()
        no_javac = temporary / "no-javac"
        no_javac.mkdir()
        stage0_env = {**CLEAN_ENV, "PATH": str(no_javac)}
        try:
            extract_stage0(stage0_seed)
        except Exception as exc:
            print(f"P15 Stage0/1/2 projection setup failed: {exc}", file=sys.stderr)
            return 2
        build = command_result(
            [sys.executable, str(stage0_seed / "latent.py"),
             str(ROOT / "bootstrap" / "compiler.lt"), "-t", "py", "-o", str(stage1)],
            cwd=temporary, env=stage0_env)
        if build["rc"] != 0:
            print("Stage1 build failed:\n" + build["stdout"] + build["stderr"],
                  file=sys.stderr)
            return 2
        shutil.copy2(ROOT / "bootstrap" / "latent_host.py",
                     stage1 / "latent_host.py")
        shutil.rmtree(stage0_seed)

        stage2 = temporary / "stage2"
        build = command_result(
            [sys.executable, str(stage1 / "compiler.py"),
             str(ROOT / "bootstrap" / "compiler.lt"), "-o", str(stage2)],
            cwd=temporary)
        if build["rc"] != 0:
            print("Stage2 build failed:\n" + build["stdout"] + build["stderr"],
                  file=sys.stderr)
            return 2
        shutil.copy2(ROOT / "bootstrap" / "latent_host.py",
                     stage2 / "latent_host.py")

        stage3 = temporary / "stage3"
        build = command_result(
            [sys.executable, str(stage2 / "compiler.py"),
             str(ROOT / "bootstrap" / "compiler.lt"), "-o", str(stage3)],
            cwd=temporary)
        if build["rc"] != 0:
            print("Stage3 build failed:\n" + build["stdout"] + build["stderr"],
                  file=sys.stderr)
            return 2
        shutil.copy2(ROOT / "bootstrap" / "latent_host.py",
                     stage3 / "latent_host.py")

        declared = ("compiler.py", "latent_host.py", "LtJavaDaemon.java",
                    "JReflect.java", "LtRt.java")
        expected_names = set(declared)
        stage_directories = {"stage1": stage1, "stage2": stage2, "stage3": stage3}
        stage_snapshots = {}
        stage_artifact_entries = {}
        artifact_errors = []
        for label, directory in stage_directories.items():
            files, other_entries = stage_snapshot(directory)
            stage_snapshots[label] = {"files": files, "other_entries": other_entries}
            missing = sorted(expected_names - set(files))
            unexpected = sorted(set(files) - expected_names)
            stage_artifact_entries[label] = {
                "missing_declared": missing,
                "unexpected_files": unexpected,
                "non_file_entries": other_entries}
            if missing or unexpected or other_entries:
                artifact_errors.append(
                    f"{label} artifact set differs: missing={missing}, "
                    f"unexpected={unexpected}, non_files={other_entries}")

        stage2_files = stage_snapshots["stage2"]["files"]
        stage3_files = stage_snapshots["stage3"]["files"]
        stage2_stage3_differences = file_differences(stage2_files, stage3_files)
        stage2_stage3_bytes_equal = (
            stage2_files == stage3_files and
            set(stage2_files) == expected_names and
            set(stage3_files) == expected_names and
            not stage_snapshots["stage2"]["other_entries"] and
            not stage_snapshots["stage3"]["other_entries"])
        if not stage2_stage3_bytes_equal and not stage2_stage3_differences:
            artifact_errors.append("Stage2/Stage3 have non-file or undeclared artifacts")
        elif stage2_stage3_differences:
            artifact_errors.append("Stage2 and Stage3 artifact bytes differ")

        stage0 = temporary / "stage0-oracle"
        try:
            extract_stage0(stage0)
        except Exception as exc:
            print(f"P15 Stage0/Stage1/Stage2 projection setup failed: {exc}",
                  file=sys.stderr)
            return 2

        mismatches, counts = project_runner_cases(stage0, stage1, stage2, temporary)
        property_mismatch, property_count = project_seeded_properties(
            stage0, stage1, stage2, temporary)
        if property_mismatch is not None:
            mismatches.append(property_mismatch)
        if artifact_errors:
            mismatches.append({"kind": "stage2-stage3-fixed-point",
                               "failures": artifact_errors,
                               "file_differences": stage2_stage3_differences})
        unique_case_ids = len({case["name"] for case in cases})
        projection_actual = {
            "runner_case_ids": unique_case_ids,
            "fixture_invocations": len(cases),
            "fixed_seed": properties.DEFAULT_SEED,
            "fixed_seed_property_cases": property_count,
        }
        projection_contract_passed = projection_actual == EXPECTED_PROJECTION
        if not projection_contract_passed:
            mismatches.append({
                "kind": "projection-contract",
                "failures": [
                    f"{name}: expected {expected}, got "
                    f"{projection_actual.get(name)}"
                    for name, expected in EXPECTED_PROJECTION.items()
                    if projection_actual.get(name) != expected
                ],
            })
        ledger = json.loads(MANIFEST.read_text(encoding="utf-8"))
        decisions = ledger["runner_cases"]
        included_case_ids = sorted(name for name, decision in decisions.items()
                                   if decision["disposition"] == "include")
        excluded_case_ids = sorted(set(decisions) - set(included_case_ids))
        auxiliary = ledger["auxiliary_tests"]
        included_auxiliary = sorted(
            name for name, decision in auxiliary.items()
            if decision["disposition"] == "include")
        excluded_auxiliary = sorted(
            name for name, decision in auxiliary.items()
            if decision["disposition"] == "exclude")
        fixture_decisions = ledger["fixture_decisions"]
        included_fixtures = sorted(
            name for name, decision in fixture_decisions.items()
            if decision["disposition"] == "include")
        excluded_fixtures = sorted(
            name for name, decision in fixture_decisions.items()
            if decision["disposition"] == "exclude")
        protocol = ledger["p14_protocol_operations"]
        included_protocol = sorted(
            name for name, decision in protocol.items()
            if decision["disposition"] == "include")
        excluded_protocol = sorted(
            name for name, decision in protocol.items()
            if decision["disposition"] == "exclude")
        runner_failures = sum(1 for row in mismatches if "case" in row)
        property_failures = 1 if property_mismatch is not None else 0
        report = {"stage0_commit": STAGE0_COMMIT,
                  "runner_case_ids": unique_case_ids,
                  "fixture_invocations": len(cases),
                  "runner_case_groups": counts,
                  "ledger_case_counts": {"included": len(included_case_ids),
                                          "excluded": len(excluded_case_ids),
                                          "unclassified": 0},
                  "excluded_case_ids": excluded_case_ids,
                  "auxiliary_test_counts": {
                      "included": len(included_auxiliary),
                      "excluded": len(excluded_auxiliary)},
                  "excluded_auxiliary_paths": [
                      {"path": name, "reason": auxiliary[name]["reason"]}
                      for name in excluded_auxiliary],
                  "fixture_path_counts": {
                      "inventoried": len(fixture_decisions),
                      "included": len(included_fixtures),
                      "excluded": len(excluded_fixtures)},
                  "excluded_fixture_paths": [
                      {"path": name, "reason": fixture_decisions[name]["reason"]}
                      for name in excluded_fixtures],
                  "p14_protocol_operation_counts": {
                      "included": len(included_protocol),
                      "excluded": len(excluded_protocol)},
                  "excluded_p14_protocol_operations": [
                      {"operation": name, "reason": protocol[name]["reason"]}
                      for name in excluded_protocol],
                  "stage_manifests": {
                      label: manifest_entries(snapshot["files"])
                      for label, snapshot in stage_snapshots.items()},
                  "stage_artifact_entries": stage_artifact_entries,
                  "stage2_stage3_bytes_equal": stage2_stage3_bytes_equal,
                  "stage2_stage3_file_differences": stage2_stage3_differences,
                  "projection_contract": {
                      "expected": EXPECTED_PROJECTION,
                      "actual": projection_actual,
                      "passed": projection_contract_passed},
                  "fixed_seed": properties.DEFAULT_SEED,
                  "fixed_seed_property_cases": property_count,
                  "matching_invocations": len(cases) - runner_failures,
                  "runner_mismatches": runner_failures,
                  "property_mismatches": property_failures,
                  "artifact_mismatches": len(artifact_errors),
                  "stage_difference_counts": {
                      "runner_invocations": runner_failures,
                      "property_corpus": property_failures,
                      "artifact_errors": len(artifact_errors),
                      "projection_contract": int(not projection_contract_passed)},
                  "mismatches": mismatches}
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        print("P15 Stage0/Stage1/Stage2 projection: "
              f"{unique_case_ids} ledger case IDs / {len(cases)} fixture invocations "
              f"({counts.get('positive', 0)} positive, "
              f"{counts.get('compile-negative', 0)} compile-negative, "
              f"{counts.get('runtime-negative', 0)} runtime-negative), "
              f"{property_count} fixed-seed properties; "
              f"Stage2/Stage3 bytes equal={stage2_stage3_bytes_equal}; "
              f"projection contract={'PASS' if projection_contract_passed else 'FAIL'}; "
              f"runner mismatches={runner_failures}, property mismatches={property_failures}, "
              f"artifact mismatches={len(artifact_errors)}")
        if args.report is not None:
            print(f"REPORT={args.report}")
        for mismatch in mismatches[:30]:
            print(f"{mismatch.get('case', mismatch.get('kind'))}: "
                  f"{mismatch['failures']}")
        return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
