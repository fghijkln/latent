# Changelog

## v0.9.0 — 2026-10-06

### Added

- `nonlocal name[, name...]` binds reads and writes in a function to the nearest enclosing function scope that owns that local name. Returned closures share and update the same mutable binding; unbound intermediate scopes are skipped.
- `nonlocal` works with enclosing parameters, assignments, loop targets, catch variables, and nested function names. Declarations apply to the whole function body and do not leak into nested functions.
- The read-only `latent-ast` JSON schema advances to version 2 with `NonlocalStmt(names)`; the version-1 node shapes and field ordering are unchanged.

### Compatibility

- `nonlocal` is now a reserved keyword; programs that used it as an identifier must rename it.
- Missing enclosing bindings, parameter/`global` conflicts, duplicate declarations, and declarations at module or class scope are compile-time errors.

### Verification

- `python3 tests/run_tests.py`: 113 passed, 0 failed (104 dual-backend/integration scenarios plus 9 read-only CLI regression tests).
- `py_compile` passed for all 13 tracked Python files; `javac runtime/*.java` and `git diff --check` passed.

## v0.8.0 — 2026-10-06

### Added

- User-defined ordinary functions are first-class values: they can be assigned, passed as arguments, returned, and called indirectly through variables. Public functions from imported modules can also be read as values.
- Nested functions use lexical closures that capture shared mutable bindings, including later updates; recursion, mutual recursion, and lexical shadowing are supported.
- Function-local `global x, y` declarations read and write module-level bindings, even when an enclosing local has the same name. The declaration applies throughout its function body; a nested function declares its own globals.
- Function values have the stable display form `<function name>`. Python and Java backends use aligned runtime function wrappers, arity checks, and non-function-call errors for indirect calls.

### Compatibility

- `global` is now a reserved keyword; existing programs that used it as an identifier must rename it.
- Direct named calls retain compile-time arity checks. Calls through function-valued variables perform arity checks at runtime.
- Only user-defined ordinary functions are first-class. Latent class methods, built-ins, and Python/Java interop handle methods remain direct-call-only; `nonlocal` is not supported.

### Verification

- `python3 tests/run_tests.py`: 100 passed, 0 failed (92 dual-backend/integration scenarios plus 8 read-only CLI regressions).
- Python `py_compile` and `git diff --check` passed.

## v0.7.0 — 2026-10-06

### Added

- P4 read-only CLI diagnostics: `--show-ast` (`--dump-ast` alias) emits the parsed AST; `--show-desugar` emits the desugared tree.
- Stable, versioned `latent-ast` JSON output (format version 1) with explicit node fields and source line/column positions.
- Structured lexer, parser, and desugar error reporting for diagnostic commands, plus regression coverage for attribute access/assignment and control flow.

### Compatibility

- Diagnostic modes stop before module-graph traversal, semantic analysis, code generation, and execution; they do not import/start Python or Java interop runtimes or modify normal compile artifacts.
- Normal compilation behavior and the VS Code extension files are unchanged. Diagnostic flags reject `--run`; `-t` and `-o` are ignored in diagnostic mode.

### Verification

- `python3 tests/run_tests.py`: 89 passed, 0 failed (82 existing dual-backend/integration cases plus 7 diagnostic regressions).
- Python `py_compile` and `git diff --check` passed.
- VS Code Stable 1.140.0 extension-host integration suite: 5 passing; its `latent.runTests` command also verified the 89-case project suite.

## v0.6.0 — 2026-10-06

### Added

- P2 local modules: relative `.lt` imports under explicit aliases, static dependency bundling, public/private exports, one-time dependency-first initialization, cycle detection, and cross-file diagnostics.
- P3 single inheritance for Latent classes, including forward parent references, nearest-method lookup, method overrides, inherited constructors, explicit `super.method(self, ...)` dispatch, and cross-module inheritance from public Latent classes.
- Dual-backend tests and cookbook/tutorial/specification coverage for module and inheritance behavior.

### Semantics and compatibility

- A child initializer runs alone unless it explicitly calls `super.init(self, ...)`; a child without its own `init` inherits the nearest parent initializer.
- Ordinary method overrides must keep the inherited parameter count. `init` may add/change constructor parameters, because the language's documented constructor example requires it; each explicit `super.init` call is checked against the selected parent implementation.
- `super` is a reserved keyword and is restricted to explicit receiver calls inside instance methods.
- Existing `import` and `as` identifiers are reserved for the P2 module syntax. Existing Latent programs using either as an identifier, or `super` as an identifier, must rename it.
- No multiple inheritance, interfaces, Java class inheritance, class/static methods, or operator overloading.

### Verification

- `python3 tests/run_tests.py`: 82 passed, 0 failed; successful programs are run on both Python and Java and stdout is compared byte-for-byte.
- `python3 -m py_compile ...`, `git diff --check`, and Java compilation are included in release validation.
