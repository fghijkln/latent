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
- `numpy`（可选，只在第 10 节的例子里用）

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

需要检查前端树时，可输出带源码位置的 JSON，而不生成或执行 Python/Java 程序：

```bash
python3 latent.py tests/diagnostics_sample.lt --show-ast
python3 latent.py tests/diagnostics_sample.lt --show-desugar
```

`--show-ast`（也可写 `--dump-ast`）显示解析后的 AST；`--show-desugar` 显示脱糖结果，
例如属性读写会变成内部调用。诊断只处理指定文件，不遍历导入模块、不做语义检查或代码生成，
不会导入/启动 py 或 Java 互操作运行时，也不会改动 `out/` 或 `-o` 目录。错误仍带有原有的
`文件:行:列`、源码行和 `^`；诊断选项不能与 `--run` 一起使用。

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
`-t py` 和 `-t java` 的运行结果一致**。编译器的 82 个双后端/集成场景覆盖单文件/模块
正例、编译错误、运行错误与 cookbook，另有 7 个只读 CLI 诊断回归测试。

---

## 3. REPL：交互式

不想写文件？直接进交互式：

```bash
$ python3 latent.py repl
Latent REPL (python backend). Blank line ends a block; :reset clears; :quit exits.
lt> 1 + 2
3
lt> x = 10
lt> x * 3
30
lt> fn add(a, b):
...     return a + b
...
lt> add(3, 4)
7
```

- 裸表达式自动打印值，不用写 `say`；`say` 照常输出。
- `fn` / `class` / `if` 这些块，写完后**空一行**表示结束。
- 函数、类、变量的定义跨行保持；`:reset` 清空状态，`:quit`（或 Ctrl-D）退出。

报错会指到源码行（写 `.lt` 文件编译时也一样）：

```
lt> f(1, 2)
<repl>:1:1: semant error: undefined name 'f'
    1 | f(1, 2)
        ^
```

格式是 `文件:行:列: 阶段 error: 消息`，下面跟出错行和一个 `^`。

---

## 4. 变量与数字

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

## 5. 字符串与插值

```latent
name = "latent"
say "hello $name"            # hello latent
say "1 + 2 = ${1 + 2}"       # 1 + 2 = 3
say "a" + "b"                # ab（字符串拼接）
```

`$name` 插入变量，`${expr}` 插入任意表达式的值。

## 6. 列表与映射

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
Java 的 `ArrayList` 也能 `xs[0]`。Latent 列表/映射和 py 句柄可下标写；Java `List`/
`Map` 句柄也可写。字符串下标只读：字符串不可变。

## 7. 控制流

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

## 8. 函数

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

## 9. 类

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
- 字段是动态的：`self.z = 1` 随时加；`==` 比的是 identity。

Latent 类只支持**单继承**。子类覆盖方法时，普通方法的参数数量要和父类实现一致；构造器 `init` 可以增加参数。父类构造器不会自动运行，子类要显式调用它：

```latent
class Animal:
    fn init(self, name):
        self.name = name

    fn describe(self):
        "animal:${self.name}"

class Dog(Animal):
    fn init(self, name, breed):
        super.init(self, name)       # 只从直接父类开始查找；继承链上最近的 init 会运行一次
        self.breed = breed

    fn describe(self):
        super.describe(self) + " (${self.breed})"

dog = Dog.new("Milo", "shiba")
say dog.describe()                  # animal:Milo (shiba)
```

`super.method(self, ...)` 只在实例方法里有效，且必须把当前接收者作为第一个参数；它从当前类的直接父类开始找实现，不会按运行时子类重新分派。子类没有定义 `init` 时，会继承最近祖先的构造器。父类可在同一文件中稍后声明；模块也可用公开类作父类，例如 `class Worker(models.Base):`。

多继承、Java 类继承、类/静态方法和运算符重载不在本版本范围内。

属性写和下标写从 v0.3 起已支持：

```latent
p.x = 10        # 字段赋值
xs = [1, 2, 3]
xs[0] = 99      # 下标赋值（负索引可用）
m = {"a": 1}
m["b"] = 2
```

更多例子见 [cookbook/classes.lt](cookbook/classes.lt)。

## 10. 错误处理

```latent
try:
    data = fetch(url)
catch e:
    say "failed: $e"
    data = []

throw "balance < 0"   # 自己抛错
```

- `catch` 接住一切：Latent 运行期错误、`py` 的 Python 异常、`java` 的 Java 异常。
- `e` 是消息字符串；Latent 自身错误的文案双后端一致，跨生态异常的 `e`
  是对方运行时的原文（调试用）。
- 没触发过 `catch` 时读 `e` 得 `nil`；`try` 可嵌套。

未被 `catch` 的错误会以非零状态退出，并在两个后端显示 `.lt` 文件、行号和
Latent 调用栈，例如 `program.lt:12`。运行期暂不显示列号；若某个生成帧无法映射，
诊断会安全回退到 Python traceback 或 Java 堆栈。错误被 `catch` 后仍只把原有消息
字符串赋给 `e`，不会额外打印诊断。

---

## 11. `py`：按需加载 Python

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

## 12. `java`：按需加载 Java

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

## 13. 双后端是怎么回事

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

## 14. 多文件模块（P2）

把可复用代码放在单独的 `.lt` 文件中，再用显式别名导入。示例目录：

```text
app.lt
module_parts/
  greeting.lt
```

`app.lt`：

```latent
import "module_parts/greeting.lt" as greeting

name = "Latent"
say greeting.message(name)   # Hello Latent
say greeting.prefix          # Hello
```

`module_parts/greeting.lt`：

```latent
prefix = "Hello"
fn message(name):
    "$prefix $name"
```

从项目目录可用两个后端运行同一份程序：

```bash
python3 latent.py cookbook/modules.lt -t py -o out/py --run
python3 latent.py cookbook/modules.lt -t java -o out/java --run
```

两个命令都输出 `Hello Latent`。完整示例在 [cookbook/modules.lt](cookbook/modules.lt)，包含的库文件是 [cookbook/module_parts/greeting.lt](cookbook/module_parts/greeting.lt)。

规则：

- 写法只有 `import "相对路径.lt" as 名称`；每个文件的所有导入必须位于最前面，不可嵌在函数、类或控制流中。
- 路径相对**当前声明导入的 `.lt` 文件**解析，不看启动程序时所在的当前目录；路径必须相对且以 `.lt` 结尾。
- 通过 `别名.名字` 访问模块顶层变量、函数和类；例如函数写作 `lib.add(1)`，类用 `lib.Point.new(1, 2)` 构造。导入不会把模块内部名字泄漏到当前文件。
- 顶层以下划线开头的名字不可从其他模块访问；导入成员是只读的。需要更改模块状态时，应导出一个函数。
- 同一个文件即使使用不同路径写法和别名导入，也只初始化一次；被导入模块先于当前模块执行。循环导入会在编译期报错。
- 当前仅支持本地静态 `.lt` 文件导入，不支持通配符、动态导入或包管理。公开模块类可以作为父类，详见 §9。

## 15. 已知限制（v0.7.0）

- 仅支持 Latent 单继承；不支持多继承、接口、Java 类继承、类/静态方法或运算符重载。仍无闭包捕获；模块系统仅支持 §14 所述的本地静态 `.lt` 导入，不支持循环/通配/动态导入或包管理。
- `py` 只支持模块句柄，不支持内联 Python 代码块。
- `java` 不支持基本类型类名（`java "int"` 不行）。
- 调用只有位置参数，没有关键字参数；函数不能当作值传递。
- 数字只有 float64 一种类型。
- 性能只求正确：Java 后端全装箱，跨语言调用走 JSON 行协议。够用，不快。

---

## 库 cookbook

`cookbook/` 里有 15 个可运行的例子，覆盖 Python（`math`/`datetime`/`json`/`re`/
`os`/`collections`/`random`/`itertools`）和 Java（`String`/`集合`/`time`/`nio`/
`BigDecimal`）常用库，外加一个双生态混用的例子。每个都在双后端验证过输出一致，
说明和坑点见 [cookbook/COOKBOOK.md](cookbook/COOKBOOK.md)。

---

## 下一步

- 想看完整语言定义：[SPEC.md](SPEC.md)
- 想看编译器实现：`lex.py → parse.py → desugar.py → semant.py → gen_py.py / gen_java.py`
- 跑测试：`python3 tests/run_tests.py`（89 项：82 个双后端/集成场景和 7 个只读 CLI
  诊断回归；正例/cookbook 双后端对拍）
