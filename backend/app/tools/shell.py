"""Local shell execution for the agent.

Every call is a fresh shell process that starts where the previous one ended;
the directory is kept in a state file and refreshed from the shell's own OSC 7
report. Completion comes from the OSC 133/633 markers the script emits, with a
per-session nonce so command output cannot forge an exit code.

Output is capped and spilled to ``~/.amrita/shell``.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
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


def _is_wsl_launcher(path: str | None) -> bool:
    """True for ``wsl.exe`` and the legacy ``System32\\bash.exe`` shim."""
    if not path:
        return False
    candidate = Path(path)
    name = candidate.name.lower()
    if name == "wsl.exe":
        return True
    return name == "bash.exe" and candidate.parent.name.lower() == "system32"


def _wsl_executable() -> str | None:
    """``wsl.exe``, or the legacy ``System32\\bash.exe`` shim as a fallback."""
    if os.name != "nt":
        return None
    wsl = shutil.which("wsl")
    if wsl:
        return wsl
    bash = shutil.which("bash")
    return bash if _is_wsl_launcher(bash) else None


@lru_cache(maxsize=1)
def wsl_distros() -> tuple[str, ...]:
    """Installed WSL distributions, or ``()`` when WSL is unusable.

    ``wsl.exe -l -q`` writes UTF-16LE on most builds and UTF-8 when redirected
    on others, so both are accepted.
    """
    exe = _wsl_executable()
    if not exe:
        return ()
    try:
        completed = subprocess.run(
            [exe, "-l", "-q"],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if completed.returncode != 0:
        return ()
    raw = completed.stdout
    text = (
        raw.decode("utf-16-le", "ignore") if b"\x00" in raw[:32] else raw.decode("utf-8", "replace")
    )
    names = [line.strip().strip("\x00\ufeff") for line in text.splitlines()]
    return tuple(name for name in names if name)


def wsl_available() -> bool:
    """Whether an installed distro can actually run commands."""
    return bool(wsl_distros())


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
        # ``System32\\bash.exe`` is WSL, not a native bash; auto picks WSL below.
        if found and not _is_wsl_launcher(found):
            return found
    if wsl_available():
        return _wsl_executable()
    return None


def available_flavours() -> list[str]:
    """Every flavour this machine can actually run, for the settings UI."""
    out: list[str] = []
    if os.name == "nt" and (shutil.which("pwsh") or shutil.which("powershell")):
        out.append("pwsh")
    for candidate in UNIX_FLAVOURS:
        found = shutil.which(candidate)
        if found and not _is_wsl_launcher(found):
            out.append(candidate)
    if wsl_available():
        out.append("wsl")
    return out


def detect_flavour(kind: ShellKind) -> ShellFlavour:
    """The concrete program a family session will spawn."""
    if kind == "pwsh":
        return "pwsh"
    for candidate in UNIX_FLAVOURS:
        found = shutil.which(candidate)
        if found and not _is_wsl_launcher(found):
            return candidate
    if wsl_available():
        return "wsl"
    return "bash"


def available_shells() -> list[ShellKind]:
    # ``unix`` is available when any POSIX bash can run, WSL included.
    return [kind for kind in ("pwsh", "unix") if executable(kind)]


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
    """Remove our OSC markers, returning the text, exit code and final cwd."""
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
    #: WSL distribution name; empty means the default distro.
    wsl_distro: str = ""
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
            f"{self.kind}:{self.flavour}:{self.cwd}:{self.executable_path}:{self.wsl_distro}".encode()
        ).hexdigest()[:12]
        return state_dir() / f"{self.kind}-{digest}.cwd"

    def resolved_executable(self) -> str | None:
        """The configured path if it exists, else the shell on ``PATH``."""
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
            "wsl_distro": self.wsl_distro,
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
        """``wsl`` or ``msys``; unused for pwsh."""
        if self.kind != "unix":
            return "native"
        return bash_style_for(self.flavour, self.executable_path)

    def _saved_dir(self) -> str | None:
        """The directory the previous run ended in, in the shell's own form.

        Kept verbatim: a WSL ``cd /tmp`` has no Windows equivalent.
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
        # Kind stays "bash"; "wsl" would skip the Windows -> /mnt/... translation.
        return shell_path("bash", self.cwd, self.path_style)

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
                if Path(exe).name.lower() == "wsl.exe":
                    # ``wsl.exe`` needs ``--exec`` and the script path in WSL form.
                    argv = [exe]
                    if self.wsl_distro.strip():
                        argv += ["-d", self.wsl_distro.strip()]
                    argv += ["--exec", "bash", "--noprofile", "--norc", script_arg]
                elif _is_wsl_launcher(exe):
                    argv = [exe, "--noprofile", "--norc", script_arg]
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
    """One logical session per ``(kind, flavour, cwd, executable, distro)``.

    The directory survives while the process does not: every run is a fresh
    interpreter that starts where the previous one finished.
    """

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str, str, str, str], ShellSession] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        kind: ShellKind,
        cwd: Path,
        executable_path: str = "",
        init_commands: str = "",
        flavour: str = "",
        encoding: str = "utf-8",
        wsl_distro: str = "",
    ) -> ShellSession:
        resolved_flavour = flavour or detect_flavour(kind)
        distro = wsl_distro.strip()
        key = (kind, resolved_flavour, str(cwd), executable_path.strip(), distro)
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
                    wsl_distro=distro,
                )
                self._sessions[key] = session
            else:
                # Config edits apply live, so the profile is re-read per call.
                session.init_commands = init_commands
                session.output_encoding = encoding
            return session

    async def reset(
        self,
        kind: ShellKind,
        cwd: Path,
        executable_path: str = "",
        flavour: str = "",
        wsl_distro: str = "",
    ) -> None:
        resolved_flavour = flavour or detect_flavour(kind)
        async with self._lock:
            session = self._sessions.pop(
                (kind, resolved_flavour, str(cwd), executable_path.strip(), wsl_distro.strip()),
                None,
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
    "wsl_distros",
]
