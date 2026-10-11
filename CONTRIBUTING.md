# 贡献指南

感谢参与 Latent。提交代码前，请运行与改动相关的检查；涉及语言语义、编译器、运行时或互操作的变更必须同时验证 Python 与 Java 后端。

## 核心测试

在仓库根目录运行完整回归套件：

```sh
python tests/run_tests.py
```

解析器编辑器元数据接口使用独立契约测试：

```sh
python tests/test_editor_metadata.py
```

接口格式、版本、坐标约定和 Restricted Mode 边界见 [编辑器元数据接口](EDITOR_METADATA.md)。

套件会在两个后端运行正例、错误用例和 cookbook，并比较约定输出与错误行为。需要 Python 3、JDK（含 `java` 和 `javac`）；若测试环境缺少项目使用的 Python 库，请按 CI 配置安装。

边界性质测试使用固定种子，可重复检查数值、Unicode 字符串及列表索引等边界，并比较两个后端：

```sh
python tests/test_differential_properties.py --seed 0x5EED2026
```

失败报告会包含种子与用例信息，便于复现。修复后应保留或增加针对回归行为的测试。

## VS Code 扩展

以下命令均在 `vscode-latent` 目录运行：

```sh
npm ci
npm test
npm run package
```

`npm test` 包含语法 token 检查和 VS Code Extension Host 集成测试；运行集成测试需要可启动 VS Code 的环境。无图形界面的 Linux 可按 CI 使用 `xvfb-run --auto-servernum npm test`。扩展集成测试还会调用项目编译器，因此需准备 Python 和 JDK。

## 跨后端兼容性

Latent 的目标是同一份源码在 Python 与 Java 后端保持一致的可观察行为。新增或修改语义、编译器生成代码、运行时或互操作逻辑时：

- 为两个后端添加或更新成对回归覆盖；不能只凭单后端通过来判定正确。
- 检查两端的标准输出、退出状态及相关错误行为；正常用例应保持一致，预期行为变化应同步更新规范和测试。
- 运行上面的完整套件及固定种子边界性质测试；若改动影响扩展，也运行全部扩展检查。
