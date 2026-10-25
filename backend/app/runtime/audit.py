"""Human-in-the-loop confirmation for tool calls.

A tool that is about to do something irreversible asks the browser first. The
agent task then parks on a future; the WebSocket handler resolves it when the
user approves (possibly with an edited command), rejects (with a reason), or
the timeout fires.

Why this lives in its own module rather than in the shell plugin:

* The *transport* is generic — publish a ``confirm`` event into the session's
  :class:`~.stream_hub.StreamChannel`, wait for a ``confirm_response`` frame on
  the chat socket. Nothing about it is shell-specific.
* The *policy* stays in the tool. The shell plugin decides when a command is
  worth asking about; this module only knows how to ask.

Failure is closed, but not fatal: a rejected, timed-out or undeliverable
confirmation returns a decision with ``approved=False`` and a human-readable
reason, which the tool hands back to the model as an ordinary tool result. A
turn never hangs because nobody answered.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from amrita_sense.logging import logger

#: How long a tool waits for an answer before giving up.
DEFAULT_TIMEOUT = 180.0


@dataclass
class Decision:
    approved: bool
    #: The command to run, which the user may have edited.
    command: str
    #: Present when rejected; fed back to the model so it can adapt.
    reason: str = ""
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


class AuditBroker:
    """Pending confirmations, keyed by request id."""

    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}

    def pending(self) -> list[dict[str, Any]]:
        return [
            {"id": key, "command": item.command, "created_at": item.created_at}
            for key, item in self._pending.items()
        ]

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
        timeout: float = DEFAULT_TIMEOUT,
    ) -> Decision:
        """Publish a confirmation request and wait for the browser's answer."""
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        pending = _Pending(future=loop.create_future(), command=command)
        self._pending[request_id] = pending
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
                    "timeout": timeout,
                }
            )
            try:
                decision = await asyncio.wait_for(pending.future, timeout)
            except asyncio.TimeoutError:
                return Decision(
                    approved=False,
                    command=command,
                    # ``:g`` keeps a fractional timeout readable (``0.3 秒``).
                    reason=f"等了 {timeout:g} 秒没有收到确认，已放弃执行。",
                    timed_out=True,
                    request_id=request_id,
                )
            decision.request_id = request_id
            return decision
        finally:
            self._pending.pop(request_id, None)

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
