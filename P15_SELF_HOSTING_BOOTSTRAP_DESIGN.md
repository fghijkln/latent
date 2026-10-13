# Latent 自举编译器：目标、边界与验收设计提案

> **当前状态：P15 首期 DoD 已完成。** 范围为固定 v0.13.5 Stage0 → Stage1 → Stage2 → Stage3 的 Python-hosted / Python-target 自举：243 个 case ID、244 次 fixture 调用、505 个固定种子性质用例零差异；Stage2==Stage3，两个完整 clean-clone 根目录中的 Stage2/3 产物逐字节一致；Stage0 全量双后端基线 259/259。该结论不扩展到 native 或 host-independent bootstrap。下方实施记录保留早期过程；其中当时的未完成状态由最新验收记录取代。
>
> **基线：** Latent v0.13.5，仓库当前提交 `9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9`。

## 1. 摘要与推荐方向

**推荐的首个工程目标：Python 宿主上的真正阶段自举，且只要求 Latent 编译器生成 Python 后端。** 也就是先把编译器实现写成现有 Latent 语言程序，由 v0.13.5 的 Python 编译器产生可执行的 stage1；然后 stage1 编译同一份 Latent 编译器源码得到 stage2，stage2 再编译得到 stage3，并要求 stage2 与 stage3 产物可复现。

这比“编译器源码改用 Latent 写”更可验证，但不等于脱离 Python：生成物仍由 CPython 执行，文件系统、路径、命令行和进程仍通过明示的宿主适配层提供。**系统服务可以依赖宿主；词法、语法、AST、脱糖、语义分析、模块改写与 Python 代码生成不能依赖 Python 编译器实现。**

### 三种目标的区别

| 目标 | 含义 | 是否建议作为首期终点 |
|---|---|---|
| **A. Latent 编写、现有宿主编译器执行** | 编译器的核心源码是 `.lt`，但每次都由当前 `latent.py` 编译执行；没有自编译阶段链，也不证明实现能编译自身。 | 适合作为开发中的里程碑，不足以宣称完成了可复现自举。 |
| **B. stage0→stage1→stage2→stage3 自举** | 现有 Python 编译器只作为 stage0 种子；stage1/2/3 均由 Latent 编译器源码编译产生；stage1/2 行为与 stage0 对拍，stage2 与 stage3 产物字节级一致。产物仍跑在 Python 上。 | **建议的第一个可验收目标。** |
| **C. 更独立、可移植的 bootstrap** | 自举链最终不再依赖 Python 编译器/运行时；需要另一种独立种子、可移植 VM/解释器，或能产出并自举原生/跨平台目标的路线，同时明确编译器自身运行时与工具链边界。 | 后续单独决策；不应与 B 混称，也不是首期承诺。 |

## 2. 当前仓库证据与工程含义

- `README.md` 将工程描述为纯 Python 标准库编译器，流程为 `lex.py → parse.py → desugar.py → semant.py → gen_py.py / gen_java.py`。`latent.py:23-42` 的 `compile_src` 明确串起词法、语法、脱糖和语义检查。
- `latent.py:175-308` 负责 CLI、读取源文件、诊断呈现、模块图编译、输出目录、运行生成程序、复制后端运行时文件，以及可选 `javac`/Java 运行。故系统接口目前与编译器策略混在一个 Python 入口中；自举时需要将**编译策略**移入 Latent，把**OS 交互**单独隔离。
- AST 由 `nodes.py` 中的显式节点类表示；节点保留行、列与 `source_path`。`parse.py` 是递归下降解析器，`desugar.py` 处理插值、点调用改写与末表达式隐式返回；`semant.py` 实现静态规则；`gen_py.py` 包含 Python 代码生成与生成程序运行时序言。
- 现有语言能力足以承载编译器主体：函数、递归、闭包、类、列表/映射、循环、分支、可变赋值、异常和显式本地模块均已存在。语义与语法边界记录在 `SPEC.md`；内建函数在 `semant.py:5-8`，包括 `len`、`range`、`str`、`int`、`push`、`keys` 与内部 `__say`。因此不建议为自举先扩充语言或增加标准库。
- 本地模块由 `module_system.py:472-553` 以 `realpath` 加载、按显式相对 `.lt` 导入构造无环依赖图；`module_system.py:562-568` 以规范化绝对路径 SHA-1 前缀生成内部符号。`P2_MODULES_DESIGN.md` 也规定循环导入编译期拒绝、按导入文件目录解析路径、重复路径只编译一次。
- 路径确实会进入产物：`gen_py.py:1129` 将 `source_path` 写入 `_WV_SOURCE_FILE`，模块生成路径会使用模块源路径；源映射保存文件名与行号。这是需要在自举设计中主动处理的复现性问题，而不是仅比较执行输出就能发现的问题。
- `tests/run_tests.py` 当前分别列出 Python/Java 正例、模块、编译错误、运行时错误与其他协议/诊断检查；README 声明 v0.13.5 有 259 项回归。现有 `.github/workflows/tests.yml` 使用 Python 3.11 与 JDK 17/21 矩阵，运行编译器语法检查、编辑器元数据检查和完整回归。
- 以上是设计阶段静态阅读所得的仓库事实；当时**没有执行测试或编译**。实施阶段的测试结果与当前限制记录在 §13。

## 3. 建议的首期范围

### 3.1 编译器核心必须写在 Latent 中

首期实现应在 `.lt` 源码中包含并负责：

1. Token、源码位置、错误数据及编译器自身 AST 节点模型。
2. 与 v0.13.5 语言规范相符的词法分析与递归下降解析。
3. 脱糖（含字符串插值、点访问/调用变换、隐式返回等现有规则）。
4. 静态语义检查与编译期诊断，包括现有作用域、调用绑定、类/继承与模块规则。
5. 显式本地模块图加载策略、符号改写、依赖顺序及跨模块源码位置关联。
6. **Python 后端**代码生成、目标代码字符串转义、源码映射和生成程序的运行时调用约定。
7. 编译流程与 CLI 策略：参数含义、阶段顺序、目标选择错误、诊断内容、编译失败状态及产物清单。

**禁止把任何一项核心编译阶段委托给 Python 文件。** 不可从 stage1 调用 `latent.py`、Python 版 lexer/parser/semantic/codegen，不可通过 `subprocess` 启动 stage0 代为编译，也不可在 Python 适配层实现 AST、语法、名称绑定、语义规则或 Python 代码模板。

### 3.2 首期建议支持面

- 语言：维持 `SPEC.md` v0.13.5 的语法和语义，不引入新语法、AST schema 或运行时语义。
- 后端：只生成 Python（`-t py`）；保留现有 Python 目标所支持的 Latent 代码生成，包括 `py`/`java` 互操作所需的目标代码与随附运行时源码。Java 代码生成器本身不在范围内。
- CLI 建议：源文件、`-t py`（或默认 py）、`-o`、`--run`。错误地请求 `-t java` 应给出明确“不支持该目标”的诊断，不能静默退回 py。
- 本地 `.lt` 模块：首期保留。模块编译器本身可由多个 `.lt` 文件组成，避免为了绕过模块能力而把编译器塞进一个巨型源文件。
- 建议暂缓：REPL、`--show-ast` / `--show-desugar`、编辑器元数据模式、Java 后端生成、打包/安装器、包管理/搜索路径、循环模块、增量编译和宏系统。上述是产品功能的暂缓，不是自举核心的替代实现。
- 不要求首期编译器把自身编译为 Java；不要求 stage1 是单文件二进制，不要求无 Python 解释器运行。

> 上述支持面已获用户批准并落实到实施计划；这不表示当前阶段性代码已完成兼容性验收。

## 4. 宿主运行时契约：允许薄适配，不允许编译器外包

建议增加一个很小、显式版本化的 Python 宿主适配模块（例如随 stage 产物放置的 `latent_host.py`）。Latent 编译器只经由该模块获得以下外部能力：

| 能力 | 建议接口语义 | 约束 |
|---|---|---|
| 参数与进程状态 | 取得 argv；输出 stdout/stderr；以指定状态结束 | 参数解析规则、帮助信息、目标选择和状态决策仍由 Latent 编译器实现。 |
| 路径 | join、dirname、basename、绝对路径/真实路径规范化、相对路径判断、路径分隔符规范化 | 不做搜索路径推断；Latent 负责应用本地模块规则和身份规则。 |
| UTF-8 文件 | 读取、写入、创建目录；查询/拷贝随编译器发行的固定运行时文件 | 只执行显式文件操作；适配层不扫描源码、不分析源码、不生成代码。失败需转成可检查的稳定结果/错误。 |
| 外部进程 | 可选地启动生成的 Python 程序并返回 stdout、stderr、退出码 | 仅供明确请求的 `--run` 使用；不允许以该接口调用 stage0/既有 Python 编译器。首期不要求编译 Java 源码。 |

接口可用 Latent 列表、映射和标量表示结果；映射使用字符串键。推荐文件/路径错误返回稳定的 `{ok, value/error}` 类结果，避免不同宿主异常文字渗透到语言诊断。适配层应短小、可审计、无隐藏缓存或编译策略；记录每个函数的输入、输出、失败形式及副作用。

**哪些可以是宿主依赖：** CPython 本身、标准库、文件/路径/argv/输出/进程服务，以及 Python 目标运行时序言；生成程序使用的 Java daemon 源码可作为固定随附文件复制。

**哪些不能是宿主依赖：** Python tokenizer、Python AST、Python parser、Python 编译器模块、`argparse` 所驱动的编译流程、`json.dumps` 代替目标代码转义算法、Python 语义分析或 Python 版生成器。比如 Python 字符串字面量编码/转义、Latent 节点序列化为目标代码的规则，应由 Latent emitter 明确实现。

这属于**宿主依赖的自举**，仍可称为自托管编译器；它不是宿主无关或 native bootstrap。

## 5. 源模块结构与依赖限制

建议先按职责拆分 `.lt` 模块，以下仅是候选布局：`model.lt` → `lexer.lt` → `parser.lt` → `desugar.lt`、`semant.lt` → `module_graph.lt`、`gen_py.lt` → `compiler.lt` → `main.lt`；真实拆分应按所有实际引用关系画成无环图后再定。

模块边界必须符合当前 P2 规则：显式相对 `.lt` 路径、导入在普通代码前、无循环导入、别名显式、按包含导入语句的文件目录解析。同一模块不能因不同路径/别名而初始化多次。公共错误/位置/节点定义应放在依赖底层，避免 `parser ↔ desugar` 或 `semantic ↔ module_graph` 循环。

实现应只使用当前语言已有能力：列表/字符串的 `for`、索引与赋值、字符串键映射、函数/类/闭包、递归、`while`、`try/catch/throw`，以及现有 `len/range/str/int/push/keys`。标准库非目标：不假设 Python 的 `re`、`json`、`dataclasses`、集合类型、文件对象方法、迭代器协议或任意宿主 callable 在 Latent 中可用。解析扫描、排序、去重、转义、十进制/浮点文本格式等若需要，应由 Latent 算法或有限而明确的适配原语实现。

**建议的阶段错误约定：** 内部错误至少保留阶段、逻辑源文件、行、列和稳定消息；编译失败使用非零状态，成功只在完整产物写出后报告成功。禁止以“未知节点忽略”“语义检查失败继续生成”等静默降级处理版本不匹配。

## 6. Stage0→Stage1→Stage2→Stage3 产物流

定义固定的源码入口（候选名 `bootstrap/compiler.lt`）和固定产物清单。stage 产物目录均独立，构建不可覆盖源码树中的运行文件：

1. **Stage0（信任种子）**：使用已发布的 Python 编译器提交 `9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9`。它是首期唯一用来启动自举的既有编译器，不把其 Python 实现算作 stage1。
2. **Stage1（首个 Latent 编译器）**：Stage0 将 `bootstrap/compiler.lt` 的本地模块图编译到 Python。构建器把经过审查的薄 `latent_host.py` 与所需固定运行时文件放入独立目录。Stage1 执行时是生成的 Python 编译器，而不是 Python 版 `latent.py`。
3. **Stage2（由自己编译）**：运行 Stage1，以同一份 `bootstrap/compiler.lt` 和同一宿主契约编译到全新目录。Stage2 的文件名/清单、接口与调用方式应与 Stage1 相同。
4. **Stage3（闭环复现）**：运行 Stage2 编译完全相同的源码到另一全新目录。将 Stage2 与 Stage3 的规范化产物清单逐文件比较；全部文件逐字节相同，才算通过 bootstrap fixed-point 验收。
5. **行为对拍**：Stage0、Stage1、Stage2 分别编译/运行受支持测试集。以同一组输入比较成功/失败状态、程序 stdout、编译诊断（规范化临时根目录后）与运行错误的 Latent 源位置。

产物建议包含：生成的 `compiler.py`、同版本的 `latent_host.py`、该 compiler 运行时必须随附的固定文件、以及只包含稳定相对文件名和 SHA-256 的 manifest。不得写入 stage 序号、构建时间、随机数、当前用户名或临时目录。`__pycache__`、日志和本机生成的 class 文件不列入可复现产物；比较前按 manifest 清单，而不是递归比较任意临时文件。

首期可由一个小型、审计过的外层构建驱动启动 Stage0 并整理固定产物；它不得实现编译器阶段。之后 Stage1+ 的自编译必须由生成的编译器 CLI 完成，不能依赖另一个 Python 编译实现。

## 7. 可复现性与路径策略

路径策略是实现前必须明确的设计项：当前 `module_system.py` 以 `realpath` 作为模块身份、并以其 SHA-1 摘要生成内部名；当前 Python 生成器还会将实际源路径写进源码位置元数据。若直接照搬绝对路径，stage2 与 stage3 在同一工作树或许相同，但换 checkout 根目录会得到不同的符号、源码映射或字节产物。

**推荐把物理路径和编译身份分离：**

- 物理路径由适配层用于读取文件、解析相对导入、写输出。
- 编译器维护稳定的逻辑源 ID：对自举源码使用显式 bootstrap 根目录下、`/` 分隔的相对路径；模块身份、稳定生成名和序列化顺序依赖逻辑 ID，而非 checkout 的绝对根。
- 普通用户程序的运行期诊断可以在运行时解析逻辑根/映射为本机可读路径；不能把当前构建机绝对根误当成编译语义。
- 代码生成须确定性：模块遍历、符号分配、映射/源映射序列化、字符串转义、文件编码（UTF-8）与换行（LF）稳定；不依赖哈希表随机顺序、当前 cwd、locale 或环境变量。
- manifest 按规范化相对路径排序，产物内容 SHA-256 比较；变更源码或宿主适配契约须触发重建。

**量化路径验收：** 在两个不同绝对路径的干净 checkout 中分别由本地 Stage0 构造 Stage1，再分别构造 Stage2/Stage3。允许 Stage1 因现有 Stage0 的路径行为而不同；要求每个 checkout 内 `Stage2 == Stage3`，并要求两个 checkout 的 Stage2/Stage3 规范化 manifest 与文件字节也一致。若这一目标被确认过重，可先将跨根一致性降为后续门槛，但此时文档和发布说明必须明确只保证同一路径内的复现，不能宣称路径无关。

## 8. 测试与精确验收标准（拟议）

1. **既有基线不退化：** Stage0 通过仓库原有全部 259 项回归；本地已在 Python 3.12.3/JDK 25.0.4 环境实际复跑（259 passed、0 failed）。CI 仍沿用 Python 3.11/JDK 17 与 21 矩阵；新增自举 job 固定 Python 3.11/JDK 17。
2. **目标案例清单无隐式漏项：** 为现有 `tests/run_tests.py` 建立明确的“Python 目标投影”清单，覆盖所有适用于 `-t py` 的正例、模块例、编译期负例、运行期负例、源码位置断言和 CLI 合约。每项需在 manifest 中归类为纳入或附理由排除；CI 对新增未分类用例失败。Java 后端输出专属断言可明确排除，不能把 Java 互操作在 Python 后端运行的用例误排除。
3. **自举阶段可执行：** Stage1、Stage2、Stage3 均能从独立干净目录启动，并能用 `-t py` 编译同一组正例；执行结果与 Stage0 的对应 Python 目标结果一致。Stage1/2 的目标投影所有用例零差异：退出状态一致，stdout 一致；编译错误的阶段、消息、源文件/行/列一致（仅允许将临时 checkout 根规范化）；运行期错误的 `.lt` 文件与行号一致。
4. **闭环复现：** 单次构建中 Stage2/Stage3 manifest 的相对路径集合完全相同，每个文件 SHA-256 一致且字节一致；没有额外未声明产物。CI 必须在不复用 Stage2 输出目录的前提下执行。
5. **路径复现：** 按 §7 的两个不同 checkout 根执行，Stage2/Stage3 产物集合和字节相同；至少使用两个路径前缀不同的独立工作目录。
6. **核心确实自托管：** 静态审计 allowlist 明确列出全部 Python adapter 函数；扫描编译器 `.lt` 核心与生成 Stage1/2 Python，禁止导入/打开/执行 stage0 的 compiler modules 或调用它们。允许的 Python 模块限于宿主适配、Python 标准库及 Latent Python 目标运行时。提供一个测试使 stage1 在没有 stage0 文件/`latent.py` 可见的独立产物目录仍能运行并编译。
7. **错误及文件安全：** 对缺失输入、不可读导入、无效目标、不可写输出目录分别断言非零状态和稳定诊断；编译失败不留下看似成功的半成品主产物。测试不得写入源码目录。
8. **范围不冒充：** 阶段日志与文档准确称其为“Python 宿主上的自托管/可复现自举”；除非将来通过独立验收，不得标称 host-independent、native 或无需 Python。

以上验收计数以动态 manifest 报告具体用例数，而不是人为承诺首期测试总数。阶段差异必须以明确测试或审阅决议解释；不能通过降低 Stage0 测试集、忽略差异或宽泛路径替换让结果通过。

## 9. CI 建议

保留现有 `tests.yml` 全量双后端矩阵作为 Stage0 回归门禁；新增独立 bootstrap job，使用固定 Python 3.11 和自举所需的 JDK（建议先用 17），执行：

1. 从固定 v0.13.5 commit 准备 Stage0；将目标分支 `.lt` 自举源码与固定 adapter 分离放置。
2. 在两个随机但稳定记录的临时绝对根目录执行 §6/§7 stage flow；所有构建目录清理前先完成 manifest 比较。
3. 跑 Stage1/Stage2 Python 目标投影及行为对拍；检查 Stage2/Stage3 同 checkout 与跨 checkout 字节一致。
4. 检查 adapter allowlist、产物文件清单、无源码目录写入、无调用 stage0、无未分类测试。
5. CI 日志只输出 stage 标识、逻辑相对路径、hash、差异摘要和测试计数，不输出用户环境变量或敏感值。

Job 失败时保留差异 manifest 与最小复现信息作为 CI artifact，正常通过时无需发布生成的 Stage1 二进制/产物；是否要把某个 stage 作为正式 release 资产，应另行讨论。

## 10. 主要风险与缓解

| 风险 | 影响 | 缓解建议 |
|---|---|---|
| 编译器源码大，首期一次重写过多 | Stage0→1 慢、调试难 | 按词法/语法、脱糖/AST、语义、模块、代码生成分阶段实现；每阶段先与现有 Python 实现差分。 |
| Latent 库能力有限 | 手写数据结构、字符串扫描和 emitter 代码冗长 | 先以实际编译器需要验证能力，不新增标准库；编写小规模 Latent 工具函数并让其纳入自身测试。 |
| Python 适配层边界逐渐膨胀 | 表面自托管、实际编译逻辑仍在 Python | adapter allowlist + 源码审查；拒绝 token/AST/语义/emitter 类型服务；验证 stage1 在无 stage0 条件下工作。 |
| 绝对路径泄漏 | stage2/3 在同一路径通过，但移动项目后失效或不复现 | 逻辑源 ID 与物理路径分离；双根 checkout 实测字节复现。 |
| Python 字符串/浮点格式化不稳定 | 生成 Python 语法错误或 stage 产物不一致 | 将转义与数字编码作为 Latent emitter 单独设计、边界测试；不借用宿主 JSON/`repr`。 |
| 异常/错误文本泄漏宿主差异 | 诊断跨阶段不一致、位置丢失 | 编译器内部携带统一阶段/位置/消息结构；adapter 只归一化 I/O 错误，语言错误由 Latent 生成。 |
| 混淆可复现自举与脱离宿主 | 用户对可移植性的预期错误 | 每个阶段/发布准确声明 Python 运行时要求；把 C/VM/native 路线独立列为后续项目。 |
| Stage0 固定种子难以维护 | 历史源与当前源码分歧 | 显式 pin commit/hash 并记录更新流程；更新种子须单独对拍与审查，不能自动跟随 HEAD。 |

## 11. 后续阶段（不是首期承诺）

1. **实施阶段：** 按已批准的实施计划，将编译器源代码分片、adapter API、阶段里程碑和逐步 parity 清单落实；逐步推进到 Python 目标闭环。
2. **扩展兼容面：** 决定是否为 stage compiler 加回 AST/脱糖诊断、editor metadata、REPL 及 CLI 兼容；实现并纳入差分测试。再评估 Latent 编译器生成 Java 后端是否有实际优先级。
3. **第二宿主或第二种子：** 若需要更强独立性，可让同一份 Latent 核心在另一成熟宿主/后端上编译运行，再与 Python stage 做互相对拍；这仍不自动等于 host-independent。
4. **便携/native bootstrap：** 单独选择一个稳定且可审计的最小种子（例如小型 C 引导程序或明确定义的字节码/VM），定义目标平台、编译器运行时 ABI、交叉编译和发布种子更新策略，再设计新的阶段与验收。未作此选择前不预设 C、Java、Wasm 或其他路线。

## 12. 已批准的设计选择

用户批准以下首期边界；实现必须据此推进，不得静默缩减或扩大：

- 采用 **Python 宿主上的 Stage0→Stage1→Stage2→Stage3，自托管 Python-target compiler**。
- 完整保持 v0.13.5 Python-target 语义，包括显式本地 `.lt` 模块、`-o`、`--run` 及 Python target 已有互操作行为；Java codegen、REPL、AST/editor 诊断模式暂缓。
- 编译器词法、AST、解析、脱糖、语义、模块图、Python emitter、CLI 策略与诊断均由 Latent 实现；Python 仅提供受限、版本化的 OS 宿主适配。
- Stage2/3 不但要同根逐字节一致，还须在两个不同绝对路径的干净 checkout 中逐字节复现。
- Stage0 固定于 `9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9`；自举 CI 使用 Python 3.11/JDK 17，保留现有 Python 3.11/JDK 17、21 全量矩阵。
- 本次授权只涵盖代码提交与推送；不涵盖创建版本 tag 或 GitHub Release，也不修改 main 分支保护。

## 13. 实施记录

> 本节按时间记录实施过程；前面的状态是历史快照，最新首期验收结论见本节末尾。

- 设计边界与 P15 实施计划均已批准；用户已明确授权实现、测试并推送，无需再次确认。
- M0 投影账本、M1 薄宿主契约和 M2 微型 Stage0→1→2→3 垂直切片已有初版且聚焦测试通过；当前投影账本归类 243 个 runner case 与 291 个文件路径。Stage0 全量基线实测 259 passed、0 failed（本地 Python 3.12.3/JDK 25.0.4）。
- M3 已实现并测试 Latent ASCII lexer 切片；参考 Python lexer 的 Unicode 字母/数字/空白分类尚未复现，因此完整 M3 门禁仍未通过。不得以 Python adapter 补足该编译逻辑。
- M4 Latent AST/parser 限定子集已通过固定 Stage0 测试，覆盖函数与控制流块、常用表达式优先级、调用/属性/下标/列表/map、解析失败位置与不匹配闭括号。类/继承、导入、异常、global/nonlocal、完整形参/调用参数、py/java/super 及插值等尚未实现。
- 完整 v0.13.5 兼容、Stage1/2/3 完整自举、双根复现和最终 DoD 尚未完成；当前仅为可推送的阶段性基础，不构成 P15 首期验收完成。
- 本次只授权推送代码，不创建 tag/Release；main 分支保护保持原状。
- 2026-10-12：按用户重排后的第三步，新增 `bootstrap/gen_py.lt`，首个 Python emitter 只覆盖当前 Latent parser/AST 子集；`compiler.lt` 通过 Latent parser/emitter 流程生成输出，Python adapter 只执行宿主 I/O/路径/临时目录/生成程序进程服务。Stage0→Stage1 fixture 对拍和同一固定源码根重复生成检查通过；Stage1→Stage2→Stage3 仍因 parser 尚不支持 import/模块图而未覆盖，绝不作为完整自举声明。
- 本步 smoke 发现并修复 M3 lexer 的 dedent 后缩进栈槽位复用缺陷；新增 case 与固定 Stage0 token 差分。路径身份/source map 的规范化绝对路径影响仍由固定仓库源码根规避，跨根字节可复现性继续留待后续。

### 2026-10-13 当前验收状态：P15 首期 DoD 完成

- 固定 Stage0 为 v0.13.5 commit `9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9`，并验证 Stage0 → Stage1 → Stage2 → Stage3；没有更新 Stage0 pin。
- 首期范围限于 Python 宿主上的 Python-target compiler，不包括 native / host-independent bootstrap；保留已批准的 deferred scope。
- 243 个投影 case ID、244 次 fixture 调用、505 个固定种子性质用例全部零差异；Stage2==Stage3，两个完整 clean-clone 根目录的 Stage2/3 声明产物逐字节一致；Stage0 全量双后端基线 259/259。
- 首期 DoD 标记 **Done**。绿色 workflow 是验证证据，不替代对 code-scanning findings 的逐条复核。
