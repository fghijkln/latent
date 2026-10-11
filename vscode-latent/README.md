# Latent Language Support for VS Code

Latent Language Support 为 `.lt` 文件提供轻量编辑与运行支持，不会改写 Latent 编译器源码。插件 0.6.4 随 Latent v0.13.3 发布，除解析器驱动的大纲与代码折叠、关键字补全及路径/保存安全处理外，还高亮仅限关键字形参的裸 `*` 分隔符并提供 `fnkw` 片段。大纲/折叠需要 v0.13.2 或更新的编译器；P11 语法需要 v0.13.3 或更新版本。

完整安装和使用说明见 [VS Code 插件使用指南](../VSCODE_EXTENSION_GUIDE.md)。

## 功能

- 自动识别 `.lt` 文件并提供语法高亮、`#` 行注释、括号/引号配对和块缩进。
- 关键字高亮与补全清单由编译器词法表同步生成；新增保留字可自动进入编辑器补全。
- 在 VS Code 大纲中展示函数与类（类方法按层级归入类），并支持函数、类及控制流缩进块的代码折叠。
- 高亮普通/默认形参、`*rest`、裸 `*` 仅限关键字分隔符、`**extra`、命名实参，以及调用中的 `*items` 和 `**mapping`。展开标记只在函数签名或调用实参位置获得专属高亮，不会把普通乘法或幂运算误标为展开。
- 提供函数、默认参数、变参函数、命名调用和展开调用等代码片段，以及 `class`、`if`、`for`、`try/catch`、模块导入、Python/Java 句柄等片段。
- 从命令面板运行 **Latent: 编译到 Python**、**Latent: 编译到 Java**、**Latent: 运行（Python）**、**Latent: 运行（Java）** 或 **Latent: 运行项目测试**。打开 `.lt` 编辑器时，右上角也可直接运行两个后端。
- 读取编译器的位置诊断并标记在对应 `.lt` 文件上；完整输出显示在 **Latent** 输出面板。

## Workspace Trust

扩展会在 Restricted Mode 中加载，语法高亮、片段和关键字补全等编辑支持仍可用，命令也会显示并说明受限原因。大纲与代码折叠由项目解析器 AST 元数据驱动，因此 Restricted Mode 下不会启动解析器子进程；这两项支持会在工作区受信任后启用。编译、运行和项目测试会执行工作区中的 `latent.py`、Latent 源程序或测试代码，因此 Restricted Mode 下命令也不会启动进程；只有在用户自行确认并信任工作区后才能执行。扩展不会更改 Workspace Trust 状态。受限模式也不会读取工作区对 `latent.pythonPath` 与 `latent.outputDirectory` 的覆盖值。

## 要求

- 打开的工作区是 Latent 项目（项目根目录或其子目录中有 `latent.py`）。
- Python 3（默认使用 `python3`）；大纲与折叠也通过项目的 `latent.py --editor-metadata` 解析当前缓冲区。Java 后端需要 JDK（包括 `javac` 和 `java`）。
- 使用 P10 的源程序需要 v0.13.0 或更新的编译器；使用 P11 仅限关键字形参的程序需要 v0.13.3 或更新版本。扩展本身只提供编辑器支持和调用入口。

## 安装

在 VS Code 中执行 **Extensions: Install from VSIX...**，选择 `latent-language-support-0.6.4.vsix`；或在终端执行：

```sh
code --install-extension /absolute/path/to/latent-language-support-0.6.4.vsix
```

安装后重新加载 VS Code 窗口，以载入新版本。然后打开 Latent 项目，编辑 `.lt` 文件，并从命令面板选择命令。

## 设置

- `latent.pythonPath`：Python 可执行文件，默认 `python3`。如使用虚拟环境，可填写可执行文件路径；此设置只接受可执行文件名/路径，不接受附加 shell 参数。
- `latent.outputDirectory`：编译产物目录，默认 `.latent-vscode/out`（相对 Latent 项目根目录）。插件会校验解析后的真实路径，包括符号链接目标；任何实际指向项目外的输出路径都会在启动编译器前被拒绝。

编译或运行前插件会保存当前文件；运行项目测试前会保存所有已打开的同项目脏文件，若任何文件保存失败则取消测试。进程通过操作系统参数数组启动（不经过 shell），项目路径和输出路径含空格也可安全处理。运行 Latent 程序会执行用户选择的源代码；只有用户主动触发“运行”命令时才执行。

## 开发与测试

```sh
npm install
npm test
npm run package
```

`npm test` 会验证 TextMate token/scopes 和 P8/P9/P10 片段，并通过隔离的 VS Code Extension Host 检查可信模式下的 Python/Java 命令、P10 编译运行、诊断与项目回归测试，以及 Restricted Mode 下命令的执行阻断。

解析器与扩展之间的元数据 JSON 格式、版本和坐标约定见 [EDITOR_METADATA.md](../EDITOR_METADATA.md)。
