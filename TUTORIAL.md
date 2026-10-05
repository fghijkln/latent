# Latent 教程

Latent（"潜在"）是一门小语言：直观、代码少。一份源码可以编译成 Python，
也可以编译成 Java，两种产物语义一致。

它的招牌特性是**懒加载互操作**：

```latent
np = py "numpy"                  # 借用 Python 生态
A = java "java.util.ArrayList"    # 借用 Java 生态
```

这两行在执行时**什么都不做**——不 `import`、不 `Class.forName`，
甚至不启动对端运行时。直到你第一次真正调用，加载才发生。
这就是"Latent"这个名字的来历：万物皆潜在，直到被触碰的那一刻。

本教程的每一节都附可运行的例子。约定：`$` 开头的是 shell 命令，
其余是 Latent 代码，注释 `#` 后面是期望输出。

---

## 1. 准备

- Python 3.8+（编译器本身用 Python 写；`-t py` 目标也需要它）
- JDK 17+（只在用 `-t java` 时需要）
- `numpy`（可选，只在第 6 节的例子里用）

```bash
git clone https://github.com/fghijkln/latent.git
cd latent
```

编译器是一个单文件 `latent.py`，用法：

```bash
python3 latent.py prog.lt -t py -o out --run      # 编译为 Python 并运行
python3 latent.py prog.lt -t java -o out --run    # 编译为 Java 并运行
```

`-o out` 指定输出目录，`--run` 表示编完直接运行。去掉 `--run` 就只编译不运行。

---

## 2. 第一个程序

`hello.lt`：

```latent
say "hello latent"
```

```bash
$ python3 latent.py hello.lt -t py -o out --run
hello latent
```

换 Java 后端，输出完全一样：

```bash
$ python3 latent.py hello.lt -t java -o out --run
hello latent
```

`say` 是打印语句，不需要括号。这是全教程最重要的约定：**同一份源码，
`-t py` 和 `-t java` 的运行结果一致**。编译器自带 20 个测试，
每次都在双后端之间逐字节对拍输出。

---

## 3. 变量与数字

```latent
x = 3
y = 4.5
say x + y        # 7.5
say x * 2        # 6
```

Latent 里**只有一种数字类型：float64**。整数值打印时不带 `.0`（`6` 而不是 `6.0`），
这是语言的规定，不是巧合。

```latent
say 7 / 2        # 3.5
say 7 % 3        # 1
say 2 ** 10      # 1024
say int(3.9)     # 3（向零截断）
```

## 4. 字符串与插值

```latent
name = "latent"
say "hello $name"            # hello latent
say "1 + 2 = ${1 + 2}"       # 1 + 2 = 3
say "a" + "b"                # ab（字符串拼接）
```

`$name` 插入变量，`${expr}` 插入任意表达式的值。

## 5. 列表与映射

```latent
xs = [1, 2, 3]
push(xs, 4)
say xs           # [1, 2, 3, 4]
say len(xs)      # 4

m = {"name": "latent", "v": 0.2}
say m            # {"name": "latent", "v": 0.2}
say keys(m)      # ["name", "v"]
```

`for` 可以遍历列表、字符串（逐字符）、映射（遍历键）：

```latent
total = 0
for i in [1, 2, 3, 4]:
    total = total + i
say total        # 10

for c in "hi":
    say c        # h / i（各占一行）
```

下标读值：`xs[i]`、`m[k]`、`s[i]`，负索引从末尾数：

```latent
xs = [10, 20, 30]
say xs[0]        # 10
say xs[-1]       # 30
m = {"name": "latent"}
say m["name"]    # latent
say "hello"[1]   # e
```

下标同样适用于库返回的东西：`json.loads(s)["tags"]`、`re.findall(p, t)[0]`、
Java 的 `ArrayList` 也能 `xs[0]`。只有读没有写（`xs[0] = v` 不支持）。

## 6. 控制流

```latent
n = 7
if n < 0:
    say "neg"
elif n == 0:
    say "zero"
else:
    say "pos"    # pos
```

```latent
i = 0
while i < 3:
    say i        # 0 / 1 / 2
    i = i + 1
```

`break` / `continue` 和你想的一样。真值规则同 Python：
`nil`、`false`、`0`、`""`、`[]`、`{}` 为假，其余为真。

## 7. 函数

```latent
fn fib(n):
    if n < 2:
        return n
    fib(n - 1) + fib(n - 2)

say fib(20)      # 6765
```

注意两件事：

1. **函数最后一个表达式就是返回值**，可以省略 `return`
  （`fib(n - 1) + fib(n - 2)` 这一行就是返回）。
2. 单行函数有简写：`fn add(a, b) => a + b`。

局部变量默认是 `nil`；在赋值之前读取局部变量是**编译期错误**
（比 Python 更严格，这是为了让 Java 后端也讲得通）：

```latent
fn f():
    say y        # 编译报错：local 'y' read before assignment
    y = 1
```

---

## 8. 类

```latent
class Point:
    fn init(self, x, y):
        self.x = x
        self.y = y

    fn move(self, dx, dy):
        self.x = self.x + dx
        self.y = self.y + dy
        return self

    fn sumsq(self):
        return self.x * self.x + self.y * self.y

p = Point.new(3, 4)
say p.sumsq()   # 25
p.move(1, 1)
say p.x         # 4
say p           # <Point object>
```

- `init` 是构造器，`Point.new(...)` 会自动调它；没写 `init` 时 `Point.new()` 得到空对象。
- 方法第一个参数收实例，按惯例叫 `self`（和 Python 一样是显式的）。
- 字段是动态的：`self.z = 1` 随时加；`==` 比的是 identity；暂无继承。

属性写和下标写也是 v0.3 新加的：

```latent
p.x = 10        # 字段赋值
xs = [1, 2, 3]
xs[0] = 99      # 下标赋值（负索引可用）
m = {"a": 1}
m["b"] = 2
```

更多例子见 [cookbook/classes.lt](cookbook/classes.lt)。

## 9. `py`：按需加载 Python

```latent
np = py "numpy"                # ① 这里什么都不发生
a = np.arange(6).reshape(2, 3) # ② 第一次使用时才 import numpy
say a                          # [[0 1 2]
                               #  [3 4 5]]（numpy 的打印风格）
say np.sqrt(2)                 # 1.4142135623730951
say np.pi                      # 3.141592653589793
```

关键语义（双后端一致）：

- `py "mod"` 得到一个**模块句柄**。在句柄上做第一次属性访问或调用之前，
  **不发生任何加载**。用 `-t java` 编译时，`python3` 进程甚至都不会启动——
  直到你真正调用 `np.arange` 那一刻。
- Python 返回的非 JSON 值（比如 ndarray）以**不透明句柄**存在，
  可以继续在上面调用方法、取属性、`say`。`==` 对句柄是 identity 比较。
- 调用 Python 时，整数值（`3.0`）以真正的 `int` 传入，非整数保持 `float`。
  这是为了让 `np.arange(6)` 这种要求真 int 的库正常工作。

验证懒加载的一个办法：把 `python3` 从 PATH 里拿掉，
只创建不使用的 `py` 句柄的程序依然能正常运行——
因为解释器从未被需要过。

## 10. `java`：按需加载 Java

和 `py` 完全对称：

```latent
A = java "java.util.ArrayList" # ① 这里什么都不发生（不 Class.forName）
a = A.new()                    # ② 构造：C.new(args)
a.add("hi")
a.add(42)
say a                          # ["hi", 42]
say a.size()                   # 2
say a.get(0)                   # hi

M = java "java.lang.Math"
say M.sqrt(2)                  # 静态方法：1.4142135623730951
say M.PI                       # 静态字段：3.141592653589793
```

规则：

- `C.new(args)` → 构造器；`C.m(args)` → 静态方法；`C.f` → 静态字段；
  `o.m(args)` / `o.f` → 实例方法 / 字段。
- 重载按"名字 + 参数个数 + 参数可转换"自动消解（含变长参数）。
  `Double` 按目标形参转 `int/long/double/...`（向零截断，同 `int()`）。
- **只有标量跨边界**：Java 的整数→`Double`、`String`→`String`、
  `boolean`→`Boolean`、`void`→`nil`；其他 Java 对象一律保持为不透明句柄，
  可继续调方法（比如 `a.add`）、`say`（按 Latent 风格打印）、`for` 迭代。
- Java 后端走**直接反射**（无守护进程）；Python 后端会懒启动一个 JVM
  守护进程（同样：不用就不启动）。

`py` 和 `java` 可以混用，两个后端结果一致：

```latent
np = py "numpy"
A = java "java.util.ArrayList"
a = A.new()
a.add(np.sqrt(2))
say a          # [1.4142135623730951]
```

## 11. 双后端是怎么回事

```
.lt → 词法 → 语法 → 脱糖 → 语义检查 → gen_py.py  → .py
                                          → gen_java.py → .java
```

- `-t py`：直译成 Python，附带一个 prelude（懒模块、JVM 客户端、repr 对齐）。
  用到 `java` 时，输出目录里会有 `LtJavaDaemon.java` 等文件，
  编译器有 javac 就当场编好，没有则在第一次使用时编译。
- `-t java`：全 `Object` 值模型 + `LtRt` 运行时。`py` 句柄懒启动
  `ltpy.py` 守护进程（JSON 行协议）；`java` 句柄直接反射。

两条铁律：**同一份源码双后端输出一致**；**用不上的那一端运行时根本不启动**。

## 12. 已知限制（v0.3）

- 无继承、无闭包捕获、无 `try`、无模块系统（`import` 其他 `.lt` 文件）。
- `py` 只支持模块句柄，不支持内联 Python 代码块。
- `java` 不支持字段赋值（`o.field = v`）、不支持基本类型类名（`java "int"` 不行）。
- 调用只有位置参数，没有关键字参数；函数不能当作值传递。
- 下标只有读（`xs[0]`），没有写（`xs[0] = v`）。
- 数字只有 float64 一种类型。
- 性能只求正确：Java 后端全装箱，跨语言调用走 JSON 行协议。够用，不快。

---

## 库 cookbook

`cookbook/` 里有 11 个可运行的例子，覆盖 Python（`math`/`datetime`/`json`/`re`/
`os`/`collections`/`random`/`itertools`）和 Java（`String`/`集合`/`time`/`nio`/
`BigDecimal`）常用库，外加一个双生态混用的例子。每个都在双后端验证过输出一致，
说明和坑点见 [cookbook/COOKBOOK.md](cookbook/COOKBOOK.md)。

---

## 下一步

- 想看完整语言定义：[SPEC.md](SPEC.md)
- 想看编译器实现：`lex.py → parse.py → desugar.py → semant.py → gen_py.py / gen_java.py`
- 跑测试：`python3 tests/run_tests.py`（41 个测试，双后端对拍）
