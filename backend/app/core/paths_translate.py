"""Path translation between Windows and the POSIX shells that can run here.

``C:\\Users\\me`` is ``/c/Users/me`` for Git Bash and ``/mnt/c/Users/me`` for
WSL; a POSIX host needs no translation. The caller knows which flavour it
started, so the style is passed in rather than guessed here.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_DRIVE = re.compile(r"^([A-Za-z]):[\\\\/]?(.*)$")

# Which Windows->POSIX mapping a bash implementation expects.
PosixStyle = str  # "wsl" | "msys"


def _translate(path: str, style: str) -> str:
    match = _DRIVE.match(path)
    if not match:
        return path.replace("\\", "/")
    drive, rest = match.groups()
    rest = rest.replace("\\", "/")
    if style == "msys":
        return f"/{drive.lower()}/{rest}"
    return f"/mnt/{drive.lower()}/{rest}"


def bash_path(path: Path | str, style: str = "wsl") -> str:
    """A path string the chosen bash can actually open."""
    raw = str(path)
    if os.name != "nt":
        return raw
    # ``\\wsl$`` and already-posix paths pass through untouched.
    if raw.startswith("/"):
        return raw
    return _translate(os.path.abspath(raw), style)


def native_path(path: str) -> str:
    """Inverse of :func:`bash_path`, for paths a shell reported back."""
    if os.name != "nt":
        return path
    # A session may switch style while its saved directory is still in the old form.
    match = re.match(r"^/mnt/([A-Za-z])/(.*)$", path) or re.match(r"^/([A-Za-z])/(.*)$", path)
    if not match:
        return path
    drive, rest = match.groups()
    return f"{drive.upper()}:\\" + rest.replace("/", "\\")


def shell_path(kind: str, path: Path | str, style: str = "wsl") -> str:
    return bash_path(path, style) if kind == "bash" else str(path)


def bash_style_for(flavour: str, executable_path: str = "") -> str:
    """Which mapping a session should use, from its flavour and binary.

    ``wsl`` is authoritative when the flavour says so; otherwise the executable
    path decides, because a user who pastes ``C:\\Program Files\\Git\\bin\\bash.exe``
    into 「可执行文件」 has told us which bash they mean even if the flavour field
    still says ``auto``.
    """
    if flavour == "wsl":
        return "wsl"
    lowered = executable_path.lower()
    if "git" in lowered or "msys" in lowered:
        return "msys"
    return "wsl"


__all__ = ["bash_path", "bash_style_for", "native_path", "shell_path"]
