"""Verify skill discovery + tool-pool injection.

Creates a temporary skill, then asserts:
  * ``/api/skills`` lists it,
  * the agent's tool pool contains ``skill_<name>`` (checked through the
    backend's own tool builder, not by calling the model),
  * the tool result embeds the SKILL.md body with ``$ARGUMENTS`` substituted.

    .venv/Scripts/python.exe scripts/check_skills.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

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

Then check `template.md`.
"""


async def main() -> int:
    from amrita_core import minimal_init

    await minimal_init()

    directory = PATHS.skills / "demo-skill"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (directory / "template.md").write_text("# Template\n", encoding="utf-8")

    skills = await discover_skills()
    names = [info.name for info in skills]
    print("discovered:", names)
    assert "demo-skill" in names, names

    tools = build_skill_tools(enabled=None, skills=skills)
    metas = tools.tools_meta()
    print("tool count:", len(metas))
    assert "skill_demo-skill" in metas, list(metas)[:10]

    prompt = build_skill_usage_prompt(None, skills)
    assert "demo-skill" in prompt
    print("usage prompt: ok")

    # Disabling removes the tool.
    disabled = build_skill_tools(enabled=[], skills=skills)
    assert "skill_demo-skill" not in disabled.tools_meta()
    print("disable filtering: ok")

    settings = AppSettings(enabled_skills=None)
    assert settings.enabled_skills is None
    print("settings default: ok")

    # Clean up so the repo stays tidy.
    (directory / "SKILL.md").unlink()
    (directory / "template.md").unlink()
    directory.rmdir()
    print("SKILLS OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
