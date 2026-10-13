# P16-D：Stage1 产品化决策记录

**审查基线：** `main` / `dfdc7866cec2dc6a1e53e1071fe0589a868098a7`（2026-10-13，审查开始时工作区干净）  
**结论：有条件 Go——建议后续评估并实现显式 opt-in 的实验预览入口；本记录本身不授权实现或发布。**

## 决策与理由

P15 首期已在其明示范围内完成：固定 v0.13.5 Stage0 → Stage1 → Stage2 → Stage3 的 Python-hosted、Python-target 自举；Stage0/1/2 投影覆盖 243 个 case ID、244 次 fixture invocation 和 505 个固定种子性质用例，零 mismatch；Stage2==Stage3，双 clean-clone 根目录产物逐字节一致。当前 CI 已执行这些门禁。此证据足以支持让愿意尝试自托管实现的开发者**主动选择**预览，但不等于 `latent.py` 的产品等价替代或更广的平台承诺。

适合的场景是编译器贡献者检查自举链，或明确接受实验性边界、只需 Python target 的 Latent 使用者试用。需要 Java target、REPL、只读诊断/编辑器元数据，或希望沿用现有默认体验的用户应继续使用 `latent.py`。

## 已有能力与 CLI 差异

Stage1 的编译核心（词法、解析、脱糖、语义、模块图、Python emitter 与 CLI 策略）由 `bootstrap/*.lt` 实现；`bootstrap/latent_host.py` 是提供 argv、文件/路径、输出及运行生成程序等 OS 服务的薄 Python 适配层。Stage1 CLI 有 `-t/--target py`、`-o/--outdir`、`--source-root`、`--run` 与帮助；不支持的目标和诊断选项会明确失败，不静默回退。

与当前 `latent.py` 相比：两者都可编译到 Python、指定输出目录并选择运行；Stage1 另有显式 `--source-root`，默认输出目录也不同。`latent.py` 还支持 Java 代码生成、REPL、`--show-ast`/`--show-desugar` 与 `--editor-metadata`；Stage1 **不支持**这些入口/模式，仅发射 Python。Stage1 不是已有 CLI 的同义入口；暴露时必须让用户显式 opt-in，并保留当前默认行为及现有编译器。

## 支持范围与成本

- **已承诺的 Python 宿主/UCD 组合：** CPython 3.11 / Unicode 14.0.0；CPython 3.12 / Unicode 15.0.0。其他 Python/UCD 组合未承诺。
- **CI 实际平台：** `.github/workflows/` 使用 Ubuntu 24.04；自举门禁覆盖上述两个 Python 版本与 JDK 17，完整回归覆盖 Python 3.11/3.12 × JDK 17/21。当前没有 Windows/macOS CI 矩阵；代码中的跨平台路径探针不构成这些系统的产品支持承诺。生成的 Python target 仍需 Python 运行时；使用 Java interop 时需 JDK。
- **维护成本：** 两套 compiler/CLI 需并行维护；语言语义或运行时变更须继续跑固定 Stage0 差分、自举固定点、跨根复现和完整回归。Unicode 表与 CPython/UCD 对应关系须持续管理；新增 OS/Python 组合会扩大验证矩阵。实验标签不能削弱这些门禁。

## 后续实现的单独确认与验收条件

本次**不**选定命令名、分发/按需构建方式、缓存策略或预览期限。开始实现前须另行确认这些产品选择及明确实现范围。建议最小范围：只新增独立 opt-in 入口和清晰实验提示；不切换默认、不覆盖或替换 `latent.py`、不做静默 fallback；Stage0 继续固定在 `9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9`；unsupported target/options fail closed；只宣称上述 Python/UCD 与经验证平台范围。

最低验收：入口端到端使用 Latent 实现的 Stage1；现有 `latent.py` 命令/默认输出回归不变；Stage0/1/2 投影维持 243 case ID、244 fixtures、505 properties 零差异，Stage2==Stage3 且双 clean-clone 根产物一致；全量原有双后端回归与 CI 全绿；帮助、文档、错误路径明确区分实验预览与现有稳定入口。任何更广的 Python/UCD 或 OS 支持、Java target、诊断/REPL 功能和发布承诺均另行决策。

## 非目标与状态

本决定不实施新入口、不改运行时代码、默认 CLI、CI、Stage0 pin、P16-A 的 8 条 Open 告警状态、`v0.13.6` tag 或 Release。审查时 `main` 指向 P16-C 提交 `dfdc7866…`，该提交的 GitHub Actions **Tests、DevSkim、Push on main 均成功**（[Tests run](https://github.com/fghijkln/latent/actions/runs/38385418282)）；`v0.13.6` 仍指向较早的 `a872101`。
