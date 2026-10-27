"""Plugin events, dispatched through AmritaSense's matcher system.

A plugin declares ``ctx.on(event, handler)``; that becomes an AmritaSense
:class:`~amrita_sense.hook.matcher.Matcher` rather than an entry in a private
list, so handlers get the same priority ordering, blocking and dependency
injection as everything else built on AmritaSense.

AmritaSense's registry is process-global and has no removal API, while a plugin
is loaded and unloaded on every settings change. Every matcher created here is
therefore tracked, and :meth:`Subscription.close` removes its handler from the
registry — an unloaded plugin must not keep firing into a closure whose
context is gone.
"""

from __future__ import annotations

import datetime
import functools
import inspect
import typing
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from amrita_sense.hook.event import BaseEvent
from amrita_sense.hook.matcher import EventRegistry, Matcher, MatcherFactory
from amrita_sense.logging import logger

# Events the runtime itself emits. A plugin may subscribe to any string.
RUN_START = "run_start"
RUN_END = "run_end"
MESSAGE_MODIFY = "message_modify"

Handler = Callable[..., Awaitable[Any] | Any]


class PluginEvent(BaseEvent[str], Mapping[str, Any]):
    """One event, readable both as an object and as a mapping.

    AmritaSense matches handler parameters by type, so a handler can ask for
    the event itself (``event: PluginEvent``), for a plain mapping
    (``event: Mapping[str, Any]``, what hooks used to receive), or for an
    individual field by name — ``trigger_event`` injects the payload as
    keyword arguments too.
    """

    def __init__(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self._event_type = event_type
        self._payload: Mapping[str, Any] = MappingProxyType(dict(payload))

    def get_event_type(self) -> str:
        return self._event_type

    @property
    def event_type(self) -> str:
        return self._event_type

    def __getitem__(self, key: str) -> Any:
        return self._payload[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._payload)

    def __len__(self) -> int:
        return len(self._payload)

    def __getattr__(self, name: str) -> Any:
        # Expose payload fields as attributes without shadowing event_type.
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            payload = object.__getattribute__(self, "_payload")
        except AttributeError:
            raise AttributeError(name) from None
        try:
            return payload[name]
        except KeyError:
            raise AttributeError(name) from None

    def __repr__(self) -> str:
        return f"PluginEvent({self._event_type!r}, {dict(self._payload)!r})"


def _as_handler(handler: Handler) -> Callable[..., Awaitable[Any]]:
    """Wrap a synchronous handler so AmritaSense can await it.

    AmritaSense awaits every handler, and a plugin that declares a plain
    ``def`` is an ordinary mistake rather than a reason to skip the hook.
    ``functools.wraps`` keeps the signature (``inspect.signature`` follows
    ``__wrapped__``), which is what dependency injection reads; annotations are
    pre-evaluated so a plugin written with ``from __future__ import
    annotations`` still resolves against *its* module, not this one.
    """
    if inspect.iscoroutinefunction(handler):
        return handler

    @functools.wraps(handler)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = handler(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    try:
        wrapper.__annotations__ = typing.get_type_hints(handler, include_extras=True)
    except Exception:
        # Unresolvable forward references stay as they are; injection reports them.
        logger.debug(f"Could not evaluate annotations of hook {handler!r}")
    return wrapper


def _remove(matcher: Matcher) -> None:
    """Drop every handler of ``matcher`` from AmritaSense's registry.

    The registry only offers ``register_handler``, so the bucket is rewritten
    under its own lock. Marking the matcher dead as well covers the narrow case
    of a dispatch that already copied the handler list.
    """
    registry = EventRegistry()
    buckets = registry.get_handlers(matcher.event_type)
    lock = getattr(EventRegistry, "_lock", None)
    with lock if lock is not None else nullcontext():
        for priority, handlers in list(buckets.items()):
            kept = [item for item in handlers if item.matcher is not matcher]
            if kept:
                buckets[priority] = kept
            else:
                buckets.pop(priority, None)
        if not buckets:
            getattr(EventRegistry, "_event_handlers", {}).pop(matcher.event_type, None)
    # Mark the subscription inactive so in-flight dispatch skips its handler.
    matcher._dead_at = datetime.datetime.now()


@dataclass(frozen=True)
class Subscription:
    """One handler on one event, removable as a unit."""

    event: str
    matcher: Matcher
    handler: Handler

    def close(self) -> None:
        _remove(self.matcher)


def subscribe(
    event: str,
    handler: Handler,
    *,
    priority: int = 10,
    block: bool = False,
) -> Subscription:
    """Register ``handler`` for ``event``.

    ``priority`` runs lower numbers first. ``block`` stops the handlers after
    this one; it defaults to off because several plugins may watch the same
    event, and a bus where the first listener silences the rest is a trap.
    """
    name = event.strip()
    if not name:
        raise ValueError("event name cannot be empty")
    matcher = Matcher(name, priority=priority, block=block)
    matcher.handle()(_as_handler(handler))
    return Subscription(event=name, matcher=matcher, handler=handler)


async def emit(event: str, /, **payload: Any) -> None:
    """Trigger ``event``; handlers run in priority order.

    The payload travels three ways on purpose: as the event object, as mapping
    items, and as keyword arguments for dependency injection by parameter name.
    """
    if not EventRegistry().get_handlers(event):
        # Skip unhandled events to avoid noisy logs.
        return
    # Access the event payload when its key collides with the dispatch parameter.
    kwargs = {key: value for key, value in payload.items() if key != "event"}
    try:
        await MatcherFactory.trigger_event(PluginEvent(event, payload), **kwargs)
    except Exception as exc:
        # A broken plugin must not fail the turn it was only observing.
        logger.opt(exception=exc).error(f"Handler for event '{event}' failed")


__all__ = [
    "MESSAGE_MODIFY",
    "RUN_END",
    "RUN_START",
    "Handler",
    "PluginEvent",
    "Subscription",
    "emit",
    "subscribe",
]
