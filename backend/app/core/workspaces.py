"""Workspace registry.

A workspace is just a directory the user can point the agent at. Keeping the
list here (rather than deriving it from the filesystem) means the switcher only
offers directories the user actually chose, and the backend can validate any
path a client sends before it is used for skill/agent discovery.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from pydantic import BaseModel

from app.core.config import PATHS, PROJECT_ROOT


class Workspace(BaseModel):
    name: str
    path: str
    added_at: float


_EXCLUDED = {"/", str(Path.home())}


def _entry(path: Path) -> Workspace:
    return Workspace(name=path.name or str(path), path=str(path), added_at=time.time())


def _load_sync() -> list[Workspace]:
    import json

    data = PATHS.workspaces_file
    if not data.exists():
        return [_entry(PROJECT_ROOT)]
    raw = json.loads(data.read_text(encoding="utf-8") or "[]")
    out: list[Workspace] = []
    for item in raw:
        try:
            out.append(Workspace.model_validate(item))
        except ValueError:
            continue
    if not any(w.path == str(PROJECT_ROOT) for w in out):
        out.insert(0, _entry(PROJECT_ROOT))
    return out


def _save_sync(items: list[Workspace]) -> None:
    import json

    PATHS.workspaces_file.parent.mkdir(parents=True, exist_ok=True)
    PATHS.workspaces_file.write_text(
        json.dumps([w.model_dump() for w in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def list_workspaces() -> list[dict]:
    items = await asyncio.to_thread(_load_sync)
    return [w.model_dump() for w in items]


async def add_workspace(raw: str) -> dict:
    candidate = Path(raw).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"目录不存在：{candidate}") from exc
    if not resolved.is_dir():
        raise ValueError("路径不是目录")
    if str(resolved) in _EXCLUDED:
        raise ValueError("不能把根目录或用户主目录作为工作区")

    def _work() -> dict:
        items = _load_sync()
        for item in items:
            if item.path == str(resolved):
                return item.model_dump()
        entry = _entry(resolved)
        items.append(entry)
        _save_sync(items)
        return entry.model_dump()

    return await asyncio.to_thread(_work)


async def remove_workspace(raw: str) -> bool:
    resolved = str(Path(raw).expanduser().resolve())
    if resolved == str(PROJECT_ROOT):
        raise ValueError("不能移除默认工作区")

    def _work() -> bool:
        items = _load_sync()
        remaining = [w for w in items if w.path != resolved]
        if len(remaining) == len(items):
            return False
        _save_sync(remaining)
        return True

    return await asyncio.to_thread(_work)
