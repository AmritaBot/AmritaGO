"""Per-turn billing and token accounting.

Turns AmritaCore's per-request :class:`BillingRecord` ledger into what the UI
shows: tokens, durations, tokens per second, cache counters and, when the
preset carries a price, money. Records are appended per turn to ``usage.jsonl``
and folded into the session meta so a reload needs no replay.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from amrita_core.base.backend import BillingBackend
from amrita_core.types.billing import BillingRecord
from amrita_core.types.response import UniResponseUsage
from pydantic import BaseModel


class UsageTotals(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit: int = 0
    cache_creation: int = 0
    requests: int = 0
    turns: int = 0
    cost: float = 0.0
    currency: str = "USD"

    def add(self, turn: "TurnUsage") -> None:
        self.prompt_tokens += turn.prompt_tokens
        self.completion_tokens += turn.completion_tokens
        self.total_tokens += turn.total_tokens
        self.cache_hit += turn.cache_hit or 0
        self.cache_creation += turn.cache_creation or 0
        self.requests += turn.requests
        self.turns += 1
        if turn.cost is not None:
            self.cost += turn.cost
            self.currency = turn.currency

    def to_public(self) -> dict[str, Any]:
        return self.model_dump()


class TurnUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit: int | None = None
    cache_creation: int | None = None
    requests: int = 0
    cost: float | None = None
    currency: str = "USD"
    tokens_per_second: float | None = None
    duration_ms: int | None = None
    generation_ms: int | None = None
    started_at: float | None = None
    finished_at: float | None = None
    context_tokens: int | None = None
    max_context: int | None = None
    context_percent: float | None = None
    # Messages the *next* request will carry, not the visible transcript length.
    context_messages: int | None = None
    rounds: int = 1
    steps: int = 0
    tool_calls: int = 0
    workflow: str | None = None
    agent: str | None = None
    preset: str | None = None
    model: str | None = None
    error: str | None = None

    def to_public(self) -> dict[str, Any]:
        return self.model_dump()


class SessionBillingBackend(BillingBackend):
    """Collects the records of one run for the session ledger."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._records: list[BillingRecord] = []

    async def commit_billing(self, session_id: str, records: list[BillingRecord]) -> None:
        if session_id != self._session_id:
            return
        self._records.extend(records)

    async def load_billing(self, session_id: str) -> list[BillingRecord]:
        if session_id != self._session_id:
            return []
        return list(self._records)

    @property
    def records(self) -> list[BillingRecord]:
        return list(self._records)


def _money(records: list[BillingRecord]) -> tuple[float | None, str]:
    total = Decimal("0")
    currency: str | None = None
    for record in records:
        rate = record.rate
        if rate is None:
            continue
        per = Decimal(rate.per)
        total += (
            Decimal(record.prompt_tokens) * rate.input
            + Decimal(record.completion_tokens) * rate.output
        ) / per
        currency = rate.currency
    if currency is None:
        return None, "USD"
    return float(total), currency


def summarize(
    records: list[BillingRecord],
    *,
    started_at: float,
    finished_at: float,
    first_delta_at: float | None = None,
    last_delta_at: float | None = None,
    fallback_usage: UniResponseUsage | None = None,
    context_tokens: int | None = None,
    context_messages: int | None = None,
    max_context: int | None = None,
    rounds: int = 1,
    steps: int = 0,
    tool_calls: int = 0,
    workflow: str | None = None,
    agent: str | None = None,
    preset: str | None = None,
    model: str | None = None,
    error: str | None = None,
) -> TurnUsage:
    prompt = sum(record.prompt_tokens for record in records)
    completion = sum(record.completion_tokens for record in records)
    total = sum(record.total_tokens for record in records)
    cache_hit = sum(record.cache_hit or 0 for record in records)
    cache_creation = sum(record.cache_creation or 0 for record in records)

    if not records and fallback_usage is not None:
        prompt = fallback_usage.prompt_tokens or 0
        completion = fallback_usage.completion_tokens or 0
        total = fallback_usage.total_tokens or 0
        cache_hit = fallback_usage.cache_hit or 0
        cache_creation = fallback_usage.cache_creation or 0
        context_tokens = context_tokens or prompt

    generation_ms: int | None = None
    tokens_per_second: float | None = None
    if first_delta_at is not None and last_delta_at is not None:
        generation = last_delta_at - first_delta_at
        if generation > 0:
            generation_ms = int(generation * 1000)
            tokens_per_second = round(completion / generation, 2)

    if context_tokens is None and records:
        context_tokens = records[-1].prompt_tokens
    percent: float | None = None
    if context_tokens is not None and max_context:
        percent = round(context_tokens / max_context * 100, 2)

    cost, currency = _money(records)

    return TurnUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cache_hit=cache_hit,
        cache_creation=cache_creation,
        requests=len(records),
        cost=cost,
        currency=currency,
        tokens_per_second=tokens_per_second,
        duration_ms=max(0, int((finished_at - started_at) * 1000)),
        generation_ms=generation_ms,
        started_at=started_at,
        finished_at=finished_at,
        context_tokens=context_tokens,
        max_context=max_context,
        context_percent=percent,
        context_messages=context_messages,
        rounds=rounds,
        steps=steps,
        tool_calls=tool_calls,
        workflow=workflow,
        agent=agent,
        preset=preset,
        model=model,
        error=error,
    )


__all__ = [
    "SessionBillingBackend",
    "TurnUsage",
    "UsageTotals",
    "summarize",
]
