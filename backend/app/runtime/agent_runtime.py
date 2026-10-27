"""Agent runtime: wires AmritaCore backends to the on-disk session store.

:func:`run_turn` never writes to a socket: it publishes events into a
:class:`~.stream_hub.StreamChannel` that the WebSocket handler drains, so a
dropped connection cannot fail a run and reconnecting replays the buffer.

Event ``type`` values: ``start``, ``delta``, ``reasoning``, ``meta``,
``error``, ``done``.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import Any

from amrita_core import ChatObject
from amrita_core.base.adapter import MessageContent
from amrita_core.base.backend import AbilityBackend, BackendSlots, MemoryBackend
from amrita_core.components.compaction import ContextCompactor, split_history
from amrita_core.config import get_config
from amrita_core.consts import DEFAULT_TEMPLATE
from amrita_core.contents import MessageWithMetadata
from amrita_core.contexts import AbilityContext, DatabackendOptions
from amrita_core.preset import MultiPresetManager
from amrita_core.tools.manager import MultiToolsManager
from amrita_core.tools.mcp import MultiClientManager
from amrita_core.types import MemoryModel, ModelPreset
from amrita_core.types.preset import resolve_max_context
from amrita_sense.logging import logger

from app.agents import AgentPreset
from app.capabilities import CapabilityCatalog
from app.capabilities.permissions import LABELS as ACCESS_LABELS
from app.capabilities.skills import build_skill_usage_prompt
from app.core.config import DEFAULT_SYSTEM_PROMPT
from app.core.settings import AppSettings
from app.runtime import events, fallback_watch, session_memory
from app.runtime.attachments import build_user_content
from app.runtime.billing import SessionBillingBackend, TurnUsage, summarize
from app.runtime.commands import COMMANDS, CommandContext, CommandMatch
from app.runtime.sessions import (
    HistoryEntry,
    append_history,
    bump_session,
    get_meta,
    load_context,
    record_turn_usage,
    save_context,
    save_meta,
)
from app.runtime.stream_hub import StreamChannel
from app.runtime.tool_limit import for_run
from app.runtime.workflows import DEFAULT_WORKFLOW, WORKFLOWS


class SessionMemoryBackend(MemoryBackend):
    """Loads/commits the session's JSON context file."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    async def load_memory(self, session_id: str) -> MemoryModel:
        del session_id
        memory = await load_context(self._session_id)
        return memory.model_copy(deep=True)

    async def commit_memory(self, session_id: str, memory: MemoryModel) -> None:
        del session_id
        await save_context(self._session_id, memory)


class SessionAbilityBackend(AbilityBackend):
    """Supplies tools, MCP clients and presets from one capability catalog."""

    def __init__(self, preset: ModelPreset, catalog: CapabilityCatalog) -> None:
        self._preset = preset
        self._catalog = catalog

    async def load_ability_all(self, session_id: str) -> AbilityContext:
        del session_id
        tools = self._catalog.build_tools()
        return AbilityContext(
            tools=tools,
            presets=self._build_presets(),
            mcp=self._catalog.build_mcp_view(tools),
        )

    async def load_mcp_clients(self, session_id: str) -> MultiClientManager:
        del session_id
        return self._catalog.build_mcp_view()

    async def load_tools(self, session_id: str) -> MultiToolsManager:
        del session_id
        return self._catalog.build_tools()

    async def load_presets(self, session_id: str) -> MultiPresetManager:
        del session_id
        return self._build_presets()

    def _build_presets(self) -> MultiPresetManager:
        presets = MultiPresetManager()
        presets.add_preset(self._preset)
        presets.set_default_preset(self._preset)
        return presets


def _system_prompt(
    settings: AppSettings,
    agent: AgentPreset | None = None,
    session_id: str | None = None,
) -> str:
    """The preset's own body, plus the sections the runtime owns.

    The system prompt belongs to the agent preset — a mode is defined by its
    instructions. There is no global prompt to merge in: the only things added
    here are capabilities the user enabled globally (skills) and the session
    scratch directory the run actually created.
    """
    base = agent.prompt.strip() if agent and agent.prompt.strip() else DEFAULT_SYSTEM_PROMPT
    sections = [base.rstrip()]
    if agent is not None and agent.memory == "session" and session_id:
        sections.append(session_memory.describe(session_id))
    skill_section = build_skill_usage_prompt(settings.enabled_skills)
    if skill_section:
        sections.append(skill_section)
    return "\n\n".join(sections) + "\n"


def _serialize_meta(item: MessageWithMetadata) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key, value in dict(item.metadata).items():
        if isinstance(value, BaseException):
            metadata[key] = f"{type(value).__name__}: {value}"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            metadata[key] = value
        else:
            metadata[key] = str(value)
    return {"content": item.content, "metadata": metadata}


def _is_reasoning(metadata: dict[str, Any]) -> bool:
    # Both adapters spell reasoning differently: ``type`` vs ``extra_type``.
    return (
        metadata.get("type") == "reasoning_chunk" or metadata.get("extra_type") == "reasoning_chunk"
    )


def _is_step(metadata: dict[str, Any]) -> bool:
    return str(metadata.get("type") or "").startswith("step")


def _is_tool_call(metadata: dict[str, Any]) -> bool:
    return metadata.get("type") in {"function_call", "skill_call", "tool_call"}


def _error_text(exc: BaseException) -> str:
    """The failure plus every cause behind it.

    AmritaCore's preset fallback raises ``FallbackFailed("No preset fallback
    available, exiting!")`` while handling the real provider error. Reporting
    only the outer message hides the status code and the provider's body, which
    is exactly what a user needs to diagnose a wrong key or a bad model id, so
    the implicit chained exceptions are appended.
    """
    lines = [f"{type(exc).__name__}: {exc}"]
    seen = {id(exc)}
    current = exc.__cause__ or exc.__context__
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        lines.append(f"起因：{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return "\n".join(lines)


def _is_fallback_only(text: str | None) -> bool:
    """True when the message is only the fallback wrapper, with no cause."""
    if not text:
        return False
    return "FallbackFailed" in text and "起因" not in text


def _compaction_plan(
    memory: MemoryModel, config: Any, preset: ModelPreset
) -> tuple[int, int] | None:
    """``(folded, kept)`` when this turn will fold history, else ``None``.

    Asked of the same :class:`ContextCompactor` the workflow builds, with the
    same per-run config, so the notice cannot disagree with what actually
    happens: if the predicate fires here, ``MANAGE_CONTEXT`` is about to run.
    """
    compactor = ContextCompactor(config=config, preset=preset)
    if not compactor.should_compact(memory):
        return None
    prefix, tail = split_history(memory.messages)
    if not prefix:
        return None
    return len(prefix), len(tail)


def _compaction_outcome(
    fold_plan: tuple[int, int],
    memory: MemoryModel | None,
    previous_abstract: str,
) -> dict[str, Any]:
    """The notice that says what the fold actually did.

    The predicate only says a fold was *due*; ``fold`` returns ``None`` when the
    summarizer produced nothing, and history is then left untouched. Reporting
    the two cases differently is the difference between a status line and a
    guess.
    """
    folded, kept = fold_plan
    summary = (memory.abstract if memory is not None else "") or ""
    if summary and summary != (previous_abstract or ""):
        return {
            "type": "meta",
            "content": (
                f"已把较早的 {folded} 条消息折叠为摘要（{len(summary)} 字），保留最近 {kept} 条。"
            ),
            "metadata": {
                "type": "system",
                "extra_type": "compaction_finished",
                "folded": folded,
                "kept": kept,
                "summary_chars": len(summary),
            },
        }
    return {
        "type": "meta",
        "content": "上下文压缩没有产出摘要，历史保持原样。",
        "metadata": {
            "type": "system",
            "extra_type": "compaction_skipped",
            "folded": folded,
            "kept": kept,
        },
    }


async def _iter_events(
    *,
    session_id: str,
    user_input: str,
    settings: AppSettings,
    preset: ModelPreset,
    agent: AgentPreset | None = None,
    attachments: list[dict[str, Any]] | None = None,
    requested_access: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    fallback_watch.start_turn()

    catalog = await CapabilityCatalog.collect(
        settings, agent, requested_access=requested_access, session_id=session_id
    )
    workflow = WORKFLOWS.require(
        (agent.workflow if agent else None) or settings.active_workflow or DEFAULT_WORKFLOW
    )
    agent_name = agent.name if agent else "standard"
    billing = SessionBillingBackend(session_id)
    started_at = time.time()
    content = build_user_content(user_input, attachments or [])

    # The scratch directory must exist before the prompt names it.
    if agent is not None and agent.memory == "session":
        session_memory.ensure(session_id)

    run_config = for_run()
    # Peek first: "compacting" must be announced before the fold inside the request.
    preview: MemoryModel | None = None
    fold_plan: tuple[int, int] | None = None
    try:
        preview = await load_context(session_id)
        fold_plan = _compaction_plan(preview, run_config, preset)
    except Exception:  # pragma: no cover - an unreadable context is not fatal here
        preview = None
        fold_plan = None
    compaction_reported = False

    chat = ChatObject(
        train={"role": "system", "content": _system_prompt(settings, agent, session_id)},
        user_input=content,
        session_id=session_id,
        preset=preset,
        # A per-run config copy keeps a budget change inside this session.
        config=run_config,
        train_template=agent.resolved_template() if agent else DEFAULT_TEMPLATE,
        agent_strategy=workflow.strategy,
        # ``None`` keeps AmritaCore's default graph; the step workflow passes its own.
        workflow=workflow.pipeline,
        backend=BackendSlots(
            SessionAbilityBackend(preset, catalog),
            SessionMemoryBackend(session_id),
            billing,
        ),
        backend_options=DatabackendOptions(skip_mcp_fetch=False, skip_tools_fetch=False),
    )

    yield {
        "type": "start",
        "session_id": session_id,
        "agent": agent_name,
        "workflow": workflow.name,
        "workflow_title": workflow.title,
        "workflow_mode": workflow.mode,
        "workflow_mode_label": workflow.mode_label,
        "preset": preset.name,
        "model": preset.model,
        "tools": catalog.tool_count(),
        "access": catalog.access_mode,
        "access_label": ACCESS_LABELS.get(catalog.access_mode, catalog.access_mode),
    }

    if fold_plan is not None:
        yield {
            "type": "meta",
            "content": (
                f"正在压缩上下文：把较早的 {fold_plan[0]} 条消息折叠成摘要，"
                f"保留最近 {fold_plan[1]} 条。"
            ),
            "metadata": {
                "type": "system",
                "extra_type": "compaction_started",
                "folded": fold_plan[0],
                "kept": fold_plan[1],
            },
        }

    history_events: list[dict[str, Any]] = []
    reasoning_parts: list[str] = []
    answer_parts: list[str] = []
    error_text: str | None = None
    steps = 0
    tool_calls = 0
    # A call is announced twice (start and finish), so count ids, not events.
    counted_tools: set[str] = set()
    first_delta_at: float | None = None
    last_delta_at: float | None = None
    last_rounds = 0

    async with chat.begin():
        async for item in chat.io_stream.get_response_generator():
            if isinstance(item, MessageWithMetadata):
                payload = _serialize_meta(item)
                metadata = payload["metadata"]
                if _is_reasoning(metadata):
                    # Reasoning is always streamed and persisted; the UI decides how loud it is.
                    reasoning_parts.append(payload["content"])
                    yield {"type": "reasoning", "text": payload["content"]}
                    continue
                if metadata.get("type") == "error":
                    # Keep the failure so the bubble still explains itself after a reload.
                    error_text = str(metadata.get("error") or payload["content"])
                if _is_step(metadata):
                    steps += 1
                if _is_tool_call(metadata):
                    # Deduplicate call events by tool_id.
                    tool_id = str(metadata.get("tool_id") or "")
                    if tool_id and tool_id not in counted_tools:
                        counted_tools.add(tool_id)
                        tool_calls += 1
                history_events.append(payload)
                yield {"type": "meta", **payload}
                rounds = len(billing.records)
                if rounds != last_rounds:
                    last_rounds = rounds
                    yield {"type": "usage", "usage": _live_usage(billing.records)}
                    if fold_plan is not None and not compaction_reported:
                        # First request runs after ``MANAGE_CONTEXT``, so this proves the fold landed.
                        compaction_reported = True
                        yield _compaction_outcome(
                            fold_plan,
                            chat._di_memory.memory,
                            preview.abstract if preview is not None else "",
                        )
                continue

            text = (
                str(item.get_content())
                if isinstance(item, MessageContent)
                else item
                if isinstance(item, str)
                else str(item)
            )
            if not text:
                continue
            now = time.time()
            first_delta_at = first_delta_at or now
            last_delta_at = now
            answer_parts.append(text)
            yield {"type": "delta", "text": text}

    if fold_plan is not None and not compaction_reported:
        # No request completed, so the outcome was never announced.
        compaction_reported = True
        yield _compaction_outcome(
            fold_plan,
            chat._di_memory.memory,
            preview.abstract if preview is not None else "",
        )

    finished_at = time.time()
    answer = "".join(answer_parts)

    if _is_fallback_only(error_text):
        # Only the wrapper was streamed; the cause came from the PRESET_FALLBACK matcher.
        cause = fallback_watch.describe()
        if cause:
            error_text = f"{error_text}\n起因：{cause}"

    usage = summarize(
        billing.records,
        started_at=started_at,
        finished_at=finished_at,
        first_delta_at=first_delta_at,
        last_delta_at=last_delta_at,
        fallback_usage=chat._di_resp.extra_usage,
        context_messages=len(chat._di_memory.memory.messages) if chat._di_memory.memory else None,
        max_context=resolve_max_context(preset, get_config()),
        rounds=max(1, len(billing.records)),
        steps=steps,
        tool_calls=tool_calls,
        workflow=workflow.name,
        agent=agent_name,
        preset=preset.name,
        model=preset.model,
        error=error_text,
    )

    if answer or error_text:
        await append_history(
            session_id,
            HistoryEntry(
                role="assistant",
                content=answer,
                reasoning="".join(reasoning_parts) or None,
                events=history_events,
                error=error_text,
                usage=usage,
                steps=steps,
                tool_calls=tool_calls,
                workflow=workflow.name,
                agent=agent_name,
            ),
        )

    totals, meta = await record_turn_usage(session_id, usage)
    if meta is not None:
        if meta.title in {"", "新会话"}:
            meta.title = user_input.strip().splitlines()[0][:40] or "新会话"
        meta.preset = preset.name
        meta.agent = agent_name
        await save_meta(meta)

    yield {
        "type": "done",
        "usage": usage.to_public(),
        "totals": totals.to_public(),
        "title": meta.title if meta else None,
        "message_count": meta.message_count if meta else None,
        "workflow": workflow.name,
        "agent": agent_name,
    }


def _live_usage(records: list[Any]) -> dict[str, Any]:
    """Mid-run counter: what this turn has spent so far."""
    return {
        "prompt_tokens": sum(record.prompt_tokens for record in records),
        "completion_tokens": sum(record.completion_tokens for record in records),
        "total_tokens": sum(record.total_tokens for record in records),
        "requests": len(records),
    }


async def _run_command(
    channel: StreamChannel,
    *,
    session_id: str,
    settings: AppSettings,
    match: CommandMatch,
) -> None:
    """Run one slash command inside the turn's stream.

    Not a plugin hook and not an agent run: no preset, no tools, no budget. The
    user message is already in history, and whatever the handler emits becomes
    this turn's assistant entry — so ``/compact`` survives a reload as a step
    in the transcript rather than a notice that scrolled away.
    """
    spec = COMMANDS.require(match.name)
    context = CommandContext(
        session_id=session_id,
        args=match.args,
        settings=settings,
        channel=channel,
    )
    text = ""
    try:
        text = (await spec.handler(context)) or ""
    except Exception as exc:
        logger.opt(exception=exc).error(f"Command /{spec.name} failed")
        text = f"指令 /{spec.name} 执行失败：{type(exc).__name__}: {exc}"
        await context.emit(text, {"type": "system", "extra_type": "command_failed"})

    if text or context.events:
        await append_history(
            session_id,
            HistoryEntry(
                role="assistant",
                content=text,
                events=context.events,
                workflow="command",
                agent=spec.name,
            ),
        )
    await bump_session(session_id)
    await channel.publish(
        {
            "type": "done",
            "usage": None,
            "totals": None,
            "title": None,
            "message_count": None,
            "workflow": "command",
            "agent": spec.name,
        }
    )


async def run_turn(
    channel: StreamChannel,
    *,
    session_id: str,
    user_input: str,
    settings: AppSettings,
    preset: ModelPreset | None,
    agent: AgentPreset | None = None,
    attachments: list[dict[str, Any]] | None = None,
    requested_access: str | None = None,
) -> None:
    """Execute one turn, publishing every event into ``channel``.

    Nothing in here depends on a live socket: a browser that navigates away
    mid-answer only stops reading. Reconnect and the buffer replays. Plugin
    events are emitted here (and only here), so a hook cannot fire for a run
    that never started.

    A message that names a registered slash command is answered by that
    command instead of the agent: it needs no preset, no API key and no tools.
    """
    command = COMMANDS.match(user_input)
    if command is not None:
        await append_history(
            session_id,
            HistoryEntry(
                role="user",
                content=user_input,
                attachments=list(attachments or []),
            ),
        )
        await _run_command(channel, session_id=session_id, settings=settings, match=command)
        return

    if preset is None:
        # Defensive: the socket resolves a preset before starting a real turn.
        await channel.publish({"type": "error", "message": "这一轮没有可用的模型"})
        await channel.publish({"type": "done", "usage": None, "totals": None})
        return

    await events.emit(
        events.RUN_START,
        session_id=session_id,
        agent=agent.name if agent else "standard",
        preset=preset.name,
    )
    await append_history(
        session_id,
        HistoryEntry(
            role="user",
            content=user_input,
            attachments=list(attachments or []),
        ),
    )
    error: str | None = None
    try:
        async for event in _iter_events(
            session_id=session_id,
            user_input=user_input,
            settings=settings,
            preset=preset,
            agent=agent,
            attachments=attachments,
            requested_access=requested_access,
        ):
            if event["type"] == "error":
                error = str(event.get("message"))
            await channel.publish(event)
    except Exception as exc:
        logger.opt(exception=exc).error("Agent run failed")
        error = _error_text(exc)
        if _is_fallback_only(error):
            cause = fallback_watch.describe()
            if cause:
                error = f"{error}\n起因：{cause}"
        meta = await get_meta(session_id)
        if meta is not None:
            meta.message_count += 1
            if agent is not None:
                meta.agent = agent.name
            await save_meta(meta)
        await channel.publish({"type": "error", "message": error})
    finally:
        await events.emit(
            events.RUN_END,
            session_id=session_id,
            agent=agent.name if agent else "standard",
            workflow=(
                (agent.workflow if agent else None) or settings.active_workflow or DEFAULT_WORKFLOW
            ),
            error=error,
        )


async def run_once(
    *, session_id: str, user_input: str, settings: AppSettings, preset: ModelPreset
) -> str:
    """Non-streaming helper used by tests and the CLI."""
    return "".join(
        [
            event["text"]
            async for event in _iter_events(
                session_id=session_id,
                user_input=user_input,
                settings=settings,
                preset=preset,
            )
            if event["type"] == "delta"
        ]
    )


__all__ = [
    "SessionAbilityBackend",
    "SessionMemoryBackend",
    "TurnUsage",
    "run_once",
    "run_turn",
]
