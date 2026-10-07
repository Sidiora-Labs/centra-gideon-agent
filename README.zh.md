# Gideon Agent

本仓库介绍开源自托管版本。Gideon 同时提供托管服务。

Gideon 是个人智能体，提供网页控制台、聊天、任务、自动化、知识库和受权限约束的应用。

本文是简明安装指南。完整且最新的说明请参阅英文 README 和下方链接。

## 开始使用

从源码构建需要 Python 3.12+、Node.js 22.12+ 和 Rust/Cargo 1.91.1。针对平台的 wheel 已包含 Hypermid 守护进程。网关可启动额外进程，并非所有功能都在单一进程内运行。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build
export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

首次启动后配置模型提供方。Hypermid 记忆是可选功能，需要配置。当前权限和审批仍约束工具使用。更新检查及集成可能向外部服务发送请求。

`GIDEON_HOME`: `.dev-home` / `~/.gideon`.

- [README (English)](README.md)
- [Setup](docs/guides/GETTING_STARTED.md)
- [Platforms](docs/guides/PLATFORMS.md)
- [Containers](docs/guides/CONTAINERS.md)
- [Desktop](docs/guides/DESKTOP.md)
- [Configuration](docs/reference/CONFIGURATION_REFERENCE.md)
- [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Apache License 2.0](LICENSE)
