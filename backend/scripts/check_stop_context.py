"""Self-check for stopping a turn without losing what it already did.

Run: ``uv run --no-sync python scripts/check_stop_context.py``

Both halves of "insert a message while a turn runs" are covered here:

* the graceful ladder — a tool call in flight keeps the interpreter inside the
  round, so the loop can never reach its boundary. Cancelling that call lets the
  round finish and the normal ``COMMIT_MEMORY`` keep the context, instead of the
  whole turn being killed;
* the immediate path — the turn really is gone, so the tool results it collected
  are replayed from the transcript in the ``assistant`` + ``tool`` shape a
  normal commit writes.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("AMRITA_GO_DATA", tempfile.mkdtemp(prefix="amrita-stop-"))

from app.runtime.agent_runtime import TurnDraft, _commit_interrupted, _tool_exchanges
from app.runtime.prompt_inject import GRACEFUL_TOOL_NOTICE, StopMode, TurnInjector
from app.runtime.sessions import load_context
from app.runtime.stream_hub import StreamChannel
from app.runtime.tool_gate import TOOLS
from app.runtime.workflows import BUILTIN_WORKFLOWS


class _FakeStream:
    """A suspend stream that parks on the *second* wait, or never.

    The first wait stands for "the loop is stuck in a tool call"; it times out
    exactly like ``SuspendObjectStream.wait_to_suspend`` does.
    """

    def __init__(self, *, park_on: int | None = 2, finish_on: int | None = None) -> None:
        self.park_on = park_on
        self.finish_on = finish_on
        self.armed = 0
        self.resumed = 0
        self.channel: StreamChannel | None = None

    async def wait_to_suspend(self, *tags: str, timeout: float | None = None) -> None:
        del tags
        self.armed += 1
        if self.finish_on == self.armed and self.channel is not None:
            # The turn ended on its own while the stop was waiting.
            self.channel.done = True
        if self.park_on == self.armed:
            return
        await asyncio.sleep(timeout or 0)
        raise asyncio.TimeoutError

    def resume(self) -> None:
        self.resumed += 1


def _channel(stream: _FakeStream) -> StreamChannel:
    channel = StreamChannel("s-stop")
    channel.stream = stream  # type: ignore
    channel.inject = BUILTIN_WORKFLOWS["react-step"].inject
    stream.channel = channel
    return channel


async def check_ladder_cancels_the_tool() -> None:
    """A stuck tool is cancelled so the boundary can be reached, keeping context."""
    stream = _FakeStream(park_on=2)
    channel = _channel(stream)
    entry = TOOLS.open(
        "call_ladder", "unix_run", asyncio.get_running_loop(), session_id=channel.session_id
    )

    result = await TurnInjector(channel, timeout=0.05).stop(StopMode.GRACEFUL)

    assert result.mode is StopMode.GRACEFUL and result.parked, result.to_public()
    assert channel.stop_requested, "a parked stop must ask the strategy to end the round"
    assert stream.armed == 2, f"expected a second wait after the cancel, got {stream.armed}"
    assert stream.resumed >= 1, "the producer must be released or the run hangs"
    assert entry.future.done(), "the running tool call was not cancelled"
    assert entry.future.result() == GRACEFUL_TOOL_NOTICE
    TOOLS.close("call_ladder")
    print("ladder ok")


async def check_ladder_without_tools_escalates() -> None:
    """Nothing in flight: the second wait is dropped and the turn is cut."""
    stream = _FakeStream(park_on=None)
    channel = _channel(stream)
    result = await TurnInjector(channel, timeout=0.05).stop(StopMode.GRACEFUL)
    assert result.mode is StopMode.IMMEDIATE, result.to_public()
    assert result.reason == "graceful_timeout", result.reason
    assert stream.resumed >= 1
    print("escalation ok")


async def check_finished_turn_is_not_killed() -> None:
    """A turn that ended mid-wait is reported, not escalated onto the next turn."""
    stream = _FakeStream(park_on=None, finish_on=1)
    channel = _channel(stream)
    result = await TurnInjector(channel, timeout=0.05).stop(StopMode.GRACEFUL)
    assert result.accepted and result.reason == "turn_finished", result.to_public()
    assert not channel.cancelled, "escalating here would cancel the *next* turn"
    print("turn_finished ok")


def _call(
    tool_id: str, name: str, *, result: str | None, arguments: str = '{"command":"ls"}'
) -> list:
    """The two events the strategy records per call: the start, then the end."""
    start = {
        "kind": "event",
        "content": f"Calling function {name}",
        "metadata": {
            "type": "function_call",
            "tool_id": tool_id,
            "function_name": name,
            "is_done": False,
            "arguments": arguments,
        },
    }
    if result is None:
        return [start]
    done = {
        "kind": "event",
        "content": f"Called tool {name}",
        "metadata": {
            "type": "function_call",
            "tool_id": tool_id,
            "function_name": name,
            "is_done": True,
            "arguments": arguments,
            "result": result,
        },
    }
    return [start, done]


async def check_replay() -> None:
    """A cut-off turn's finished calls are rebuilt; the unfinished one is not."""
    trace = _call("c1", "unix_run", result="ok-1") + _call("c2", "read_file", result="ok-2")
    trace += _call("c3", "unix_run", result=None)

    rebuilt = _tool_exchanges(trace)
    assert len(rebuilt) == 4, f"expected two pairs, got {len(rebuilt)}"
    assert rebuilt[0].role == "assistant" and rebuilt[0].tool_calls[0].id == "c1"
    assert rebuilt[1].role == "tool" and rebuilt[1].tool_call_id == "c1"
    assert rebuilt[1].content == "ok-1" and rebuilt[1].name == "unix_run"
    assert rebuilt[2].tool_calls[0].function.name == "read_file"
    assert rebuilt[3].content == "ok-2"
    print("replay ok")


async def check_commit() -> None:
    """The interrupted turn writes user + answer + the tool rounds it finished."""
    draft = TurnDraft(session_id="s-commit")
    draft.answer.append("先看看入口。")
    draft.trace = _call("k1", "unix_run", result="HTTP/1.1 200 OK")

    await _commit_interrupted(draft, "布尔盲注入", None)
    memory = await load_context("s-commit")
    roles = [message.role for message in memory.messages]
    assert roles == ["user", "assistant", "assistant", "tool"], roles
    assert memory.messages[1].content == "先看看入口。"
    assert memory.messages[3].content == "HTTP/1.1 200 OK"

    # A second finalize must not duplicate the turn.
    await _commit_interrupted(draft, "布尔盲注入", None)
    assert len((await load_context("s-commit")).messages) == 4
    print("commit ok")


async def main() -> None:
    await check_ladder_cancels_the_tool()
    await check_ladder_without_tools_escalates()
    await check_finished_turn_is_not_killed()
    await check_replay()
    await check_commit()


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
