# P10：变参形参与调用展开（v0.13.0）

> **状态：** P10 随 v0.13.0 发布，基于 v0.12.0 / P9 提交 `fe6f41f3815ea728bfc18ac99a09f81a4972cae2`。P9 默认参数及 `latent-ast` schema v4 保持兼容；本文说明 v0.13.0 新增的变参语法和 schema v5。

## 目标与适用范围

为 Latent 用户定义函数增加可变位置形参 `*rest`、可变命名形参 `**extra`，并允许 Latent 调用使用 `*items` / `**mapping` 展开实参。适用对象包括普通函数、函数值/闭包、公开模块函数、Latent 直接或绑定方法、继承/`super` 方法及构造器/`init`。两端共用相同的检查与绑定规则。

本次不增加仅限关键字形参，也不改变内建函数、Python/Java 互操作句柄方法或构造器的调用协议。它们不接受 P10 的展开实参；内建函数在已知时编译期拒绝，互操作调用在运行期以 `ArgumentError` 拒绝。函数值中的用户定义 Latent 函数仍可接收展开实参。

## 语法与顺序

函数签名按以下固定顺序排列：

1. 必需位置形参；
2. 默认位置形参；
3. 可选的单个 `*rest`；
4. 可选的单个 `**extra`。

因此 `fn f(a, b=2, *rest, **extra):` 合法；位置形参不能出现在默认形参之后或 `*rest` / `**extra` 之后。`*rest` 不能位于 `**extra` 后，同类收集器不能重复。方法的首个 `self` 仍必须是必需的普通位置形参。签名中 `*` 之后不支持新的关键字专用区域，例如 `fn f(*, option)` 不合法。

调用实参允许普通位置表达式、`*items`、`name=expression` 和 `**mapping`。所有普通位置实参与 `*items` 必须在第一个命名实参或 `**mapping` 之前；进入命名阶段后不能再出现位置实参或位置展开。命名实参与 `**mapping` 可交错，并按源码顺序处理。`super.method(self, ...)` 中显式接收者仍必须是第一个普通位置实参。

## 收集与展开语义

- `*items` 只接受 Latent 列表值，不接受字符串、映射或任意可迭代对象。它在当前实参位置浅拷贝列表中的元素并按列表顺序加入位置实参。语言中的 `range(...)` 结果是 Latent 列表，因此其结果可以展开；range 本身不是一种额外的迭代器类型。
- `**mapping` 只接受 Latent 映射值。它在当前实参位置按映射的**插入顺序**浅拷贝键值项；每个键都必须是字符串。非字符串键、非映射值或非列表位置展开均以 `ArgumentError` 失败。
- 显式实参表达式从左到右求值。每个列表/映射展开也在其源码位置验证并快照，随后才计算右侧的下一个实参表达式。因此展开表达式及其元素/映射值的副作用具有确定的源码顺序。
- 绑定时，位置值先填充固定位置形参（必需形参和默认形参），其余位置值进入 `*rest`。命名值优先匹配固定形参；未知名称仅在存在 `**extra` 时进入该收集器。两个收集器始终会收到 Latent 原生列表/映射；没有多余值时分别为空列表/空映射。
- 收集器变量名不是额外的普通形参名。例如 `*rest` 的 `rest=...` 不会把列表收集器替换掉；若存在 `**extra`，它会作为未知命名值进入 `extra`，否则是未知名称错误。
- P9 默认值继续按原规则工作：实参绑定后，仅对省略的固定默认形参按形参顺序求值；显式 `nil` 不触发默认值。位置展开按顺序填充默认位置形参，因此调用方若要把值送入 `*rest`，需先提供前面的固定位置槽位（包括默认槽位）。

## 重复值和错误检查

- 同一命名键出现多次（显式 `name=...` 与一个或多个映射展开之间也一样）报 `got duplicate named argument`。多个 `**mapping` 按插入顺序处理，冲突不会静默覆盖。
- 固定形参同时由位置值和命名值提供时，报 `got multiple values for argument`。
- 没有 `*rest` 时，多余位置值报过量实参；没有 `**extra` 时，未知命名值报未知参数；必需形参未由位置或命名值提供时，报缺失必需参数。
- 签名已知时，编译期仍检查明显可知的重复、未知及过量显式实参。展开内容的长度、键集合以及由展开引入的冲突/缺失，在运行期统一检查；函数值或动态接收者也使用相同运行时绑定器。Python 与 Java 后端的 Latent 参数错误均归类为 `ArgumentError`，并映射到 `.lt` 源位置。

## 版本与 AST

P10 在词法器原有 `*` / `**` token 上扩充解析；稳定 AST 由 v4 升至 **v5**：

- 函数参数列表新增 `RestParam(name)`、`ExtraParam(name)`；`DefaultParam(name, default)` 和必需形参字符串保持原样。
- 调用参数列表新增 `StarArg(value)`、`StarStarArg(value)`；`Call(function, arguments)` 字段名/顺序不变。
- v1–v4 的既有节点字段与顺序保持不变，包括 `NonlocalStmt`、P8 `NamedArg`、P9 `DefaultParam`。AST 版本字段变为 5，消费端需在读取新 dump 前接受 v5。

采用 P10 语法的源码需要 v0.13.0 或更新版本；v0.12.0 / P9 的 tag、源文件与产物保持不变。

## 覆盖范围与验证用例

- `tests/p10_variadic_arguments.lt`：普通函数、默认值、空/多个展开、闭包/函数值、额外名称顺序、副作用顺序、Latent 构造器、继承方法、绑定方法和 `super`。
- `tests/modules/variadic/`：公开模块函数及其函数值。
- `cookbook/variadic_arguments.lt`：可运行示例。
- `tests/p10_negative/`：签名/调用顺序、内建函数及 Python/Java 互操作排除项、缺失/未知/重复/过量实参、非法展开类型和非字符串映射键。
- `tests/test_diagnostics.py`：schema v5 新节点及历史节点形状回归。

## 最终验证记录

- 在排除 `.git`、`out/`、`P7_PLAN.md`、`vscode-latent/` 和 VS Code 测试用户数据的隔离副本运行完整 `python3 tests/run_tests.py`：**180 passed, 0 failed**；12 项只读 AST/CLI 诊断通过。180 项合计包含 P9 原有但旧总数未计入的 6 个静态负例，并包含 25 项新增 P10 回归。
- 对全部 13 个 tracked Python 文件运行 `py_compile` 通过；`javac runtime/*.java` 成功；`git diff --check` 通过。P8/P9 冒烟样例的 Python、Java 输出逐字节一致。
- 发布范围只纳入插件源代码、测试、元数据和文档；生成缓存、临时测试输出及历史 VSIX 均排除，`P7_PLAN.md` 保持原样。插件 VSIX 0.6.2 作为 v0.13.0 GitHub Release 资产单独提供。
