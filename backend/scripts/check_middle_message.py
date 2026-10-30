"""Self-check for the ``processing_message`` guidance.

Core's own tool description ("not for general completion") reads to a model
like permission to wrap up, so a turn can end on a progress note and the answer
is never written. The fix is guidance in the *default system prompt*, not a
rewritten tool schema: what a tool is stays the provider's contract, and
AmritaGO never mutates Core's registry or the snapshot it hands to the agent.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amrita_core.builtins.tools import PROCESS_MESSAGE_TOOL
from amrita_core.config import AmritaConfig, set_config
from amrita_core.tools.manager import ToolsManager

from app.capabilities.skills import core_tool_data
from app.core.config import DEFAULT_SYSTEM_PROMPT

set_config(AmritaConfig())


def main() -> None:
    # 1. The snapshot passes schemas through untouched.
    tools = core_tool_data()
    snapshot = [tool for tool in tools if tool.data.function.name == "processing_message"]
    assert snapshot, "processing_message missing from the snapshot"
    assert snapshot[0].data.function.description == PROCESS_MESSAGE_TOOL.description, (
        "snapshot description was rewritten; tool schemas belong to the provider"
    )
    assert snapshot[0].data.function.parameters == PROCESS_MESSAGE_TOOL.parameters
    print("snapshot untouched ok")

    # 2. Core's global registry is unchanged too.
    raw = ToolsManager()._models["processing_message"]
    assert raw.data.function.description == PROCESS_MESSAGE_TOOL.description
    print("global registry untouched ok")

    # 3. The default prompt carries the timing guidance.
    prompt = DEFAULT_SYSTEM_PROMPT
    assert "processing_message" in prompt, "default prompt never mentions the tool"
    for phrase in ("stage", "never the answer", "cannot end the turn"):
        assert phrase in prompt, f"missing {phrase!r} in the default prompt"
    print("default prompt guidance ok")

    print("middle message ok")


if __name__ == "__main__":
    main()
