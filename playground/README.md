# Latent Playground

在浏览器里写 Latent，编译成 Python 并运行。

- 纯标准库 Python 服务（`app.py`）+ 单页界面（`index.html`）
- 防护措施：禁用 `py`/`java` 互操作与模块导入；程序在独立子进程组运行，限制 CPU、地址空间、文件大小、进程数、运行时间及捕获输出；HTTP 请求体也有长度和读取时限

这些 Python 层限制和运行时属性检查**不是完整的操作系统级沙箱**，不能替代容器/OS 的强隔离。请勿将本服务暴露给不可信用户，除非部署环境另行提供适当的系统级隔离和资源控制。

本地运行（仓库根目录）：

```bash
PORT=8080 python3 playground/app.py   # http://localhost:8080
```

Docker：

```bash
docker build -f playground/Dockerfile -t latent-playground .
docker run -p 8080:8080 latent-playground
```
