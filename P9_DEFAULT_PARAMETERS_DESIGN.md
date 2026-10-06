# P9 Design: Default Parameter Values

**Status:** Released in v0.12.0 on 2026-10-06, building on the v0.11.0 / P8 baseline.

## Scope

P9 adds call-time default values to Latent user-defined function parameters, using syntax such as `fn scale(x, factor=2)`. Required parameters must precede all defaulted parameters. P9 does not add variadic parameters, keyword-only parameters, defaults for built-ins, or defaults for Python/Java interop callables.

The first method receiver is always required and cannot have a default. Constructor defaults are the defaults declared by the runtime-selected Latent `init`, including an inherited `init` when the child class does not override it.

## Evaluation and binding

- Actual argument expressions keep P8's left-to-right source evaluation order and are all evaluated before the callee binds them.
- A default expression is evaluated only if its parameter was omitted. Explicit `nil` is a supplied value and suppresses the default.
- Defaults are evaluated on every invocation, in formal parameter order. A default can use an earlier parameter after it has been supplied or defaulted, and lexical closure/global bindings are read when the invocation occurs.
- A default cannot refer to its own or a later formal parameter; such references are rejected statically because those bindings are not initialized yet.
- Supplied positional/named arguments use the existing P8 binding rules. Missing optional parameters are represented internally by a distinct marker, not by Latent `nil`, then their defaults run at the function entry.
- Static signatures are checked at compile time where P8 can establish the target. Function values and dynamically resolved Latent methods use the same required-count/name metadata at runtime. Built-ins and Python/Java interop boundaries remain unchanged.

## Supported callables

Defaults apply consistently to ordinary functions, function values, nested closures, public module functions, Latent direct and bound methods, inherited methods, `super` calls, `C.new(...)`, and Latent `init` methods. Python and Java backends bind to the same formal order and use the same required/optional split. Python/Java interop does not inherit Latent defaults.

## AST schema and compatibility

The stable `latent-ast` schema advances from v3 to v4. A defaulted formal is represented by `DefaultParam(name, default)` in `FnDef.parameters`. Required formals remain strings, so existing parameter-list values and all pre-existing node field names, order, and shapes remain unchanged. The new node is explicitly serialized; compiler-only binding metadata stays out of the public dump. Old v3-only AST consumers must accept schema v4 before reading dumps containing defaults.

## Diagnostics and validation

P8 duplicate, unexpected-name, multiple-value, and required-argument diagnostics remain shared by static and dynamic binding paths. Positional arity diagnostics for functions without defaults remain unchanged. P9 regressions cover valid positional/named mixtures, override behavior, default-to-default dependencies, per-call side effects and evaluation order, updated closure capture, modules, methods, inherited constructors, both backends, required/unknown/duplicate failures, and stable AST compatibility.

`python3 tests/run_tests.py` completed with **149 passed, 0 failed**, including 11 AST/CLI diagnostics regressions. The focused P9 suite covers 2 positive programs, 6 static compile negatives, and 4 dynamic runtime negatives across Python and Java.

## Release boundary

This design note documents the P9 semantics released in v0.12.0. The prior v0.11.0 / P8 release artifacts remain unchanged.
