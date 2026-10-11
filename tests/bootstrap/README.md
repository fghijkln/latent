# M0 Python-target test projection ledger

`manifest.json` is the auditable M0 ledger. It records every test-case name in the explicit static lists and mappings of `tests/run_tests.py`, the membership of each named runner category, and the full inventories of Latent fixtures, Java host fixtures, and `tests/test_*.py` modules. The checker parses `run_tests.py` with Python's AST; it does **not** import or execute the runner to collect cases.

## Projection boundary

- **Included runner cases:** Every current explicit `run_tests.py` case has a Python-target compile/run or compile-rejection leg. The ledger separately preserves POSITIVE, MODULE_POSITIVE, all compile-negative groups and their marker/location maps, NEG_RUNTIME, and each individual COOKBOOK case. No cookbook group is excluded: Python API, Java interop, mixed, language, and module examples are included because the runner executes their `target=py` leg.
- **Included generated properties:** `tests/test_differential_properties.py` generates bounded Latent fixtures and executes both target outputs; its Python execution leg is in scope.
- **P14 protocol split:** `call_module_function` (stale Python module-function ref) is included; `call_static_method` (stale Java static-method ref) is explicitly excluded as Java-only. `test_p14_protocol.py` has a Python-target disposition because it contains the included Python protocol assertion as well as the separately classified Java assertion.
- **AST/editor exclusions:** `tests/diagnostics_sample.lt`, `test_diagnostics.py`, and `test_editor_metadata.py` exercise read-only AST/desugar diagnostics or parser-derived editor metadata rather than generated Python-target behavior. Each is explicitly classified and justified in the manifest.
- **Java-only exclusions:** The three `tests/java/*.java` host fixtures are compiled by `javac` for Java overload or host/static interop support; they are Java source dependencies, not Python-target source fixtures. Java interop **Latent** cases and cookbook examples remain included where the Python target leg is exercised.
- **Bootstrap support:** `test_bootstrap_host.py` and its Python adapter, `test_bootstrap_smoke.py`, and the fixed-Stage0 Latent lexer/parser fixtures and orchestrators are included as bootstrap support coverage, even though the legacy differential runner does not invoke those standalone tests.

## Guard behavior

The checker fails for an added or removed static runner case/category reference, an added fixture path or test module absent from the inventory, a missing disposition, or an unreasoned exclusion. It also compares runner-invoked auxiliary test paths and AST-extracted P14 wire operation names, so changes to those protocols need explicit ledger updates.

Run from the repository root:

```sh
python3 tests/bootstrap/checker.py
python3 tests/bootstrap/test_checker.py
```

The focused tests verify that the existing repository passes and that a synthetic new unclassified `POSITIVE` case is rejected.
