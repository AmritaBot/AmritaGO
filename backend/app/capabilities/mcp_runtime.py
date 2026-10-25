"""MCP server registry and connection lifecycle.

MCP tools are registered into the process-wide ``ToolsManager`` by
``ClientManager``. Every session then clones that registry (see
``skills.build_skill_tools``), so a connected server's tools reach each new
session's tool pool without per-session reconnection.
"""

from __future__ import annotations

import asyncio
from typing import Any

from amrita_core.tools.mcp import ClientManager
from pydantic import BaseModel, Field

from app.core.config import PATHS
from app.core.storage import read_json, write_json

# ClientManager's class-level manager is the registry sessions clone from.


class MCPServer(BaseModel):
    name: str
    transport: str = Field(
        ...,
        description=(
            "MCP transport string: a local script path, "
            '`stdio://["cmd","arg"]`, `sse://host/path`, or an `http(s)://` URL.'
        ),
    )
    enabled: bool = True
    connected: bool = False
    error: str | None = None
    tools: list[str] = Field(default_factory=list)


class MCPRegistry(BaseModel):
    servers: list[MCPServer] = Field(default_factory=list)


class MCPRuntime:
    """Owns the single ``ClientManager`` used by this process."""

    def __init__(self) -> None:
        self._manager: ClientManager = ClientManager()
        self._lock = asyncio.Lock()
        self._servers: dict[str, MCPServer] = {}

    @property
    def manager(self) -> ClientManager:
        """The single ``ClientManager`` shared by every session."""
        return self._manager

    async def load(self) -> list[MCPServer]:
        raw = await read_json(PATHS.mcp, {"servers": []})
        registry = MCPRegistry.model_validate(raw or {"servers": []})
        self._servers = {server.name: server for server in registry.servers}
        for server in self._servers.values():
            server.connected = False
            server.tools = []
        return list(self._servers.values())

    async def persist(self) -> None:
        registry = MCPRegistry(servers=list(self._servers.values()))
        await write_json(PATHS.mcp, registry.model_dump())

    def list_servers(self) -> list[MCPServer]:
        return list(self._servers.values())

    def get(self, name: str) -> MCPServer | None:
        return self._servers.get(name)

    def connected_names(self) -> list[str]:
        return [s.name for s in self._servers.values() if s.connected]

    async def upsert(self, server: MCPServer) -> MCPServer:
        self._servers[server.name] = server
        await self.persist()
        return server

    async def remove(self, name: str) -> bool:
        server = self._servers.pop(name, None)
        if server is None:
            return False
        await self.disconnect(name)
        await self.persist()
        return True

    async def set_enabled(self, name: str, enabled: bool) -> MCPServer | None:
        server = self._servers.get(name)
        if server is None:
            return None
        server.enabled = enabled
        await self.persist()
        if enabled:
            await self.connect(name)
        else:
            await self.disconnect(name)
        return server

    async def connect(self, name: str) -> MCPServer | None:
        server = self._servers.get(name)
        if server is None:
            return None
        async with self._lock:
            try:
                await self._manager.initialize_this(server.transport, fail_then_raise=True)
                client = self._manager.script_to_clients.get(str(server.transport))
                server.connected = True
                server.error = None
                server.tools = (
                    [tool.function.name for tool in client.get_tools()]
                    if client is not None
                    else []
                )
            except Exception as exc:
                server.connected = False
                server.error = f"{type(exc).__name__}: {exc}"
                server.tools = []
        await self.persist()
        return server

    async def disconnect(self, name: str) -> MCPServer | None:
        server = self._servers.get(name)
        if server is None:
            return None
        async with self._lock:
            try:
                await self._manager.unregister_client(server.transport)
            except Exception as exc:
                server.error = f"{type(exc).__name__}: {exc}"
            server.connected = False
            server.tools = []
        await self.persist()
        return server

    async def connect_enabled(self) -> list[MCPServer]:
        results: list[MCPServer] = []
        for server in list(self._servers.values()):
            if server.enabled:
                updated = await self.connect(server.name)
                if updated is not None:
                    results.append(updated)
        return results

    async def shutdown(self) -> None:
        async with self._lock:
            for server in list(self._servers.values()):
                if server.connected:
                    try:
                        await self._manager.unregister_client(server.transport)
                    except Exception:
                        pass
                    server.connected = False
            for client in list(self._manager.clients):
                try:
                    await client.close_no_wait()
                except Exception:
                    pass
            self._manager.clients.clear()


RUNTIME = MCPRuntime()


def server_payload(server: MCPServer) -> dict[str, Any]:
    return server.model_dump()
