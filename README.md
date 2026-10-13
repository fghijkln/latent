# Latent

[简体中文](#简体中文) · [English](#english)

## 简体中文

Latent 是一门通用小语言：语法直观、代码精简，同一份源码可由现有编译器生成 Python 或 Java。它的重点不是取代纯 Python，而是让一个程序能按需调用两边的库：`py "numpy"` 与 `java "java.time.LocalDate"` 可以共存于同一源码中；对应运行时只在真正使用时才启动或加载。

正式发布版本请见 [GitHub Releases](https://github.com/fghijkln/latent/releases)。当前开发线完成了 **P15：Python 宿主上的可复现自举编译器**。

### P15 自举更新

P15 将编译器核心写在 Latent 中，并验证固定的阶段链：

```text
固定 Stage0（v0.13.5 Python 编译器） → Stage1 → Stage2 → Stage3
```

Stage0 只负责启动自举；Stage1、Stage2、Stage3 的词法、解析、脱糖、语义分析、模块处理、Python 代码生成和 CLI 策略由 Latent 编译器源码实现。Python 适配层只提供受限的操作系统服务，例如文件、路径与进程 I/O。CI 检查 Stage0/Stage1/Stage2 的 Python 目标行为投影，并验证 Stage2 与 Stage3 的产物在同一构建及不同干净 checkout 根目录下逐字节复现。

这项工作是**运行在 Python 宿主上的自托管自举**，不是 native 或 host-independent bootstrap：生成的编译器仍需 Python 运行时。自举编译器当前只生成 Python；它不替代现有 `latent.py` 编译器，也不实现 Java 代码生成、REPL、AST/脱糖诊断或编辑器元数据模式。

当前 P15 验证记录包含 **243 个投影 case ID、244 次 fixture 调用、505 个固定种子性质案例，零差异**；Stage2 与 Stage3 产物相同。CI 另运行 Stage0 的完整双后端回归，以及 Python/JDK 版本矩阵和 VS Code 扩展检查。请参阅 [P15 设计说明](P15_SELF_HOSTING_BOOTSTRAP_DESIGN.md) 与 [P15 实施及验收记录](P15_SELF_HOSTING_IMPLEMENTATION_PLAN.md)。

### 快速开始

运行 Python 后端：

```bash
python3 latent.py prog.lt -t py -o out --run
```

运行 Java 后端（需要 JDK 17+；只有程序使用 `py` 互操作时才需要 Python 运行时）：

```bash
python3 latent.py prog.lt -t java -o out --run
```

查看语法树或脱糖后的 AST（只读诊断，不编译或运行）：

```bash
python3 latent.py prog.lt --show-ast
python3 latent.py prog.lt --show-desugar
```

交互式 REPL（Python 后端）：

```bash
python3 latent.py repl
```

`--show-ast` 也接受 `--dump-ast`。诊断模式输出版本化 JSON，携带源文件、行号和列号；只检查命令行指定的文件，不遍历模块图、不加载互操作运行时，也不写编译产物。

### 语言与互操作

Latent 支持函数与闭包、默认参数、命名参数、变参与调用展开、仅限关键字形参、单继承与 `super`、本地显式别名模块、异常处理，以及 Python/Java 互操作。P13/P14 扩展了特定 Python/Java 方法和函数作为 Latent callable 值的能力；覆盖范围与限制见对应设计记录。

```latent
fn fib(n):
    if n < 2:
        return n
    fib(n - 1) + fib(n - 2)

say fib(20)                         # 6765
np = py "numpy"                     # 延迟加载
say np.sqrt(2)
ArrayList = java "java.util.ArrayList"  # 延迟加载
items = ArrayList.new()
items.add("hello")
say items
```

### 工程与验证

- 现有 Python 编译器实现：`lex.py → parse.py → desugar.py → semant.py → gen_py.py / gen_java.py`。
- P15 编译器核心源码位于 `bootstrap/`；薄 Python 宿主适配层为 `bootstrap/latent_host.py`。
- 完整现有回归：`python3 tests/run_tests.py`。v0.13.5 基线套件包含 259 个回归条目；CI 覆盖 Python 3.11 与 JDK 17/21。
- 自举阶段及双根复现门禁：`python3 -m unittest tests.test_bootstrap_smoke -v`。
- Stage0/Stage1/Stage2/Stage3 的 Python 目标投影：`python3 tests/bootstrap/project_stage0.py`。
- P15 CI 使用 Python 3.11/JDK 17；另有 VS Code 扩展测试与打包任务。

正例与 Cookbook 示例会对拍双后端输出；运行期负例检查失败状态及 `.lt` 源码位置。编译错误包含文件、行、列及源码定位；可映射的未捕获运行错误包含 `.lt` 文件、行号和 Latent 调用栈。

### 当前边界

- 自举编译器目前仅生成 Python；Java 后端由现有 `latent.py` 编译器提供。
- P15 需要 Python 宿主；它不宣称无需 Python、native 或 host-independent。
- 模块导入限于显式本地 `.lt` 别名；不支持循环导入、包管理或多继承。
- Python/Java 互操作 callable 值仅覆盖文档列出的受支持方法与函数；不支持任意 callable 对象、独立 builtin（如 `len`）或 Latent callback。
- 数值类型为 float64；`java "int"` 等 Java 基本类型名不支持。

### 文档

- [教程](TUTORIAL.md) · [语言规范](SPEC.md) · [发布记录](CHANGELOG.md) · [贡献与测试指南](CONTRIBUTING.md)
- [P15 自举设计](P15_SELF_HOSTING_BOOTSTRAP_DESIGN.md) · [P15 实施计划与验收记录](P15_SELF_HOSTING_IMPLEMENTATION_PLAN.md)
- [P13 互操作绑定实例方法值](P13_INTEROP_BOUND_METHOD_VALUES_DESIGN.md) · [P14 Python 模块函数与 Java 静态方法值](P14_MODULE_AND_STATIC_FUNCTION_VALUES_DESIGN.md)
- [P12 方法覆盖签名兼容性](P12_METHOD_OVERRIDE_SIGNATURE_COMPATIBILITY_DESIGN.md) · [P11 仅限关键字形参](P11_KEYWORD_ONLY_PARAMETERS_DESIGN.md)
- [P10 变参与调用展开](P10_VARIADIC_ARGUMENTS_DESIGN.md) · [P9 默认参数](P9_DEFAULT_PARAMETERS_DESIGN.md) · [P8 命名参数](P8_NAMED_ARGUMENTS_DESIGN.md)
- [VS Code 扩展指南](VSCODE_EXTENSION_GUIDE.md)

### 许可证

MIT，见 [LICENSE](LICENSE)。

## English

Latent is a small general-purpose language with concise, approachable syntax. The existing compiler can compile the same source to Python or Java. The goal is not to replace pure Python, but to let one program use libraries from both ecosystems on demand: `py "numpy"` and `java "java.time.LocalDate"` can appear in the same source file, with the corresponding runtime loaded or started only when it is actually used.

See [GitHub Releases](https://github.com/fghijkln/latent/releases) for published versions. The current development line completes **P15: a reproducible self-hosting compiler hosted on Python**.

### P15 bootstrap update

P15 implements the compiler core in Latent and validates this fixed stage chain:

```text
fixed Stage0 (the v0.13.5 Python compiler) → Stage1 → Stage2 → Stage3
```

Stage0 is used only to seed the bootstrap. The Stage1, Stage2, and Stage3 compiler logic—lexing, parsing, desugaring, semantic analysis, module handling, Python code generation, and CLI policy—is implemented in Latent. A small Python adapter provides operating-system services such as file, path, and process I/O. CI checks the Python-target behavior projection across Stage0/Stage1/Stage2 and verifies byte-for-byte Stage2/Stage3 reproducibility both within one build and across clean checkouts at different absolute paths.

This is a **Python-hosted self-hosting bootstrap**, not a native or host-independent bootstrap: generated compiler stages still require a Python runtime. The bootstrap compiler currently emits Python only. It does not replace the existing `latent.py` compiler and does not implement Java code generation, a REPL, AST/desugared-AST diagnostics, or editor-metadata mode.

The current P15 validation report covers **243 projection case IDs, 244 fixture invocations, and 505 fixed-seed property cases with zero mismatches**; Stage2 and Stage3 artifacts match. CI also runs the full Stage0 dual-backend regression suite, a Python/JDK version matrix, and VS Code extension checks. See the [P15 design](P15_SELF_HOSTING_BOOTSTRAP_DESIGN.md) and [P15 implementation and acceptance record](P15_SELF_HOSTING_IMPLEMENTATION_PLAN.md).

### Quick start

Run the Python backend:

```bash
python3 latent.py prog.lt -t py -o out --run
```

Run the Java backend (JDK 17+ required; Python is needed only when the program uses `py` interop):

```bash
python3 latent.py prog.lt -t java -o out --run
```

Inspect the parsed or desugared AST (read-only diagnostics; no compilation or execution):

```bash
python3 latent.py prog.lt --show-ast
python3 latent.py prog.lt --show-desugar
```

Start the interactive REPL (Python backend):

```bash
python3 latent.py repl
```

`--show-ast` also accepts `--dump-ast`. These diagnostics emit versioned JSON with source-file, line, and column information. They inspect only the specified file: they do not traverse a module graph, load an interop runtime, or write compiler artifacts.

### Language and interop

Latent supports functions and closures, default and named arguments, variadic parameters and argument expansion, keyword-only parameters, single inheritance and `super`, explicitly aliased local modules, exceptions, and Python/Java interop. P13/P14 add supported Python/Java methods and functions as Latent callable values; see the design notes for exact scope and limitations.

```latent
fn fib(n):
    if n < 2:
        return n
    fib(n - 1) + fib(n - 2)

say fib(20)                         # 6765
np = py "numpy"                     # lazy loading
say np.sqrt(2)
ArrayList = java "java.util.ArrayList"  # lazy loading
items = ArrayList.new()
items.add("hello")
say items
```

### Project and verification

- Existing Python compiler pipeline: `lex.py → parse.py → desugar.py → semant.py → gen_py.py / gen_java.py`.
- P15 compiler sources are in `bootstrap/`; the thin Python host adapter is `bootstrap/latent_host.py`.
- Full existing regression suite: `python3 tests/run_tests.py`. The v0.13.5 baseline contains 259 regression entries; CI covers Python 3.11 with JDK 17 and 21.
- Bootstrap-stage and dual-root reproducibility gate: `python3 -m unittest tests.test_bootstrap_smoke -v`.
- Python-target projection through Stage0/Stage1/Stage2/Stage3: `python3 tests/bootstrap/project_stage0.py`.
- P15 CI uses Python 3.11/JDK 17; separate jobs test and package the VS Code extension.

Positive cases and Cookbook examples compare output across both backends. Runtime negative cases check failure status and `.lt` source locations. Compile-time errors include file, line, column, and source context; mapped uncaught runtime errors include the `.lt` file, line, and Latent call stack.

### Current limitations

- The bootstrap compiler currently emits Python only; Java code generation remains available through the existing `latent.py` compiler.
- P15 requires a Python host. It does not claim to run without Python or to be native/host-independent.
- Imports are limited to explicitly aliased local `.lt` modules; circular imports, package management, and multiple inheritance are not supported.
- Python/Java interop callable values are limited to documented supported methods and functions; arbitrary callable objects, standalone built-ins such as `len`, and Latent callbacks are unsupported.
- Numbers use float64; Java primitive type names such as `java "int"` are unsupported.

### Documentation

- [Tutorial](TUTORIAL.md) · [Language specification](SPEC.md) · [Changelog](CHANGELOG.md) · [Contributing and tests](CONTRIBUTING.md)
- [P15 bootstrap design](P15_SELF_HOSTING_BOOTSTRAP_DESIGN.md) · [P15 implementation plan and acceptance record](P15_SELF_HOSTING_IMPLEMENTATION_PLAN.md)
- [P13 interop bound instance method values](P13_INTEROP_BOUND_METHOD_VALUES_DESIGN.md) · [P14 Python module functions and Java static method values](P14_MODULE_AND_STATIC_FUNCTION_VALUES_DESIGN.md)
- [P12 method override signature compatibility](P12_METHOD_OVERRIDE_SIGNATURE_COMPATIBILITY_DESIGN.md) · [P11 keyword-only parameters](P11_KEYWORD_ONLY_PARAMETERS_DESIGN.md)
- [P10 variadic parameters and argument expansion](P10_VARIADIC_ARGUMENTS_DESIGN.md) · [P9 default parameters](P9_DEFAULT_PARAMETERS_DESIGN.md) · [P8 named arguments](P8_NAMED_ARGUMENTS_DESIGN.md)
- [VS Code extension guide](VSCODE_EXTENSION_GUIDE.md)

### License

MIT; see [LICENSE](LICENSE).
