"""Per-session scratch directory for plans and working notes.

A preset opts in with ``memory: session``; the directory is
``~/.amrita/sessions/<id>/memory`` — inside the session, so it is deleted with
the conversation, but outside the workspace, so a read-only preset can still
keep notes.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from amrita_core.tools.models import (
    FunctionDefinitionSchema,
    FunctionParametersSchema,
    FunctionPropertySchema,
    ToolData,
    ToolFunctionSchema,
)

from app.core.config import PATHS

# Refuse to grow a scratch file past this; a plan is never 128 KiB.
MAX_BYTES = 128 * 1024

MEMORY_TOOL_NAMES: frozenset[str] = frozenset(
    {"memory_write", "memory_read", "memory_list", "memory_delete"}
)

_NAME_ARG = "name"
_CONTENT_ARG = "content"


def memory_dir(session_id: str) -> Path:
    """The scratch directory of one session (not created by this call)."""
    if "/" in session_id or "\\" in session_id or session_id in {"", ".", ".."}:
        raise ValueError("invalid session id")
    return PATHS.sessions / session_id / "memory"


def ensure(session_id: str) -> Path:
    directory = memory_dir(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def clear(session_id: str) -> None:
    """Drop the notes but keep the session itself."""
    shutil.rmtree(memory_dir(session_id), ignore_errors=True)


def describe(session_id: str) -> str:
    """The prompt section that tells the agent where to write."""
    return (
        "## 会话记忆（临时）\n\n"
        f"本次会话有一个临时目录：`{memory_dir(session_id)}`\n\n"
        "把计划、拆解、待办、阶段性结论写在这里（`memory_write`），下一轮或别的 Agent "
        "可以读回来（`memory_read` / `memory_list`）。它随对话删除而删除，不属于工作区："
        "改代码仍然走工作区工具，不要把源码复制进来。\n"
    )


def _target(root: Path, raw: Any) -> Path:
    """Resolve ``raw`` under ``root``; anything else is an error."""
    name = str(raw or "").strip()
    if not name:
        raise ValueError("name is required")
    if name in {".", ".."} or ".." in name or "/" in name or "\\" in name:
        raise ValueError("name must be a plain file name inside the memory directory")
    candidate = (root / name).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("name escapes the memory directory")
    return candidate


def _schema(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> ToolFunctionSchema:
    return ToolFunctionSchema(
        type="function",
        strict=True,
        function=FunctionDefinitionSchema(
            name=name,
            description=description,
            parameters=FunctionParametersSchema(
                type="object",
                properties=properties,
                required=required,
            ),
        ),
    )


def _root(context: Any) -> Path | None:
    session_id = getattr(context, "session_id", None)
    if not session_id:
        return None
    try:
        return memory_dir(str(session_id))
    except ValueError:
        return None


def build_tools(context: Any) -> list[ToolData]:
    """Memory tools, or nothing when the preset did not opt in."""
    agent = getattr(context, "agent", None)
    if getattr(agent, "memory", None) != "session":
        return []

    async def write(ctx: Any) -> str:
        root = _root(ctx)
        if root is None:
            return "error: this run has no session memory directory"
        try:
            target = _target(root, ctx.data.get(_NAME_ARG))
        except ValueError as exc:
            return f"error: {exc}"
        content = str(ctx.data.get(_CONTENT_ARG) or "")
        if len(content.encode("utf-8")) > MAX_BYTES:
            return f"error: content exceeds {MAX_BYTES} bytes; split it into several files"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {target.name} ({len(content)} chars)"

    async def read(ctx: Any) -> str:
        root = _root(ctx)
        if root is None:
            return "error: this run has no session memory directory"
        try:
            target = _target(root, ctx.data.get(_NAME_ARG))
        except ValueError as exc:
            return f"error: {exc}"
        if not target.is_file():
            return f"error: {target.name} does not exist; call memory_list first"
        return target.read_text(encoding="utf-8", errors="replace")

    async def listing(ctx: Any) -> str:
        root = _root(ctx)
        if root is None:
            return "error: this run has no session memory directory"
        if not root.is_dir():
            return "memory is empty"
        rows = sorted(
            (item for item in root.iterdir() if item.is_file()),
            key=lambda item: item.name,
        )
        if not rows:
            return "memory is empty"
        lines = [f"{root}", ""]
        lines.extend(f"- {item.name} ({item.stat().st_size} bytes)" for item in rows)
        return "\n".join(lines)

    async def delete(ctx: Any) -> str:
        root = _root(ctx)
        if root is None:
            return "error: this run has no session memory directory"
        try:
            target = _target(root, ctx.data.get(_NAME_ARG))
        except ValueError as exc:
            return f"error: {exc}"
        if not target.is_file():
            return f"error: {target.name} does not exist"
        target.unlink()
        return f"deleted {target.name}"

    return [
        ToolData(
            data=_schema(
                "memory_write",
                "Write a file into this session's scratch directory. Use it for "
                "plans, checklists and intermediate findings you want to survive "
                "the turn. Replaces the whole file.",
                {
                    _NAME_ARG: FunctionPropertySchema(
                        type="string",
                        description="Plain file name, e.g. plan.md.",
                    ),
                    _CONTENT_ARG: FunctionPropertySchema(
                        type="string",
                        description="Full file content (markdown is fine).",
                    ),
                },
                [_NAME_ARG, _CONTENT_ARG],
            ),
            func=write,
            custom_run=True,
        ),
        ToolData(
            data=_schema(
                "memory_read",
                "Read a file from this session's scratch directory.",
                {
                    _NAME_ARG: FunctionPropertySchema(
                        type="string",
                        description="Plain file name previously written.",
                    ),
                },
                [_NAME_ARG],
            ),
            func=read,
            custom_run=True,
        ),
        ToolData(
            data=_schema(
                "memory_list",
                "List the files in this session's scratch directory.",
                {},
                [],
            ),
            func=listing,
            custom_run=True,
        ),
        ToolData(
            data=_schema(
                "memory_delete",
                "Delete a file from this session's scratch directory.",
                {
                    _NAME_ARG: FunctionPropertySchema(
                        type="string",
                        description="Plain file name to remove.",
                    ),
                },
                [_NAME_ARG],
            ),
            func=delete,
            custom_run=True,
        ),
    ]


__all__ = [
    "MAX_BYTES",
    "MEMORY_TOOL_NAMES",
    "build_tools",
    "clear",
    "describe",
    "ensure",
    "memory_dir",
]
