"""Route uvicorn's standard-library logging into loguru.

uvicorn installs its own formatter via ``dictConfig``, which would interleave a
second style with AmritaCore's loguru output. ``LoguruHandler`` forwards records
into loguru; the console entry point calls this before uvicorn starts so even
the first line goes through the same sink.
"""

from __future__ import annotations

import logging

from amrita_sense.logging import LoguruHandler

# Loggers reconfigured explicitly, since each sets ``propagate = False``.
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
