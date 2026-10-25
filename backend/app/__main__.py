"""Console entry point: ``python -m app`` or the ``amrita-go`` script."""

from __future__ import annotations

from app.core.config import HOST, PORT
from app.core.logging_setup import setup_logging


def main() -> None:
    import uvicorn

    # Install before ``uvicorn.run`` so startup lines are already formatted.
    setup_logging()

    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
        # Protocol pings time out while the dev proxy bundles; the app-level keepalive replaces them.
        ws_ping_interval=None,
        ws_ping_timeout=None,
    )


if __name__ == "__main__":
    main()
