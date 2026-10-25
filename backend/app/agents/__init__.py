"""Agent presets: one Markdown file per agent mode.

The front matter holds ``name/description/icon/tools/access/workflow/memory/
template``; everything after it is the prompt body, and the body *is* the
system prompt. A body line starting with ``\\---`` becomes a literal ``---``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from amrita_core.consts import DEFAULT_TEMPLATE
from amrita_sense.logging import logger
from jinja2 import Template, TemplateSyntaxError

from app.capabilities.permissions import ACCESS_MODES
from app.core.config import PATHS
from app.runtime.workflows import DEFAULT_WORKFLOW, WORKFLOWS, WorkflowSpec

_FENCE = re.compile(r"^---\s*$", re.MULTILINE)
_ESCAPED_FENCE = "\\---"


class AgentPresetError(ValueError):
    """Raised when a preset file cannot be understood."""


def _as_globs(raw: Any, key: str) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    raise AgentPresetError(f"`{key}` must be a list or a comma-separated string")


@dataclass
class AgentPreset:
    name: str
    title: str
    description: str = ""
    icon: str = "bot"
    # Glob patterns matched against tool names. ``None`` = allow every tool.
    tools: list[str] | None = None
    # Access modes this preset may use; ``None`` allows every mode.
    access: list[str] | None = None
    # Registered workflow to run; ``None`` follows the global switch.
    workflow: str | None = None
    # ``"session"`` mounts an app-owned scratch directory for plans and notes.
    memory: str | None = None
    # ``None`` -> use AmritaCore's default Jinja2 template.
    template: str | None = None
    prompt: str = ""
    path: Path | None = None
    scope: str = "user"
    extra: dict[str, Any] = field(default_factory=dict)

    def tool_allowed(self, tool_name: str) -> bool:
        """``None`` tools means "everything"; an empty list means "nothing"."""
        if self.tools is None:
            return True
        return any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in self.tools)

    def access_allowed(self, mode: str) -> bool:
        """``None`` allows every mode; a list restricts to what it names."""
        if self.access is None:
            return True
        return mode in self.access

    @property
    def read_only(self) -> bool:
        """A preset that can only read is the common safe default."""
        return (
            self.access is not None
            and "readonly" in self.access
            and not ("workspace" in self.access or "full" in self.access)
        )

    def resolved_template(self) -> Template:
        """The Jinja2 template to render the system prompt with."""
        if not self.template or not self.template.strip():
            return DEFAULT_TEMPLATE
        return Template(self.template)

    def resolved_workflow(self, fallback: str | None = None) -> WorkflowSpec:
        """The preset's workflow, or the caller's active one when unpinned."""
        return WORKFLOWS.require(self.workflow or fallback or DEFAULT_WORKFLOW)

    @property
    def workflow_pinned(self) -> bool:
        """A preset that names a workflow overrides the global switch."""
        return self.workflow is not None


def _unescape_body(body: str) -> str:
    lines: list[str] = []
    for line in body.splitlines():
        if line.startswith(_ESCAPED_FENCE):
            lines.append("---" + line[len(_ESCAPED_FENCE) :])
        else:
            lines.append(line)
    return "\n".join(lines)


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split into ``(yaml_text, body)``.

    A file without front matter is treated as "prompt only" — useful for a
    quick local mode. Everything else must be well-formed, because a silently
    ignored ``tools:`` list is worse than a loud error.
    """
    stripped = text.lstrip("\ufeff")
    lines = stripped.splitlines()
    if not lines or not _FENCE.match(lines[0]):
        return "", stripped.strip()

    end_meta = None
    for index in range(1, len(lines)):
        if _FENCE.match(lines[index]):
            end_meta = index
            break
    if end_meta is None:
        raise AgentPresetError("front matter is not closed with '---'")

    yaml_text = "\n".join(lines[1:end_meta])
    rest = lines[end_meta + 1 :]
    first_content = next((line for line in rest if line.strip()), None)
    if first_content is not None and _FENCE.match(first_content):
        raise AgentPresetError(
            "a second '---' block is no longer supported: put the Jinja2 "
            "template under the YAML `template:` key, and start a body line "
            "with '\\---' when you need a literal '---'"
        )
    return yaml_text, _unescape_body("\n".join(rest)).strip()


def parse_preset(path: Path, scope: str = "user") -> AgentPreset:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentPresetError(f"cannot read {path}: {exc}") from exc

    yaml_text, body = _split_frontmatter(text)

    meta: dict[str, Any] = {}
    if yaml_text.strip():
        try:
            loaded = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise AgentPresetError(f"invalid YAML: {exc}") from exc
        if loaded is not None and not isinstance(loaded, dict):
            raise AgentPresetError("front matter must be a mapping")
        meta = loaded or {}

    name = str(meta.get("name") or path.stem).strip()
    if not name:
        raise AgentPresetError("preset name is empty")

    tools = _as_globs(meta.get("tools"), "tools")
    access = _as_globs(meta.get("access"), "access")
    if access is not None:
        unknown = [mode for mode in access if mode not in ACCESS_MODES]
        if unknown:
            raise AgentPresetError(
                f"unknown access mode(s): {', '.join(unknown)}; allowed: {', '.join(ACCESS_MODES)}"
            )

    # Accepted and ignored: reasoning is always streamed now.
    memory = meta.get("memory")
    if memory is not None:
        memory = str(memory).strip().lower() or None
    if memory not in (None, "session"):
        raise AgentPresetError("`memory` must be `session` (or omitted)")

    workflow = meta.get("workflow")
    if workflow is not None:
        workflow = str(workflow).strip() or None
    if workflow is not None:
        try:
            WORKFLOWS.require(workflow)
        except ValueError as exc:
            raise AgentPresetError(str(exc)) from exc

    template = meta.get("template")
    if template is not None and not isinstance(template, str):
        raise AgentPresetError("`template` must be a string")
    template = template.strip() if isinstance(template, str) else ""
    if template:
        try:
            Template(template)
        except TemplateSyntaxError as exc:
            raise AgentPresetError(f"invalid Jinja2 template: {exc}") from exc

    known = {
        "name",
        "title",
        "description",
        "icon",
        "tools",
        "access",
        "workflow",
        "memory",
        "enable_thinking",
        "template",
    }
    extra = {key: value for key, value in meta.items() if key not in known}

    return AgentPreset(
        name=name,
        title=str(meta.get("title") or name),
        description=str(meta.get("description") or ""),
        icon=str(meta.get("icon") or "bot"),
        tools=tools,
        access=access,
        workflow=workflow,
        memory=memory,
        template=template or None,
        prompt=body or str(meta.get("prompt") or ""),
        path=path,
        scope=scope,
        extra=extra,
    )


def unknown_tool_patterns(preset: AgentPreset, available: Iterable[str]) -> list[str]:
    """Preset ``tools:`` entries that match nothing in the current tool pool.

    The pool is the **mixed** one — plugin tools and MCP tools together — because
    a preset does not care where a tool comes from: it names tools, and a name
    is a name. Checking only the built-in tools would report every MCP rule as a
    typo, which is exactly how a warning becomes noise people filter out.

    Reported, never enforced: a rule may legitimately wait for an MCP server
    that is not connected right now, and a stale rule that suddenly matches a
    future tool is harmless. The editor shows the list; the console gets one
    line per preset.
    """
    if preset.tools is None:
        return []
    names = [name for name in available if name]
    return [
        pattern
        for pattern in preset.tools
        if not any(fnmatch.fnmatchcase(name, pattern) for name in names)
    ]


def discover_sync(workspace: Path | None = None) -> list[AgentPreset]:
    """Collect presets; a workspace file shadows a built-in or user one by name."""
    PATHS.ensure()
    found: list[AgentPreset] = []
    seen: set[str] = set()
    for directory, scope in (
        (
            PATHS.workspace_agents if workspace is None else workspace / ".amrita" / "agents",
            "workspace",
        ),
        (PATHS.user_agents, "user"),
    ):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                preset = parse_preset(path, scope)
            except AgentPresetError as exc:
                logger.warning(f"Skipping agent preset {path}: {exc}")
                continue
            if preset.name in seen:
                continue
            seen.add(preset.name)
            found.append(preset)
    # Built-ins first; a file of the same name shadows one and keeps its place.
    return [preset for preset in BUILTIN_PRESETS if preset.name not in seen] + found


async def discover(workspace: Path | None = None) -> list[AgentPreset]:
    return await asyncio.to_thread(discover_sync, workspace)


def find(name: str, workspace: Path | None = None) -> AgentPreset | None:
    for preset in discover_sync(workspace):
        if preset.name == name:
            return preset
    return None


BUILTIN_STANDARD = AgentPreset(
    name="standard",
    title="标准模式",
    description="允许使用全部已启用的工具；工作流跟随全局设置。",
    icon="wrench",
    tools=None,
    access=None,
    workflow=None,
    scope="builtin",
)

# Shipped modes. They live in code, so only a same-named file can override one.
BUILTIN_PLANNER = AgentPreset(
    name="planner",
    title="计划模式",
    description="先拆解任务再动手，适合多步骤需求。只读，计划写在会话临时目录里。",
    icon="list-tree",
    tools=None,
    access=["readonly"],
    workflow="react-step",
    memory="session",
    scope="builtin",
    prompt=(
        "你是计划模式。收到需求后先列出步骤，再逐步执行；每一步说明依据。\n\n"
        "本预设使用步进 ReAct：任务会先被拆解成多步计划，再逐个执行，"
        "所以务必先说明整体思路再动手。\n"
        "当前会话是只读的：可以读取文件、搜索代码、翻目录，但不能修改工作区；"
        "这一模式不提供 shell，需要跑命令请换成具备写权限的预设。\n"
        "如果需要动手改代码，请在回答末尾提醒用户切换到具备写权限的 Agent 预设。\n\n"
        "## 计划写在哪里\n\n"
        "系统提示词里会给出本次会话的临时记忆目录。把拆解结果用 `memory_write` 存成 "
        "`plan.md`（步骤、依赖、验收标准），执行过程中用 `memory_write` 更新状态，"
        "需要时用 `memory_read` 读回，不要让计划只存在于对话里。"
        "这个目录随对话删除，不属于工作区，可以放心写；"
        "但不要用它保存源码或试图绕过只读限制。\n"
    ),
)

BUILTIN_CODER = AgentPreset(
    name="coder",
    title="编码模式",
    description="可读写工作区，工作流跟随全局设置。",
    icon="code",
    # No whitelist: a coder that cannot reach the files is not a coder.
    tools=None,
    access=["workspace", "full"],
    workflow=None,
    scope="builtin",
    prompt=(
        "你是编码模式，可以读写当前工作区中的文件。动手前先读相关代码，"
        "改完自己验证一遍。回答里不要重复贴出整份文件，说清楚改了什么、为什么改。\n"
    ),
)

BUILTIN_PRESETS: tuple[AgentPreset, ...] = (
    BUILTIN_STANDARD,
    BUILTIN_PLANNER,
    BUILTIN_CODER,
)

# Names reserved by the built-ins, so an override can be labelled as one.
BUILTIN_NAMES: frozenset[str] = frozenset(preset.name for preset in BUILTIN_PRESETS)


def resolve(name: str | None, workspace: Path | None = None) -> AgentPreset:
    """Look up a preset, falling back to the built-in standard mode."""
    if name:
        found = find(name, workspace)
        if found is not None:
            return found
    return BUILTIN_STANDARD


def to_public(preset: AgentPreset) -> dict[str, Any]:
    workflow: WorkflowSpec | None = WORKFLOWS.get(preset.workflow)
    return {
        "name": preset.name,
        "title": preset.title,
        "description": preset.description,
        "icon": preset.icon,
        "tools": preset.tools,
        "access": preset.access,
        "read_only": preset.read_only,
        "workflow": preset.workflow,
        "workflow_pinned": preset.workflow_pinned,
        "workflow_title": workflow.title if workflow else None,
        "workflow_mode": workflow.mode if workflow else None,
        "workflow_mode_label": workflow.mode_label if workflow else None,
        "memory": preset.memory,
        "scope": preset.scope,
        # True when this file overrides a built-in mode.
        "shadows_builtin": preset.scope != "builtin" and preset.name in BUILTIN_NAMES,
        "path": str(preset.path) if preset.path else None,
        "has_custom_template": bool(preset.template and preset.template.strip()),
        "has_custom_prompt": bool(preset.prompt.strip()),
    }
