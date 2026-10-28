from __future__ import annotations

import asyncio
import os
import tempfile

from amrita_core.types import MemoryModel
from amrita_core.types.message import Message

DATA = tempfile.mkdtemp(prefix="amrita-edit-")
os.environ["AMRITA_GO_DATA"] = DATA

from app.runtime import events
from app.runtime.audit import BROKER
from app.runtime.message_edit import MessageEditError, rewrite_last_prompt
from app.runtime.prompt_inject import SLOTS, QueuedPrompt
from app.runtime.sessions import (
    HistoryEntry,
    append_history,
    create_session,
    get_meta,
    load_context,
    load_history,
    save_context,
)


class _FakeChannel:
    """Just enough of a channel for the audit broker to publish into."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

    async def publish(self, event: dict[str, object]) -> None:
        return None


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

    outcome = await rewrite_last_prompt(sid, "second prompt, edited", expected="second prompt")
    assert outcome.removed_entries == 2, outcome
    assert outcome.removed_messages == 1, outcome
    assert outcome.context_messages == 2, outcome
    assert outcome.dropped_prompt is False, outcome

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
        await rewrite_last_prompt(sid, "other", expected="something else")
    except MessageEditError as exc:
        assert "刷新" in str(exc), exc
    else:
        raise AssertionError("expected a stale-client error")

    # An answered prompt is editable too: the answer it produced goes with it.
    await append_history(sid, HistoryEntry(role="user", content="third"))
    await append_history(sid, HistoryEntry(role="assistant", content="real answer"))
    answered = await rewrite_last_prompt(sid, "third edited")
    assert answered.removed_entries == 2, answered
    entries = await load_history(sid)
    assert [entry.content for entry in entries] == ["first", "answer one"], entries

    # A prompt queued behind the turn is vetoed by the edit.
    await append_history(sid, HistoryEntry(role="user", content="fourth"))
    await SLOTS.slot(sid).offer(sid, QueuedPrompt(content="queued next"))
    vetoed = await rewrite_last_prompt(sid, "fourth edited")
    assert vetoed.dropped_prompt is True, vetoed
    assert await SLOTS.slot(sid).peek() is None

    # An empty rewrite is rejected before anything is touched.
    try:
        await rewrite_last_prompt(sid, "   ")
    except MessageEditError as exc:
        assert "不能为空" in str(exc), exc
    else:
        raise AssertionError("expected an empty-content error")

    # A confirmation parked by the edited turn is answered instead of left hanging.
    await append_history(sid, HistoryEntry(role="user", content="fifth"))
    parked = asyncio.create_task(
        BROKER.ask(
            _FakeChannel(sid),
            title="确认",
            command="rm -rf build",
            cwd=".",
            shell="bash",
        )
    )
    await asyncio.sleep(0)
    assert BROKER.parked(sid)
    await rewrite_last_prompt(sid, "fifth edited")
    decision = await asyncio.wait_for(parked, 5)
    assert decision.approved is False, decision
    assert "修改" in decision.reason, decision
    assert not BROKER.parked(sid)

    subscription.close()
    print("message_edit ok", DATA)


asyncio.run(main())
