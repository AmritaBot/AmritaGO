# 插件指南

> [!WARNING]
> 外部插件是普通 Python 代码，会在 AmritaGO 进程内以当前用户权限执行。它们不在沙箱中，可能读写本机文件、访问网络或读取进程可访问的数据。只安装和运行你信任、已审阅的插件；Alpha 阶段不要在生产或含敏感资料的环境中测试。

## 安装外部插件

将 Python 文件放入用户数据目录下的 `plugins/`：

```text
~/.amrita/plugins/hello.py
```

Windows 路径通常是 `%USERPROFILE%\.amrita\plugins\hello.py`。程序启动时会加载目录中非下划线开头的 `*.py` 文件。插件模块需要导出 `plugin(ctx)` 或兼容入口 `setup(ctx)`；可选用 `NAME` 和 `DESCRIPTION` 指定插件名称与说明。新增、删除或修改插件后重启 AmritaGO，或在「设置 -> 插件」里点「重载」。

**新插件默认不启用。** 启用状态保存在 `settings.json` 的 `enabled_plugins` 允许列表中；不在列表里的插件仍会出现在设置界面，但不会激活，也不会提供工具、工作流或钩子。全新安装时只有随应用发布的内置插件默认启用；从旧版本升级且已有 `settings.json` 时，现有插件保持原来的启用状态。此后新增的插件（包括新放入目录的外部插件）都需要手动打开。`core-tools`、`files`、`todos` 是固定启用的保护插件，不能关闭。

```python
NAME = "hello"
DESCRIPTION = "示例插件"


async def plugin(ctx):
    # 在此声明配置、工具、工作流或事件钩子。
    yield
    # 可在卸载时释放插件持有的资源。
```

`plugin` 是生命周期生成器：`yield` 前执行启动和声明逻辑，`yield` 后执行清理。插件可能在会话运行期间被重复激活和卸载，不要在模块导入或激活时制造无法清理的全局副作用。

## 声明配置

插件可以使用 Pydantic 模型声明设置。应用会据此生成设置表单，并将配置存入 `~/.amrita/plugin_configs/<插件名>.json`。

```python
from pydantic import BaseModel, Field


class HelloConfig(BaseModel):
    prefix: str = Field(
        default="你好，",
        title="问候前缀",
        description="问候语开头",
    )


async def plugin(ctx):
    ctx.config(HelloConfig)
    yield
```

插件运行时通过 `await load_config("hello", HelloConfig)` 读取经过校验的配置，不要自行读写 `plugin_configs` 文件。`"hello"` 应与插件名一致（默认是文件名，不含 `.py`）。

## 注册工具

工具通过 `ToolSource` 延迟创建；构建函数返回 AmritaCore 的 `ToolData`。以下示例注册一个读取配置并返回问候语的工具：

```python
from typing import Any

from amrita_core.tools.models import (
    FunctionDefinitionSchema,
    FunctionParametersSchema,
    FunctionPropertySchema,
    ToolData,
    ToolFunctionSchema,
)
from app.plugins import ToolSource, load_config
from pydantic import BaseModel, Field

NAME = "hello"


class HelloConfig(BaseModel):
    prefix: str = Field(default="你好，", title="问候前缀")


def build_tools(_context: Any) -> list[ToolData]:
    async def greet(ctx: Any) -> str:
        config = await load_config(NAME, HelloConfig)
        return f"{config.prefix}{ctx.data['name']}"

    schema = ToolFunctionSchema(
        type="function",
        strict=True,
        function=FunctionDefinitionSchema(
            name="hello_greet",
            description="用配置的前缀问候一个人",
            parameters=FunctionParametersSchema(
                type="object",
                properties={
                    "name": FunctionPropertySchema(type="string", description="姓名"),
                },
                required=["name"],
            ),
        ),
    )
    return [ToolData(data=schema, func=greet, custom_run=True)]


async def plugin(ctx):
    ctx.config(HelloConfig)
    ctx.tool(ToolSource(name=NAME, description="问候工具", build=build_tools))
    yield
```

工具名应清晰且唯一；参数要有明确类型和描述。对用户输入进行校验，限定文件与网络访问范围，并显式返回失败信息。工具的安全性不能仅依赖 Agent 的提示词或访问模式。

`custom_run=True` 的工具收到 `ToolRunContext`：`data` 是本轮模型参数，`ctx` 是 AmritaCore 的策略上下文，`session_id` 与 `access_mode` 是本轮会话和生效的访问模式，由运行时注入。按会话保存状态、向会话流发布事件（例如人工确认）时用 `session_id`，不要自己猜。

## 其他声明接口

`PluginContext` 在激活期间提供以下声明方法：

- `ctx.tool(ToolSource(...))`：添加工具来源。
- `ctx.config(PydanticModel)`：声明插件配置。
- `ctx.on(event, handler)`：订阅运行事件；当前常见事件为 `run_start`、`run_end`。
- `ctx.workflow(WorkflowSpec(...))`：注册工作流。
- `ctx.command(CommandSpec(...))`：注册斜杠指令，随插件激活注册、停用失效。
- `ctx.mcp(MCPProvider(...))`：向工具池报告 MCP 来源。
- `ctx.apply(plugin, name=..., description=...)`：注册另一个插件，需重新加载后激活。

插件禁用时会跳过激活，其工具、工作流及钩子不会加入当前工具池。`core-tools`、`files` 和 `todos` 是固定启用的核心插件，不能从界面禁用。

### 斜杠指令示例

```python
from app.runtime.commands import CommandSpec


async def hello_plugin(ctx):
    async def hello(command_ctx):
        # ``say`` 输出正文，``emit`` 输出一条 trace 步骤；两者都进会话历史。
        await command_ctx.emit("插件指令被调用", {"type": "system", "extra_type": None})
        await command_ctx.say(f"你好，{command_ctx.args or '陌生人'}")
        return "已问候"  # 返回值作为本轮助手正文持久化

    ctx.command(
        CommandSpec(
            name="hello",
            title="打招呼",
            description="回一句问候，参数作为名字。",
            handler=hello,
            usage="/hello <名字>",
        )
    )
    yield
```

指令在 `activate()` 时进入注册表，在 `deactivate()` 时随快照一起消失；名字与内置指令或已注册指令冲突会直接报错，而不是静默覆盖。

启用设置中的开发者模式（或设置 `AMRITA_GO_DEBUG=1`）可检查插件重复激活时声明结果是否一致；这不能代替代码审计或安全测试。
