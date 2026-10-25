"""Skill (Anthropic Agent Skills style) discovery and tool-pool injection.

A skill is a directory under ``data/skills/<dir>/`` containing ``SKILL.md``::

    ---
    name: pdf-report
    description: Build a PDF report from conversation notes.
    version: 1.0.0
    ---
    # Instructions
    Turn the user's request into a report. $ARGUMENTS

Progressive disclosure mirrors the AmritaBot implementation:

1. Only skill *metadata* is advertised in the tool pool (one tool per skill).
2. Invoking the tool returns the ``SKILL.md`` body, so whole instructions are
   paid for only when the skill is actually used.
3. The tool result also lists sibling reference files, so the model can ask for
   them by name instead of receiving the whole directory up front.

Skills are registered into a **clone** of the global ``ToolsManager`` so that
per-session filtering never mutates the process-wide tool registry.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

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

from app.core.config import PATHS

_ARGUMENTS_PARAM = "arguments"
_SKILL_FILE = "SKILL.md"


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

    @property
    def directory(self) -> Path:
        return self.path.parent


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Minimal YAML front-matter reader (flat ``key: value`` pairs only)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    meta: dict[str, str] = {}
    end = None
    for idx in range(1, len(lines)):
        line = lines[idx]
        if line.strip() in {"---", "..."}:
            end = idx
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip().strip('"').strip("'")
        meta[key.strip()] = value
    if end is None:
        return {}, text
    return meta, "\n".join(lines[end + 1 :]).lstrip("\n")


def _read_skill(directory: Path, scope: str = "user") -> SkillInfo | None:
    skill_file = directory / _SKILL_FILE
    if not skill_file.is_file():
        return None
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, _ = _parse_frontmatter(text)
    name = meta.get("name") or directory.name
    description = meta.get("description") or "No description provided."
    return SkillInfo(
        name=name.strip(),
        description=description.strip(),
        version=meta.get("version"),
        path=skill_file,
        scope=scope,
    )


def discover_skills_sync() -> list[SkillInfo]:
    """Collect skills from the workspace, then the user directory.

    Discovery order is precedence order: a workspace skill with the same name
    as a user skill wins, which lets a project pin its own version of a shared
    skill without touching the global copy.
    """
    PATHS.ensure()
    found: list[SkillInfo] = []
    seen: set[str] = set()
    for directory, scope in (
        (PATHS.workspace_skills, "workspace"),
        (PATHS.user_skills, "user"),
    ):
        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue
            info = _read_skill(child, scope)
            if info is None or info.name in seen:
                continue
            seen.add(info.name)
            found.append(info)
    return found


async def discover_skills() -> list[SkillInfo]:
    return await asyncio.to_thread(discover_skills_sync)


def skill_enabled(name: str, enabled: list[str] | None) -> bool:
    """``None`` means "all enabled"; an empty list means "none enabled"."""
    if enabled is None:
        return True
    return name in enabled


def _read_body(info: SkillInfo) -> str:
    text = info.path.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(text)
    return body


def _reference_files(info: SkillInfo) -> list[str]:
    """List sibling files (excluding SKILL.md) for progressive disclosure."""
    out: list[str] = []
    for path in sorted(info.directory.rglob("*")):
        if not path.is_file() or path.name == _SKILL_FILE:
            continue
        out.append(path.relative_to(info.directory).as_posix())
    return out


def _build_skill_schema(info: SkillInfo) -> ToolFunctionSchema:
    return ToolFunctionSchema(
        type="function",
        strict=True,
        function=FunctionDefinitionSchema(
            name=f"skill_{_sanitize(info.name)}",
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


def _sanitize(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)


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

        body = await asyncio.to_thread(_read_body, info)
        body = body.replace("$ARGUMENTS", str(arguments))
        refs = await asyncio.to_thread(_reference_files, info)
        parts = [body.strip()]
        if refs:
            parts.append(
                "\n\n<skill_files>\n" + "\n".join(f"- {ref}" for ref in refs) + "\n</skill_files>\n"
                "Ask for a specific file if you need its contents."
            )
        return "\n".join(part for part in parts if part)

    return invoke


def core_tool_data() -> list[ToolData]:
    """Snapshot of AmritaCore's registered tools.

    ``get_tools`` runs each tool's ``enable_if`` predicate, which reads the
    global config; before ``minimal_init`` that raises, so fall back to the raw
    registry in that case.
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
        tool_name = f"skill_{_sanitize(info.name)}"
        if tool_name in names:
            continue
        names.add(tool_name)
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
        "## Available skills",
        "Call a skill tool when the user's task matches its purpose:",
    ]
    lines.extend(f"- {info.name}: {info.description}" for info in available)
    lines.append(
        'Pass the user\'s request as the "arguments" string; it replaces '
        "$ARGUMENTS in the skill template. The tool returns the skill "
        "instructions plus the list of files you may ask for."
    )
    return "\n".join(lines)
