# Changelog

## v0.13.0 — 2026-10-06

### Added

- Latent 函数支持 `*rest` 位置收集器和 `**extra` 命名收集器；调用支持 `*items` / `**mapping`，与 P8 命名参数及 P9 默认参数组合。
- 适用普通函数/闭包/函数值、公开模块函数、Latent 绑定/继承方法、`super` 和构造器。位置收集器产生 Latent 列表；命名收集器产生保留插入顺序的 Latent 映射。
- `latent-ast` 从 v4 升至 v5，新增 `RestParam`、`ExtraParam`、`StarArg`、`StarStarArg`，旧节点字段继续保持。
- 随版本发布 VS Code 扩展 0.6.2，包含 P10 语法高亮和代码片段；提供编译、运行、测试命令及 Restricted Mode 执行阻断。

### Boundaries

- 形参顺序固定为必需位置、默认位置、`*rest`、`**extra`；不增加仅限关键字形参。`*items` 只接受 Latent 列表；`**mapping` 只接受字符串键映射并按插入顺序展开。重复、未知、缺失和过量值均报错。
- 内建函数和 Python/Java 互操作调用不接受展开实参；其既有调用协议不变。P9 默认参数仍由 v0.12.0 引入，P10 变参与 AST v5 于 v0.13.0 发布。详见 [P10 设计记录](P10_VARIADIC_ARGUMENTS_DESIGN.md)。

### Verification

- 在不含受保护 VS Code/用户文件的隔离副本运行 `python3 tests/run_tests.py`：**180 passed, 0 failed**（12 项 AST/CLI 诊断）；当前合计显式计入此前遗漏的 6 个 P9 静态编译负例，P10 增加 25 项回归。
- 13 个 tracked Python 文件 `py_compile` 通过；`javac runtime/*.java` 成功（class 输出放在临时目录）；`git diff --check` 通过。P8/P9 Python 与 Java 冒烟样例的输出逐字节一致。
- VSIX 0.6.2 作为 GitHub Release 资产单独提供；历史 VSIX、`node_modules`、缓存与测试输出不纳入发布范围。`P7_PLAN.md` 保持原样。

## v0.12.0 — 2026-10-06

### Added

- Call-time defaults for Latent functions, closures/function values, module functions, Latent methods, inherited methods, `super` calls, constructors, and `init`; required formals precede defaulted formals.
- Defaults are evaluated only for omitted arguments, on every call, in formal order. Earlier bound formals and lexical bindings are available; explicit `nil` suppresses a default.
- The stable `latent-ast` schema advances from v3 to v4 with `DefaultParam(name, default)`; legacy required-parameter strings and prior node fields retain their shapes.

### Compatibility

- P8 named/positional binding rules remain in force; duplicate, unknown, missing-required, and method/constructor checks apply statically where the target is known and dynamically to Latent function values/methods.
- Variadic and keyword-only parameters are not added. Built-ins and Python/Java interop remain unchanged.
- Programs without default parameters retain v0.11.0 call behavior. The v0.11.0 tag and release artifacts remain unchanged.

### Verification

- `python3 tests/run_tests.py`: 149 passed, 0 failed, including 11 AST/CLI diagnostics regressions; the focused P9 coverage includes 2 positive programs, 6 static compile negatives, and 4 dynamic runtime negatives on both backends.
- Python syntax checks, Java runtime compilation, and `git diff --check` passed.

## v0.11.0 — 2026-10-06

### Added

- Named arguments for Latent user-defined functions, first-class functions and closures, public module functions, Latent direct/bound methods, and `C.new(...)` / Latent `init` constructors.
- Static argument validation for calls with a known signature; runtime binding by preserved parameter names for function values and dynamically resolved Latent methods. Positional actuals must precede named actuals, and all actual expressions remain left-to-right.
- `latent-ast` schema v3 with an explicit `NamedArg(name, value)` node; existing node field shapes remain locked by regression tests.

### Compatibility

- Named-argument calls are new in v0.11.0; source using `callee(name=value)` requires this release or later. Existing positional-call syntax and v0.10.0 release artifacts are unchanged.
- `latent-ast` advances from schema v2 to v3 for the new `NamedArg` node. Existing node field shapes remain unchanged, but AST consumers must accept v3 before reading P8 dumps.
- A direct call whose signature is statically known (including an inherited `init`) now fails at compile time for invalid arguments instead of reaching the former runtime arity check; indirect function-value and uncertain-receiver calls remain runtime-checked. Runtime argument-binding failures share the `ArgumentError` category across backends while retaining the established arity message text.
- Built-ins and Python/Java interop methods/constructors remain positional-only. Defaults, variadics, and keyword-only parameters are not added.

### Verification

- `python3 tests/run_tests.py`: 142 passed, 0 failed (132 dual-backend/integration scenarios plus 10 read-only CLI regressions).
- `py_compile` passed for all 13 tracked Python files; `javac runtime/*.java` and `git diff --check` passed.

## v0.10.0 — 2026-10-06

### Added

- Reading `obj.method` returns a bound Latent method when no same-named instance field exists. The value captures the receiver and can be assigned, passed, returned, captured by a closure, and called indirectly; inherited methods bind to the runtime subclass instance.
- Bound methods display as `<bound method Class.method>`, hide `self` from their arity, and use the same runtime arity checks on both backends. Explicit `obj.method(...)` calls keep their existing dispatch path.

### Compatibility

- On a Latent instance with a matching method but no matching field, bare `obj.method` now returns a bound method instead of raising the previous missing-field error. A same-named instance field still wins; `obj.method = value` still writes a field.
- Built-ins and Python/Java handle methods remain direct-call-only. P7 does not change the `latent-ast` v2 shape.

### Verification

- `python3 tests/run_tests.py`: 116 passed, 0 failed (107 dual-backend/integration scenarios plus 9 read-only CLI regression tests).
- `py_compile` passed for all 13 tracked Python files; `javac runtime/*.java` and `git diff --check` passed.

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
