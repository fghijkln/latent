# M0 Python-target test projection ledger

`manifest.json` is the auditable M0 ledger. It records every test-case name in the explicit static lists and mappings of `tests/run_tests.py`, the membership of each named runner category, and the full inventories of Latent fixtures, Java host fixtures, and `tests/test_*.py` modules. The checker parses `run_tests.py` with Python's AST; it does **not** import or execute the runner to collect cases.

## Projection boundary

- **Included runner cases:** Every current explicit `run_tests.py` case has a Python-target compile/run or compile-rejection leg. The ledger separately preserves POSITIVE, MODULE_POSITIVE, all compile-negative groups and their marker/location maps, NEG_RUNTIME, and each individual COOKBOOK case. No cookbook group is excluded: Python API, Java interop, mixed, language, and module examples are included because the runner executes their `target=py` leg.
- **Included generated properties:** `tests/test_differential_properties.py` generates bounded Latent fixtures and executes both target outputs; its Python execution leg is in scope.
- **P14 protocol split:** `call_module_function` (stale Python module-function ref) is included; `call_static_method` (stale Java static-method ref) is explicitly excluded as Java-only. `test_p14_protocol.py` has a Python-target disposition because it contains the included Python protocol assertion as well as the separately classified Java assertion.
- **AST/editor exclusions:** `tests/diagnostics_sample.lt`, `test_diagnostics.py`, and `test_editor_metadata.py` exercise read-only AST/desugar diagnostics or parser-derived editor metadata rather than generated Python-target behavior. Each is explicitly classified and justified in the manifest.
- **Java-only exclusions:** The three `tests/java/*.java` host fixtures are compiled by `javac` for Java overload or host/static interop support; they are Java source dependencies, not Python-target source fixtures. Java interop **Latent** cases and cookbook examples remain included where the Python target leg is exercised.
- **Bootstrap support:** `test_bootstrap_host.py` and its Python adapter, `test_bootstrap_smoke.py`, and the fixed-Stage0 Latent lexer/parser fixtures and orchestrators are included as bootstrap support coverage, even though the legacy differential runner does not invoke those standalone tests.

## M3 lexer Unicode coverage

`test_bootstrap_lexer.py` compiles the Latent lexer through the pinned Stage0 and compares generated token kind/value/line/column tuples and lexer error positions against Stage0. Its generated corpus covers non-ASCII and supplementary-plane identifiers, Unicode decimal and numeric-tail characters, every Python-recognized whitespace codepoint as a blank/comment prefix, and selected malformed inputs. A second generated Latent probe checks each transition boundary/interior of the Unicode property runs and every decimal digit value against Stage0.

The checked-in data modules are pinned to Unicode 14.0.0 and 15.0.0. The Latent classifier selects the matching static table using only `unicodedata.unidata_version` metadata; range lookup and all character classification remain Latent code. The boundary suite runs against both planned Python 3.11/UCD 14 and local Python 3.12/UCD 15. Error text is compared exactly for numeric errors and mapped control/whitespace characters; other non-printable characters still require exact rejection and line/column checks, but their repr spelling is not treated as a category-classification result.

## M4 parser parameter and call coverage

`test_bootstrap_parser.py` probes the fixed Stage0 parser and generates a temporary Latent differential fixture for parameter/call/interop-prefix cases. It compares accepted versus rejected inputs and, for syntax errors, exact line, column, and diagnostic text. The corpus covers default, variadic, and keyword-only parameters; named and unpacked actual arguments and their ordering; invalid signatures/call ordering; trailing-comma boundaries; and `py`, `java`, and `super` primaries. The checked-in `bootstrap_parser.lt` separately asserts the added mapping-AST shapes while verifying simple positional parameters/arguments retain their legacy shapes.

## M7 local module graph slice

`bootstrap/module_graph.lt` performs explicit relative `.lt` loading in Latent, using the host only for path and UTF-8 file primitives. It resolves imports relative to the importing file, confines real paths (including symlink targets) to the selected source root, rejects absolute/invalid paths and non-`.lt` extensions, enforces top-level prologue ordering, reports source-relative parse/module locations, traverses dependencies in deterministic postorder, deduplicates canonical files, and rejects cycles. Its stable `source_id` values are root-relative POSIX-style paths and do not contain the physical checkout root.

The fixed-Stage0 fixture checks nested ordering, two spellings of one physical module, identical logical IDs under different temporary roots, missing/invalid/absolute/NUL/escaping imports, symlink escape, late/nested imports, parse locations in imported files, duplicate aliases, and cycle locations. `bootstrap/compiler.lt` calls the graph loader before its compile pipeline. The current linker assigns deterministic, collision-checked module symbols in Latent, resolves public alias-member reads/calls, preserves root-relative source IDs, and emits dependency-first per-module initializers with cached state. Fixed Stage0 differential coverage checks a three-module graph, repeated import of one canonical file, same-name exports, user-name collision avoidance, module-global reads, initialization order, deterministic output, and private-member rejection. The linker still explicitly rejects classes, nested functions, and global/nonlocal declarations in a linked graph; the remaining M7/M8 semantics and full Python-target compatibility are not claimed. Stage1 generated from the fixed Stage0 seed can still carry Stage0's absolute bootstrap source-map metadata; this is distinct from the root-relative source identity and from Stage1-produced user output, and remains subject to the P15 Stage1/M13 distinction.

## M8 single-file classes and inheritance slice

The Latent Python emitter/runtime supports single-file classes with or without one Latent parent: `.new()` construction, optional/default or inherited `init`, instance-local dynamic fields, inherited and overridden method dispatch, captured bound methods, identity equality, and class/instance/method display. Parent-first class construction is deterministic even for forward declarations. `super.method(self, ...)` is statically bound to the current class and dispatches from its direct parent; parent graph validation rejects unknown/non-class parents and inheritance cycles. Non-`init` overrides are checked for P12 binding compatibility, and known class constructor calls are checked against the nearest inherited initializer. `test_bootstrap_smoke.py` compares the repository's inheritance and P12 override programs against pinned Stage0, including exact first-line diagnostics for 21 inheritance/override/super compile negatives. Class declarations in linked multi-module programs remain explicitly unsupported by the module linker. This remains a bounded slice: it does not claim module-exported classes, complete class runtime diagnostics/source mapping, full static type inference for dynamic receiver calls, or full Python-target parity.

## M5 desugaring slice

`bootstrap/desugar.lt` performs the transformations in Latent and returns structured success/failure results. It preserves each copied/generated node's logical `source_id`, line, and column; the parser's `parse_expression_source` entry point parses interpolation expressions in Latent and rejects trailing tokens. The tested rewrites cover `$$`, `$name` and `${expression}` interpolation, `say` to `__say`, dot reads/calls to `__wgetattr`/`__wcall`, subscript reads/writes and attribute writes to their helper calls, `super.method(...)` to `SuperCall`, and final-expression implicit returns including both branches of a final `if/else`. Traversal tests also cover the currently parsed statement, parameter, call-argument, collection, operator, interop-prefix, class and exception node forms; an unknown node fails explicitly.

The bootstrap fixture's normalized interpolation, dot read, dot call, subscript read and both implicit-return branch expressions are compared by canonical AST signature against the pinned Stage0 desugarer on matching source. Separate assertions verify malformed/unterminated interpolation, invalid standalone `super`, trailing interpolation tokens, source identity/locations, and fail-closed unknown-node errors. This is structural differential coverage only: `compiler.lt` does not yet feed this IR to `gen_py.lt`, and no Stage0-versus-Latent generated-program behavior comparison is claimed for M5; helper runtime/emitter integration remains future work.

## Guard behavior

The checker fails for an added or removed static runner case/category reference, an added fixture path or test module absent from the inventory, a missing disposition, or an unreasoned exclusion. It also compares runner-invoked auxiliary test paths and AST-extracted P14 wire operation names, so changes to those protocols need explicit ledger updates.

Run from the repository root:

```sh
python3 tests/bootstrap/checker.py
python3 tests/bootstrap/test_checker.py
```

The focused tests verify that the existing repository passes and that a synthetic new unclassified `POSITIVE` case is rejected.
