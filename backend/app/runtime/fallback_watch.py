"""Capture the provider error that a preset fallback swallowed.

AmritaCore's ``call_completion`` retries against other presets and raises
``FallbackFailed`` while handling the real provider error, so the original
status code and body survive only in the exception chain. One
``PRESET_FALLBACK`` matcher records ``exc_info`` into a turn-scoped context
variable; it never touches ``ctx.preset``, so fallback behaviour is unchanged.
"""

from __future__ import annotations

from contextvars import ContextVar

from amrita_core.hook.event import FallbackContext
from amrita_core.hook.on import on_preset_fallback
from amrita_sense.logging import logger

#: Errors captured for the turn that is currently running in this task.
_BUCKET: ContextVar[list[str] | None] = ContextVar("amrita_go_fallback_errors", default=None)


def start_turn() -> None:
    """Begin a fresh capture bucket for the run about to start."""
    _BUCKET.set([])


def recorded() -> list[str]:
    return list(_BUCKET.get() or [])


def describe() -> str:
    """Captured errors as one readable block, newest last, de-duplicated."""
    seen: list[str] = []
    for item in recorded():
        if item not in seen:
            seen.append(item)
    return "\n".join(seen)


def stop_turn() -> None:
    _BUCKET.set(None)


_matcher = on_preset_fallback()


@_matcher.handle()
async def _capture(event: FallbackContext) -> None:
    """Remember why the gateway call failed; leave the fallback itself alone."""
    bucket = _BUCKET.get()
    if bucket is None:
        # No turn is tracking (CLI, tests, background job): log and move on.
        logger.debug(f"Preset fallback (untracked): {event.exc_info!r}")
        return
    bucket.append(f"{type(event.exc_info).__name__}: {event.exc_info}")


__all__ = ["describe", "recorded", "start_turn", "stop_turn"]
