"""Rewriting a prompt the agent never answered.

A prompt that failed — no key, a provider error, a stopped turn — is still the
user's to change. Rewriting it rolls the model context back to just before that
prompt, drops the failed exchange from the transcript, and announces the change
as a ``message_modify`` event; the client then sends the edited text as an
ordinary turn.

Everything after the prompt goes with it. A tool call that already ran cannot
be kept while the request that caused it is replaced, and a half-answered turn
is not something a later turn can continue from.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from amrita_sense.logging import logger

from app.runtime import events
from app.runtime.sessions import (
    HistoryEntry,
    get_meta,
    load_context,
    load_history,
    save_context,
    save_meta,
    write_history,
)
from app.runtime.stream_hub import HUB


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


def _failed_only(entry: HistoryEntry) -> bool:
    """A bubble that reports a failure and carries no answer."""
    return entry.role == "assistant" and not entry.content.strip() and bool(entry.error)


def _unanswered(entries: list[HistoryEntry], index: int) -> bool:
    """Whether nothing after ``index`` is an actual reply."""
    return all(_failed_only(entry) for entry in entries[index + 1 :])


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


async def rewrite_unanswered_prompt(
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
    if HUB.busy(session_id):
        raise MessageEditError("这一轮还在跑，等它结束再改")
    meta = await get_meta(session_id)
    if meta is None:
        raise MessageEditError("会话不存在", status=404)

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
    if not _unanswered(entries, index):
        raise MessageEditError("这条消息已经有回复了，不能再改")

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
    )
    return EditOutcome(
        entry_id=target.id,
        previous=target.content,
        content=text,
        removed_entries=len(entries) - len(keep),
        removed_messages=removed_messages,
        context_messages=len(memory.messages),
    )


__all__ = ["EditOutcome", "MessageEditError", "rewrite_unanswered_prompt"]
