"""Human-in-the-loop confirmation for tool calls.

The tool publishes a ``confirm`` event into the session stream and parks on a
future that the chat socket resolves with an approval (optionally with an
edited command) or a rejection (with a reason). Policy lives in the tool; this
module only knows how to ask. Every failure mode resolves to "not approved".

There is deliberately **no timeout**. A prompt is a question to the person who
started the run; answering "nobody replied" on their behalf turns a slow reader
into a rejected command, and the turn then continues down a path they never
chose. A request lives until it is answered, cancelled, or the turn is stopped.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from amrita_sense.logging import logger

# Kept for callers that still pass a timeout; the broker ignores it.
DEFAULT_TIMEOUT = 180.0


@dataclass
class Decision:
    approved: bool
    # The command to run, which the user may have edited.
    command: str
    # Present when rejected; fed back to the model so it can adapt.
    reason: str = ""
    # Never set any more: approvals wait indefinitely. Kept for old callers.
    timed_out: bool = False
    edited: bool = False
    request_id: str = ""


@dataclass
class _Pending:
    future: asyncio.Future[Decision] = field(
        default_factory=lambda: asyncio.get_event_loop().create_future()
    )
    command: str = ""
    created_at: float = field(default_factory=time.time)
    #: Session the question belongs to, so a tool timeout can tell
    #: "the model is slow" from "the human is thinking".
    session_id: str = ""


class AuditBroker:
    """Pending confirmations, keyed by request id."""

    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}

    def pending(self) -> list[dict[str, Any]]:
        return [
            {"id": key, "command": item.command, "created_at": item.created_at}
            for key, item in self._pending.items()
        ]

    def is_pending(self, request_id: str) -> bool:
        """Whether an answer could still reach a caller.

        A replayed ``confirm`` whose request is gone would raise a prompt whose
        only possible outcome is "已过期", so the drain uses this to drop it
        before it ever reaches the browser.
        """
        return request_id in self._pending

    def parked(self, session_id: str) -> bool:
        """Whether this session is waiting on the human right now."""
        return any(
            item.session_id == session_id and not item.future.done()
            for item in self._pending.values()
        )

    async def ask(
        self,
        channel: Any,
        *,
        title: str,
        command: str,
        cwd: str,
        shell: str,
        hint: str = "",
        output: str = "",
    ) -> Decision:
        """Publish a confirmation request and wait for the browser's answer."""
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        pending = _Pending(
            future=loop.create_future(),
            command=command,
            session_id=str(getattr(channel, "session_id", "") or ""),
        )
        self._pending[request_id] = pending
        status = "cancelled"
        try:
            await channel.publish(
                {
                    "type": "confirm",
                    "request_id": request_id,
                    "title": title,
                    "command": command,
                    "cwd": cwd,
                    "shell": shell,
                    "hint": hint,
                    "output": output,
                }
            )
            # No ``wait_for``: the question stays open until it is answered,
            # the call is cancelled, or the turn is stopped.
            decision = await pending.future
            decision.request_id = request_id
            status = "answered"
            return decision
        finally:
            self._pending.pop(request_id, None)
            # Every request closes exactly once, whatever ended it. The stream
            # is replayed on reconnect, so an unbalanced ``confirm`` would
            # resurrect a question that nobody can answer any more — and two
            # parallel tool calls would then fight over one dialog.
            with suppress(asyncio.CancelledError, Exception):
                await channel.publish(
                    {"type": "confirm_closed", "request_id": request_id, "status": status}
                )

    def resolve(
        self,
        request_id: str,
        *,
        approved: bool,
        command: str | None = None,
        reason: str = "",
    ) -> bool:
        """Answer a pending request. Returns False when it is already gone."""
        pending = self._pending.get(request_id)
        if pending is None:
            logger.warning(f"Confirmation {request_id} is no longer pending")
            return False
        if pending.future.done():
            return False
        edited = command is not None and command.strip() != pending.command
        final = (command or pending.command).strip()
        if approved and not final:
            reason = reason or "命令被清空"
            approved = False
        pending.future.set_result(
            Decision(
                approved=approved,
                command=final or pending.command,
                reason=reason,
                edited=edited,
                request_id=request_id,
            )
        )
        return True

    def reject_all(self, reason: str) -> int:
        """Deny everything outstanding — used when the user stops the turn."""
        count = 0
        for request_id, pending in list(self._pending.items()):
            if pending.future.done():
                continue
            pending.future.set_result(
                Decision(
                    approved=False,
                    command=pending.command,
                    reason=reason,
                    request_id=request_id,
                )
            )
            count += 1
        return count


BROKER = AuditBroker()

__all__ = ["BROKER", "DEFAULT_TIMEOUT", "AuditBroker", "Decision"]
