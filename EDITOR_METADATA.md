# 编辑器元数据接口

编译器提供一个只读、带版本的解析器接口，供编辑器导航和代码折叠使用。编辑器行为由 Latent 语法及 AST 结构驱动，不再由插件重复猜测语法。

## 调用方式

在仓库根目录，将当前 `.lt` 缓冲区写入标准输入：

```sh
python3 latent.py --editor-metadata < current-buffer.lt
```

命令只进行词法分析和解析，并向标准输出写出一个 JSON 文档；**不会**运行语义分析、生成后端、写编译产物或执行源程序。遇到词法或语法错误时，诊断写入标准错误、返回非零状态码，且不输出不完整 JSON。此模式可以省略位置参数；VS Code 插件通过这种方式传入当前缓冲区，因此未保存的编辑也会反映在结果中。

## v1 文档格式

```json
{
  "format": "latent-editor-metadata",
  "version": 1,
  "symbols": [
    {
      "kind": "class",
      "name": "Box",
      "range": { "startLine": 1, "endLine": 4 },
      "selection": { "line": 1, "column": 7 },
      "children": [
        {
          "kind": "function",
          "name": "init",
          "range": { "startLine": 2, "endLine": 3 },
          "selection": { "line": 2, "column": 8 },
          "children": []
        }
      ]
    }
  ],
  "foldingRanges": [
    { "startLine": 1, "endLine": 4 },
    { "startLine": 2, "endLine": 3 }
  ]
}
```

- `format` 和整数 `version` 标识公开契约。消费者遇到未知格式或版本时应拒绝解析，不应猜测其含义。
- `symbols` 包含解析得到的 `fn` 和 `class` 声明。类方法作为类的子项；函数或控制流代码块中的嵌套声明归属于其外围声明。每个符号包含 `kind`（`function` 或 `class`）、`name`、范围 `range`、名称选区 `selection` 和 `children` 数组。
- `foldingRanges` 描述多行函数、类、条件、循环和 try/catch 结构；范围从语法块头行延伸到该结构最后一行源码。
- 行号和列号均为**从 1 开始**；范围的结束行包含在范围内。`selection.column` 指向声明名称的首字符。
- 消费者可以忽略不改变上述含义的新增字段。若要改变格式或坐标语义，必须提升版本号。

## VS Code 集成与工作区信任

插件使用 `latent.pythonPath` 启动项目中的 `latent.py`，并将当前文档内容传入标准输入；同一文档版本的大纲与折叠请求共享一次解析结果。只有格式和版本受支持、坐标有效的元数据才会转换成 VS Code 大纲符号与折叠范围。源码无效或元数据不兼容时，插件不返回大纲/折叠项目，但不影响编辑和关键字补全。

由于该功能会调用工作区中的编译器入口，插件在 Restricted Mode 下不会启动解析器进程。语法高亮、片段和关键字补全仍可用；基于 AST 的大纲与折叠会在用户信任工作区后启用。元数据模式本身只解析 Latent 源码，不会执行它。

运行契约测试：

```sh
python3 tests/test_editor_metadata.py
```
