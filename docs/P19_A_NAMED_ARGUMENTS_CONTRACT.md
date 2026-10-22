# P19-A：Python 宿主命名实参与同步 callback kwargs 契约

**状态：契约冻结，尚未实现。** 基线为 `main@9071846e12784bb98ea7ce050c93eec534b884c7`。本文件只定义 P19 后续实现的验收条件，不宣称 P18 已支持这些能力，也不启动 P19-B。本文中的“必须”指后续实现的门禁。

P19 的最小增量是：允许 **Latent → Python 的显式 `name=value` 调用**，以及 **Python → Latent 借用同步回调收到的 kwargs**。同一份 Latent 源码在现有 Python 编译器的 `py`、`java` 两目标上保持这些语义；Stage1 仍是显式 opt-in、Python-only preview。

## 1. 依据、现状与新增决定

依据为 [SPEC](../SPEC.md) §3、§4/P8–P12、§6–8、[P18 契约](../P18_SYNCHRONOUS_CALLBACKS.md)、[贡献指南](../CONTRIBUTING.md)。实现核对点：

- `parse.py:Parser.call_args`、形参解析：已有 `NamedArg`、`StarArg`、`StarStarArg`；显式名称必须是词法 `NAME`，位置实参/位置展开不能跟在命名实参后；没有 `/` 形参标记。
- `semant.py:_check_arguments`：已知 Latent 签名静态检查；宿主 Python 签名不在这里猜测。
- `gen_py.py:_wv_bind_args`、`runtime/LtRt.java:bindArgs`：已有 Latent 动态 binder；固定形参、默认值缺省标记、keyword-only、`*rest`、`**extra` 已存在。`_LtNamedArg`/`LtRt.NamedArg` 携带已求值的值。
- `gen_py.py:_wv_reject_interop_args`、`_wv_pyhost_call`、`_WVCallback.__call__`、`runtime/LtRt.java:rejectInteropArgs`、`Daemon.callArgs/exchangeResponse`、`runtime/ltpy.py:CallbackProxy/execute/_valid_request_payload`：P18 的宿主命名实参和非空 callback kwargs 仍被拒绝，传输尚无 kwargs 通道。
- `bootstrap/gen_py.lt` 和 `bootstrap/java_runtime.lt`：Stage1 的 Python emitter/prelude 也必须跟进后续实现，不能只修改现有 Python 编译器。此处的 `java_runtime.lt` 是 Python 产物中的 JVM 互操作支持，不代表 Stage1 已有 Java emitter。

| 内容 | 基线已经保证 | P19 新增，尚须实现 |
|---|---|---|
| Latent 内部命名绑定 | 静态/动态检查、左到右求值、keyword-only、调用时默认值、变参 | 不改语言内部规则 |
| Python 宿主调用 | 位置实参与 P18 直接同步回调 | 显式名称转交 Python；直接命名槽也可传 Latent 回调 |
| Python 回调到 Latent | 位置序列进入原 binder；无来源启发式 | kwargs 顺序转换为 named markers 后进入同一 binder |
| 回调生命周期、转换、错误保真 | P18 的借用、同线程、清理、嵌套泵、错误 ID | 对新增命名槽应用相同检查，不扩大生命周期 |
| `*`/`**` | Latent 内部调用支持；Latent→宿主拒绝 | 仍拒绝 Latent→Python/Java 展开，包括空展开 |

## 2. Latent → Python：准入与绑定

### 2.1 准入路径

允许现有 **Python 句柄直接成员调用**、P13 提取的 **Python 绑定实例方法值**、P14 提取的 **Python 模块函数值**携带显式命名实参。直接调用路径不因 P19 额外收窄或扩展；提取值路径仍严格遵循 P13/P14 的 callable 分类和取值时捕获规则。

`obj.method(...)` 与 `method = obj.method; method(...)` 在两者原本均获准时具有相同命名绑定效果；旧 wrapper 不因宿主成员重绑定而改指新函数。不能因为新增 kwargs 就允许任意 callable 字段、property/custom descriptor 返回的 callable、普通 opaque callable、独立 builtin 值或构造器值间接调用。Python 工厂/类的现有直接成员调用若原本可用，仍由该直接路径处理；本契约不引入新的构造器值机制。

**Java 宿主 API**（不是 Java 编译目标的 Python 互操作）、Java 静态方法值、Java 构造器，以及 Latent 内建函数仍不支持命名实参/展开。不得把“两个编译目标都支持 Python kwargs”误写为“Java 反射支持 kwargs”。

### 2.2 求值与调用顺序

1. 沿用现有 callee/receiver 求值与成员分派时序，不为了检查宿主签名新增预调用、`inspect.signature`、descriptor 探测或 import。
2. 合法调用的显式实参表达式按源码从左到右、各一次求值，包括位置值与命名值；不得按形参顺序重排。若某表达式抛错，后续表达式、宿主调用均不发生。
3. 先保留有序实参标记，再检查 P19 支持范围、重复名称和转换；不得直接构造 dict 抹掉重复项。运行期才能发现的结构/绑定错误发生在显式实参求值后、宿主函数体执行前；转换失败可以短路其后的转换，但不得重新求值表达式。
4. 显式重复 `h.f(x=mark(1), x=mark(2))` 是桥入口 `ArgumentError`，两次 `mark` 均已发生，宿主函数体未执行。若程序本身存在解析/静态语义错误，则编译失败，任何运行期副作用均不发生。
5. 位置序列和按源码顺序收集的唯一命名序列，转换后通过实际 `function(*args, **kwargs)` 调用 Python。桥不得按名称自行绑定 Python 签名、补默认值或用失败后位置重试作为 fallback。

Python 决定位置/命名冲突、未知名称、必需项、positional-only、keyword-only、`*args`、`**kwargs` 的含义；C/native builtin 无可用签名时也直接调用，不因反射失败而拒绝。宿主函数可主动抛同类错误，桥不声称区分“Python binder 抛错”和“函数体抛错”。

### 2.3 Python 签名规则

以 Python 定义 `def f(a, /, b=2, *rest, flag, tail=9, **extra): ...` 为例：

- `a` 只能由位置值填充；`b` 可位置或命名；`flag` 必须命名；`tail` 可省略。省略时使用 Python 自己的默认值（定义时创建，不是 Latent 调用时表达式）。显式 `nil` 转为 `None`，不触发默认值。
- 超额位置值进 `rest`；未知名称仅在有 `**extra` 时收集。`rest=...`、`extra=...` 不直接替代收集器，而是作为未知名称进入 `extra`；无 `**extra` 则由 Python 拒绝。
- `f(1, a=7, flag=true)` **成功**：Python 的 positional-only 名称 `a` 可独立进入 `extra`。`f(a=1, flag=true)` 仍缺位置 `a`；不准用泛化规则禁止上述合法情形。
- 同一 positional-or-keyword 参数由位置与命名同时填充由 Python 抛 `TypeError`；未知名称且无 `**kwargs`、缺必需项、超额位置或把 keyword-only 当位置也由 Python 抛 `TypeError`。
- 显式名称只能使用当前 Latent `NAME` 语法；不增添字符串关键字、转义保留字或 `**mapping` 替代入口。因语言保留字/标识符规则不同而无法显式书写的宿主参数，不在这一增量内解决。

## 3. Python → Latent：kwargs 回调

### 3.1 交给现有 Latent binder

Python adapter 收到的是已由 Python 形成的 `(args, kwargs)`，不检查调用者的源码、字节码或包装来源。直接 `cb(value=1)`、`cb(**mapping)`、`functools.partial`、Python/native wrapper 均以最终交付的值为准；空 kwargs 与 P18 位置调用完全等价。Python 自己拒绝的重复关键字合并或非字符串键，在进入 adapter 前就已失败，不得伪装为 Latent binder 错误。

adapter 必须先执行 P18 的 active scope、owner thread 和嵌套限制检查，再转换位置值及 kwargs 值；位置值按序在前，kwargs 按 Python 插入顺序在后。每个 kwargs 项成为一个原 binder 能理解的 named marker，**不是末尾追加一个普通映射实参**。转换后只调用正常 Latent binder 一次，不创建第二套签名规则。

| Latent 签名/输入 | 必须行为 |
|---|---|
| `fn cb(value)`；Python `cb(value=3)` | 固定形参绑定为 Latent number `3` |
| `fn cb(value=7)`；`cb()` / `cb(value=None)` | 前者调用时求默认值；后者绑定 `nil`，不求默认值 |
| `fn cb(a, *, flag, tail=9)`；`cb(1, flag=True)` | 成功；固定 keyword-only 规则不变 |
| 同上；`cb(1, True)` / `cb(1)` | 分别超额位置/缺必需 keyword-only；函数体与默认表达式不执行 |
| `fn cb(a, *rest, flag=0, **extra)`；`cb(1, 2, flag=3, z=4)` | `a=1`，`rest=[2]`，`flag=3`，`extra={"z":4}` |
| 同上；`cb(1, rest=8, extra=9)` | 两个名称进入 `extra`；不替代收集器变量 |
| `fn cb(a)`；`cb(1, a=2)` | `ArgumentError`：`got multiple values for argument 'a'` |
| `fn cb(a)`；`cb(a=1, other=2)` | `ArgumentError`：`got unexpected named argument 'other'` |
| `fn cb(a, **extra)`；`cb(a=1, **{"not-an-id":2, "if":3})` | 成功；额外键按原字符串与插入顺序收集，不加标识符白名单 |

Latent **没有 positional-only 参数**，固定位置区形参仍均可按名绑定；方法的隐式接收者不属于暴露给宿主的绑定签名。callback 可以是 P18 已支持的 Latent 普通函数、闭包、公开模块函数值或 Latent 绑定方法；Python/Java interop wrapper 不变成 Latent callback。

绑定先验证重复、冲突、未知名称、超额位置与缺必需项；只在绑定成功进入函数时，按声明顺序求被省略的 Latent 默认表达式。保留 P9 的调用时/闭包环境语义、fresh list/map 默认值、显式 `nil` 与缺省标记的区别。默认表达式抛错时保留其真实 `.lt` 位置，函数体未开始。

Python 可交付任意字符串 kwargs 键；匹配固定形参用精确字符串相等，不改大小写或 Unicode 规范化。未知键按现有 binder 规则收集或报错，不要求其能在 Latent 源码中作为显式名称书写。重复 kwargs 在正常 Python adapter 入口不可存在；对低层伪造的重复 wire entries 必须按协议错误拒绝，不能靠 dict 后值覆盖。

### 3.2 命名槽中的借用回调

`host.run(callback=latent_fn, value=1)` 与 P18 的直接位置回调使用同一调用 scope。只允许参数槽的**直接值**是已获准的 Latent 函数/绑定方法；`callback=[latent_fn]`、映射中隐藏回调、interop wrapper 冒充回调仍拒绝。相同 Latent 函数出现在多个参数槽可获得独立 token，不能复用单次消费的 descriptor token。

命名槽不创建持久注册。成功、参数转换失败、宿主错误、callback 错误及协议失败的所有路径都清理/撤销 adapter、token、invocation 与 error registry。过期/跨线程调用必须在执行 binder、默认值或 callback body 前失败。P18 的 same-thread re-entrant pump、嵌套深度 16、容器深度 64、循环检查、deferred/awaitable/generator 与返回 callable 的拒绝均不变，并同样覆盖 kwargs 值及其精确 builtin 容器。保留 opaque host 对象的原有句柄寿命；callback scope 结束不使显式保存在 Latent 中的普通 host object 失效。

## 4. 桥字段与协议冻结

这是后续实现的内部 wire 契约，不是对第三方 daemon 的兼容承诺；发送方、接收方、bootstrap prelude 和故障注入测试必须一起更新。

### 4.1 统一逻辑表示

逻辑调用为 `args: [value, ...]`、`kwargs: [[name, value], ...]`。有序 pair list 保留名称顺序，便于在进入 Python dict 前检查重复；不能用普通 JSON object 表示 kwargs。用户映射继续使用既有 `__map` data envelope，不能把映射内容解释为控制帧。名称原样编码为字符串，值沿用现有 codec（数字、nil、布尔、opaque refs、容器与错误边界），不能把 kwargs 值字符串化。

- Python 目标：内部参数拆分得到相同有序表示；`_wv_pyhost_call` 等价入口接收位置值和有序命名项；`_WVCallback` 把 kwargs 项转为 `_LtNamedArg`。符号名可按实现组织，不改公开 AST schema v6。
- Java 目标：`Daemon.callArgs` 等价入口分离 markers，所有 Python call payload 加 `kwargs`；`runtime/ltpy.py` 校验后解码并执行 `fn(*args, **kwargs)`。回调帧的命名项转为 `LtRt.NamedArg` 后追加到位置序列，调用 `LtFunction.invoke`/现有 binder。

### 4.2 帧形状

新发送方对 `call`、`call_bound_method`、`call_module_function` **总是**发送 `args` 和 `kwargs`（即使为空）：

```json
{"kind":"request","id":1,"parent":0,"payload":{"op":"call","target":{"mod":"fixture"},"attr":"run","args":[],"kwargs":[["callback",{"__lt_callback__":1}],["value",3]]}}
```

callback 帧增加唯一字段 `kwargs`：

```json
{"kind":"callback","parent":1,"id":1,"callback":1,"args":[],"kwargs":[["value",3],["flag",true]]}
```

`callback_result`（成功 `value` 或失败 `error_id/error_message`）、response、release_refs、请求父子关联、方向、ID 范围 `1..9007199254740991`、单调/replay 检查和 frame ownership 维持 P18。非 call 操作（get/getitem/setitem、repr、binop 等）不接受 `kwargs` 字段，不顺带开启命名 operator 调用。

接收端明确接受旧 P18 形状**缺少** kwargs，规范化为空序列，以保留旧 positional fixtures；缺字段不等于可接受 `null`。除这一个旧形状外仍按 exact-key schema 验证，不能忽略任意未知字段。旧 raw request 路径若保留，只保留原位置功能，不启用 kwargs 或 callback；新特性只能走校验后的 framed 路径。

所有 kwargs 必须是列表；每项恰好两个元素；名称必须是字符串且唯一；值须是有效 codec 值。错误类型、非字符串键、重复键、非法 descriptor、未知 ID/parent、错方向和非法额外字段是 **ProtocolAbort/ProtocolException**，中止受影响 daemon，不得包装成可吞掉的普通 Python `TypeError` 或 callback error。正常用户源码的显式重复名称必须在发送前成为 `ArgumentError`，不是协议故障。

callback descriptor 仅可作为三类 call 的直接位置槽或直接命名值；两者共用 request-local token registry、唯一性和消费检查。不得递归接受嵌套 descriptor，亦不得允许 callback 输入 kwargs 中携回 adapter，或 callback_result 返回 adapter。所有 envelope 嵌套处都要保留控制/数据隔离、原有句柄释放与协议 IO 隔离。

## 5. 错误类别、位置与双目标一致性

| 层次 | 分类/时机 | 验收位置与稳定性 |
|---|---|---|
| 语法 | 解析诊断 `parse error`（内部 `ParseError`）：命名后位置值、命名后 `*`、非法 NAME、`/` 形参标记 | 编译期，原文件/行/列与源码定位；无运行期副作用；不要求 Python `SyntaxError` |
| 已知 Latent 签名 | 现有 semantic compile diagnostic | 保留现有文案与节点坐标；不改内部 P8–P12 静态策略 |
| Python 入口结构 | `ArgumentError`：重复显式名；仍禁用的展开 | 两目标稳定类别；重复 `got duplicate named argument 'x'`；定位外层 `.lt` 调用行 |
| Python 签名/函数体 | 原 Python `TypeError` 等宿主异常 | 外层 `.lt` call 行；只断言类别/稳定片段，不要求跨 Python 版本完整 traceback 或两目标原文逐字相同 |
| Latent callback binder | 既有 `ArgumentError` 与精确 binder 文案 | 定位触发 host call 的 `.lt` 行/调用栈；未进入 body 不捏造 body 行或运行期列号 |
| callback body/default | 原 Latent 错误 | 保留实际 body/default 的 `.lt` 位置及外层调用栈 |
| 宿主改写 callback 错误 | 新宿主异常 | 定位外层 call，不虚构 callback body 位置 |
| 生命周期/转换限制 | P18 对应运行期错误 | 未执行 callback；断言稳定边界片段与外层位置，不把原本不同的宿主异常类说成统一 |
| 协议损坏 | fatal protocol error | 受影响 daemon 终止；不继续泵、不回落、不把它当普通宿主错误；释放 registries |

Callback binder 文案继续使用 `takes N args, got M`、`missing required argument 'x'`、`missing required keyword-only argument 'x'`、`got multiple values for argument 'x'`、`got unexpected named argument 'x'`。多重错误的选择顺序沿用现有 binder，不在 P19 重新排序。Python 签名错误顺序由实际 Python 实现决定。

Latent `catch e` 仍得到消息字符串；不要引入新可捕获类体系。Java callback 失败通过 request-local error ID 恢复原 `Throwable`，保留 `.lt` stack；Python host 可用广义 `except Exception`、原消息、吞掉/重抛/转换。**不承诺任意 Python type-specific handler 在两目标等价**，例如 `except IndexError`/`except TypeError` 对 Java carrier 的行为仍受 P18 限制。错误恢复之后的正常命名调用必须仍可用；协议故障不承诺继续使用同一 daemon。

## 6. 具体正反例

以下是未来验收 fixture，不是当前 P18 可执行承诺。`fixture` 是待新增测试宿主模块，不是标准库；不需要 inline `py:`（仍不支持）。

```python
# fixture.py：由测试提供

def render(a, /, b=2, *rest, flag, tail=9, **extra):
    return [a, b, list(rest), flag, tail, list(extra.items())]

def run(callback, *, value, suffix="!"):
    return callback(value=value, suffix=suffix)

def strict(value, *, flag):
    return value
```

```latent
host = py "fixture"
say host.render(1, flag=true)           # [1, 2, [], true, 9, []]（按 Latent repr）
say host.render(1, flag=true, a=7)      # a=7 在 extra，不覆盖位置 a
run = host.run
fn decorate(value, *, suffix="?"):
    return str(value) + suffix
say run(callback=decorate, value=3)     # 3!
say run(callback=decorate, value=3, suffix=nil)  # 显式 nil；若 body 拼接不合法则是 body 错误，不改用默认值
```

```latent
host = py "fixture"
host.strict(value=1, flag=true, value=2) # ArgumentError：重复显式名；host body 不执行
host.strict(1, value=2, flag=true)       # Python TypeError：位置/名称重复
host.strict(1, unknown=2)               # Python TypeError：未知名/缺 flag，具体选择由 Python 决定
host.strict(1, true)                    # Python TypeError：keyword-only 不由位置填充
host.render(a=1, flag=true)             # Python TypeError：缺 positional-only a
host.strict(flag=true, 1)               # 解析失败，不运行任何代码
host.strict(*[1], flag=true)            # 仍拒绝宿主展开
host.strict(1, **{"flag":true})         # 仍拒绝宿主展开
```

对回调，Python `callback(**{"value":3, "suffix":"!"})` 与直接 kwargs 等价；`callback(3, value=4)` 是 Latent binder 冲突。Python `callback(value=1, **{"value":2})` 和 `callback(**{1:2})` 在 Python 形成调用时失败，不能计作“已进入 callback”的负例。

## 7. 验收矩阵（后续实现必须落为测试）

记号：**R**=`tests/run_tests.py` 双目标回归；**F**=沿用 `tests/test_p18_callbacks.py` 的 focused 双目标/协议 harness，新增 P19 互操作案例（名称和组织可后续确定）；**P**=Python-target Stage0/1/2 projection ledger 与 `project_stage0.py`；**B**=bootstrap host/lexer/parser、Stage1 preview 与 fixed-point gates；**D**=固定种子 differential properties；**CI**=现有 `.github/workflows/tests.yml`。这是一份待实现矩阵，**不是已通过的测试清单**。

| ID | 输入/条件 | 必须观测 | 落点 |
|---|---|---|---|
| N01 | Python module direct / extracted function，混合位置/命名、名称乱序 | 相同绑定结果；原 P14 捕获行为不变 | R/F/P |
| N02 | 普通 Python 与 C/native bound method，direct / extracted | kwargs 委托 Python；typed wrapper 限制不变 | R/F/P |
| N03 | `mark` 记录 callee/args、名字顺序、某实参抛错 | 参数左到右各一次；不按签名重排；错误后不运行后项/host | R/F/P |
| N04 | 显式重复名称，两处带副作用 | 两处已求值；桥 ArgumentError；host body=0 | R/F/P |
| N05 | 位置/名称冲突、未知名、缺位置/keyword-only、超额位置 | Python TypeError；外层 call 行；host body=0 | R/F/P |
| N06 | Python `/` 与 keyword-only；含/不含 `**kwargs` | 正确委托；pos-only 同名可进入 extra；不猜签名 | R/F/P |
| N07 | Python 默认对象/None、`*args`、`**kwargs` 与收集器同名 | Python 原默认语义；顺序与收集器名称不被特殊绑定 | R/F/P |
| N08 | kwargs 中 bool/nil/int/负零、list/map、opaque object | 逐值沿用 P18 conversion；不字符串化；普通 map 不变 control | R/F/P |
| C01 | `cb(value=x)` / `cb(**mapping)` / partial/native wrapper / 空 kwargs | 按最终交付值同样进入 binder，无来源启发式 | R/F/P |
| C02 | callback 固定形参按名、bound method 隐式 self、闭包/module fn | 正常固定绑定和闭包共享状态；self 不暴露为参数槽 | R/F/P |
| C03 | Latent 默认值省略/显式 nil、多次调用 mutable 默认、默认副作用 | 原调用时/声明顺序/freshness；绑定错误不求默认、不执行 body | R/F/P |
| C04 | keyword-only 必需/可选、混合 `*rest`/`**extra`、collector 同名 | 第 3 节表格的精确绑定、空 list/map 与插入顺序 | R/F/P |
| C05 | callback 重复位置+名、未知名、缺必需、超额位置 | 精确 Latent ArgumentError 文案；body=0；外层位置 | R/F/P |
| C06 | kwargs 非标识符/保留字/Unicode 键；无 extra；非字符串 Python key | 字符串精确匹配/extra；无 extra 报未知；非字符串由 Python 提前拒绝 | R/F/P |
| C07 | Python 重复 kwargs 合并 | adapter/binder/body 均未进入；Python TypeError | F/P |
| L01 | 直接命名 callback、位置与命名双槽同一函数、多次 invoke | 同 scope、每槽独立 token，正确调用/状态累计 | R/F/P |
| L02 | callback 隐藏在命名 list/map、interop wrapper 作 callback、返回 adapter | 与 P18 对应位置负例同样拒绝 | R/F/P |
| L03 | 成功/失败后保存 callback 再用、worker thread、depth 16/17 | 不执行 stale/thread-invalid body/default；限制与清理不回退 | F/P/B |
| L04 | kwargs 中循环、64/65 层、deferred；callback 返回 deferred/callable | 与 P18 同转换边界；host 消费结果前拒绝 | F/P |
| E01 | callback binder/body/default 错误；宿主吞掉/重抛/转换 | 分类、真实/外层位置、Java 原 Throwable/ID 清理；广义 catch 保真 | F/P |
| E02 | 一次命名 call 或 callback 失败后正常调用 | request-local registries 清理；无 callback/token/错误泄漏 | F/P |
| W01 | kwargs 缺省旧帧 / 新帧空 pairs / 非空 pairs | 旧 positional fixtures 不变；新行为正确 | F |
| W02 | kwargs null/object、坏 pair、非字符串/重复 name、extra fields | fatal protocol error，daemon abort；不能静默丢名/覆盖值 | F |
| W03 | direct named descriptor、nested descriptor、错/重复 token 与 ID/parent | 只放行合法直接槽；旧 ownership/replay/direction 检查全部保留 | F |
| W04 | kwargs map 内伪控制字段、native FD IO、nested host re-entry | data envelope 隔离、私有协议 IO、原线程泵不回归 | F/P |
| X01 | Latent→Python `*`/`**`（包括空）；Java host named/expanded/callback | 仍为负例；不借 P19 扩大语言或反射面 | R/F/P |
| X02 | forbidden callable fields/descriptor/static/class methods/opaque callable | 保留 P13/P14 既有准入/拒绝边界，不因 kwargs 变 callable | R/F/P |
| G01 | 所有既有投影/性质 case、P18 boundaries、Stage2==3/双根一致 | 除单独显式登记的 P19 增量外零意外差异；不删测试、不弱化断言 | R/P/B/D/CI |

对 P19 后续实现，现有 `host_keyword_callback`、`callback_star_kwargs` 两个 P18 focused 负例会变成受支持绑定正例；`host_named_argument` 不可只删断言：该输入的 `other` 是否被 Python 接受取决于 fixture 签名，必须改成明确的 Python 绑定错误或使用合法签名正例。保留它们的 coverage lineage，不能把删除计作通过。相应 Stage1 feature expectations 若在 ledger 中必须逐项更新。

以下三项 included runner/projection cases 显式登记行为变化：原两项转为成功，获批新增的第三项仍为运行期负例、仅改变拒绝来源和诊断。须保留原 case ID 与源码，不能因目录名含 `negative` 就删除或排除：

| 精确 case ID | 固定 Stage0 原行为 | P19 Stage1/2 新行为（两个现有编译目标也须遵守对应结果） |
|---|---|---|
| `p13_negative/python_named` | 编译成功、运行失败；stdout 为空；互操作 named 拒绝诊断与 `python_named.lt:4` | 编译/运行退出码均为 0；stdout、stderr 均为空；实际调用 `Sample.push(value="named")` |
| `p14_negative/module_named` | 编译成功、运行失败；stdout 为空；`named arguments are not supported for Python/Java interop calls` 与 `module_named.lt:3` | 编译/运行退出码均为 0；stdout、stderr 均为空；实际调用 `scale(value=1)` |
| `p8_negative/python_interop` | 编译成功、运行退出码为 1；stdout 为空；桥入口 `ArgumentError: named arguments are not supported for Python/Java interop calls`，位置 `python_interop.lt:2` | 编译成功、运行退出码仍为 1；stdout 为空；真实调用 `math.sqrt(value=4)` 后由 Python 抛 `TypeError: math.sqrt() takes no keyword arguments`，位置仍为 `python_interop.lt:2` |

Stage0 的完整旧期望继续取固定 seed 的现有归一化结果，不重写为 P19 成功；后续 delta 记录须钉住实际非零退出码、原诊断和行号。新成功结果不能靠跳过调用达到，N01/N02 另用可观察返回值/副作用验证。`p13_negative/java_named`、`p14_negative/static_named` 继续保留原拒绝、失败状态和源位置，不能随 Python 两项一起转正。

**P19-B 精确修订（2026-10-22，所有者明确批准）：** 仅在原两项之外新增 `p8_negative/python_interop` 这一条 delta。该 fixture 的源码和负例身份不变；变化仅是显式 kwargs 到达 Python 后，原桥拒绝变为 Python 自身的签名 `TypeError`。manifest 与 checker 必须分别钉住固定 Stage0 的完整原拒绝，以及 Stage1/2 的完整归一化新诊断、退出状态和第 2 行位置；不按函数名特殊处理，不反射签名，不转换回旧错误，也不失败后重试。P19 Stage0 delta allowlist 恰为上述三个精确 ID；不新增通配、类别排除或第四项差异，其余历史投影继续严格比较。本修订不启动 P19-C，不改变 callback kwargs 的实施边界。

固定 Stage0 永远不获得 P19 能力。若某既有投影 case 的 Stage1/2 行为确需改变，只能增加**窄而显式**的 delta（准确 case ID、Stage0 原拒绝、Stage1/2 新 stdout/stderr/退出状态/阶段/位置），并更新 checker 与其测试；不得套用通用“命名参数差异忽略”规则。新增功能案例沿用 P18 的 Stage1/2 feature 覆盖模式，不假称 Stage0 支持。所有原 included IDs、fixture 调用、505 个性质样本及 P18 唯一既有 Stage0 delta `p14_negative/module_callback` 保留，不重 pin、不搬到 excluded、不降低原有 projection 合同。

当前 `checker.py` 的 delta allowlist 只允许 `p14_negative/module_callback`；`project_stage0.py` 只读取 manifest 的 `p18_stage1_feature` / `p18_stage1_cases`，checker 还要求 `p18_` 前缀与精确 callback arity 用例。因此后续实现必须为 P19 增加独立 feature ledger 字段及投影/checker/测试支持（或等价的显式版本化泛化），不能仅把 P19 案例塞进旧字段或改掉 P18 前缀检查。delta allowlist 只能逐个加入上述精确 ID，原 P18 专项校验与唯一旧 delta 必须保留。243 个既有 included IDs、244 次既有 fixture 调用和 505 个性质样本不得减少；因两项转正导致 compile/runtime-negative 分类计数变化必须显式更新 checker/projection 期望，而非取消计数断言。新增 P19 feature 样本另外计数，不掩盖原 inventory 的变化。

## 8. 门禁及本阶段提交边界

后续实现与现有 regression/bootstrap/CI 的对接命令：

```sh
python3 tests/bootstrap/check_supported_runtime.py
python3 tests/test_p18_callbacks.py
python3 tests/run_tests.py
python3 tests/test_differential_properties.py --seed 0x5EED2026
python3 tests/bootstrap/checker.py
python3 tests/bootstrap/test_checker.py
python3 tests/bootstrap/project_stage0.py --report /tmp/p19-projection.json
python3 -m unittest tests.test_latent_stage1 tests.test_bootstrap_host tests.test_bootstrap_lexer tests.test_bootstrap_parser -v
python3 tests/bootstrap/pending_snapshot_gate.py
# 提交后，对实际新 HEAD 运行原 clean-clone gate：
python3 -m unittest tests.test_bootstrap_smoke -v
```

固定 Stage0 为 `9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9`。提交前 pending snapshot 验证实际待提交源码的双根 Stage2/3 固定点；clean-clone gate 在提交前只验证旧 HEAD，不能冒充待提交源码证据。提交后须记录实际新 HEAD 的 clean-clone 结果。现有 CI 保持 Python 3.11/3.12 × JDK 17/21 回归矩阵、JDK17 上两版 Python 自举，以及独立 VS Code 检查/打包；本机单环境结果不等同完整远端矩阵通过。新 focused P19 案例必须挂入现有 CI 实际执行路径，不仅留为手工脚本。

P19-A 仅新增本文件，不改变 SPEC、README、编译器、runtime、bootstrap、tests、projection ledger、CI、Stage0 pin 或 preview launcher。本文件不是运行通过证据，实际执行结果在提交交付中单列。所有实现验收行在 P19-A 都属于待完成项。

## 9. 已发现冲突、保守规则与仍待后续落实事项

1. **SPEC 与 P18 明确禁止宿主命名/回调 kwargs。** P18 是已实现基线，本文件是有界的未来增量，不静默把旧规范改写成已支持。后续实现必须同步修订 SPEC §4/P8/P10/P11 与 P18 对应限制的适用状态，不能删除仍适用于 Java host、展开、非同步 callback 的限制。P19-A 不修改这些文件。
   另：SPEC §4 中仍有“Latent callable 作为宿主 callback 被拒绝”的旧概述，与已经交付的 P18 同步 Python callback 例外不一致。应把 P18 视为后续窄例外，后续文档更新须明确区分这项既有例外与 P19 新增命名能力，不能宣称 P19 才首次支持 callback。
2. **Latent 语法没有 `/`。** P19 只遵守目标 Python 的 positional-only；不为 Latent 回调添加 `/`，不引入新的 Param AST 或 schema 版本。若要增加 Latent positional-only，须另立语言提案，不能塞入 P19-B。
3. **默认值模型不同是有意保留。** 宿主用 Python 默认值；回调用 Latent 每次调用时默认值。统一桥传值，不统一两种语言的默认生命周期。
4. **名称可表达性不对称。** Latent 显式名称遵循 `NAME`，callback kwargs 可含任意字符串且由 extra 收集；不能以一致性为由开放宿主 `**`、保留字转义或拒绝合法 callback extra key。
5. **跨目标一致性不等于任意宿主异常类等价。** 遵守 SPEC §6/P18 的消息/位置与广义 Exception 限定，不许声称 `except TypeError` 等在所有 callback 错误上两端等价。Java unconditional-throw/unreachable-statement 缺陷仍是独立问题；负例继续用当前 Java generator 可编译的控制流，不能顺带修复或合并 bug/security 分支。
6. **实施待办而非悬空语义选择：** 双侧 protocol validator 的旧/新形状、kwargs codec 与直接命名 callback 槽、Stage1 emitter/prelude、错误归类和源位置、现有负例转型与 ledger 的逐项 delta，以及矩阵测试/CI 挂接都尚未实现。具体 helper 拆分、测试文件名和 exact Python traceback 不是本阶段冻结的公开 API。若实现发现当前 binder/语法无法遵守本契约，应先报告并修订契约，不用 hidden fallback、放宽 projection 或扩大语法来绕过。

## 10. 明确非目标

不做 runtime/compiler/test 实现；不做 P19-B；不添加 Latent→宿主 `*`/`**` 展开、Latent `/` 或新 AST 语法、Python 字符串名称调用语法、Java named arguments/反射参数名绑定、Java SAM/interface callback、通过 Python-target JVM daemon 反向 Java callback、持久/跨线程/异步/deferred callback、callback 注册/撤销 API、任意 callable 自动提升、容器隐藏 callback、callback 返回 callable、循环图或容器 alias identity 新承诺、host sandbox、性能/并发优化、宿主签名反射绑定或 fallback 重试。

不扩大 Stage1（仍 Python-only、opt-in、无默认切换，不增加 Java emitter/REPL/AST 或 editor modes），不改变 Stage0/投影原合同，不触碰独立 P17-D 文件与 bug/security 分支，不创建 tag、release 或 PR。本阶段仅可提交这份新文档到已核验的 main，并以普通 fast-forward 推送；发现 branch/remote/worktree 状态偏离授权基线时停止，不覆盖、不 reset、不 force push。
