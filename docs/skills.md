# Skill 系统

Skill 是一组按需提供给 Agent 的任务说明，格式与 Anthropic Agent Skills 兼容。AmritaGO 使用 [faskill](https://pypi.org/project/faskill/) 完成发现、front matter 校验、内容缓存和调用处理，不再自行解析 Markdown。每个 Skill 是一个目录，入口文件固定命名为 `SKILL.md`。

> [!NOTE]
> Skill 是提供给模型的指令，不是可执行插件，也不是权限或安全边界。Skill 正文和随附文件都应当视为可信输入；不要在其中存放密钥、个人数据或不应暴露给模型的内容。

## 发现位置与优先级

AmritaGO 把两个 Skill 目录交给 faskill：

| 级别 | 默认目录 | 用途 |
| --- | --- | --- |
| 工作区 | `<仓库>/.amrita/skills/` 或 `$AMRITA_GO_WORKSPACE/skills/` | 随项目共享的项目知识 |
| 用户 | `~/.amrita/skills/` 或 `$AMRITA_GO_DATA/skills/` | 用户自己的通用 Skill |

工作区目录优先。两个目录存在同名 Skill 时，faskill 保留工作区版本，用户级同名项不会出现在列表或工具池中。

```text
<工作区>/.amrita/skills/
└─ demo-project/
   ├─ SKILL.md
   └─ references/
      └─ conventions.md
```

仓库内的示例位于 [.amrita/skills/demo-project/SKILL.md](../.amrita/skills/demo-project/SKILL.md)，配套的只读 Agent 在 [.amrita/agents/demo-project.md](../.amrita/agents/demo-project.md)。

## `SKILL.md` 格式

`SKILL.md` 由标准 YAML front matter 和正文组成，由 faskill 解析和校验：

```markdown
---
name: demo-project
description: 帮助 Agent 理解本项目的目录结构和代码约定。
version: "1.0"
---

# 项目导览

根据用户的问题阅读相关项目文件，再解释目录职责和实现方式。
用户补充的任务内容：$ARGUMENTS
```

- `name` 是 Skill 的唯一标识，也是工具名。
- `description` 会显示在设置界面并写入系统提示词。
- `version` 可选，只用于展示；请写成字符串（例如 `"1.0"`），数字形式会被 faskill 丢弃。
- front matter 不合法、缺少必填字段或 YAML 语法错误时，faskill 会记录错误并跳过该 Skill，不阻断应用启动。
- 不要把 API Key、密码等敏感值写进 Skill；项目级 Skill 通常会进入 Git 历史。

## 启用与调用流程

1. 在「设置 → 技能」查看已发现的 Skill 及其工作区/用户来源，并切换启用状态。
2. 启用的 Skill 注册为同名工具（例如 `demo-project`），名称和简介同时写入系统提示词。关闭的 Skill 不注册工具，也不出现在提示词中。
3. 模型判断任务匹配时调用该工具，并传入 `arguments` 字符串。
4. AmritaGO 调用 faskill 的 `invoke_skill`，返回处理后的正文：
   - 结果以 `Base directory for this skill: <目录>` 开头，便于模型引用同目录文件；
   - 每处 `$ARGUMENTS` 替换为传入的参数字符串；
   - 若正文没有 `$ARGUMENTS` 且参数非空，faskill 在末尾追加 `ARGUMENTS: <参数>`；
   - 参数超过 1 MB 会被 faskill 拒绝。

同一轮内的 Skill 调用通过 `custom_run` 工具执行，并在结果进入历史前推送一条 `skill_call` 元信息，界面显示「使用了技能：<名称>」。

`enabled_skills` 保存在用户数据目录的 `settings.json`：

- `null`（默认）：全部已发现的 Skill 启用。
- 字符串列表：只启用列表中同名 Skill。
- 空列表 `[]`：关闭全部 Skill。

设置界面第一次修改开关时，会把默认的 `null` 展开为当前发现的 Skill 名称列表。直接编辑 `settings.json` 时请填写 Skill 的 `name`。

发现结果会按目录中 `SKILL.md` 的修改时间做轻量刷新：新增、删除或修改 Skill 后，重新打开技能设置即可看到变化；正在运行的轮次不会中途重建工具池。

## 与 AmritaBot 的关系

该实现沿用 AmritaBot 的 Faskill 集成方式，但适配了 AmritaGO 的运行模型：

- 使用 `faskill.create_context` / `discover` / `list_skills` / `invoke_skill`，不再手写 front matter 或 `$ARGUMENTS` 解析；
- Skill 工具名就是 Skill 名，与 faskill 和 AmritaBot 一致；
- 启用过滤、系统提示词拼装和 `skill_call` 元信息由 AmritaGO 自己的能力层负责；
- 脚本级渐进披露（Skill 内脚本工具）尚未接入；当前调用只返回 Skill 正文及目录上下文，不会自动执行 Skill 目录中的脚本。
*** End Patch
