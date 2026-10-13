# P16-C：Python 宿主版本支持决策

## 决策

**将 CPython 3.12 纳入 bootstrap 正式支持范围。** 支持矩阵明确限定为：

| CPython | Unicode 数据库 | 状态 |
|---|---|---|
| 3.11 | 14.0.0 | 正式支持 |
| 3.12 | 15.0.0 | 正式支持（本 P16-C 评估） |

这项承诺针对 Python 宿主上的 Python-target bootstrap。已检查并保留 Latent 的薄宿主 API；Unicode 分类继续使用仓库内固定数据表，分类算法仍由 Latent 实现。其他 Python/UCD 组合尚未承诺；以后增加宿主版本必须提供匹配的 Unicode 表（若需要）并通过相同的宿主、lexer/parser、全量回归、自举投影和复现门槛。

## 基线与测试环境

- 起始 `main`：`3891fb4c2cd75152fcc5dc3ea72654a01281129a`，工作区干净。
- 固定 Stage0：v0.13.5，commit `9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9`；本评估未改动 pin。
- 标签 `v0.13.6` 仍指向 `a872101202d782ece1fe7888f51b7e8ee9430466`。
- 本地环境：Python 3.12.3、Unicode 15.0.0、OpenJDK 25.0.4.1、NumPy 2.5.3。

## P16-C 验收证据

在 Python 3.12.3 上，以下门槛均成功：

| 门槛 | 结果 |
|---|---|
| 投影账本与变更守卫 | 通过；243 个 runner case ID 已分类，291 个路径已登记；checker 的 13 个测试通过 |
| Python 宿主适配 API | 21 个契约测试通过，覆盖公开 allowlist、版本、路径/文件、稳定错误映射、临时目录与生成程序进程服务 |
| Unicode 与 lexer | 3 个测试通过；Unicode 15.0.0 属性边界、Stage0 token/value/位置差分均通过 |
| Parser | 4 个测试通过；包含固定 Stage0 差分及仓库 Latent 源文件解析检查 |
| Bootstrap smoke 与复现 | 19 个测试通过；Stage1→Stage2→Stage3 固定点成立，两个不同 clean-clone 根目录的 Stage2/3 产物逐字节一致 |
| 固定 Stage0 完整双后端回归 | 259 passed，0 failed |
| Stage0/Stage1/Stage2 Python-target 投影 | 243 case ID、244 次 fixture invocation、505 个固定种子 property；runner/property/artifact mismatches 均为 0，Stage2==Stage3 |

因此，本地证据同时覆盖 Python 3.12 的 API、Unicode 15 表、词法/解析行为、完整旧版 Stage0 回归及自举行为/产物复现；不是只依据语法编译或小型 smoke 测试作出版本承诺。

## CI 变更

`.github/workflows/tests.yml` 将完整回归矩阵扩展至 Python 3.11/3.12 × JDK 17/21；自举门禁扩展至 Python 3.11/3.12 × JDK 17。VS Code 扩展任务仍使用原有 Python 3.11 环境。P16-C 不更换 Stage0、不调整 P16-A/B 告警状态、不创建 tag/Release，也不修改分支保护。
