"""Replayable per-stream event buffer.

The agent runs in its own task and never talks to a socket directly: it only
appends events to a :class:`StreamChannel`. A WebSocket handler drains that
channel. Two consequences matter:

* A disconnected browser cannot break the run — writing to the buffer never
  raises, so a failed ``send`` is simply the handler's problem.
* Reconnecting is trivial: the whole run is still in the buffer, so the client
  replays it from the start and continues live from wherever the agent is.

The buffer itself is an append-only ``StringIO`` (one JSON event per line),
which is exactly the "keep writing, read from the front" shape a resumable
stream needs.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from io import StringIO
from typing import Any

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
        #: The user answered "继续迭代": a new turn follows once this one winds down.
        self.continue_requested = False
        #: Cut off at the gate: nothing follows, no summary and no last model call.
        self.aborted = False
        #: Turns chained so far, reported for information.
        self.continuations = 0
        self._buf = StringIO()
        self._lock = asyncio.Lock()
        self._version = 0
        self._changed = asyncio.Event()
        #: Set by whoever started the run, so the socket can cancel it.
        self.task: asyncio.Task[None] | None = None

    def cancel(self) -> None:
        """Ask the run to stop. Safe to call repeatedly or after completion."""
        self.cancelled = True
        if self.task is not None and not self.task.done():
            self.task.cancel()

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

    async def forget(self, session_id: str) -> None:
        async with self._lock:
            stream_id = self._latest.pop(session_id, None)
            if stream_id:
                self._by_stream.pop(stream_id, None)


HUB = StreamHub()
