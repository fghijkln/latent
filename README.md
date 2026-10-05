# Latent

一门通用小语言：直观、代码少，一份源码编译为 Java 或 Python。
`py "mod"` / `java "com.foo.Bar"` 一行按需加载两个生态——懒到第一次真正
使用时才 `import`／`Class.forName`／才启动对端运行时。

万物皆潜在（latent），直到被触碰的那一刻。

- 教程：[TUTORIAL.md](TUTORIAL.md)
- 语言规范：[SPEC.md](SPEC.md)

## 用法

```bash
# 编译为 Python 并运行（用到了 java 才需要 java 在 PATH）
python3 latent.py prog.lt -t py -o out --run

# 编译为 Java 并运行（需要 JDK 17+；用到了 py 才需要 python3 在 PATH）
python3 latent.py prog.lt -t java -o out --run
```

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
- 测试：`python3 tests/run_tests.py` —— 13 个正例双后端输出逐字节对拍，
  5 个编译期负例，2 个运行时负例（20/20 通过）。

## v0.2 已知边界

无类、无闭包捕获、无 `try`、无模块系统；`py` 暂只支持模块句柄，
不支持内联 Python 代码块；`java` 不支持字段赋值与基本类型类名
（`java "int"`）；数字只有 float64 一种类型。

## 许可证

MIT，见 [LICENSE](LICENSE)。
