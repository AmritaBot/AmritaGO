"""Local shell execution for the agent.

Completion is reported by the shell itself, using the OSC 133/633 shell
integration alphabet (the one VSCode and iTerm2 read):

* ``133;A`` prompt start, ``133;B`` command start, ``133;C`` output start —
  emitted so a future PTY-backed session can slice prompt / command / output
  without guessing, and so the markers above are already in place.
* ``633;D;<exit>;<nonce>`` — command finished, with a per-session nonce. A
  command that happens to print something marker-shaped cannot forge completion,
  because the nonce never leaves the runner.
* ``7;file://host/path`` — the working directory after the command, reported by
  the shell itself rather than assumed by us.

``mcp-ssh-reloaded`` is the reference for this shape: markers over a PTY plus a
session that remembers its directory, its shell and its recent commands. There
is no such thing as "OSC 266" — the terminal-integration sequences in use are
OSC 7 (cwd), OSC 133 (semantic prompt) and OSC 633 (VSCode's extension of it).

The working directory persists between calls: each run starts in the directory
the previous one finished in, recorded in a small state file *and* refreshed
from OSC 7. A fresh process per call is deliberate — a shell left running
between calls can block forever on a prompt nothing answers, and a timeout
would then kill the wrong thing.

Output is capped and spilled to ``~/.amrita/shell``, so a runaway command costs
disk instead of memory.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.core.config import PATHS
from app.core.paths_translate import bash_style_for, native_path, shell_path

#: A session family, not an executable: ``unix`` covers bash and zsh.
ShellKind = Literal["pwsh", "unix"]
#: The program inside a family; ``wsl`` is bash in the Linux namespace, with its own paths.
ShellFlavour = Literal["bash", "zsh", "pwsh", "wsl"]

#: Preference order when a unix session pins none; ``wsl`` is absent on purpose.
UNIX_FLAVOURS: tuple[ShellFlavour, ...] = ("bash", "zsh")

#: Every flavour a user may pick explicitly.
ALL_FLAVOURS: tuple[ShellFlavour, ...] = ("bash", "zsh", "wsl", "pwsh")


#: Default output cap handed back to the model.
DEFAULT_MAX_OUTPUT_BYTES = 192 * 1024
#: Default wall-clock limit for one command.
DEFAULT_TIMEOUT = 60.0
#: Hard ceiling a caller cannot exceed.
MAX_TIMEOUT = 600.0

_MARKER_PREFIX = "]633;D;"
_OSC_PREFIX = "]"


class ShellUnavailable(RuntimeError):
    """Raised when the requested shell executable is not installed."""


def _wsl_executable() -> str | None:
    """``wsl.exe``, or the distro-less ``bash.exe`` shim as a fallback.

    ``wsl.exe`` is preferred: it accepts ``--exec`` and works from any working
    directory, while the legacy ``System32\\bash.exe`` requires a configured
    default distro and shows a warning on first use.
    """
    if os.name != "nt":
        return None
    return shutil.which("wsl") or shutil.which("bash")


def executable(kind: ShellKind, flavour: str | None = None) -> str | None:
    """Resolve the program for a family, honouring an explicit flavour."""
    if kind == "pwsh":
        return shutil.which("pwsh") or shutil.which("powershell")
    if flavour == "wsl":
        return _wsl_executable()
    if flavour in UNIX_FLAVOURS:
        return shutil.which(flavour)
    for candidate in UNIX_FLAVOURS:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def wsl_available() -> bool:
    """Whether a WSL bash can be started at all."""
    return _wsl_executable() is not None


def available_flavours() -> list[str]:
    """Every flavour this machine can actually run, for the settings UI."""
    out: list[str] = []
    if os.name == "nt" and (shutil.which("pwsh") or shutil.which("powershell")):
        out.append("pwsh")
    out.extend(candidate for candidate in UNIX_FLAVOURS if shutil.which(candidate))
    if wsl_available():
        out.append("wsl")
    return out


def detect_flavour(kind: ShellKind) -> ShellFlavour:
    """Which concrete program a family session will spawn."""
    if kind == "pwsh":
        return "pwsh"
    for candidate in UNIX_FLAVOURS:
        if shutil.which(candidate):
            return candidate
    return "bash"


def available_shells() -> list[ShellKind]:
    # ``unix`` is available when any POSIX bash can run, WSL included.
    return [
        kind
        for kind in ("pwsh", "unix")
        if executable(kind) or (kind == "unix" and wsl_available())
    ]


def detect_kind() -> ShellKind:
    """Prefer unix on POSIX and pwsh on Windows."""
    if os.name == "nt":
        return "pwsh" if executable("pwsh") else "unix"
    return "unix" if executable("unix") else "pwsh"


@dataclass
class ShellResult:
    shell: ShellKind
    command: str
    output: str
    exit_code: int | None
    cwd: str
    elapsed_ms: int
    truncated: bool = False
    spilled: str | None = None
    timed_out: bool = False
    error: str | None = None

    def as_text(self) -> str:
        header = [
            f"shell: {self.shell}",
            f"cwd: {self.cwd}",
            f"exit_code: {self.exit_code if self.exit_code is not None else 'unknown'}",
        ]
        if self.timed_out:
            header.append("timed_out: true")
        if self.error:
            header.append(f"error: {self.error}")
        if self.spilled:
            header.append(f"full_output: {self.spilled}")
        if self.truncated:
            header.append("truncated: true (head and tail kept)")
        body = self.output.rstrip("\n")
        return "\n".join(header) + ("\n\n" + body if body else "")


def state_dir() -> Path:
    path = PATHS.data / "shell"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


#: Names that mean UTF-8, normalised so "UTF8" behaves like "utf-8".
_UTF8_ALIASES = frozenset({"utf-8", "utf8", "utf_8", "utf-8-sig", "utf_8_sig"})


def is_utf8(encoding: str) -> bool:
    return encoding.strip().lower().replace(" ", "-") in _UTF8_ALIASES


def _bash_script(
    command: str,
    start_dir: str,
    state: Path,
    nonce: str,
    init: str = "",
    style: str = "wsl",
) -> str:
    # ``start_dir`` is already in the shell's own form.
    setup = f"{init}\n" if init.strip() else ""
    return (
        "set -o pipefail\n"
        f"cd -- {_sh_quote(start_dir)} 2>/dev/null || true\n"
        f"{setup}"
        "printf '\\033]133;A\\007'\n"
        "printf '\\033]133;B\\007'\n"
        f"{command}\n"
        "__amrita_rc=$?\n"
        "printf '\\033]133;C\\007'\n"
        f"pwd > {_sh_quote(shell_path('bash', state, style))}\n"
        # Report where the shell actually ended up; the state file is the fallback.
        "printf '\\033]7;file://localhost%s\\007' \"$(pwd)\"\n"
        "printf '\\033]633;D;%s;" + nonce + '\\007\' "$__amrita_rc"\n'
        'exit "$__amrita_rc"\n'
    )


def _pwsh_script(
    command: str,
    start_dir: str,
    state: Path,
    nonce: str,
    init: str = "",
    encoding: str = "utf-8",
) -> str:
    setup = f"{init}\n" if init.strip() else ""
    # Force the child console to UTF-8, or CP936 mangles every redirect.
    console = (
        "$__amrita_enc = [System.Text.UTF8Encoding]::new($false)\n"
        "[Console]::OutputEncoding = $__amrita_enc\n"
        "$OutputEncoding = $__amrita_enc\n"
        if is_utf8(encoding)
        else ""
    )
    return (
        "$ErrorActionPreference = 'Continue'\n"
        f"{console}"
        f"Set-Location -LiteralPath {_ps_quote(start_dir)}\n"
        f"{setup}"
        "$__amrita_rc = 0\n"
        "$__amrita_esc = [char]27\n"
        "$__amrita_bel = [char]7\n"
        '[Console]::Out.Write("$__amrita_esc]133;A$__amrita_bel")\n'
        '[Console]::Out.Write("$__amrita_esc]133;B$__amrita_bel")\n'
        "try {\n"
        f"{command}\n"
        "if ($LASTEXITCODE -ne $null) { $__amrita_rc = $LASTEXITCODE }\n"
        "} catch {\n"
        "  Write-Error $_\n"
        "  $__amrita_rc = 1\n"
        "}\n"
        '[Console]::Out.Write("$__amrita_esc]133;C$__amrita_bel")\n'
        # OSC 7 wants a URI, so turn `C:\\a\\b` into `file:///C:/a/b`.
        "$__amrita_pwd = (Get-Location).Path.Replace([char]92, [char]47)\n"
        '[Console]::Out.Write("$__amrita_esc]7;file:///$__amrita_pwd/$__amrita_bel")\n'
        f'[Console]::Out.Write("$__amrita_esc{_MARKER_PREFIX}$__amrita_rc;{nonce}$__amrita_bel")\n'
        "[Console]::Out.Flush()\n"
        f"[IO.File]::WriteAllText({_ps_quote(str(state))}, (Get-Location).Path)\n"
        "exit $__amrita_rc\n"
    )


def _uri_to_cwd(value: str, kind: ShellKind) -> str | None:
    """Turn the payload of an OSC 7 sequence into a directory path."""
    raw = value
    if raw.startswith("file://"):
        raw = raw[len("file://") :]
        # Drop the authority (`localhost`, a hostname, or nothing).
        raw = raw.split("/", 1)[1] if "/" in raw else ""
        raw = "/" + raw
    if not raw:
        return None
    if kind == "pwsh" and re.match(r"^/[A-Za-z]:", raw):
        # ``file:///C:/x`` becomes a native path: the state file is written that way.
        raw = raw[1:].replace("/", "\\")
    return raw


def strip_marker(
    text: str, nonce: str, kind: ShellKind = "unix"
) -> tuple[str, int | None, str | None]:
    """Remove our OSC markers, returning the text, exit code and final cwd.

    Only the sequences we emit are consumed. An unterminated one is dropped
    rather than passed through: it would otherwise leave a stray ``ESC]`` in
    the model's output.
    """
    esc = "\x1b"
    pieces: list[str] = []
    exit_code: int | None = None
    cwd: str | None = None
    index = 0
    while True:
        start = text.find(f"{esc}{_OSC_PREFIX}", index)
        if start < 0:
            pieces.append(text[index:])
            break
        pieces.append(text[index:start])
        ends = [pos for pos in (text.find("\x07", start), text.find(f"{esc}\\", start)) if pos >= 0]
        if not ends:
            break
        end = min(ends)
        payload = text[start + len(f"{esc}{_OSC_PREFIX}") : end]
        if payload.startswith("7;"):
            candidate = _uri_to_cwd(payload[2:], kind)
            if candidate:
                cwd = candidate
        elif payload.startswith("633;D;"):
            code, _, marker_nonce = payload[len("633;D;") :].partition(";")
            if marker_nonce == nonce:
                try:
                    exit_code = int(code)
                except ValueError:
                    exit_code = None
        elif payload.startswith("133;"):
            pass
        else:
            # Not ours: keep it so a program's title/hyperlink still works.
            pieces.append(text[start:end])
            pieces.append(text[end])
        index = end + 1
    return "".join(pieces), exit_code, cwd


@dataclass
class ShellSession:
    kind: ShellKind
    cwd: Path
    #: ``bash`` / ``zsh`` / ``pwsh``. Defaults to whatever is installed.
    flavour: str = ""
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    #: Configured interpreter path; empty means "whatever is on PATH".
    executable_path: str = ""
    #: Snippet run before every command, e.g. ``source ~/.bashrc``.
    init_commands: str = ""
    #: Codec for the process output; UTF-8 unless the shell emits the OEM page.
    output_encoding: str = "utf-8"
    nonce: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    #: Stable handle the UI uses to reset or inspect one session.
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: float = field(default_factory=time.time)
    #: Recent runs, newest last; bounded because it is a transcript, not a log.
    runs: list[dict[str, Any]] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @property
    def state_file(self) -> Path:
        digest = hashlib.sha1(
            f"{self.kind}:{self.flavour}:{self.cwd}:{self.executable_path}".encode()
        ).hexdigest()[:12]
        return state_dir() / f"{self.kind}-{digest}.cwd"

    def resolved_executable(self) -> str | None:
        """The configured path if it exists, else the shell on ``PATH``.

        A configured path that does not exist is a configuration error the user
        needs to see, not a silent fallback: on Windows ``/bin/bash`` only
        exists *inside* WSL, so quietly running ``C:\\Windows\\system32\\bash.exe``
        instead would look like the setting was ignored.
        """
        configured = self.executable_path.strip().strip('"')
        if not configured:
            return executable(self.kind, self.flavour or None)
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate)
        # A bare name (`bash`, `pwsh`) is a PATH lookup, not a relative file.
        return shutil.which(configured)

    def _record(self, result: ShellResult) -> None:
        self.runs.append(
            {
                "command": result.command,
                "exit_code": result.exit_code,
                "elapsed_ms": result.elapsed_ms,
                "cwd": result.cwd,
                "timed_out": result.timed_out,
                "at": time.time(),
                "output": result.output[-2000:],
            }
        )
        del self.runs[:-30]

    def report(self) -> dict[str, Any]:
        last = self.runs[-1] if self.runs else None
        return {
            "id": self.id,
            "shell": self.kind,
            "flavour": self.flavour or detect_flavour(self.kind),
            "path_style": self.path_style,
            "cwd": self.display_cwd(),
            "root": str(self.cwd),
            "executable": self.resolved_executable() or "",
            "executable_configured": bool(self.executable_path.strip()),
            "encoding": self.output_encoding,
            "init_commands": self.init_commands,
            "started_at": self.started_at,
            "runs": len(self.runs),
            "last": last,
            "history": self.runs[-10:],
        }

    @property
    def path_style(self) -> str:
        """``wsl`` or ``msys``; meaningless (and unused) for pwsh.

        Derived from the flavour *and* the executable path: a user who pins
        ``...\\Git\\bin\\bash.exe`` has chosen MSYS paths even if the flavour
        still says ``auto``.
        """
        if self.kind != "unix":
            return "native"
        return bash_style_for(self.flavour, self.executable_path)

    def _saved_dir(self) -> str | None:
        """The directory the previous run ended in, in that shell's own form.

        Kept verbatim rather than normalised: a WSL ``bash`` may ``cd /tmp``,
        which has no Windows equivalent, and converting it would silently send
        the next command back to the workspace root.
        """
        try:
            saved = self.state_file.read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            return None
        return saved or None

    def launch_dir(self) -> str:
        """The path handed to the shell's ``cd``/``Set-Location``."""
        saved = self._saved_dir()
        if saved:
            if self.kind == "unix" and saved.startswith("/"):
                return saved
            if self.kind == "pwsh" and not saved.startswith("/"):
                return saved
        return shell_path(self.flavour or "bash", self.cwd, self.path_style)

    def display_cwd(self) -> str:
        """A path a Python caller can reason about."""
        saved = self._saved_dir()
        if saved is None:
            return str(self.cwd)
        # unix shells report POSIX paths; pwsh reports native ones.
        return saved if self.kind == "unix" else str(Path(native_path(saved)))

    async def _spill(self, data: bytes) -> str:
        path = state_dir() / f"{self.kind}-{uuid.uuid4().hex[:8]}.log"
        await asyncio.to_thread(path.write_bytes, data)
        return str(path)

    async def run(
        self,
        command: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        extra_env: dict[str, str] | None = None,
    ) -> ShellResult:
        exe = self.resolved_executable()
        if exe is None:
            raise ShellUnavailable(
                f"{self.executable_path.strip() or self.kind} was not found; "
                "check 「可执行文件」 in the shell plugin settings "
                "(on Windows, /bin/bash only exists inside WSL)"
            )

        started = time.monotonic()
        async with self._lock:
            start_dir = self.launch_dir()
            if self.kind == "pwsh":
                Path(start_dir).mkdir(parents=True, exist_ok=True)
            self.cwd.mkdir(parents=True, exist_ok=True)
            script_dir = Path(tempfile.mkdtemp(prefix="amrita-sh-"))
            state = self.state_file

            if self.kind == "unix":
                script_path = script_dir / "run.sh"
                # Bytes, not text: Windows text mode would append CRLF.
                script_path.write_bytes(
                    _bash_script(
                        command,
                        start_dir,
                        state,
                        self.nonce,
                        self.init_commands,
                        self.path_style,
                    ).encode("utf-8")
                )
                script_arg = shell_path("bash", script_path, self.path_style)
                if self.flavour == "wsl":
                    # ``wsl.exe`` needs ``--exec`` and the script path in WSL form.
                    argv = [exe, "--exec", "bash", "--noprofile", "--norc", script_arg]
                else:
                    argv = [exe, "--noprofile", "--norc", script_arg]
            else:
                script_path = script_dir / "run.ps1"
                script_path.write_text(
                    _pwsh_script(
                        command,
                        start_dir,
                        state,
                        self.nonce,
                        self.init_commands,
                        self.output_encoding,
                    ),
                    encoding="utf-8-sig",
                )
                argv = [
                    exe,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(script_path),
                ]

            env = os.environ.copy()
            env.update(extra_env or {})
            limit = min(max(1.0, timeout), MAX_TIMEOUT)

            # The process cwd stays native; only the script text uses the shell's form.
            native_cwd = str(self.cwd)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=native_cwd,
                    env=env,
                )
            except OSError as exc:
                shutil.rmtree(script_dir, ignore_errors=True)
                return ShellResult(
                    shell=self.kind,
                    command=command,
                    output="",
                    exit_code=None,
                    cwd=str(self.cwd),
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    error=f"cannot start {self.kind}: {exc}",
                )

            timed_out = False
            try:
                raw, _ = await asyncio.wait_for(proc.communicate(), limit)
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                raw, _ = await proc.communicate()
            finally:
                shutil.rmtree(script_dir, ignore_errors=True)

            elapsed_ms = int((time.monotonic() - started) * 1000)
            text, marker_code, reported_cwd = strip_marker(
                raw.decode(self.output_encoding, "replace"), self.nonce, self.kind
            )
            if reported_cwd:
                # Trust what the shell reported over a state file a crash may have missed.
                try:
                    self.state_file.write_text(reported_cwd, encoding="utf-8")
                except OSError:
                    pass
            # Killed process: no marker means no exit code, so do not invent one.
            exit_code = (
                None if timed_out else (marker_code if marker_code is not None else proc.returncode)
            )

            data = text.encode("utf-8", "replace")
            truncated = len(data) > self.max_output_bytes
            spilled: str | None = None
            if truncated:
                spilled = await self._spill(data)
                head = data[: self.max_output_bytes // 4]
                tail = data[-(self.max_output_bytes * 3 // 4) :]
                data = head + b"\n... [truncated] ...\n" + tail

            result = ShellResult(
                shell=self.kind,
                command=command,
                output=data.decode("utf-8", "replace"),
                exit_code=exit_code,
                cwd=self.display_cwd(),
                elapsed_ms=elapsed_ms,
                truncated=truncated,
                spilled=spilled,
                timed_out=timed_out,
                error=f"timed out after {limit:.0f}s" if timed_out else None,
            )
            self._record(result)
            return result


class ShellManager:
    """One logical session per ``(kind, cwd, executable)``.

    "Logical" because the working directory survives while the process does
    not: every run is a fresh interpreter that starts where the previous one
    finished. Pointing a plugin at a different executable is therefore a
    different session, not a reconfiguration of the old one — otherwise the
    saved directory of, say, WSL bash would be handed to Git Bash.
    """

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str, str, str], ShellSession] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        kind: ShellKind,
        cwd: Path,
        executable_path: str = "",
        init_commands: str = "",
        flavour: str = "",
        encoding: str = "utf-8",
    ) -> ShellSession:
        resolved_flavour = flavour or detect_flavour(kind)
        key = (kind, resolved_flavour, str(cwd), executable_path.strip())
        async with self._lock:
            session = self._sessions.get(key)
            if session is None:
                session = ShellSession(
                    kind=kind,
                    cwd=cwd,
                    flavour=resolved_flavour,
                    executable_path=executable_path,
                    init_commands=init_commands,
                    output_encoding=encoding,
                )
                self._sessions[key] = session
            else:
                # Config edits apply live, so the profile is re-read per call.
                session.init_commands = init_commands
                session.output_encoding = encoding
            return session

    async def reset(
        self, kind: ShellKind, cwd: Path, executable_path: str = "", flavour: str = ""
    ) -> None:
        resolved_flavour = flavour or detect_flavour(kind)
        async with self._lock:
            session = self._sessions.pop(
                (kind, resolved_flavour, str(cwd), executable_path.strip()), None
            )
        if session is not None:
            session.state_file.unlink(missing_ok=True)

    async def reset_id(self, session_id: str) -> bool:
        """Forget one session by its public id (the UI's "断开" button)."""
        async with self._lock:
            for key, session in list(self._sessions.items()):
                if session.id == session_id:
                    self._sessions.pop(key, None)
                    session.state_file.unlink(missing_ok=True)
                    return True
        return False

    async def shutdown(self) -> None:
        async with self._lock:
            self._sessions.clear()

    def sessions(self) -> list[ShellSession]:
        return list(self._sessions.values())

    def describe(self) -> list[dict[str, Any]]:
        return [session.report() for session in self._sessions.values()]


SHELLS = ShellManager()


__all__ = [
    "ALL_FLAVOURS",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT",
    "MAX_TIMEOUT",
    "SHELLS",
    "UNIX_FLAVOURS",
    "ShellFlavour",
    "ShellKind",
    "ShellManager",
    "ShellResult",
    "ShellSession",
    "ShellUnavailable",
    "available_flavours",
    "available_shells",
    "detect_flavour",
    "detect_kind",
    "executable",
    "is_utf8",
    "state_dir",
    "strip_marker",
    "wsl_available",
]
