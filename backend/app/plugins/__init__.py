"""Plugin runtime with generator-based lifecycles.

Plugins extend the agent without touching process-wide registries. A plugin is
an ordinary callable that receives a :class:`PluginContext`, declares what it
contributes, and yields once to mark the end of setup::

    async def notes_plugin(ctx: PluginContext):
        ctx.tool(ToolSource(name="notes", build=build_note_tools))
        ctx.on("run_end", log_usage)

        yield

        release_notes_cache()

Registration (``context.apply``) only records the plugin. The agent decides
when to run it, and each activation collects declarations into a fresh,
immutable :class:`PluginSnapshot`; nothing is written to a global registry by
the plugin itself. Unloading runs the code after ``yield``. In developer mode
the runtime deliberately loads, unloads and loads again, comparing the two
snapshots so an activation that leaks state is reported immediately.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import os
import sys
from collections.abc import Awaitable, Callable, Generator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from amrita_core.tools.models import ToolData
from amrita_sense.logging import debug_log, logger
from pydantic import BaseModel

from app.core.config import PATHS
from app.runtime.workflows import BUILTIN_WORKFLOWS, WORKFLOWS, WorkflowSpec

HookHandler = Callable[[Mapping[str, Any]], Awaitable[Any] | Any]
BuildTools = Callable[[Any], list[ToolData]]


class PluginError(RuntimeError):
    """Base class for plugin lifecycle failures."""


class PluginRegistrationError(PluginError):
    """Raised when two plugins declare the same workflow."""


@dataclass(frozen=True)
class ToolSource:
    """Lazy factory for a group of tools.

    ``build`` receives a capability context owned by the runtime layer. A
    provider never receives the global tool manager, so it cannot mutate it.
    """

    name: str
    description: str
    build: BuildTools


@dataclass(frozen=True)
class MCPDeclaration:
    """What one MCP server currently offers, without exposing its client."""

    name: str
    transport: str
    enabled: bool
    connected: bool
    tools: tuple[str, ...]


@dataclass(frozen=True)
class MCPProvider:
    name: str
    description: str
    discover: Callable[[], list[MCPDeclaration]]


@dataclass(frozen=True)
class PluginInfo:
    name: str
    description: str
    module: str
    tools: tuple[str, ...] = ()
    workflows: tuple[str, ...] = ()
    mcp: tuple[str, ...] = ()
    hooks: tuple[str, ...] = ()
    #: Name of the Pydantic model the plugin declared, if any.
    config_name: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "module": self.module,
            "tools": list(self.tools),
            "workflows": list(self.workflows),
            "mcp": list(self.mcp),
            "hooks": list(self.hooks),
            "has_config": self.config_name is not None,
            "config_name": self.config_name,
        }


@dataclass(frozen=True)
class PluginSnapshot:
    plugins: tuple[PluginInfo, ...] = ()
    tool_sources: tuple[ToolSource, ...] = ()
    mcp_providers: tuple[MCPProvider, ...] = ()
    workflows: Mapping[str, WorkflowSpec] = field(default_factory=lambda: MappingProxyType({}))
    hooks: Mapping[str, tuple[HookHandler, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    #: plugin name → the model its settings validate against.
    configs: Mapping[str, type[BaseModel]] = field(default_factory=lambda: MappingProxyType({}))

    def signature(self) -> tuple[Any, ...]:
        hooks = tuple(sorted((event, len(handlers)) for event, handlers in self.hooks.items()))
        return (
            tuple((p.name, p.tools, p.workflows, p.mcp) for p in self.plugins),
            tuple((t.name, t.description) for t in self.tool_sources),
            tuple((m.name, m.description) for m in self.mcp_providers),
            tuple(
                (spec.name, spec.source)
                for spec in sorted(self.workflows.values(), key=lambda item: item.name)
            ),
            hooks,
            tuple(sorted(self.configs)),
        )

    def to_public(self) -> dict[str, Any]:
        return {
            "plugins": [info.to_public() for info in self.plugins],
            "tools": [
                {"name": source.name, "description": source.description}
                for source in self.tool_sources
            ],
            "mcp": [
                {"name": provider.name, "description": provider.description}
                for provider in self.mcp_providers
            ],
            "workflows": [
                spec.to_public()
                for spec in sorted(self.workflows.values(), key=lambda item: item.name)
            ],
            "hooks": sorted(self.hooks),
        }


EMPTY_SNAPSHOT = PluginSnapshot()

#: Plugins the app depends on, so the UI cannot switch them off.
PROTECTED_PLUGINS: frozenset[str] = frozenset({"core-tools", "files", "todos"})


@dataclass
class _PluginSpec:
    plugin: Callable[..., Any]
    name: str
    description: str
    module: str


class _Activation:
    """Declarations gathered during one load pass."""

    def __init__(self) -> None:
        self.tool_sources: list[ToolSource] = []
        self.mcp_providers: list[MCPProvider] = []
        self.workflows: dict[str, WorkflowSpec] = {}
        self.hooks: dict[str, list[HookHandler]] = {}
        self.configs: dict[str, type[BaseModel]] = {}
        self.infos: list[PluginInfo] = []
        self._current: list[str] = []
        self._tools: list[str] = []
        self._workflows: list[str] = []
        self._mcp: list[str] = []
        self._hooks: list[str] = []
        self._config: str | None = None

    def begin(self, spec: _PluginSpec) -> None:
        self._current = [spec.name, spec.description, spec.module]
        self._tools = []
        self._workflows = []
        self._mcp = []
        self._hooks = []
        self._config = None

    def add_tool(self, source: ToolSource) -> None:
        self.tool_sources.append(source)
        self._tools.append(source.name)

    def add_mcp(self, provider: MCPProvider) -> None:
        self.mcp_providers.append(provider)
        self._mcp.append(provider.name)

    def add_workflow(self, spec: WorkflowSpec) -> None:
        if spec.name in BUILTIN_WORKFLOWS or spec.name in self.workflows:
            raise PluginRegistrationError(f"workflow '{spec.name}' is already registered")
        self.workflows[spec.name] = spec
        self._workflows.append(spec.name)

    def add_hook(self, event: str, handler: HookHandler) -> None:
        self.hooks.setdefault(event, []).append(handler)
        self._hooks.append(event)

    def set_config(self, name: str, model: type[BaseModel]) -> None:
        """Register a config model under the current plugin's name."""
        if not self._current:
            raise PluginError("a config must be declared from inside a plugin")
        if name in self.configs:
            raise PluginRegistrationError(f"config for '{name}' is already registered")
        self.configs[name] = model
        self._config = name

    def end(self) -> None:
        if not self._current:
            return
        name, description, module = self._current
        self.infos.append(
            PluginInfo(
                name=name,
                description=description,
                module=module,
                tools=tuple(self._tools),
                workflows=tuple(self._workflows),
                mcp=tuple(self._mcp),
                hooks=tuple(sorted(set(self._hooks))),
                config_name=self._config,
            )
        )
        self._current = []

    def snapshot(self) -> PluginSnapshot:
        return PluginSnapshot(
            plugins=tuple(self.infos),
            tool_sources=tuple(self.tool_sources),
            mcp_providers=tuple(self.mcp_providers),
            workflows=MappingProxyType(dict(self.workflows)),
            hooks=MappingProxyType(
                {event: tuple(handlers) for event, handlers in self.hooks.items()}
            ),
            configs=MappingProxyType(dict(self.configs)),
        )


class _Handle:
    async def close(self) -> None:
        return None


class _NullHandle(_Handle):
    pass


class _GeneratorHandle(_Handle):
    def __init__(self, generator: Generator[Any, Any, Any]) -> None:
        self._generator = generator

    async def close(self) -> None:
        try:
            next(self._generator)
        except StopIteration:
            return
        else:
            logger.warning("Plugin yielded more than once; closing the generator")
            self._generator.close()


class _AsyncGeneratorHandle(_Handle):
    def __init__(self, generator: Any) -> None:
        self._generator = generator

    async def close(self) -> None:
        try:
            await self._generator.__anext__()
        except StopAsyncIteration:
            return
        else:
            logger.warning("Plugin yielded more than once; closing the generator")
            await self._generator.aclose()


class _ContextManagerHandle(_Handle):
    def __init__(self, manager: Any) -> None:
        self._manager = manager

    async def close(self) -> None:
        await self._manager.__aexit__(None, None, None)


class _SyncContextManagerHandle(_Handle):
    def __init__(self, manager: Any) -> None:
        self._manager = manager

    async def close(self) -> None:
        self._manager.__exit__(None, None, None)


class PluginContext:
    """Handle passed to a plugin during activation."""

    def __init__(
        self,
        runtime: PluginRuntime,
        activation: _Activation | None = None,
        plugin: _PluginSpec | None = None,
    ) -> None:
        self._runtime = runtime
        self._activation = activation
        self._plugin = plugin

    def apply(
        self,
        plugin: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        """Register a plugin. This never executes it."""
        self._runtime.register(plugin, name=name, description=description)
        if self._activation is not None:
            debug_log(
                f"plugin '{self._plugin.name if self._plugin else '?'}' registered "
                f"another plugin; it becomes active after the next reload"
            )

    def _require_activation(self) -> _Activation:
        if self._activation is None:
            raise PluginError("declarations are only valid during plugin activation")
        return self._activation

    def tool(self, source: ToolSource) -> None:
        self._require_activation().add_tool(source)

    def mcp(self, provider: MCPProvider) -> None:
        self._require_activation().add_mcp(provider)

    def workflow(self, spec: WorkflowSpec) -> None:
        self._require_activation().add_workflow(spec)

    def on(self, event: str, handler: HookHandler) -> None:
        self._require_activation().add_hook(event, handler)

    def config(self, model: type[BaseModel]) -> None:
        """Declare this plugin's settings model.

        Declaring is all a plugin does; the runtime owns where the values live
        and the UI derives its form from the model. A plugin reads them back
        with :func:`load_config`, never from the filesystem.
        """
        activation = self._require_activation()
        if self._plugin is None:
            raise PluginError("a config must be declared from inside a plugin")
        activation.set_config(self._plugin.name, model)


class PluginRuntime:
    def __init__(self) -> None:
        self._specs: dict[str, _PluginSpec] = {}
        self._snapshot: PluginSnapshot = EMPTY_SNAPSHOT
        self._handles: list[_Handle] = []
        self._activation: _Activation | None = None
        self._lock = asyncio.Lock()
        self.debug = False
        #: Names skipped during activation; kept apart so they stay listable.
        self.disabled: set[str] = set()

    @property
    def snapshot(self) -> PluginSnapshot:
        return self._snapshot

    @property
    def context(self) -> PluginContext:
        return PluginContext(self)

    def register(
        self,
        plugin: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> str:
        if not callable(plugin):
            raise PluginError("plugin must be callable")
        resolved = (
            name or getattr(plugin, "__qualname__", None) or getattr(plugin, "__name__", "plugin")
        )
        module = getattr(plugin, "__module__", "") or ""
        doc = inspect.getdoc(plugin) or ""
        resolved_description = (
            description if description is not None else (doc.splitlines()[0] if doc else "")
        )
        spec = _PluginSpec(
            plugin=plugin,
            name=resolved,
            description=resolved_description,
            module=module,
        )
        previous = self._specs.get(resolved)
        if previous is not None and previous.plugin is not plugin:
            logger.warning(
                f"Plugin '{resolved}' is being replaced by {module or 'an external module'}"
            )
        self._specs[resolved] = spec
        return resolved

    def apply(
        self,
        plugin: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> str:
        return self.register(plugin, name=name, description=description)

    def registered(self) -> list[PluginInfo]:
        return [
            PluginInfo(name=spec.name, description=spec.description, module=spec.module)
            for spec in self._specs.values()
        ]

    async def _start(self, spec: _PluginSpec) -> _Handle:
        assert self._activation is not None
        self._activation.begin(spec)
        context = PluginContext(self, self._activation, spec)
        awaitable = spec.plugin(context)
        result: Any = await awaitable if inspect.isawaitable(awaitable) else awaitable

        if inspect.isasyncgen(result):
            handle: _Handle = _AsyncGeneratorHandle(result)
            try:
                await result.__anext__()
            except StopAsyncIteration:
                self._activation.end()
                return _NullHandle()
        elif inspect.isgenerator(result):
            handle = _GeneratorHandle(result)
            try:
                next(result)
            except StopIteration:
                self._activation.end()
                return _NullHandle()
        elif hasattr(result, "__aenter__") and hasattr(result, "__aexit__"):
            handle = _ContextManagerHandle(result)
            await result.__aenter__()
        elif hasattr(result, "__enter__") and hasattr(result, "__exit__"):
            handle = _SyncContextManagerHandle(result)
            result.__enter__()
        else:
            handle = _NullHandle()

        self._activation.end()
        return handle

    async def _load(self) -> PluginSnapshot:
        self._activation = _Activation()
        self._handles = []
        for spec in list(self._specs.values()):
            if spec.name in self.disabled and spec.name not in PROTECTED_PLUGINS:
                logger.info(f"Plugin '{spec.name}' is disabled; not loading it")
                continue
            try:
                handle = await self._start(spec)
            except Exception as exc:
                logger.opt(exception=exc).error(f"Plugin '{spec.name}' failed to load; skipping it")
                if self._activation is not None:
                    self._activation.end()
                continue
            self._handles.append(handle)
        return self._activation.snapshot()

    async def _unload(self) -> None:
        handles, self._handles = self._handles, []
        for handle in reversed(handles):
            try:
                await handle.close()
            except Exception as exc:
                logger.opt(exception=exc).error("Plugin teardown failed")
        self._activation = None

    async def activate(self) -> PluginSnapshot:
        """Load every registered plugin and publish one immutable snapshot."""
        async with self._lock:
            await self.deactivate()
            snapshot = await self._load()
            if self.debug or debug_enabled():
                first = snapshot.signature()
                await self._unload()
                second_snapshot = await self._load()
                second = second_snapshot.signature()
                if first != second:
                    logger.error(
                        "Plugin reload produced a different snapshot; "
                        f"first={first} second={second}"
                    )
                else:
                    logger.info(
                        "Developer mode: plugin load/unload/load produced an identical snapshot"
                    )
                snapshot = second_snapshot
            self._snapshot = snapshot
            WORKFLOWS.install_extra(snapshot.workflows)
            return snapshot

    async def deactivate(self) -> None:
        await self._unload()
        self._snapshot = EMPTY_SNAPSHOT
        WORKFLOWS.install_extra({})

    async def reload(self) -> PluginSnapshot:
        return await self.activate()

    async def dispatch(self, event: str, payload: Mapping[str, Any]) -> tuple[Any, ...]:
        """Call every handler for ``event`` and collect their return values.

        Handlers receive an immutable mapping. Nothing here mutates runtime
        state; the agent decides what to do with the returned values.
        """
        hooks = self._snapshot.hooks
        handlers = hooks.get(event, ())
        if not handlers:
            return ()
        frozen = MappingProxyType(dict(payload))
        results: list[Any] = []
        for handler in handlers:
            try:
                value = handler(frozen)
                if inspect.isawaitable(value):
                    value = await value
            except Exception as exc:
                logger.opt(exception=exc).error(f"Plugin hook '{event}' failed in {handler!r}")
                continue
            results.append(value)
        return tuple(results)


PLUGINS = PluginRuntime()


async def load_config(plugin: str, model: type[BaseModel]) -> BaseModel:
    """Read a plugin's persisted settings as a validated model instance.

    Kept out of :class:`PluginContext` on purpose: the UI's save path must be
    able to read the same values without an activation in progress.
    """
    from app.plugins.config import CONFIGS

    return await CONFIGS.load_model(plugin, model)


def debug_enabled() -> bool:
    raw = (os.environ.get("AMRITA_GO_DEBUG") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    try:
        from amrita_sense.logging import debug

        return bool(debug)
    except Exception:
        return False


def load_external_plugins(runtime: PluginRuntime, directory: Path | None = None) -> int:
    """Import ``~/.amrita/plugins/*.py`` and register their ``plugin`` hook.

    Loading is deliberately separate from activation: importing a module is a
    one-time filesystem action, while activation can be repeated (and is
    repeated in developer mode) without re-running module top-level code.
    """
    root = directory or PATHS.plugins
    root.mkdir(parents=True, exist_ok=True)
    loaded = 0
    for path in sorted(root.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"amrita_go_plugin_{path.stem}"
        try:
            existing = sys.modules.get(module_name)
            if existing is not None:
                importlib.reload(existing)
                module = existing
            else:
                module_spec = importlib.util.spec_from_file_location(module_name, path)
                if module_spec is None or module_spec.loader is None:
                    raise ImportError("cannot build an import spec")
                module = importlib.util.module_from_spec(module_spec)
                sys.modules[module_name] = module
                module_spec.loader.exec_module(module)
        except Exception as exc:
            logger.opt(exception=exc).error(f"Failed to import plugin {path}")
            continue

        entry = getattr(module, "plugin", None) or getattr(module, "setup", None)
        if not callable(entry):
            logger.warning(f"Plugin {path} has no `plugin(ctx)` function; skipping")
            continue
        runtime.register(
            entry,
            name=getattr(module, "NAME", path.stem),
            description=getattr(module, "DESCRIPTION", "") or (inspect.getdoc(entry) or ""),
        )
        loaded += 1
    return loaded


__all__ = [
    "EMPTY_SNAPSHOT",
    "PLUGINS",
    "PROTECTED_PLUGINS",
    "MCPDeclaration",
    "MCPProvider",
    "PluginContext",
    "PluginError",
    "PluginInfo",
    "PluginRegistrationError",
    "PluginRuntime",
    "PluginSnapshot",
    "ToolSource",
    "debug_enabled",
    "load_config",
    "load_external_plugins",
]
