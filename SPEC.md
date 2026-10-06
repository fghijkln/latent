# Latent 语言规范 v0.11.0

> Latent（`.lt` 源码，`latent` 编译器）。
> 定位：通用小语言，独立项目。直观、代码少；一份源码可编译为 Java 或 Python。
>
> v0.2 变更：Python 与 Java 本体彻底纳入范围——`py "mod"` 与
> `java "com.foo.Bar"` 对称，两个后端都能直接用两个生态的库。
> v0.6.0 在 v0.5.0 基线上发布 P2 本地模块与 P3 单继承；两项功能在 Python、Java 后端共用同一套静态语义。
> v0.7.0 增加只读的版本化 JSON AST 与脱糖诊断命令；不改变常规编译和运行行为。
> v0.8.0 在 v0.7.0 基线上增加用户定义函数值、嵌套词法闭包和 `global`。
> v0.9.0 增加 `nonlocal` 词法绑定；`latent-ast` JSON schema 升级到版本 2。
> v0.10.0（P7）允许 Latent 实例绑定方法作为函数值。
> v0.11.0（P8）增加 Latent 调用的命名参数；`latent-ast` JSON schema 升级到版本 3。
>
> **发布状态：** 本规范描述 v0.11.0 已发布语义。

## 1. 设计目标

1. **直观**：缩进块、无分号、无括号 print、心智模型接近 Python 但更少仪式。
2. **代码少**：表达式导向（函数末表达式即返回值）、字符串插值、单数字类型。
3. **双后端**：同一份源码，`latent a.lt -t py` / `-t java`，语义一致。
4. **按需加载 Python 与 Java** 是一等语法：`py "numpy"` / `java "java.util.ArrayList"`
   一行即得懒加载句柄，双后端语义一致（用不上的那一端解释器/JVM 根本不启动）。

## 2. 词法

- 缩进敏感（空格，禁止 tab）。`#` 单行注释。
- 关键字：`fn if elif else while for in and or not true false nil py java say return break continue global nonlocal import as super`
- 字面量：数字 `42` `3.14`（**全语言只有一种数字类型：float64**），
  字符串 `".."` `'..'`（支持 `$name` / `${expr}` 插值），`true false nil`，
  列表 `[1, 2]`，映射 `{"a": 1}`（键为字符串）。
- 运算符：`+ - * / % **`，比较 `== != < <= > >=`，逻辑 `and or not`，
  `=` 赋值，`.` 属性访问（用于 py / java 句柄），`=>` 单行函数。

## 3. 语法（v0.11.0）

```
program  := stmt*
stmt     := importstmt | assign | fndef | classdef | ifstmt | whilestmt | forstmt
          | saystmt | exprstmt | returnstmt | breakstmt | continuestmt | globalstmt | nonlocalstmt
          | trystmt | throwstmt
importstmt := "import" STRING "as" NAME
globalstmt := "global" NAME ("," NAME)*
nonlocalstmt := "nonlocal" NAME ("," NAME)*
fndef    := "fn" NAME "(" [params] ")" ( "=>" expr | ":" block )
classdef := "class" NAME ["(" parentref ")"] ":" block
parentref := NAME ["." NAME]              # 本地类，或 import 别名下的公开类
supercall := "super" "." NAME "(" NAME ["," args] ")"
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
          | NAME | "super" | list | map | call | "py" expr | "java" expr | "(" expr ")"
call     := primary "(" [args] ")" | primary "." NAME "(" [args] ")"
args     := argument ("," argument)*
argument := expr | NAME "=" expr
index    := primary "[" expr "]"          # v0.2.1 新增：下标读
```

P8/v0.11.0 中，位置参数只能出现在命名参数之前；出现命名参数后再出现位置参数是解析错误。`super.method(self, ...)` 的显式接收者仍必须是第一个位置实参。

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
- **作用域**：函数内被赋值的名字是当前函数局部变量；模块顶层变量是模块全局。
  当前函数的局部变量在首次赋值前读取仍为**编译期错误**。嵌套函数按词法链解析
  自由名字，并捕获其所属调用帧中的共享可变绑定；闭包创建后，外层对该绑定的后续
  赋值会被所有捕获它的闭包观察到。嵌套函数自身若给同名名字赋值，则该名字是它的
  局部变量；若要写外层绑定，须依照 P6 `nonlocal` 规则显式声明。
- **函数值（P5）**：用户定义的普通函数可赋值、作为参数传递、作为返回值及经变量调用；
  支持嵌套函数、闭包、递归与词法遮蔽。公开模块函数也可通过限定名取作函数值，仍按
  模块隔离及导出/私有规则解析。函数值的 `say` 表示为 `<function 名称>`。
- **`global`（P5）**：函数内的 `global x, y` 将所列名字绑定到其模块级名字空间；
  对同名外层局部变量也优先写/读模块全局，不捕获该外层绑定。名字须在模块作用域中
  声明。该声明作用于整个函数体（包括其控制流块），不穿透到内嵌函数；内嵌函数可
  自己声明 `global`。
- **`nonlocal`（P6，v0.9.0）**：函数内的 `nonlocal x, y` 将读取和所有赋值
  绑定到最近的、词法外层函数中实际具有该局部绑定的作用域；搜索跳过未绑定该名的
  中间函数作用域。参数、局部赋值、循环目标、`catch` 变量和嵌套函数名都可提供外层
  绑定。目标不存在时为编译期错误；声明与同函数参数、`global` 或重复声明冲突也为
  编译期错误。赋值给声明名本身不是冲突。声明只作用于当前函数，不穿透到嵌套函数；
  嵌套函数要写该绑定时必须自行声明。模块顶层拒绝 `nonlocal`，类体也拒绝（类体只
  接受方法定义）；类方法仍是独立函数作用域。
- **Latent 绑定方法值（P7，v0.10.0）**：读取 `obj.name` 时先查实例字段；字段
  不存在才沿运行期类及父类查找方法，并返回捕获该实例的绑定函数值。`self` 不出现在
  显式参数中；继承方法绑定到实际子类实例，覆盖方法按最近实现选取。绑定值可赋值、
  传参、返回、被闭包捕获并间接调用；显示为 `<bound method Class.method>`，其中 `Class`
  是接收者的运行期类名。属性读取若字段和方法都不存在，仍报未定义字段错误。
- **直接分派与外部方法边界**：`obj.name(...)` 仍走原有专用方法分派，不受实例同名字段
  遮蔽影响；`obj.name = value` 仍写字段。内建函数以及 Python/Java 句柄的方法仍不是
  函数值；`super.method(...)` 也不作为值。
- **`.` 访问**：py / java 句柄成员按既有规则脱糖为 `__wgetattr(h, "attr")` /
  `__wcall(h, "attr", args)`；模块命名空间成员则在编译期按导入别名静态解析，
  不构造运行时模块对象（见 §11）。Latent 实例的裸 `Dot` 读取沿用 `__wgetattr`，
  显式方法调用仍沿用 `__wcall`；P7/v0.10.0 未改变 AST v2 形状。P8/v0.11.0 新增 `NamedArg` AST 节点，因此 `latent-ast` schema 升为 v3，既有节点字段形状不变。

### P8/v0.11.0：命名参数

命名参数适用于用户定义普通函数、函数值/闭包、公开模块函数、Latent 直接与绑定方法，以及 `C.new(...)`/Latent `init`。绑定方法的签名不含隐式 `self`；继承或覆盖调用按最终选中的 Latent 实现使用其形参名。所有必需形参必须恰好收到一个值，不支持默认值、可变参数或仅限关键字参数。

已知签名的直接函数、公开模块函数、可静态判定的 Latent 方法、`super` 与构造调用在编译期检查重复、未知、缺失和多余实参；函数值及无法静态判定接收者的调用在运行期用同一绑定规则检查。两后端对运行期参数绑定错误统一报告 `ArgumentError` 和相同文案。实参表达式依源代码从左到右求值，绑定不会重排或提前求值。

内建函数以及 Python/Java 句柄方法和构造器仍是位置参数专用；对它们传入命名参数会明确拒绝，不会把名称转交到互操作目标。使用命名参数语法的源码需要 v0.11.0 或更新版本；v0.10.0 仍只支持位置参数。

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

v0.10.0 允许把实例方法取作值：
`step = p.move` 会捕获 `p`，此后 `step(1, 1)` 与 `p.move(1, 1)` 调用同一方法；
读取时同名实例字段优先，显式方法调用则继续走既有分派。

语义保证：

1. `class` 块内只能是 `fn` 定义；方法名 `new` 保留（构造专用）。
2. `class Child(Parent):` 表示单继承，父类必须是已声明的 Latent 类；声明顺序可
   前后不限。允许通过 P2 的模块别名继承公开类：`class Child(models.Base):`。
   未知/非类父引用、私有模块类、自继承和传递循环均为编译期错误。
3. 实例方法按最具体类优先查找，未覆盖的方法沿父链继承。普通方法覆盖必须保留
   参数数量（含显式接收者）；`init` 可增加或调整参数，构造调用按实际选中的
   `init` 校验。
4. `C.new(args)` 创建一个 C 实例。若 C 自己定义 `init`，只调用 C 的实现；父构造器
   不会隐式运行。C 未定义 `init` 时继承最近祖先的实现；没有任何 `init` 时，
   `C.new()` 创建空对象，有参数则为运行期错误。
5. `super.m(self, args...)` 仅能在有父类的实例方法中使用，必须显式把当前方法的
   第一个参数作为接收者；它从当前类的直接父类开始找最近实现，不会动态分派回子类。
   `super` 不是值，不能访问字段、保存或传递。
6. 字段仍动态地保存在同一个实例上：`self.x = v` 即创建/赋值；`obj.x` 读取字段，
   不存在为运行期错误。实例相等仍按 identity、真值恒真，打印使用源类名。
7. 方法内名字规则与函数相同（参数、局部变量先读后写为编译期错误，可读全局）。

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

### 只读 CLI 内省（P4）

```bash
python3 latent.py prog.lt --show-ast       # --dump-ast 是同义别名
python3 latent.py prog.lt --show-desugar
```

两种模式向 stdout 输出 UTF-8 JSON，文档标识为 `format: "latent-ast"`、`version: 2`；
包含阶段名、源文件路径，以及显式定义字段的节点树。每个源节点都记录 1 起算的行、列；
没有语法位置的 `Program` 容器使用 null。版本 2 新增 `NonlocalStmt` 变体；已有节点的
字段顺序、名称与 JSON 形状和格式版本 1 完全相同。未来变更不兼容结构时必须升级版本号。
非有限浮点字面值以 `"inf"`、`"-inf"` 或 `"nan"` 字符串表示，以保持合法 JSON。

`--show-ast` 只词法分析并解析指定文件；`--show-desugar` 再运行脱糖变换。两者均不做语义
检查、不遍历模块依赖、不生成或执行 Python/Java 程序、不加载或启动 py/JVM 互操作运行时，
也不创建或改动常规编译目录（包括给定的 `-o`）。`-t`、`-o` 在诊断模式下忽略；`--run`
不能与诊断选项组合。词法、语法与脱糖错误沿用结构化编译错误路径（文件、行、列、源码行、
插入符），退出状态非零。

## 11. 模块系统（P2，v0.6.0）

```latent
# app.lt
import "lib/math.lt" as math
say math.add(2, 3)
say math.answer
box = math.Box.new(7)
```

```latent
# lib/math.lt
answer = 42
fn add(x, y):
    x + y

class Box:
    fn init(self, value):
        self.value = value
```

语义保证：

1. `import "相对路径.lt" as 名称` 是唯一的 Latent 模块导入形式；`import`、`as` 是保留关键字。导入声明必须位于文件顶部且在所有普通代码之前，不能放进函数、类或控制流块；因此旧代码若曾将这两个词用作标识符，需改名。
2. 路径相对**声明该导入的文件所在目录**解析，而不是相对进程当前工作目录；必须是相对路径且以 `.lt` 结尾。`..` 可用于父目录。不存在/不可读的文件为编译期错误。
3. 每个模块通过导入别名访问：`名称.字段` 读取顶层变量，`名称.函数(...)` 调用顶层函数，`名称.类.new(...)` 构造顶层类。P5 起，公开的 `名称.函数` 也可作为用户定义函数值赋值、传参、返回和间接调用；模块别名本身仍不是值、不可调用，命名空间成员仍不能被赋值。
4. 顶层变量、函数和类属于各自模块；不自动注入导入方的名字空间。除以下划线开头的顶层名字外，顶层变量/函数/类均可通过别名访问。以下划线开头的名字为私有，导入方访问会在编译期报错。
5. 同一文件内别名必须唯一，且不能与该文件的局部绑定冲突。模块成员以只读导入视图暴露；要修改状态，应由模块导出的函数提供操作。
6. 编译器以规范化后的真实文件路径识别模块。同一个文件即使经不同相对路径、以不同别名导入，也只解析、生成并初始化一次；不同模块的同名函数、类或变量彼此隔离。
7. 模块图在编译期静态解析并打包为一个 Python 或 Java 编译单元。运行时初始化遵循依赖优先：首次初始化导入方时先初始化它的依赖，再运行其顶层语句；每个模块成功初始化后缓存结果，不会因第二个别名重复执行副作用。每次启动一个新程序时缓存重新开始。
8. 循环导入暂不支持，在编译期拒绝并报告回边导入位置；缺失文件、私有/不存在成员及其他编译错误尽量指向相关 `.lt` 文件和行列。未捕获运行期错误沿用 §6 的多文件行映射。
9. 不支持通配导入、包管理、动态导入、从 Python/Java 导入 Latent 模块或把模块命名空间作为普通对象传递。
10. 模块公开类可作为父类使用，例如 `class Worker(models.Base):`；模块别名、公开/私有检查与普通 `alias.Class` 访问相同。依赖模块的父类元数据会先于子类初始化。

P2 模块与 P3 继承一起包含在 v0.6.0 中。双后端维持相同的模块初始化、命名空间可见性及父类分派语义。

## 12. 仍不支持（已记录，不算遗漏）

- 多继承、接口、Java 类继承、类/静态方法、运算符重载。
- 类方法/内建函数/Python 与 Java 句柄方法的一等值化；模块系统之外的包管理、通配/动态导入仍不支持（见 §11）。
- 默认参数、可变参数、仅限关键字参数；`py` 内联代码块（`py:` 多行 Python 源码）。
- Java 基本类型类名（`java "int"`）不支持；`int[]` 等数组类名不支持
  （用 `java.util.ArrayList` 或 Latent 原生列表代替）。
- Java 后端数字目前全 `Double`；`int(x)` 语义两端一致即可。
- 性能：Java 后端装箱 + 守护进程 JSON-RPC 只求正确，不求快。

## 13. 示例

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
