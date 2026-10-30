"""Skill discovery and tool-pool injection, backed by ``faskill``.

``faskill`` owns the SKILL.md format (front matter, ``$ARGUMENTS``, script
metadata, caching). This module only adapts its ``SkillContext``: discover
workspace-then-user skills, expose one tool per enabled skill under the skill's
own name, emit ``skill_call`` stream metadata, and build the skill section of
the system prompt.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from amrita_core.contents import MessageMetadataPayload, MessageWithMetadata
from amrita_core.tools.manager import MultiToolsManager, ToolsManager
from amrita_core.tools.models import (
    FunctionDefinitionSchema,
    FunctionParametersSchema,
    FunctionPropertySchema,
    ToolContext,
    ToolData,
    ToolFunctionSchema,
)
from faskill import SkillContext, SkillMetadata, create_context

from app.core.config import PATHS

_ARGUMENTS_PARAM = "arguments"

# Guards lazy discovery: the web server calls the sync API from worker threads.
_context_lock = threading.Lock()
_context: SkillContext | None = None
_context_signature: tuple[Any, ...] | None = None


class SkillCallMetadata(MessageMetadataPayload):
    """Stream metadata emitted when a skill tool runs."""

    skill_name: str


@dataclass
class SkillInfo:
    name: str
    description: str
    version: str | None
    path: Path
    scope: str = "user"
    allowed_tools: tuple[str, ...] | None = None

    @property
    def directory(self) -> Path:
        return self.path.parent


def _skill_dirs() -> list[Path]:
    """Workspace first: faskill keeps the first source for a duplicate name."""
    PATHS.ensure()
    return [PATHS.workspace_skills, PATHS.user_skills]


def _signature(directories: list[Path]) -> tuple[Any, ...]:
    """Cheap change detector so a new or edited SKILL.md is picked up."""
    entries: list[tuple[str, int]] = []
    for root in directories:
        for skill_file in sorted(root.glob("*/SKILL.md")):
            try:
                entries.append((str(skill_file), skill_file.stat().st_mtime_ns))
            except OSError:
                entries.append((str(skill_file), 0))
    return tuple(entries)


def _build_context(directories: list[Path]) -> SkillContext:
    context = create_context(skill_dirs=[str(directory) for directory in directories])
    context.discover()
    return context


def _context_for_discovery() -> tuple[SkillContext, list[Path]]:
    global _context, _context_signature
    directories = _skill_dirs()
    signature = _signature(directories)
    with _context_lock:
        if _context is None or _context_signature != signature:
            try:
                _context = _build_context(directories)
            except Exception:
                # An unreadable directory must not take the whole app down.
                _context = create_context(skill_dirs=[])
                _context.discover()
            _context_signature = signature
        return _context, directories


def _scope_of(path: Path, directories: list[Path]) -> str:
    try:
        resolved = path.resolve()
    except OSError:
        return "user"
    for directory, scope in zip(directories, ("workspace", "user"), strict=False):
        try:
            if resolved.is_relative_to(directory.resolve()):
                return scope
        except OSError:
            continue
    return "user"


def _to_info(meta: SkillMetadata, directories: list[Path]) -> SkillInfo:
    return SkillInfo(
        name=meta.name,
        description=meta.description,
        version=meta.version,
        path=meta.skill_path,
        scope=_scope_of(meta.skill_path, directories),
        allowed_tools=meta.allowed_tools,
    )


def discover_skills_sync() -> list[SkillInfo]:
    """Collect skills from the workspace, then the user directory."""
    context, directories = _context_for_discovery()
    return [_to_info(meta, directories) for meta in context.list_skills(include_qualified=False)]


async def discover_skills() -> list[SkillInfo]:
    return await asyncio.to_thread(discover_skills_sync)


def skill_enabled(name: str, enabled: list[str] | None) -> bool:
    """``None`` means "all enabled"; an empty list means "none enabled"."""
    if enabled is None:
        return True
    return name in enabled


def _build_skill_schema(info: SkillInfo) -> ToolFunctionSchema:
    return ToolFunctionSchema(
        type="function",
        strict=True,
        function=FunctionDefinitionSchema(
            name=info.name,
            description=info.description,
            parameters=FunctionParametersSchema(
                type="object",
                properties={
                    _ARGUMENTS_PARAM: FunctionPropertySchema(
                        type="string",
                        description=(
                            "The user's request or task payload for this skill "
                            "(empty string when there is nothing to pass)."
                        ),
                    )
                },
                required=[_ARGUMENTS_PARAM],
            ),
        ),
    )


def _make_handler(info: SkillInfo):
    async def invoke(ctx: ToolContext) -> str:
        arguments = ctx.data.get(_ARGUMENTS_PARAM) or ""

        stream = ctx.ctx.io_stream
        if stream is not None:
            try:
                await stream.yield_response(
                    MessageWithMetadata(
                        content=f"使用了技能：{info.name}",
                        metadata=SkillCallMetadata(
                            type="skill_call",
                            extra_type=None,
                            skill_name=info.name,
                        ),
                    )
                )
            except Exception:
                pass

        context, _ = _context_for_discovery()
        # faskill's invocation is synchronous (disk read + template processing).
        return await asyncio.to_thread(context.invoke_skill, info.name, str(arguments))

    return invoke


def core_tool_data() -> list[ToolData]:
    """Snapshot of AmritaCore's registered tools.

    ``get_tools`` runs each tool's ``enable_if`` predicate, which reads the
    global config; before ``minimal_init`` that raises, so fall back to the raw
    registry in that case.

    The schemas are passed through untouched. What a tool *means* is the
    provider's contract, and rewriting its description would make the prompt
    disagree with the registry for every other consumer. Guidance for how the
    agent should use a tool belongs in the system prompt.
    """
    try:
        global_tools = ToolsManager().get_tools()
    except RuntimeError:
        global_tools = ToolsManager()._models
    return list(global_tools.values())


def skill_tool_data(
    enabled: list[str] | None,
    skills: list[SkillInfo] | None = None,
) -> list[ToolData]:
    out: list[ToolData] = []
    names: set[str] = set()
    for info in skills if skills is not None else discover_skills_sync():
        if not skill_enabled(info.name, enabled):
            continue
        if info.name in names:
            continue
        names.add(info.name)
        out.append(
            ToolData(
                data=_build_skill_schema(info),
                func=_make_handler(info),
                custom_run=True,
            )
        )
    return out


def build_skill_tools(
    enabled: list[str] | None,
    skills: list[SkillInfo] | None = None,
) -> MultiToolsManager:
    """Compose core tools and enabled skill tools into a fresh manager."""
    manager = MultiToolsManager()
    for tool in core_tool_data():
        name = tool.data.function.name
        if not manager.has_tool(name):
            manager.register_tool(tool)
    for tool in skill_tool_data(enabled, skills):
        if not manager.has_tool(tool.data.function.name):
            manager.register_tool(tool)
    return manager


def build_skill_usage_prompt(
    enabled: list[str] | None, skills: list[SkillInfo] | None = None
) -> str:
    """System-prompt section describing the available skills."""
    available = [
        info
        for info in (skills if skills is not None else discover_skills_sync())
        if skill_enabled(info.name, enabled)
    ]
    if not available:
        return ""
    lines = [
        "## Available faskill skills",
        "Call a skill tool when the user's task matches its purpose:",
    ]
    lines.extend(f"- {info.name}: {info.description}" for info in available)
    lines.append(
        'Pass the user\'s request as the "arguments" string; it is inserted '
        "into the skill template via $ARGUMENTS (or appended when the template "
        "has no placeholder). The tool returns the processed skill content."
    )
    return "\n".join(lines)
