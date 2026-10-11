# Latent VS Code 插件使用指南

Latent Language Support 为 `.lt` 文件提供语法高亮、代码片段、编译与运行入口。此指南对应随 Latent **v0.13.3** 发布的 VS Code 插件 **0.6.4**；大纲与代码折叠需要 v0.13.2 或更新的编译器，P10 变参与调用展开需要 v0.13.0 或更新版本，P11 仅限关键字形参需要 v0.13.3 或更新版本。

## 安装

从 [Latent v0.13.3 GitHub Release](https://github.com/fghijkln/latent/releases/tag/v0.13.3) 下载 `latent-language-support-0.6.4.vsix`。在 VS Code 中打开命令面板（Windows/Linux：`Ctrl+Shift+P`，macOS：`Cmd+Shift+P`），运行 **Extensions: Install from VSIX...** 并选择下载的文件；也可以在终端运行：

```sh
code --install-extension /absolute/path/to/latent-language-support-0.6.4.vsix
```

安装或升级完成后，执行 **Developer: Reload Window**。插件要求 VS Code 1.85.0 或更新版本。

## 打开项目与编辑 `.lt` 文件

在 VS Code 中打开包含 `latent.py` 的 Latent 项目目录，再打开其中的 `.lt` 文件。插件会自动将文件识别为 Latent，并提供语法高亮、`#` 行注释、括号与引号配对，以及块缩进。

保留字补全与高亮词表同步生成；语言新增的关键字经生成与 CI 检查后，会自动出现在编辑器补全建议中。

函数和类会显示在 VS Code 的“大纲”（Outline）视图中；类方法按层级归入所属类。函数、类及 `if`、`for`、`while`、`try` 等缩进代码块可通过行号左侧的折叠控件收起。可信工作区中，插件会将当前缓冲区交给项目解析器的 `--editor-metadata` 模式，因此未保存的编辑也会反映在大纲与折叠范围中。

在 Latent 文件中输入片段前缀并按 `Ctrl+Space`（macOS 通常为 `Cmd+Space`；也可在键入前缀后等待建议）查看可用片段。常用前缀包括：

- `fn`、`fndef`、`fnv`、`fnkw`：普通函数、默认参数函数、含 `*rest` / `**extra` 的变参函数、含仅限关键字形参的函数。
- `callnamed`、`callspread`：命名参数调用、带 `*items` / `**mapping` 展开的调用。
- `class`、`if`、`for`、`try`、`import`、`say`、`py`、`java`：类、控制流、模块导入、输出及 Python/Java 互操作句柄。

选中片段后，按 `Tab` 在占位符之间移动。P10 的 `*rest`、`**extra`、`*items` 和 `**mapping` 及 P11 的裸 `*` 分隔符会在函数签名或调用实参位置获得专属语法高亮；普通乘法与幂运算不会被误当作展开标记。

## 编译、运行与测试

打开 `.lt` 文件后，按 `Ctrl+Shift+P` / `Cmd+Shift+P` 并搜索以下命令：

- **Latent: 编译到 Python**、**Latent: 编译到 Java**：编译当前文件，不运行程序。
- **Latent: 运行（Python）**、**Latent: 运行（Java）**：编译并运行当前文件。编辑器标题栏也提供这两个运行入口。
- **Latent: 运行项目测试**：在项目根目录运行 `tests/run_tests.py`。

编译器诊断会映射到 `.lt` 文件的相应行列；完整过程和输出显示在 **Latent** 输出面板。编译或运行前插件会保存当前文件；运行项目测试前会先保存所有已打开的同项目脏文件，任何保存失败都会取消测试。编译产物默认写入项目根目录下的 `.latent-vscode/out`。

## 运行环境与设置

调用插件命令需要 Python 3，因为它通过 Python 启动项目中的 `latent.py`。Java 后端需要 JDK 17 或更新版本（包括 `javac` 和 `java`）；Python 后端程序如果使用 `java` 互操作，也需要可用的 Java 运行环境。只有源程序实际使用 Python 或 Java 互操作时，才需要对应的外部运行时。

可在 VS Code 的用户设置或项目 `.vscode/settings.json` 中配置：

```json
{
  "latent.pythonPath": "python3",
  "latent.outputDirectory": ".latent-vscode/out"
}
```

`latent.pythonPath` 是 Python 可执行文件名或路径，不接受附加 shell 参数。`latent.outputDirectory` 是相对于 Latent 项目根目录的路径；插件会检查解析后的真实路径，包含符号链接的最终目标，并拒绝实际位于项目目录之外的输出路径。若使用虚拟环境，可将 `pythonPath` 设为该环境中的 Python 可执行文件。

## Workspace Trust 与安全边界

插件在 VS Code **Restricted Mode** 中采用受限支持：语法高亮、片段、关键字补全和命令列表仍可用；大纲与代码折叠需要调用工作区内的解析器，因此在受限模式下不会启动解析器进程，也暂不可用。编译、运行及项目测试涉及执行工作区内的编译器、源程序或测试代码，因此这些命令也会被阻止，不会启动进程。插件不会代替用户更改 Workspace Trust。

如果你已检查项目来源并决定执行其中的代码，应由你在 VS Code 中自行确认并信任该工作区，然后再运行命令。Restricted Mode 下，插件也不会读取工作区对 `latent.pythonPath` 和 `latent.outputDirectory` 的覆盖设置。是否信任工作区应由用户根据项目内容自行判断。

更多语言语义请参阅 [Latent 教程](TUTORIAL.md)、[语言规范](SPEC.md) 和 [P10 变参设计](P10_VARIADIC_ARGUMENTS_DESIGN.md)。

解析器元数据 JSON 的稳定格式、版本及坐标约定见 [编辑器元数据接口](EDITOR_METADATA.md)。
