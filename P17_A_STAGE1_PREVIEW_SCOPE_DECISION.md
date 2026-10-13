# P17-A：Stage1 可选实验预览产品范围决策

> **状态：用户已批准（2026-10-13）本文所列 P17-A 产品范围默认值；本批准仅冻结范围决策，不授权启动 P17-B 实现或发布。**
> P16-D 的有条件 Go 只允许考虑显式 opt-in 的实验预览，并明确要求先另行确认产品选择；本记录载明用户已批准的选择。

**审查基线：** `main` / `f3d092ae1f1cb576d25ed34c88853b0c76495811`；该基线提交时工作树干净（2026-10-13）。

## 决议

首个预览限定为**仓库源码检出内、显式启动、按需构建的 Stage1 Python-target 编译器**。它是独立实验入口，不是 `latent.py` 的替代品；默认编译器、固定 Stage0、既有 CI/告警状态和发布流程均保持不变。

| 决策项 | P17-A 决策值（用户已批准） |
|---|---|
| 入口名称与形状 | 独立命令名 `latent-stage1`，参数原样委托给 Stage1 CLI；源码检出阶段的规范调用为 `python3 tools/latent_stage1.py <Stage1 参数>`。不增加 `latent.py --stage1` 模式，不改写或覆盖 `latent` 命令。若未来引入可安装发行包，才在另行批准后将同一命令注册为 console script。 |
| 分发与构建 | 首期仅提供仓库检出内的源码包装器；首次需要时，以本机仓库中固定 Stage0 commit 构建 Stage1。沿用现有 `git archive <固定 commit>` 的本地取源路径，不自动联网下载、不发布 wheel/安装器/预编译资产。若检出中缺少该 commit，给出明确错误并停止，不回退到 `latent.py`。 |
| 缓存 | 仅使用用户级缓存（Linux 上优先 XDG cache，默认可落到 `~/.cache/latent/stage1/`），不写源码树或项目输出目录。键至少包含 Stage0 固定 commit、影响产物的 Stage1 源码/宿主适配/包装器/随附运行时输入的确定性摘要、Python 主次版本、UCD 版本及平台标识；构建产物通过完整性清单校验并原子发布。并发构建和不完整/损坏缓存不得产生半成品或静默降级；缓存 miss/失效只触发固定 Stage0 重建，构建失败即失败。 |
| 预览复审与退出 | 自 P17-B 实现进入主线起 **90 天内复审**，并且任何包含该入口的首次公开发布前必须先复审（以先到者为准）。复审须明确选择：继续实验并给出下一次复审日期、经独立决策毕业为稳定功能，或撤回入口。不得自动转为稳定；撤回时移除入口/预览说明并确保旧缓存不会再被调用。 |
| 平台与能力声明 | 仅沿用已验证范围：Ubuntu 24.04 上的 CPython 3.11 / Unicode 14.0.0，以及 CPython 3.12 / Unicode 15.0.0。当前证据不支持对 Windows、macOS、其他 Linux 发行版或其他 Python/UCD 组合作产品承诺。Stage1 只编译 Python target；Java target、REPL、AST/脱糖诊断、编辑器元数据均不属于该预览。 |
| 发布边界 | P17-A 只形成决策文档。P17 不创建 tag、GitHub Release 或发布资产；预览实现和任何后续公开发布都须分别审阅/授权。不得在本决策下更换 Stage0 pin、改 P16-A Open 告警状态或修改现有 `latent.py` 默认路径。 |

## 入口与用户预期

推荐包装器只负责定位构建输入、生成/复用 Stage1 并启动 Stage1；编译器策略仍由 `bootstrap/*.lt` 实现，薄 Python 宿主仅提供既有 OS 服务。包装器不得自行解析/重写编译选项、实现编译逻辑或在 Stage1 失败时转交 `latent.py`。

转发的 Stage1 CLI 形状应保持当前源码契约：

```text
latent-stage1 <source.lt> [-t py | --target py] [-o OUTDIR | --outdir OUTDIR]
              [--source-root ROOT] [--run] [-h | --help]
```

应保持其 Python-only 行为：请求 Java target 或 Stage1 不支持的诊断选项时明确报错并以非零状态退出。Stage1 的当前默认输出目录为 `build/py`，不同于 `latent.py` 的既有默认输出目录；帮助和预览文档需提示此差异及实验状态。不得声称 Stage1 具备 `latent.py` 的 Java 生成、REPL、诊断或编辑器功能。

## 实施前最低验收边界

P17-B 若获负责人批准，至少应保留 P16-D 的验收要求：

- `latent.py` 的默认入口、默认输出及现有行为不变；Stage1 opt-in 入口端到端使用 Latent 实现的 Stage1，不含静默 fallback。
- 继续执行固定 Stage0/Stage1/Stage2 投影：243 个 case ID、244 次 fixture invocation、505 个固定种子性质案例零 mismatch；Stage2==Stage3，双 clean-clone 根目录产物逐字节一致。
- 现有完整双后端回归（P16-D 记录的 259 项基线）及相关 CI 全绿；明确覆盖支持矩阵中的两组 CPython/UCD 组合。
- 帮助、错误路径和文档清楚标识实验预览、Python-only 限制、支持范围与退出/复审机制。

新增操作系统、Python/UCD 组合、功能、安装/包分发、稳定承诺或发布资产均不因这些验收自动获批，须另作决定。

## 仓库证据与适用边界

- 当前 `README.md` 将现有编译器入口记录为 `python3 latent.py ...`，并说明 P15 自举编译器只生成 Python、不替代 `latent.py`（README §P15 与“快速开始”）。`latent.py:175-191,233-250` 显示现有 CLI 与输出目录处理；不得借新预览改变该入口。
- `bootstrap/compiler.lt:163-229` 实现 Stage1 参数处理、Python target 限制、`--source-root`、`--run`、帮助和诊断选项的明确拒绝；其当前默认输出目录为 `build/py`。`bootstrap/latent_host.py` 明确限定为薄 OS 适配层。
- `P16_C_PYTHON_HOST_SUPPORT.md` 承诺的宿主/UCD 组合为 CPython 3.11 / UCD 14.0.0 与 CPython 3.12 / UCD 15.0.0。`P16_D_STAGE1_PRODUCTIZATION_DECISION.md` 指出现有 CI 在 Ubuntu 24.04 上运行、没有 Windows/macOS CI，并且明确未选命令名、分发/按需构建、缓存和预览期限。
- `tests/test_bootstrap_smoke.py` 固定 Stage0 commit 为 `9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9`，通过本地 `git archive` 提取；`tests/bootstrap/project_stage0.py` 使用该 pin 构建 Stage1。当前 `.github/workflows/tests.yml` 的 bootstrap job 使用 Ubuntu 24.04、Python 3.11/3.12，并以 `fetch-depth: 0` 保留历史。仓库当前没有 `pyproject.toml`、`setup.py`、`setup.cfg`、`requirements*.txt` 或 Makefile 等 Python 安装元数据；因此批准的首期不扩张为包分发，与源码检出内按需构建边界一致。
- P16-D 已记录最低行为/复现门槛。本 P17-A 不重新解释或降低这些门槛，也不把 P16-C/D 中的历史基线提交误写为本次实际审查基线。

## 明确不做

本记录只记录用户已批准的 P17-A 范围决策，不实施任何运行时代码、CLI 包装器、构建/缓存逻辑、CI 工作流、文档入口链接或发布配置变更；不修改 Stage0 pin、P16-A 告警、tag、Release、分支保护或 `latent.py`。启动 P17-B 实现仍须另行明确审阅并授权实现范围；本次批准不构成实现许可。
