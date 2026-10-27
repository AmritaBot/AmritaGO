"""Built-in plugins.

These are ordinary plugins: they declare tools, MCP providers and hooks during
activation and are unloaded by the same generator lifecycle as an external
plugin. Nothing here writes to AmritaCore's global tool manager.
"""

from __future__ import annotations

from typing import Any

from amrita_sense.logging import logger

from app.capabilities.mcp_runtime import RUNTIME as MCP
from app.capabilities.skills import core_tool_data, skill_tool_data
from app.plugins import (
    MCPDeclaration,
    MCPProvider,
    PluginContext,
    PluginRuntime,
    ToolSource,
)
from app.plugins.files import files_plugin, install_files_plugin
from app.plugins.shell import install_shell_plugins, pwsh_plugin, unix_plugin
from app.plugins.todos import install_todos_plugin
from app.runtime.events import PluginEvent
from app.runtime.session_memory import build_tools as build_memory_tools


def _build_core_tools(_context: Any):
    return core_tool_data()


def _build_skill_tools(context: Any):
    return skill_tool_data(context.settings.enabled_skills, list(context.skills))


def _build_memory_tools(context: Any):
    """Empty unless the preset asked for ``memory: session``."""
    return build_memory_tools(context)


def _discover_mcp() -> list[MCPDeclaration]:
    return [
        MCPDeclaration(
            name=server.name,
            transport=server.transport,
            enabled=server.enabled,
            connected=server.connected,
            tools=tuple(server.tools),
        )
        for server in MCP.list_servers()
    ]


async def core_tools_plugin(ctx: PluginContext):
    """Expose AmritaCore's built-in tool registry as a tool source."""
    ctx.tool(
        ToolSource(
            name="core",
            description="AmritaCore 内置工具",
            build=_build_core_tools,
        )
    )
    yield


async def skills_plugin(ctx: PluginContext):
    """Expose skills as one tool per skill, filtered by settings and preset."""
    ctx.tool(
        ToolSource(
            name="skills",
            description="工作区与用户目录中的 Skill",
            build=_build_skill_tools,
        )
    )
    yield


async def memory_plugin(ctx: PluginContext):
    """Per-session scratch directory, for presets that declare ``memory``."""
    ctx.tool(
        ToolSource(
            name="memory",
            description="会话临时记忆目录",
            build=_build_memory_tools,
        )
    )
    yield


async def mcp_plugin(ctx: PluginContext):
    """Expose connected MCP servers and their tools."""
    ctx.mcp(
        MCPProvider(
            name="mcp",
            description="已连接的 MCP 服务器",
            discover=_discover_mcp,
        )
    )
    yield


async def diagnostics_plugin(ctx: PluginContext):
    """Log run boundaries so a failed turn leaves a trace."""

    async def _on_run_end(event: PluginEvent) -> None:
        logger.debug(
            f"run_end session={event.session_id} workflow={event.workflow} error={event.error}"
        )

    async def _on_message_modify(event: PluginEvent) -> None:
        logger.debug(
            "message_modify "
            f"session={event.session_id} "
            f"removed={event.removed_entries} "
            f"{len(event.previous)}->{len(event.content)} chars"
        )

    ctx.on("run_end", _on_run_end)
    ctx.on("message_modify", _on_message_modify)
    yield


def install_builtin_plugins(runtime: PluginRuntime) -> None:
    runtime.register(
        core_tools_plugin,
        name="core-tools",
        description="AmritaCore 内置工具",
    )
    runtime.register(
        skills_plugin,
        name="skills",
        description="Skill 工具注入",
    )
    runtime.register(
        mcp_plugin,
        name="mcp",
        description="MCP 服务器与工具",
    )
    runtime.register(
        memory_plugin,
        name="memory",
        description="会话临时记忆目录",
    )
    runtime.register(
        diagnostics_plugin,
        name="diagnostics",
        description="运行边界日志",
    )
    install_files_plugin(runtime)
    install_todos_plugin(runtime)
    install_shell_plugins(runtime)


__all__ = [
    "core_tools_plugin",
    "diagnostics_plugin",
    "files_plugin",
    "install_builtin_plugins",
    "mcp_plugin",
    "memory_plugin",
    "pwsh_plugin",
    "skills_plugin",
    "unix_plugin",
]
