# AmritaGO

AmritaGO 是本地运行的 Agent 工作台，提供对话、工具调用、Agent 预设、MCP 和插件扩展。前端由 React 构建，后端基于 AmritaCore、FastAPI 和 WebSocket。

> [!WARNING]
> **当前处于 Alpha 阶段。** 功能、数据格式和接口仍在快速变化，存在较多不稳定因素和潜在安全缺陷，**不建议用于生产环境，也不要向公网开放**。在获得更多测试反馈、完善功能后，预计会进行较大范围的重构；届时配置格式、插件 API 和使用方式都可能变化。

## 开始使用

若已发布到 PyPI，可用 `uvx` 临时运行（首次运行会自动安装）：

```bash
uvx --from "amrita-go==0.1.0a1" amrita-go
```

Alpha 版本的发布状态可能变化；如果该版本尚未发布，请按[部署指南](docs/deployment.md)从源码运行。启动后默认访问 `http://127.0.0.1:8000`。

开发者快速启动（源码依赖需按 [部署指南](docs/deployment.md) 准备 AmritaCore 同级目录）：

```bash
cd backend
uv sync --all-extras
cd ../frontend
bun install
bun run build
cd ../backend
uv run python -m app
```

## 文档

- [文档目录](docs/README.md)
- [部署指南](docs/deployment.md)：`uvx`、源码启动和打包
- [配置指南](docs/configuration.md)：模型、数据目录、密钥和环境变量
- [Agent 预设指南](docs/agents.md)：内置预设、项目级/用户级预设和示例 Agent
- [Skill 系统](docs/skills.md)：Skill 目录结构、发现规则、启用与按需加载
- [插件指南](docs/plugins.md)：安装、编写与安全注意事项

## 反馈

提交问题前请确认使用的 AmritaGO 版本、操作系统和安装方式，并清除日志中的 API Key、令牌、个人路径等敏感信息。Alpha 阶段的配置和插件接口可能随版本变化。
