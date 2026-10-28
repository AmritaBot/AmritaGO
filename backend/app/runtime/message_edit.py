"""Rewriting the last prompt, whether it was answered or not.

The last user message is always the user's to change. Rewriting it ends
the running turn, rolls the model context back to just before that prompt,
drops everything after it from the transcript, and announces the change as a
``message_modify`` event; the client then sends the edited text as an ordinary
turn.

A pending injected prompt is dropped at the same time: it was queued behind the
turn this edit is deleting, so it can no longer become a turn of its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from amrita_sense.logging import logger

from app.runtime import events
from app.runtime.audit import BROKER
from app.runtime.limit_gate import LIMITS
from app.runtime.prompt_inject import SLOTS, StopMode, TurnInjector
from app.runtime.sessions import (
    get_meta,
    load_context,
    load_history,
    save_context,
    save_meta,
    write_history,
)
from app.runtime.stream_hub import HUB
from app.runtime.tool_gate import TOOLS


class MessageEditError(RuntimeError):
    """The prompt cannot be rewritten; ``status`` is the HTTP status to use."""

    def __init__(self, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class EditOutcome:
    entry_id: str
    previous: str
    content: str
    # Transcript entries dropped, including the prompt itself.
    removed_entries: int
    # Model-context messages dropped with it.
    removed_messages: int
    context_messages: int
    # A prompt queued behind the edited turn was vetoed.
    dropped_prompt: bool = False


def _content_text(content: Any) -> str:
    """Flatten a memory message's content to text.

    A prompt with attachments is stored as content items, so the text parts are
    concatenated; images contribute nothing and are matched by position instead.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, Mapping):
            text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _trim_index(messages: list[Any], prompt: str) -> int | None:
    """Where the prompt starts in the model context, or ``None``.

    Matching on content rather than position because the context also holds
    tool traffic the transcript never showed, so the two lists do not line up.
    Falls back to the last user message: an image-only prompt has no text to
    compare, and it is still the message being replaced.
    """
    wanted = prompt.strip()
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if getattr(message, "role", None) != "user":
            continue
        if _content_text(getattr(message, "content", "")).strip() == wanted:
            return index
    for index in range(len(messages) - 1, -1, -1):
        if getattr(messages[index], "role", None) == "user":
            return index
    return None


async def _wait_idle(session_id: str, timeout: float = 20.0) -> None:
    """Wait until the session has no running chain.

    Awaiting the turn task is not enough: the driver keeps going after a turn
    (it may chain a successor) and writes history on its way out, so a rollback
    that ran earlier would be overwritten by that write.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while HUB.busy(session_id):
        if loop.time() >= deadline:
            logger.warning(f"Session {session_id}: still running after {timeout}s, editing anyway")
            return
        await asyncio.sleep(0.02)


async def _end_running(session_id: str) -> bool:
    """Stop the running turn and answer anything it is waiting on.

    The pending prompt is dropped *before* the stop so the driver cannot start a
    successor turn with the message this edit is deleting, and the chain is
    awaited *before* the rollback so its history write cannot land after it.
    """
    dropped = await SLOTS.slot(session_id).clear() is not None
    # Confirmations and gate prompts belong to the turn that is about to end.
    BROKER.reject_session(session_id, "这条消息已被修改，确认请求已取消。")
    LIMITS.reject_session(session_id)
    TOOLS.cancel_session(session_id, "这条消息已被修改")
    channel = HUB.latest(session_id)
    if channel is not None and not channel.finished:
        await TurnInjector(channel).stop(StopMode.IMMEDIATE)
    await _wait_idle(session_id)
    # A prompt queued while the turn was winding down goes too.
    if await SLOTS.slot(session_id).clear() is not None:
        dropped = True
    return dropped


async def rewrite_last_prompt(
    session_id: str,
    content: str,
    *,
    expected: str | None = None,
) -> EditOutcome:
    """Roll the session back to before its last prompt.

    ``expected`` is the text the client believes it is editing. A mismatch means
    the client is looking at a stale transcript — most likely a turn finished in
    another tab — so the edit is refused rather than applied to the wrong
    message.
    """
    text = content.strip()
    if not text:
        raise MessageEditError("修改后的内容不能为空")
    meta = await get_meta(session_id)
    if meta is None:
        raise MessageEditError("会话不存在", status=404)

    dropped = await _end_running(session_id)

    entries = await load_history(session_id)
    index: int | None = None
    for position in range(len(entries) - 1, -1, -1):
        if entries[position].role == "user":
            index = position
            break
    if index is None:
        raise MessageEditError("这个会话还没有用户消息")
    target = entries[index]
    # Reject stale clients before editing so the version mismatch is explicit.
    if expected is not None and expected.strip() != target.content.strip():
        raise MessageEditError("会话已经变了，请刷新后再改")

    keep = entries[:index]

    memory = await load_context(session_id)
    cut = _trim_index(memory.messages, target.content)
    removed_messages = 0
    if cut is None:
        # If context was folded or cleared, keep the rollback so the user can retype.
        logger.warning(f"Session {session_id}: no context message matched the edited prompt")
    else:
        removed_messages = len(memory.messages) - cut
        memory.messages = memory.messages[:cut]

    await save_context(session_id, memory)
    await write_history(session_id, keep)
    meta.message_count = sum(1 for entry in keep if entry.role == "user")
    await save_meta(meta)

    # Publish after rollback so handlers observe the updated session state.
    await events.emit(
        events.MESSAGE_MODIFY,
        session_id=session_id,
        entry_id=target.id,
        previous=target.content,
        content=text,
        removed_entries=len(entries) - len(keep),
        removed_messages=removed_messages,
        context_messages=len(memory.messages),
        dropped_prompt=dropped,
    )
    return EditOutcome(
        entry_id=target.id,
        previous=target.content,
        content=text,
        removed_entries=len(entries) - len(keep),
        removed_messages=removed_messages,
        context_messages=len(memory.messages),
        dropped_prompt=dropped,
    )


__all__ = ["EditOutcome", "MessageEditError", "rewrite_last_prompt"]
