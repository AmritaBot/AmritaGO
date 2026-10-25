"""Access modes: a coarse, user-visible statement of what an agent may do.

``readonly`` / ``workspace`` / ``full``. A preset declares an upper bound and
the client requests one per message; the effective mode is the intersection,
so a preset can narrow a request but never widen it.
"""

from __future__ import annotations

import re
from typing import Literal

AccessMode = Literal["readonly", "workspace", "full"]

#: Ordered from least to most permissive. Order matters: intersecting uses it.
ACCESS_MODES: tuple[str, ...] = ("readonly", "workspace", "full")

LABELS: dict[str, str] = {
    "readonly": "仅可查看",
    "workspace": "工作区内修改",
    "full": "完全权限",
}

#: Tools that cannot change anything; everything else is a writer.
_READ_ONLY_TOOL_PREFIXES = (
    "read_",
    "list_",
    "get_",
    "search_",
    "show_",
    "describe_",
    "grep_",
    "find_",
    "skill_",
)

#: Dual-use tools: harmless call, the argument decides.
_READ_ONLY_TOOL_SUFFIXES = ("_status", "_info", "_read", "_list", "_search")

#: Families a read-only run does not get: a name check cannot keep a shell honest.
SHELL_TOOL_PREFIXES = ("pwsh_", "unix_", "bash_", "shell_", "powershell_")

#: Vouched-for tools: progress messages, session-scoped memory and todos.
_READ_ONLY_TOOL_NAMES = frozenset(
    {
        "processing_message",
        "memory_write",
        "memory_read",
        "memory_list",
        "memory_delete",
        "todo_write",
        "todo_update",
    }
)

#: Commands a read-only shell still refuses, matched as word sequences.
_WRITE_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("rm",),
    ("rmdir",),
    ("mv",),
    ("cp",),
    ("mkdir",),
    ("touch",),
    ("chmod",),
    ("chown",),
    ("dd",),
    ("tee",),
    ("truncate",),
    ("ln",),
    ("rsync",),
    ("git", "add"),
    ("git", "commit"),
    ("git", "push"),
    ("git", "checkout"),
    ("git", "reset"),
    ("git", "clean"),
    ("git", "apply"),
    ("git", "merge"),
    ("git", "rebase"),
    ("git", "stash"),
    ("git", "restore"),
    ("git", "rm"),
    ("npm", "install"),
    ("pnpm", "install"),
    ("yarn", "add"),
    ("bun", "add"),
    ("pip", "install"),
    ("uv", "add"),
    ("cargo", "add"),
    ("go", "get"),
    ("apt",),
    ("apt-get",),
    ("brew",),
    ("winget",),
    ("choco",),
    ("set-content",),
    ("add-content",),
    ("out-file",),
    ("new-item",),
    ("remove-item",),
    ("move-item",),
    ("copy-item",),
    ("set-itemproperty",),
    ("rename-item",),
    ("clear-content",),
    ("invoke-expression",),
)

#: Pre-compiled because this runs on every shell call.
_WRITE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(r"\b" + r"\s+".join(words) + r"\b", re.IGNORECASE), " ".join(words))
    for words in _WRITE_COMMANDS
)


def normalize(mode: str | None) -> str:
    return mode if mode in ACCESS_MODES else "workspace"


def narrow(declared: list[str] | None, requested: str | None) -> str:
    """The effective mode: what the preset allows, capped by what was asked."""
    want = normalize(requested)
    if declared is None:
        return want
    allowed = [mode for mode in ACCESS_MODES if mode in declared]
    if not allowed:
        return "readonly"
    # ``readonly`` is a subset of every other mode, so narrowing always succeeds.
    if want == "readonly":
        return want
    cap = allowed[-1]
    return want if ACCESS_MODES.index(want) <= ACCESS_MODES.index(cap) else cap


def is_shell_tool(tool_name: str) -> bool:
    """Whether a tool runs an arbitrary command line."""
    return tool_name.lower().startswith(SHELL_TOOL_PREFIXES)


def tool_allowed_for_mode(tool_name: str, mode: str) -> bool:
    """Whether a read-only run may call this tool at all.

    Deny-by-default. Two rules apply:

    * the shell family is refused outright — see :data:`SHELL_TOOL_PREFIXES`;
    * everything else is kept only if its name identifies it as a reader.

    The tool is then *absent* from the pool, so the model never sees a tool it
    is not allowed to use, which is the whole point: a refusal the model has to
    discover by calling costs a round trip and teaches it to retry.
    """
    if mode != "readonly":
        return True
    lowered = tool_name.lower()
    if is_shell_tool(lowered):
        return False
    if lowered in _READ_ONLY_TOOL_NAMES:
        return True
    if any(lowered.startswith(prefix) for prefix in _READ_ONLY_TOOL_PREFIXES):
        return True
    return any(lowered.endswith(suffix) for suffix in _READ_ONLY_TOOL_SUFFIXES)


def command_allowed_for_mode(command: str, mode: str) -> str | None:
    """Return a rejection reason when ``command`` writes, else ``None``.

    Kept even though a read-only run no longer gets a shell: plugins can declare
    their own command-running tools, and defence in depth costs one call.

    This is a guard rail, not a sandbox: a determined caller can obfuscate a
    write. The point is that an accidental ``rm -rf`` in a read-only preset is
    refused loudly rather than executed.
    """
    if mode != "readonly":
        return None
    for pattern, label in _WRITE_PATTERNS:
        if pattern.search(command):
            return (
                f"read-only mode refuses a command containing '{label}'; "
                "pick a write-enabled agent preset to run it"
            )
    if ">" in command:
        return "read-only mode refuses output redirection"
    return None


__all__ = [
    "ACCESS_MODES",
    "LABELS",
    "SHELL_TOOL_PREFIXES",
    "AccessMode",
    "command_allowed_for_mode",
    "is_shell_tool",
    "narrow",
    "normalize",
    "tool_allowed_for_mode",
]
