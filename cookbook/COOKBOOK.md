# Latent 库 Cookbook

`cookbook/` 有 18 个可运行入口 `.lt` 示例（不计被导入的辅助模块文件），全部在双后端（`-t py` / `-t java`）验证过输出逐字节一致。`named_arguments.lt` 展示 v0.11.0/P8 已发布语法；`variadic_arguments.lt` 展示 v0.13.0/P10 变参与调用展开。运行方式：

```bash
python3 latent.py cookbook/py_json.lt -t py -o out --run
python3 latent.py cookbook/py_json.lt -t java -o out --run
```

`tests/run_tests.py` 会把全部 18 个例子自动双后端对拍。

## Python 生态

| 文件 | 模块 | 要点 |
|---|---|---|
| `py_math.lt` | `math`、`statistics` | `floor/gcd/factorial/isclose`、`mean/median` |
| `py_math.lt` | `builtins` | `py "builtins"` 解锁 `list()/sorted()/sum()`，用来物化别的库返回的惰性迭代器；`enumerate` 返回的 tuple 过边界自动变 list |
| `py_datetime.lt` | `datetime` | `date/datetime/timedelta/strftime/strptime`；日期相减得 `timedelta`（不透明句柄，`.days` 照常读） |
| `py_json.lt` | `json` | `dumps/loads`；纯 dict/list 过边界仍是原生数据，可直接 `t["tags"]` 索引 |
| `py_re.lt` | `re` | `match/search/findall/sub/split`；Match 对象是句柄，用 `.group(n)` 取值 |
| `py_os.lt` | `os.path`、`pathlib` | `basename/join/splitext`、`Path.name/suffix` |
| `py_os.lt` | `collections` | `Counter.most_common()`、`deque.appendleft()`（dict 子类不再被拍扁，方法保留） |
| `py_os.lt` | `random`、`itertools` | 记得 `seed()` 保证确定性；`chain/count/islice/accumulate` 配 `builtins.list` 物化 |

## Java 生态与双生态

| 文件 | 类/生态 | 要点 |
|---|---|---|
| `java_strings.lt` | `String`、`StringBuilder`、`Integer`、`Math` | `S.join/S.format` 静态方法；`StringBuilder` 链式 `append`；`parseInt/toHexString`；`pow/hypot` |
| `java_collections.lt` | `ArrayList/HashMap/HashSet`、`Collections` | 集合是句柄，用方法驱动；`Collections.sort(xs)` 原地排序；Java List 可直接 `for` 和 `xs[0]` |
| `java_time.lt` | `LocalDate`、`LocalDateTime` | `of/plusDays/plusHours/getDayOfWeek/isLeapYear`，链式调用 |
| `java_nio.lt` | `Paths`、`Files` | `writeString/readString/size/exists/deleteIfExists`，一行读写文件 |
| `java_bigdecimal.lt` | `BigDecimal` | 精确十进制：`0.1+0.2` 得 `0.3`；`divide` 的 scale/rounding 用位置参数传 |
| `mixed_io.lt` | 混用 | Python `open()` 写文件，Java NIO 读回来——两个生态在同一个程序里 |
| `classes.lt` | Latent 类 | `Account` 存取款：`init`/`new`/方法/字段，`==` 为 identity |
| `inheritance.lt` | Latent 单继承 | `Dog(Animal)`、显式 `super.init`、覆盖方法与继承分派 |
| `functions.lt` | 函数值与闭包 | 函数作参数/返回值、词法捕获、`nonlocal` 计数器状态闭包 |
| `named_arguments.lt` | P8/v0.11.0 命名参数 | 命名函数调用、Latent 构造器与绑定方法调用；位置实参仍必须排在命名实参前 |
| `variadic_arguments.lt` | P10/v0.13.0 变参 | `*rest` / `**extra`、位置/映射展开与默认形参混用；仅 Latent 调用 |
| `pipeline.lt` | 旗舰 demo | 双生态销售管道：Python 写 CSV → Java NIO 读 → re 解析 → try/catch 跳坏行 → numpy 均值 → BigDecimal 求和 → java.time 时间戳 |

## 本地 `.lt` 模块

`modules.lt` 导入相对路径 `module_parts/greeting.lt`，再经别名访问公开函数和顶层变量：

```bash
python3 latent.py cookbook/modules.lt -t py -o out/py --run
python3 latent.py cookbook/modules.lt -t java -o out/java --run
```

两个后端均输出 `Hello Latent`。导入路径以声明它的 `.lt` 文件为基准，而不是生成产物的当前工作目录。模块细节与首版边界见 [SPEC.md §11](../SPEC.md#11-模块系统p2-v060) 和 [P2_MODULES_DESIGN.md](../P2_MODULES_DESIGN.md)。模块导入的公开类可作为继承父类；示例见 `inheritance.lt` 与 [P3_INHERITANCE_DESIGN.md](../P3_INHERITANCE_DESIGN.md)。

## 互操作速查

- `py "mod"` / `java "com.foo.Bar"` 拿到句柄；`C.new(a)` 构造、`M.f(a)` 静态、`o.f(a)` 实例、`M.PI` 字段。
- 只有标量（数字/字符串/布尔/nil）和**精确类型**的 list/dict 直接过边界；其他一律不透明句柄（`Counter`、`datetime` 对象、`ArrayList` 等）。
- Python 的 tuple 过边界变成 Latent list（`say` 两边都打印成 `[...]`，索引通用）。
- `==` 在句柄上是 identity 语义。
- v0.11.0/P8 为 Latent 函数/方法提供命名参数；Python/Java 互操作调用与内建函数仍只支持位置参数。例如对句柄写 `timedelta(hours=36)` 会被拒绝。
- v0.13.0/P10 为 Latent 调用增加变参及展开；内建函数和 Python/Java 互操作调用不接受 `*items` / `**mapping`。

## 已知的坑

1. **Latent 字符串是原生值，不是 Java 对象**：`"hi".toUpperCase()` 调不动。用 `S.join` / `S.format` 这类静态方法，或 `StringBuilder` 做拼接。
2. **只有用户定义函数是一等值**：普通函数和嵌套闭包可以赋值、传递、返回、间接调用；内建函数（如 `int`）、Latent 类方法和 Python/Java 句柄方法仍不可作为函数值。`collections.defaultdict(int)` 仍不能把内建 `int` 作为回调传入。
3. **下标写支持范围**：Latent 列表/映射、py 句柄，以及 Java `List`/`Map` 句柄都支持 `xs[0] = v` / `m["k"] = v`；字符串仍不可写。负索引适用于序列。
4. **Java 方法重载按“第一个能对上”的来**：`coerce` 会把 Latent 数字转成 `int/long/double` 等，歧义时别依赖重载解析。
5. **跨边界有 JSON 行协议开销**：循环里逐个调 Java/Python 方法优先保证正确，不保证性能。真要快，把循环写进对端的一次调用里。
6. **`.lt` 模块是静态子集**：只支持本地显式别名导入；不支持循环、通配/动态导入、包管理或写入导入模块的成员。
