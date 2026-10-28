"""The turn timeline: stored in arrival order, migrated from the old split.

Run with ``uv run --no-sync python scripts/check_trace.py``.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("AMRITA_GO_DATA", tempfile.mkdtemp(prefix="amrita-trace-"))

from app.runtime.sessions import (
    HistoryEntry,
    trace_event,
    trace_reasoning,
)


def legacy_migration() -> None:
    """A record written before the timeline existed still loads, in one order."""
    entry = HistoryEntry.model_validate(
        {
            "role": "assistant",
            "content": "答案",
            "reasoning": "先想一下",
            "events": [
                {"content": "Called tool shell", "metadata": {"type": "function_call"}},
                {"content": "步骤", "metadata": {"type": "step"}},
            ],
        }
    )
    kinds = [step["kind"] for step in entry.trace]
    assert kinds == ["reasoning", "event", "event"], entry.trace
    assert entry.trace[0]["text"] == "先想一下", entry.trace
    # The legacy keys are gone: writing this entry back produces a timeline only.
    dumped = entry.model_dump()
    assert "reasoning" not in dumped and "events" not in dumped, dumped
    assert "trace" in dumped, dumped


def interleaved_timeline() -> None:
    """Thinking, a tool call, more thinking, a notice — in that order."""
    trace: list[dict[str, object]] = []
    trace_reasoning(trace, "先想")
    trace_reasoning(trace, "一下")  # streamed chunks join the open step
    trace_event(trace, "Called tool shell", {"type": "function_call"})
    trace_reasoning(trace, "再想")
    trace_event(trace, "已压缩上下文", {"type": "system", "extra_type": "compaction_finished"})

    kinds = [step["kind"] for step in trace]
    assert kinds == ["reasoning", "event", "reasoning", "event"], trace
    # Coalescing must not merge across the tool call.
    assert trace[0]["text"] == "先想一下", trace
    assert trace[2]["text"] == "再想", trace

    entry = HistoryEntry(role="assistant", content="答案", trace=trace)
    restored = HistoryEntry.model_validate(entry.model_dump())
    assert restored.trace == trace, restored.trace


def roundtrip_json() -> None:
    entry = HistoryEntry(role="assistant", content="")
    trace_reasoning(entry.trace, "只有思考")
    assert HistoryEntry.model_validate_json(entry.model_dump_json()).trace == entry.trace


legacy_migration()
interleaved_timeline()
roundtrip_json()
print("trace ok")
