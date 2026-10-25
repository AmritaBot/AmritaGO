"""Tool-budget policy on top of AmritaCore's ReAct strategy.

AmritaCore already handles an exhausted budget: ``on_limited()`` tells the model
to stop calling tools and answer with what it has. That is the right default —
a run that keeps going forever is a run nobody is watching — but it is silent
about one legitimate case: the agent was making progress and the user would have
paid for more.

So this module adds a second policy, selected by ``settings.tool_limit_policy``:

``graceful``
    Exactly AmritaCore's behaviour: wind down, answer, finish. The *number* of
    wind-down rounds is the user's ``tool_limit_extension`` setting, applied to
    ``agent_tool_refusal_rounds`` for the run.

``ask``
    Park the run and ask the browser. Three answers, three endings:

    * **继续迭代** does not extend this turn — it winds down exactly as above and
      a *new turn* starts afterwards with its normal budget. A budget you can
      raise on demand is not a budget.
    * **优雅收尾** winds this turn down: the model answers with what it has.
    * **直接中断** cuts the turn off where it stands. No wind-down round, no last
      model call — the run stops and says so.

    A timeout, or a Stop press, resolves to the wind-down rather than losing the
    work already done.

How the policies share one code path
------------------------------------
Every answer but ``abort`` ends in ``super().on_limited()``; the only difference
is whether ``continue_requested`` gets set on the stream channel first. The
runtime watches that flag after the turn finishes and spawns the successor, so
this class never touches the loop's counter, never rewrites its own config, and
cannot corrupt a run halfway through.

``abort`` is the exception, and it works by *not* marking the budget exhausted:
the loop guards interpret that as "do not wind down" and break out immediately,
which is exactly the behaviour asked for. The turn still has an answer stored
(a one-line notice) so ``LLM_COMPLETION`` does not quietly ask the model again.

``_asked`` stops a second guard (the step loop has two) from asking twice in the
same round.
"""

from __future__ import annotations

from typing import Any

from amrita_core.builtins.agent import ReActAgentStrategy
from amrita_core.config import AmritaConfig, get_config
from amrita_core.contents import MessageMetadataPayloadSystem, MessageWithMetadata
from amrita_core.types import UniResponse
from amrita_sense.logging import logger

from app.core.settings import load_settings
from app.runtime.limit_gate import LIMITS
from app.runtime.stream_hub import HUB

#: What the transcript says when the user cuts a spent run off.
ABORT_NOTICE = (
    "已按你的选择中断本轮：工具调用预算用完，模型不再继续调用工具。需要接着做时发一条新消息即可。"
)


def for_run(config: AmritaConfig | None = None) -> AmritaConfig:
    """A per-run config copy, so a budget change stays inside one session.

    ``ChatObject`` falls back to the process-wide config; passing a copy keeps
    ``function_config.agent_tool_call_limit`` private to the run that changed it.

    The refusal-round budget is folded in here as well, because it is a *user*
    setting (设置 → 行为) covering what a cut-off run may still do — AmritaCore
    reads it off the config, and the app-level knob would otherwise be ignored.
    """
    from app.core.settings import load_settings_sync

    copy = (config or get_config()).model_copy(deep=True)
    try:
        copy.function_config.agent_tool_refusal_rounds = int(
            load_settings_sync().tool_limit_extension
        )
    except (OSError, ValueError):  # pragma: no cover - unreadable settings file
        pass
    return copy


class LimitAwareStrategy(ReActAgentStrategy):
    """``ReActAgentStrategy`` that can ask before ending the turn."""

    #: Set once this round asked; the guards call ``on_limited`` twice.
    _asked: bool = False

    async def on_limited(self) -> None:
        if self._asked:
            return

        settings = await load_settings()
        channel = self._channel()
        if settings.tool_limit_policy != "ask" or channel is None:
            # Nobody to ask (CLI run, dead socket): wind down.
            await super().on_limited()
            return

        self._asked = True
        budget = int(self.config.function_config.agent_tool_call_limit)
        action = await LIMITS.ask(
            channel,
            budget=budget,
            timeout=float(settings.tool_limit_timeout),
        )
        if action == "continue":
            channel.continue_requested = True
            logger.info("User asked to continue; a new turn will start after this one")
            await self.io_stream.yield_response(
                MessageWithMetadata(
                    content=(
                        "用户选择继续迭代。本轮先按已有信息收尾，随后会自动开启新一轮继续处理。"
                    ),
                    metadata=MessageMetadataPayloadSystem(
                        type="system",
                        message="Continue requested; new turn follows",
                        extra_type="tool_limit_continue",
                    ),
                )
            )
            # A new turn follows, so this one still has to end tidily.
            await super().on_limited()
            return

        if action == "abort":
            await self._abort()
            return

        # ``graceful``: AmritaCore's own wind-down.
        await super().on_limited()

    async def _abort(self) -> None:
        """End the turn here, without a wind-down round.

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

    def _channel(self) -> Any | None:
        session_id = getattr(self.chat_object, "session_id", None)
        return HUB.latest(session_id) if session_id else None


__all__ = ["ABORT_NOTICE", "LimitAwareStrategy", "for_run"]
