"""Workspace file IO tools.

Until now the only way to touch a file was to shell out, which meant every read
cost a process and every write depended on the shell's quoting. These tools are
the boring, direct version: read, write, list, search — all confined to the
active workspace by the same resolution rule the shell uses.

Why four tools and not one `file` tool with an `op`: the model picks tools by
name, and `file_write` being absent in read-only mode is a clearer signal than
`file(op="write")` failing at runtime. The names also end in `_read` / `_list` /
`_search`, which is what :func:`app.capabilities.permissions.tool_allowed_for_mode` uses to
keep them in a read-only run — `file_write` does not match, so it is dropped.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from amrita_core.tools.models import (
    FunctionDefinitionSchema,
    FunctionParametersSchema,
    FunctionPropertySchema,
    ToolData,
    ToolFunctionSchema,
)
from pydantic import BaseModel, Field

from app.plugins import PluginContext, PluginRuntime, ToolSource, load_config


class FileConfig(BaseModel):
    max_read_kb: int = Field(
        default=256,
        ge=4,
        le=8192,
        title="单次读取上限（KiB）",
        description="超出部分不返回，并提示用 offset/limit 继续读。",
    )
    max_list_entries: int = Field(
        default=400,
        ge=10,
        le=5000,
        title="目录列举上限",
        description="一次最多列出多少条目，超出只给前若干条与总数。",
    )
    max_search_results: int = Field(
        default=80,
        ge=1,
        le=500,
        title="搜索命中上限",
        description="一次搜索最多返回多少行匹配。",
    )
    allow_outside_workspace: bool = Field(
        default=False,
        title="允许越过工作区",
        description="关闭时所有路径必须位于当前工作区内。",
    )


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


def _root(context: Any) -> Path:
    raw = getattr(getattr(context, "settings", None), "active_workspace", None)
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    return Path.cwd().resolve()


def _resolve(root: Path, requested: str | None, allow_outside: bool) -> Path:
    """Resolve ``requested`` against the workspace, refusing to escape it."""
    value = (requested or "").strip() or "."
    candidate = Path(value).expanduser()
    base = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = base.resolve()
    except OSError as exc:
        raise ValueError(f"cannot resolve {base}: {exc}") from exc
    if not allow_outside and resolved != root and root not in resolved.parents:
        raise ValueError(
            f"{resolved} is outside the workspace; enable 「允许越过工作区」 "
            "in the files plugin settings to work there"
        )
    return resolved


async def _config() -> FileConfig:
    """Read the plugin config at call time.

    Tool factories are sync (``ToolSource.build``), so the config — which is
    loaded from disk and can be edited between turns — is read here instead of
    being baked in when the pool is assembled.
    """
    loaded = await load_config("files", FileConfig)
    return loaded if isinstance(loaded, FileConfig) else FileConfig()


def _read_tool() -> ToolData:
    async def run(ctx: Any) -> str:
        config = await _config()
        data = ctx.data
        try:
            path = _resolve(_root(ctx), data.get("path"), config.allow_outside_workspace)
        except ValueError as exc:
            return f"error: {exc}"
        if not path.is_file():
            return f"error: {path} is not a file"
        offset = max(1, int(data.get("offset") or 1))
        limit = max(1, int(data.get("limit") or 400))
        try:
            raw = path.read_bytes()[: config.max_read_kb * 1024 + 1]
        except OSError as exc:
            return f"error: {exc}"
        truncated = len(raw) > config.max_read_kb * 1024
        text = raw[: config.max_read_kb * 1024].decode("utf-8", "replace")
        lines = text.splitlines()
        window = lines[offset - 1 : offset - 1 + limit]
        numbered = "\n".join(f"{offset + index:>6}| {line}" for index, line in enumerate(window))
        header = f"path: {path}\nlines: {offset}-{offset + len(window) - 1} of {len(lines)}"
        if truncated:
            header += f"\nnote: file truncated at {config.max_read_kb} KiB; use offset/limit"
        return f"{header}\n\n{numbered}" if numbered else f"{header}\n\n(empty)"

    return ToolData(
        data=_schema(
            "file_read",
            "Read a UTF-8 text file inside the workspace. Returns numbered lines "
            "so a later edit can quote them.",
            {
                "path": FunctionPropertySchema(type="string", description="File path."),
                "offset": FunctionPropertySchema(
                    type="integer", description="First line to return (1-based)."
                ),
                "limit": FunctionPropertySchema(
                    type="integer", description="How many lines to return."
                ),
            },
            ["path"],
        ),
        func=run,
        custom_run=True,
    )


def _write_tool() -> ToolData:
    async def run(ctx: Any) -> str:
        config = await _config()
        data = ctx.data
        try:
            path = _resolve(_root(ctx), data.get("path"), config.allow_outside_workspace)
        except ValueError as exc:
            return f"error: {exc}"
        content = str(data.get("content") or "")
        append = bool(data.get("append"))
        if path.is_dir():
            return f"error: {path} is a directory"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if append:
                with path.open("a", encoding="utf-8", newline="") as handle:
                    handle.write(content)
            else:
                path.write_text(content, encoding="utf-8", newline="")
        except OSError as exc:
            return f"error: {exc}"
        return f"wrote {len(content)} chars to {path}" + (" (append)" if append else "")

    return ToolData(
        data=_schema(
            "file_write",
            "Write a UTF-8 text file inside the workspace. Creates parent "
            "directories. Use append=true to add to the end.",
            {
                "path": FunctionPropertySchema(type="string", description="File path."),
                "content": FunctionPropertySchema(type="string", description="Full text."),
                "append": FunctionPropertySchema(
                    type="boolean", description="Append instead of replacing."
                ),
            },
            ["path", "content"],
        ),
        func=run,
        custom_run=True,
    )


def _list_tool() -> ToolData:
    async def run(ctx: Any) -> str:
        config = await _config()
        data = ctx.data
        try:
            path = _resolve(_root(ctx), data.get("path"), config.allow_outside_workspace)
        except ValueError as exc:
            return f"error: {exc}"
        if not path.is_dir():
            return f"error: {path} is not a directory"
        depth = max(1, min(int(data.get("depth") or 1), 4))
        entries: list[str] = []
        total = 0

        def walk(directory: Path, level: int) -> None:
            nonlocal total
            try:
                children = sorted(
                    directory.iterdir(), key=lambda item: (item.is_file(), item.name.lower())
                )
            except OSError:
                return
            for child in children:
                total += 1
                if len(entries) < config.max_list_entries:
                    size = ""
                    if child.is_file():
                        try:
                            size = f"  {child.stat().st_size:>9} B"
                        except OSError:
                            size = ""
                    entries.append(
                        f"{'  ' * (level - 1)}{child.name}{'/' if child.is_dir() else ''}{size}"
                    )
                if child.is_dir() and level < depth:
                    walk(child, level + 1)

        walk(path, 1)
        header = f"path: {path}\nentries: {total}"
        if total > len(entries):
            header += f" (showing first {len(entries)})"
        return header + "\n\n" + "\n".join(entries)

    return ToolData(
        data=_schema(
            "file_list",
            "List a directory inside the workspace, directories first. depth=2..4 walks deeper.",
            {
                "path": FunctionPropertySchema(
                    type="string", description="Directory; omit for the workspace root."
                ),
                "depth": FunctionPropertySchema(
                    type="integer", description="Recursion depth, 1-4."
                ),
            },
            [],
        ),
        func=run,
        custom_run=True,
    )


def _search_tool() -> ToolData:
    async def run(ctx: Any) -> str:
        config = await _config()
        data = ctx.data
        needle = str(data.get("pattern") or "")
        if not needle:
            return "error: pattern is required"
        try:
            root = _resolve(_root(ctx), data.get("path"), config.allow_outside_workspace)
        except ValueError as exc:
            return f"error: {exc}"
        glob = str(data.get("glob") or "").strip()
        case_sensitive = bool(data.get("case_sensitive"))
        haystack_needle = needle if case_sensitive else needle.lower()
        hits: list[str] = []
        scanned = 0
        for file in root.rglob("*"):
            if len(hits) >= config.max_search_results:
                break
            if not file.is_file():
                continue
            if glob and not fnmatch.fnmatch(file.name, glob):
                continue
            # Skip the two directories that make every search useless.
            if any(part in {".git", "node_modules", "__pycache__"} for part in file.parts):
                continue
            try:
                if file.stat().st_size > 512 * 1024:
                    continue
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            for number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.lower()
                if haystack_needle in haystack:
                    hits.append(f"{file.relative_to(root)}:{number}: {line.strip()[:200]}")
                    if len(hits) >= config.max_search_results:
                        break
        if not hits:
            return f"no matches for {needle!r} under {root} (scanned {scanned} files)"
        return f"matches: {len(hits)} (scanned {scanned} files)\n\n" + "\n".join(hits)

    return ToolData(
        data=_schema(
            "file_search",
            "Search file contents under the workspace and return file:line hits.",
            {
                "pattern": FunctionPropertySchema(
                    type="string", description="Substring to look for."
                ),
                "path": FunctionPropertySchema(
                    type="string", description="Directory to search; omit for the root."
                ),
                "glob": FunctionPropertySchema(
                    type="string", description="Only match these file names, e.g. *.py."
                ),
                "case_sensitive": FunctionPropertySchema(
                    type="boolean", description="Default false."
                ),
            },
            ["pattern"],
        ),
        func=run,
        custom_run=True,
    )


def _build(_context: Any) -> list[ToolData]:
    """All four tools; ``file_write`` is dropped by the access-mode filter.

    Returning it here rather than gating it on ``access_mode`` keeps this
    function independent of the run — the catalog needs to *list* every tool,
    including the one a read-only run will not get.
    """
    return [
        _read_tool(),
        _list_tool(),
        _search_tool(),
        _write_tool(),
    ]


async def files_plugin(ctx: PluginContext):
    """Register the workspace file tools."""
    ctx.config(FileConfig)
    ctx.tool(
        ToolSource(
            name="files",
            description="工作区文件读写、列目录、搜内容",
            build=_build,
        )
    )
    yield


def install_files_plugin(runtime: PluginRuntime) -> None:
    runtime.register(
        files_plugin,
        name="files",
        description="文件 IO 工具（file_read / file_list / file_search / file_write）",
    )


__all__ = ["FileConfig", "files_plugin", "install_files_plugin"]
