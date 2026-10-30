"""Replayable per-stream event buffer.

The agent appends events to a :class:`StreamChannel` and a WebSocket handler
drains it: writing never raises, so a disconnected browser cannot break a run,
and a reconnect replays the run from the start. Storage is an append-only
``StringIO`` holding one JSON event per line.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from io import StringIO
from typing import TYPE_CHECKING, Any

from amrita_sense.streaming import SuspendObjectStream

# Deferred ``InjectSpec`` import avoids a cycle with ``prompt_inject``.
if TYPE_CHECKING:
    from app.runtime.prompt_inject import InjectSpec

_TERMINAL = {"done", "error"}


class StreamChannel:
    """Append-only event log for one agent run."""

    def __init__(self, session_id: str, stream_id: str | None = None) -> None:
        self.session_id = session_id
        self.stream_id = stream_id or uuid.uuid4().hex
        self.created_at = time.time()
        self.done = False
        self.cancelled = False
        self.error: str | None = None
        # Cut off at the gate: nothing follows, no summary and no last model call.
        self.aborted = False
        # Checked at round boundaries to end gracefully without cancellation.
        self.stop_requested = False
        self.stop_mode: str | None = None
        self._buf = StringIO()
        self._lock = asyncio.Lock()
        self._version = 0
        self._changed = asyncio.Event()
        # Set by whoever started the run, so the socket can cancel it.
        self.task: asyncio.Task[None] | None = None
        # Active turn to cancel while leaving queued prompts for their own turn.
        self.turn_task: asyncio.Task[None] | None = None
        # This turn's suspend stream, used to arm graceful stops on the correct run.
        self.stream: SuspendObjectStream[Any] | None = None
        # ``InjectSpec`` of the workflow this turn runs.
        self.inject: InjectSpec | None = None
        # Summary of the last finished turn, republished as ``done`` by the driver.
        self.turn_summary: dict[str, Any] | None = None
        # Next channel in the chain; the forwarder follows it when this one ends.
        self.successor: StreamChannel | None = None

    @property
    def finished(self) -> bool:
        """Whether this channel can no longer accept a stop."""
        return self.done or self.cancelled

    def cancel(self) -> None:
        """Ask the run to stop. Safe to call repeatedly or after completion."""
        self.cancelled = True
        if self.task is not None and not self.task.done():
            self.task.cancel()

    def cancel_turn(self) -> None:
        """Stop the turn that is running right now, keeping the chain alive.

        Falls back to the chain task while no turn has started yet, so a stop
        that arrives during setup is not silently ignored.
        """
        turn = self.turn_task
        if turn is not None and not turn.done():
            turn.cancel()
            return
        if self.task is not None and not self.task.done():
            self.task.cancel()

    def notify(self) -> None:
        """Wake a forwarder without appending an event.

        Used when a successor channel is linked in: the reader parked on this
        channel has nothing new to send, but it does need to look again.
        """
        self._version += 1
        self._changed.set()

    async def publish(self, event: dict[str, Any]) -> None:
        """Append one event. Never raises, never blocks on a consumer."""
        payload = dict(event)
        payload.setdefault("stream_id", self.stream_id)
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        async with self._lock:
            self._buf.write(line)
            if payload.get("type") in _TERMINAL:
                self.done = True
                if payload.get("type") == "error":
                    self.error = str(payload.get("message", ""))
            self._version += 1
            self._changed.set()

    @property
    def size(self) -> int:
        return len(self._buf.getvalue())

    def read_from(self, offset: int) -> tuple[list[str], int, bool]:
        """Return ``(lines, new_offset, done)`` starting at ``offset``."""
        text = self._buf.getvalue()
        if offset >= len(text):
            return [], len(text), self.done
        tail = text[offset:]
        lines = [line for line in tail.split("\n") if line]
        return lines, len(text), self.done

    async def wait_change(self, since_version: int, timeout: float = 30.0) -> bool:
        """Block until new data arrives; ``False`` on timeout."""
        while True:
            if self._version != since_version:
                return True
            self._changed.clear()
            if self._version != since_version:
                return True
            try:
                await asyncio.wait_for(self._changed.wait(), timeout)
            except asyncio.TimeoutError:
                return False

    @property
    def version(self) -> int:
        return self._version


class StreamHub:
    """Registry of the newest channel per session."""

    def __init__(self) -> None:
        self._by_stream: dict[str, StreamChannel] = {}
        self._latest: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create(self, session_id: str) -> StreamChannel:
        channel = StreamChannel(session_id)
        async with self._lock:
            # Drop the previous channel: its content is already in history.jsonl.
            previous = self._latest.get(session_id)
            if previous:
                self._by_stream.pop(previous, None)
            self._by_stream[channel.stream_id] = channel
            self._latest[session_id] = channel.stream_id
        return channel

    def get(self, stream_id: str) -> StreamChannel | None:
        return self._by_stream.get(stream_id)

    def latest(self, session_id: str) -> StreamChannel | None:
        stream_id = self._latest.get(session_id)
        return self._by_stream.get(stream_id) if stream_id else None

    def busy(self, session_id: str) -> bool:
        """Whether a turn is still running for this session.

        Used to keep file-level edits (compaction) out of a live run: the run
        holds its own context copy and would write it back on its way out.
        """
        channel = self.latest(session_id)
        if channel is None or channel.done or channel.cancelled:
            return False
        return channel.task is not None and not channel.task.done()

    async def forget(self, session_id: str) -> None:
        async with self._lock:
            stream_id = self._latest.pop(session_id, None)
            if stream_id:
                self._by_stream.pop(stream_id, None)


HUB = StreamHub()
