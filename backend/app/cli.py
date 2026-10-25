"""Command line: serve the app, or work on its data.

Global flags override the environment variables the app reads *at import time*,
so they are applied before any ``app.*`` import — that is why every application
import in this module is inside a function. A module-level import here would
freeze ``AMRITA_GO_PORT`` (and the credential key) before ``--port``/``--secret``
had a chance to take effect.

Subcommands are deliberately data operations, not a second UI: ``export`` reads
one session out to a file, ``rotate-key`` re-encrypts the credential store.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    # ``SUPPRESS`` keeps a subparser from overwriting a flag given before the
    # subcommand with its own default (the classic ``parents=`` gotcha).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default=argparse.SUPPRESS, help="监听地址（覆盖 AMRITA_GO_HOST）")
    common.add_argument(
        "--port", type=int, default=argparse.SUPPRESS, help="监听端口（覆盖 AMRITA_GO_PORT）"
    )
    common.add_argument(
        "--secret",
        default=argparse.SUPPRESS,
        help="凭据加密密钥（覆盖 AMRITA_GO_SECRET，Base64 的 32 字节 Fernet key）",
    )
    common.add_argument("--data", default=argparse.SUPPRESS, help="数据目录（覆盖 AMRITA_GO_DATA）")
    common.add_argument(
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="开发者模式（AMRITA_GO_DEBUG=1）",
    )

    parser = argparse.ArgumentParser(
        prog="amrita-go",
        description="AmritaGO 本地 Agent 工作台。不带子命令时启动服务。",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command")

    export = sub.add_parser(
        "export",
        parents=[common],
        help="导出某个会话",
        description="把一个会话导出成 Markdown 或 JSON。",
    )
    export.add_argument(
        "session",
        help="会话 ID、唯一前缀、标题，或 latest（最近更新的会话）",
    )
    export.add_argument(
        "-f",
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="输出格式（默认 markdown）",
    )
    export.add_argument(
        "-o",
        "--output",
        default=None,
        help="输出文件；- 表示写到标准输出（默认 ./amrita-<会话ID>.<扩展名>）",
    )

    rotate = sub.add_parser(
        "rotate-key",
        parents=[common],
        help="轮换凭据加密密钥",
        description=(
            "生成新密钥，并用它重新加密已保存的 Provider 凭据。"
            "当前密钥来自 AMRITA_GO_SECRET 时拒绝执行——环境变量永远优先，"
            "写到磁盘的新密钥不会被使用。"
        ),
    )
    rotate.add_argument("--yes", "-y", action="store_true", help="不再交互确认")

    return parser


def apply_env(args: argparse.Namespace) -> None:
    """Copy flag overrides into the environment before ``app`` is imported."""
    mapping = {
        "host": "AMRITA_GO_HOST",
        "port": "AMRITA_GO_PORT",
        "secret": "AMRITA_GO_SECRET",
        "data": "AMRITA_GO_DATA",
    }
    for attr, env in mapping.items():
        value = getattr(args, attr, None)
        if value is not None:
            os.environ[env] = str(value)
    if getattr(args, "debug", False):
        os.environ["AMRITA_GO_DEBUG"] = "1"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    apply_env(args)
    command = getattr(args, "command", None)
    if command == "export":
        return export_session(args)
    if command == "rotate-key":
        return rotate_key(args)
    return serve()


def serve() -> int:
    import uvicorn

    from app.core.config import HOST, PORT
    from app.core.logging_setup import setup_logging

    # Install before ``uvicorn.run`` so startup lines are already formatted.
    setup_logging()

    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
        # Protocol pings time out while the dev proxy bundles; the app-level keepalive replaces them.
        ws_ping_interval=None,
        ws_ping_timeout=None,
    )
    return 0


# export


def _timestamp(value: float | None) -> str:
    if not value:
        return "-"
    return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")


async def _resolve_session(selector: str) -> tuple[Any, list[Any]]:
    from app.runtime.sessions import get_meta, list_sessions, load_history

    meta = await get_meta(selector)
    if meta is None:
        sessions = await list_sessions(include_archived=True)
        if selector in {"latest", "last"}:
            meta = max(sessions, key=lambda item: item.updated_at, default=None)
        else:
            matches = [
                item for item in sessions if item.id.startswith(selector) or item.title == selector
            ]
            if len(matches) > 1:
                ids = ", ".join(item.id for item in matches[:5])
                raise LookupError(f"选择器 {selector!r} 匹配到多个会话：{ids}…")
            meta = matches[0] if matches else None
    if meta is None:
        raise LookupError(f"没有找到会话 {selector!r}")
    return meta, await load_history(meta.id)


def _render_markdown(meta: Any, entries: list[Any]) -> str:
    lines: list[str] = [f"# {meta.title or meta.id}", ""]
    lines += [
        f"- 会话 ID：`{meta.id}`",
        f"- 创建：{_timestamp(meta.created_at)}",
        f"- 更新：{_timestamp(meta.updated_at)}",
        f"- 模型：{meta.preset or '-'}",
        f"- Agent：{meta.agent or '-'}",
        f"- 消息数：{meta.message_count}",
        f"- 用量：{meta.usage.total_tokens} tokens · {meta.usage.cost:.4f} {meta.usage.currency}",
        "",
    ]
    roles = {"user": "用户", "assistant": "助手", "system": "系统", "tool": "工具"}
    for entry in entries:
        lines += [f"## {roles.get(entry.role, entry.role)} · {_timestamp(entry.created_at)}", ""]
        if entry.content:
            lines += [entry.content.strip(), ""]
        if entry.reasoning:
            lines += [
                "<details><summary>思考过程</summary>",
                "",
                entry.reasoning.strip(),
                "",
                "</details>",
                "",
            ]
        for event in entry.events or []:
            text = str(event.get("content") or "").strip()
            if not text:
                continue
            kind = str((event.get("metadata") or {}).get("type") or "event")
            first = text.splitlines()[0][:200]
            lines.append(f"- `{kind}` {first}")
        if entry.events:
            lines.append("")
        if entry.error:
            lines += ["> **错误**：" + entry.error.replace("\n", "\n> "), ""]
    return "\n".join(lines).rstrip() + "\n"


def export_session(args: argparse.Namespace) -> int:
    from app.core.config import PATHS

    try:
        meta, entries = asyncio.run(_resolve_session(args.session))
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.format == "json":
        content = json.dumps(
            {
                "session": meta.model_dump(mode="json"),
                "entries": [entry.model_dump(mode="json") for entry in entries],
            },
            ensure_ascii=False,
            indent=2,
        )
        extension = "json"
    else:
        content = _render_markdown(meta, entries)
        extension = "md"

    if args.output == "-":
        sys.stdout.write(content)
        return 0

    target = Path(args.output or f"amrita-{meta.id}.{extension}").expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"已导出 {len(entries)} 条记录到 {target}")
    print(f"数据目录：{PATHS.data}")
    return 0


# rotate-key


def rotate_key(args: argparse.Namespace) -> int:
    from cryptography.fernet import Fernet, InvalidToken

    from app.core.config import PATHS
    from app.core.crypto import (
        ENV_SECRET,
        SecretError,
        backup_key,
        describe_key,
        load_key,
        new_key,
        store_key,
    )

    if (os.environ.get(ENV_SECRET) or "").strip():
        print(
            f"拒绝执行：{ENV_SECRET} 已设置，读取密钥时环境变量永远优先。\n"
            "请先取消该环境变量（或换成新值）再轮换磁盘上的密钥。",
            file=sys.stderr,
        )
        return 1

    try:
        info = describe_key(PATHS.data)
        old = load_key(PATHS.data)
    except SecretError as exc:
        print(f"无法读取当前密钥：{exc}", file=sys.stderr)
        return 1

    credential_paths = sorted(PATHS.providers.glob("*/credentials.enc"))
    plaintext: dict[Path, bytes] = {}
    for path in credential_paths:
        try:
            plaintext[path] = Fernet(old).decrypt(path.read_bytes())
        except (InvalidToken, OSError) as exc:
            # Stop before touching anything: a half-rotated store is worse than
            # an unchanged one.
            print(f"无法用当前密钥解密 {path}：{exc}\n未做任何改动。", file=sys.stderr)
            return 1

    if not args.yes:
        if not sys.stdin.isatty():
            print("非交互环境请加 --yes 确认轮换。", file=sys.stderr)
            return 1
        print(f"当前密钥来源：{info.source}（{info.detail}）")
        print(f"将重新加密 {len(plaintext)} 个 Provider 凭据，并替换密钥。")
        if input("继续？[y/N] ").strip().lower() not in {"y", "yes"}:
            print("已取消")
            return 1

    new = new_key()
    # Encrypt everything to temporary files first: if this fails, the old key and
    # every credential file are still untouched.
    staged: list[tuple[Path, Path]] = []
    try:
        for path, payload in plaintext.items():
            tmp = path.with_name(f".{path.name}.rotating")
            tmp.write_bytes(Fernet(new).encrypt(payload))
            staged.append((path, tmp))
    except OSError as exc:
        for _, tmp in staged:
            tmp.unlink(missing_ok=True)
        print(f"写入临时文件失败，未做任何改动：{exc}", file=sys.stderr)
        return 1

    backup = backup_key(PATHS.data, old) if info.source == "file" else None
    try:
        stored = store_key(PATHS.data, new)
    except OSError as exc:
        for _, tmp in staged:
            tmp.unlink(missing_ok=True)
        print(f"写入新密钥失败，未做任何改动：{exc}", file=sys.stderr)
        return 1

    replaced = 0
    try:
        for path, tmp in staged:
            os.replace(tmp, path)
            replaced += 1
    except OSError as exc:
        remaining = [str(path) for path, _ in staged[replaced:]]
        print(
            f"替换凭据时失败：{exc}\n"
            f"已替换 {replaced}/{len(staged)} 个。旧密钥备份：{backup or '（无，密钥来自钥匙串）'}\n"
            "未替换的文件仍用旧密钥加密，可用备份密钥手工解密：\n  " + "\n  ".join(remaining),
            file=sys.stderr,
        )
        return 1

    for path in plaintext:
        try:
            Fernet(new).decrypt(path.read_bytes())
        except (InvalidToken, OSError) as exc:  # pragma: no cover - disk failure
            print(f"轮换后校验失败：{path}：{exc}", file=sys.stderr)
            return 1

    print(f"已轮换密钥（新密钥来源：{stored.source}，{stored.detail}）")
    print(f"已重新加密 {replaced} 个 Provider 凭据")
    if backup is not None:
        print(f"旧密钥备份：{backup}（确认一切正常后可删除）")
    print("正在运行的 AmritaGO 进程请重启一次，避免仍在内存中的旧密钥继续使用。")
    return 0


__all__ = ["apply_env", "build_parser", "export_session", "main", "rotate_key", "serve"]
