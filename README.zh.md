<p align="left"><img src="assets/gideon.png" alt="Gideon"></p>

<h1 align="center">Gideon Agent</h1>
<p align="center">
  <a href="https://github.com/Sidiora-Labs/centra-gideon-agent"><img src="https://img.shields.io/badge/Project-Centra%20AI-0A0A0A?style=flat-square" alt="Centra AI" /></a>
  <a href="https://github.com/Sidiora-Labs"><img src="https://img.shields.io/badge/Built%20by-Sidiora%20Labs-0A0A0A?style=flat-square" alt="Built by Sidiora Labs" /></a>
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/LICENSE-2-0A0A0A?style=flat-square" alt="Apache License Version 2.0" /></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/Version-0.1.3-0A0A0A?style=flat-square" alt="Version 0.1.3" /></a>
</p>
Gideon 是一个运行在你自己机器上的个人 AI agent。单个进程同时提供 Web 控制台并承担实际工作：聊天、长时间运行的目标循环、记忆、知识库、任务、定时计划、收件箱，以及一个受权限管控的应用平台。

它是为这样一个人打造的：希望 agent 能真正访问自己的计算机和自己的服务，而不必把钥匙交给托管产品。状态存放在你选定的目录中。模型提供方是可插拔的：Anthropic 或 OpenAI 密钥、OpenAI 兼容端点、AWS Bedrock 凭据，或本地运行的模型。

> **Pre-1.0：** Gideon 目前是 **v0.1.3**。它迭代很快，一次发布就可能破坏某些东西。升级前请先运行 `gideon snapshot`，并阅读 [CHANGELOG.md](CHANGELOG.md) 了解已发布的内容。

## 它能做什么

- **与它对话，或把工作交给它。** 聊天会话支持流式回复、工具调用、工件（artifacts），以及可搜索的对话记录。子 agent 接下任务并在后台汇报，你则可以继续做别的事。
- **让它无人值守地运行。** 循环（loops）按计划在多个轮次中推进一个目标。任务、触发器和流程把一次性请求变成可重复执行的东西。
- **给它记忆。** 分层记忆在多次对话之间保留偏好和上下文。知识库保存你指向它的文档，因此回答会引用你的材料，而不是开放的互联网。
- **让你始终知情。** 收件箱汇集需要你处理的事项，来源包括 Slack 等渠道，也包括 Gideon 本身。语音输入和语音回复是可选的附加功能。
- **扩展它。** 应用平台及其 Python SDK（`gideon.sdk`）覆盖模型、渠道、搜索、工具和仪表盘。技能、提示词和 MCP 服务器无需改动核心即可增加能力。
- **决定它可以碰什么。** 工具审批、按应用划分的权限、凭据处理、命令筛查和审计追踪。核心与提供方无关：集成始终位于应用中，绝不放在核心包里。
- **观察它的工作。** 控制台展示会话、活动、正在运行的循环、已排期的作业和健康状况，还有一个终端用于操作 Gideon 所运行的机器。

## 环境要求

- Python 3.12 或更高版本。
- Node.js 22.12 或更高版本并带有 npm，如果你想从源码构建控制台。CI 使用 Node 24 构建控制台。
- macOS 或 Linux。在 Windows 上，请使用 [docs/guides/CONTAINERS.md](docs/guides/CONTAINERS.md) 中的 Docker Compose 路径。
- 任何依赖模型的功能都需要一个模型提供方，在首次启动后配置。本地模型也可以。

没有外部数据库，也没有消息代理。一切都在同一个网关进程中运行。

## 从仓库检出运行

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build

export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

打开网关打印出的控制台地址。`npm run build` 会把控制台写入 `apps/console/dist`，网关直接从检出目录提供它。`gideon setup` 会询问工作区目录和时区。模型提供方随后在控制台中配置，因为全新的 home 还没有任何提供方应用可以存放凭据。

`GIDEON_HOME` 是存放配置、凭据、对话和其他运行时状态的目录。如果不设置它，Gideon 会使用 `~/.gideon`。在试验期间保留隔离的 `.dev-home` 取值是刻意的：它能让开发实例远离你真正的实例。用 Ctrl-C 停止前台网关。

如果改用打包安装，`sh infrastructure/website/install.sh` 会用 `uv` 完成引导。要在运行之前检查安装脚本的字节内容，请参见[验证一行命令](docs/guides/GETTING_STARTED.md#verify-the-one-liner)。从零安装到第一次聊天的完整演练见 [docs/guides/GETTING_STARTED.md](docs/guides/GETTING_STARTED.md)。

## 开发

```sh
.venv/bin/python -m pip install -e '.[test]'
sh tooling/scripts/install_git_hooks.sh
```

该钩子脚本会格式化已暂存的 Python 代码并为你的提交签名，这正是 CI 所检查的内容。`npm install` 不会安装钩子。

| 命令 | 作用 |
| --- | --- |
| `make format` | 使用 black 和 isort 格式化 Python |
| `make lint` | 对 runtime 和 checks 运行 black、isort、flake8 和 mypy |
| `make test` | 运行 Python 测试套件（`checks/runtime`） |
| `make serve` | 构建控制台并针对 `.dev-home` 启动网关 |
| `make serve-web` | 针对某个网关在 3100 端口运行控制台开发服务器 |
| `npm run typecheck:web` | 对控制台进行类型检查 |
| `npm run test:web` | 使用 Vitest 运行控制台测试 |
| `make test-e2e` | Chromium 交互检查 |
| `make docker-up` | 从 `infrastructure/compose` 启动容器栈 |

[CONTRIBUTING.md](CONTRIBUTING.md) 涵盖了协作约定、DCO 签名，以及变更如何分类和评审。

## 仓库结构导览

| 路径 | 内容 |
| --- | --- |
| `runtime/gideon/core` | 配置、共享资源、持久化辅助工具 |
| `runtime/gideon/engine` | Agent 执行与运行时协调 |
| `runtime/gideon/cognition` | 记忆、知识与上下文组装 |
| `runtime/gideon/automation` | 计划、触发器与流程 |
| `runtime/gideon/security` | 权限、凭据处理与筛查 |
| `runtime/gideon/integrations`, `runtime/gideon/extensions` | 提供方与应用集成 |
| `runtime/gideon/interfaces` | CLI、网关和控制台 API 接口 |
| `runtime/gideon/sdk` | 应用 SDK，以 `gideon.sdk` 导入 |
| `runtime/gideon/operations`, `runtime/gideon/assurance` | 自更新、备份与验证 |
| `apps/console` | React 控制台与共享的客户端资源 |
| `apps/desktop`, `apps/mobile` | Electron 与 Capacitor 外壳 |
| `packages/python-client` | 网关 API 的 Python 客户端 |
| `checks/runtime`, `checks/harness` | 行为检查与自我开发测试装置 |
| `docs` | 架构、指南、参考、安全与设计 |
| `tooling`, `infrastructure` | 开发脚本、打包、容器、网站 |
| `examples` | 一个应用模板和一个注册表示例，均不提供服务也不被安装 |

## 文档

[docs/README.md](docs/README.md) 是索引。快速入口：

- [docs/VISION.md](docs/VISION.md)：这个项目想成为什么。
- [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md)：网关是如何构成的。
- [docs/reference/CLI.md](docs/reference/CLI.md)：每一条命令和每一个标志。
- [docs/reference/CONFIGURATION_REFERENCE.md](docs/reference/CONFIGURATION_REFERENCE.md)：每一项设置。
- [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md)：信任边界究竟是什么。

## 状态

Gideon 处于 pre-1.0 阶段，正在积极开发中。网关、控制台、桌面和移动外壳、Python 客户端，以及检验它们的各项检查都在代码树中，CI 会在每次变更时运行 Python 测试套件、控制台测试和交互检查。

这不意味着：不存在托管服务，并且除非你把 `GIDEON_RELEASE_REPOSITORY` 指向某个地址，否则本仓库既不假定有已发布的包，也不假定有发布端点。集成需要各自的配置、凭据和平台支持。代码树中的某些能力尚未针对真实提供方做过端到端的验证。检查通过只能说明它们所覆盖的部分，对未覆盖的部分什么也说明不了。

## 安全

Gideon 会读取本地文件、运行工具并与你配置的服务通信，因此网关令牌以及它运行所用的账户都授予了真实的访问权限。漏洞报告请通过私密渠道发送给提供给你这份代码检出的人。参见 [SECURITY.md](SECURITY.md)。

Gideon 不发送任何遥测数据。除非你配置了会这样做的集成，否则任何关于你使用情况的信息都不会离开你的机器。

## 贡献与获取帮助

- [CONTRIBUTING.md](CONTRIBUTING.md)：环境搭建、命令、DCO 签名与评审。
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)：我们在此如何相处。
- [SUPPORT.md](SUPPORT.md)：去哪里提问，以及提问时应包含什么。
- [GOVERNANCE.md](GOVERNANCE.md)：谁决定什么。
- [Issues](https://github.com/Sidiora-Labs/centra-gideon-agent/issues) 用于报告缺陷和提出想法，[releases](https://github.com/Sidiora-Labs/centra-gideon-agent/releases) 展示已发布的内容，[安全政策](https://github.com/Sidiora-Labs/centra-gideon-agent/security/policy) 说明漏洞如何处理。仓库本身位于 [Sidiora-Labs/centra-gideon-agent](https://github.com/Sidiora-Labs/centra-gideon-agent)。

## 许可证

Apache License 2.0。参见 [LICENSE](LICENSE)。Copyright 2026 Sidiora Labs Inc.