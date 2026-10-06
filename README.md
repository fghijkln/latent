# Latent

一门通用小语言：直观、代码少，一份源码编译为 Java 或 Python。
`py "mod"` / `java "com.foo.Bar"` 一行按需加载两个生态——懒到第一次真正
使用时才 `import`／`Class.forName`／才启动对端运行时。

万物皆潜在（latent），直到被触碰的那一刻。

**当前发布：v0.7.0**（只读编译器内省）。

Latent 不追求在简单脚本上赢过 Python——写纯 Python 逻辑，Python 的工具和
生态成熟得多，无需争辩。它的赌注只有一个：**同一份源码，跑在 Python 和
Java 两个运行时上，并在同一个程序里按需使用两边的库**。`py "numpy"` 和
`java "java.time.LocalDate"` 可以出现在同一个文件里，互不启动对方的
运行时，直到真正被用到的那一刻。

代价是真实的：两套后端、跨语言对象让调试和错误处理更难。为此编译器用
机制而不是运气来还债：`tests/run_tests.py` 每次都在双后端之间逐字节
对拍输出（82 项双后端/集成场景），另有 7 项只读 CLI 诊断回归，两端行为不一致即失败。

- 教程：[TUTORIAL.md](TUTORIAL.md)
- 语言规范：[SPEC.md](SPEC.md)
- P2 模块语义：[P2_MODULES_DESIGN.md](P2_MODULES_DESIGN.md)
- P3 继承语义：[P3_INHERITANCE_DESIGN.md](P3_INHERITANCE_DESIGN.md)
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
- 测试：`python3 tests/run_tests.py` —— 21 个正例、30 个编译期负例、
  16 个运行期负例、15 个 cookbook（82 个双后端/集成场景）以及 7 个只读诊断
  单元/CLI 回归；正例与 cookbook 双后端输出逐字节对拍，运行期负例验证两端失败
  状态与 `.lt` 源码位置。
- 编译期错误显示 `文件:行:列`、源码行和 `^`；未捕获的运行期错误在
  Python/Java 后端显示 `.lt` 文件、行号和 Latent 调用栈。无法映射时回退到
  原生 traceback/Java 堆栈；运行期暂不显示列号。

## 已知边界（v0.7.0）

支持 Latent 单继承、`super`、`try`/`catch`/`throw`、属性与下标读写；未捕获的运行期错误
已映射到 `.lt` 文件和行号。P2 静态模块与 P3 继承随 v0.6.0 发布；v0.7.0 增加只读 AST/脱糖诊断。模块导入仍只支持本地显式别名，不支持循环导入或包管理。仍无多继承、闭包捕获；
`py` 只支持模块句柄，
不支持内联 Python 代码块；`java` 不支持基本类型类名（`java "int"`）；调用只有
位置参数；数字只有 float64 一种类型。

## 许可证

MIT，见 [LICENSE](LICENSE)。
