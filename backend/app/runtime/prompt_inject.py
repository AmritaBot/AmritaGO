"""Prompt injection: end the current turn early and queue the next message.

Two ways to end a turn, and the difference is *where* it ends.

``graceful``
    Suspend the interpreter at a suspend point the workflow declared, so the
    run stops between two model rounds instead of inside one. The producer is
    parked — ``SuspendObjectStream.wait_to_suspend`` blocks until the
    interpreter actually reaches the point — so "the turn has stopped" is a
    fact rather than a guess, and nothing is thrown away: the workflow still
    runs its normal wind-down and commits the context.

``immediate``
    Cancel the turn task. Works for every workflow, including the ones that
    declare no suspend point, at the price of cutting whatever was in flight.

Which one runs is decided by the workflow. A declared suspend point is
preferred and ``immediate`` is the fallback, so a workflow with no safe point
cannot pretend to have one: the capability is part of the workflow spec, and
the registry refuses to install a spec that names a point its graph does not
have.

Only one prompt may be pending per session. A queue was considered and
rejected: with more than one entry, "which bubble is this?" and "which one did
revoke remove?" stop having a single answer, while the user thinks of exactly
one "next message". Offering while occupied therefore *replaces*, and the
replaced handle comes back so the caller can restore the text it held.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from amrita_sense.logging import logger

from app.runtime.audit import BROKER
from app.runtime.limit_gate import LIMITS
from app.runtime.stream_hub import StreamChannel
from app.runtime.tool_gate import TOOLS

# Escalate a graceful stop to immediate after this wait if no suspend point is reached.
DEFAULT_GRACEFUL_TIMEOUT = 10.0


class StopMode(str, Enum):
    """How a turn should end.

    ``str``-valued (rather than 3.11's ``StrEnum``) because the backend still
    runs on 3.10; use ``.value`` when it has to leave Python.
    """

    IMMEDIATE = "immediate"
    GRACEFUL = "graceful"


@dataclass(frozen=True)
class InjectSpec:
    """Which suspend points a workflow offers, and for which stop modes.

    ``points`` maps a node tag to the modes that may use it. The default (no
    points) means "this workflow cannot be stopped gracefully" — which is the
    honest answer for a single-round pipeline such as ``chat``.
    """

    points: Mapping[str, frozenset[StopMode]] = MappingProxyType({})
    # Shown in the UI: "当前步骤结束后" beats a bare tag name.
    boundary: str = ""

    @property
    def graceful_tags(self) -> tuple[str, ...]:
        return tuple(tag for tag, modes in self.points.items() if StopMode.GRACEFUL in modes)

    @property
    def graceful(self) -> bool:
        return bool(self.graceful_tags)

    def to_public(self) -> dict[str, Any]:
        return {
            "immediate": True,
            "graceful": self.graceful,
            "points": list(self.graceful_tags),
            "boundary": self.boundary,
        }


@dataclass(frozen=True)
class QueuedPrompt:
    """A message waiting to become the next turn."""

    content: str
    attachments: tuple[dict[str, Any], ...] = ()
    source: str = "user"


@dataclass(frozen=True)
class PromptHandle:
    """Identity of one queued prompt, returned so it can be revoked."""

    id: str
    session_id: str
    content: str
    attachments: tuple[dict[str, Any], ...]
    created_at: float

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "attachments": list(self.attachments),
            "created_at": self.created_at,
        }


class RevokeStatus(str, Enum):
    """Outcome of :meth:`PromptInjectAPI.revoke`."""

    REVOKED = "revoked"
    # The handle was never queued, or was already revoked.
    NOT_FOUND = "not_found"
    # The next turn already took it; only ``message_edit`` can change it now.
    TOO_LATE = "too_late"


@dataclass(frozen=True)
class OfferResult:
    handle: PromptHandle
    # The prompt this one displaced, if the slot was occupied.
    replaced: PromptHandle | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "handle": self.handle.to_public(),
            "replaced": self.replaced.to_public() if self.replaced else None,
        }


@dataclass(frozen=True)
class StopResult:
    accepted: bool
    mode: StopMode
    # True only when the interpreter was confirmed to be parked at a point.
    parked: bool = False
    reason: str = ""
    latency: float = 0.0

    def to_public(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "mode": self.mode.value,
            "parked": self.parked,
            "reason": self.reason,
            "latency": round(self.latency, 3),
        }


class PromptSlot:
    """The single pending prompt of one session."""

    def __init__(self) -> None:
        self._handle: PromptHandle | None = None
        # Remember the last taken handle so revoke can distinguish too-late from not-found.
        self._taken: PromptHandle | None = None
        self._lock = asyncio.Lock()

    async def offer(self, session_id: str, prompt: QueuedPrompt) -> OfferResult:
        handle = PromptHandle(
            id=uuid.uuid4().hex,
            session_id=session_id,
            content=prompt.content,
            attachments=prompt.attachments,
            created_at=time.time(),
        )
        async with self._lock:
            replaced = self._handle
            self._handle = handle
        return OfferResult(handle=handle, replaced=replaced)

    async def take(self) -> PromptHandle | None:
        """Atomically remove and return the pending prompt.

        This is the linearization point: whoever wins this lock decides whether
        a concurrent ``revoke`` succeeds, so a revoked prompt can never start a
        turn and a started turn can never be revoked.
        """
        async with self._lock:
            handle = self._handle
            if handle is None:
                return None
            self._handle = None
            self._taken = handle
            return handle

    async def revoke(self, handle_id: str) -> RevokeStatus:
        async with self._lock:
            if self._handle is not None and self._handle.id == handle_id:
                self._handle = None
                return RevokeStatus.REVOKED
            if self._taken is not None and self._taken.id == handle_id:
                return RevokeStatus.TOO_LATE
        return RevokeStatus.NOT_FOUND

    async def peek(self) -> PromptHandle | None:
        async with self._lock:
            return self._handle

    async def clear(self) -> PromptHandle | None:
        async with self._lock:
            handle, self._handle = self._handle, None
            return handle


class PromptSlots:
    """Per-session slots.

    Keyed by session rather than kept as one global slot: a slot is the next
    message *of a conversation*, so two conversations must never see each
    other's queue.
    """

    def __init__(self) -> None:
        self._by_session: dict[str, PromptSlot] = {}

    def slot(self, session_id: str) -> PromptSlot:
        return self._by_session.setdefault(session_id, PromptSlot())

    def forget(self, session_id: str) -> None:
        self._by_session.pop(session_id, None)


SLOTS = PromptSlots()


@runtime_checkable
class PromptInjectAPI(Protocol):
    """What the socket layer needs in order to interrupt a running turn."""

    def capabilities(self) -> InjectSpec: ...

    async def stop(self, mode: StopMode) -> StopResult: ...

    async def offer(self, prompt: QueuedPrompt) -> OfferResult: ...

    async def revoke(self, handle_id: str) -> RevokeStatus: ...

    async def pending(self) -> PromptHandle | None: ...

    async def stop_and_offer(self, prompt: QueuedPrompt, mode: StopMode) -> StopResult: ...


class TurnInjector:
    """``PromptInjectAPI`` bound to one live channel.

    The channel, not the session, owns the stream: each turn builds a fresh
    ``ChatObject`` and therefore a fresh ``SuspendObjectStream``, so a suspend
    signal can only ever reach the turn it was armed on.
    """

    def __init__(
        self,
        channel: StreamChannel,
        *,
        timeout: float = DEFAULT_GRACEFUL_TIMEOUT,
    ) -> None:
        self._channel = channel
        self._session_id = channel.session_id
        self._timeout = timeout
        self._lock = asyncio.Lock()
        # Graceful-stop waiter, cancelled by an immediate stop.
        self._waiter: asyncio.Task[None] | None = None

    def capabilities(self) -> InjectSpec:
        return self._channel.inject or InjectSpec()

    async def offer(self, prompt: QueuedPrompt) -> OfferResult:
        return await SLOTS.slot(self._session_id).offer(self._session_id, prompt)

    async def revoke(self, handle_id: str) -> RevokeStatus:
        return await SLOTS.slot(self._session_id).revoke(handle_id)

    async def pending(self) -> PromptHandle | None:
        return await SLOTS.slot(self._session_id).peek()

    async def stop_and_offer(self, prompt: QueuedPrompt, mode: StopMode) -> StopResult:
        """Queue the message first, then stop.

        The order matters: a stop that succeeded while the offer failed would
        end the turn and hand the user nothing, with no handle to revoke.
        """
        await self.offer(prompt)
        return await self.stop(mode)

    async def stop(self, mode: StopMode) -> StopResult:
        channel = self._channel
        if channel.finished:
            return StopResult(accepted=False, mode=mode, reason="no_active_run")
        if mode is StopMode.IMMEDIATE:
            # Handle immediate stops outside the lock so they can override a waiting graceful stop.
            return await self._immediate(reason="user_requested")
        async with self._lock:
            if channel.finished:
                return StopResult(accepted=False, mode=mode, reason="no_active_run")
            if not self.capabilities().graceful:
                return await self._immediate(reason="no_graceful_point")
            return await self._graceful()

    # Internals

    async def _immediate(self, *, reason: str = "") -> StopResult:
        channel = self._channel
        channel.stop_mode = StopMode.IMMEDIATE
        channel.stop_requested = True
        self._release_parked()
        waiter = self._waiter
        if waiter is not None and not waiter.done():
            # Cancel the waiter because no suspend point will release it now.
            waiter.cancel()
        stream = channel.stream
        if stream is not None:
            stream.resume()
        channel.cancel_turn()
        return StopResult(accepted=True, mode=StopMode.IMMEDIATE, reason=reason)

    def _release_parked(self) -> None:
        """Answer everything this session is waiting on.

        Scoped to the session: a stop in one conversation must not deny a
        confirmation or cancel a tool call in another.
        """
        BROKER.reject_session(self._session_id, "用户中止了本轮，确认请求已取消。")
        LIMITS.reject_session(self._session_id)
        TOOLS.cancel_session(self._session_id, "用户中止了本轮")

    async def _graceful(self) -> StopResult:
        channel = self._channel
        stream = channel.stream
        tags = self.capabilities().graceful_tags
        if stream is None or not tags:
            return await self._immediate(reason="no_graceful_point")
        if channel.stop_mode is not None:
            return StopResult(accepted=False, mode=StopMode.GRACEFUL, reason="already_stopping")

        channel.stop_mode = StopMode.GRACEFUL
        started = time.time()
        waiter = asyncio.create_task(stream.wait_to_suspend(*tags, timeout=self._timeout))
        self._waiter = waiter
        parked = False
        try:
            await asyncio.wait({waiter})
            error = None if waiter.cancelled() else waiter.exception()
            if error is None and not waiter.cancelled():
                parked = True
                # Set before resuming because single_execute reads this as soon as execution continues.
                channel.stop_requested = True
            else:
                logger.info(
                    f"Graceful stop did not park ({type(error).__name__ if error else 'cancelled'});"
                    " escalating to an immediate stop"
                )
        finally:
            self._waiter = None
            if not waiter.done():
                waiter.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait({waiter})
            # Always resume because a timed-out wait may leave the producer parked without a resume future.
            stream.resume()

        latency = time.time() - started
        if channel.stop_mode is StopMode.IMMEDIATE:
            # A concurrent immediate request won the race and already did it.
            return StopResult(
                accepted=True, mode=StopMode.IMMEDIATE, reason="superseded", latency=latency
            )
        if not parked:
            return await self._immediate(reason="graceful_timeout")
        return StopResult(accepted=True, mode=StopMode.GRACEFUL, parked=True, latency=latency)


__all__ = [
    "DEFAULT_GRACEFUL_TIMEOUT",
    "SLOTS",
    "InjectSpec",
    "OfferResult",
    "PromptHandle",
    "PromptInjectAPI",
    "PromptSlot",
    "PromptSlots",
    "QueuedPrompt",
    "RevokeStatus",
    "StopMode",
    "StopResult",
    "TurnInjector",
]
