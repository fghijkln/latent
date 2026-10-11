# P13：互操作绑定实例方法值（实施记录）

> **状态：P13 随 v0.13.5 正式发布。** 实施基线为 v0.13.4 / `d860c54c3fd4c1b75175a7e7e688e58601c04a73`；下文保留批准的设计与实施触点，并记录代码路径和验证结果。

## 1. 摘要

P13 实现让从 **Python 或 Java 对象实例**读取到的受支持绑定实例方法成为 Latent callable value。该值可赋给变量、传给 Latent 函数、从 Latent 函数返回、由闭包捕获，并通过普通间接调用语法调用。

```latent
ArrayList = java "java.util.ArrayList"
items = ArrayList.new()
append = items.add

fn apply(action, value):
    action(value)

apply(append, "one")
say items.size()                 # 1
```

直接调用 `items.add("one")` 仍使用既有互操作直接调用路径。Python 对象上的提取和调用应提供等价能力。Java 重载在每次实际调用时，仍由当前 `JReflect` 转换与候选选择规则处理。

### 目标

- 只增加“已提取的绑定实例方法值”这一种 callable 值来源。
- Python / Java 两种目标后端上的行为尽量对齐。
- 保留现有点号语法、稳定 AST schema v6、实参从左到右求值、错误源码位置映射及直接互操作调用行为。

### 非目标

不因 P13 自动增加：

- Python 模块函数、Java 静态方法的一等值化。
- Python 或 Java 构造器的一等值化；既有直接构造写法继续有效。
- 独立的内建函数（例如 `len`、`int`）或无接收实例的 Python builtin function 的一等值化。
- 任意 callable 字段、任意 callable property、任意返回 callable 的 descriptor 的一等值化。
- 将 Latent 函数、闭包或其他 Latent callable 作为回调传过互操作边界。
- 互操作目标的关键字参数、`*` / `**` 展开或 Latent 形参签名推断。
- 语法、AST schema、模块系统、反射可访问性或 Java 重载算法的改变。

## 2. 实施前基线行为与设计依据

1. **读属性与直接方法调用在脱糖后是不同操作。** `desugar.py` 把 `obj.member` 改写为 `__wgetattr(obj, "member")`；把 `obj.member(args)` 改写为 `__wcall(obj, "member", args...)`，并保留 `direct_method` 标记。已有 AST `Dot` / `Call` 足够表达 P13 实施语义，不需要新增语法或节点。
2. **实施前，Python 后端只对 Latent 实例读取构造绑定方法包装器。** `gen_py.py` 的 `_wv_wgetattr` 在 `_LtObj` 上先查 `_lt_fields`，再找 Latent 方法并构造 `_LtFunction`；对 Python 普通对象则返回 `getattr(h, attr)` 的原值。`_wv_wcall` 对 Python/Java 互操作保持直接调用，`_wv_call` 当时只接受 `_LtFunction`。
3. **实施前，Java 后端也把 Latent 绑定方法包装成 `LtFunction`，但 Java 互操作属性读取只做字段读取。** `runtime/LtRt.java` 的 `wgetattr` 对 `JReflect.JObj` 调用 `JReflect.getField`；互操作直接调用走 `wcall`。变量式调用的 `callValue` 当时只接受 `LtFunction`。
4. **Java 实例反射入口可复用。** `runtime/JReflect.java` 的 `call(JObj, method, args)` 调用 `callOn`，按目标运行时类、方法名、实例/静态类别和当次实参数值构造候选，再应用现有转换等级、特异性与歧义诊断；`getField(JObj, field)` 只读取公开字段。P13 不应在方法提取时选定某个重载。
5. **两种远端句柄都有按进程存活的 opaque-reference 表。** Java 后端访问 Python 的 `PyHandle` 经 `runtime/ltpy.py` JSON 行协议；`get` 通过 `getattr` 返回值，非 JSON 原生值编码为 `__ref`，`call` 仍是“对 target 的 attr 取值后位置调用”。Python 后端访问 Java 的 `_JHandle` 经 `LtJavaDaemon` 与 `JReflect`；daemon 的 `get`、`call` 各自独立处理属性/字段和直接方法，返回 Java 对象时用 `__jref`。
6. **互操作实参边界已有位置专用保护。** Python 与 Java 运行时的 `rejectInteropArgs` / `_wv_reject_interop_args` 会拒绝带名称或展开标记的互操作实参。P13 应在间接互操作调用中沿用它们，不把 Latent binder 应用于外部方法。
7. **错误定位可复用现有机制。** 生成器对 `.lt` 语句保留源码行映射；Python 后端 `_wv_report_uncaught` 从生成代码 traceback 映射回 `.lt`，Java 后端生成运行时 traceback 时也映射至 `.lt` 行。间接调用抛错应归属调用 callable 的 Latent 源码位置。

## 3. 实施语义

以下规则是第 9 节所列、已由用户批准并在本次工作区实现的首版默认值。

### 3.1 提取范围与成员优先级

`value = receiver.name` 只有在 `receiver` 是 Python/Java **对象实例句柄**，且 `name` 经对应语言的规则识别为绑定实例方法时，才产生 P13 callable wrapper。

- **字段/实例属性优先。** 若该名称先解析为字段或普通属性值，则保持既有属性读取结果；不把该值仅因“可调用”而包装。字段同名遮蔽方法时，间接调用该字段值仍按普通非函数值行为失败。Java 类句柄上的静态字段读取和静态方法读取不产生 P13 方法值。
- **Python 对象：**只接受由类上的普通实例方法描述符绑定到当前实例而得到的 bound method。读取结果的绑定接收者必须是该对象本身。可包含明确具有当前实例 `__self__` 的 Python 原生 bound method，以及满足同一判据的 C 实现 bound instance method；不包含 `staticmethod`、`classmethod`、模块函数、任意实例 callable 字段、`property` 的 callable 返回值或无法确认为普通方法绑定的自定义 descriptor 返回值。
- **Java 对象：**仅对公开、非静态实例方法建立方法值。先尊重已有可读公开字段；若无字段但存在一个或多个同名公开实例方法，则建立方法族值。静态方法、构造器和不可见/非公开成员不在范围内。
- **属性不存在且没有受支持方法时：**沿用目标后端当前的属性不存在错误；P13 不改变反射权限，也不引入 `getX` / property 命名约定。

提取只适用于 `py` / `java` 对象实例句柄，不适用于 `py "module"` 模块句柄、`java "Class"` 类句柄或 Latent 类方法（后者的现有绑定值语义不变）。

### 3.2 callable 值、调用和生命周期

- 方法值可赋值、传入 Latent 函数、返回、捕获在一个或多层闭包中，并由变量调用；接收者在提取时捕获，调用者不再提供接收者。
- **强引用：**wrapper 在其自身可达期间保持接收者可用。Python 本地路径保留 bound method/receiver 强引用；Java 本地路径持有 Java 对象 wrapper；daemon 路径保留已有 opaque id/对象表引用。句柄和 callable wrapper 的生命周期不应因源变量重新赋值而失效。当前 daemon 对象表已维持引用至 daemon 退出，首版接受该既有回收边界，不另造释放协议。
- **Python 捕获时间：**在取值时获得并保留真实 bound method 对象，而非每次调用都重新执行属性/descriptor 查找。这符合 Python 的 `bound = obj.method` 读取语义，并使后续类属性变动不偷偷改写已捕获的 callable。
- **Java 捕获内容：**wrapper 在取值时保留接收者及方法名，不捕获某个反射 `Method` 重载。每次调用按当次实参进入原有 `JReflect.call` 分派路径；相同对象上的 Java 运行时类稳定，重载适用性则随实参而定。
- Java 与 Python 目标返回的结果继续走现有边界编码/标量转换/opaque-object 规则。一个方法返回的 callable 对象不因此自动成为可调用的 Latent 方法值；需要重新读取某个对象上的方法，才进入本规则。

### 3.3 位置参数协议

- P13 callable 采用互操作现有的**位置参数专用协议**。`f(a, b)` 按源码顺序把值传给宿主方法。
- `f(name=value)`、`f(*items)`、`f(**mapping)` 在调用 P13 wrapper 时，以既有一致的互操作 `ArgumentError` 文案拒绝；不查询 Python 函数签名、不猜 Java 参数名、不使用 Latent 默认值、`*rest` 或 `**extra` binder。
- 被调 Python/Java 运行时自行执行形参数量及宿主类型检查；Python 参数错误与 Java “无匹配重载”/歧义错误沿现有互操作错误路径传回。
- `obj.method(args)` 继续走原来的直接互操作调用通路，包含现有实参求值顺序、反射选择和错误行为。属性取值后再调用必须是新增的间接通路。

### 3.4 显示、错误和源码位置

- 推荐显示形状为 `<bound method <qualified-type>.<member>>`，与 P7 Latent 绑定方法一致但可识别宿主类型。例如 Python 使用稳定的 `module.qualname`，Java 使用现有二进制类名。远端 daemon 在创建 wrapper 时提供必要的类型/方法元数据，而不是让 Python/Java 后端各自猜名字。
- 试图调用未包装的普通字段/值仍报告现有 `call on non-function value`。互操作 wrapper 对命名实参及展开实参继续报告现有 `ArgumentError`；缺少属性沿用现有访问错误；方法体异常、Java 无匹配/歧义异常沿现有通路传递。
- Python 与 Java 后端必须将未捕获调用失败映射到**间接调用表达式**所在 `.lt` 行；闭包中调用时，映射到闭包内该调用的源行。P13 不扩展或更改异常栈格式的其余部分。

## 4. 实际实现触点

| 触点 | P13 方向 | 明确不需要 |
|---|---|---|
| `parse.py`、`nodes.py`、`ast_dump.py` | 不变。既有 `Dot` 读、`Call` 和间接函数调用节点足够。 | 新 token、节点、AST schema 升版 |
| `desugar.py` | 预期不变。仍将属性读改写为 `__wgetattr`，将显式 `obj.name(...)` 改写为 `__wcall`。 | 把所有 `obj.name(...)` 改写成先读属性再调用；这会改变直接路径 |
| `semant.py` | 保持不变。运行期由 `gen_py.py` / `LtRt.java` 识别专用 wrapper，现有间接动态调用已可承接；不推断外部签名。测试局部变量避开已有内建名，不改变保留名称规则。 | 外部签名推断、Java overload 编译期绑定 |
| `gen_py.py` runtime prelude | 增加明确标识的互操作 bound-method callable wrapper；让 `_wv_wgetattr` 在既有句柄读取分支中执行窄范围判定；让 `_wv_call` 识别它并在调用时拒绝名称/展开、转发位置实参。继续区分 `_LazyMod`、普通 Python 对象、`_JHandle`。`_wv_repr` 增加稳定显示。 | 接受所有 Python callable，或者放宽直接 `_wv_wcall` |
| `runtime/LtRt.java` | 引入与 `LtFunction` 并列、语义明确的 `InteropBoundMethod`（或等价内部 callable 类型）；`wgetattr` 对 `JObj` / `PyHandle` 产生该 wrapper；`callValue` 识别该类型并走位置调用/互操作拒绝逻辑；显示路径识别新值。`wcall` 保持原样。 | 将未知 arity 的互操作方法伪造为零参数 `LtFunction` 并错误地使用 Latent binder |
| `runtime/JReflect.java` | 增加 `hasPublicField` 与 `hasPublicInstanceMethod` 两个窄检查：前者判断公开字段是否存在以确保字段优先，后者仅匹配公开非静态、非 bridge 实例方法；实际调用复用 `call(JObj, name, args)`，故 overload 仍在每次调用解析。 | 新重载规则、反射缓存必要性或静态/构造器包装 |
| `runtime/LtJavaDaemon.java` | 远端 `JObj` 取值时先读字段；字段未命中且有公开实例方法时返回结构化 `bound_method` 描述（原对象 id、方法名、运行期类名）。Latent 侧 wrapper 保存现有 `_JHandle` 与方法名；每次调用仍发送普通 `call` RPC。 | Latent callable 反向传入 JVM daemon |
| `runtime/ltpy.py` 与 Java 后端 Python `Daemon` 客户端（`runtime/LtRt.java`） | Java backend 通过窄 `get_bound_method` 响应区分获准方法与一般 `__ref`：daemon 验证真实 bound method，将其保存在独立 `_bound_methods` 强引用表并返回 typed opaque id；`call_bound_method` 只对该已捕获方法执行位置调用。普通 `get` / `call` 不改语义。 | 把所有 Python `__ref` 都当 callable、任意远端 `eval` / callback |
| `tests/`、`tests/run_tests.py` | 加两种宿主 fixture、两种目标后端的正例/负例；维持双后端输出对拍。 | 更改 AST fixture、发布产物或直接互操作用例预期 |

实现可在 daemon 内部使用带类型的 remote method ref 标记，或使用独立 RPC operation；具体编码不应与用户映射中可能出现的 `__ref` / `__jref` 数据冲突。协议端必须严格验证句柄类型、目标进程与对象 id，错误需安全返回。

## 5. 四组合跨后端测试矩阵

矩阵维度是 **生成目标后端** × **被提取方法所属宿主**。四格均须覆盖提取、赋值、传参、返回、闭包捕获及间接调用；Java 两格另外验证重载在实际调用时选择。

| 生成目标 | Python 宿主对象上的绑定方法 | Java 宿主对象上的绑定方法 |
|---|---|---|
| Python（`-t py`） | 本地 Python 对象：取值一次后赋值/传入/返回；闭包持有 receiver；反复调用能观察同一对象状态；确认 Python bound method 的接收者身份与读取时捕获。检查 callable field/property 不被包装、命名与展开参数失败且 `.lt` 行正确。 | 经 `_JHandle` / `LtJavaDaemon`：取 `items.add` 作为值，通过 Latent helper 与返回闭包调用；`add(E)` / `add(index,E)` 依据每次传入的值/arity 沿既有反射规则选择；接收者保持同一对象。测试方法/字段冲突、静态方法不被暴露、歧义/无重载错误行映射。 |
| Java（`-t java`） | 经 `PyHandle` / `ltpy.py`：远端 Python bound method 被识别为专属 opaque method ref；在 Latent helper 和逃逸闭包中调用；验证 receiver 被保活，普通 callable property/实例 callable 不误判；参数拒绝、daemon 异常与 `.lt` 行映射。 | 本地 `JReflect.JObj`：`items.add` 提取后赋值/传入/返回/闭包捕获；通过同一 `JReflect.call` 检查 overload 每次按实参选择；字段优先、静态方法排除、无重载/歧义诊断与 `.lt` 行映射。 |

### 矩阵所有单元共用的断言

- 以同一 fixture 的观察输出做 Python / Java target byte-for-byte 比较；无法跨语言对齐的宿主错误断言类别、稳定前缀与正确 Latent 行，不脆弱依赖宿主堆栈全文。
- 测试直接调用 `receiver.method(...)` 仍然通过既有路径；专门的回归确保 P13 不改变它的输出、重载选择、错误和惰性启动时机。
- 测试普通非 callable 字段读取仍返回原值；将它间接调用时失败；callable property、Python 模块函数、Java 静态方法、构造器、`len` 等独立内建函数不能被意外加入此功能。
- 对每个宿主，在 Latent 函数内捕获的 bound value 在创建该值的外层作用域结束后仍能调用；清除原 receiver 变量不应使 wrapper 失效。
- 负例分别覆盖 `fn(name=value)`、`fn(*items)`、`fn(**mapping)` 和向宿主传递 Latent callable 的企图；不得静默丢弃参数名或把 Latent 函数跨边界传成回调。

## 6. 实施步骤（已完成）

1. **决策锁定与基线保护。** 先确认第 9 节列出的边界；添加/选定最小 Python 与 Java fixture 设计，确认 direct-call 现有测试预期和 daemon 测试夹具。
2. **公共 callable 协议。** 在 Python/Java 运行时中引入独立 P13 wrapper 表示及显示格式；只扩展一般间接 `call` 入口，保持 Latent `LtFunction` 的绑定逻辑和错误不变。先完成同进程构造单测/小型回归。
3. **Python 对象路径。** 支持 Python target 的本地对象方法读取，以及 Java target 经 `ltpy.py` 的识别/opaque bound-method 引用。确保只有批准的绑定方法类型被提升，receiver 生命周期明确。
4. **Java 对象路径。** 支持 Java target 经 `JReflect` 直接读取/调用，以及 Python target 经 `LtJavaDaemon` 的 Java object handle；复用既有 Java 重载选择。保持静态方法、字段和构造边界。
5. **四组合验收与兼容检查。** 落实第 5 节矩阵、跨后端 stdout/error 行对拍、直接调用负担保护、P8–P12 与 AST v6 回归、Python `py_compile`、Java runtime/fixture 编译及 `git diff --check`。最后同步规范/教程与 P13 实施记录。

## 7. 主要风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Python descriptor / property / instance shadowing 难以分类 | 普通属性可能意外变成 callable，产生行为和安全边界变化 | 用窄的静态 descriptor 识别与真实 bound receiver identity 检查；不以 `callable(value)` 单独作为准入条件；测试属性同名、property、custom descriptor 和实例遮蔽 |
| Java 字段与同名 overload 家族 | `obj.name` 原本是字段；方法值提取可能遮蔽数据 | 字段优先，只在无公开实例字段且存在非静态实例方法时构造 wrapper；静态类句柄不启用 |
| daemon opaque id 生命周期 | 多个保存的方法值增加 daemon 对象表长期占用；错误 id 可能影响无关对象 | 复用强引用表、建立带类型的独立引用语义和 stale-id 错误；首版明确到 daemon 退出前保留，不增加释放协议 |
| 远端“属性值”与“方法值”混淆 | 任意 `__ref` 可能被误当成函数 | daemon 明确验证绑定方法，并只为这类值发方法引用标记；普通引用保持现状 |
| Java 重载解析被错误推迟到方法提取时 | 取值无实参，无法确定重载，且改变当前算法 | 提取只捕获目标与方法名；只在调用时使用实际位置参数执行现有 `JReflect` 选择 |
| wrapper 被当作宿主回调传递 | 实现范围超出“不做跨边界 callback passing”，可能形成复杂生命周期/并发问题 | 在 P13 的互操作参数编码前拒绝 Latent function/wrapper 值；只允许 callable 值留在 Latent 侧并调用宿主方法 |
| 异常与 source-map 归属不一致 | Python/Java 后端 traceback 指向 runtime helper 而非用户调用 | 负例断言 `.lt` 间接调用行；保持 runtime exception 原因链，不吞掉并包装为无位置错误 |
| 直接调用被 getter 优先级改变 | 兼容性回归 | 保留 `__wcall` / `wcall` 与 `JReflect.call` 现有直接入口；新增测试锁定直接语法旧行为 |

## 8. 验收标准

- 四个矩阵格都可把受支持的 Python/Java bound instance method 作为 Latent callable 赋值、传给 Latent 函数、返回、闭包捕获并间接调用。
- Java 同名 overload 在实际间接调用时按现有 `JReflect` 规则选择；直接调用结果不变。
- Python/Java 属性优先级、method eligibility、位置参数限制、显示、异常类别及 `.lt` 源位置按本节批准的实施语义。
- 没有新语法、AST 节点或 schema 升级；静态不推断外部方法签名。
- 模块/静态函数、构造器、独立 builtin function、任意 callable property 和跨边界 Latent callback 均未意外纳入。
- 全量双后端回归、四格新专项、Python/Java 工具链与 diff 检查通过。

## 9. 用户已批准的设计决策

以下五项默认已获用户明确批准，并作为本次实现的边界：

1. **Python callable 属性准入（重要）。** 决议：接受 `inspect` 可可靠确认为当前实例绑定的 C/Python 原生 bound instance method；同时排除任意 callable 实例字段、`property` 返回 callable、`staticmethod`、`classmethod` 和自定义非方法 descriptor。特别是 builtin bound methods（如 `list.append`）属于“bound instance method”范围，已批准纳入；独立 builtin function（如 `len`）仍排除。
2. **字段/属性与方法同名时的优先级（重要）。** 决议：与 Python 实例查找、现有 Latent 字段语义及 Java 公共字段读取保持一致：字段/普通属性胜过方法；只有没有字段/属性时才产生绑定方法值。该优先级已获批准。
3. **提取时捕获 vs 调用时查找。** 决议：Python 捕获取值时得到的实际 bound method；Java 捕获 receiver + 名称，并在每次调用时选择 overload。这样保留 Python bound method 的读取语义，同时满足 Java overload 在调用时消解。跨语言这种差异化捕获时机已获批准。
4. **显示格式稳定性。** 决议：新增 `<bound method <qualified-type>.<member>>`，Python 用 `module.qualname`、Java 用类二进制名；该格式已获批准，并已由测试固定；不加 `py:` / `java:` 命名空间。
5. **daemon 生命周期。** 决议：首版复用当前 daemon 强引用表：已捕获句柄保持到 daemon 退出，不引入显式 release/refcount 协议。该进程期生命周期与潜在长期占用已获批准。

本记录不再有待确认的 P13 设计项。Python descriptor 识别、字段优先级、捕获时机、显示格式和 daemon 生命周期均按上列批准决议实现。


## 10. 实施与验证结果

### 实现范围

- `gen_py.py`：新增独立 interop bound-method callable wrapper、Python descriptor/receiver 身份筛选、位置参数调用、callback 参数拒绝及稳定 repr；direct `__wcall` 路径未改。
- `runtime/ltpy.py`：daemon 仅为经验证的真实 bound method 分配专用 opaque id；`call_bound_method` 调用提取时捕获的对象，强引用保留到 daemon 退出。
- `runtime/LtRt.java` / `runtime/JReflect.java` / `runtime/LtJavaDaemon.java`：添加 Java/Python 方法 wrapper、字段优先和公开非静态方法筛选；Java wrapper 捕获 receiver+名称并在每次调用时复用既有 overload resolver；Python daemon 方法引用使用独立 typed response。
- 未改 parser、desugar、AST/nodes/schema 或 `semant.py`；未改直接 `obj.method(...)`、发布版本号或 release assets。
- 新增 Python/Java host fixtures、四组合正例、11 个 runtime 排除/参数/callback 负例和 Cookbook 入口，并同步 SPEC、README、TUTORIAL 与 Cookbook。

### 验证

- `python3 tests/run_tests.py`：**234 passed, 0 failed**；包含 13 项只读 AST/CLI 诊断、505 个有界固定种子性质案例，以及 P13 四组合、11 个双后端 runtime 负例和 Cookbook 样例。
- `python3 -m py_compile gen_py.py runtime/ltpy.py tests/run_tests.py tests/python/p13_host.py`：通过。
- `javac -d /tmp/p13-final-classes runtime/LtRt.java runtime/JReflect.java runtime/LtJavaDaemon.java tests/java/P13InteropFixture.java`：通过。
- `git diff --check`：通过（最终检查另见工作区结果）。
- `cd vscode-latent && xvfb-run --auto-servernum npm test`：退出码 0；TextMate tokenization/snippet 测试通过；VS Code Extension Host 集成测试 **12 passing**（包含从扩展启动完整项目回归）；Restricted Mode 测试 **2 passing**，包含“不在受限模式启动 parser metadata”的断言。
