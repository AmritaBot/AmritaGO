# 插件指南

> [!WARNING]
> 外部插件是普通 Python 代码，会在 AmritaGO 进程内以当前用户权限执行。它们不在沙箱中，可能读写本机文件、访问网络或读取进程可访问的数据。只安装和运行你信任、已审阅的插件；Alpha 阶段不要在生产或含敏感资料的环境中测试。

## 安装外部插件

将 Python 文件放入用户数据目录下的 `plugins/`：

```text
~/.amrita/plugins/hello.py
```

Windows 路径通常是 `%USERPROFILE%\.amrita\plugins\hello.py`。程序启动时会加载目录中非下划线开头的 `*.py` 文件。插件模块需要导出 `plugin(ctx)` 或兼容入口 `setup(ctx)`；可选用 `NAME` 和 `DESCRIPTION` 指定插件名称与说明。新增、删除或修改插件后重启 AmritaGO。

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

## 其他声明接口

`PluginContext` 在激活期间提供以下声明方法：

- `ctx.tool(ToolSource(...))`：添加工具来源。
- `ctx.config(PydanticModel)`：声明插件配置。
- `ctx.on(event, handler)`：订阅运行事件；当前常见事件为 `run_start`、`run_end`。
- `ctx.workflow(WorkflowSpec(...))`：注册工作流。
- `ctx.mcp(MCPProvider(...))`：向工具池报告 MCP 来源。
- `ctx.apply(plugin, name=..., description=...)`：注册另一个插件，需重新加载后激活。

插件禁用时会跳过激活，其工具、工作流及钩子不会加入当前工具池。`core-tools`、`files` 和 `todos` 是固定启用的核心插件，不能从界面禁用。

启用设置中的开发者模式（或设置 `AMRITA_GO_DEBUG=1`）可检查插件重复激活时声明结果是否一致；这不能代替代码审计或安全测试。
