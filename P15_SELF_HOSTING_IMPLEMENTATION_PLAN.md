# Latent 自托管编译器实施计划（P15）

> **状态：范围已获批准，实施进行中。** 用户已明确授权按本计划实施、测试并推送；此授权不包括创建 tag/Release 或修改 main 分支保护。最终完成仍以本计划全部 DoD 为准，阶段性切片不得冒充完整 v0.13.5 自举。
>
> **依据：** [P15 自举设计提案](P15_SELF_HOSTING_BOOTSTRAP_DESIGN.md)。本计划落实该提案已批准的首期范围，不重新扩大或缩小范围。
>
> **基线：** v0.13.5，Stage0 固定提交 `9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9`。CI 以 Python 3.11 为基础，自举验证使用 JDK 17；现有完整回归仍保留 Python 3.11 / JDK 17 与 21 矩阵。

## 1. 目标、边界与不变量

交付一个由 Latent 编写核心、以 Python 为输出目标、运行在 Python 宿主上的自托管编译器。目标阶段链为 **固定 Stage0 → Stage1 → Stage2 → Stage3**：Stage0 仅用于启动自举；Stage1/2/3 的编译策略由 Latent 源码实现；同一构建根下 Stage2 与 Stage3 的规范化产物须逐字节相同，并且在两个不同绝对路径的干净 checkout 中，Stage2/3 产物也须逐字节相同。

首期承诺完整覆盖 **Latent v0.13.5 的 Python 目标语义**，包括显式本地 `.lt` 模块、`-o`、`--run`，以及 Python 目标既有的 Java/Python 互操作行为和必要随附运行时。只实现 Python 代码生成；不实现 Java 代码生成。Python 3.11、标准库和明示的薄 Python 宿主适配层可作为运行环境，但不能代替编译器核心。

### 必须保持在 Latent 中的内容

Token/源码位置/错误结构、AST、词法分析、递归下降解析、脱糖、名称绑定与全部静态语义、模块发现与依赖图/符号改写、Python emitter（包括字符串字面量转义与源码映射）、参数与目标选择策略、CLI 解析、诊断构造、编译失败/成功状态和产物清单策略。Stage1+ 不得导入、打开、调用或以子进程启动 Stage0/Python 版编译器模块，也不得把这些工作委托给 Python 适配器。

### 明确暂缓（不计入首期完成条件）

Java 后端代码生成、REPL、AST/脱糖输出诊断模式、编辑器元数据模式、包管理/搜索路径、循环模块、增量编译、宏、安装器，以及脱离 Python 宿主的 native/host-independent bootstrap。暂缓这些产品能力不代表可以用 Python 实现代替相应编译核心。

## 2. 依赖顺序与里程碑

规模仅为相对估计：**S / M / L**，不是工期或日历承诺。候选源码布局为 `bootstrap/model.lt`、`lexer.lt`、`parser.lt`、`desugar.lt`、`semant.lt`、`module_graph.lt`、`gen_py.lt`、`compiler.lt`、`main.lt`，以及短小、版本化的 `bootstrap/latent_host.py`。实际拆分须在实现时依据引用关系形成无环依赖图；这些路径/文件名是计划候选，不构成已存在文件。

| ID / 规模 | 目标、可能模块与交付 | 前置条件 | 聚焦验收门（通过前不得进入后续扩张） | Python 边界：不得委托给宿主 |
|---|---|---|---|---|
| **M0 范围与测试账本 / S** | 冻结编译器核心、宿主接口、阶段目录/manifest 约定；建立基线测试投影清单的格式和未分类检查方案。只产出计划实现所需的设计记录/清单结构。 | 用户日后明确请求开始实施；本计划及 P15 设计范围。 | 评审确认各模块依赖无环、每个当前测试条目将被明确纳入或附理由排除；本地模块身份、逻辑源 ID、错误结构、CLI 与产物文件约定清晰。 | 不编写 Python 编译逻辑；账本只做测试分类，不运行或包装 Stage0 来冒充 Stage1。 |
| **M1 编译器骨架与宿主适配契约 / M** | 建立 `.lt` 编译器入口、阶段错误/结果模型和最小调用骨架；实现薄 `latent_host.py` 的版本化 API 草案及契约测试。接口覆盖 argv、stdout/stderr/退出、路径操作、UTF-8 文件/目录、固定运行时文件复制；`--run` 所需进程接口可先留在契约中。 | M0。 | 宿主接口逐项有输入、结果、失败表示及副作用说明；路径/文件错误可转为稳定结果；adapter allowlist 可审查；构建目录和源码目录隔离。 | 不解析 CLI 策略、不读取/扫描 `.lt` 源码、不分析模块、不构造 token/AST、不生成 Python 代码；标准库仅用于明示 OS 服务。 |
| **M2 首个垂直切片（最小端到端与微型自举） / M** | 在最终候选模块和接口内，先实现一小块保持可扩展的 Latent 编译器：足以编译一个无模块、确定性的小型 v0.13.5 程序；Stage0 编译该微型 `bootstrap/compiler.lt` 得到 Stage1，Stage1 编译同一编译器源码得到 Stage2，Stage2 再得 Stage3。切片不另建 Python compiler prototype。 | M1；已有 Latent 子集及 Stage0。 | 在隔离输出目录运行一个现有或等价的极小 Python-target fixture；比对 Stage0/1 的编译结果和程序 stdout/退出状态；Stage2/3 manifest 集合一致且逐文件字节一致；Stage1 在看不到 Stage0 文件时仍能编译 fixture。任何一项失败即停止扩张并修复。 | 切片里的 tokenization、语法、节点、目标代码生成和流程仍由 `.lt` 实现；Python 只提供文件/路径/输出/进程服务。此切片只是早期架构门，不代表完整语义或产品验收。 |
| **M3 完整词法分析 / M** | 完成 v0.13.5 全部 token、关键字、操作符、注释、数字/字符串/转义与行列位置处理；候选 `model.lt`、`lexer.lt`。 | M2；保留切片可通过。 | 词法边界、Unicode、转义、坏字符/未闭合字面量位置等聚焦用例通过；逐项与 Stage0 词法/编译可观察结果对拍；切片 Stage0→1→2→3 门禁持续通过。 | 扫描与 token 归类须由 Latent 完成；不可使用 Python `tokenize`、正则规则或 Python 异常文本决定语言诊断。 |
| **M4 AST 与完整解析器 / L** | 定义带逻辑文件 ID、行列的 AST 节点；实现 v0.13.5 递归下降语法，覆盖函数/闭包/类/继承、列表与映射、循环/分支、异常、参数形态、导入等全部 Python 目标所需语法；候选 `model.lt`、`parser.lt`。 | M3。 | 对每一语法族建立有效/无效输入与源位置检查；语法错误稳定、非零失败；全量切片继续可自编译。解析器接受面不得因为暂时没有后端而静默漏掉 v0.13.5 语法。 | AST schema、递归下降流程、语法边界和 parser diagnostics 都写在 Latent；不使用 Python AST/parser/第三方 parser。 |
| **M5 脱糖与中间表示归一 / M** | 覆盖字符串插值、点访问/调用转换、末表达式隐式返回，以及现有脱糖规则；建立各阶段输入/输出节点不变量。候选 `desugar.lt`。 | M4。 | 对应正例、脱糖前后行为对拍、错误位置保留；各节点种类均有处理，未识别节点必须报内部阶段错误而非忽略。AST/脱糖 CLI 展示模式仍暂缓。 | 脱糖变换、节点构造与顺序全部由 Latent 执行；不委托 Python AST transform。 |
| **M6 完整静态语义 / L** | 实现作用域、读前绑定、global/nonlocal、函数/闭包、调用参数绑定、内建函数、类/继承、override 兼容、`super`、interop 调用检查等 v0.13.5 Python 目标共用规则；候选 `semant.lt`。 | M5。 | 覆盖全部现有适用编译期负例及位置断言；正例无误拒；与 Stage0 诊断类别/消息/逻辑源位置对拍；不允许语义错误后继续发射。 | 名称解析、签名绑定、类型/类关系判断、调用可行性和诊断模板都在 Latent；Python 不能承担语义判定。 |
| **M7 本地模块图与稳定身份 / L** | 实现显式相对 `.lt` 导入、导入文件目录相对解析、真实物理路径访问与逻辑源 ID 分离、导出/可见性、循环拒绝、依赖顺序、去重、碰撞检查、跨模块源码位置与确定性符号改写；候选 `module_graph.lt`。 | M6；模块接口和逻辑 ID 方案冻结。 | 正/负模块 fixture 覆盖缺失文件、扩展名、嵌套/迟到导入、循环、私有/不存在导出、碰撞、别名/同一模块复用；不同导入拼写指向同文件时只编译一次；路径根不同不改变逻辑模块身份与序列化顺序。 | 模块规则、图遍历、依赖排序、符号分配与语言诊断均在 Latent；adapter 只做被要求的路径/文件操作，不推断搜索路径或扫描源码树。 |
| **M8 Python 代码生成与目标运行时 / L** | 实现 v0.13.5 Python 后端、目标字符串编码/转义、数值文本规则、源码映射、生成程序运行时约定、模块包装/导入，以及 Python target 中既有 Java/Python interop 所需生成内容和固定随附运行时；候选 `gen_py.lt`。 | M5、M6；M7 的逻辑 ID/模块 API。 | Python 后端正例与输出断言通过；运行期错误、Latent 源位置、模块初始化顺序和互操作行为对拍；转义/数值边界、UTF-8/LF 稳定；生成物可由 Python 3.11 执行。 | emitter、Python 字面量转义、源码映射、符号/临时名及运行时调用约定由 Latent 生成；禁止 `json.dumps`、`repr` 或 Python 模板代码代替 Latent emitter。Java 后端生成器仍不实现。 |
| **M9 CLI、诊断与安全写出 / M** | 完成源文件、默认或显式 `-t py`、`-o`、`--run`、帮助/参数错误、明确拒绝 `-t java`、稳定阶段诊断、退出状态、产物完整写出后才成功，以及原子/隔离输出策略；候选 `main.lt`、`compiler.lt`。AST/脱糖和编辑器模式不实现。 | M1、M4–M8。 | 对缺失/不可读输入、坏模块、无效 target、不可写输出目录、编译失败无半成品、运行失败、`--run` stdout/stderr/退出映射分别断言；任何失败非零；不写源码树。M2 切片仍通过。 | CLI 语义、参数判定、诊断拼装、是否运行、退出码和目标选择由 Latent 决策；adapter 只暴露原始 argv、I/O 与可选进程服务。 |
| **M10 Stage0→Stage1（完整目标实现） / L** | 以固定 v0.13.5 Stage0 编译完整 `bootstrap/compiler.lt` 本地模块图到独立 Stage1 目录；组装经过审阅的宿主 adapter 和固定运行时，写出稳定清单。 | M0–M9；完整投影清单已有可计算结果。 | Stage1 可从隔离目录直接启动，且目录中无 Stage0/Python compiler；Stage0/Stage1 在初始 Python-target 投影上退出状态、stdout、规范化诊断与运行期源码位置一致；没有源码树写入或未声明文件。 | Stage0 是唯一可信启动种子，不能成为 Stage1 的运行依赖或代编译 subprocess；生成 Stage1 的核心实现必须是 `.lt`。 |
| **M11 Stage1→Stage2 / L** | Stage1 使用其自身 CLI、同一编译器源码、相同 host contract 编译到全新 Stage2 目录。 | M10。 | Stage1/Stage2 对已支持投影逐项行为一致；Stage2 文件清单/入口/接口与 Stage1 固定；Stage1 编译时不访问 Stage0、Python 版 latent modules 或上一次输出目录。 | 自编译由 Stage1 执行；禁止 Python 外层 driver 代替 stage 编译，外层只可编排目录和调用已生成 CLI。 |
| **M12 Stage2→Stage3 与同根 fixed point / M** | Stage2 编译同一源码到另一个全新 Stage3 目录；定义 manifest 的规范化路径、排序与 hash。 | M11。 | Stage2/3 manifest 相对路径集合完全相同；每个清单文件 SHA-256 和字节均相同；无多余产物；失败保存最小差异摘要；不复用输出目录。 | 比较器可由简单外层工具实现；产物内容及 manifest 的语义、文件名/顺序规则由 Latent 编译器稳定产生，不写入 stage 序号、时间、随机数、用户名、cwd 或绝对根。 |
| **M13 双绝对根目录复现 / L** | 在两个路径前缀不同的全新 checkout 分别运行 Stage0→1→2→3，比较规范化 Stage2/3 产物。 | M12；逻辑 ID 与所有生成路径策略完成。 | 每个 checkout 内 Stage2==Stage3；两个根的 Stage2/3 路径集合、manifest 与内容逐字节相同。Stage1 可以因 Stage0 现有绝对路径行为而有差异；该例外不得外推至 Stage2/3。 | 物理路径仅用于 I/O；编译身份、名称、序列化顺序与 source map 使用稳定逻辑 ID。禁止用 Python 侧全局字符串替换产物路径来制造表面复现。 |
| **M14 全量 Python-target 一致性 / L** | 完成测试账本每项的纳入/排除判定和 Stage0/1/2 对拍；将所有适用测试持续纳入自举门禁。 | M10–M13；账本及测试 runner 已有完整条目归属。 | Python 投影纳入项全绿且差异为零；未分类新增项为零；排除项逐项有理由及范围所有者；现有 Stage0 全套双后端基线仍保持。动态报告分别给出案例数、排除数、stage 差异数。 | 通过策略是补齐 Latent compiler 或明确审定真实 scope exclusion；不得在 Python/runner 中加入面向某个 stage 的语义特判、删用例、宽泛忽略诊断差异。 |
| **M15 CI 与最终门禁 / M** | 保留现有全量双后端回归矩阵，新增 Python 3.11/JDK 17 bootstrap job；运行清单分类、stage 流、双根复现、allowlist 与源码树只读/无 Stage0 依赖检查；失败时保存受限诊断 artifact。 | M0–M14。 | CI 从干净 checkout 运行成功；现有 Python 3.11/JDK 17、21 全回归不退化；bootstrap job 两根都通过；新增测试未分类时失败；日志不打印环境变量/敏感值。通过不自动发布 Stage 产物。 | CI 编排脚本只准备 Stage0、调用 Latent 编译器、验证 manifests 和收集结果；不得暗含 Python 编译器实现或修补 stage 输出。 |

## 3. 测试投影：逐项覆盖，不用固定数字缩窄

现有 README 描述的 **259 项**是 v0.13.5 回归规模的历史说明；本任务未运行测试重新核定该数值，也不能直接把 259 当作 Stage1 投影的数量。投影必须以仓库当前测试驱动和测试文件为来源，动态生成清单、总数及各类别计数。**Stage0 继续承担现有完整回归（含双后端）；Stage1/2/3 的完整性依据则是逐项定义的 Python-target 投影。** 两个集合不应混为一谈。

投影清单逐项纳入或排除 `tests/run_tests.py` 的每个测试名/fixture 与辅助脚本条目，至少覆盖：

1. `POSITIVE`、`MODULE_POSITIVE` 的所有 Python 编译/执行、stdout、stderr、退出状态断言；不能只选择 Python 专属案例而漏掉 `mixed`、Java/Python interop、模块、继承、闭包、各版本参数调用等 Python target 适用案例。
2. `NEG_COMPILE`、P8/P9/P10/P11/P12 参数/语法/override 负例、继承语义负例、模块负例的 Python 编译拒绝；纳入消息 marker、诊断阶段与源文件/行/列断言。当前测试若分别检查 py/java 共同行为，Stage1 投影只比较 Python 目标，但 Stage0 双目标原测试仍保留。
3. 全部 `NEG_RUNTIME` 中适用于 Python target 的用例：非零运行失败、runtime error marker、`.lt` 源位置；包括通过 Python target 使用的 Java interop 相关行为。不得把异常行为误列为 Java codegen 专属。
4. 全部 `COOKBOOK` 条目：逐一检验是否是 Python target 可执行/interop 行为；对确属 Java 代码生成专属的文件/断言，记录确切条目和排除理由，而不是按 cookbook 整组排除。
5. `test_differential_properties.py` 的固定种子边界案例（数值、Unicode、列表索引等）：保留 Python target 对应结果；若原测试是跨后端比较，Stage0 的原双后端比较照常运行，同时 Stage1/2 与 Stage0 Python 结果逐项对照。
6. `test_p14_protocol.py` 及其他辅助协议用例：依赖/观测若进入 Python target 的 Java interop 则纳入；只有经审查确认是 Java 后端专属时才可排除并说明证据。
7. 一般编译与运行诊断（错误位置、错误阶段、非零状态）纳入；`test_diagnostics.py` 中仅用于 AST/脱糖 CLI 输出或只读模式的断言可因该模式已明确暂缓而排除自举投影，但须在报告中单独列明；其余普通编译诊断不得借此排除。
8. `test_editor_metadata.py` 和编辑器 metadata 模式可排除自举投影，因为是已暂缓接口；Stage0 的现有 editor metadata CI/契约测试仍保留，不得移除或削弱。

清单字段至少包括稳定测试 ID、源 fixture/辅助脚本、Python applicability、纳入/排除状态、排除理由、断言类型（成功输出/编译诊断/运行错误/源位置/CLI/互操作/性质）和引入版本。CI 必须检查测试树和现有 runner 新增条目均有清单归属；新增但未分类即失败。任何范围变更都先更新并审查清单，禁止通过减少基线、宽泛正则替换诊断、忽略 stage 差异或只汇报总数来“通过”。

## 4. 固定 Stage0、阶段产物与 manifest 策略

- **Stage0 pin：** 首期固定 `9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9`，不可自动跟随分支、tag 或 HEAD。构建日志和 manifest metadata 记录 pin 和 seed 输入标识；不得把其实现计为 Stage1。
- **更新政策：** 只有单独审查过的显式改动才能更新 Stage0 pin；记录旧/新提交、compiler/固定运行时输入 hash、变更理由与影响；用旧/新 seed 对同一 Python 投影与全量 Stage0 回归进行比较，并重新通过 Stage0→1、Stage1→2、Stage2→3、双根复现。更新种子不自动批准语义或目标范围变更。
- **阶段目录：** 源码、Stage1、Stage2、Stage3 和第二 checkout 均独立；不能复用或覆盖上阶段输出。编译错误不得留下看似成功的主产物；成功后才报告成功。测试不得向源码树写产物。
- **稳定输出：** 固定清单至少记录相对路径排序后的 `compiler.py`、版本匹配的 `latent_host.py`、生成编译器必需的固定运行时文件及其 SHA-256。文本为 UTF-8、换行 LF；manifest 本身不得嵌入自身递归 hash，可由单独 envelope 或固定约定处理。只纳入正式声明的文件；`__pycache__`、日志、临时文件、class 文件等不作为编译器产物。
- **禁止非确定性来源：** 产物不得含阶段号、构建时间、随机数、机器用户名、checkout 绝对根、当前 cwd、locale 或环境变量；模块顺序、临时名、符号分配、mapping/source-map 序列化必须确定。路径比较使用逻辑源 ID 和规范化相对路径，不做宽泛临时根替换。
- **比对报告：** CI 留存每一阶段的规范化 manifest、文件名/hash、逐文件差异摘要、投影用例计数和最小复现路径信息；正常成功不发布 Stage 产物。日志不可输出用户环境变量或凭据。

## 5. 停止 / 继续门槛

- **G0 — 合同冻结：** M0/M1 的模块 DAG、host API、逻辑源 ID、CLI 与产物清单方案经审查通过。出现需要新增 Latent 语言特性、扩大宿主 API 或改变 Python-target 兼容面时先停下并重新确认范围。
- **G1 — 垂直切片：** M2 的微型 Stage0→1→2→3、无 Stage0 可见的 Stage1 编译和 fixture 行为全部通过。若需要 adapter 里增加 lexer/parser/emitter/语义逻辑，或 Stage1 运行依赖 Stage0，停止并纠正架构，不继续堆功能。
- **G2 — 子系统门：** M3–M9 每个子系统的聚焦正/负例与 Stage0 对拍通过，源码位置不丢失，微型 fixed-point 继续通过。新增语法/语义若需超出批准 v0.13.5 范围，先停下评估，不把“自举需要”当作新增语言功能授权。
- **G3 — 完整 Stage1：** M10 的 Python-target 初始投影零差异，Stage1 在完全不提供 Stage0 文件/模块的环境中可运行。失败按差异类别修复；不得用测试排除或 stage 特例绕过。
- **G4 — 闭环与根目录：** M11/M12 固定点达成，M13 双根字节一致。任何绝对路径泄漏、额外产物或不稳定顺序都阻断宣称“可复现自举”。
- **G5 — 完整兼容与 CI：** M14 全部适用用例 100% 通过、零未分类项、所有排除已说明；M15 的全量基线与 bootstrap job 全绿。未通过前不宣布首期 Done、不发布构建产物。

## 6. 关键风险与顺序控制

1. **范围庞大、前端差异累积：** 先做 M2 垂直切片，再按 lexer→parser/AST→desugar→semantic→modules→Python backend 推进；每个阶段持续对拍，不等到最后才发现兼容缺口。
2. **Latent 现有容器/字符串能力受限：** 只用 v0.13.5 已有列表、字符串、字符串键映射、函数/闭包/类、循环、异常和内建；所需排序、去重、扫描和转义用 Latent 小算法实现并独立测。若发现真实缺少语言能力，作为范围问题停下来，不在 Python adapter 偷加原语。
3. **适配层膨胀：** host API 有 allowlist 和契约测试；每次新增 OS 原语均说明不可由已有接口组合的理由。adapter 不得出现 compiler token/AST/semantic/emitter 类型逻辑。
4. **Stage0 绝对路径渗漏：** Stage1 可能继承 Stage0 当前路径行为，但 Stage2/3 双根一致是硬门；物理路径与逻辑源身份自 M0/M7 就分离，不能最后靠文本替换补救。
5. **Python 代码格式化/错误位置漂移：** 字符串/数字编码、LF、源映射、模块顺序作为独立 emitter 验收；语言错误由 Latent 生成，adapter 只把 I/O 失败映射到稳定错误类型。
6. **基线清单随版本增长：** 测试分类与测试新增同一提交更新；报告动态显示数量和纳入/排除差异，不把旧的 259 当作固定总量或覆盖承诺。
7. **误称独立性：** 文档、CI 和发布说明称“Python 宿主上的可复现自托管 bootstrap”；不称 native、host-independent 或无需 Python。后续独立种子/VM/native 路线另行设计。

## 7. 最终 Definition of Done

只有同时满足以下条件才算首期完成：

- 编译器的 lexer、parser/AST、desugar、静态语义、本地模块图、Python emitter、CLI 策略和诊断实现均在 Latent 源码中；Python adapter 仅提供已审查的 OS 服务。
- v0.13.5 Python-target 语义完整，显式本地 `.lt` 模块、`-o`、`--run` 与 Python 目标既有互操作行为保持兼容；`-t java` 明确报不支持。暂缓功能不被默默实现或声称支持。
- Stage1/2/3 可独立启动；Stage1 在无 Stage0 可见时工作；Stage0/1/2 对完整 Python-target 投影逐项零差异；原有 Stage0 双后端基线仍通过。
- 每个 checkout 内 Stage2==Stage3；两个不同绝对根的 Stage2/3 manifests 和全部声明产物字节一致，无额外未声明文件、无源码树写入、无绝对根泄漏。
- 测试投影包含全部适用现有基线案例，逐项排除均有理由，新增案例零未分类；跨阶段成功/失败、stdout/stderr、退出状态、诊断位置均按约定核验。
- 现有 Python 3.11/JDK 17 与 21 全量 CI 保持；新增 Python 3.11/JDK 17 自举 job 通过，日志和 artifacts 符合安全与复现策略。
- 文档准确声明依赖 Python 宿主，不将本首期描述为 native 或 host-independent；Stage0 pin 与其更新流程明确。

## 8. 本计划的执行记录与授权边界

- 已批准并执行推荐边界；Stage0 pin 仍为 `9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9`。
- 已完成 M0 投影账本初版：243 个 runner case 全部有 Python-target disposition，0 个未分类；加入 bootstrap lexer/parser 覆盖后，291 个清单路径已归类。checker 与其 3 项测试通过。
- 已完成 M1 宿主 API v1 初版：仅提供 argv、stdio、路径/UTF-8 文件操作、显式文件复制及 `--run` 所需生成程序执行；宿主契约 5 项测试通过。
- 已完成 M2 微型切片初版：固定 Stage0→Stage1→Stage2→Stage3；两个绝对根内 Stage2/3 的声明产物逐字节一致，小型 fixture 与 Stage0 输出一致，缺失输入稳定失败。此门禁只覆盖微型子集。
- M3 的 Latent ASCII 词法切片已实现，并以固定 Stage0 编译运行 fixture；词法边界/转义/运算符用例通过。已知差异：参考实现接受 Unicode 字母、数字及更多 Unicode 空白，而本切片目前仅实现 ASCII 字符分类，因此 M3 的完整 Unicode 对拍门尚未通过；没有把字符分类交给 Python 宿主。
- M4 的 Latent 映射 AST/递归下降 parser 限定子集已通过固定 Stage0 fixture（结构块、常用表达式优先级、调用/属性/下标/列表/map 与解析位置诊断）。类/继承、导入、异常、global/nonlocal、完整形参与调用参数、py/java/super、插值等完整语法仍未实现，不构成完整 M4 验收。
- 当前本地 Stage0 全量回归已通过：259 passed、0 failed；运行环境为 Python 3.12.3/JDK 25.0.4（并非 CI 目标版本）。M0/M1/M2/M3/M4 的聚焦测试在当前环境通过；新增 bootstrap CI 使用 Python 3.11/JDK 17，原有 Python 3.11/JDK 17、21 全量矩阵保留。
- 本次不创建 tag/Release，不修改 main 分支保护。
