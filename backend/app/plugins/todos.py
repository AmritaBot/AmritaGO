"""The session to-do list.

A plan the model writes once and then progressively ticks off, which is the
single cheapest way to keep a long task from drifting (and the reason
``react-step`` produces better long-run behaviour than a bare ReAct loop).

Storage is per session — ``~/.amrita/sessions/<id>/todos.json`` — because a
plan belongs to the conversation that made it, and it should disappear with it.
Two properties matter:

* **Full replace is the primitive.** ``todo_write`` takes the complete list, so
  the model never has to reason about patch ordering; ``todo_update`` exists for
  the incremental case ("mark 3 done") but is built on the same file.
* **Every change is pushed.** The tool publishes a ``todos`` event into the
  session stream, so the panel updates without polling and a reconnect replays
  the list to wherever it is now.

The tools are in ``_READ_ONLY_TOOL_NAMES``'s spirit but not its list: they write
session state, never the workspace, so a read-only preset keeps them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from amrita_core.tools.models import (
    FunctionDefinitionSchema,
    FunctionParametersSchema,
    FunctionPropertySchema,
    ToolData,
    ToolFunctionSchema,
)
from amrita_sense.logging import logger
from pydantic import BaseModel, Field

from app.core.config import PATHS
from app.plugins import PluginContext, PluginRuntime, ToolSource
from app.runtime.stream_hub import HUB

MAX_ITEMS = 100


class TodoItem(BaseModel):
    id: str
    text: str
    done: bool = False


class TodoList(BaseModel):
    title: str = "待办事项"
    items: list[TodoItem] = Field(default_factory=list)

    @property
    def done_count(self) -> int:
        return sum(1 for item in self.items if item.done)

    def to_public(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "items": [item.model_dump() for item in self.items],
            "total": len(self.items),
            "done": self.done_count,
        }

    def as_text(self) -> str:
        if not self.items:
            return f"{self.title}: (empty)"
        header = f"{self.title} ({self.done_count}/{len(self.items)})"
        lines = [f"[{'x' if item.done else ' '}] {item.id}. {item.text}" for item in self.items]
        return "\n".join([header, *lines])


def todo_path(session_id: str) -> Path:
    return PATHS.data / "sessions" / session_id / "todos.json"


def load_sync(session_id: str) -> TodoList:
    path = todo_path(session_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return TodoList()
    try:
        return TodoList.model_validate(raw)
    except ValueError:
        logger.warning(f"Ignoring malformed todo list at {path}")
        return TodoList()


def save_sync(session_id: str, todos: TodoList) -> None:
    path = todo_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(todos.model_dump_json(indent=2), encoding="utf-8")


def clear(session_id: str) -> None:
    todo_path(session_id).unlink(missing_ok=True)


def _renumber(items: list[TodoItem]) -> list[TodoItem]:
    """Stable short ids: the model says "3" instead of quoting a uuid."""
    return [
        TodoItem(id=str(index + 1), text=item.text, done=item.done)
        for index, item in enumerate(items[:MAX_ITEMS])
    ]


#: Item shape for the array arguments, declared once to avoid drift.
_ITEM_TEXT = FunctionPropertySchema(type="string", description="One item's text.")


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]):
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


async def _publish(session_id: str | None, todos: TodoList) -> None:
    if not session_id:
        return
    channel = HUB.latest(session_id)
    if channel is None:
        return
    await channel.publish({"type": "todos", "todos": todos.to_public()})


def _parse_items(raw: Any) -> list[TodoItem]:
    items: list[TodoItem] = []
    for entry in raw or []:
        if isinstance(entry, str):
            items.append(TodoItem(id="", text=entry.strip(), done=False))
        elif isinstance(entry, dict):
            text = str(entry.get("text") or entry.get("title") or "").strip()
            if text:
                items.append(
                    TodoItem(
                        id="", text=text, done=bool(entry.get("done") or entry.get("completed"))
                    )
                )
    return [item for item in items if item.text]


def _write_tool() -> ToolData:
    async def run(ctx: Any) -> str:
        session_id = getattr(ctx, "session_id", None)
        if not session_id:
            return "error: the to-do list needs a session"
        data = ctx.data
        if "todos" not in data or data.get("todos") is None:
            # An explicit empty array wipes the list; a missing one is a mistake.
            return "error: todos is required (pass an empty array to clear the list)"
        items = _parse_items(data.get("todos"))
        title = str(data.get("title") or "").strip() or "待办事项"
        todos = TodoList(title=title, items=_renumber(items))
        save_sync(session_id, todos)
        await _publish(session_id, todos)
        return todos.as_text()

    return ToolData(
        data=_schema(
            "todo_write",
            "Replace the whole to-do list for this conversation. Use it to lay "
            "out a plan up front, and to rewrite it when the plan changes. "
            "Passing an empty array clears the list.",
            {
                "todos": FunctionPropertySchema(
                    type="array",
                    items=_ITEM_TEXT,
                    description='Full list, e.g. ["read the config", "patch the parser"].',
                ),
                "title": FunctionPropertySchema(
                    type="string", description="Heading; defaults to 待办事项."
                ),
            },
            ["todos"],
        ),
        func=run,
        custom_run=True,
    )


def _update_tool() -> ToolData:
    async def run(ctx: Any) -> str:
        session_id = getattr(ctx, "session_id", None)
        if not session_id:
            return "error: the to-do list needs a session"
        data = ctx.data
        todos = load_sync(session_id)
        if not todos.items:
            return "error: there is no list yet; call todo_write first"

        by_id = {item.id: item for item in todos.items}
        changed: list[str] = []

        for raw_id in data.get("mark_done") or []:
            item = by_id.get(str(raw_id))
            if item is not None and not item.done:
                item.done = True
                changed.append(f"done {item.id}")
        for raw_id in data.get("mark_open") or []:
            item = by_id.get(str(raw_id))
            if item is not None and item.done:
                item.done = False
                changed.append(f"reopen {item.id}")

        removed = {str(raw_id) for raw_id in data.get("remove") or []}
        kept = [item for item in todos.items if item.id not in removed]
        if removed:
            changed.append(f"removed {len(todos.items) - len(kept)}")

        for entry in _parse_items(data.get("add")):
            kept.append(entry)
            changed.append(f"added {entry.text[:30]}")
        if data.get("title"):
            todos.title = str(data["title"]).strip() or todos.title

        if not changed:
            return f"error: nothing to do; pass mark_done / mark_open / remove / add\n\n{todos.as_text()}"

        todos.items = _renumber(kept)
        save_sync(session_id, todos)
        await _publish(session_id, todos)
        return todos.as_text()

    return ToolData(
        data=_schema(
            "todo_update",
            "Change the existing to-do list: tick items off, reopen them, "
            "remove them, or append new ones. Ids are the numbers shown by "
            "todo_write.",
            {
                "mark_done": FunctionPropertySchema(
                    type="array",
                    items=_ITEM_TEXT,
                    description="Ids to mark completed.",
                ),
                "mark_open": FunctionPropertySchema(
                    type="array",
                    items=_ITEM_TEXT,
                    description="Ids to mark unfinished.",
                ),
                "remove": FunctionPropertySchema(
                    type="array",
                    items=_ITEM_TEXT,
                    description="Ids to delete.",
                ),
                "add": FunctionPropertySchema(
                    type="array",
                    items=_ITEM_TEXT,
                    description="New item texts to append.",
                ),
                "title": FunctionPropertySchema(type="string", description="Optional new title."),
            },
            [],
        ),
        func=run,
        custom_run=True,
    )


def _build(_context: Any) -> list[ToolData]:
    return [_write_tool(), _update_tool()]


async def todos_plugin(ctx: PluginContext):
    """Register the plan tools."""
    ctx.tool(
        ToolSource(
            name="todos",
            description="会话内的待办清单",
            build=_build,
        )
    )
    yield


def install_todos_plugin(runtime: PluginRuntime) -> None:
    runtime.register(
        todos_plugin,
        name="todos",
        description="待办清单（todo_write / todo_update），与计划面板联动",
    )


__all__ = [
    "TodoItem",
    "TodoList",
    "clear",
    "install_todos_plugin",
    "load_sync",
    "save_sync",
    "todo_path",
    "todos_plugin",
]
