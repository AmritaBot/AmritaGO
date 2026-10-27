"""Small async file helpers: atomic JSON writes and JSONL appends."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _atomic_write_sync(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_json_sync(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    # ``utf-8-sig`` tolerates the BOM that hand-edited files often carry.
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json_sync(path: Path, data: Any) -> None:
    _atomic_write_sync(path, json.dumps(data, ensure_ascii=False, indent=2))


def append_jsonl_sync(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl_sync(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip().lstrip("\ufeff")
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def write_jsonl_sync(path: Path, records: list[dict[str, Any]]) -> None:
    """Replace a JSONL file in one atomic step.

    Appending is the normal path; rewriting is for the cases where history is
    rolled back, and a half-written transcript there would be worse than a
    stale one.
    """
    payload = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    _atomic_write_sync(path, payload)


async def read_json(path: Path, default: Any = None) -> Any:
    return await asyncio.to_thread(read_json_sync, path, default)


async def write_json(path: Path, data: Any) -> None:
    await asyncio.to_thread(write_json_sync, path, data)


async def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    await asyncio.to_thread(append_jsonl_sync, path, record)


async def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return await asyncio.to_thread(read_jsonl_sync, path)


async def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    await asyncio.to_thread(write_jsonl_sync, path, records)
