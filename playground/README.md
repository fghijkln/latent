# Latent Playground

在浏览器里写 Latent，编译成 Python 并运行。

- 纯标准库 Python 服务（`app.py`）+ 单页界面（`index.html`）
- 安全限制：禁用 `py`/`java` 互操作与模块导入；程序在子进程中运行，限 5 秒 CPU、256 MB 内存，并限制输出与进程数

本地运行（仓库根目录）：

```bash
PORT=8080 python3 playground/app.py   # http://localhost:8080
```

Docker：

```bash
docker build -f playground/Dockerfile -t latent-playground .
docker run -p 8080:8080 latent-playground
```
