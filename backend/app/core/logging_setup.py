"""Route uvicorn's standard-library logging into loguru.

Two reasons this exists:

* **One format.** uvicorn configures ``logging`` with ``dictConfig`` and its own
  ``logging.Formatter``, so a terminal that also carries AmritaCore's loguru
  output shows two different styles interleaved — ``INFO:     Application
  startup complete.`` next to ``2026-10-24 23:44 | INFO | ...``.
* **One sink.** Closing the app should not require hunting for a second handler
  that still writes to stderr, and a filter (`LOG_LEVEL`) that works for
  AmritaCore should work for the server too.

How it is wired: :class:`amrita_sense.logging.LoguruHandler` forwards a
``logging.LogRecord`` into loguru, so installing it on the ``uvicorn.*`` loggers
is enough. ``propagate`` is turned off on each of them — otherwise the record
would reach the root logger as well and print twice.

Order matters, and is why this is a function rather than a module-level
statement in two places: uvicorn runs ``dictConfig`` when its ``Config`` object
is built, which is *before* the app module is imported, so a module-level call
in ``app.main`` always lands after uvicorn's handlers and replaces them. The
console entry point calls it early so even the first line ("Started server
process") goes through loguru.
"""

from __future__ import annotations

import logging

from amrita_sense.logging import LoguruHandler

#: Loggers reconfigured explicitly, since each sets ``propagate = False``.
MANAGED_LOGGERS: tuple[str, ...] = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "uvicorn.asgi",
    "fastapi",
)


def setup_logging(level: str | int = "INFO") -> None:
    """Send ``uvicorn.*`` records to loguru. Safe to call at any time.

    Order-independent on purpose. uvicorn runs ``dictConfig`` while building its
    ``Config`` object, and *that* happens before the app module is imported — so
    the later call (this one, from ``app.main``) is the one that survives.
    Running it twice, or from a second entry point, is harmless: the handler list
    is replaced, so each record is still emitted exactly once.
    """
    for name in MANAGED_LOGGERS:
        target = logging.getLogger(name)
        # Replace the handlers: uvicorn's own formatter would print a second copy.
        target.handlers = [LoguruHandler()]
        target.propagate = False
        target.setLevel(level)


__all__ = ["MANAGED_LOGGERS", "setup_logging"]
