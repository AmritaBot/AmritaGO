# 配置指南

> [!NOTE]
> 当前是 Alpha 版本。设置界面和配置文件格式可能在后续版本调整；手工编辑配置前先备份数据目录。

## 首次配置模型

启动后打开「设置 → 模型」：

1. 新增 Provider，填写名称、Base URL、协议和 API Key。
2. 使用「嗅探模型」读取服务端提供的模型列表；也可手动添加模型 ID。
3. 为模型按需设置上下文长度、最大输出、是否支持图片、温度和计价。
4. 选择当前使用的模型。

Provider 保存连接信息，一个 Provider 下可配置多个模型。当前支持的协议以界面可选项为准；OpenAI 兼容服务通常使用 `openai` 协议，Anthropic 服务使用 `anthropic` 协议。不同服务的 Base URL 格式可能不同，请以服务商文档为准。

## 数据目录

默认用户数据目录为 `~/.amrita`（Windows 下为用户主目录中的 `.amrita`），可通过 `AMRITA_GO_DATA` 修改。主要内容包括：

| 路径 | 用途 |
| --- | --- |
| `settings.json` | 当前模型、Agent、工作流、工作区及启用状态 |
| `providers/registry.json` | Provider 与模型信息，不包含明文 API Key |
| `providers/<id>/credentials.enc` | 加密保存的 Provider 凭据 |
| `secret.key` | 本地凭据加密密钥（若使用文件存储） |
| `plugin_configs/` | 插件声明的配置 |
| `plugins/*.py` | 用户外部插件 |
| `mcp.json` | MCP 服务配置 |
| `agents/`、`skills/` | 用户级 Agent 预设和 Skill |
| `sessions/` | 会话、历史、用量和附件等数据 |

不要公开或提交整个数据目录。备份加密凭据时，必须同时保管对应的加密密钥（或原系统钥匙串条目）；丢失密钥后，已保存的 API Key 无法恢复。

## 工作区目录

`AMRITA_GO_WORKSPACE` 指向工作区的 `.amrita` 配置目录。未设置时，源码运行使用仓库内 `.amrita`。工作区可保存项目级 Agent 预设和 Skill；用户级配置放在数据目录中。

```text
<工作区>/.amrita/
├─ agents/*.md
└─ skills/<技能名>/SKILL.md
```

工作区也可以在应用中切换或登记。文件工具的工作范围取决于界面当前选择的工作区及访问模式。

Skill 发现使用 `AMRITA_GO_WORKSPACE` 对应的工作区 Skill 目录及 `AMRITA_GO_DATA/skills` 用户目录。Skill 发现目录、同名覆盖和按需调用方式见 [Skill 系统指南](skills.md)。注意：当前实现的 Skill 工作区目录由 `AMRITA_GO_WORKSPACE` 确定。

## 凭据与密钥

Provider API Key 以 Fernet 加密后写入凭据文件，密钥来源按程序策略选择：显式环境变量、已有系统钥匙串密钥或本地 `secret.key`。Linux 桌面环境可使用系统钥匙串；无可用钥匙串时使用文件存储。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `AMRITA_GO_DATA` | `~/.amrita` | 用户数据目录 |
| `AMRITA_GO_WORKSPACE` | 源码运行时为仓库 `.amrita` | 工作区配置目录 |
| `AMRITA_GO_FRONTEND` | 源码运行时为 `frontend/dist`；安装包使用内置前端 | 覆盖前端产物目录 |
| `AMRITA_GO_HOST` | `127.0.0.1` | 监听地址 |
| `AMRITA_GO_PORT` | `8000` | 监听端口 |
| `AMRITA_GO_SECRET` | 未设置 | 指定 Fernet 密钥（Base64 编码的 32 字节密钥）；使用时自行安全保管，不写入仓库 |
| `AMRITA_GO_NO_KEYRING` | 未设置 | 设为 `1` 时禁用系统钥匙串，使用本地密钥文件 |
| `AMRITA_GO_DEBUG` | 未设置 | 设为 `1` 时启用插件生命周期一致性检查 |

不要在 Issue、聊天、截图或日志中粘贴 API Key、`AMRITA_GO_SECRET`、`secret.key` 内容。密钥轮换会使原有加密凭据无法解密，轮换前请备份并准备重新录入 Provider 凭据。

## 插件和 MCP 设置

- 在「设置 → 插件」启用或禁用可切换插件，并编辑插件自行声明的配置。启停状态持久化在 `settings.json` 的 `enabled_plugins`；新插件默认不启用，不在列表中的插件不会被激活。
- MCP 服务定义保存在数据目录的 `mcp.json`，连接状态和工具列表可在设置界面查看。
- `core-tools`、`files`、`todos` 等核心插件是固定启用的。
- 只读访问模式不提供 shell 工具；`workspace` 和 `full` 的具体边界请在使用前确认。Alpha 阶段不要把访问模式当作安全沙箱。

Agent 预设详情见 [Agent 预设指南](agents.md)，插件配置详情见[插件指南](plugins.md)。
