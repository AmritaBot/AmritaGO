"""Park a run that spent its tool budget, and ask whether to keep going.

Sibling of :mod:`app.audit`, and separated for the same reason: the *transport*
is generic (publish an event, wait for a frame on the chat socket), the *policy*
belongs to the strategy that spent the budget. The two are not merged because a
confirmation carries a command the user may edit, while this one carries only a
budget the user may accept — sharing a payload type would mean a struct where
half the fields are meaningless.

"继续" is an answer, not an extension: the turn still winds down, and the runtime
starts a fresh one afterwards. So the event carries no round count to add.

There are three answers, and the difference between the last two is the point of
the gate: **优雅收尾** winds down (the model answers with what it has), while
**直接中断** drops the turn where it stands. Collapsing them into one "stop"
would decide for the user, and "I would rather not pay for another round than
read a rushed answer" is a legitimate thing to want.

Failure is closed: a timeout, a stop request or a missing channel all resolve to
``"graceful"``, which is AmritaCore's ordinary wind-down — losing the work
already done is the one outcome nobody asked for.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from amrita_sense.logging import logger

#: Browser answers: ``continue`` starts a new turn, ``graceful`` winds down, ``abort`` stops it.
Action = Literal["continue", "graceful", "abort"]

#: Every action the browser may send; anything else is treated as ``graceful``.
ACTIONS: tuple[str, ...] = ("continue", "graceful", "abort")

#: Long enough to decide, short enough not to hold a turn open.
DEFAULT_TIMEOUT = 300.0


@dataclass
class _Pending:
    future: asyncio.Future[str] = field(
        default_factory=lambda: asyncio.get_event_loop().create_future()
    )
    budget: int = 0
    created_at: float = field(default_factory=time.time)


class LimitGate:
    """Pending "continue iterating?" requests, keyed by request id."""

    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}

    def pending(self) -> list[dict[str, Any]]:
        return [
            {"id": key, "budget": item.budget, "created_at": item.created_at}
            for key, item in self._pending.items()
        ]

    async def ask(
        self,
        channel: Any,
        *,
        budget: int,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> str:
        """Publish the question and wait for one of :data:`ACTIONS`."""
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        pending = _Pending(future=loop.create_future(), budget=budget)
        self._pending[request_id] = pending
        try:
            await channel.publish(
                {
                    "type": "limit",
                    "request_id": request_id,
                    "budget": budget,
                    "timeout": timeout,
                }
            )
            try:
                return await asyncio.wait_for(pending.future, timeout)
            except asyncio.TimeoutError:
                # Nobody answered: wind down instead of losing the work.
                logger.info(f"Tool-budget gate {request_id} timed out; winding down")
                return "graceful"
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: str, action: str) -> bool:
        """Answer a pending gate. Returns False when it is already gone."""
        pending = self._pending.get(request_id)
        if pending is None:
            logger.warning(f"Tool-budget gate {request_id} is no longer pending")
            return False
        if pending.future.done():
            return False
        # An unknown word is treated as a timeout: wind down.
        pending.future.set_result(action if action in ACTIONS else "graceful")
        return True

    def reject_all(self) -> int:
        """Stop everything outstanding — used when the user stops the turn."""
        count = 0
        for pending in list(self._pending.values()):
            if pending.future.done():
                continue
            pending.future.set_result("abort")
            count += 1
        return count


LIMITS = LimitGate()

__all__ = ["ACTIONS", "DEFAULT_TIMEOUT", "LIMITS", "Action", "LimitGate"]
