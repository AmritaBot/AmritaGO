"""Cancel one running tool call from the browser.

The tool loop runs inside AmritaCore's strategy, out of reach of the chat
socket. This module is the hand-off: the strategy registers each call here
before executing it, and the socket resolves that call's future when the user
presses 取消. Cancelling ends the *call*, not the turn — the model receives a
plain result string saying what happened and why, so it can pick another
approach instead of the whole run dying.

The same registry is what the UI reads to decide whether a tool row still
offers a cancel button: a call is cancelable exactly while it is in here.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from amrita_sense.logging import logger

    # Supply a default reason for clients that omit it.
DEFAULT_REASON = "用户没有填写原因"

    # Use verb-led cancellation and timeout messages that cannot imply success.
CANCELLED_PREFIX = "已取消：用户手动终止了这次工具调用"
TIMEOUT_PREFIX = "已超时：这次工具调用超过"


@dataclass
class RunningTool:
    """One in-flight call, as the socket sees it."""

    tool_id: str
    tool: str
    started_at: float
    # Resolved with the user's reason when the call is cancelled.
    future: asyncio.Future[str]

    def public(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "tool": self.tool,
            "started_at": self.started_at,
        }


class ToolGate:
    """In-flight tool calls, keyed by the provider's ``tool_call`` id."""

    def __init__(self) -> None:
        self._running: dict[str, RunningTool] = {}

    def running(self) -> list[dict[str, Any]]:
        return [entry.public() for entry in self._running.values()]

    def open(
        self,
        tool_id: str,
        tool: str,
        loop: asyncio.AbstractEventLoop,
    ) -> RunningTool:
        """Register a call that is about to execute."""
        entry = RunningTool(
            tool_id=tool_id,
            tool=tool,
            started_at=time.time(),
            future=loop.create_future(),
        )
        self._running[tool_id] = entry
        return entry

    def close(self, tool_id: str) -> None:
        """Forget a call; safe to call twice, or after a cancel."""
        self._running.pop(tool_id, None)

    def cancel(self, tool_id: str, reason: str = "") -> bool:
        """Ask one call to stop. Returns False when it already finished."""
        entry = self._running.get(tool_id)
        if entry is None:
            logger.info(f"Tool call {tool_id} is no longer running; cancel ignored")
            return False
        if entry.future.done():
            return False
        entry.future.set_result(reason.strip() or DEFAULT_REASON)
        return True

    def cancel_all(self, reason: str) -> int:
        """Stop everything outstanding — used when the whole turn is stopped."""
        count = 0
        for entry in list(self._running.values()):
            if entry.future.done():
                continue
            entry.future.set_result(reason)
            count += 1
        return count


TOOLS = ToolGate()

__all__ = [
    "CANCELLED_PREFIX",
    "DEFAULT_REASON",
    "TIMEOUT_PREFIX",
    "TOOLS",
    "RunningTool",
    "ToolGate",
]
