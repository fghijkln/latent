# Latent 库 Cookbook

`cookbook/` 里的每个 `.lt` 文件都是可运行的例子，全部在双后端（`-t py` / `-t java`）验证过输出逐字节一致。跑法：

```bash
python3 latent.py cookbook/py_json.lt -t py -o out --run
python3 latent.py cookbook/py_json.lt -t java -o out --run
```

`tests/run_tests.py` 会把全部 11 个例子自动双后端对拍。

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

## Java 生态

| 文件 | 类 | 要点 |
|---|---|---|
| `java_strings.lt` | `String`、`StringBuilder`、`Integer`、`Math` | `S.join/S.format` 静态方法；`StringBuilder` 链式 `append`；`parseInt/toHexString`；`pow/hypot` |
| `java_collections.lt` | `ArrayList/HashMap/HashSet`、`Collections` | 集合是句柄，用方法驱动；`Collections.sort(xs)` 原地排序；Java List 可直接 `for` 和 `xs[0]` |
| `java_time.lt` | `LocalDate`、`LocalDateTime` | `of/plusDays/plusHours/getDayOfWeek/isLeapYear`，链式调用 |
| `java_nio.lt` | `Paths`、`Files` | `writeString/readString/size/exists/deleteIfExists`，一行读写文件 |
| `java_bigdecimal.lt` | `BigDecimal` | 精确十进制：`0.1+0.2` 得 `0.3`；`divide` 的 scale/rounding 用位置参数传 |
| `mixed_io.lt` | 混用 | Python `open()` 写文件，Java NIO 读回来——两个生态在同一个程序里 |

## 互操作速查

- `py "mod"` / `java "com.foo.Bar"` 拿到句柄；`C.new(a)` 构造、`M.f(a)` 静态、`o.f(a)` 实例、`M.PI` 字段。
- 只有标量（数字/字符串/布尔/nil）和**精确类型**的 list/dict 直接过边界；其他一律不透明句柄（`Counter`、`datetime` 对象、`ArrayList` 等）。
- Python 的 tuple 过边界变成 Latent list（`say` 两边都打印成 `[...]`，索引通用）。
- `==` 在句柄上是 identity 语义。
- 调用只支持**位置参数**，没有关键字参数：`timedelta(1, 43200)` 可以，`timedelta(hours=36)` 不行。

## 已知的坑

1. **Latent 字符串是原生值，不是 Java 对象**：`"hi".toUpperCase()` 调不动。用 `S.join` / `S.format` 这类静态方法，或 `StringBuilder` 做拼接。
2. **内建函数不是一等值**：`collections.defaultdict(int)` 写不出来（`int` 传不进去）。v0.2 不支持把函数当值传。
3. **只有下标读，没有下标写**：`xs[0]`、`m["k"]`、`t["tags"][0]` 都可以（含负索引）；但 `xs[0] = v` 语法不支持，改列表用 `push`，改映射用 `update` 这类方法。
4. **Java 方法重载按"第一个能对上"的来**：`coerce` 会把 Latent 数字转成 `int/long/double` 等，歧义时别依赖重载解析。
5. **跨边界有 JSON 行协议开销**：循环里逐个调 Java/Python 方法优先保证正确，不保证性能。真要快，把循环写进对端的一次调用里。
