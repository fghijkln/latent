# Latent Language Support for VS Code

Latent Language Support 为 `.lt` 文件提供轻量编辑与运行支持，不会改写 Latent 编译器源码。插件 0.6.2 随 Latent v0.13.0 一同发布，增加 P10 变参语法高亮，并补全 P8 命名参数和 P9 默认参数的编辑支持。P10 编译/运行需要 v0.13.0 或更新的 Latent 编译器。

完整安装和使用说明见 [VS Code 插件使用指南](../VSCODE_EXTENSION_GUIDE.md)。

## 功能

- 自动识别 `.lt` 文件并提供语法高亮、`#` 行注释、括号/引号配对和块缩进。
- 高亮普通/默认形参、`*rest`、`**extra`、命名实参，以及调用中的 `*items` 和 `**mapping`。展开标记只在函数签名或调用实参位置获得专属高亮，不会把普通乘法或幂运算误标为展开。
- 提供函数、默认参数、变参函数、命名调用和展开调用等代码片段，以及 `class`、`if`、`for`、`try/catch`、模块导入、Python/Java 句柄等片段。
- 从命令面板运行 **Latent: 编译到 Python**、**Latent: 编译到 Java**、**Latent: 运行（Python）**、**Latent: 运行（Java）** 或 **Latent: 运行项目测试**。打开 `.lt` 编辑器时，右上角也可直接运行两个后端。
- 读取编译器的位置诊断并标记在对应 `.lt` 文件上；完整输出显示在 **Latent** 输出面板。

## Workspace Trust

扩展会在 Restricted Mode 中加载，语法高亮等编辑支持仍可用，命令也会显示并说明受限原因。编译、运行和项目测试会执行工作区中的 `latent.py`、Latent 源程序或测试代码，因此 Restricted Mode 下命令不会启动任何进程；只有在用户自行确认并信任工作区后才能执行。扩展不会更改 Workspace Trust 状态。受限模式也不会读取工作区对 `latent.pythonPath` 与 `latent.outputDirectory` 的覆盖值。

## 要求

- 打开的工作区是 Latent 项目（项目根目录或其子目录中有 `latent.py`）。
- Python 3（默认使用 `python3`）；Java 后端需要 JDK（包括 `javac` 和 `java`）。
- 使用 P10 的源程序需要编译器实现 P10；扩展本身只提供编辑器支持和调用入口。

## 安装

在 VS Code 中执行 **Extensions: Install from VSIX...**，选择 `latent-language-support-0.6.2.vsix`；或在终端执行：

```sh
code --install-extension /absolute/path/to/latent-language-support-0.6.2.vsix
```

安装后重新加载 VS Code 窗口，以载入新版本。然后打开 Latent 项目，编辑 `.lt` 文件，并从命令面板选择命令。

## 设置

- `latent.pythonPath`：Python 可执行文件，默认 `python3`。如使用虚拟环境，可填写可执行文件路径；此设置只接受可执行文件名/路径，不接受附加 shell 参数。
- `latent.outputDirectory`：编译产物目录，默认 `.latent-vscode/out`（相对 Latent 项目根目录）。为避免路径转义与意外写入，插件拒绝项目根目录之外的输出路径。

运行前插件会保存当前文件。进程通过操作系统参数数组启动（不经过 shell），项目路径和输出路径含空格也可安全处理。运行 Latent 程序会执行用户选择的源代码；只有用户主动触发“运行”命令时才执行。

## 开发与测试

```sh
npm install
npm test
npm run package
```

`npm test` 会验证 TextMate token/scopes 和 P8/P9/P10 片段，并通过隔离的 VS Code Extension Host 检查可信模式下的 Python/Java 命令、P10 编译运行、诊断与项目回归测试，以及 Restricted Mode 下命令的执行阻断。
