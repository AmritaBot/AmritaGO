"""Capability composition at the AmritaCore backend boundary.

Each run gets a fresh ``MultiToolsManager`` built from the active plugin
snapshot: presets filter tools by glob, MCP servers by glob, and skills by the
global switch. Nothing is registered into AmritaCore's global managers.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

from amrita_core.tools.manager import MultiToolsManager
from amrita_core.tools.mcp import MCPClient, MultiClientManager
from amrita_core.tools.models import ToolData
from amrita_sense.logging import logger

from app.capabilities.mcp_runtime import RUNTIME as MCP
from app.capabilities.permissions import ACCESS_MODES, narrow, tool_allowed_for_mode
from app.capabilities.skills import SkillInfo, discover_skills_sync
from app.plugins import PLUGINS, MCPDeclaration
from app.runtime.workflows import WORKFLOWS


@dataclass(frozen=True)
class MCPSource:
    name: str
    transport: str
    enabled: bool
    connected: bool
    tools: tuple[str, ...] = ()

    def to_public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "enabled": self.enabled,
            "connected": self.connected,
            "tools": list(self.tools),
        }


@dataclass(frozen=True)
class ToolBuildContext:
    settings: Any
    agent: Any
    skills: tuple[SkillInfo, ...] = ()
    mcp: tuple[MCPSource, ...] = ()
    access_mode: str = "workspace"
    #: Session the run belongs to, for sources that keep per-conversation state.
    session_id: str | None = None


@dataclass
class CapabilityCatalog:
    settings: Any
    agent: Any = None
    skills: tuple[SkillInfo, ...] = ()
    mcp: tuple[MCPSource, ...] = ()
    #: Effective access mode for this run (readonly / workspace / full).
    access_mode: str = "workspace"
    session_id: str | None = None
    _tools: MultiToolsManager | None = field(default=None, repr=False)

    @classmethod
    async def collect(
        cls,
        settings: Any,
        agent: Any = None,
        requested_access: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityCatalog:
        """Compose a catalog for one run.

        ``requested_access`` is what the client asked for; the preset narrows
        it. Resolving it here (rather than in each tool) means every capability
        — plugin tools, MCP tools, the shell — sees the same decision.
        """
        access_mode = narrow(
            getattr(agent, "access", None) if agent is not None else None,
            requested_access,
        )
        skills = tuple(await _discover_skills())
        declared = _mcp_declarations()
        sources = tuple(
            MCPSource(
                name=item.name,
                transport=item.transport,
                enabled=item.enabled,
                connected=item.connected,
                tools=item.tools,
            )
            for item in declared
        )
        return cls(
            settings=settings,
            agent=agent,
            skills=skills,
            mcp=sources,
            access_mode=access_mode,
            session_id=session_id,
        )

    def allowed_mcp(self) -> list[MCPSource]:
        settings = self.settings
        globally_enabled = set(getattr(settings, "enabled_mcp", []) or [])
        out: list[MCPSource] = []
        for source in self.mcp:
            if not source.enabled:
                continue
            if globally_enabled and source.name not in globally_enabled:
                continue
            out.append(source)
        return out

    def build_tools(self) -> MultiToolsManager:
        if self._tools is not None:
            return self._tools

        manager = MultiToolsManager()
        declared_mcp_names = {tool_name for source in self.mcp for tool_name in source.tools}
        context = ToolBuildContext(
            settings=self.settings,
            agent=self.agent,
            skills=self.skills,
            mcp=self.mcp,
            access_mode=self.access_mode,
            session_id=self.session_id,
        )
        for source in PLUGINS.snapshot.tool_sources:
            try:
                produced = source.build(context)
            except Exception as exc:
                logger.opt(exception=exc).error(
                    f"Tool source '{source.name}' failed; continuing without it"
                )
                continue
            for tool in produced:
                name = tool.data.function.name
                if source.name == "core" and name in declared_mcp_names:
                    continue
                if not tool_allowed_for_mode(name, self.access_mode):
                    continue
                # Switched off globally; the filter lives on the copy being built.
                if tool_disabled(self.settings, name):
                    continue
                if not manager.has_tool(name):
                    manager.register_tool(tool)

        for source in self.allowed_mcp():
            for tool in _mcp_tools(source):
                name = tool.data.function.name
                if not tool_allowed_for_mode(name, self.access_mode):
                    continue
                if tool_disabled(self.settings, name):
                    continue
                if not manager.has_tool(name):
                    manager.register_tool(tool)

        if self.agent is not None:
            for name in list(manager._models):
                if manager.has_tool(name) and not self.agent.tool_allowed(name):
                    manager.disable_tool(name)

        self._tools = manager
        return manager

    def build_mcp_view(self, tools: MultiToolsManager | None = None) -> MultiClientManager:
        manager = MultiClientManager(tools_manager=tools or self.build_tools())
        global_manager = MCP.manager
        for source in self.allowed_mcp():
            client = global_manager.script_to_clients.get(source.transport)
            if client is None:
                continue
            manager.clients.append(client)
            manager.script_to_clients[source.transport] = client
        clients = {id(client) for client in manager.clients}
        for name, owner in global_manager.name_to_clients.items():
            if id(owner) in clients:
                manager.name_to_clients[name] = owner
        for origin, remapped in global_manager.tools_remapping.items():
            manager.tools_remapping[origin] = remapped
        for remapped, origin in global_manager.reversed_remappings.items():
            manager.reversed_remappings[remapped] = origin
        return manager

    def tool_catalog(self) -> list[dict[str, Any]]:
        """Every tool any source can produce, tagged with its source.

        Unfiltered on purpose: the editor needs the full name list to write
        glob patterns against. :meth:`allowed_tools` is the effective set,
        ``enabled`` says whether the global switch keeps it, and
        ``preset_allowed`` says whether the active preset's whitelist does.
        """
        context = ToolBuildContext(
            settings=self.settings,
            agent=self.agent,
            skills=self.skills,
            mcp=self.mcp,
            access_mode=self.access_mode,
            session_id=self.session_id,
        )
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        declared_mcp_names = {tool_name for source in self.mcp for tool_name in source.tools}
        for source in PLUGINS.snapshot.tool_sources:
            try:
                produced = source.build(context)
            except Exception:
                continue
            for tool in produced:
                schema = tool.data.function
                if source.name == "core" and schema.name in declared_mcp_names:
                    continue
                if schema.name in seen:
                    continue
                seen.add(schema.name)
                out.append(self._describe_tool(schema.name, schema.description, source.name))
        for source in self.mcp:
            for name in source.tools:
                if name in seen:
                    continue
                seen.add(name)
                out.append(self._describe_tool(name, "", f"mcp:{source.name}"))
        out.sort(key=lambda item: item["name"])
        return out

    def _describe_tool(self, name: str, description: str, source: str) -> dict[str, Any]:
        agent = self.agent
        return {
            "name": name,
            "description": description,
            "source": source,
            # Switched off for every conversation (``settings.disabled_tools``).
            "enabled": not tool_disabled(self.settings, name),
            # The active preset's ``tools`` whitelist does not admit it.
            "preset_allowed": agent.tool_allowed(name) if agent is not None else True,
            "preset_pinned": bool(getattr(agent, "tools", None) is not None),
            # The current access mode alone would refuse it.
            "mode_allowed": tool_allowed_for_mode(name, self.access_mode),
        }

    def tool_tree(self) -> dict[str, Any]:
        """The tool pool grouped by where each tool comes from.

        One group per plugin tool source plus one per MCP server, because those
        are the only two things a tool can come from and the user asked to see
        which. Counts describe the effective pool, not the catalog.
        """
        catalog = self.tool_catalog()
        plugins = _source_plugin_map()
        by_name = {item["name"]: item for item in catalog}
        groups: list[dict[str, Any]] = []
        claimed: set[str] = set()

        for source in PLUGINS.snapshot.tool_sources:
            members = [
                item
                for item in catalog
                if item["source"] == source.name and item["name"] not in claimed
            ]
            if not members:
                continue
            claimed.update(item["name"] for item in members)
            plugin = plugins.get(source.name)
            label = _SOURCE_LABELS.get(source.name) or (
                plugin.name if plugin is not None else source.name
            )
            groups.append(
                {
                    "id": f"plugin:{source.name}",
                    "kind": "plugin",
                    "label": label,
                    "detail": (plugin.description if plugin is not None else source.description),
                    "plugin": plugin.name if plugin is not None else None,
                    "connected": True,
                    "tools": members,
                }
            )

        # MCP servers last: they are remote and may be offline.
        enabled_mcp = {source.name for source in self.allowed_mcp()}
        for source in self.mcp:
            members = [
                item
                for item in by_name.values()
                if item["source"] == f"mcp:{source.name}" and item["name"] not in claimed
            ]
            if not members:
                continue
            claimed.update(item["name"] for item in members)
            groups.append(
                {
                    "id": f"mcp:{source.name}",
                    "kind": "mcp",
                    "label": source.name,
                    "detail": source.transport,
                    "plugin": None,
                    "connected": source.connected and source.name in enabled_mcp,
                    "tools": members,
                }
            )

        for group in groups:
            group["tools"].sort(key=lambda item: item["name"])
            group["count"] = len(group["tools"])
            group["enabled_count"] = sum(1 for item in group["tools"] if item["enabled"])

        total = len(catalog)
        enabled = sum(1 for item in catalog if item["enabled"] and item["mode_allowed"])
        return {
            "groups": groups,
            "total": total,
            "enabled": enabled,
            "disabled": [item["name"] for item in catalog if not item["enabled"]],
            "disabled_patterns": disabled_tools(self.settings),
            "access_mode": self.access_mode,
            "agent": getattr(self.agent, "name", None),
            "preset_pinned": bool(getattr(self.agent, "tools", None) is not None),
        }

    def tool_count(self) -> int:
        return len(self.build_tools()._models)

    def allowed_tools(self) -> list[str]:
        """Tool names the active preset actually keeps enabled."""
        manager = self.build_tools()
        return sorted(name for name in manager._models if manager.has_tool(name))

    def effective_workflow(self) -> Any:
        """The workflow this run will execute: preset first, else the global one."""
        agent_workflow = getattr(self.agent, "workflow", None) if self.agent else None
        return WORKFLOWS.require(agent_workflow or getattr(self.settings, "active_workflow", None))

    def to_public(self) -> dict[str, Any]:
        workflow = self.effective_workflow()
        return {
            "tools": self.tool_catalog(),
            "allowed_tools": self.allowed_tools(),
            "mcp": [source.to_public() for source in self.mcp],
            "allowed_mcp": [source.name for source in self.allowed_mcp()],
            "access_mode": self.access_mode,
            "access_modes": list(ACCESS_MODES),
            "declared_access": getattr(self.agent, "access", None) if self.agent else None,
            "workflow": workflow.name,
            "workflow_mode": workflow.mode,
            "workflow_mode_label": workflow.mode_label,
            "workflow_pinned": bool(getattr(self.agent, "workflow", None) is not None),
            "workflows": [spec.to_public() for spec in WORKFLOWS.list()],
            "plugins": [info.to_public() for info in PLUGINS.snapshot.plugins],
        }


async def _discover_skills() -> list[SkillInfo]:
    import asyncio

    return await asyncio.to_thread(discover_skills_sync)


def _mcp_declarations() -> list[MCPDeclaration]:
    collected: dict[str, MCPDeclaration] = {}
    for provider in PLUGINS.snapshot.mcp_providers:
        try:
            for item in provider.discover():
                collected.setdefault(item.name, item)
        except Exception as exc:
            logger.opt(exception=exc).error(
                f"MCP provider '{provider.name}' failed to report servers"
            )
    return list(collected.values())


def _mcp_tools(source: MCPSource) -> list[ToolData]:
    client = MCP.manager.script_to_clients.get(source.transport)
    if client is None:
        return []
    schemas = {tool.function.name: tool for tool in client.get_tools()}
    out: list[ToolData] = []
    for name in source.tools:
        schema = schemas.get(name)
        if schema is None:
            continue
        out.append(ToolData(data=schema, func=_mcp_runner(client, name)))
    return out


def _mcp_runner(client: MCPClient, tool_name: str):
    async def invoke(data: dict[str, Any]) -> str:
        return await client.simple_call(tool_name, data)

    return invoke


def matches(name: str, patterns: list[str] | None) -> bool:
    if patterns is None:
        return True
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def disabled_tools(settings: Any) -> list[str]:
    return list(getattr(settings, "disabled_tools", None) or [])


def tool_disabled(settings: Any, name: str) -> bool:
    """Is this tool switched off for every conversation?

    Globs are accepted (``mcp_*``) because the catalog is derived from plugins
    and an MCP server can hand back dozens of tools with a common prefix.
    """
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in disabled_tools(settings))


#: Human labels for the tool sources that ship with the app.
_SOURCE_LABELS = {
    "core": "内置",
    "skills": "技能",
    "memory": "会话记忆",
    "files": "文件",
    "todos": "待办",
    "pwsh": "PowerShell",
    "unix": "Unix 终端",
}


def _source_plugin_map() -> dict[str, Any]:
    """tool source name -> the plugin that declared it (if any)."""
    out: dict[str, Any] = {}
    for plugin in PLUGINS.snapshot.plugins:
        for tool_source in plugin.tools:
            out.setdefault(tool_source, plugin)
    return out


async def catalog_for(
    settings: Any,
    agent: Any = None,
    requested_access: str | None = None,
    session_id: str | None = None,
) -> CapabilityCatalog:
    return await CapabilityCatalog.collect(
        settings, agent, requested_access=requested_access, session_id=session_id
    )


__all__ = [
    "CapabilityCatalog",
    "MCPSource",
    "ToolBuildContext",
    "catalog_for",
    "disabled_tools",
    "matches",
    "tool_disabled",
]
