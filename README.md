# Latent

一门通用小语言：直观、代码少，一份源码编译为 Java 或 Python。
`py "mod"` / `java "com.foo.Bar"` 一行按需加载两个生态——懒到第一次真正
使用时才 `import`／`Class.forName`／才启动对端运行时。

万物皆潜在（latent），直到被触碰的那一刻。

**当前发布：v0.13.0**（P10 变参形参与调用展开、`latent-ast` schema v5；并包含 P9 默认参数、P8 命名参数、v0.10.0 的 Latent 实例绑定方法值、v0.9.0 的 `nonlocal` 词法绑定与 v0.8.0 的函数值和闭包）。

P8 设计与实现记录见 [P8 命名参数设计](P8_NAMED_ARGUMENTS_DESIGN.md)。
P9 默认参数语义与 AST schema v4 见 [P9 默认参数设计](P9_DEFAULT_PARAMETERS_DESIGN.md)。
P10 变参语义与 AST schema v5 见 [P10 变参形参与调用展开](P10_VARIADIC_ARGUMENTS_DESIGN.md)。

Latent 不追求在简单脚本上赢过 Python——写纯 Python 逻辑，Python 的工具和
生态成熟得多，无需争辩。它的赌注只有一个：**同一份源码，跑在 Python 和
Java 两个运行时上，并在同一个程序里按需使用两边的库**。`py "numpy"` 和
`java "java.time.LocalDate"` 可以出现在同一个文件里，互不启动对方的
运行时，直到真正被用到的那一刻。

代价是真实的：两套后端、跨语言对象让调试和错误处理更难。为此编译器用
机制而不是运气来还债：`tests/run_tests.py` 当前有 186 个回归条目（包括 12 项只读 AST/CLI 诊断），其中包含阶段 1 的固定语义用例和 1 个有界、固定种子的性质测试入口；该入口运行时会报告实际案例数，但在套件总数中按 1 项计。双后端输出逐字节对拍，两端行为不一致即失败。P10 新增 25 项回归。

- 教程：[TUTORIAL.md](TUTORIAL.md)
- 语言规范：[SPEC.md](SPEC.md)
- P2 模块语义：[P2_MODULES_DESIGN.md](P2_MODULES_DESIGN.md)
- P3 继承语义：[P3_INHERITANCE_DESIGN.md](P3_INHERITANCE_DESIGN.md)
- P5 函数值与闭包设计：[P5_FUNCTION_VALUES_DESIGN.md](P5_FUNCTION_VALUES_DESIGN.md)
- P6 `nonlocal` 设计：[P6_NONLOCAL_DESIGN.md](P6_NONLOCAL_DESIGN.md)
- P8 命名参数设计：[P8_NAMED_ARGUMENTS_DESIGN.md](P8_NAMED_ARGUMENTS_DESIGN.md)
- P9 默认参数设计：[P9_DEFAULT_PARAMETERS_DESIGN.md](P9_DEFAULT_PARAMETERS_DESIGN.md)
- P10 变参与调用展开：[P10_VARIADIC_ARGUMENTS_DESIGN.md](P10_VARIADIC_ARGUMENTS_DESIGN.md)
- VS Code 插件使用指南：[VSCODE_EXTENSION_GUIDE.md](VSCODE_EXTENSION_GUIDE.md)
- 发布记录：[CHANGELOG.md](CHANGELOG.md)

## 用法

```bash
# 编译为 Python 并运行（用到了 java 才需要 java 在 PATH）
python3 latent.py prog.lt -t py -o out --run

# 编译为 Java 并运行（需要 JDK 17+；用到了 py 才需要 python3 在 PATH）
python3 latent.py prog.lt -t java -o out --run

# 查看解析 AST / 脱糖后的 AST；输出 JSON，不编译、不运行、不写产物
python3 latent.py prog.lt --show-ast
python3 latent.py prog.lt --show-desugar

# 交互式（Python 后端；空行结束缩进块，裸表达式自动打印）
python3 latent.py repl
```

`--show-ast`（别名 `--dump-ast`）与 `--show-desugar` 输出版本化、稳定的 JSON AST，节点
带源文件、行号和列号。它们只检查命令行指定的单个文件：前者做词法/语法分析，后者再做
脱糖；不执行语义检查、代码生成或模块图遍历，不加载 Python/Java 互操作运行时，也不创建
或修改 `out/` 及 `-o` 指向的普通编译产物。诊断模式下 `-t`/`-o` 不生效；`--run` 不可与诊断
选项并用。语法错误仍显示编译阶段、源码行和 `^` 定位。

- `-t py` 产出：`<prog>.py` + `LtJavaDaemon.java`、`JReflect.java`、`LtRt.java`
 （`java` 句柄用的 JVM 守护进程；有 javac 就当场编译好，
  没有则在第一次 `java` 使用时编译）。
- `-t java` 产出：`<Prog>.java` + `LtRt.java`（运行时）、`JReflect.java`（反射核心）、
  `ltpy.py`（懒启动的 Python 守护进程，`LATENT_PY` 环境变量或
  `-Dlatent.pydaemon=` 可指定位置）。`java` 句柄走直接反射，无守护进程。

## 速览

```latent
fn fib(n):
    if n < 2:
        return n
    fib(n - 1) + fib(n - 2)

say fib(20)                       # 6765

name = "latent"
say "hello $name, ${1 + 2 * 3}"   # 插值

np = py "numpy"                   # 懒加载：这里什么都不发生
say np.sqrt(2)                    # 第一次使用时才 import（Java 下才启动 python3）

A = java "java.util.ArrayList"    # 同样懒：不 Class.forName（Python 下不启动 JVM）
a = A.new()                       # 构造
a.add("hi")
say a                             # ["hi"]
say java "java.lang.Math".sqrt(2)  # 静态方法直接调：1.4142135623730951
```

## 工程

- 纯 Python 标准库实现：`lex.py → parse.py → desugar.py → semant.py → gen_py.py / gen_java.py`
- 测试：`python3 tests/run_tests.py` —— 186 个回归条目，其中 12 项为只读 AST/CLI 诊断；包括阶段 1 固定语义用例和 1 个有界、固定种子的性质测试入口（实际生成案例数会在运行时打印，套件计数为 1 项）。
  正例与 cookbook 双后端输出逐字节对拍，运行期负例验证两端失败状态与 `.lt` 源码位置。
- 编译期错误显示 `文件:行:列`、源码行和 `^`；未捕获的运行期错误在
  Python/Java 后端显示 `.lt` 文件、行号和 Latent 调用栈。无法映射时回退到
  原生 traceback/Java 堆栈；运行期暂不显示列号。

## 已知边界（已发布 v0.13.0）

支持 Latent 单继承、`super`、`try`/`catch`/`throw`、属性与下标读写；未捕获的运行期错误
已映射到 `.lt` 文件和行号。P2 静态模块与 P3 继承随 v0.6.0 发布；v0.7.0 增加只读 AST/脱糖诊断；v0.8.0 增加用户定义函数值、嵌套词法闭包和 `global`，详见 [P5 设计记录](P5_FUNCTION_VALUES_DESIGN.md)；v0.9.0 增加 `nonlocal`，详见 [P6 设计记录](P6_NONLOCAL_DESIGN.md)；v0.10.0 让 `obj.method` 在没有同名字段时返回捕获实例的绑定方法值，实例字段仍优先；v0.11.0/P8 增加 Latent 调用的命名参数；v0.12.0/P9 增加函数、闭包、模块函数、方法与构造器的默认参数；v0.13.0/P10 增加相同调用范围内的 `*rest`、`**extra` 形参及 `*items`、`**mapping` 实参展开，并将 `latent-ast` 升级到 schema v5。模块导入仍只支持本地显式别名，不支持循环导入或包管理。仍无多继承；内建函数及互操作句柄方法不作为函数值；
`py` 只支持模块句柄，
不支持内联 Python 代码块；`java` 不支持基本类型类名（`java "int"`）；数字只有 float64 一种类型。

**阶段 1 已实现并纳入回归**：两后端统一了 `NaN` 真值、比较与负底数非整数幂语义；Java 后端的字符串长度、下标和迭代按 Unicode 码点处理；Java 重载调用按转换等级和形参特异性选择唯一未被支配的候选，歧义时报告稳定错误。相关规则见[语言规范](SPEC.md#4-语义)及其 Java 互操作章节。Latent 列表/映射传给兼容的 Java `List`/`Map` 形参以及列表转 Java 数组也已受运行时支持。

## v0.11.0：命名参数

位置参数必须先于命名参数，实参仍按源码从左到右求值；签名已知时在编译期检查，否则在运行期按保留的形参名绑定。使用命名调用的源码要求 v0.11.0 或更新版本。

## v0.12.0：默认参数

Latent 用户定义函数的默认表达式在每次调用时、仅对省略的实参按形参顺序求值；显式传入 `nil` 不会触发默认值。必需形参必须位于默认形参之前，默认值可引用更早的形参。该功能适用于函数值、闭包、模块函数、Latent 方法与构造器；内建函数及 Python/Java 互操作调用不变。`latent-ast` schema 升级到 v4，新增 `DefaultParam(name, default)` 节点。

## v0.13.0：P10 变参与调用展开

P10 为 Latent 函数增加 `*rest` 位置收集器和 `**extra` 命名收集器，并允许 Latent 调用使用 `*items`、`**mapping` 展开实参；支持函数值、闭包、模块函数、Latent 方法、`super` 与构造器。`*items` 只接受 Latent 列表；`**mapping` 只接受键为字符串的 Latent 映射，并按插入顺序展开。内建函数及 Python/Java 互操作调用不接受展开实参；仍不支持仅限关键字形参。AST schema v5 新增 `RestParam`、`ExtraParam`、`StarArg` 和 `StarStarArg`，详见 [P10 设计记录](P10_VARIADIC_ARGUMENTS_DESIGN.md)。

## 许可证

MIT，见 [LICENSE](LICENSE)。
