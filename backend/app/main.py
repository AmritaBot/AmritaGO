"""FastAPI application: REST + WebSocket endpoints and the bundled SPA."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Literal

from amrita_core import minimal_init
from amrita_core.types import ModelPreset
from amrita_sense.logging import logger
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app import __version__
from app.agents import (
    BUILTIN_NAMES,
    BUILTIN_STANDARD,
    AgentPreset,
    AgentPresetError,
    parse_preset,
    to_public,
    unknown_tool_patterns,
)
from app.agents import (
    discover as discover_agents,
)
from app.agents import (
    find as find_agent,
)
from app.agents import (
    resolve as resolve_agent,
)
from app.capabilities import catalog_for
from app.capabilities.mcp_runtime import RUNTIME as MCP
from app.capabilities.skills import discover_skills
from app.core.config import PATHS
from app.core.core_config import (
    ConfigRejected,
    apply_on_startup,
    prune_unknown,
    read_config,
    save_config,
)
from app.core.crypto import describe_key
from app.core.logging_setup import setup_logging
from app.core.providers import (
    PROVIDERS,
    Credentials,
    ModelEntry,
    Provider,
    ProviderError,
    list_models,
    new_provider_id,
    preset_key,
    probe_models,
    resolve_preset,
)
from app.core.settings import (
    AppSettings,
    list_presets,
    load_settings,
    migrate_legacy_presets,
    preset_public,
    save_settings,
)
from app.plugins import PLUGINS, PROTECTED_PLUGINS, load_external_plugins, sync_plugin_states
from app.plugins.builtin import install_builtin_plugins
from app.plugins.config import CONFIGS, PluginConfigError, describe_plugin
from app.plugins.todos import clear as clear_todos
from app.plugins.todos import load_sync as load_todos
from app.runtime.agent_runtime import run_turn
from app.runtime.attachments import AttachmentError, save_image, save_text
from app.runtime.audit import BROKER
from app.runtime.commands import COMMANDS, CommandError, fold_session
from app.runtime.limit_gate import LIMITS
from app.runtime.message_edit import MessageEditError, rewrite_last_prompt
from app.runtime.prompt_inject import (
    SLOTS,
    PromptInjectAPI,
    QueuedPrompt,
    StopMode,
    TurnInjector,
)
from app.runtime.sessions import (
    archive_session,
    branch_session,
    branches_of,
    clear_session,
    create_session,
    delete_session,
    get_meta,
    list_sessions,
    load_context,
    load_history,
    load_usage,
    save_meta,
    set_session_config,
)
from app.runtime.stream_hub import HUB, StreamChannel
from app.runtime.tool_gate import TOOLS
from app.runtime.workflows import WORKFLOWS
from app.tools.shell import SHELLS, available_shells

# Route uvicorn's ``logging`` records into loguru before anything logs.
setup_logging()

# Schemas


class SessionCreateRequest(BaseModel):
    title: str = "新会话"
    preset: str | None = None
    # ``None`` means "inherit the current default".
    workspace: str | None = None
    agent: str | None = None


class SessionConfigRequest(BaseModel):
    """What a session can be re-pointed at. ``None`` leaves a field alone."""

    workspace: str | None = None
    agent: str | None = None


class SettingsUpdate(BaseModel):
    active_model: str | None = None
    enabled_skills: list[str] | None = None
    enabled_mcp: list[str] | None = None
    active_workflow: str | None = None
    debug: bool | None = None
    disabled_tools: list[str] | None = None
    # Authoritative allow-list; omitted names are disabled (new plugins too).
    enabled_plugins: list[str] | None = None
    # Legacy inverted form, translated to :attr:`enabled_plugins`.
    disabled_plugins: list[str] | None = None
    # ``graceful`` | ``ask`` — what a run does when it spends its tool budget.
    tool_limit_policy: Literal["graceful", "ask"] | None = None
    # Rounds appended to the same turn each time the user asks to continue.
    tool_limit_extension: int | None = Field(default=None, ge=1, le=100)
    # Refusal rounds granted to a cut-off run so it can close the turn civilly.
    tool_limit_refusal_rounds: int | None = Field(default=None, ge=1, le=50)
    tool_limit_timeout: float | None = Field(default=None, ge=10, le=3600)
    # Wall-clock limit for one tool call; ``0`` disables it.
    tool_timeout: float | None = Field(default=None, ge=0, le=86400)


class ModelPayload(BaseModel):
    id: str
    label: str = ""
    max_context: int | None = None
    max_output: int = 28000
    multimodal: bool = False
    temperature: float = 0.7
    top_p: float = 0.8
    thinking: bool = True
    thinking_effort: str | None = None
    price_in: float | None = None
    price_out: float | None = None
    currency: str = "USD"


class ProviderPayload(BaseModel):
    id: str | None = None
    name: str
    base_url: str
    protocol: str = "openai"
    max_context: int | None = None
    max_output: int = 28000
    enabled: bool = True
    # Omit to keep the stored key; empty string to clear it.
    api_key: str | None = None
    models: list[ModelPayload] = Field(default_factory=list)


class ProviderModelPayload(BaseModel):
    model: ModelPayload


class ModelPatch(BaseModel):
    """A few knobs changed from the model switcher.

    Only the fields present in the request are applied, so the switcher never
    has to send a whole entry back. ``thinking_effort: ""`` clears it, which is
    how the switcher returns to the default.
    """

    model_config = ConfigDict(extra="forbid")

    thinking_effort: str | None = None
    thinking: bool | None = None


class ProbePayload(BaseModel):
    base_url: str
    api_key: str = ""
    protocol: str = "openai"


class AttachmentPayload(BaseModel):
    kind: str
    name: str = ""
    data_url: str | None = None
    text: str | None = None


class MCPServerPayload(BaseModel):
    name: str
    transport: str
    enabled: bool = True


class AgentRawPayload(BaseModel):
    raw: str
    workspace: str | None = None


_AGENT_TEMPLATE = """---
name: {name}
title: {title}
description: ""
icon: bot
# Restrict tools with name globs; omit to allow all or use [] to allow none.
tools:
# Access modes: readonly / workspace / full. Omit to allow all three.
access:
# Choose a workflow or inherit the global setting.

# workflow: react

# Set a session scratch directory for plans and notes, or omit it.

# memory: session
---
你是 {title}。
"""


def _safe_name(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name).strip("-")
    if not safe:
        raise HTTPException(status_code=400, detail="invalid name")
    return safe


def _workspace_path(raw: str | None):
    """Validate a client-supplied workspace path against the registry."""
    if not raw:
        return None
    candidate = Path(raw).expanduser().resolve()
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail="workspace not found")
    return candidate


# Lifespan


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    PATHS.ensure()
    await minimal_init()
    # Install the persisted AmritaCore config over the library defaults.
    await apply_on_startup()
    await MCP.load()
    if await MCP.connect_enabled():
        logger.info(f"Connected MCP servers: {MCP.connected_names()}")

    await migrate_legacy_presets()

    settings = await load_settings()
    PLUGINS.debug = settings.debug
    install_builtin_plugins(PLUGINS)
    load_external_plugins(PLUGINS)
    # Registration must finish first: the allow-list is derived from it.
    settings = await sync_plugin_states(settings)
    snapshot = await PLUGINS.activate()
    logger.info(
        f"Plugins loaded: {[info.name for info in snapshot.plugins]} "
        f"(workflows: {', '.join(WORKFLOWS.names())})"
    )

    logger.info(f"AmritaGO data directory: {PATHS.data}")
    logger.info(f"AmritaGO workspace directory: {PATHS.workspace}")
    # Name the credential key once, at startup: it is the first thing to check.
    key = describe_key(PATHS.data)
    logger.info(f"Credential key: {key.source} ({key.detail})")
    try:
        yield
    finally:
        await SHELLS.shutdown()
        await PLUGINS.deactivate()
        await MCP.shutdown()


app = FastAPI(title="AmritaGO", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API = "/api"


# Settings & presets


@app.get(f"{API}/settings")
async def get_settings() -> dict[str, Any]:
    settings = await load_settings()
    return settings.model_dump()


@app.patch(f"{API}/settings")
async def patch_settings(update: SettingsUpdate) -> dict[str, Any]:
    settings = await load_settings()
    data = settings.model_dump()
    patch = update.model_dump(exclude_none=True)
    registered = {spec.name for spec in PLUGINS.registered()}
    if "enabled_plugins" not in patch and "disabled_plugins" in patch:
        disabled = set(patch["disabled_plugins"])
        patch["enabled_plugins"] = sorted(name for name in registered if name not in disabled)
    plugin_change = "enabled_plugins" in patch
    if plugin_change:
        # Refuse rather than filter: a "disabled" plugin that still runs is worse.
        requested = set(patch["enabled_plugins"])
        blocked = sorted((PROTECTED_PLUGINS & registered) - requested)
        if blocked:
            raise HTTPException(
                status_code=400,
                detail=("这些插件是这个应用的基础能力，不能从界面禁用：" + "、".join(blocked)),
            )
        patch["disabled_plugins"] = []
    else:
        patch.pop("disabled_plugins", None)
    for key, value in patch.items():
        data[key] = value
    saved = await save_settings(AppSettings.model_validate(data))
    if "debug" in patch and saved.debug != PLUGINS.debug:
        PLUGINS.debug = saved.debug
        await PLUGINS.activate()
    if plugin_change:
        # The snapshot must be rebuilt, or the next turn still asks for old tools.
        PLUGINS.disabled = registered - set(saved.enabled_plugins or [])
        await PLUGINS.activate()
    return saved.model_dump()


@app.post(f"{API}/settings")
async def post_settings(update: SettingsUpdate) -> dict[str, Any]:
    return await patch_settings(update)


@app.get(f"{API}/commands")
async def get_commands() -> dict[str, Any]:
    """Slash commands the runtime will intercept.

    Built-ins plus whatever the active plugins declared — the composer's
    palette is built from this, so a plugin's command appears without a
    front-end change and disappears when the plugin is switched off.
    """
    return {"commands": [spec.to_public() for spec in COMMANDS.list()]}


@app.get(f"{API}/presets")
async def get_legacy_presets() -> dict[str, Any]:
    """Read-only view of pre-provider presets, for the migration notice."""
    presets = await list_presets()
    return {"presets": [preset_public(preset) for preset in presets]}


@app.get(f"{API}/providers")
async def get_providers() -> dict[str, Any]:
    settings = await load_settings()
    return {
        "active": settings.active_model,
        "providers": [provider.to_public() for provider in await PROVIDERS.list()],
        "models": await list_models(),
    }


def _validate_provider(payload: ProviderPayload) -> None:
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="名称不能为空")
    base = payload.base_url.strip()
    if not base:
        raise HTTPException(status_code=400, detail="Base URL 不能为空")
    if not base.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Base URL 必须以 http:// 或 https:// 开头",
        )
    if payload.protocol not in {"openai", "anthropic"}:
        raise HTTPException(status_code=400, detail="协议只能是 openai 或 anthropic")
    seen: set[str] = set()
    for model in payload.models:
        if not model.id.strip():
            raise HTTPException(status_code=400, detail="模型 ID 不能为空")
        if model.id in seen:
            raise HTTPException(status_code=400, detail=f"模型 {model.id} 重复")
        seen.add(model.id)
        if model.max_context is not None and model.max_context < 256:
            raise HTTPException(status_code=400, detail="max_context 太小")
        if model.max_output < 1:
            raise HTTPException(status_code=400, detail="max_output 必须大于 0")
        if not 0 <= model.temperature <= 2:
            raise HTTPException(status_code=400, detail="temperature 应在 0到2 之间")
        if not 0 < model.top_p <= 1:
            raise HTTPException(status_code=400, detail="top_p 应在 (0, 1] 之间")


@app.post(f"{API}/providers")
async def upsert_provider(payload: ProviderPayload) -> dict[str, Any]:
    """Create or update a provider. Omitting ``api_key`` keeps the stored one."""
    _validate_provider(payload)
    existing = await PROVIDERS.get(payload.id) if payload.id else None
    provider = Provider(
        id=payload.id or new_provider_id(payload.name),
        name=payload.name.strip(),
        base_url=payload.base_url.strip().rstrip("/"),
        protocol=payload.protocol,
        max_context=payload.max_context,
        max_output=payload.max_output,
        enabled=payload.enabled,
        models=[ModelEntry.model_validate(model.model_dump()) for model in payload.models],
        created_at=existing.created_at if existing else time.time(),
    )
    if existing is not None and existing.created_at:
        provider.created_at = existing.created_at

    credentials: Credentials | None = None
    if payload.api_key is not None:
        previous = await PROVIDERS.credentials(provider.id)
        credentials = Credentials(
            api_key=payload.api_key.strip(),
            extra_headers=previous.extra_headers,
        )
    saved = await PROVIDERS.save(provider, credentials)
    return saved.to_public()


@app.delete(f"{API}/providers/{{provider_id}}")
async def remove_provider(provider_id: str) -> dict[str, Any]:
    if not await PROVIDERS.delete(provider_id):
        raise HTTPException(status_code=404, detail="provider not found")
    settings = await load_settings()
    if settings.active_model and settings.active_model.startswith(f"{provider_id}::"):
        settings.active_model = None
        await save_settings(settings)
    return {"ok": True}


@app.post(f"{API}/providers/{{provider_id}}/toggle")
async def toggle_provider(provider_id: str, enabled: bool = True) -> dict[str, Any]:
    provider = await PROVIDERS.set_enabled(provider_id, enabled)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return provider.to_public()


@app.post(f"{API}/providers/{{provider_id}}/models")
async def add_provider_model(provider_id: str, payload: ProviderModelPayload) -> dict[str, Any]:
    try:
        provider = await PROVIDERS.add_model(
            provider_id, ModelEntry.model_validate(payload.model.model_dump())
        )
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return provider.to_public()


@app.put(f"{API}/providers/{{provider_id}}/models")
async def update_provider_model(provider_id: str, payload: ProviderModelPayload) -> dict[str, Any]:
    try:
        provider = await PROVIDERS.update_model(
            provider_id, ModelEntry.model_validate(payload.model.model_dump())
        )
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return provider.to_public()


@app.patch(f"{API}/providers/{{provider_id}}/models/{{model_id}}")
async def patch_provider_model(
    provider_id: str, model_id: str, patch: ModelPatch
) -> dict[str, Any]:
    """Change one model's knobs without resending the whole entry.

    Used by the model switcher, which only knows the field it is editing.
    """
    fields = patch.model_dump(exclude_unset=True)
    if "thinking_effort" in fields:
        effort = (fields["thinking_effort"] or "").strip()
        if effort and (len(effort) > 32 or not effort.replace("-", "").replace("_", "").isalnum()):
            raise HTTPException(status_code=400, detail="thinking_effort 不合法")
        fields["thinking_effort"] = effort or None
    if not fields:
        raise HTTPException(status_code=400, detail="没有要修改的字段")
    try:
        provider = await PROVIDERS.patch_model(provider_id, model_id, fields)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return provider.to_public()


@app.delete(f"{API}/providers/{{provider_id}}/models/{{model_id}}")
async def remove_provider_model(provider_id: str, model_id: str) -> dict[str, Any]:
    try:
        provider = await PROVIDERS.remove_model(provider_id, model_id)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    settings = await load_settings()
    if settings.active_model == preset_key(provider_id, model_id):
        settings.active_model = None
        await save_settings(settings)
    return provider.to_public()


@app.post(f"{API}/providers/probe")
async def probe_provider(payload: ProbePayload) -> dict[str, Any]:
    """Ask an endpoint which models it serves; failures are informational."""
    result = await probe_models(payload.base_url, payload.api_key.strip(), payload.protocol)
    return result.model_dump()


@app.post(f"{API}/models/activate")
async def activate_model(payload: dict[str, Any]) -> dict[str, Any]:
    key = str(payload.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    try:
        preset = await resolve_preset(key)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if preset is None:
        raise HTTPException(status_code=404, detail="model not found")
    settings = await load_settings()
    settings.active_model = key
    await save_settings(settings)
    return {"active": key, "model": preset.model, "provider": preset.name}


@app.post(f"{API}/attachments")
async def upload_attachment(payload: AttachmentPayload) -> dict[str, Any]:
    try:
        if payload.kind == "image":
            if not payload.data_url:
                raise AttachmentError("data_url is required for an image")
            item = save_image(payload.data_url, payload.name or "pasted-image")
        elif payload.kind == "text":
            if payload.text is None:
                raise AttachmentError("text is required")
            item = save_text(payload.text, payload.name or None)
        else:
            raise AttachmentError("kind must be 'image' or 'text'")
    except AttachmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return item.to_public()


# Workspaces


class WorkspacePayload(BaseModel):
    path: str


@app.get(f"{API}/workspaces")
async def get_workspaces() -> dict[str, Any]:
    from app.core.workspaces import list_workspaces

    settings = await load_settings()
    return {
        "active": settings.active_workspace,
        "workspaces": await list_workspaces(),
    }


@app.post(f"{API}/workspaces")
async def post_workspace(payload: WorkspacePayload) -> dict[str, Any]:
    from app.core.workspaces import add_workspace

    try:
        entry = await add_workspace(payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return entry


@app.delete(f"{API}/workspaces")
async def remove_workspace(path: str) -> dict[str, Any]:
    from app.core.workspaces import remove_workspace as drop

    if not await drop(path):
        raise HTTPException(status_code=404, detail="workspace not found")
    return {"ok": True}


@app.post(f"{API}/workspaces/activate")
async def activate_workspace(payload: WorkspacePayload) -> dict[str, Any]:
    from app.core.workspaces import add_workspace

    try:
        entry = await add_workspace(payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    settings = await load_settings()
    settings.active_workspace = entry["path"]
    await save_settings(settings)
    return entry


# Agent presets


class AgentCreatePayload(BaseModel):
    name: str
    title: str | None = None
    scope: str = "user"
    workspace: str | None = None


@app.get(f"{API}/agents")
async def get_agents(workspace: str | None = None) -> dict[str, Any]:
    resolved = _workspace_path(workspace)
    presets = await discover_agents(resolved)
    settings = await load_settings()
    catalog = await catalog_for(settings, resolve_agent(settings.active_agent, resolved))
    # Plugin tools plus declared MCP tools: preset rules are checked against both.
    pool = [item["name"] for item in catalog.tool_catalog()]
    agents: list[dict[str, Any]] = []
    for preset in presets:
        payload = to_public(preset)
        payload["unknown_tools"] = unknown_tool_patterns(preset, pool)
        agents.append(payload)
    return {
        "active": settings.active_agent,
        "directories": [str(p) for p in PATHS.agent_dirs(resolved)],
        "agents": agents,
        "builtin": to_public(BUILTIN_STANDARD),
        "workflows": [spec.to_public() for spec in WORKFLOWS.list()],
        "tools": catalog.tool_catalog(),
        "mcp": [source.to_public() for source in catalog.mcp],
    }


@app.get(f"{API}/agents/{{name}}")
async def get_agent(name: str, workspace: str | None = None) -> dict[str, Any]:
    preset = find_agent(name, _workspace_path(workspace))
    if preset is None:
        raise HTTPException(status_code=404, detail="agent preset not found")
    settings = await load_settings()
    catalog = await catalog_for(settings, preset)
    return {
        **to_public(preset),
        # Rules matching nothing now; saving is allowed, an MCP server may be offline.
        "unknown_tools": unknown_tool_patterns(
            preset, [item["name"] for item in catalog.tool_catalog()]
        ),
        "raw": preset.path.read_text(encoding="utf-8") if preset.path else "",
        # Built-ins have no file, so the prompt is the only way to read them.
        "prompt": preset.prompt,
    }


@app.post(f"{API}/agents")
async def create_agent(payload: AgentCreatePayload) -> dict[str, Any]:
    name = _safe_name(payload.name)
    workspace = _workspace_path(payload.workspace) or PATHS.workspace.parent
    directory = (
        workspace / ".amrita" / "agents" if payload.scope == "workspace" else PATHS.user_agents
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    if path.exists():
        raise HTTPException(status_code=409, detail="agent preset already exists")
    path.write_text(
        _AGENT_TEMPLATE.format(name=name, title=payload.title or name),
        encoding="utf-8",
    )
    preset = parse_preset(path, payload.scope)
    return to_public(preset)


@app.put(f"{API}/agents/{{name}}")
async def put_agent(name: str, payload: AgentRawPayload) -> dict[str, Any]:
    """Save raw Markdown. Invalid content is rejected rather than written.

    Validation runs on a temp copy first: a half-typed file that only breaks
    YAML must never reach disk, otherwise the preset silently disappears from
    the list on the next discovery.
    """
    preset = find_agent(name, _workspace_path(payload.workspace))
    if preset is None:
        raise HTTPException(status_code=404, detail="agent preset not found")
    if preset.scope == "builtin":
        # Shipped in code: a save can only be a mistake.
        raise HTTPException(
            status_code=403,
            detail=(
                f"「{preset.title}」是内建预设，磁盘上没有对应的 Markdown 文件。"
                "因此不能修改。需要定制请新建一个同名预设，它会覆盖这个内建模式。"
            ),
        )
    if preset.path is None:
        raise HTTPException(status_code=404, detail="agent preset not found")

    fd, tmp_name = tempfile.mkstemp(suffix=".md")
    candidate = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload.raw)
        parse_preset(candidate, preset.scope)
    except AgentPresetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    finally:
        await asyncio.to_thread(candidate.unlink, missing_ok=True)

    preset.path.write_text(payload.raw, encoding="utf-8")
    return to_public(parse_preset(preset.path, preset.scope))


@app.delete(f"{API}/agents/{{name}}")
async def remove_agent(name: str, workspace: str | None = None) -> dict[str, Any]:
    preset = find_agent(name, _workspace_path(workspace))
    if preset is None:
        raise HTTPException(status_code=404, detail="agent preset not found")
    if preset.scope == "builtin":
        # The shipped modes are code; only a same-named file can be deleted.
        raise HTTPException(
            status_code=403,
            detail=f"「{preset.title}」是内建预设，不能删除。",
        )
    if preset.path is None:
        raise HTTPException(status_code=404, detail="agent preset not found")
    preset.path.unlink()
    # Deleting a file that shadowed a built-in restores the built-in.
    return {"ok": True, "restored_builtin": preset.name in BUILTIN_NAMES}


@app.post(f"{API}/agents/{{name}}/activate")
async def activate_agent(name: str) -> dict[str, Any]:
    settings = await load_settings()
    settings.active_agent = name
    await save_settings(settings)
    return {"active": name}


# Workflows, plugins and capabilities


class WorkflowPayload(BaseModel):
    name: str


@app.get(f"{API}/workflows")
async def get_workflows() -> dict[str, Any]:
    settings = await load_settings()
    workspace = _workspace_path(settings.active_workspace)
    active_agent = resolve_agent(settings.active_agent, workspace)
    presets = await discover_agents(workspace)
    # workflow name -> presets that pin it, so the UI can say which ignores the switch.
    pinned_by: dict[str, list[str]] = {}
    for preset in presets:
        if preset.workflow:
            pinned_by.setdefault(preset.workflow, []).append(preset.name)
    return {
        "active": settings.active_workflow,
        "default": WORKFLOWS.require(None).name,
        "effective": active_agent.resolved_workflow(settings.active_workflow).name,
        "active_agent": active_agent.name,
        "active_agent_workflow": active_agent.workflow,
        "pinned_by": pinned_by,
        "workflows": [spec.to_public() for spec in WORKFLOWS.list()],
    }


@app.post(f"{API}/workflows/activate")
async def activate_workflow(payload: WorkflowPayload) -> dict[str, Any]:
    try:
        WORKFLOWS.require(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    settings = await load_settings()
    workspace = _workspace_path(settings.active_workspace)
    active_agent = resolve_agent(settings.active_agent, workspace)
    if active_agent.workflow and active_agent.workflow != payload.name:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Agent 预设「{active_agent.title}」把工作流固定为 "
                f"{active_agent.workflow}，切换全局工作流不会影响它。"
                "要么改预设里的 workflow 字段，要么换一个不固定工作流的预设。"
            ),
        )
    settings.active_workflow = payload.name
    await save_settings(settings)
    return {"active": payload.name}


def _plugins_payload() -> dict[str, Any]:
    """Registered plugins with their activation and persistence state."""
    snapshot = PLUGINS.snapshot
    active = {info.name: info for info in snapshot.plugins}
    items: list[dict[str, Any]] = []
    for spec in PLUGINS.registered():
        info = active.get(spec.name)
        payload = (
            info.to_public()
            if info is not None
            else {
                "name": spec.name,
                "description": spec.description,
                "module": spec.module,
                "tools": [],
                "workflows": [],
                "mcp": [],
                "hooks": [],
                "has_config": spec.name in snapshot.configs,
                "config_name": spec.name if spec.name in snapshot.configs else None,
            }
        )
        payload["enabled"] = spec.name not in PLUGINS.disabled or spec.name in PROTECTED_PLUGINS
        payload["loaded"] = info is not None
        payload["protected"] = spec.name in PROTECTED_PLUGINS
        items.append(payload)
    return {
        "debug": PLUGINS.debug,
        "directory": str(PATHS.plugins),
        **snapshot.to_public(),
        "plugins": items,
        "enabled_plugins": sorted(
            spec.name
            for spec in PLUGINS.registered()
            if spec.name not in PLUGINS.disabled or spec.name in PROTECTED_PLUGINS
        ),
        "disabled_plugins": sorted(PLUGINS.disabled),
        "protected_plugins": sorted(PROTECTED_PLUGINS),
    }


@app.get(f"{API}/plugins")
async def get_plugins() -> dict[str, Any]:
    """Registered plugins with their activation state.

    A disabled plugin still appears here (with ``enabled: false`` and whatever
    it contributed last time it loaded) — otherwise turning one off would hide
    the switch that turns it back on.
    """
    return _plugins_payload()


@app.post(f"{API}/plugins/reload")
async def reload_plugins() -> dict[str, Any]:
    """Reload external plugin modules, then re-run activation.

    The allow-list is re-applied after importing: a file dropped into the
    plugin directory is new, so it stays disabled until the user enables it,
    while previously disabled plugins remain disabled.
    """
    settings = await load_settings()
    load_external_plugins(PLUGINS)
    await sync_plugin_states(settings)
    await PLUGINS.activate()
    return _plugins_payload()


# Plugin configuration


class PluginConfigPayload(BaseModel):
    # Dotted paths -> values, as produced by the derived form.
    values: dict[str, Any] = Field(default_factory=dict)
    # Hash of the snapshot the client edited. ``None`` skips the check.
    hash: str | None = None


def _plugin_config_model(plugin: str) -> type[BaseModel]:
    model = PLUGINS.snapshot.configs.get(plugin)
    if model is None:
        raise HTTPException(
            status_code=404,
            detail={"message": f"插件 {plugin} 没有声明可配置项"},
        )
    return model


@app.get(f"{API}/plugin-configs")
async def list_plugin_configs() -> dict[str, Any]:
    """Every plugin that declared a settings model, with current values.

    The frontend renders each entry from ``fields``; nothing here knows what a
    given plugin's options mean.
    """
    items = [
        await describe_plugin(name, model)
        for name, model in sorted(PLUGINS.snapshot.configs.items())
    ]
    return {"configs": items, "directory": str(PATHS.plugin_configs)}


@app.get(f"{API}/plugin-configs/{{plugin}}")
async def get_plugin_config(plugin: str) -> dict[str, Any]:
    return await describe_plugin(plugin, _plugin_config_model(plugin))


@app.put(f"{API}/plugin-configs/{{plugin}}")
async def put_plugin_config(plugin: str, payload: PluginConfigPayload) -> dict[str, Any]:
    model = _plugin_config_model(plugin)
    try:
        saved = await CONFIGS.save_model(plugin, model, payload.values, payload.hash)
    except PluginConfigError as exc:
        detail: dict[str, Any] = {"message": exc.message, "errors": exc.errors}
        if exc.current_hash is not None:
            detail["current_hash"] = exc.current_hash
            raise HTTPException(status_code=409, detail=detail) from None
        raise HTTPException(status_code=400, detail=detail) from None
    return {**(await describe_plugin(plugin, model)), **saved}


@app.get(f"{API}/capabilities")
async def get_capabilities(
    agent: str | None = None,
    workspace: str | None = None,
    access: str | None = None,
) -> dict[str, Any]:
    settings = await load_settings()
    resolved = resolve_agent(agent or settings.active_agent, _workspace_path(workspace))
    catalog = await catalog_for(settings, resolved, requested_access=access)
    return {
        "agent": resolved.name,
        "workflow": resolved.workflow,
        **catalog.to_public(),
    }


@app.get(f"{API}/tools")
async def get_tools(
    agent: str | None = None,
    workspace: str | None = None,
    access: str | None = None,
) -> dict[str, Any]:
    """The tool pool, grouped by origin, with per-tool switch state.

    Read-only: switching happens through ``PATCH /api/settings`` so the value
    lives with the rest of the settings and survives a reload.
    """
    settings = await load_settings()
    resolved = resolve_agent(agent or settings.active_agent, _workspace_path(workspace))
    catalog = await catalog_for(settings, resolved, requested_access=access)
    return catalog.tool_tree()


# Skills


@app.get(f"{API}/skills")
async def get_skills() -> dict[str, Any]:
    skills = await discover_skills()
    settings = await load_settings()
    return {
        "enabled": settings.enabled_skills,
        "directories": [str(path) for path in PATHS.skill_dirs()],
        "skills": [
            {
                "name": info.name,
                "description": info.description,
                "version": info.version,
                "path": str(info.path),
                "scope": info.scope,
            }
            for info in skills
        ],
    }


# AmritaCore runtime config


class CoreConfigPayload(BaseModel):
    values: dict[str, Any]
    hash: str


@app.get(f"{API}/core-config")
async def get_core_config() -> dict[str, Any]:
    """Editable AmritaCore fields plus the schema the form is built from."""
    return await read_config()


@app.put(f"{API}/core-config")
async def put_core_config(payload: CoreConfigPayload) -> dict[str, Any]:
    try:
        return await save_config(payload.values, payload.hash)
    except ConfigRejected as exc:
        detail: dict[str, Any] = {"message": exc.message, "errors": exc.errors}
        if exc.current_hash is not None:
            detail["current_hash"] = exc.current_hash
            raise HTTPException(status_code=409, detail=detail) from None
        raise HTTPException(status_code=400, detail=detail) from None


@app.post(f"{API}/core-config/prune")
async def prune_core_config() -> dict[str, Any]:
    """Remove config keys the installed AmritaCore no longer defines.

    Offered as an explicit action rather than done automatically on save: the
    user should be able to *see* what their file contained before it goes away,
    and a library version bump should never quietly rewrite their config.
    """
    return await prune_unknown()


# MCP


@app.get(f"{API}/mcp")
async def get_mcp() -> dict[str, Any]:
    return {"servers": [server.model_dump() for server in MCP.list_servers()]}


@app.post(f"{API}/mcp")
async def upsert_mcp(payload: MCPServerPayload) -> dict[str, Any]:
    from app.capabilities.mcp_runtime import MCPServer

    server = MCPServer(name=payload.name, transport=payload.transport, enabled=payload.enabled)
    await MCP.upsert(server)
    if payload.enabled:
        await MCP.connect(payload.name)
    current = MCP.get(payload.name)
    return (current or server).model_dump()


@app.delete(f"{API}/mcp/{{name}}")
async def remove_mcp(name: str) -> dict[str, Any]:
    if not await MCP.remove(name):
        raise HTTPException(status_code=404, detail="server not found")
    return {"ok": True}


@app.post(f"{API}/mcp/{{name}}/toggle")
async def toggle_mcp(name: str, enabled: bool = True) -> dict[str, Any]:
    server = await MCP.set_enabled(name, enabled)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")
    return server.model_dump()


@app.post(f"{API}/mcp/{{name}}/reconnect")
async def reconnect_mcp(name: str) -> dict[str, Any]:
    await MCP.disconnect(name)
    server = await MCP.connect(name)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")
    return server.model_dump()


# Sessions


@app.get(f"{API}/sessions")
async def get_sessions(include_archived: bool = False) -> dict[str, Any]:
    metas = await list_sessions(include_archived=include_archived)
    # Compute running status from active turns; it is not persisted.
    return {"sessions": [{**meta.model_dump(), "running": HUB.busy(meta.id)} for meta in metas]}


@app.post(f"{API}/sessions")
async def post_session(payload: SessionCreateRequest) -> dict[str, Any]:
    settings = await load_settings()
    meta = await create_session(
        title=payload.title,
        preset=payload.preset,
        # Snapshot defaults so this session remains independent of later changes.
        workspace=payload.workspace or settings.active_workspace,
        agent=payload.agent or settings.active_agent,
    )
    return {**meta.model_dump(), "running": False}


@app.get(f"{API}/sessions/{{session_id}}")
async def get_session(session_id: str) -> dict[str, Any]:
    meta = await get_meta(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="session not found")
    return meta.model_dump()


@app.delete(f"{API}/sessions/{{session_id}}")
async def remove_session(session_id: str) -> dict[str, Any]:
    if not await delete_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True}


@app.patch(f"{API}/sessions/{{session_id}}")
async def rename_session(session_id: str, title: str) -> dict[str, Any]:
    meta = await get_meta(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="session not found")
    meta.title = title

    await save_meta(meta)
    return meta.model_dump()


@app.post(f"{API}/sessions/{{session_id}}/config")
async def set_session_settings(session_id: str, payload: SessionConfigRequest) -> dict[str, Any]:
    """Point this conversation at a workspace and/or agent.

    Per session, not per process: the switcher in the composer edits *this*
    conversation, so two sessions can work in different directories at once.
    """
    workspace = payload.workspace
    if workspace is not None:
        resolved = _workspace_path(workspace)
        if resolved is None:
            raise HTTPException(status_code=400, detail="workspace not found")
        workspace = str(resolved)
    meta = await set_session_config(session_id, workspace=workspace, agent=payload.agent)
    if meta is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {**meta.model_dump(), "running": HUB.busy(meta.id)}


@app.get(f"{API}/sessions/{{session_id}}/history")
async def get_history(session_id: str) -> dict[str, Any]:
    if await get_meta(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    entries = await load_history(session_id)
    return {"entries": [entry.model_dump() for entry in entries]}


@app.get(f"{API}/sessions/{{session_id}}/usage")
async def get_usage(session_id: str) -> dict[str, Any]:
    meta = await get_meta(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="session not found")
    turns = await load_usage(session_id)
    context = await load_context(session_id)
    return {
        "totals": meta.usage.model_dump(),
        "turns": [turn.model_dump() for turn in turns],
        # Messages the *model* will see next request; compaction shrinks it.
        "context": {
            "messages": len(context.messages),
            "abstract": bool(context.abstract),
            "tokens": context.usage.prompt_tokens if context.usage else None,
        },
    }


@app.post(f"{API}/sessions/{{session_id}}/clear")
async def clear_session_endpoint(session_id: str) -> dict[str, Any]:
    if not await clear_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    # The plan belongs to the history being cleared.
    clear_todos(session_id)
    return {"ok": True}


@app.post(f"{API}/sessions/{{session_id}}/compact")
async def compact_session_endpoint(session_id: str) -> dict[str, Any]:
    """Fold older history into the summary on demand (``/compact``).

    The same :func:`fold_session` the chat command runs, so a fold from here and
    one from the transcript leave identical history. Between turns only: a live
    run holds its own context copy, so folding the file underneath it would be
    undone when that run saves.
    """
    if await get_meta(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    if HUB.busy(session_id):
        raise HTTPException(status_code=409, detail="这一轮还在跑，等它结束再压缩")

    settings = await load_settings()
    try:
        outcome = await fold_session(session_id, settings)
    except CommandError as exc:
        # Preserve provider errors and their status codes for the UI.
        status = 502 if str(exc).startswith("压缩失败") else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {
        "compacted": outcome.compacted,
        "folded": outcome.folded,
        "kept": outcome.kept,
        "summary_chars": outcome.summary_chars,
        "messages": outcome.messages,
        "detail": outcome.detail,
    }


class BranchPayload(BaseModel):
    index: int | None = None
    title: str | None = None
    # Branch at this bubble, keeping it. Works for a user message too.
    entry_id: str | None = None


class MessageEditPayload(BaseModel):
    content: str
    # What the client believes it is editing; a mismatch means it is stale.
    expected: str | None = None


@app.post(f"{API}/sessions/{{session_id}}/messages/edit")
async def edit_message_endpoint(session_id: str, payload: MessageEditPayload) -> dict[str, Any]:
    """Rewrite the last prompt and hand the client back a clean tail.

    Works at any moment: a running turn is stopped first, so the user never has
    to wait for the agent to finish before correcting what they asked. The
    context is rolled back to before that prompt and everything after it leaves
    the transcript; the client sends the edited text as a normal turn.
    """
    try:
        outcome = await rewrite_last_prompt(
            session_id,
            payload.content,
            expected=payload.expected,
        )
    except MessageEditError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {
        "ok": True,
        "entry_id": outcome.entry_id,
        "previous": outcome.previous,
        "content": outcome.content,
        "removed_entries": outcome.removed_entries,
        "removed_messages": outcome.removed_messages,
        "context_messages": outcome.context_messages,
        "dropped_prompt": outcome.dropped_prompt,
    }


@app.post(f"{API}/sessions/{{session_id}}/archive")
async def archive_session_endpoint(session_id: str, archived: bool = True) -> dict[str, Any]:
    meta = await archive_session(session_id, archived)
    if meta is None:
        raise HTTPException(status_code=404, detail="session not found")
    return meta.model_dump()


@app.post(f"{API}/sessions/{{session_id}}/branch")
async def branch_session_endpoint(session_id: str, payload: BranchPayload) -> dict[str, Any]:
    try:
        meta = await branch_session(
            session_id,
            index=payload.index,
            title=payload.title,
            entry_id=payload.entry_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    if meta is None:
        raise HTTPException(status_code=404, detail="session not found")
    return meta.model_dump()


@app.get(f"{API}/sessions/{{session_id}}/branches")
async def get_branches(session_id: str) -> dict[str, Any]:
    return {"branches": [meta.model_dump() for meta in await branches_of(session_id)]}


@app.get(f"{API}/sessions/{{session_id}}/todos")
async def get_todos(session_id: str) -> dict[str, Any]:
    """The plan the agent is working through, as last written by its tools.

    Read straight off disk so a page reload shows the plan without replaying the
    stream; the ``todos`` stream event keeps the same shape so the client can
    apply both without special-casing.
    """
    return load_todos(session_id).to_public()


@app.delete(f"{API}/sessions/{{session_id}}/todos")
async def delete_todos(session_id: str) -> dict[str, Any]:
    clear_todos(session_id)
    return {"ok": True}


@app.get(f"{API}/shells")
async def get_shells() -> dict[str, Any]:
    """Live terminal sessions.

    A session is logical: the process is per-call, but the directory, the
    configured executable and the recent commands belong to the session, which
    is what makes it inspectable (and resettable) from the UI.
    """
    return {
        "available": available_shells(),
        "sessions": SHELLS.describe(),
        "spool": str(PATHS.data / "shell"),
    }


@app.delete(f"{API}/shells/{{session_id}}")
async def close_shell(session_id: str) -> dict[str, Any]:
    """Forget a session: its saved working directory and its history."""
    if not await SHELLS.reset_id(session_id):
        raise HTTPException(status_code=404, detail="terminal session not found")
    return {"ok": True}


# Chat over WebSocket


def _forwardable(line: str) -> bool:
    """Whether a buffered event is still meaningful to a client that reconnects.

    A ``confirm`` is a question, and a question whose caller has already moved
    on is not history — replaying it would pop a dialog that can only answer
    "已过期". Everything else is history and is forwarded as is. The string
    check keeps the common case (deltas) from being parsed at all.
    """
    if '"confirm"' not in line:
        return True
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return True
    if payload.get("type") != "confirm":
        return True
    return BROKER.is_pending(str(payload.get("request_id") or ""))


async def _drain(websocket: WebSocket, channel: StreamChannel, offset: int = 0) -> int:
    """Forward buffered + live events to the socket; return the new offset.

    Sending is best-effort: if the peer vanished mid-chunk we stop forwarding
    but the agent keeps running and the buffer keeps growing, so a reconnect
    loses nothing. This function never raises for a dead peer.

    A turn that chains into a queued prompt publishes its next channel on
    ``successor``; the reader follows it instead of ending, so one socket sees
    the whole chain while every turn keeps its own buffer (a shared buffer would
    replay already-committed turns on reconnect).
    """
    cursor = offset
    current = channel
    while True:
        # Read the version before waiting, so anything appended wakes us at once.
        version = current.version
        lines, cursor, done = current.read_from(cursor)
        for line in lines:
            if not _forwardable(line):
                continue
            try:
                await websocket.send_text(line)
            except (WebSocketDisconnect, RuntimeError, ConnectionError):
                return cursor
        successor = current.successor
        if successor is not None:
            current = successor
            cursor = 0
            continue
        if done:
            return cursor
        await current.wait_change(version)


def _frame(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _stop_mode(payload: dict[str, Any]) -> StopMode:
    """Read the requested stop mode, defaulting to the graceful one.

    Graceful is the better default: it stops between rounds instead of inside
    one. Workflows without a suspend point fall back to an immediate stop by
    themselves, so the caller does not have to know which workflow is running.
    """
    raw = str(payload.get("mode") or StopMode.GRACEFUL)
    try:
        return StopMode(raw)
    except ValueError:
        return StopMode.GRACEFUL


async def _ack_stop(websocket: WebSocket, injector: PromptInjectAPI, mode: StopMode) -> None:
    """Stop a turn and report how it went.

    The reply is sent on the socket rather than buffered in the channel: it
    describes a request, not the transcript, and replaying it on reconnect would
    only confuse a fresh client.
    """
    try:
        result = await injector.stop(mode)
    except Exception as exc:  # pragma: no cover - a stop must never break the socket
        logger.opt(exception=exc).error("Failed to stop a turn")
        return
    with suppress(WebSocketDisconnect, RuntimeError, ConnectionError):
        await websocket.send_text(_frame({"type": "stop_ack", **result.to_public()}))


async def _forward_until_done(
    websocket: WebSocket, channel: StreamChannel, offset: int = 0
) -> None:
    """Drain a channel from ``offset``, swallowing a dead peer.

    Wrapped in a task so the reader loop stays responsive to ``ping`` and to a
    new ``send`` while an older replay is still running.
    """
    try:
        await _drain(websocket, channel, offset)
    except (WebSocketDisconnect, RuntimeError, ConnectionError):
        return


@app.websocket(f"{API}/ws/chat")
async def chat_socket(websocket: WebSocket) -> None:
    """Bidirectional chat socket.

    Client -> server messages:
      ``{"type": "send", "session_id": "...", "message": "...", "preset": "..."}``
          start a turn (returns immediately; events arrive on this socket)
      ``{"type": "resume", "session_id": "...", "stream_id": "..."}``
          replay a buffer from the beginning and continue live
      ``{"type": "stop", "session_id": "...", "mode": "graceful"|"immediate"}``
          end the running turn; ``graceful`` waits for a declared suspend point
          and falls back to ``immediate`` when the workflow has none
      ``{"type": "prompt_offer", "session_id": "...", "message": "...",
          "mode": "...", "stop": true}``
          queue a message for the next turn, replacing any pending one
      ``{"type": "prompt_revoke", "session_id": "...", "handle_id": "..."}``
          withdraw a queued message that has not started yet
      ``{"type": "tool_cancel", "tool_id": "...", "reason": "..."}``
          stop one running tool call; the turn continues with the reason
      ``{"type": "ping"}``

    Server -> client extras: ``queued``, ``unqueued``, ``stop_ack``, and
    ``turn_end`` (one turn finished; the stream stays open if another follows).
    ``done`` closes the stream, which is why only the driver sends it.
    """
    await websocket.accept()

    forward_tasks: set[asyncio.Task[None]] = set()
    # Run stops and offers in the background so suspend waits do not block pings.
    bg_tasks: set[asyncio.Task[None]] = set()

    def spawn_background(coro: Any) -> None:
        task = asyncio.create_task(coro)
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)

    def spawn_forward(channel: StreamChannel, offset: int = 0) -> None:
        # One live forwarder only; a newer send supersedes an older replay.
        for task in list(forward_tasks):
            task.cancel()
        forward_tasks.clear()
        task = asyncio.create_task(_forward_until_done(websocket, channel, offset))
        forward_tasks.add(task)
        task.add_done_callback(forward_tasks.discard)

    # Replay an unfinished run on connect; a finished one is already in history.
    session_id = websocket.query_params.get("session_id")
    if session_id:
        latest = HUB.latest(session_id)
        if latest is not None and not latest.finished:
            spawn_forward(latest)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(_frame({"type": "error", "message": "invalid JSON"}))
                continue

            kind = payload.get("type")
            if kind == "ping":
                await websocket.send_text(_frame({"type": "pong"}))
                continue

            target_session = payload.get("session_id") or session_id
            if not target_session:
                await websocket.send_text(
                    _frame({"type": "error", "message": "session_id is required"})
                )
                continue

            if kind == "resume":
                stream_id = payload.get("stream_id")
                channel = HUB.get(stream_id) if stream_id else HUB.latest(target_session)
                if channel is not None:
                    spawn_forward(channel)
                continue

            if kind == "stop":
                channel = HUB.latest(target_session)
                if channel is None or channel.finished:
                    continue
                mode = _stop_mode(payload)
                injector: PromptInjectAPI = TurnInjector(channel)
                spawn_background(_ack_stop(websocket, injector, mode))
                continue

            if kind == "prompt_offer":
                # Queue before stopping so a failed offer cannot leave the user without a message.
                channel = HUB.latest(target_session)
                if channel is None or channel.finished:
                    await websocket.send_text(
                        _frame(
                            {
                                "type": "error",
                                "message": "这一轮已经结束，直接发送即可",
                            }
                        )
                    )
                    continue
                text = (payload.get("message") or "").strip()
                files = tuple(payload.get("attachments") or [])
                if not text and not files:
                    await websocket.send_text(_frame({"type": "error", "message": "消息为空"}))
                    continue
                injector = TurnInjector(channel)
                offer = await injector.offer(QueuedPrompt(content=text, attachments=files))
                await websocket.send_text(_frame({"type": "queued", **offer.to_public()}))
                if payload.get("stop", True):
                    spawn_background(_ack_stop(websocket, injector, _stop_mode(payload)))
                continue

            if kind == "prompt_revoke":
                channel = HUB.latest(target_session)
                revoker = TurnInjector(channel) if channel is not None else None
                status = (
                    (await revoker.revoke(str(payload.get("handle_id") or ""))).value
                    if revoker is not None
                    else "not_found"
                )
                await websocket.send_text(
                    _frame(
                        {
                            "type": "unqueued",
                            "handle_id": str(payload.get("handle_id") or ""),
                            "status": status,
                        }
                    )
                )
                continue

            if kind == "confirm_response":
                ok = BROKER.resolve(
                    str(payload.get("request_id") or ""),
                    approved=bool(payload.get("approved")),
                    command=payload.get("command"),
                    reason=str(payload.get("reason") or ""),
                )
                if not ok:
                    await websocket.send_text(
                        _frame({"type": "error", "message": "这条确认请求已过期"})
                    )
                continue

            if kind == "limit_response":
                ok = LIMITS.resolve(
                    str(payload.get("request_id") or ""),
                    str(payload.get("action") or "graceful"),
                )
                if not ok:
                    await websocket.send_text(
                        _frame({"type": "error", "message": "这个预算询问已过期"})
                    )
                continue

            if kind == "tool_cancel":
                # Pass the cancellation reason to the model so it can choose another approach.
                ok = TOOLS.cancel(
                    str(payload.get("tool_id") or ""),
                    str(payload.get("reason") or ""),
                )
                if not ok:
                    await websocket.send_text(
                        _frame({"type": "error", "message": "这个工具调用已经结束"})
                    )
                continue

            if kind != "send":
                continue

            attachments = list(payload.get("attachments") or [])
            message = (payload.get("message") or "").strip()
            requested_access = payload.get("access")
            if not message and not attachments:
                await websocket.send_text(_frame({"type": "error", "message": "消息为空"}))
                continue
            meta = await get_meta(target_session)
            if meta is None:
                await websocket.send_text(_frame({"type": "error", "message": "session not found"}))
                continue

            existing = HUB.latest(target_session)
            if existing is not None and not existing.finished:
                await websocket.send_text(
                    _frame(
                        {
                            "type": "error",
                            "message": "this session is still answering; wait or reconnect",
                        }
                    )
                )
                continue

            settings = await load_settings()
            # Resolve session settings before one-off overrides and global defaults.
            run_settings = settings.model_copy(
                update={
                    "active_workspace": meta.workspace or settings.active_workspace,
                    "active_agent": meta.agent or settings.active_agent,
                }
            )
            # Handle registered commands before checking model availability.
            resolved: ModelPreset | None = None
            if COMMANDS.match(message) is None:
                model_key = payload.get("model") or settings.active_model
                resolved = await resolve_preset(model_key)
                if resolved is None:
                    await websocket.send_text(
                        _frame(
                            {
                                "type": "error",
                                "message": (
                                    "没有可用的模型。请在设置 -> 模型里添加 Provider "
                                    "和至少一个模型，并选中它。"
                                ),
                            }
                        )
                    )
                    continue
                if not resolved.api_key:
                    await websocket.send_text(
                        _frame(
                            {
                                "type": "error",
                                "message": (
                                    f"{resolved.name} 没有 API Key。请到设置 -> 模型里补上。"
                                ),
                            }
                        )
                    )
                    continue

            # Bound to a name so the closure captures a non-optional value.
            model = resolved

            workspace = _workspace_path(payload.get("workspace") or run_settings.active_workspace)
            agent = resolve_agent(payload.get("agent") or run_settings.active_agent, workspace)

            current = target_session
            channel = await HUB.create(current)

            async def _run(
                ch: StreamChannel,
                sid: str,
                text: str,
                cfg: AppSettings,
                model_preset: ModelPreset | None,
                agent_preset: AgentPreset,
                files: list[dict[str, Any]],
            ) -> None:
                # The run is independent of this socket; a disconnect stops only the forwarder.
                await run_turn(
                    ch,
                    session_id=sid,
                    user_input=text,
                    settings=cfg,
                    preset=model_preset,
                    agent=agent_preset,
                    attachments=files,
                    requested_access=requested_access,
                )

            async def _drive(
                ch: StreamChannel,
                text: str,
                files: list[dict[str, Any]],
            ) -> None:
                """Run turns until the queue is empty, then close the stream.

                One channel per turn: each keeps its own buffer, so a reconnect
                replays only the turn that is still running and reads the
                finished ones from ``history.jsonl``. A shared buffer would
                replay turns that were already committed.

                ``done`` is published here rather than by the turn, because the
                stream is not over until there is nothing left to run — and a
                stopped turn must still close the stream, or the UI stays busy
                forever.
                """
                current_ch = ch
                current_text = text
                current_files = files
                summary: dict[str, Any] = {}
                try:
                    while True:
                        turn = asyncio.create_task(
                            _run(
                                current_ch,
                                current,
                                current_text,
                                run_settings,
                                model,
                                agent,
                                current_files,
                            )
                        )
                        current_ch.turn_task = turn
                        try:
                            await asyncio.wait({turn})
                        except asyncio.CancelledError:
                            # The chain itself is being killed: take the turn with it.
                            turn.cancel()
                            with suppress(asyncio.CancelledError, Exception):
                                await asyncio.wait({turn})
                            raise
                        summary = current_ch.turn_summary or summary
                        handle = await SLOTS.slot(current).take()
                        if handle is None:
                            break
                        # Link the next turn before this one ends so the socket follows without a gap.
                        successor = await HUB.create(current)
                        successor.task = asyncio.current_task()
                        current_ch.successor = successor
                        current_ch.notify()
                        current_ch = successor
                        current_text = handle.content
                        current_files = list(handle.attachments)
                finally:
                    payload = {key: value for key, value in summary.items() if key != "type"}
                    with suppress(asyncio.CancelledError, Exception):
                        await current_ch.publish({"type": "done", **payload})

            channel.task = asyncio.create_task(_drive(channel, message, attachments))
            spawn_forward(channel)
    except WebSocketDisconnect:
        return
    finally:
        for task in list(forward_tasks):
            task.cancel()
        forward_tasks.clear()
        for task in list(bg_tasks):
            task.cancel()
        bg_tasks.clear()


@app.get(f"{API}/streams/{{session_id}}")
async def stream_state(session_id: str) -> dict[str, Any]:
    """Introspection for reconnect logic and debugging."""
    pending = await SLOTS.slot(session_id).peek()
    channel = HUB.latest(session_id)
    if channel is None:
        return {
            "active": False,
            "pending": pending.to_public() if pending is not None else None,
        }
    return {
        "active": not channel.finished,
        "stream_id": channel.stream_id,
        "bytes": channel.size,
        "error": channel.error,
        "stopping": channel.stop_mode is not None,
        "inject": channel.inject.to_public() if channel.inject is not None else None,
        "pending": pending.to_public() if pending is not None else None,
    }


@app.get(f"{API}/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "data_dir": str(PATHS.data),
        "mcp_connected": MCP.connected_names(),
    }


# Static SPA (built frontend), mounted last so /api wins

if (PATHS.frontend_dist / "index.html").exists():
    app.mount(
        "/assets",
        StaticFiles(directory=PATHS.frontend_dist / "assets"),
        name="assets",
    )

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> Any:
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = PATHS.frontend_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(PATHS.frontend_dist / "index.html")
