# Latent 自托管编译器实施计划（P15）

> **状态：** P15 原 DoD 与旧 CI 曾通过；M13 审计发现第二根仅复制 `bootstrap/`，后续审阅又剔除了会污染干净检出的工作树 overlay。当前严格实现已在本地提交 `3c23a2323efe1fd658ad74026e6d14b146a32176` 上通过 clean-clone 全套验证：bootstrap 28/28、Stage0 基线 259/259；最终 manifest 为两份同提交且 `git status` 干净的完整 checkout，构建无写入，Stage2/3 根内及跨根字节/manifest 零差异。现待记录验收结果、推送并验证新 GitHub CI。用户授权实施、测试并推送；不包括创建 tag/Release 或修改 main 分支保护。范围仍为 Python 宿主上的可复现自托管 bootstrap，不声称 native 或 host-independent。
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
- M3 Unicode 词法差异已补齐：Latent lexer 现用 Latent 二分区间查找实现字母、`isalnum` 数字尾缀、`isdigit`、十进制数值与 `isspace`；静态表覆盖 Unicode 14.0/15.0，Latent 只读取 `unicodedata.unidata_version` 以选择表，宿主不做字符分类。固定 Stage0 token/error-position 差分与属性边界、所有十进制码位对拍，已分别在 Python 3.11/UCD 14 和 Python 3.12/UCD 15 通过。非打印、非空白字符的错误 repr 拼写仍只比较拒绝结果及行列位置；该诊断显示差异不改变字符分类。未知 UCD 版本目前回退到 Unicode 15 表。
- M4 的 Latent 映射 AST/递归下降 parser 限定子集已通过固定 Stage0 fixture（结构块、常用表达式优先级、调用/属性/下标/列表/map 与解析位置诊断）。类/继承、导入、异常、global/nonlocal、完整形参与调用参数、py/java/super、插值等完整语法仍未实现，不构成完整 M4 验收。
- 本轮 Stage0 Python-target 完整回归已通过：259 passed、0 failed；运行环境为 Python 3.12.3/JDK 25.0.4。bootstrap lexer/parser/host/smoke 聚焦套件 10 项通过；M3 lexer 三项 Unicode 差分/属性边界测试亦在 Python 3.11/UCD 14 和 Python 3.12/UCD 15 分别通过。新增 bootstrap CI 使用 Python 3.11/JDK 17，原有 Python 3.11/JDK 17、21 全量矩阵保留。
- 本次不创建 tag/Release，不修改 main 分支保护。

### 本步记录：Latent Python emitter 首个增量切片（未完成 M8）

- 按用户本轮明确重排的顺序，先在 `bootstrap/gen_py.lt` 实现当前 AST 子集的 Python emitter，并将 `bootstrap/compiler.lt` 改为调用 Latent lexer/parser/emitter；宿主只处理 argv、路径、UTF-8 文件、原子写出、临时构建目录及生成 Python 程序进程服务。Java 后端未改动。
- emitter 覆盖当前 M4 parser 实际产出的函数（必需位置形参）、赋值、表达式语句、say、return、if/elif/else、while、for、break/continue，以及数字、字符串、布尔、nil、名称、二元/一元运算、调用、属性、下标、列表和字符串键映射；它不实现完整 v0.13.5 脱糖、语义、模块图、interop 或运行时约定。
- 端到端 smoke 已改为固定仓库 `bootstrap/` 源根下的 Stage0→Stage1 对拍；Stage1 对小型 fixture 的输出/退出状态匹配 Stage0，Stage1 生成物在同一源码根下重复生成字节一致。由于当前 parser 不支持 `import`/模块图，Stage1→Stage2→Stage3 暂不由本步覆盖；当前模块符号/source map 仍含规范化绝对路径，跨根复现明确延后。
- 实现过程中回归测试发现 M3 lexer 在 `dedent` 后重新开启 sibling 缩进块时保留了旧栈槽位，导致后续 AST 块归属错误；已通过重用弹栈后的槽位修复，并增加固定 Stage0 token 差分案例。
- 后续仍需完成完整 parser/AST、脱糖、静态语义、模块图/逻辑源 ID、完整 Python emitter 与 target runtime、CLI/`--run` 集成、Stage1→3 闭环、双根复现、Python-target 全量对拍及 CI 门禁。当前切片不代表 M4/M8 或 P15 DoD 完成。

### 本步记录：M4 形参、调用实参与 interop 前缀语法增量（未完成 M4）

- `bootstrap/parser.lt` 现以 Latent 解析 P8–P11 形参形式：必需位置形参保留原字符串 AST 形状；默认位置参数为 `DefaultParam`；`*rest`、`**extra`、裸 `*` 和必需/默认仅限关键字形参分别生成 `RestParam`、`ExtraParam`、`KeywordOnlyParam` 等带行列位置的映射节点。位置区“必需在默认之前”、裸 `*` 后必须有仅限关键字形参、收集器顺序/重复等解析规则和 Stage0 诊断均已覆盖。
- 调用实参支持位置、`name=value`、`*items`、`**mapping`，保持位置展开必须先于首个命名实参、命名实参可出现在映射展开前后、映射展开可多次出现。简单位置参数 AST 仍保留旧表达式节点；命名与展开实参分别使用 `NamedArg`、`StarArg`、`StarStarArg` 节点。
- 表达式 primary 现解析 `super`、`py <primary>`、`java <primary>` 为 `Super`、`PyImport`、`JavaImport` AST；这些仍只是语法树构造，不包含 super 语义或互操作检查。其 emitter 当前以明确 codegen 错误拒绝，避免生成缺少目标运行时的代码。
- `tests/bootstrap_parser.lt` 断言新增 AST 字段与旧简单形状；`tests/test_bootstrap_parser.py` 使用固定 Stage0 动态生成的期望，对 15 个输入（6 个接受、9 个拒绝）逐项比较接受结果，并对拒绝输入精确比较行、列、诊断消息。覆盖签名组合、错误签名、位置/命名/展开实参、顺序错误、尾逗号边界和 `py`/`java`/`super` 前缀。该增量复用已在 M0 ledger 分类的 parser 测试/fixture 路径，未新增清单路径；ledger checker 本步通过。
- **当前仍不支持的 M4 语法/AST要求：**`import "..." as name`、`global`、`nonlocal`、`class`/继承主体、`try`/`catch`、`throw` 语句以及字符串插值语法；AST 仍只有行列、没有 M4 要求的逻辑文件 ID。`super` 当前只是通用 `Super` receiver/dot/call 形状；`py`/`java` 尚无对应目标运行时。P8–P11 新参数/实参节点的 emitter 支持与语义分析边界记录在下一步。本计划不据此宣称接受完整 v0.13.5 grammar；M4、M5–M15 与最终 DoD 均未完成。
- 本步最终验证：`python3 -m compileall -q bootstrap tests` 通过；bootstrap host 6 项、smoke 1 项、lexer/parser 5 项、ledger checker 3 项均通过；Stage0 全量 `python3 tests/run_tests.py` 为 **259 passed, 0 failed**。本机环境 Python 3.12.3 / OpenJDK 25.0.4.1；未在本步重跑 CI 规划的 Python 3.11/JDK 17、21 矩阵。Stage0 全量结果不替代 M4 完整语法或自举门禁。

### 本步记录：P8–P11 参数与调用实参 emitter 增量（未完成 M8）

- `parser.lt` 保留 `FnDef.params` 中简单必需位置形参的旧字符串形状，并增加同序 `param_specs` 描述供 emitter 使用；parser fixture 覆盖新描述，不改旧字段的简单 AST 断言。
- `gen_py.lt` 现发射默认位置参数、`*rest`、必需/默认仅限关键字参数、`**extra`，并生成调用时默认求值、Latent list/map 校验、位置/命名绑定、收集器与函数包装运行时；调用端支持 `NamedArg`、`StarArg`、`StarStarArg`，包括映射展开后继续给出命名实参。固定 v0.13.5 Stage0 与 Stage1 对同一函数/调用 fixture 的 stdout、stderr、退出状态逐项相同，覆盖默认值依赖较早形参、函数值间接调用，以及动态调用缺少必需实参时两端均失败并报告相同的绑定错误内容。
- `PyImport`、`JavaImport`、`Super` 目前返回明确 codegen error，且不写输出文件。固定 Stage0 接受的 py/java 互操作表达式因此仍属已知不支持；`super` 的类/继承运行时亦未实现。本步不将这些表达式发射成裸 Python 名称或假定互操作语义。
- Bootstrap 尚无 M6 静态语义层：参数绑定运行时可检查缺失、重复/未知命名及容量错误，但某些 Stage0 编译期拒绝可能仍在 Stage1 编译成功、执行时才失败；内建函数和 interop 的静态参数/值语义也尚不对齐。该差距必须由后续语义分析与运行时实现消除，不能视为 v0.13.5 兼容。
- 新增/扩展 smoke 使用固定 Stage0 核对支持用例输出，并核实 py/java/super 不支持路径 fail closed；测试文件沿用既有账本路径，账本说明已同步。完整 Stage0 与 Bootstrap 聚焦回归结果见本步最终验证。
- 本步最终验证：`python3 -m compileall -q bootstrap tests` 通过；Bootstrap 聚焦套件 **14 passed**，ledger 单测 **3 passed**，`tests/bootstrap/checker.py` 报告 **243 included / 0 excluded / 291 inventoried paths**；完整 Stage0 `python3 tests/run_tests.py` 为 **259 passed, 0 failed**。Stage0 环境为 Python 3.12.3 / OpenJDK 25.0.4.1；未重跑 CI 规划的 Python 3.11/JDK 17、21 矩阵。以上 Stage0 结果不代表自举投影通过，也不代表 M4/M6/M8 或 P15 DoD 完成。

### 本轮记录：M4 语句语法族、AST 逻辑身份与仓库语料对拍（M4 仍未完成）

- `bootstrap/parser.lt` 新增 Latent 实现的 `import "…" as alias`、`global`/`nonlocal` 名称列表、含本地或别名限定父类的 `class` 方法体、`try/catch` 与 `throw`；按 Stage0 的 class-body 规则拒绝非方法成员及空类。`FnDef` 与 `ClassDef` 补齐 `source_name`；`ClassRef` 保留 `name`、`alias`、`display` 和其父类标识符位置。
- `bootstrap/ast.lt` 现为每个映射节点显式保留 `source_id` 字段。`parser.parse(source, source_id)` 和 `parse_tokens(tokens, source_id)` 接受调用方提供的逻辑 ID，并递归写入所有 AST 子节点；省略时保持 `nil`，绝不从物理路径或 cwd 猜测身份。新增 fixture 核验 Program、形参、父类、表达式、语句节点的 ID，以及导入/声明/类/父类/try/throw 的字段与行列位置。
- 固定 Stage0 差分新增至 28 个目标语法/错误案例；另增加仓库语料门禁，对 `bootstrap/`、`tests/`、`cookbook/` 的全部 **286 个 `.lt` 源文件**逐一比较 Stage0 与 Latent parser 的接受/拒绝结果，并对每个拒绝比较精确行、列和诊断消息；有效输入同时确认所传相对路径逻辑 ID 保留。该语料门禁首次发现并修正了一元加号的接受面差异（Latent 不支持一元 `+`）。
- 本轮验证：`python3 -m compileall -q bootstrap tests` 与 `git diff --check` 通过；`tests/bootstrap/checker.py` 报告 **243 included / 0 excluded / 291 inventoried paths**；ledger 单测 **3 passed**；bootstrap host/smoke/lexer/parser 聚焦套件 **15 passed**，其中 286 源语料 parser 对拍通过。当前运行的 `python3 tests/run_tests.py` 是在本轮源码编辑前启动的 Stage0 回归；在记录此项时仍未收到完成状态，因此不将其计作本轮 post-change 全量回归证据。
- **里程碑状态：M4 不标记完成。** Parser 语法接受面已覆盖当前仓库语料并补齐此前缺失的 statement families，但 M4 全部 AST 形状/位置的独立覆盖与完整阶段切片门仍未全部验收；M5 脱糖、M6 静态语义、M7 本地模块图/稳定符号身份、M8 完整 Python emitter/runtime、M9 CLI/错误与安全写出均未完成。当前 emitter 对尚无运行时/语义支持的类、导入、异常和 interop 节点仍应 fail closed；Stage1→Stage2→Stage3、fixed point、双根复现、完整 Python-target 对拍和最终 P15 DoD 均未通过或未执行。

- **Stage0 全量结果补记：** 本轮启动的 `python3 tests/run_tests.py` 最终以 exit 0 完成，**259 passed / 0 failed**（含 505 个固定种子性质案例）。该测试运行期间仓库仅修改 P15 `bootstrap/` 实现及 bootstrap 专用 fixture/test/计划记录；Stage0 Python 编译器实现和标准回归输入未改动。此结果证明原有 Stage0 基线通过，不替代以上 15 项 bootstrap 专项验证，也不证明任何完整 stage 自举门禁通过。


### 本轮记录：M5 Latent 脱糖器切片（M5 仍未完成）

- 新增 `bootstrap/desugar.lt`，由 Latent 对 parser 当前产生的节点做显式递归转换；结果统一为结构化 `{ok, value, error, line, col, source_id}`，未知语句/表达式节点和不支持的赋值目标均 fail closed。复制和合成节点继承逻辑 `source_id` 与锚定行列；参数 `param_specs` 及 node-valued 的兼容 `params` 默认表达式均递归变换，简单必需形参仍保留字符串形状。
- 已实现并由 Latent 执行：字符串 `$name` / `${expression}` 插值、`$$` 在包含插值的字符串中的字面转义；`say → __say`；点读取 `Dot → __wgetattr`、直接点调用 `Call(Dot, ...) → __wcall`（含 `direct_method` 提示）；下标读取/写入 `→ __index` / `__wsetindex`、属性写入 `→ __wsetattr`；`super.method(...) → SuperCall` 且滥用的独立 `super` 显式报错；仅函数体末表达式隐式返回，并递归改写末尾带 `else` 的 `if` 两支。循环、容器、运算、函数形参与调用实参、导入、类/父类、异常、global/nonlocal、显式 return、break/continue 和 py/java 前缀均有 traversal 覆盖。
- `bootstrap/parser.lt` 增加 `parse_expression_source`，用于在 Latent 中解析插值表达式、递归标注 `source_id`，并对词法/语法错误和尾随 token 返回结构化结果；M5 对 malformed/unclosed interpolation、trailing tokens、独立 super、未知节点及错误行列/source ID 有断言。
- 固定 Stage0 对同一插值/点访问样例的 6 个规范化 AST 签名与 Latent 输出逐项相同，覆盖插值 `say`、dot 读取、dot 调用、下标尾表达式及 `if/else` 两个隐式返回值。仓库现有 bootstrap parser 测试仍逐项比较 286 个 `.lt` 文件的 parser 接受面和拒绝诊断。**这些是结构/AST 对拍，不是脱糖前后生成程序的运行时行为对拍。**
- **明确未完成：** `bootstrap/compiler.lt` 尚未把脱糖结果接到 `bootstrap/gen_py.lt`；当前 emitter/runtime 不提供这些规范化 helper 的完整语义，因此没有把它接入编译链或声称其生成程序行为一致。`$$` 在没有任何插值表达式的字符串上遵循 Stage0 当前实现的原样字符串快捷路径；M5 的运行时行为、错误诊断全量对拍、标准 IR 消费端与全量语法族验收仍未完成。M5 保持进行中；M4 完整验收、M6–M15 及最终 P15 DoD 仍未完成。

- **M5 聚焦验证：** `python3 -m compileall -q bootstrap tests` 与 `git diff --check` 通过；bootstrap host **6 passed**、smoke **3 passed**、lexer **3 passed**、parser **3 passed**（含 286 个 `.lt` parser corpus 与 6 条 Stage0 M5 AST 签名差分）；账本 checker 报告 **243 included / 0 excluded / 291 inventoried paths**，ledger 单测 **3 passed**。这些检查只确认 M5 脱糖树和 parser/ledger 覆盖，不替代尚待完成的运行期行为对拍或 full bootstrap gates。

- **Stage0 post-change 全量结果：** `python3 tests/run_tests.py` 在本轮代码修改后以 exit 0 完成，**259 passed / 0 failed**，包括 **505** 个固定种子性质案例；本机 Python **3.12.3** / OpenJDK **25.0.4.1**。Bootstrap 聚焦结果见上项。该全量结果验证 Stage0 回归未受影响，不表示 Stage1/2/3、运行期脱糖一致性或完整 Python-target 自举门禁通过；本轮未重跑规划中的 Python 3.11/JDK 17、21 CI 矩阵。


### 本轮补充：M5 接线、M6 静态语义增量与 M9 CLI（仍未完成 P15）

> 本节更新并覆盖前文“脱糖尚未接入编译器”“尚无 M6 静态语义层”及“CLI/`--run` 尚未集成”等历史状态描述。它只记录已执行的切片，不改变批准范围，也不将阶段性通过等同于 M5/M6/M8/M9 完成。

- `bootstrap/compiler.lt` 当前执行 **parse → Latent desugar → Latent semant → Latent Python emitter**；语义失败或 emitter fail-closed 均发生在创建输出目录/写 `.py` 之前。parser API 获得调用者提供的 source ID，但当前 CLI 暂以传入的源文件路径（物理路径）作为该 ID；M7 要求的稳定逻辑模块 ID、跨根 source map 与依赖图尚未实现。
- M5 的 Latent 脱糖器已接入真正编译流程。Stage0/Stage1 运行期对拍覆盖 `$name`/`${expr}` 插值、数值 stringify、函数末表达式隐式返回、列表负索引读取、列表索引写入与输出；Stage0 M5 AST 签名断言继续覆盖 say、dot、call、index、if 两支隐式返回。属性读/调/写目前没有 Python 目标运行时，因此 emitter 明确拒绝，不会发出含未定义 helper 的 Python 程序；py/java import 与 super 也仍 fail closed。故此接线不构成 M5 全量语义验收或 M8 完成。
- 新增 `bootstrap/semant.lt`，所有规则在 Latent 中实现，不把编译期语义判断移入 host adapter。当前切片覆盖顶层/函数名字解析、直线局部读前赋值、嵌套函数作用域的一部分、循环/try 名称绑定、global/nonlocal 的基础绑定检查、默认参数前向引用、已知函数的直接实参签名绑定，以及内建函数的命名/展开/元数约束。固定 Stage0 对照共 10 个编译期负例：undefined、read-before、函数缺参、重复命名、位置与命名重复赋值、未知命名、内建命名参数/展开/元数、默认参数前向引用；每例比较非零退出、诊断首行（含阶段/行列/消息）并确认失败不创建输出目录。
- **M6 未完成项：** 目前不完整覆盖全部 v0.13.5 scope/closure 控制流交集、重复定义/完整 global 与 nonlocal 约束、所有 built-in、调用展开的完整静态推理、间接函数值、类/继承与 override 兼容、`super`、Python/Java interop 的静态检查及完整诊断类别/source-map；这些仍须逐项补入和对拍，不能以 10 个负例代表 M6 DoD。
- CLI 已实现 `-t/--target py`、`-o/--outdir`、`--help`、明确拒绝 `-t java` 和未知 target、拒绝暂缓的诊断 CLI 模式、`--run` 的 stdout/stderr/退出码转发，并修复多点号源文件 stem 只剥离最后扩展名。编译/语义失败不创建输出目录；host 的原子 UTF-8 写入 API 仅负责 OS 写文件。`--run` 成功、程序返回非零、help/target 拒绝和多点号 stem 有 CLI 测试。CLI 仍缺完整 parser/argparse 兼容、隔离 Stage 产物 manifest 与所有失败路径门禁，故 M9 仍未完成。
- M4 方面，固定 Stage0 parser oracle 当前逐一检查仓库 `bootstrap/`、`tests/`、`cookbook/` 中 286 个 `.lt` 输入的接受/拒绝及拒绝位置/消息；额外语法边界包括 import/declarations/classes/try/throw/参数与调用形态。它证明当前语法接受面语料一致，但并不证明每种节点的完整 AST schema/子节点位置与 Stage0 一致；AST 形状/位置独立覆盖及逻辑身份分离仍是 M4/M7 待办。
- 本轮验证：最新 smoke `python3 -m unittest discover -s tests -p 'test_bootstrap_smoke.py' -v` **3 passed**（内含 10 个 Stage0 语义诊断对拍与 CLI/fail-closed 场景）；全套 bootstrap 聚焦测试在最近一次完整运行中 **15 passed**，此后仅追加了 fail-closed emitter 分支，且最新 smoke 再次通过；ledger 测试 **3 passed**、checker 为 **243 included / 0 excluded / 291 inventoried paths**；`python3 -m compileall -q bootstrap tests` 与 `git diff --check` 通过。`python3 tests/run_tests.py` exit 0：**259 passed / 0 failed**（其中 505 个固定种子性质案例），用于确认未改动 Stage0 全量双后端基线。
- **仍未通过或未执行的硬门：** M4 完整 AST 验收；M5/M6/M8/M9 完整验收；M7 本地模块图与稳定逻辑 ID；M10 Stage0→Stage1 完整 Python-target 投影；M11 Stage1→Stage2；M12 Stage2→Stage3 fixed point；M13 双根复现；M14 完整 Python-target 投影逐案零差异；M15 新 CI 自举门禁。当前没有 Stage1→2→3 产物或双根对拍证据，故不能宣布“全部自举准备工作已完成”或 P15 DoD。


### 本轮新增：M7 显式本地模块图与稳定逻辑身份切片（M7 仍未完成）

- 新增 `bootstrap/module_graph.lt`，由 Latent 执行 DFS 加载、导入规则检查、依赖顺序和诊断构造；`latent_host.py` 未新增源代码扫描或解释能力，沿用已有显式 realpath/dirname/join/relpath/norm/UTF-8 read 原语。导入路径相对当前 importer 解析，入口实际父目录为本次 source root；realpath 目标必须留在该 root 内（包含符号链接逃逸检查），只接受显式 `.lt`、拒绝绝对/反斜线/NUL 路径。
- 每个 AST 从调用点获得根相对 POSIX logical `source_id`，如 `main.lt`、`sub/lib.lt`；物理 realpath 仅保存在图记录供 I/O 和真实文件去重，不传给 parser 当源身份。依赖以源码 import 顺序 DFS postorder 产生稳定序列；canonical realpath 去重；正在访问状态用于拒绝环；重复别名、导入不在模块 prologue、嵌套导入、扩展名和 root escape 都由 Latent 返回带 source_id/行列的结构错误。
- **上一轮 M7 基线（现已部分由下节更新）**：`bootstrap/compiler.lt` 已先调用 `module_graph.load`，保留单文件路径；当时含 import 的图仍以 `multi-module Python emission is not yet supported` fail closed。Stage0 图加载差分覆盖依赖顺序、路径安全/去重、诊断位置及循环；Stage1 集成覆盖缺失深层依赖，并确认当时有效图在 codegen 缺口处拒绝。
- **本轮后仍未完成的 M7/M8**：多模块 class/继承与完整 interop、完整跨模块语义和所有错误边界、try/catch 与 global/nonlocal/nested function 的一致支持，及全量 Python-target 兼容都尚未证明。固定 Stage0 生成的 Stage1 compiler 文件仍含 Stage0 绝对 source-map metadata；本轮只验证 Stage1 生成的用户多模块产物不泄漏物理项目根，M13 对 Stage2/3 双根零泄漏的硬门仍未执行。
- 本步最终验证：`python3 -m compileall -q bootstrap tests`、`git diff --check` 通过；最终 bootstrap 专项 **17 passed**，账本 checker 单测 **3 passed**，账本报告 **243 included / 0 excluded / 291 inventoried paths**；全量 Stage0 `python3 tests/run_tests.py` **259 passed / 0 failed**，含 **505** 个固定种子性质案例。未重跑 Python 3.11/JDK 17、21 CI 矩阵。未通过/未执行的仍包括完整 M4–M9、M10–M15、stage fixed-point、双根复现及完整 Python-target 投影；不得据本切片宣称 M7/P15 完成。


### 本轮：Latent 多模块链接与 Python 初始化器首个切片（M7/M8 仍未完成）

- `bootstrap/module_graph.lt` 新增 `prepare(graph)`：全部模块绑定收集、符号分配、别名解析、私有成员拒绝与 AST 名称改写均由 Latent 完成。符号按图的确定性 DFS postorder 编号，并用 Latent lexer 收集所有用户标识符以避免生成名冲突；逻辑 `source_id` 保持根相对，不参与物理路径拼接。没有扩充 `latent_host.py`。
- `bootstrap/compiler.lt` 将图链接结果接入既有 Latent desugar→semant→emitter 流程。`bootstrap/gen_py.lt` 新增 `generate_modules`：按模块产生缓存状态与 init 函数，依赖先初始化、模块顶层语句只在其 init 中运行，所有模块函数/全局变量改名后放在一个 Python compilation unit。公开函数/变量经 `alias.member` 读取或调用；别名成员只读。
- 固定 Stage0 差分 fixture 覆盖三模块依赖顺序、同一 realpath 的重复导入与别名、跨模块同名函数、本地变量遮蔽模块全局、顶层模块全局、生成名与用户名冲突、private 拒绝、深层缺失依赖、初始化顺序、输出确定性与不泄漏项目根。成功程序 stdout/退出状态与固定 Stage0 一致。
- 明确保守拒绝：当前多模块 emitter 尚不支持 class/继承、嵌套函数、try/catch、global/nonlocal 和未实现的 interop/runtime；这些不应被视为模块兼容已完成。测试确认 try/catch 是结构化编译拒绝，不再传出空 AST 节点。
- 实际 Stage0→Stage1 构建成功；尝试 Stage1 编译自身完整 `bootstrap/compiler.lt` 到 Stage2 时，当前首个明确阻塞为 `parser.lt:652:5: module error: try/catch statements are not yet supported by bootstrap module linking`。即使补齐该处，bootstrap 源码中的 `py "latent_host"` 仍需要 emitter 的 Python host interop 支持；因此本轮没有 Stage2/Stage3、fixed-point 或双根复现证据。
- 最终验证（本机 Python 3.12 / JDK 25）：`python3 -m compileall -q bootstrap tests`、`git diff --check` 通过；bootstrap 专项 **17 passed**；ledger checker **243 included / 0 excluded / 291 inventoried paths**，ledger 单测 **3 passed**；固定 Stage0 全量双后端回归 **259 passed / 0 failed**（含 505 个固定种子性质案例）。未重跑 CI 规划的 Python 3.11/JDK 17、21 矩阵。
- 本轮只完成可用的早期多模块切片，未完成 M7/M8，也未通过 M10–M15、Stage1→2→3 或 P15 最终 DoD；下一步的直接阻塞是模块内 `try/catch` 脱糖/语义/emitter 一致性及 `py "latent_host"` 的 Latent emitter/runtime 支持，之后还需持续做 Stage0 对拍、Stage2/3 fixed-point 与双根复现。


### 本轮增量：异常与 Python host interop 接线、真实自举固定点/双根门（P15 仍未完成）

> 本节更新上一节记录的直接阻塞。该增量证明当前 self-host compiler 源码可以经 Stage0→Stage1→Stage2→Stage3 编译并达到受限产物固定点；不代表 v0.13.5 Python-target 全语义或 P15 DoD 完成。

- `bootstrap/module_graph.lt` 现在由 Latent 递归验证、改写 try/catch 的 body 与 handler；catch 名按当前模块/函数绑定改写到对应的稳定符号。`bootstrap/gen_py.lt` 在 Latent emitter/runtime 中实现 `try/catch`、`throw`、惰性 Python 模块对象、属性读取/方法调用，以及 self-host compiler 所用的 `keys`/`push` 支持。`bootstrap/latent_host.py` 未增加编译器逻辑或扩展 OS API；`py "latent_host"` 仅通过原有 Python import 能力访问既有文件/路径/stdio/子进程服务。
- 固定 Stage0 对拍新增 Python `py` module repr/属性/函数调用、host 路径调用、throw/catch 字符串和成功 try 后 nil catch 变量；已有多模块案例再对拍模块初始化、函数内 catch、模块顶层 catch 符号链接。Java interop、属性赋值和 super 继续明确 fail-closed。
- 新固定点用例以固定 Stage0 编译完整 `bootstrap/compiler.lt` 为 Stage1；两个不同绝对源码根各自从独立 Stage0 archive 开始，随后删除该 Stage0 目录，并以空 `PYTHONPATH` 运行 Stage1→Stage2→Stage3。第二根复制的是完整 `bootstrap/` 源树（排除 `__pycache__`），不是完整仓库 checkout。每阶段只声明 `compiler.py` 和固定 `latent_host.py`；测试检查精确目录文件集合、相对路径排序的 SHA-256 manifest，且 Stage2/3 与两根之间逐文件比较原始 bytes。
- `.github/workflows/tests.yml` 的 Python 3.11/JDK 17 bootstrap job 已改为显式运行 `python -m unittest tests.test_bootstrap_smoke -v`，其中包含 Stage0/1 行为差分、Stage1→2→3 fixed point 与双根 manifest/字节复现门；原 Stage0 lexer/parser、host 与 projection ledger 检查保留。
- **仍未完成的范围/门：** 这些行为对拍是有界自举切片，不是 Python-target ledger 的 243 个 runner case 全量 Stage0/Stage1 对拍。Stage1 仍未实现/验证完整 class/inheritance/super、完整 Python/Java interop 及互操作值/回调、属性写入、完整静态语义/诊断/source map、全量 CLI 与负运行期用例；Stage2/3 清单当前仅含单一 compiler entry 和固定 adapter，CI 未产出受限诊断 artifacts。本轮本机也未运行 Python 3.11/JDK 17 CI 环境。故 M7/M8、M10/M14/M15 的完整验收与最终 P15 DoD 仍未通过，不能宣称完整自托管兼容已完成。
- 本轮最后一次代码/测试变更后的验证（本机 Python 3.12 / JDK 25）：`python -m compileall -q bootstrap tests` 与 `git diff --check` 通过；projection ledger 为 **243 included / 0 excluded / 291 inventoried paths**，ledger 单测 **3 passed**，host 契约 **6 passed**；lexer、parser 与 smoke 合计 **13 passed**，含 **286 个仓库 `.lt` 输入**的 Stage0 parser 接受/拒绝对拍。固定点用例通过：Stage0 删除、空 `PYTHONPATH` 下 Stage1→2→3 成功，Stage2==Stage3 manifest 及原始 bytes 相等，另一个独立 Stage0 在另一绝对 bootstrap 源根上生成完全相同的 Stage2/3 bytes。最后一次完整 Stage0 `python tests/run_tests.py` **259 passed / 0 failed**，含 **505** 个固定种子性质案例。未在本机运行 Python 3.11/JDK 17 CI 矩阵；P15 全语义/全投影的未完成项仍如上列明。


### 本轮增量：单文件、无继承类基础语义（M8 仍未完成，P15 仍未完成）

> 本节更新前文“class/inheritance/super 尚未实现”的历史状态：Latent Python emitter/runtime 现具备有界单文件类切片。继承、`super`、多模块 class 与完整 P15 语义均未实现或未验收；不改变 v0.13.5 Python-target 完整范围，也不声称所有自举准备已完成。

- `bootstrap/gen_py.lt` 使用 Latent 逻辑发射无父类的 class 工厂、方法签名包装和实例 runtime。覆盖 `.new()` / 可选 `init` 与默认参数、动态实例字段读写、字段/方法读取、直接方法调用、bound method、类/实例/方法 repr、实例身份比较；工厂名确定性避让所有已收集的模块绑定。
- `bootstrap/semant.lt` 在 Latent 中新增类/函数重复定义检查、类内重复方法检查、`new` 保留规则、方法首参数必须为必需位置 receiver 的检查；对可静态识别的直接 `Class.new(...)` 依据 `init` 的形参列表（排除 receiver）绑定，覆盖 no-init 额外实参编译期拒绝。Python/Java host 属性写入与完整互操作不在此支持范围。
- 固定 Stage0 对拍覆盖自定义单文件类程序和仓库现有 `tests/class_basic.lt`。执行比较包含 initializer/default、独立实例、字段变更、方法间调用与模块全局读取、bound method 表示、类/实例 repr 与相等；runtime negatives 覆盖缺字段、缺方法和 bound-method 错误元数；compile negatives 覆盖重复方法、非法/默认 receiver、保留 `new`、no-init constructor arity。含用户函数名与生成工厂名冲突。继承和 `super` 输入在发射前 fail-closed；多模块类仍由 module linker 拒绝。
- `tests/bootstrap/README.md`、`manifest.json` 已更新，区分该单文件类切片和仍明确拒绝/缺失的能力。
- 最终验证：Python 工具/测试 `compileall` 与 `git diff --check` 通过；projection checker 报告 **243 included / 0 excluded / 291 inventoried paths**，ledger 单测 **3 passed**；host/lexer/parser 聚焦用例 **13 passed**；host object 字段写入收紧为明确 unsupported 后，完整 bootstrap smoke **8 passed**，覆盖仓库 `class_basic.lt`、host-write 负例及 Stage1→Stage2→Stage3 fixed-point/双根 bytes 门禁；固定 Stage0 全量双后端 **259 passed / 0 failed**（含 **505** 固定种子性质案例，本机 Python 3.12.3 / OpenJDK 25.0.4.1）。未在本机重跑 Python 3.11/JDK 17、21 CI 矩阵。
- **后续硬缺口：** 尚未做到 ledger 中 243 个 Python-target runner case 的 Stage0/Stage1 逐案行为与诊断零差异验收；继承/override/`super`、跨模块 class、完整 host 属性写入与 Python/Java interop/回调、完整 scope/class semantics、全部负编译/运行期诊断和 source map、全 CLI 兼容及 CI M15 尚未完成。M4/M5/M6/M7/M8/M9/M10/M11/M12/M13/M14/M15 的完整 DoD 仍未全部通过。必须继续逐项扩展并对拍；本轮仅完成 class 基础切片，不得宣称所有自举准备工作或 P15 已完成。


### 本轮增量：单文件继承、override 兼容与 `super`（M8/M6 仍未完成）

> 本节更新上一节“继承/override/`super` 尚未实现”的状态。此为固定 Stage0 下的单文件继承切片，不等于完整 M6/M8、243-case 投影或 P15 DoD。

- `bootstrap/semant.lt` 现由 Latent 校验父类必须是同文件类、拒绝未知/非类父类、检查继承环，并沿继承链查找 `init` 与方法。非 `init` override 按 P12 绑定规则检查：保留位置形参次序/名称与关键字槽位、可选性、rest/extra 能力以及不新增必需参数。方法体的 `super.method(self, ...)` 检查实例方法上下文、当前 receiver、父链目标方法和调用实参签名；直接 `Class.new(...)` 可沿父链找到 initializer。
- `bootstrap/gen_py.lt` 现以确定的父先子后顺序生成同文件类；实例方法读取/调用沿父链分派，构造器继承父 `init`，`super` 从当前方法所属类的直接父类开始查找。bound-method wrapper 在参数绑定后调用其底层函数，避免继承的 keyword-only/default 形参二次绑定；Latent `__wcall` 的命名实参直接交给 Latent runtime，且映射双下划线 helper 名避免 Python 类体名称改写。多模块类、限定父类 alias 仍 fail-closed。
- `tests/test_bootstrap_smoke.py` 增加固定 Stage0/Stage1 的 `inheritance.lt` 与 `p12_override_compatibility.lt` 输出/退出状态差分；另对比 **21** 个继承/override/构造器/`super` 编译负例的退出状态、诊断首行和失败不落盘。原有“无父类 `super` 的 emitter 失败”断言已更新为准确的 M6 语义错误。`tests/bootstrap/README.md` 更新支持范围与未实现边界；未增加 ledger 路径或 Stage0 测试用例。
- 验证：`python -m unittest tests.test_bootstrap_host tests.test_bootstrap_smoke tests.test_bootstrap_lexer tests.test_bootstrap_parser -v` **22 passed**（含 Stage1→2→3 fixed point 与双根 bytes 复现）；固定 Stage0 `python3 tests/run_tests.py` **259 passed / 0 failed**（505 个固定种子性质案例）；`compileall`、`git diff --check`、ledger checker 与 ledger 单测 **3 passed**，账本为 **243 included / 0 excluded / 291 inventoried paths**。本机为 Python 3.12 / OpenJDK 25；未因此宣称 CI 的 Python 3.11/JDK 17 门通过。
- **下一阻塞与未完成项：** 首要门槛仍是把 ledger 的 **243 个适用 runner case** 做成逐项 Stage0/Stage1 成功行为、编译诊断、运行错误、stdout/stderr/退出码差分；该全量投影尚未实现。随后还需扩展多模块 class/inheritance、可静态推导类型的直接方法实参检查、完整 Python/Java interop 与回调、诊断/source map/CLI 完整兼容，并通过 M10–M15 的所有硬门。M4/M5/M6/M7/M8/M9 及完整 P15 DoD 仍未完成，不得宣称“所有自举准备工作已完成”。


### 本轮增量：单文件函数作用域、闭包与 Python runtime 兼容切片（M6/M8 仍未完成）

- `bootstrap/gen_py.lt` 现为每个函数收集本地赋值、循环变量、try/catch 名与嵌套定义名；将 `global`/`nonlocal` 声明提升至生成函数开头，并为未赋值的局部名初始化 `nil`，保持 Latent 对条件分支未赋值读取的行为，同时支持 Python 闭包 cell。作用域收集不进入内层函数体，避免污染外层绑定。
- 同一有界 runtime 切片将 `range(...)` 映射为 Latent 列表，对宿主 tuple 递归采用 Latent list 表示，并将动态调用非函数的运行错误映射为 Latent 的 `call on non-function value`。这些不是完整 builtin/interop 适配。
- `tests/test_bootstrap_smoke.py` 新增固定 Stage0/Stage1 差分，覆盖 **20 个单文件/ cookbook 程序**：`scope`、P5 closure/global、P6 nonlocal、P8–P11 参数/闭包、range 循环、动态非函数错误，以及 `py_math`、`py_os` 和 cookbook functions。stdout、stderr 与退出码逐项相等。
- 本轮最终完整 bootstrap 门禁 `python3 -m unittest tests.test_bootstrap_host tests.test_bootstrap_smoke tests.test_bootstrap_lexer tests.test_bootstrap_parser -v` **23 passed / 0 failed**，包含 Stage1→Stage2→Stage3 fixed point 和双根原始字节一致；作用域/runtime 差分专项 **1 passed**（内含 20 个程序）；`git diff --check` 通过。Stage0 源码未修改，本轮没有重跑 `tests/run_tests.py`；此前 Stage0 259/259 仍仅是此前一次基线，不作为本轮后的新运行结果。
- **下一阻塞与未完成项：** 243-case Stage0/Stage1 完整 Python-target 投影仍未实现。71 个成功用例的早期普查在本切片前只有 28 项完全匹配，结果已因本轮作用域/runtime 修复而过期，不用于宣称当前通过率。多模块入口当前把 entry 文件父目录固定作 source root，导致需从 entry 向上导入同一模块树内文件的有效图误报 root escape；下一步应提供明确、可差分的 source-root 选择，同时保留 realpath/symlink escape 拒绝，再扩大多模块 class/nested function 与 interop 覆盖。Java interop、全量诊断/source map、M10–M15 和完整 P15 DoD 仍未完成，不能声称所有自举准备工作已完成。


### 本轮增量：显式模块 source-root 选择与越界保护（M7 仍未完成）

- `bootstrap/module_graph.lt` 的 `load` 增加可选 `requested_root`：未指定时保留 entry 的 realpath 父目录作为既有默认；指定时先解析为绝对 realpath 并要求其为现存目录，再要求 entry realpath 位于该根内。所有 import 仍相对 importer 文件解析、realpath 去重，任一文件或 symlink 目标越过所选根继续 fail-closed。`bootstrap/compiler.lt` 新增 `--source-root ROOT`，相对 root 以调用 CLI 的 working directory 解析；缺参有明确参数错误。
- 固定 Stage0 module-graph differential 在两个不同 checkout 根下验证跨目录向上导入的 root-relative ID/DFS 顺序一致，并覆盖不存在 root、entry 在 root 外、普通 import 越界和既有 symlink 越界拒绝。Stage0/Stage1 compiler integration 用两个绝对目录不同的项目对拍成功程序，测试相对和绝对 source-root 参数、stdout/stderr/退出码与生成字节稳定，确认输出不泄漏绝对 checkout root。
- 本轮全量门禁：`python3 -m unittest tests.test_bootstrap_host tests.test_bootstrap_smoke tests.test_bootstrap_lexer tests.test_bootstrap_parser -v` **23 passed / 0 failed**（含 Stage1→Stage2→Stage3 fixed point 与双根 bytes）；固定 Stage0 `python3 tests/run_tests.py` **259 passed / 0 failed**（505 个固定种子性质案例）；`compileall`、`git diff --check` 通过；ledger **243 included / 0 excluded / 291 inventoried paths**，ledger 单测 **3 passed**。未重跑 CI 的 Python 3.11/JDK 17 矩阵。
- **P15 尚未完成：** 显式 source-root 只修复模块图边界，不增加多模块 class/nested function/Java interop 能力；243-case Stage0/Stage1 全量行为与诊断投影、完整 source map/CLI 及 M10–M15 硬门仍未完成。随后应把成功/负例 case 投影自动化并按差异推进多模块 class/nested function/interop，不得宣称所有自举准备工作已完成。


### 本轮增量：多模块 class/closure 链接与宿主 equality parity（M6/M7/M8 仍未完成）

> 本节更新此前“linked graph 不支持 class、nested function 和 global/nonlocal”的历史状态；旧段落保留作为时间线记录。当前切片仍不是完整 Python-target 投影或 P15 DoD。

- `bootstrap/module_graph.lt` 现在将顶层 class 纳入各模块的 collision-checked 私有符号表和 initializer globals；重写跨模块 class-parent alias 与方法体引用；父模块初始化先于子类发射。模块图还支持嵌套函数词法捕获、`global` 名称映射到模块私有变量、`nonlocal` 保持词法绑定，以及在符号链接前预解析字符串插值中的自由模块名。嵌套 class 继续明确拒绝。
- `bootstrap/gen_py.lt` 的多模块 emitter 按继承拓扑在所属模块 initializer 中生成 class factory；运行时 `==`/`!=` 与固定 Stage0 对齐：浮点 NaN 不相等、Latent list/map 递归比较、外部宿主对象（含 NumPy ndarray）按 identity 比较。
- `tests/test_bootstrap_smoke.py` 增加八个多模块 Stage0/Stage1 fixture（class/inheritance/bound method/default/keyword-only/functions/closures/nonlocal）、cookbook module interpolation 回归，并将 `py_numpy` 加入单文件 scope/runtime differential。
- 当前验证：bootstrap host/smoke/lexer/parser 共 **25 tests passed**，含 Stage1→Stage2→Stage3 fixed-point 与双根逐字节门；Stage0 `tests/run_tests.py` **259 passed / 0 failed**（505 个固定种子性质案例）；`compileall`、`git diff --check`、ledger checker 与单测通过，ledger 为 **243 included / 0 excluded / 291 inventoried paths**（ledger 单测 3 passed）。固定 Stage0 seed 下 71 个 success/cookbook 正例最新扫描为 **54 matched / 17 mismatched**；17 个差异全部是 Java interop case 的 Stage1 codegen 拒绝，没有其余正例差异。
- **下一阻塞与未完成项：** 17 个 Java interop 正例仍需在 Latent emitter/runtime 中实现 Java JSON-RPC wrappers，并通过 fixed Java runtime source packaging 让 Stage1 输出可运行；host adapter 已有显式文件复制 primitive，Stage0 构建的 Stage1 目录含三份 daemon/runtime `.java` source。后续还须完成 243-case Stage0/Stage1 编译诊断/运行错误/输出投影、完整 interop 回调、全量 source map/CLI 和 M10–M15 门禁。P15 仍未完成，不得声称“所有自举准备工作已完成”。


### 本轮增量：Java/Python runtime 与数值语义 parity（M8 仍部分完成，P15 未完成）

> 本节更新此前“Python/Java interop 尚未实现”的历史状态：Stage1 Python target 现在包含一条经过固定 Stage0 对拍的有界互操作运行时切片；这不等价于完整 Python-target 语义，也不代表全量自举 DoD 已完成。

- `bootstrap/java_runtime.lt` 以 Latent 字符串发射 Java/Python bridge：惰性启动既有 Java daemon、Java class/instance/method handles、构造与方法/字段访问、抽取 bound/static method、Java collections 迭代/索引及 Latent/host 值编解码。`bootstrap/compiler.lt` 继续仅通过既有显式 host-copy API 将固定 `LtJavaDaemon.java`、`JReflect.java`、`LtRt.java` sidecars 随 Python target 输出；没有扩大 OS API 或将编译器逻辑移入 adapter。
- `bootstrap/gen_py.lt` 将 Latent 数值词面统一发射为 float64，并新增与 Stage0 一致的 `range`（截断数值参数并返回 Latent 数字列表）、`len`（返回 Latent float64）与幂运算（负底数的有限非整数指数返回 `nan`）helper。它们修复了 loop、`phase1_semantics` 和 cookbook pipeline 上此前的运行差异。
- `tests/test_bootstrap_smoke.py` 增加一个固定 Stage0 differential 测试，包含 **18 个**重点程序：`loop`、`phase1_semantics`、Java basics/lazy、mixed/try、P13/P14 bound/static fixture、Java strings/collections/time/NIO/BigDecimal、mixed I/O、pipeline 与 interop bound/function values；逐项比较 stdout、stderr、退出码。另按 Stage0 runner 的 javac 流程做 71 个成功 Python-target positive/cookbook case 普查，结果 **71/71 匹配**；该普查不替代正式的全账本 243-case 投影。
- 验证（本机 Python 3.12 / OpenJDK 25）：完整 bootstrap host/smoke/lexer/parser **26 passed / 0 failed**（434.608 秒，包含 Stage1→Stage2→Stage3 fixed point 与双根 bytes 门）；固定 Stage0 `python3 tests/run_tests.py` **259 passed / 0 failed**（含 505 个固定种子性质案例）；`compileall`、`git diff --check` 通过；projection ledger 为 **243 included / 0 excluded / 291 inventoried paths**，checker 单测 **3 passed**。本机结果不替代 CI Python 3.11/JDK 17、21 矩阵。
- **下一阻塞与未完成项：** ledger 的 243 项 Stage0/Stage1 成功/编译拒绝/运行期错误全量差分仍未完成；完整 Python/Java interop（特别是 callback、负例诊断与 source location）、全部诊断/source map、全 CLI 兼容及 M10/M14/M15 CI 硬门仍未通过。必须继续按 ledger 找出并消除下一个差异；不得宣称“所有自举准备”或 P15 已完成。


### 本轮最终验收：控制流类型 join、完整投影与 M15 隔离检查（本地及远端 CI 通过）

> 本节更新并取代第 269 行关于 243 项全量投影仍未实现的历史状态，并记录 M10–M15 与最终 DoD 的验收结果。

- `bootstrap/semant.lt` 现对 `if`/`while`/`for`/`try` 的 `class_types` 做分支前快照、分支恢复和 join 后写入变量失效，匹配 Stage0 控制流类型追踪；修复 `p8_named_arguments.lt` 中 join 点后误保留单一分支类类型的误报。`tests/test_bootstrap_smoke.py` 及全量正例投影覆盖了该回归。
- `bootstrap/module_graph.lt` 将源码文本、物理路径与逻辑 source ID 保留在诊断上下文；`bootstrap/compiler.lt` 生成带源码行与 caret 的编译诊断；`bootstrap/gen_py.lt` 输出语句级源码标记并映射 Latent runtime traceback；`bootstrap/java_runtime.lt` 的数值/内建调用规则继续与固定 Stage0 对齐。投影只将 Stage1 模块初始化器名按其逻辑源文件计算到 Stage0 的精确 SHA-1 后缀，不抹除对应源码路径/行号或其他 stderr 差异。
- `tests/bootstrap/project_stage0.py` 已从固定 Stage0 构建 Stage1/2/3，并对账本所有 **243 included / 0 excluded / 291 inventoried paths** 执行 **244** 个 fixture 调用（**71** 正例、**88** 编译负例、**85** 运行负例）和 **505** 个固定种子性质用例；逐项比较 Stage0/1/2 的状态、stdout/stderr、编译诊断/源码位置及运行错误位置。最新报告为 **244/244 fixture 匹配、505/505 性质匹配、0 runner/property/artifact mismatch**；Stage1/2/3 均无缺少或额外产物，Stage2/Stage3 声明文件 bytes 相等。Stage1 初始 seed emission 仅临时隐藏 `javac`，避免固定 Stage0 的 eager `.class` 缓存进入清单；必需 Java `.java` sidecars 保留，JDK-backed interop fixtures 照常运行。
- `bootstrap/latent_host.py` 显式声明精确 `__all__` API allowlist，host contract 新增对公开函数集合的断言。双根 fixed-point 测试删除 Stage0 seed 后再由 Stage1 编译 Stage2，设置空 `PYTHONPATH`，将各阶段放在独立临时目录，并对构建前后的仓库文件做只读快照比较（排除 VCS、依赖目录及解释器/测试缓存）。
- 本机验证：`python3 tests/run_tests.py` **259 passed / 0 failed**；最新 host/smoke/lexer/parser bootstrap suite **28 passed / 0 failed**（含 Stage1→2→3 fixed point、双绝对根和源码树只读断言）；Stage0/1/2 projector **243 case IDs / 244 fixture invocations / 505 properties 全部零差异**，stage artifact mismatch **0**；ledger checker 与其 **3 项**测试、Python syntax checks、`git diff --check` 均通过。本机为 Python **3.12.3** / OpenJDK **25.0.4.1**。
- **远端最终验收：** GitHub `Tests` workflow run [38285571371](https://github.com/fghijkln/latent/actions/runs/38285571371) 对实现提交 `068ad81d417f779802d39a189f21b82394d9ed76` 返回 **success**；4 个 job（VS Code extension、Python 3.11/JDK 17、P15 bootstrap / Python 3.11/JDK 17、Python 3.11/JDK 21）全部完成并通过。P15 job 的账本分类、host contract、双根 Stage1→3、lexer/parser 和全量 Stage0/1/2 投影步骤均成功；normalized validation reports 已上传，未上传 Stage 编译产物。
- **DoD 结论：** M10–M15 的本地与干净 checkout CI 门均通过；全量 Python-target 投影零差异、Stage2/3 固定点与双根复现逐字节一致、原有双后端回归保持通过、全部排除项具备范围理由。P15 首期可标记 **Done**；不扩大到 native、host-independent 或计划外功能。

- **M14 账本报告补充（最终投影已验证）：** CI JSON 现分别列出 runner 用例 **243/0**（included/excluded）、fixture 路径 **285/6**（共 291 项）、辅助测试路径 **11/2**、P14 协议操作 **2/1**，并逐项记录全部 9 个明确排除项的理由。3 个 Java-only host helper 源文件虽不作为 Latent 输入 fixture 计入，但仍由纳入的 Python-target interop/property 用例编译和使用。报告 schema 扩展后的完整投影再次通过：244/244 fixture、505/505 property，Stage2→Stage3 及 artifact 门均零差异。


### 2026-10-13 追加审计：M13 双完整检出证据补强（本机 clean-clone 门禁通过，远端 CI 待验收）

- **审计发现：** 原固定点测试的第二个根目录仅复制 `bootstrap/`，故虽有 Stage2/3 字节一致结果，却不足以证明 M13 所要求的两个完整 checkout 路径可复现。历史 CI run `38285571371` 和最新的 `38287288939` 均运行了这一旧测试；两次绿色结果都不作为完整 checkout 证据。M13 已重新开放。
- **测试修复：** `tests/test_bootstrap_smoke.py` 现在从同一当前 Git HEAD 创建两个独立的完整本地 clone，核验二者 HEAD 相同、所有 `git ls-files` 路径均存在，并要求两个 clone 的 `git status --porcelain` 为空；不覆盖调用方工作树，确保二者都是记录提交的干净检出。Stage0 archive 每根独立提取；Stage1→2→3 源码来自对应完整根，Stage0 在 Stage1 编译 Stage2 前删除；Stage 输出位于两根之外。构建前后对所有非 `.git` 项（类型、权限模式、`mtime_ns`、文件内容、符号链接目标、目录/特殊节点）做 SHA-256 快照，完整源树从初始 clean copy 之外不允许任何持久写入。产物按递归相对路径生成 manifest，精确拒绝缺失/多余文件、目录或链接，并同时比较每根 Stage2==Stage3 以及两根 Stage2/Stage3 的 manifest 与原始 bytes。固定点子进程设 `PYTHONPATH=''`、`enable_mcp=false`；对应 CI bootstrap job 也在 job 级别显式设置 `enable_mcp: "false"`。
- **本机证据：** 提交 `3c23a2323efe1fd658ad74026e6d14b146a32176` 上的最终 bootstrap host/smoke/lexer/parser suite 通过 **28/28**（456.119 秒）。报告确认 checkout-a/b HEAD 同为该提交；各自 `git status --porcelain` 为空，372 个 tracked paths / **432** 个快照项完整，跨根类型/权限/内容一致，每根构建前后含 `mtime_ns` 的快照一致。两根内 Stage2==Stage3、跨根 Stage2/Stage3 的 manifest 和原始 bytes 均一致、差异为零；Stage2/3 仅有 5 个声明文件，无多余项。
- **后续门禁状态：** 同一提交上的 Stage0 全量双后端基线通过 **259/259**；Stage0→1/2 projector 对 **243** 个账本 case ID / **244** 个 fixture / **505** 个 properties 的既有验证零差异，账本 243 included、0 excluded 且 checker 3 项测试通过（投影器未改、此提交只调整测试/文档/CI）。本机为 Python 3.12.3 / OpenJDK 25.0.4.1；推送后仍需 GitHub CI 验证 Python 3.11/JDK 17/21 矩阵与 clean checkout run。


### 2026-10-13 追加复核：源树快照内容与元数据完整性（clean clone 已通过）

- 最终 diff 复核发现原快照把目录编码成固定字节 `b"<directory>"`，可能与同路径文件内容碰撞，且会跳过 FIFO/socket 等特殊节点；仅 hash 内容也看不出内容不变的 touch 或 checkout 根目录中新建再删除的路径。最终实现把每个条目的类型、权限模式、`mtime_ns` 及文件内容/符号链接目标/特殊节点类型纳入摘要，并包括根目录本身。
- 回归探针仅在临时目录验证文件/目录类型碰撞和内容不变的持久 touch；最终 28/28 suite 已在无 overlay 的 clean clones 上通过。报告的 `both_git_worktrees_clean`、`both_checkouts_content_mode_identical`、`working_tree_write_free` 均为 true；各根 Stage2/3 与跨根 Stage2/3 manifest/bytes 的 file differences 均为空。
