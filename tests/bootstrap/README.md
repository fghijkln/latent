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

The fixed-Stage0 fixture checks nested ordering, two spellings of one physical module, identical logical IDs under different temporary roots, missing/invalid/absolute/NUL/escaping imports, symlink escape, late/nested imports, parse locations in imported files, duplicate aliases, and cycle locations. `bootstrap/compiler.lt` calls the graph loader before its compile pipeline. The linker assigns deterministic, collision-checked module symbols, resolves public alias-member reads/calls and class-parent references, preserves root-relative source IDs, and emits dependency-first per-module initializers with cached state. It now supports top-level classes in linked graphs (including cross-module inheritance/`super`), nested functions with closure capture, module `global` bindings, lexical `nonlocal`, and free module names inside interpolated strings. Fixed Stage0 differential coverage includes eight linked-module programs for classes, inheritance, bound methods, defaults, keyword-only calls, nested closures, and global/nonlocal mutation, plus the cookbook module-interpolation case. Nested class declarations remain fail-closed; complete runtime diagnostics/source maps, full Python/Java interop, and full Python-target compatibility are not claimed. Stage1 generated from the fixed Stage0 seed can still carry Stage0's absolute bootstrap source-map metadata; this is distinct from root-relative source identity and Stage1-produced user output, and remains subject to the P15 Stage1/M13 distinction.

## M8 single-file classes and inheritance slice

The Latent Python emitter/runtime supports single-file classes with or without one Latent parent: `.new()` construction, optional/default or inherited `init`, instance-local dynamic fields, inherited and overridden method dispatch, captured bound methods, identity equality, and class/instance/method display. Parent-first class construction is deterministic even for forward declarations. `super.method(self, ...)` is statically bound to the current class and dispatches from its direct parent; parent graph validation rejects unknown/non-class parents and inheritance cycles. Non-`init` overrides are checked for P12 binding compatibility, and known class constructor calls are checked against the nearest inherited initializer. Multi-module tests now compare top-level class exports, constructors, inherited methods, bound methods, cross-module `super`, default/keyword-only arguments, and nested closure behavior against pinned Stage0. The bounded slice still does not claim complete class runtime diagnostics/source mapping, full static type inference for dynamic receiver calls, Java interop, or full Python-target parity.

## M5 desugaring slice

`bootstrap/desugar.lt` performs the transformations in Latent and returns structured success/failure results. It preserves each copied/generated node's logical `source_id`, line, and column; the parser's `parse_expression_source` entry point parses interpolation expressions in Latent and rejects trailing tokens. The tested rewrites cover `$$`, `$name` and `${expression}` interpolation, `say` to `__say`, dot reads/calls to `__wgetattr`/`__wcall`, subscript reads/writes and attribute writes to their helper calls, `super.method(...)` to `SuperCall`, and final-expression implicit returns including both branches of a final `if/else`. Traversal tests also cover the currently parsed statement, parameter, call-argument, collection, operator, interop-prefix, class and exception node forms; an unknown node fails explicitly.

The bootstrap fixture's normalized interpolation, dot read, dot call, subscript read and both implicit-return branch expressions are compared by canonical AST signature against the pinned Stage0 desugarer on matching source. Separate assertions verify malformed/unterminated interpolation, invalid standalone `super`, trailing interpolation tokens, source identity/locations, and fail-closed unknown-node errors. `compiler.lt` feeds the transformed IR to `gen_py.lt`; supported transformations are exercised by fixed-Stage0 behavior tests. The module linker pre-desugars string expressions only as needed to rewrite free module names inside interpolation before the normal lowering pass. Complete diagnostics/source maps and full Python-target compatibility remain future work.

## Guard behavior

The checker fails for an added or removed static runner case/category reference, an added fixture path or test module absent from the inventory, a missing disposition, or an unreasoned exclusion. It also compares runner-invoked auxiliary test paths and AST-extracted P14 wire operation names, so changes to those protocols need explicit ledger updates.

Run from the repository root:

```sh
python3 tests/bootstrap/checker.py
python3 tests/bootstrap/test_checker.py
```

The focused tests verify that the existing repository passes and that a synthetic new unclassified `POSITIVE` case is rejected.


## M6 single-file function scope and runtime slice

The single-file Python emitter collects local assignments across control flow without entering nested function bodies, initializes unbound Latent locals to `nil`, and hoists `global`/`nonlocal` declarations so Python closure cells preserve the language's binding behavior. A pinned Stage0 differential test now also covers `py_numpy`: Stage1 uses Stage0-compatible recursive equality for Latent lists/maps, NaN behavior for numbers, and identity comparison for external host objects (including NumPy arrays). In the bounded runtime slice, `range(...)` produces a Latent list, host tuples display as Latent lists, and calling a non-function reports the Latent runtime error. Module-linked nested closures and global/nonlocal declarations have separate Stage0 comparisons. This does not imply complete built-in adaptation, Java interop, complete diagnostics/source maps, or full Python-target projection; those remain explicit P15 blockers.


## M7 explicit source-root selection

The Stage1 CLI accepts `--source-root ROOT` to select the logical module root explicitly; relative roots are resolved from the compiler's working directory. The root must be an existing directory, the entry must resolve inside it, and every imported file is still canonicalized through `realpath` and rejected if it escapes the root (including symlink escapes). Omitting the option preserves the prior entry-parent default. Fixed-Stage0 graph probes compare logical IDs across two different checkouts and cover upward relative imports, invalid roots, entries outside the root, import escapes and symlink escapes. Stage0/Stage1 integration compares explicit-root execution for linked classes, closures and interpolation and confirms generated bytes are independent of the absolute checkout root. Root containment remains enforced; nested class declarations, Java interop, and complete Python-target compatibility remain unsupported.
