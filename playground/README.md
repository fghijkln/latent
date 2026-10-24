# Latent Playground

在浏览器里写 Latent，编译成 Python 并运行。

- 纯标准库 Python 服务（`app.py`）+ 单页界面（`index.html`）
- 防护措施：禁用 `py`/`java` 互操作与模块导入；程序在独立子进程组运行，限制 CPU、地址空间、文件大小、进程数、运行时间及捕获输出；HTTP 请求体也有长度和读取时限
- 每个服务进程最多执行一个程序（含编译阶段）；忙时 `/api/run` 返回 HTTP 503，不创建额外执行子进程。该上限不跨服务进程共享，多 worker/多副本部署还需总资源配额及入口限流。
- 输出超限会中止程序并标记 `truncated=true`、`ok=false`；界面显示“输出超限，程序已中止”及退出码，不把截断等同于正常执行成功。
- Docker 镜像中的 `/app` 由 root 持有且对 `runner` 不可写，降低逃逸代码持久改写服务页面的风险；运行程序的子进程仍与服务同 UID，因此这不是完整的进程隔离。

这些 Python 层限制、生成代码的命名隔离和运行时属性检查**不是完整的操作系统级沙箱**，不能替代容器/OS 的强隔离。它们不提供独立的文件系统、网络、系统调用或服务账号权限边界，也不代替 HTTP 连接数和按来源限流。请勿将本服务暴露给不可信用户，除非部署环境另行提供适当的系统级隔离和资源控制。

仓库没有生产部署清单，无法确认实际运行环境是否启用了只读 rootfs、执行代码的独立 UID、收紧的 capabilities/seccomp 或网络隔离。部署前应逐项核验这些条件；至少应使用只读根文件系统，并仅为运行所需的临时目录提供受限可写挂载。由于服务与执行子进程仍同 UID，任何尚未发现的代码执行逃逸仍可能向该 UID 的服务进程发信号；不要把镜像内 `/app` 权限收紧误当成此风险已消失。

本地运行（仓库根目录）：

```bash
PORT=8080 python3 playground/app.py   # http://localhost:8080
```

Docker：

```bash
docker build -f playground/Dockerfile -t latent-playground .
docker run -p 8080:8080 latent-playground
```
