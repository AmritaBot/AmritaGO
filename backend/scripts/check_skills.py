"""Verify faskill skill discovery + tool-pool injection.

Creates a temporary skill in the workspace skills directory, then asserts:
  * discovery lists it,
  * the tool pool contains a tool named after the skill,
  * faskill's ``$ARGUMENTS`` substitution reaches the tool result,
  * disabling removes the tool.

    .venv/Scripts/python.exe scripts/check_skills.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.capabilities.skills import (
    build_skill_tools,
    build_skill_usage_prompt,
    discover_skills,
)
from app.core.config import PATHS
from app.core.settings import AppSettings

SKILL = """---
name: demo-skill
description: A demo skill used by the test suite.
version: 0.1.0
---

# Demo

Handle this request: $ARGUMENTS
"""


async def main() -> int:
    from amrita_core import minimal_init

    await minimal_init()

    directory = PATHS.workspace_skills / "demo-skill"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(SKILL, encoding="utf-8")

    try:
        skills = await discover_skills()
        names = [info.name for info in skills]
        print("discovered:", names)
        assert "demo-skill" in names, names

        tools = build_skill_tools(enabled=None, skills=skills)
        metas = tools.tools_meta()
        print("tool count:", len(metas))
        assert "demo-skill" in metas, list(metas)[:10]

        # faskill owns $ARGUMENTS substitution; the handler must expose it.
        tool = tools._models["demo-skill"]
        result = await tool.func(  # type: ignore[call-arg]
            SimpleNamespace(data={"arguments": "REQUEST"}, ctx=SimpleNamespace(io_stream=None))
        )
        assert "REQUEST" in result, result

        prompt = build_skill_usage_prompt(None, skills)
        assert "demo-skill" in prompt
        print("usage prompt: ok")

        # Disabling removes the tool.
        disabled = build_skill_tools(enabled=[], skills=skills)
        assert "demo-skill" not in disabled.tools_meta()
        print("disable filtering: ok")

        settings = AppSettings(enabled_skills=None)
        assert settings.enabled_skills is None
        print("settings default: ok")
    finally:
        # Clean up so the repo stays tidy.
        shutil.rmtree(directory, ignore_errors=True)

    print("SKILLS OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
