from __future__ import annotations

import asyncio
import os
import tempfile

from amrita_core.types import MemoryModel
from amrita_core.types.message import Message

DATA = tempfile.mkdtemp(prefix="amrita-edit-")
os.environ["AMRITA_GO_DATA"] = DATA

from app.runtime import events
from app.runtime.message_edit import (
    MessageEditError,
    rewrite_unanswered_prompt,
)
from app.runtime.sessions import (
    HistoryEntry,
    append_history,
    create_session,
    get_meta,
    load_context,
    load_history,
    save_context,
)


async def main() -> None:
    meta = await create_session("t")
    sid = meta.id

    await append_history(sid, HistoryEntry(role="user", content="first"))
    await append_history(sid, HistoryEntry(role="assistant", content="answer one"))
    await append_history(sid, HistoryEntry(role="user", content="second prompt"))
    await append_history(
        sid,
        HistoryEntry(role="assistant", content="", error="OpenAIError: 401"),
    )

    memory = MemoryModel()
    memory.messages = [
        Message(role="user", content="first"),
        Message(role="assistant", content="answer one"),
        Message(role="user", content="second prompt"),
    ]
    await save_context(sid, memory)

    seen: list[dict[str, object]] = []

    async def on_modify(event: events.PluginEvent) -> None:
        seen.append(dict(event))

    subscription = events.subscribe(events.MESSAGE_MODIFY, on_modify)

    outcome = await rewrite_unanswered_prompt(
        sid, "second prompt, edited", expected="second prompt"
    )
    assert outcome.removed_entries == 2, outcome
    assert outcome.removed_messages == 1, outcome
    assert outcome.context_messages == 2, outcome

    entries = await load_history(sid)
    assert [entry.content for entry in entries] == ["first", "answer one"], entries

    context = await load_context(sid)
    assert [message.content for message in context.messages] == ["first", "answer one"]

    updated = await get_meta(sid)
    assert updated is not None and updated.message_count == 1, updated

    assert seen and seen[-1]["content"] == "second prompt, edited", seen
    assert seen[-1]["previous"] == "second prompt", seen

    # A stale client is refused instead of editing the wrong prompt.
    try:
        await rewrite_unanswered_prompt(sid, "other", expected="something else")
    except MessageEditError as exc:
        assert "刷新" in str(exc), exc
    else:
        raise AssertionError("expected a stale-client error")

    # A prompt that has an answer is not editable.
    await append_history(sid, HistoryEntry(role="user", content="third"))
    await append_history(sid, HistoryEntry(role="assistant", content="real answer"))
    try:
        await rewrite_unanswered_prompt(sid, "third edited")
    except MessageEditError as exc:
        assert "已经有回复" in str(exc), exc
    else:
        raise AssertionError("expected an answered-prompt error")

    # An empty rewrite is rejected before anything is touched.
    try:
        await rewrite_unanswered_prompt(sid, "   ")
    except MessageEditError as exc:
        assert "不能为空" in str(exc), exc
    else:
        raise AssertionError("expected an empty-content error")

    subscription.close()
    print("message_edit ok", DATA)


asyncio.run(main())
