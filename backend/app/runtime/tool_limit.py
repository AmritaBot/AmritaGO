"""Tool-budget policy on top of AmritaCore's ReAct strategy.

``graceful`` is AmritaCore's behaviour: wind down and answer, with
``tool_limit_refusal_rounds`` requests to do it. ``ask`` parks the run and lets
the browser decide — ``continue`` appends requests *inside this turn*, ``优雅收尾``
winds it down, ``直接中断`` drops it. ``abort`` works by not marking the budget
exhausted, so the loop's guards break out immediately.

A *round* here is one user message; the budget counts **LLM requests** inside it,
i.e. one per tool-calling turn the model takes. The budget belongs to the run's
config copy, so a grant is just a larger ``agent_tool_call_limit``: no new turn,
no new channel, no framework-level cap.

The same strategy also owns *single* tool execution (:meth:`call_tool`), because
that is the only place both the cancel button and the global timeout can be
enforced without touching AmritaCore: a call is announced, raced against the
user's cancel, and bounded by ``tool_timeout``.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, cast

from amrita_core.builtins.agent import ReActAgentStrategy
from amrita_core.config import AmritaConfig, get_config
from amrita_core.contents import MessageMetadataPayloadSystem, MessageWithMetadata
from amrita_core.types import ToolCall, UniResponse
from amrita_sense.logging import logger

from app.core.settings import load_settings
from app.runtime.limit_gate import LIMITS
from app.runtime.stream_hub import HUB
from app.runtime.tool_gate import CANCELLED_PREFIX, TIMEOUT_PREFIX, TOOLS

# Fallback when ``settings.json`` cannot be read; matches the field default.
DEFAULT_TOOL_TIMEOUT = 600.0

# What the transcript says when the user cuts a spent run off.
ABORT_NOTICE = (
    "已按你的选择中断本轮：工具调用预算用完，模型不再继续调用工具。需要接着做时发一条新消息即可。"
)


def for_run(config: AmritaConfig | None = None) -> AmritaConfig:
    """A per-run config copy, so a budget change stays inside one session.

    ``ChatObject`` falls back to the process-wide config; passing a copy keeps
    ``function_config.agent_tool_call_limit`` private to the run that changed it.

    The refusal-request budget is folded in here as well, because it is a *user*
    setting (设置 -> 行为) covering what a cut-off run may still do — AmritaCore
    reads it off the config, and the app-level knob would otherwise be ignored.
    """

    from app.core.settings import load_settings_sync

    copy = (config or get_config()).model_copy(deep=True)
    try:
        copy.function_config.agent_tool_refusal_rounds = int(
            load_settings_sync().tool_limit_refusal_rounds
        )
    except (OSError, ValueError):  # pragma: no cover - unreadable settings file
        pass
    return copy


async def _abandon(task: asyncio.Task[Any]) -> None:
    """Stop a call whose result is no longer wanted, and wait for it to unwind.

    ``asyncio.wait`` rather than ``await task``: the task's own cancellation is
    an outcome to wait for, not a cancellation of *this* coroutine.
    """
    task.cancel()
    await asyncio.wait({task})


class LimitAwareStrategy(ReActAgentStrategy):
    """``ReActAgentStrategy`` that can buy more LLM requests before ending a turn.

    The loop guards call :meth:`on_limited` once the request budget is spent and
    then read :attr:`budget_exhausted` to decide whether to keep looping. That
    one flag carries two questions — "may the loop continue?" and "should tools
    be refused?" — and a grant answers them differently, so they are split
    here: the guards see True (keep looping) while ``_budget_exhausted`` stays
    False, so the tool gate keeps executing calls.
    """

    # ``called_count`` we last asked at; both guards may ask for one request.
    _asked_at: int = -1
    # A grant is outstanding, so the guards must keep looping.
    _granted: bool = False

    @property
    def budget_exhausted(self) -> bool:
        """Whether the guards should keep the loop alive.

        Not quite "the budget is spent": after a grant it is not spent, but the
        loop still has to continue, which is all the guards ask about.
        """
        return self._budget_exhausted or self._granted

    async def on_limited(self) -> None:
        if self._budget_exhausted:
            # Already winding down; the guards call this on every later request.
            return

        requests = self._requests()
        if self._asked_at == requests:
            return

        settings = await load_settings()
        channel = self._channel()
        if settings.tool_limit_policy != "ask" or channel is None:
            # Nobody to ask (CLI run, dead socket): wind down.
            await super().on_limited()
            return

        self._asked_at = requests
        self._granted = False
        extension = int(settings.tool_limit_extension)
        action = await LIMITS.ask(
            channel,
            budget=int(self.config.function_config.agent_tool_call_limit),
            extension=extension,
            timeout=float(settings.tool_limit_timeout),
        )
        if action == "continue":
            await self._extend(requests, extension)
            return
        if action == "abort":
            await self._abort()
            return

        # ``graceful``: AmritaCore's own wind-down.
        await super().on_limited()

    async def call_tool(self, tool_call: ToolCall) -> str:
        """Execute one tool call, bounded in time and cancelable by the user.

        The call is announced on the stream first, so the UI offers 取消 for
        exactly the window in which cancelling means something. Both endings are
        returned as ordinary results: the model is told what happened, keeps its
        context and can try something else — that is the difference between
        cancelling a call and killing the turn.

        The timeout is a backstop for tools that ignore their own limits (an MCP
        server that never answers, say). A tool that enforces a shorter limit of
        its own still wins, because it returns before this one fires.
        """
        timeout = await self._tool_timeout()
        gate = TOOLS.open(tool_call.id, tool_call.function.name, asyncio.get_running_loop())
        await self._publish(
            {
                "type": "tool_running",
                "tool_id": tool_call.id,
                "tool": tool_call.function.name,
                "timeout": timeout,
                "started_at": gate.started_at,
            }
        )
        task = asyncio.create_task(super().call_tool(tool_call))
        # ``aborted`` means the turn itself was stopped; the UI never sees it,
        # but it keeps the published outcome honest.
        outcome = "aborted"
        try:
            done, _ = await asyncio.wait(
                {task, gate.future},
                timeout=timeout if timeout > 0 else None,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                # ``result()`` re-raises the tool's own failure, which the
                # framework turns into an ``ERR:`` tool result.
                failed = task.cancelled() or task.exception() is not None
                outcome = "failed" if failed else "done"
                return task.result()
            if gate.future in done:
                outcome = "cancelled"
                await _abandon(task)
                return await self._tool_cancelled(tool_call, gate.future.result())
            outcome = "timeout"
            await _abandon(task)
            return await self._tool_timed_out(tool_call, timeout)
        finally:
            TOOLS.close(tool_call.id)
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await self._publish(
                    {"type": "tool_finished", "tool_id": tool_call.id, "outcome": outcome}
                )

    async def _tool_timeout(self) -> float:
        """The global wall-clock limit for one call; ``0`` means no limit."""
        try:
            return float((await load_settings()).tool_timeout)
        except (OSError, ValueError):  # pragma: no cover - unreadable settings file
            return DEFAULT_TOOL_TIMEOUT

    async def _tool_cancelled(self, tool_call: ToolCall, reason: str) -> str:
        notice = (
            f"{CANCELLED_PREFIX}（原因：{reason}）。"
            "它没有产生结果；请基于已有信息继续，或换一种做法。"
        )
        await self._tool_notice(tool_call, notice, "tool_cancelled")
        return notice

    async def _tool_timed_out(self, tool_call: ToolCall, timeout: float) -> str:
        notice = (
            f"{TIMEOUT_PREFIX} {timeout:g} 秒仍未返回，已被终止。"
            "请换一种更快的做法，或基于已有信息继续。"
        )
        await self._tool_notice(tool_call, notice, "tool_timeout")
        return notice

    async def _tool_notice(self, tool_call: ToolCall, content: str, extra_type: str) -> None:
        """Record the ending in the transcript, not only on the live stream.

        ``tool_running`` / ``tool_finished`` are transient — they exist so the
        cancel button can appear and disappear. *Why* a call ended is worth
        keeping, so it is emitted as a normal meta event that history stores.
        """
        metadata = cast(
            MessageMetadataPayloadSystem,
            {
                "type": "system",
                "message": content,
                "extra_type": extra_type,
                "tool_id": tool_call.id,
                "function_name": tool_call.function.name,
            },
        )
        await self.io_stream.yield_response(
            MessageWithMetadata(content=content, metadata=metadata)
        )

    async def _publish(self, event: dict[str, Any]) -> None:
        """Send a transient event: the UI needs it, the transcript does not."""
        channel = self._channel()
        if channel is None or channel.done:
            return
        await channel.publish(event)

    async def _extend(self, requests: int, extension: int) -> None:
        """Append ``extension`` LLM requests to *this* turn.

        The guards re-read ``agent_tool_call_limit`` off the run's config copy
        right after ``on_limited`` returns, so raising it here is enough to keep
        the same turn going. ``_refusal_rounds`` is cleared because no wind-down
        happened; ``_budget_exhausted`` stays False so tools keep executing.
        """
        new_limit = requests + extension
        self.config.function_config.agent_tool_call_limit = new_limit
        self._refusal_rounds = 0
        self._budget_exhausted = False
        self._granted = True
        logger.info(f"Tool budget extended by {extension} requests -> {new_limit}")
        await self.io_stream.yield_response(
            MessageWithMetadata(
                content=(
                    f"用户选择继续迭代：本轮再追加 {extension} 次 LLM 请求，"
                    f"预算上限提高到 {new_limit} 次。"
                ),
                metadata=MessageMetadataPayloadSystem(
                    type="system",
                    message="Tool budget extended",
                    extra_type="tool_limit_continue",
                ),
            )
        )

    async def _abort(self) -> None:
        """End the turn here, without a wind-down request.

        Leaving ``_budget_exhausted`` False is the whole mechanism: the loop
        guards read it as "this strategy did not opt into winding down" and
        break out (``BreakLoop`` on the legacy path, ``False`` from the step
        conditionals). Storing a final response keeps ``LLM_COMPLETION`` from
        asking the model one last time, which is the difference between
        interrupting a run and merely telling it to hurry up.
        """
        channel = self._channel()
        if channel is not None:
            channel.aborted = True
        logger.info("User aborted a run that spent its tool budget")
        # Both the live stream and the stored response carry this line.
        await self.io_stream.yield_response(ABORT_NOTICE)
        self._store_final_response(
            UniResponse(role="assistant", content=ABORT_NOTICE, tool_calls=None)
        )

    def _requests(self) -> int:
        loop = getattr(self.chat_object, "_di_loop", None)
        return int(getattr(loop, "called_count", 0) or 0)

    def _channel(self) -> Any | None:
        session_id = getattr(self.chat_object, "session_id", None)
        return HUB.latest(session_id) if session_id else None


__all__ = ["ABORT_NOTICE", "DEFAULT_TOOL_TIMEOUT", "LimitAwareStrategy", "for_run"]
