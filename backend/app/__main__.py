"""Console entry point: ``python -m app`` or the ``amrita-go`` script.

Argument parsing lives in :mod:`app.cli` and happens before any application
import, so ``--port`` / ``--secret`` can still override the environment.
"""

from __future__ import annotations

from app.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
