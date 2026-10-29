"""Self-check for the context message ceiling wiring.

Run: ``uv run --no-sync python scripts/check_context_limit.py``

The app-level ``context_message_limit`` is a *user* setting, so it has to reach
AmritaCore's ``llm.memory_length_limit`` on every run; a non-positive value means
"no ceiling" and Core spells that as ``0``. The API schema is checked too,
because a bound the form cannot save is a bound the user cannot remove.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("AMRITA_GO_DATA", tempfile.mkdtemp(prefix="amrita-ctx-"))

from amrita_core import set_config
from amrita_core.config import AmritaConfig
from pydantic import ValidationError

from app.core.settings import AppSettings, save_settings
from app.main import SettingsUpdate
from app.runtime.tool_limit import for_run


def check_override() -> None:
    """Every saved value lands on the run config, with -1 flattened to 0."""
    # ``for_run`` copies the process-wide config, which the app sets at startup.
    set_config(AmritaConfig())
    for value, expected in ((200, 200), (37, 37), (0, 0), (-1, 0)):
        asyncio.run(save_settings(AppSettings(context_message_limit=value)))
        config = for_run()
        assert config.llm.memory_length_limit == expected, (
            f"{value} -> {config.llm.memory_length_limit}, expected {expected}"
        )


def check_schema() -> None:
    """``-1`` (no ceiling) is accepted; anything below it is refused."""
    assert SettingsUpdate(context_message_limit=-1).context_message_limit == -1
    assert SettingsUpdate(context_message_limit=0).context_message_limit == 0
    assert SettingsUpdate(context_message_limit=100000).context_message_limit == 100000
    try:
        SettingsUpdate(context_message_limit=-2)
    except ValidationError:
        return
    raise AssertionError("-2 must be rejected")


def main() -> None:
    check_override()
    check_schema()
    print("context_limit ok")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"context_limit FAILED: {exc}")
        sys.exit(1)
