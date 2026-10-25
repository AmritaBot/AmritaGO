# 配置指南

> [!NOTE]
> 当前是 Alpha 版本。设置界面和配置文件格式可能在后续版本调整；手工编辑配置前先备份数据目录。

## 首次配置模型

启动后打开「设置 -> 模型」：

1. 新增 Provider，填写名称、Base URL、协议和 API Key。
2. 使用「嗅探模型」读取服务端提供的模型列表；也可手动添加模型 ID。
3. 为模型按需设置上下文长度、最大输出、是否支持图片、温度和计价。
4. 选择当前使用的模型。

Provider 保存连接信息，一个 Provider 下可配置多个模型。当前支持的协议以界面可选项为准；OpenAI 兼容服务通常使用 `openai` 协议，Anthropic 服务使用 `anthropic` 协议。不同服务的 Base URL 格式可能不同，请以服务商文档为准。

### 思考强度（thinking_effort）

输入框右下角的模型选择器除了切换模型，还能直接改当前模型的**思考强度**：下拉里选 `minimal`、`low`、`medium`、`high`、`xhigh` 或 `max`，会作为 `reasoning_effort` 随每次请求发给服务商；选「默认」则不覆盖核心配置（开启「思考」的模型按 `high` 发送，其余模型不发送该参数）。

- 只对当前模型生效，不需要断开正在进行的对话。
- 各家支持的值不同，服务商不支持时会忽略这个参数；报错就换回「默认」。
- 修改保存在 `providers/registry.json` 的模型条目里，与在「设置 -> 模型」里编辑是同一份数据。

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

工作区也可以在应用中切换或登记。文件与 shell 工具的工作范围取决于界面当前选择的工作区及访问模式；未选择过工作区时使用项目根目录（源码运行时即仓库根），不会退回到启动进程的当前目录。

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

- 在「设置 -> 插件」启用或禁用可切换插件，并编辑插件自行声明的配置。启停状态持久化在 `settings.json` 的 `enabled_plugins`；新插件默认不启用，不在列表中的插件不会被激活。
- MCP 服务定义保存在数据目录的 `mcp.json`，连接状态和工具列表可在设置界面查看。
- `core-tools`、`files`、`todos` 等核心插件是固定启用的。
- 只读访问模式不提供 shell 工具；`workspace` 和 `full` 的具体边界请在使用前确认。Alpha 阶段不要把访问模式当作安全沙箱。

Agent 预设详情见 [Agent 预设指南](agents.md)，插件配置详情见[插件指南](plugins.md)。

## 工具预算与继续迭代

**轮**指你和 Agent 的一问一答（你主动发一次消息就是一轮）；一轮里模型每发一次工具的请求算**一次 LLM 请求**。工具预算 `agent_tool_call_limit` 按 LLM 请求次数计算，与该次请求里要调几个工具无关。用完之后怎么处理在「设置 -> 行为」里选：

| 选项 | 行为 |
| --- | --- |
| 优雅停机 | 本轮结束。模型还能再发 `收尾请求数` 次请求，但每次请求都会被拒绝并附上原因，让它把已有结论说完。 |
| 挂起并询问 | 先停住并弹出询问：`继续迭代`、`优雅收尾`、`直接中断`。超时、关闭页面或连接断开按优雅收尾处理。 |

「继续迭代」是**在本轮内追加请求数**，不是新开一轮：

- 预算上限变成「已用请求数 + 追加请求数」（追加请求数默认 5，可在设置里改到 1~100），模型带着现有上下文接着做，不重放历史、不新建会话。
- 追加后工具照常执行；`收尾请求数` 只在用完之后选择「优雅收尾」时生效，对应 `agent_tool_refusal_rounds`。
- 每达到一次新的上限都会再问一次，能继续多少次由用户决定，服务端不设次数上限。

## 手动压缩上下文

输入框里用 `/compact` 可以把较早的消息折叠成摘要（复用与自动压缩相同的实现），折叠后保留最近的完整回合。它只在两轮之间可用：本轮还在跑时接口会拒绝，因为运行中的会话持有自己的上下文副本，写入会被覆盖。没有可折叠的历史、或模型没有产出摘要时，界面会提示原因且不改动历史。

## Shell 与 WSL

`unix` shell 插件支持原生 bash、zsh、Git Bash 和 WSL。可在「设置 -> 插件 -> shell-unix」里配置：

| 选项 | 说明 |
| --- | --- |
| `flavour` | `auto` 优先原生 bash/zsh，没有原生 shell 时回退 WSL；也可固定为 `bash`、`zsh` 或 `wsl`。 |
| `wsl_distro` | WSL 发行版名称；留空使用默认发行版。可用 `wsl -l -q` 查看名称（如 `Ubuntu`）。 |
| `executable` | 固定解释器路径。选 WSL 时可留空，由插件使用 `wsl.exe`。 |
| `init_commands` | 每条命令前执行的初始化脚本，例如 `. ~/.bashrc` 或 `cd /workspace`。 |

WSL 模式下，插件自动把 Windows 工作区路径翻译为 `/mnt/<盘符>/...`，并通过 `wsl.exe [ -d <发行版> ] --exec bash` 执行命令。切换发行版会使用独立会话，不与默认发行版的当前目录混用。`auto` 只有在没有原生 bash/zsh 时才选择 WSL，避免把命令意外跑进 Linux 文件系统。

注意：

- WSL 发行版列表在启动时探测并缓存；安装新发行版后重启 AmritaGO，设置页才会出现。
- 工作区路径必须能被 WSL 访问；位于 `\\wsl$` 或其他 Windows 无法映射到 `wsl.exe` 的工作目录可能无法启动。
- 在 WSL 内 `cd /tmp` 等 Linux 路径会被保留为下一次命令的起点；这是有意行为，不是工作区限制的绕过方式，但请避免在需要工作区隔离的任务中使用。
- `executable` 指向 Git Bash 时仍按 MSYS 路径 `/c/...` 处理，不会误加 WSL 的 `--exec` 参数。
