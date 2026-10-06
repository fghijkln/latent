# Latent 语言规范 v0.5

> Latent（`.lt` 源码，`latent` 编译器）。
> 定位：通用小语言，独立项目。直观、代码少；一份源码可编译为 Java 或 Python。
>
> v0.2 变更：Python 与 Java 本体彻底纳入范围——`py "mod"` 与
> `java "com.foo.Bar"` 对称，两个后端都能直接用两个生态的库。
> v0.5.0：校准至 v0.4.0 已实现语义，并为未捕获运行期错误增加双后端 `.lt` 行号映射。

## 1. 设计目标

1. **直观**：缩进块、无分号、无括号 print、心智模型接近 Python 但更少仪式。
2. **代码少**：表达式导向（函数末表达式即返回值）、字符串插值、单数字类型。
3. **双后端**：同一份源码，`latent a.lt -t py` / `-t java`，语义一致。
4. **按需加载 Python 与 Java** 是一等语法：`py "numpy"` / `java "java.util.ArrayList"`
   一行即得懒加载句柄，双后端语义一致（用不上的那一端解释器/JVM 根本不启动）。

## 2. 词法

- 缩进敏感（空格，禁止 tab）。`#` 单行注释。
- 关键字：`fn if elif else while for in and or not true false nil py java say return break continue global`
- 字面量：数字 `42` `3.14`（**全语言只有一种数字类型：float64**），
  字符串 `".."` `'..'`（支持 `$name` / `${expr}` 插值），`true false nil`，
  列表 `[1, 2]`，映射 `{"a": 1}`（键为字符串）。
- 运算符：`+ - * / % **`，比较 `== != < <= > >=`，逻辑 `and or not`，
  `=` 赋值，`.` 属性访问（用于 py / java 句柄），`=>` 单行函数。

## 3. 语法（v0.5）

```
program  := stmt*
stmt     := assign | fndef | classdef | ifstmt | whilestmt | forstmt
          | saystmt | exprstmt | returnstmt | breakstmt | continuestmt
          | trystmt | throwstmt
fndef    := "fn" NAME "(" [params] ")" ( "=>" expr | ":" block )
classdef := "class" NAME ":" block        # v0.3 新增；block 内只能是 fndef
trystmt  := "try" ":" block "catch" NAME ":" block   # v0.4 新增
throwstmt:= "throw" expr                              # v0.4 新增
ifstmt   := "if" expr ":" block ("elif" expr ":" block)* ("else" ":" block)?
whilestmt:= "while" expr ":" block
forstmt  := "for" NAME "in" expr ":" block
saystmt  := "say" expr
assign   := NAME "=" expr
          | postfix "=" expr              # v0.3 新增：obj.attr = v / xs[i] = v
                                          # （postfix 为 Name/Dot/Subscript 时）
expr     := orexpr
orexpr   := andexpr ("or" andexpr)*
...
primary  := NUMBER | STRING | "true" | "false" | "nil"
          | NAME | list | map | call | "py" expr | "java" expr | "(" expr ")"
call     := primary "(" [args] ")" | primary "." NAME "(" [args] ")"
index    := primary "[" expr "]"          # v0.2.1 新增：下标读
```

- `for x in xs`：`xs` 为列表（或字符串，逐字符；或 Java List/数组）。
- `xs[i]` / `m[k]` / `s[i]`：下标读。整数索引（负数从末尾数，越界为运行期错误）；
  映射按 key 取值（key 不存在为运行期错误）；字符串取单字符。下标写见 §5：
  Latent 列表/映射及 py 句柄可写；Java `List`/`Map` 句柄可写；字符串不可写。

- `fn` 单行形式：`fn add(a, b) => a + b`。
- 函数末表达式即返回值（可省略 `return`）；`return` 用于提前返回。

## 4. 语义

- **动态类型**，无类型声明。`+` 按操作数重载：数字相加、字符串拼接、列表拼接；
  其他混用为编译期/运行期错误。
- **真值**：`nil`、`false`、`0`、`""`、`[]`、`{}` 为假，其余为真（同 Python）。
- **数字打印**：整数值不带 `.0`（`3` 而非 `3.0`），其余按最短往返表示。
- **say**：按语言自身的 `repr` 打印：`nil`、`true`/`false`、`[1, 2]`、`{"a": 1}`。
- **作用域**：函数内被赋值的名字是局部变量；读全局变量自由；
  局部变量在文本首次赋值之前读取是**编译期错误**（比 Python 更严格，避免
  Java 后端 definite-assignment 问题）。顶层变量即全局变量。
- **函数**：一等声明但 v0.1 不可作值传递；递归允许；参数按值传递
  （列表/映射为引用语义，同 Python）。
- **`.` 访问**：只允许出现在 py / java 句柄上，统一 desugar 为运行时调用
  `__wgetattr(h, "attr")` / `__wcall(h, "attr", args)`；运行时按句柄类型分发
  （py 句柄走 Python 语义，java 句柄走 Java 反射语义）。

## 5. 类（v0.3 新增）

```latent
class Point:
    fn init(self, x, y):   # 构造器；Point.new(args) 时自动调用
        self.x = x
        self.y = y

    fn move(self, dx, dy):  # 方法：第一个参数收实例（按惯例叫 self）
        self.x = self.x + dx
        self.y = self.y + dy
        return self

p = Point.new(3, 4)   # 构造：与 java 互操作的 C.new(args) 同一套写法
say p.move(1, 1).x    # 4（方法可链式，返回 self 即可）
say p                 # <Point object>
say Point             # <class Point>
```

语义保证：

1. `class` 块内只能是 `fn` 定义；方法名 `new` 保留（构造专用）。
2. `C.new(args)` 创建实例并调用 `init(self, ...)`（若定义了 `init`）；未定义
   `init` 时 `C.new()` 得空对象，带参数则为运行期错误。
3. 字段动态：`self.x = v` 即创建/赋值；`obj.x` 读字段，不存在为运行期错误。
   方法只能通过 `obj.m(args)` 调用（`obj.m` 不调用时不返回值，属未定义行为，
   不要依赖）。
4. 实例是独立的值：`==` 为 identity；真值恒真；`say` 打印 `<Point object>`。
5. 方法内名字规则与函数相同（参数、局部变量先读后写为编译期错误，可读全局）。
6. **无继承**（v0.3 不做，见 §10）。

`obj.attr = v` 与 `xs[i] = v`（v0.3 新增， desugar 为 `__wsetattr` /
`__wsetindex`）：

- Latent 实例：字段写；Latent 列表/映射：下标写（负索引可用，越界为运行期错误）。
- py 句柄：走 daemon `setattr` / `setitem`；java 句柄：字段赋值走反射
  `setField`，List/Map 句柄走 `set` / `put`。
- 字符串不可写；映射用 `m[k] = v`，不要用 `m.k = v`。

## 6. 错误处理（v0.4 新增）

```latent
try:
    risky()
catch e:
    say "failed: $e"   # e 是错误消息字符串

throw "boom"           # 主动抛错；catch 到的 e 就是 "boom"
```

语义保证：

1. `catch` 接住一切：Latent 运行期错误（下标越界、缺 key、读不到字段/方法）、
   `py` 调用的 Python 异常、`java` 调用的 Java 异常。
2. `e` 统一为**消息字符串**：Latent 自身错误的文案双后端一致
   （如 `index out of range: 5`）；跨生态异常的 `e` 是对方运行时的原文
   （可能带堆栈），两端不保证一致——测试断言时只断言固定字符串。
3. `catch` 变量在 `try` 前默认为 `nil`；未触发过 handler 时读取 `e`
   得 `nil`（双后端一致）。
4. `throw` 的值经字符串化后抛出；可出现在任何语句位置，可嵌套，
   handler 里可以再 `throw`。
5. 未被 `catch` 的运行期错误在 Python 与 Java 后端均尽量显示原始 `.lt` 文件、
   行号和 Latent 调用栈；暂不提供运行期列号。映射不可用时安全回退到后端原生
   traceback/堆栈。此诊断不改变 `catch e` 的消息字符串、跨生态异常原文或失败退出状态。

## 7. 按需加载 Python：`py`

```latent
np = py "numpy"          # 懒加载：此时不 import、不启动解释器
a = np.array([1, 2, 3])  # 第一次真正使用时才加载
say np.pi                # 属性读取同样懒加载
say a                    # 打印远端对象：[1 2 3]
```

语义保证：

1. `py expr` 求值为一个**模块句柄**，`expr` 必须是字符串（模块名）。
2. 在句柄上第一次做属性访问/调用之前，**不发生任何加载动作**。
   - Python 后端：`import` 推迟到第一次属性访问。
   - Java 后端：`python3` 解释器进程在第一次 py 调用时才启动（常驻守护进程，
     后续调用复用）；模块在守护进程内按需 `import` 并缓存。
3. 远端值：Java 后端下 Python 返回的非 JSON 值（ndarray、自定义对象等）
   以**不透明句柄**表示，可继续在其上调用/取属性/`say`（走远端 repr）。
   JSON 可直接表示的值（数字/字符串/布尔/nil/**精确类型**的 list/dict）在两端
   自动转回本地值；dict/list 的**子类实例**（如 `Counter`、`defaultdict`、
   `namedtuple`）保持为句柄以免方法丢失；tuple 转为 list（两端 `say` 一致）。
4. Python 出错 → Java 端抛运行时异常（带 Python 堆栈信息）；Python 后端直接透出。
5. **互操作数值规则**：调用 Python 时，整数值（`3.0`）以 `int` 传入、
   非整数值保持 `float`（numpy 等库要求真正的 int；双后端一致）。
6. **远端算术**：`+ - * / % **` 作用于远端对象时，通过 Python dunder
  （`__add__` 等）委托执行，双后端一致；`say` 远端对象走远端 `str()`。
7. **`==` 在远端对象上是 identity 比较**（同一句柄才相等），双后端一致；
   `<` 等比较不支持远端对象。

## 8. 按需加载 Java：`java`

```latent
A = java "java.util.ArrayList"   # 懒加载：此时不 Class.forName、不启动 JVM
a = A.new()                      # 构造：C.new(args)
a.add("hi")
a.add(42)
say a                            # ["hi", 42]
say a.size()                     # 2
M = java "java.lang.Math"
say M.sqrt(2)                    # 静态方法：1.4142135623730951
say M.PI                         # 静态字段：3.141592653589793
for x in a:                      # Java List/数组可迭代
    say x
```

语义保证：

1. `java expr` 求值为一个**类句柄**，`expr` 必须是字符串（完整类名；
   嵌套类用 `Outer$Inner` 写法）。
2. 在句柄上第一次做属性访问/调用之前，**不发生任何加载动作**。
   - Java 后端：`Class.forName` 推迟到第一次构造/调用/读字段；直接反射，无守护进程。
   - Python 后端：`java`（JVM）进程在第一次 java 调用时才启动（`LtJavaDaemon`，
     JSON 行协议，与 `ltpy.py` 对称）；类在守护进程内按需加载。
3. `C.new(args)` → 构造器；`C.m(args)` → 静态方法；`C.f` → 静态字段；
   `o.m(args)` / `o.f` → 实例方法/字段。重载按“名字+参数个数+可转换”
   消解（含变长参数）；找不到匹配时报运行时错误。
4. **边界类型规则**（双后端一致）：
   - Latent → Java：`Double` 按目标形参转 `int/long/double/...`（向零截断，
     同 `int()`）；`String`→`String`；`Boolean`→`boolean`；
     Latent 列表→`List` 形参直接传、`T[]` 形参逐个转换；其他按 `Object`。
   - Java → Latent：只有标量跨边界——整数类型→`Double`、`String`→`String`、
     `boolean`→`Boolean`、`char`→单字符字符串、`void`/`null`→`nil`；
     **其他一切 Java 对象（List/Map/数组/普通对象）保持为不透明句柄**，
     可继续在其上调方法/读字段/`say`（走 Java 侧格式化）/`for` 迭代。
     例外：`BigDecimal`/`BigInteger` 存在的意义就是不当 double，保留为句柄。
5. `say` Java 句柄：类句柄打印 `<class com.foo.Bar>`；对象句柄尽量按
   Latent 风格打印（List/Map/数组展开元素），否则走 `toString()`。
6. **`==` 在 java 句柄上是 identity 比较**（同一对象才相等），与 py 句柄一致；
   java 句柄的真值恒为真（非空）。
7. Java 出错 → 运行时异常（Java 后端直接抛；Python 后端带 Java 堆栈信息透出）。

## 9. 内建函数

| 函数 | 说明 |
|---|---|
| `say x` | 打印（语句形式） |
| `len(x)` | 列表/映射/字符串长度 |
| `range(n)` / `range(a, b)` | 列表 `[a, b)` |
| `str(x)` | 转字符串（同 say 的格式化） |
| `int(x)` | 向零截断取整 |
| `push(xs, x)` | 列表末尾追加（原地），返回 `nil` |
| `keys(m)` | 映射键列表 |

## 10. 编译器架构

纯 Python 标准库实现，四遍前端 + 双后端：

```
.lt → lex.py → parse.py → desugar.py → semant.py → gen_py.py → .py
                                                   → gen_java.py → .java (+ LtRt.java, JReflect.java)
```

- `lex.py`：缩进 token（INDENT/DEDENT，错误带行列号）。
- `parse.py`：递归下降 → AST（`nodes.py`）。
- `desugar.py`：字符串插值→拼接；`elif`→嵌套 if；`a.b`/`a.b()`→`__wgetattr`/`__wcall`；
  `say`→内建调用；函数末表达式→`return`。
- `semant.py`：未定义名字、函数签名 arity、局部变量先读后写、顶层顺序执行检查。
- `gen_py.py`：直译 + prelude（懒模块类、JVM 客户端、repr、远端语义对齐）。
- `gen_java.py`：全 `Object` 值模型 + `LtRt` 运行时；
  顶层变量→`static Object` 字段，函数→`static Object` 方法；
  py 互操作→`LtRt` 懒启动 `ltpy.py` 守护进程（JSON 行协议）；
  java 互操作→`JReflect` 直接反射（`Class.forName` 懒加载，无守护进程）；
  生成语句保留 `.lt` 行映射，用于未捕获异常诊断。
- `runtime/`：`LtRt.java`（Java 后端运行时）、`JReflect.java`（Java 反射互操作核心，
  Java 后端直调、Python 后端经 `LtJavaDaemon` 调）、`ltpy.py`（Python 守护进程）、
  `LtJavaDaemon.java`（JVM 守护进程，供 Python 后端用）。

## 11. v0.5 仍不支持（已记录，不算遗漏）

- 类的继承、`super`、类方法/静态方法、运算符重载。
- 闭包捕获、函数作值、模块系统（`import` 其他 .lt）。
- 关键字参数（`f(x=1)`）、`py` 内联代码块（`py:` 多行 Python 源码）。
- Java 基本类型类名（`java "int"`）不支持；`int[]` 等数组类名不支持
  （用 `java.util.ArrayList` 或 Latent 原生列表代替）。
- Java 后端数字目前全 `Double`；`int(x)` 语义两端一致即可。
- 性能：Java 后端装箱 + 守护进程 JSON-RPC 只求正确，不求快。

## 12. 示例

```latent
# fib.lt
fn fib(n):
    if n < 2:
        return n
    fib(n - 1) + fib(n - 2)

say fib(20)

np = py "numpy"
say np.arange(5)          # [0 1 2 3 4]
say np.sqrt(2)            # 1.4142135623730951
```

```latent
# 插值与循环
name = "latent"
say "hello $name, ${1 + 2 * 3}"

total = 0
for i in range(1, 101):
    total = total + i
say total                 # 5050
```

```latent
# java.lt — 两个生态一起用
np = py "numpy"
A = java "java.util.ArrayList"
a = A.new()
a.add(np.sqrt(2))
say a                     # [1.4142135623730951]
say a.size()              # 1
```
