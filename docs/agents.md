# Agent 预设指南

AmritaGO 的 Agent 预设使用 Markdown 文件表示，文件顶部可以有一段 YAML front matter，正文就是 Agent 的系统提示词。内置预设包括 `standard`、`planner` 和 `coder`；可在「设置 → Agent」查看和切换。

仓库自带的示例预设位于 `.amrita/agents/demo-project.md`。它是只读的项目导览助手，用来演示工作区级预设的结构。启动 AmritaGO 后，在 Agent 设置中选择「项目导览」即可使用。

## 添加预设

用户级预设存放在 `~/.amrita/agents/`，项目级预设放在 `<工作区>/.amrita/agents/`。两处均为每个 Agent 一个 `.md` 文件。项目级同名预设优先于用户级预设和内置预设；删除覆盖文件即可恢复被覆盖的内置预设。

```markdown
---
name: reviewer
title: 只读审查
description: 检查代码并给出具体、可验证的审查意见。
icon: search
tools:
  - file_read
  - file_list
  - file_search
access:
  - readonly
workflow: react
---
你是代码审查助手。先阅读相关实现和测试，再指出有证据支持的问题。
只报告可复现的缺陷；说明影响、触发条件和相关文件，不要修改文件。
```

保存文件后刷新或重新打开 Agent 设置。如果新建文件没有出现，可重启服务。

## Front matter 字段

| 字段 | 作用 |
| --- | --- |
| `name` | 稳定的预设标识；省略时使用文件名。 |
| `title` | 界面显示名称。 |
| `description` | 预设用途简介。 |
| `icon` | 界面图标名称。 |
| `tools` | 可选的工具名 glob 白名单；省略表示不额外限制，`[]` 表示不允许工具。 |
| `access` | 可选的访问模式上限：`readonly`、`workspace`、`full`。 |
| `workflow` | 固定工作流；可用 `chat`、`react`、`react-step` 或已注册的插件工作流。省略时跟随全局选择。 |
| `memory` | 设置为 `session` 时提供本会话临时记忆目录。 |
| `template` | 可选的 Jinja2 模板；通常省略并使用 AmritaCore 默认模板。 |

正文是系统提示词，不要在 front matter 后再加第二段 YAML。需要在正文单独写 `---` 时，将行首写成 `\---`。

`tools` 按工具名称匹配，可使用 `file_*` 等 glob。Skill 工具使用 Skill 名称本身（例如 `demo-project`），没有 `skill_` 前缀。名字拼错的规则不会生效；设置界面会显示当前匹配不到工具的规则。MCP 服务未连接时，相关规则也可能暂时无法匹配。

`access` 是预设允许使用的访问模式上限，不是安全沙箱。`readonly` 会移除 shell 工具；外部插件仍在当前进程权限下运行。Alpha 阶段请仅在可信环境使用 Agent 和工具。

## 示例预设说明

仓库中的 `demo-project` 只允许 `file_read`、`file_list`、`file_search` 和 `demo-project`，并限制为 `readonly`。它会浏览文件、解释项目结构，但不会执行命令或写文件。示例 Skill 位于 [.amrita/skills/demo-project/SKILL.md](../.amrita/skills/demo-project/SKILL.md)；发现、启用和调用细节见 [Skill 系统指南](skills.md)。
