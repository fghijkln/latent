# P14：Python 模块函数与 Java 静态方法值设计与实现记录

> **状态：P14 随 v0.13.5 正式发布。** 实施基线为 v0.13.4 / `d860c54c3fd4c1b75175a7e7e688e58601c04a73`，并保留先前 P13 实现与文档清理。

## 1. 摘要与目标

P14 将受支持的 **Python 模块函数值** 与 **Java 类句柄上的公开静态方法族**提升为 Latent callable 值。值可被赋值、作为参数传给 Latent 函数、从 Latent 函数返回、由逃逸闭包捕获，并以普通变量调用。现有直接写法 `module.function(args)` 和 `Class.method(args)` 继续走各自已有的直接互操作通路。

实现沿用 P13 的关键边界：不扩大 Latent callable 到宿主回调；不把任意远程对象句柄、任意 callable 属性或 property 结果视为函数；Java 重载保持在实际调用时消解；不新增语法或 AST schema。Python 函数在属性读取时捕获；Java 静态方法值捕获类及方法名、在每次调用时用实参进入现有反射 overload resolver。

```latent
host = py "p14_host"
scale = host.scale

fn apply(action, value):
    return action(value)

fn make_saved(action):
    fn saved(value):
        return action(value)
    return saved

saved_scale = make_saved(scale)
say apply(saved_scale, 4)

Fixture = java "P14InteropFixture"
classify = Fixture.classify
say apply(classify, "text")
say apply(classify, 7)
say Fixture.classify("direct")   # 原有直接调用路径不变
```

其中测试用 `P14InteropFixture.classify(String)` / `classify(double)` 表示同一静态方法族；实际重载以每次调用参数决定，不在属性读取时选定。

## 2. 当前代码证据与基线行为

| 主题 | 当前代码事实 | P14 含义 |
|---|---|---|
| 属性读取与直接点调用 | `desugar.py:209-236` 将 `x.y` 降为 `__wgetattr(x, "y")`，而 `x.y(args)` 降为独立的 `__wcall(x, "y", args...)`。`gen_java.py:699-712` 对这两种内部调用分别生成 `LtRt.wgetattr`、`LtRt.wcall` 或 `LtRt.callValue`。 | 已有表示足够新增宿主 callable 值，不需修改语法、节点或 schema；必须维持两条路径分离。 |
| Latent 间接调用 | Python 后端 `_wv_call` (`gen_py.py:625-638`) 目前接受 `_LtFunction` 和 P13 `_InteropBoundMethod`；Java 后端 `LtRt.callValue` (`runtime/LtRt.java:679-688`) 目前接受 `LtFunction` 和 `InteropBoundMethod`。 | 增加明确的 P14 callable 类型/分支，不把所有 Python `callable` 或 `__ref`/`__jref` 一概放行。 |
| Python 本地模块 | `_LazyMod` (`gen_py.py:19-33`) 在第一次属性访问时惰性 import；`_wv_wgetattr` (`gen_py.py:808-839`) 最终通过普通 `getattr` 返回模块成员。`_wv_wcall` (`gen_py.py:867-871`) 仍直接取模块属性并按位置调用。 | 属性读取目前可拿到函数对象，但该原值并非 `_wv_call` 接受的 Latent callable；直接调用不依赖新 wrapper。 |
| Java 类句柄本地反射 | Java 后端 `LtRt.wgetattr` (`runtime/LtRt.java:914-916`) 对 `JClass` 只调用 `JReflect.getField`；直接 `wcall` (`LtRt.java:968-974`) 对类句柄直接走 `JReflect.callStatic`。`JReflect.callStatic` (`runtime/JReflect.java:65-67`) 使用既有反射入口。 | P14 应只扩展 `JClass` 的属性读取和间接 `callValue`；直接调用及其 overload 算法不改。 |
| Java overload 解析 | `JReflect.callOn` (`runtime/JReflect.java:93-124`) 每次按当前类、方法名、静态/实例类别和当次参数生成候选、选择或报告无匹配/歧义；候选通过 `Class.getMethods()` 取得。 | 方法值只保存 class + name，不捕获某个 `Method` overload；每次 `invoke` 调用现有 `JReflect.callStatic`。 |
| Python daemon（Java 生成目标访问 Python） | `runtime/ltpy.py:24-27, 30-53` 以 `_objs` 保存非 JSON Python 值的强引用；`get_bound_method` 特意跳过 module target (`ltpy.py:157-173`)，普通 `get` 则 `encode(getattr(...))` (`ltpy.py:182-188`)。Java 客户端目前把此类返回值解为普通 `PyHandle`，而非 callable。 | 模块函数需有独立的 typed response/handle 和专用调用 op；不能把任意 `__ref` 改成可调用值。 |
| JVM daemon（Python 生成目标访问 Java） | `LtJavaDaemon` 的 `get` 对类目标只读取 `JReflect.getField` (`runtime/LtJavaDaemon.java:63-81`)；`call` 对类目标走 `JReflect.callStatic` (`:53-61`)。实例 P13 wrapper 使用结构化响应；daemon 对 Java 对象的 `_objs` 保留强引用到 quit (`:17-18, 251-256`)。 | 静态方法提取需有独立结构化 typed response/引用；daemon 上的 callable 描述强引用到退出，调用仍走 `JReflect.callStatic`。 |
| P13 的互操作保护 | Python `_wv_reject_interop_args` (`gen_py.py:446-452`) 与 Java `rejectInteropArgs` (`LtRt.java:454-462`) 拒绝命名和展开参数；`_wv_pyarg`、`_jvm_encode`、`LtRt.rejectInteropCallables` 分别保护 Latent callable 不跨边界 (`gen_py.py:291-309, 641-645`; `LtRt.java:463-480`)。 | 所有 P14 callable wrapper 必须并入现有保护，且对列表/映射内嵌值同样拒绝；错误类别和参数限制保持一致。 |
| Latent 自有模块与内建名 | 本地 Latent `import` 由 `module_system.py:253-326` 在编译期解析为内部符号；它不同于 `py "module"`。`semant.py:765` 保持内建函数（如 `len`）不可作为一等值。 | P14 是宿主互操作属性值，不改变 P2 模块语义、内建名规则或模块图改写。 |

P13 最近一次完整验证基线为 `234/234`；本次 P14 在该基线上增加正/负/协议/Cookbook 条目，v0.13.5 总计 **259 项**，完整验证结果见本记录末尾。

## 3. 范围、非目标与决策状态

### 3.1 用户已批准的 P14 约束

以下内容是本次实现的固定边界，不当作待确认事项：

1. Python 模块函数与 Java 公开静态方法族作为 Latent callable 值，可赋值、传参、返回、被闭包捕获并间接调用。
2. `module.function(args)`、`Class.method(args)` 等直接点调用维持既有路径与行为。
3. 宿主调用只接收位置实参；命名实参、`*`、`**` 均拒绝。
4. Latent callable 值不作为 Python/Java 宿主回调传入；含于列表/映射中的 callable 也不例外。
5. 不新增语法、AST 节点或 AST schema 版本。
6. 排除构造器值、任意 callable 对象/属性、property 或动态 descriptor 产物、以及独立内建函数（例如 `len`）。
7. 数据成员/字段先于方法值；Python 在读取时捕获函数对象；Java 捕获类 + 方法名、调用时解析 overload。
8. 显示稳定、不依赖宿主地址或随机值；daemon 持有的提取引用采用 P13 风格，无显式 release，强引用保持到 daemon 退出。

### 3.2 明确不做

- 不将任意 Python 可调用对象、Java `MethodHandle`、构造器或 Java 实例方法重新纳入 P14；实例绑定方法仍归 P13。
- 不将 Python module-level `__getattr__` 动态制造的任意 callable、Python module 子类的自定义描述符，或任意函数返回的 callable 自动升级。
- 不支持宿主关键字参数、Latent binder 形参推断、宿主 callback、异步调用或释放/引用计数协议。
- 不改变 Python/Java 直接调用的成员可见性、异常语义、daemon 启动时机、参数转换、Java overload 排序算法或 Latent 内建函数语义。
- 不把 Python 模块句柄或 Java 类句柄整体变成可调用值；只有符合准入的单个成员读取才得到 callable。

### 3.3 已批准并实现的三项语义决议

以下三项均在实施前由用户明确批准；表中内容是本次实现采用的固定语义，不再是建议或待确认事项。

| 决议 | 实现语义 | 结果 |
|---|---|---|
| **模块拥有的 C 函数**与独立 builtin 的边界 | 接受真实 `ModuleType` namespace 中精确导出的 `FunctionType`；也接受除 `builtins` 模块外，namespace 属性精确指向的 `BuiltinFunctionType` 且 `value.__self__ is module` 的 C 函数（如 `math.sqrt`）。显式拒绝 `builtins` namespace 中的独立 builtin（如 `builtins.len`），即使 CPython 将其 `__self__` 指向该模块；不使用宽泛的 `callable()` 判定。动态 `__getattr__` 产生但未导出到 namespace 的 callable 也拒绝。 | Python 本地与 Python daemon 使用同一精确分类；模块重导出的函数按读取模块 namespace 中的绑定捕获，读取后重绑定不改变已创建 wrapper。 |
| **Java 继承来的静态方法** | 纳入与直接调用完全相同的公开方法集合：使用 `Class.getMethods()`，并按直接 resolver 相同的 `static` / `bridge` 过滤；因此可提取继承的公开静态方法。 | `JReflect.hasPublicStaticMethod` 与 `callOn` 共用同一候选范围；静态接口成员行为亦遵循现有 resolver，不另造继承规则。 |
| **同名 Java 静态字段与静态方法** | 属性读取优先返回可读公开静态字段；仅静态字段未命中而存在符合直接调用范围的静态方法族时才返回方法值。显式 `Class.name(...)` 仍直走既有方法调用路径。 | 已在两个 daemon 方向和本地 Java getter 中实现字段优先；fixture 验证 `Fixture.collision` 得字段而 `Fixture.collision(...)` 仍调方法。 |

其余低风险实现细节沿用本记录所列 typed wrapper、独立 daemon 表与进程级强引用设计；没有新增语法、AST 节点或 schema 版本。

## 4. 已实施的值与调用语义

### 4.1 提取资格与成员优先级

- **Python：**仅 `py "module"` 产生的模块属性读取可触发本功能；先以现有属性语义读取成员，再只在值通过批准的精确函数分类时返回 P14 wrapper。普通数据、callable 实例值、自定义 descriptor/property 结果以及任意 `callable(value)` 命中均保留普通互操作值，不升级。模块 namespace 中的数据值优先于任何推测出的可调用性。
- **Java：**仅 `java "Class"` 类句柄上的公开静态方法族可提取。先读取公开静态字段；不存在字段时才检查公开静态方法。只考虑与 `callStatic` 一致的可见性、继承和 bridge 过滤规则。实例 `JObj` 仍遵守 P13，Latent 对象/类方法行为不变。
- 缺失成员维持各后端当前缺失属性/字段错误；不会因为“看起来 callable”而返回宽泛代理。提取类句柄成员时不得把 `Class.new(...)` 构造语法变成构造器值。

### 4.2 捕获、绑定与调用

- **Python 函数：读取时捕获。** 在 `module.function` 求值时保存当时读取到的那个真实函数对象。之后模块替换同名属性不改变既有值；wrapper 强持有该对象。远端 Python daemon 通过 typed id/table 保存精确函数对象，不仅保存属性名后再动态 `getattr`。
- **Java 静态方法族：读取时捕获类 + 名称，调用时解 overload。** Wrapper 不捕获具体 `java.lang.reflect.Method` overload。每次调用以原方法名和此次位置参数进入 `JReflect.callStatic` / 等价 RPC；当前 overload 选择、歧义与 no-match 诊断原样复用。
- 调用 wrapper 不附加 Python `self` 或 Java 接收者参数。参数沿 Latent 左到右求值规则生成后，按现有互操作转换规则传递；Python 参数错误与 Java no-match/ambiguity 通过现有宿主异常通路返回。
- 返回值仍走现有 JSON/native 转换或 opaque handle 编码。Python 函数返回一个 callable 对象、或 Java 静态方法返回一个 `Runnable` / 函数式对象，不代表返回值可间接调用；除非它本身是新的、符合提取规则的成员读取结果。

### 4.3 参数与 callback 边界

- `f(a, b)` 支持；`f(name=value)`、`f(*items)`、`f(**mapping)` 使用既有互操作 `ArgumentError` 类别与文案拒绝，不查询 Python 签名、不使用 Latent default/variadic binder，也不猜 Java 参数名。
- 传入 Latent `fn`、Latent 绑定方法、P13/P14 wrapper，或把它们放在列表/映射内传给任何 Python/Java 宿主调用，继续拒绝。新增 wrapper 必须纳入 Python `_wv_pyarg`、`_jvm_encode` 以及 Java `containsInteropCallable` 等递归识别点；不得漏掉 wrapper 类型。
- 互操作异常应映射到 **间接调用所在 `.lt` 行**；若发生在闭包内，映射到闭包的实际调用行。直接语法调用仍映射其原调用行。

### 4.4 稳定显示与错误

固定无地址的显示文本：

- Python module function：`<module function p14_host.scale>`（由模块导出名/函数 qualified name 形成稳定字符串；重导出命名规则按第 3.3 节决议）。
- Java static method family：`<static method P14InteropFixture.classify>`，使用 Java 二进制类名与方法名，不附 overload signature，因为值代表整族重载。

调用普通非 callable 字段/引用仍报既有 `call on non-function value`；命名/展开仍为既有互操作参数 `ArgumentError`；Latent callable 被宿主拒绝仍用现有 callback 错误类别；方法体抛错、Python 参数错误、Java no-match/ambiguous 错误沿用现有包装及源码映射机制。P14 不以吞错/模糊化诊断换取双后端文本完全相同；跨后端测试优先断言错误类别、稳定前缀和 Latent 源文件/行号。

## 5. 两个生成后端与两个 daemon 方向的实现触点

| 组合/触点 | 本次实现路径 | 保持的边界 |
|---|---|---|
| **Python 生成目标 × Python 模块（本地）** | `gen_py.py` runtime prelude 增加 `_InteropModuleFunction`，`_wv_wgetattr` 对 `_LazyMod` 按精确类型、模块 namespace identity 和 C function owner 条件分类并捕获真实函数；`_wv_call`、`_wv_repr`、`_wv_pyarg` 加入该类型。 | `_wv_wcall` 对 `_LazyMod` 的直接 `getattr(...)(...)` 路径不改；不把任意模块 callable/普通对象句柄传给 `_wv_call`。保留 `_LazyMod` 懒加载行为。 |
| **Python 生成目标 × Java 静态方法（经 `LtJavaDaemon`）** | `_JVM.req` 解析专用 `static_method` response 并构造 `_InteropStaticMethod`；`LtJavaDaemon` 的 `get` 对类句柄先读公开静态字段、再查公开静态方法，登记 `StaticMethodInfo`；`call_static_method` RPC 每次调用 `JReflect.callStatic`。 | Python wrapper 保存远端 typed ref；daemon 端 descriptor 强引用至进程退出。不要把 Java 静态方法编码成普通 `__jref` 对象，亦不在提取时选 overload。直接 `__wcall`/`op: call` 保持原样。 |
| **Java 生成目标 × Java 静态方法（本地 `JReflect`）** | `LtRt.wgetattr(JReflect.JClass, attr)` 先按 `hasPublicStaticField` 读字段，未命中再用 `hasPublicStaticMethod`；新增 `InteropStaticMethod` 保存 `JClass` 与名称，`LtRt.callValue` 间接调用复用 `JReflect.callStatic`。 | 不重写 `JReflect` 的转换、特异性、歧义算法；字段先于方法；`LtRt.wcall` 的静态直接调用不改。wrapper 不伪装成 `LtFunction`，避免误走 Latent binder。 |
| **Java 生成目标 × Python 模块函数（经 `ltpy.py`）** | Java `LtRt.wgetattr(PyHandle)` 与 `Daemon` 使用 `get_module_function` / `call_module_function`；`ltpy.py` 用精确模块函数谓词分配独立 `_module_functions` typed id 表并强引用捕获的函数；客户端 `PyModuleFunctionRef` / `InteropModuleFunction` 保存 id 与 display，`callValue` 专门分派。 | 普通 `get` 对非 JSON 值仍返回普通 `PyHandle`；不将 `__ref` 当函数。独立 typed 表到 daemon 退出才释放，不新增 release。直接 Python `pycall` 不变。 |
| **`gen_java.py` / 两个后端的生成表达式** | 既有生成表达式已经把属性读取写到 `wgetattr`、普通值调用写到 `callValue`、显式点调用写到 `wcall`；无需改生成器或 AST，仅扩展各运行时分派。 | 未修改 `parse.py`、`nodes.py`、`desugar.py`、AST schema；`obj.member(args)` 仍不改写为属性取值后调用。 |

两 daemon 协议都应采用专属 typed response/op 或内部 typed table，不借用用户可构造的普通 JSON map 形状冒充 callable。对未知、损坏或 stale typed id 采用清晰错误；daemon 消息不得允许任意反射表达式或任意 Python eval。

## 6. 跨后端 2×2 验证矩阵（已覆盖）

矩阵行是编译目标，列是宿主来源；`tests/p14_module_and_static_values.lt` 在 **四格**覆盖提取、变量赋值、Latent 函数参数传入、从 Latent 函数返回、闭包捕获并逃逸外层作用域、间接调用及同一 wrapper 重复调用。

| 生成目标 | Python 模块函数 | Java 公开静态方法族 |
|---|---|---|
| Python (`-t py`) | 本地 `_LazyMod` 路径：fixture `p14_host.py` 中纯 Python module function；验证读取时捕获，闭包返回后可调用，源函数/模块属性后续重绑不改变已捕获对象。C 模块函数按第 3.3 决议单独覆盖。 | `_JHandle`/`LtJavaDaemon` 类句柄路径：fixture overload（如 `classify(String)`、`classify(double)`）；同一个值依次传入不同参数类型，确认每次在 JVM 端 resolve。覆盖类名显示、静态字段冲突与远端 stale ref 错误。 |
| Java (`-t java`) | `PyHandle`/`ltpy.py` daemon 路径：module function typed ref 被提取并跨闭包保活；普通远端 `__ref` 仍不可调用；函数实参按现有 `_pyarg` 转换。 | 本地 `JReflect.JClass` 路径：同一 overload fixture 验证 property read、传参/返回/闭包捕获、逐次分派；字段优先和继承静态方法行为按已锁定选择测试。 |

矩阵实测断言：

1. 同一共享 Latent 程序分别以 `py`、`java` 生成运行，输出逐字节一致；宿主错误按稳定类型/前缀及 `.lt` 行核验。
2. 四格均在闭包创建所在外层函数已返回后调用 wrapper，避免只测试仍在栈上的局部变量。
3. Java overload 测试在同一捕获方法值上至少调用两个能够由现有 resolver 区分的重载，并验证 direct `Class.method(...)` 输出与 P14 前一致。
4. Python capture 测试在提取后替换模块属性，确认已捕获 callable 仍指向读取时的函数；随后再次读取可观察新绑定。
5. 两个目标后端都分别验证 Python host 与 Java host；覆盖本地路径及两个 daemon 路径。

## 7. 负向、兼容与回归用例

### 7.1 负向用例

- Python 模块 wrapper：分别用命名实参、`*`、`**` 调用（3 项）；Java static wrapper 同样 3 项。两 target 均运行，确保错误类别/位置一致。
- Host callback：拒绝 Latent 普通函数和 P13/P14 callable；P14 wrapper 覆盖直接传参、嵌套列表与嵌套映射，分别经 Python/Java host 并在两个目标后端运行，不能静默剥离 wrapper、发送伪 handle 或转换为 callback。
- 准入排除：`len` 仍不是一等值；Python 模块中的 callable 实例/数据属性、动态 `__getattr__` 返回值（若 fixture 有）、property/descriptor 产生的 callable 不被包装；Java 静态构造器不成为值；普通 Java/Python opaque callable 结果仍为非函数值。
- 成员优先级：Python 普通数据属性继续原样返回；Java 同名公开静态字段在属性读取中优先；direct `Class.collision(...)` 仍能调用同名静态方法族。无公开静态字段/静态方法时，保持既有缺失字段错误。
- 远端协议：普通对象 `__ref`/`__jref` 不能作为函数；非法/过期 typed ref 有确定错误；返回值内的 callable 不会升级。
- 源码定位：四个矩阵组合各至少一个间接调用失败，均报告源文件与调用 `.lt` 行；闭包内失败指向闭包调用行而非 helper/daemon。

### 7.2 兼容回归

- 保持所有 P13 bound instance method 正/负例及实例字段优先、daemon 强引用测试不变。
- 保持 direct `py_module.func(...)`、`JavaClass.staticMethod(...)`、`JavaClass.new(...)`、`object.instanceMethod(...)` 的输出、异常/重载及懒启动行为；尤其确认 direct call 不经 P14 wrapper。
- 保持 Latent `len` 等 builtin 值的编译期诊断、P5 函数值/闭包、P2 静态模块导出规则、AST v6 JSON/只读 CLI golden 输出不变。
- 现有无法调用的 Python 函数属性读取可能因稳定显示从宿主 repr 改为 P14 display；此为明确新增的可观察显示变化。非目标字段/普通对象值的显示不变。

## 8. 实施记录

1. **语义与协议已锁定。** 用户批准 module-owned C 函数准入、继承静态方法范围、同名静态字段优先；两个方向均使用独立 typed callable 与 daemon 表，不借用普通 opaque ref。
2. **四条运行路径已实现。** `gen_py.py` 本地 Python 模块函数和 Java static-method wrapper；`runtime/LtRt.java` 本地 Java 静态方法族和 Python module-function wrapper；`runtime/ltpy.py` 与 `runtime/LtJavaDaemon.java` 各自提供 typed-ref RPC 及进程级强引用。
3. **fixtures 与测试已加入。** `tests/python/p14_host.py`、`tests/java/P14InteropFixture.java` 和共享 Latent 正例覆盖四格、capture/rebinding、overload、字段优先、继承与显示；独立 static extraction positive 保留原 P13 exclusion fixture 并迁移到 P14。负例覆盖 named/`*`/`**`、callback（直接、嵌套列表/映射）、builtins.len 与其他 non-goals、stale typed id 及 source map。
4. **文档已同步。** SPEC、README、TUTORIAL 与 Cookbook 均记录 P14 的实际语义，并明确其随 v0.13.5 发布；Cookbook 示例在两个目标后端对拍。
5. **最终验证状态。** Python compile、Java runtime 与 P13/P14 fixture compile 均通过；编辑器元数据测试 **4/4** 通过；修复后的完整双后端回归 **259/259** 通过。VS Code `npm test` 首次运行时，其 embedded project regression 与测试文件迁移并发，导致 assertion 收到旧路径缺失；工作区稳定后重跑通过：tokenization 通过、普通工作区扩展套件 **12/12**（含嵌入式完整项目回归）通过、Restricted Mode **2/2** 通过，命令退出码 0。

## 9. 风险与缓解

| 风险 | 影响 | 建议缓解 |
|---|---|---|
| Python callable 分类过宽 | arbitrary callable、模块动态属性或独立 builtin 意外变成可调用值 | 用精确类型 + 所有权/模块 namespace identity 判定；绝不只看 `callable()`；显式排除 `builtins` namespace 内的 `len` 等独立 builtin。 |
| module function 被重新绑定 | 已提取 callable 随模块修改漂移，难以预测闭包行为 | Python 捕获真实函数对象；daemon ref table 保存对象本身，非模块名。 |
| Python/Java 后端准入不一致 | 同一 Latent 源码一端能取值、另一端失败 | Python local 与 daemon 使用同一规范函数 predicate 和拒绝案例；fixture 在两路径对拍。 |
| Java field/method 冲突 | 静态字段读取被新 wrapper 遮蔽，造成兼容破坏 | `getField` 优先；只在明确字段未命中时考虑方法族；direct `wcall` 不改。 |
| Java 继承/bridge 边界 | 把不可由 direct resolver 调用的方法包装，或漏掉已有 direct 能调用的方法 | 复用 `Class.getMethods()` 与 `callOn` 同样的 `static` / bridge 过滤；继承公开静态方法沿用直接调用集合。 |
| overload 在提取时提前绑定 | 参数类型不同的同名 overload 失去动态选择，或无参取值失败 | wrapper 只记录类和名称；每次调用复用 `JReflect.callStatic`。 |
| daemon ref 与普通对象 handle 混淆 | 一般 `__ref` 误作函数、callback 越界或 typed id 冲突 | 结构化专用响应字段和独立 typed table/op；严格检查类型、id、目标 daemon；保持到进程退出但不暴露任意 RPC。 |
| 长时间进程持有表增长 | 捕获很多函数值时 daemon 内存随之增长 | 明确继承 P13 首版到进程退出的生命周期；不在 P14 引入局部释放协议，未来若需则单独设计。 |
| 异常显示/source map 漂移 | remote exception 指向 runtime helper 而非源调用 | 所有 4 格各断言 `.lt` 文件/行；保留已有 cause/message，并与宿主堆栈全文解耦。 |
| 直接调用被 getter 改写 | 已有调用顺序、反射选择或惰性启动变化 | 不改 `__wcall`/`wcall`；加入 direct-call 输出/异常/overload 锁定测试。 |

## 10. 定量验收结果

本次实现的验收覆盖与测试计数如下（完整测试命令退出前，不将其标为通过）：

1. **矩阵覆盖：**2 个生成目标 × 2 种宿主来源 = **4/4 格通过**；每格至少覆盖提取、赋值、传参、返回、逃逸闭包捕获和间接调用，共至少 **24 个可观察行为断言**（可由少量端到端 fixture 承载）。
2. **overload / capture：**同一 Java wrapper 至少按两组实参选中 **2 个不同 overload**；Python 读取后重绑模块属性，旧 wrapper 仍调用旧函数、新读取取得新函数。
3. **限制覆盖：**位置参数专用拒绝至少 **6 项**（两类 host callable 各测 named / `*` / `**`）；Latent callback 直接/嵌套拒绝覆盖 **两种宿主及四个矩阵格**，无任何 callable 穿越边界。
4. **排除项：**至少 **5 类**不准入或兼容案例通过：builtin `len`、Python callable 数据属性/动态值、Java 静态字段冲突、构造器/非方法值、普通 remote opaque ref；实际 fixture 可按需要细分增加。
5. **显示与诊断：**Python/Java 各自 display 至少 **1 条精确稳定断言**；4 个矩阵格各至少 **1 个间接失败 source-map 断言**；不依赖对象地址或宿主堆栈全文。
6. **兼容与项目回归：**P13 及原语义基线为 **234 项**；P14 新增 **25 项净回归**（四格正例、22 个双后端 runtime 负例、1 个 typed-ref protocol 检查、1 个 Cookbook 示例；另将一个原 P13 Java-static exclusion case 迁移为 P14 extraction positive，不计净增），当前合计 **259 项，259/259 通过**。新增 `builtins.len` 负例明确锁定独立 builtin 排除；AST schema 保持 **v6**，未改只读 AST/脱糖输出。Python compile、Java runtime/fixtures 编译、4 项编辑器 metadata 测试，以及 VS Code `npm test`（普通工作区 12/12、Restricted Mode 2/2、tokenization）全部通过。

---

**发布结果：**P14 随 v0.13.5 发布；本记录保留实现范围、兼容边界与完整验收数据。
