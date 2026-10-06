# Changelog

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
