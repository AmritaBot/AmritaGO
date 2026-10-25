"""Slash commands the runtime dispatches before starting an agent turn.

A command is a named handler that runs *inside* the turn's stream: the user's
message is still written to history and everything the handler emits lands in
the same trace, so ``/compact`` reads like any other step instead of a hidden
side effect behind a REST call.

Registration follows the plugin lifecycle. Built-ins live here; a plugin
declares more with ``ctx.command(...)``, and the plugin runtime installs the
declared set into this registry on activation — so a command disappears with
the plugin that provided it, with no unregister call to forget.

Only stdlib is imported at module level: the plugin runtime imports this file,
and pulling the agent stack in here would make that a cycle.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.settings import AppSettings
    from app.runtime.stream_hub import StreamChannel

CommandHandler = Callable[["CommandContext"], Awaitable["str | None"]]


class CommandError(RuntimeError):
    """A failure a user can act on (no model, bad usage); the message is shown."""


@dataclass
class CommandContext:
    """What a handler is given: the turn's identity plus a way to speak.

    ``emit`` writes a meta event (a step in the trace, persisted in history);
    ``say`` writes answer text. Both go through the run's channel, so a
    command's output survives a reload exactly like an agent's.
    """

    session_id: str
    args: str
    settings: AppSettings
    channel: StreamChannel
    _events: list[dict[str, Any]] = field(default_factory=list)
    _text: list[str] = field(default_factory=list)

    async def emit(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        payload_metadata = (
            dict(metadata) if metadata is not None else {"type": "system", "extra_type": None}
        )
        self._events.append({"content": content, "metadata": payload_metadata})
        await self.channel.publish(
            {"type": "meta", "content": content, "metadata": payload_metadata}
        )

    async def say(self, text: str) -> None:
        self._text.append(text)
        await self.channel.publish({"type": "delta", "text": text})

    @property
    def text(self) -> str:
        return "".join(self._text)

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)


@dataclass(frozen=True)
class CommandSpec:
    """One command. ``handler`` returns the text to persist, or ``None``."""

    name: str
    title: str
    description: str
    handler: CommandHandler
    # Shown in ``/help``; defaults to ``/<name>``.
    usage: str = ""
    # ``builtin`` or the plugin that declared it.
    source: str = "builtin"

    @property
    def command_line(self) -> str:
        return self.usage or f"/{self.name}"

    def to_public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "usage": self.command_line,
            "source": self.source,
        }


@dataclass(frozen=True)
class CommandMatch:
    """A message that names a registered command."""

    name: str
    args: str


@dataclass(frozen=True)
class FoldOutcome:
    """What a manual context fold did, in terms both the UI and the API use."""

    compacted: bool
    folded: int
    kept: int
    summary_chars: int
    messages: int
    detail: str


async def fold_session(
    session_id: str,
    settings: AppSettings,
    *,
    announce: Callable[[int, int], Awaitable[None]] | None = None,
) -> FoldOutcome:
    """Fold older history into the summary, once.

    Shared by ``/compact`` and the REST endpoint so a manual fold and an
    automatic one leave the same history behind. ``announce`` is called with
    ``(folded, kept)`` after the plan is known and before the model is asked —
    that gap is where the UI shows "compacting".
    """
    from amrita_core.components.compaction import ContextCompactor, split_history
    from amrita_core.config import get_config

    from app.core.providers import resolve_preset
    from app.runtime.sessions import load_context, save_context

    memory = await load_context(session_id)
    prefix, tail = split_history(memory.messages)
    if not prefix:
        return FoldOutcome(
            compacted=False,
            folded=0,
            kept=len(tail),
            summary_chars=len(memory.abstract or ""),
            messages=len(memory.messages),
            detail="没有可折叠的历史：至少需要一条更早的消息",
        )

    preset = await resolve_preset(settings.active_model)
    if preset is None:
        raise CommandError("还没有可用的模型，先在设置里选一个")

    if announce is not None:
        await announce(len(prefix), len(tail))

    compactor = ContextCompactor(config=get_config(), preset=preset)
    try:
        folded = await compactor.compact(memory)
    except Exception as exc:  # a provider error must not look like a broken app
        raise CommandError(f"压缩失败：{type(exc).__name__}: {exc}") from exc

    summary = memory.abstract or ""
    if not folded:
        return FoldOutcome(
            compacted=False,
            folded=len(prefix),
            kept=len(tail),
            summary_chars=len(summary),
            messages=len(memory.messages),
            detail="模型没有产出摘要，历史保持原样",
        )

    await save_context(session_id, memory)
    return FoldOutcome(
        compacted=True,
        folded=len(prefix),
        kept=len(tail),
        summary_chars=len(summary),
        messages=len(memory.messages),
        detail="",
    )


async def _compact(ctx: CommandContext) -> str | None:
    """``/compact``: fold older messages into the summary, in this turn."""

    async def announce(folded: int, kept: int) -> None:
        await ctx.emit(
            f"正在压缩上下文：把较早的 {folded} 条消息折叠成摘要，保留最近 {kept} 条。",
            {
                "type": "system",
                "extra_type": "compaction_started",
                "folded": folded,
                "kept": kept,
            },
        )

    try:
        outcome = await fold_session(ctx.session_id, ctx.settings, announce=announce)
    except CommandError as exc:
        # Announced as a normal step, not an error frame: the turn itself is
        # fine, only this command could not do its job.
        await ctx.emit(str(exc), {"type": "system", "extra_type": "compaction_skipped"})
        return None

    if not outcome.compacted:
        await ctx.emit(
            outcome.detail or "上下文压缩没有产出摘要，历史保持原样。",
            {
                "type": "system",
                "extra_type": "compaction_skipped",
                "folded": outcome.folded,
                "kept": outcome.kept,
            },
        )
        return None

    await ctx.emit(
        f"已把较早的 {outcome.folded} 条消息折叠为摘要（{outcome.summary_chars} 字），"
        f"保留最近 {outcome.kept} 条。",
        {
            "type": "system",
            "extra_type": "compaction_finished",
            "folded": outcome.folded,
            "kept": outcome.kept,
            "summary_chars": outcome.summary_chars,
        },
    )
    return None


async def _help(ctx: CommandContext) -> str | None:
    """``/help``: list what this install actually registers."""
    lines = ["可用指令：", ""]
    lines.extend(f"- `{spec.command_line}` — {spec.description}" for spec in COMMANDS.list())
    text = "\n".join(lines)
    await ctx.say(text)
    return text


BUILTIN_COMMANDS: dict[str, CommandSpec] = {
    "compact": CommandSpec(
        name="compact",
        title="压缩上下文",
        description="把较早的消息折叠成摘要，腾出上下文空间（需要模型）。",
        handler=_compact,
        usage="/compact",
    ),
    "help": CommandSpec(
        name="help",
        title="指令列表",
        description="列出当前可用的指令，包括插件注册的。",
        handler=_help,
        usage="/help",
    ),
}


class CommandRegistry:
    """Built-in commands plus the active plugin-provided set."""

    def __init__(self) -> None:
        self._extra: Mapping[str, CommandSpec] = MappingProxyType({})

    def install_extra(self, commands: Mapping[str, CommandSpec]) -> None:
        """Replace the plugin-provided set with one immutable snapshot."""
        self._extra = MappingProxyType(dict(commands))

    def list(self) -> list[CommandSpec]:
        merged = dict(BUILTIN_COMMANDS)
        merged.update(self._extra)
        return sorted(merged.values(), key=lambda item: item.name)

    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.list())

    def get(self, name: str) -> CommandSpec | None:
        return BUILTIN_COMMANDS.get(name) or self._extra.get(name)

    def require(self, name: str) -> CommandSpec:
        spec = self.get(name)
        if spec is None:
            available = ", ".join(f"/{item}" for item in self.names()) or "(none)"
            raise CommandError(f"unknown command '/{name}'; registered: {available}")
        return spec

    def match(self, text: str) -> CommandMatch | None:
        """``/name rest`` when ``name`` is registered, else ``None``.

        An unregistered slash word is *not* an error: a message may legitimately
        start with ``/`` (a path, a regex). Only a known name diverts the turn.
        """
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None
        head, _, rest = stripped[1:].partition(" ")
        name = head.strip().lower()
        if not name or self.get(name) is None:
            return None
        return CommandMatch(name=name, args=rest.strip())


COMMANDS = CommandRegistry()

__all__ = [
    "BUILTIN_COMMANDS",
    "COMMANDS",
    "CommandContext",
    "CommandError",
    "CommandMatch",
    "CommandRegistry",
    "CommandSpec",
    "FoldOutcome",
    "fold_session",
]
