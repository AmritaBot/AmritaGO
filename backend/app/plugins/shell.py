"""Shell tools: one plugin per *family*, sharing one session layer.

``unix`` covers bash, zsh and WSL; ``pwsh`` stays separate because its dialect
and path form differ. The shared layer is :class:`app.tools.shell.ShellSession`.
An optional audit gate can show a command to the user, who may edit or reject
it with a reason returned to the model as an ordinary tool result.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Literal

from amrita_core.tools.models import (
    FunctionDefinitionSchema,
    FunctionParametersSchema,
    FunctionPropertySchema,
    ToolData,
    ToolFunctionSchema,
)
from amrita_sense.logging import logger
from pydantic import BaseModel, Field, field_validator

from app.capabilities.permissions import command_allowed_for_mode
from app.core.workspaces import resolve_workspace
from app.plugins import PluginContext, PluginRuntime, ToolSource, load_config
from app.runtime.audit import BROKER
from app.runtime.stream_hub import HUB
from app.tools.shell import (
    ALL_FLAVOURS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT,
    MAX_TIMEOUT,
    SHELLS,
    ShellKind,
    ShellSession,
    ShellUnavailable,
    available_flavours,
    available_shells,
    detect_flavour,
    wsl_distros,
)

_COMMAND_ARG = "command"
_TIMEOUT_ARG = "timeout"
_CWD_ARG = "cwd"

# Approval policy: ``rules`` (allow-list), ``bypass`` (never ask), ``writes``, ``always``.
AuditMode = Literal["rules", "bypass", "writes", "always"]


class ShellConfig(BaseModel):
    """Settings shared by the pwsh and unix plugins."""

    max_output_kb: int = Field(
        default=DEFAULT_MAX_OUTPUT_BYTES // 1024,
        ge=4,
        le=2048,
        title="输出上限（KiB）",
        description="单次命令回传给模型的最大输出；超出部分写入文件并只给头尾。",
    )
    default_timeout: float = Field(
        default=DEFAULT_TIMEOUT,
        ge=1,
        le=MAX_TIMEOUT,
        title="默认超时（秒）",
        description="模型未指定 timeout 时的等待上限。",
    )
    max_timeout: float = Field(
        default=MAX_TIMEOUT,
        ge=1,
        le=3600,
        title="超时上限（秒）",
        description="模型可以要求更长的超时，但不得超过这个值。",
    )
    allow_outside_workspace: bool = Field(
        default=False,
        title="允许越过工作区",
        description="关闭时 cwd 必须位于当前工作区内；开启后不做路径限制。",
    )
    executable: str = Field(
        default="",
        title="可执行文件",
        description=(
            "留空则用 PATH 上的那个。想固定到某个解释器时写完整路径，"
            "例如 /bin/bash、/usr/bin/zsh、C:\\Program Files\\PowerShell\\7\\pwsh.exe。"
        ),
    )
    init_commands: str = Field(
        default="",
        title="初始化脚本",
        description=(
            "每次执行命令前先跑这一段，用来加载 shell 配置，例如 "
            "source ~/.bashrc 或 . $PROFILE。注意它会在每次调用里重跑一遍。"
        ),
    )
    encoding: str = Field(
        default="utf-8",
        title="输出编码",
        description=(
            "解码子进程输出用的编码。pwsh 下选 utf-8 会同时把控制台编码设成 UTF-8，"
            "这是 Windows 上不乱码的关键；若某个老工具坚持输出 GBK，改成 gbk。"
        ),
    )
    audit: AuditMode = Field(
        default="writes",
        title="审批模式",
        description=(
            "rules = 自动审批（规则）：命中「自动批准」直接跑，否则询问；"
            "bypass = 完全绕过，任何命令都不问；"
            "writes = 只在看起来会改文件的命令上问；"
            "always = 每条命令都问。任何模式下都可以在确认框里改命令或填理由拒绝。"
        ),
    )
    auto_approve: str = Field(
        default="",
        title="自动批准的规则",
        description=(
            "仅在 rules 模式下生效。一行（或逗号分隔）一条，整条命令匹配，"
            "`*` 代表任意字符，例如 `git status*`、`uv run pytest*`。区分大小写以外的部分。"
        ),
    )
    always_ask: str = Field(
        default="",
        title="强制询问的规则",
        description=(
            "仅在 rules 模式下生效，写法同上。命中就询问，**优先于自动批准**——"
            "用来在放行一片命令时把危险的那几条单独拦下来。"
        ),
    )

    @field_validator("audit", mode="before")
    @classmethod
    def _migrate_off(cls, value: Any) -> Any:
        """``off`` was the old spelling of "never ask"; keep those files valid."""
        if isinstance(value, str) and value.strip().lower() == "off":
            return "bypass"
        return value


# Same vocabulary as the read-only guard, so "what counts as a write" has one definition.
_WRITE_HINTS = (
    "rm",
    "rmdir",
    "mv",
    "cp",
    "mkdir",
    "touch",
    "chmod",
    "chown",
    "dd",
    "tee",
    "truncate",
    "ln",
    "rsync",
    "git add",
    "git commit",
    "git push",
    "git reset",
    "git clean",
    "git checkout",
    "git restore",
    "npm install",
    "bun add",
    "pip install",
    "uv add",
    "apt",
    "brew",
    "winget",
    "set-content",
    "add-content",
    "out-file",
    "new-item",
    "remove-item",
    "move-item",
    "copy-item",
    "sudo",
    ">",
)


def looks_like_write(command: str) -> bool:
    lowered = command.lower()
    return any(hint in lowered for hint in _WRITE_HINTS)


class UnixConfig(ShellConfig):
    flavour: Literal["auto", "bash", "zsh", "wsl"] = Field(
        default="auto",
        title="Shell 方言",
        description=(
            "auto 优先原生 bash / zsh，没有原生 shell 时才回退到 WSL；"
            "wsl 表示通过 wsl.exe 在 Linux 发行版里跑 /bin/bash，路径会翻译成 /mnt/c/...；"
            "Git Bash 用 /c/...（把可执行文件指向 Git 的 bash.exe 时会自动按 Git Bash 处理）。"
        ),
    )
    wsl_distro: str = Field(
        default="",
        title="WSL 发行版",
        description=(
            "仅在 WSL 方言下生效；留空使用默认发行版。可填 `wsl -l -q` 列出的名称，"
            "例如 Ubuntu。指定发行版会作为独立会话，不与默认发行版共享当前目录。"
        ),
    )


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]):
    return ToolFunctionSchema(
        type="function",
        strict=True,
        function=FunctionDefinitionSchema(
            name=name,
            description=description,
            parameters=FunctionParametersSchema(
                type="object",
                properties=properties,
                required=required,
            ),
        ),
    )


def _workspace_root(context: Any) -> Path:
    raw = getattr(getattr(context, "settings", None), "active_workspace", None)
    return resolve_workspace(raw)


def _resolve_target(root: Path, requested: str | None) -> Path:
    """Keep every shell call inside the workspace."""
    base = root if not requested else (root / requested).expanduser()
    try:
        resolved = base.resolve()
    except OSError as exc:
        raise ValueError(f"cannot resolve {base}: {exc}") from exc
    if resolved != root and root not in resolved.parents:
        raise ValueError(
            f"{resolved} is outside the workspace; start the agent on a "
            "different workspace to work there"
        )
    if not resolved.is_dir():
        raise ValueError(f"{resolved} is not a directory")
    return resolved


def _flavour_for(kind: ShellKind, config: ShellConfig) -> str:
    if kind == "pwsh":
        return "pwsh"
    requested = str(getattr(config, "flavour", "auto") or "auto").strip().lower()
    if requested in {"", "auto"}:
        return detect_flavour(kind)
    # A flavour this machine cannot run would fail on its first command; fall back.
    return requested if requested in ALL_FLAVOURS else detect_flavour(kind)


def _split_rules(raw: str) -> list[str]:
    """One rule per line, with ``,``/``;`` accepted so a single-line input works."""
    return [part.strip() for part in re.split(r"[\n,;]+", raw or "") if part.strip()]


def _rule_regex(rule: str) -> re.Pattern[str]:
    """``git status*`` -> a full-match pattern; ``*`` is the only wildcard."""
    parts = [re.escape(piece) for piece in rule.split("*")]
    return re.compile(".*".join(parts), re.IGNORECASE | re.DOTALL)


def matches_rule(rules: str, command: str) -> str | None:
    """The first rule that matches the whole command, or ``None``."""
    for rule in _split_rules(rules):
        try:
            if _rule_regex(rule).fullmatch(command.strip()):
                return rule
        except re.error:  # pragma: no cover - re.escape makes this impossible
            logger.warning(f"Ignoring unparsable shell rule: {rule}")
    return None


async def _audit(
    ctx: Any,
    session: ShellSession,
    command: str,
    config: ShellConfig,
) -> tuple[str, str | None]:
    """Ask the user about ``command``; return ``(command, rejection_reason)``.

    Anything that goes wrong (no channel, timeout, no answer) resolves to
    "do not run".
    """
    mode = str(config.audit or "writes").strip().lower()
    if mode == "bypass":
        # Explicitly configured: no question, no dwell; logged at debug.
        logger.debug(f"shell audit bypassed: {command[:120]}")
        return command, None
    if mode == "rules":
        deny = matches_rule(config.always_ask, command)
        if deny is not None:
            # Deny beats allow, so a user can carve danger out of a broad allow.
            logger.info(f"shell audit: '{deny}' forces a prompt for {command[:80]}")
        else:
            allow = matches_rule(config.auto_approve, command)
            if allow is not None:
                logger.info(f"shell audit auto-approved by '{allow}': {command[:80]}")
                return command, None
    elif mode == "writes" and not looks_like_write(command):
        return command, None

    session_id = getattr(ctx, "session_id", None)
    channel = HUB.latest(session_id) if session_id else None
    if channel is None:
        # No live stream means nobody to ask; refusing is the safe direction.
        return command, (
            "这条命令需要人工确认，但当前没有可用的会话流（例如非流式调用），"
            "所以没有执行。请在界面里重新发起。"
        )

    last = session.runs[-1] if session.runs else None
    output = ""
    if last:
        output = f"$ {last['command']}\nexit={last['exit_code']}\n{last['output'][-1500:]}"

    decision = await BROKER.ask(
        channel,
        title="Agent 想执行命令",
        command=command,
        cwd=session.display_cwd(),
        shell=f"{session.kind}/{session.flavour}",
        hint="可以修改命令后再批准；拒绝时请写清理由，它会作为工具结果返回给模型。",
        output=output,
    )
    if not decision.approved:
        reason = decision.reason or "用户拒绝执行"
        return decision.command, f"用户拒绝了这条命令：{reason}"
    return decision.command, None


def _tool_pair(kind: ShellKind, plugin_name: str) -> list[ToolData]:
    label = "PowerShell" if kind == "pwsh" else "Unix shell"
    prefix = "pwsh" if kind == "pwsh" else "unix"
    run_name = f"{prefix}_run"
    status_name = f"{prefix}_status"

    run_hint = (
        f"Run a command in {label}. The working directory survives between "
        "calls, so `cd` here is visible to the next call."
    )
    if kind == "unix" and os.name == "nt":
        run_hint += (
            " On Windows this goes through Git Bash or WSL (wsl.exe), so paths "
            "written inside the command must be in that shell's form "
            "(e.g. /mnt/c/... under WSL); the cwd argument is translated for you."
        )

    async def run(ctx: Any) -> str:
        config = await load_config(plugin_name, UnixConfig if kind == "unix" else ShellConfig)
        assert isinstance(config, ShellConfig)
        mode = str(getattr(ctx, "access_mode", None) or "workspace")
        if mode == "readonly":
            # Normally filtered out of the pool; this catches an alias or an in-flight run.
            return (
                "error: the shell is unavailable in read-only mode "
                "(只读模式不提供 shell). Switch the access mode to "
                "「工作区内修改」 or 「完全权限」 to run commands."
            )
        root = _workspace_root(ctx)
        data = ctx.data
        command = str(data.get(_COMMAND_ARG) or "").strip()
        if not command:
            return "error: command is required"
        refusal = command_allowed_for_mode(command, mode)
        if refusal:
            return f"error: {refusal}"
        try:
            if config.allow_outside_workspace:
                target = _workspace_root(ctx)
                if data.get(_CWD_ARG):
                    target = (target / str(data[_CWD_ARG])).expanduser().resolve()
                if not target.is_dir():
                    return f"error: {target} is not a directory"
            else:
                target = _resolve_target(root, data.get(_CWD_ARG))
        except (OSError, ValueError) as exc:
            return f"error: {exc}"

        try:
            timeout = float(data.get(_TIMEOUT_ARG) or config.default_timeout)
        except (TypeError, ValueError):
            timeout = config.default_timeout
        timeout = min(max(1.0, timeout), config.max_timeout)

        session = await SHELLS.get(
            kind,
            target,
            config.executable,
            config.init_commands,
            _flavour_for(kind, config),
            config.encoding,
            str(getattr(config, "wsl_distro", "") or ""),
        )
        session.max_output_bytes = config.max_output_kb * 1024

        command, rejection = await _audit(ctx, session, command, config)
        if rejection:
            return f"error: {rejection}"

        try:
            result = await session.run(command, timeout=timeout)
        except ShellUnavailable as exc:
            return f"error: {exc}"
        return result.as_text()

    async def status(ctx: Any) -> str:
        config = await load_config(plugin_name, UnixConfig if kind == "unix" else ShellConfig)
        assert isinstance(config, ShellConfig)
        root = _workspace_root(ctx)
        sessions = [item for item in SHELLS.describe() if item["shell"] == kind]
        configured = config.executable.strip()
        # A configured path only counts as installed if it exists.
        installed = (
            await asyncio.to_thread(os.path.isfile, configured)
            if configured
            else kind in available_shells()
        )
        exe = configured or "(PATH)"
        flavour = _flavour_for(kind, config)
        lines = [
            f"shell: {kind}",
            f"flavour: {flavour}",
            f"executable: {exe}",
            f"installed: {'yes' if installed else 'no'}",
            f"flavours_available: {', '.join(available_flavours()) or 'none'}",
            f"workspace: {root}",
            f"workspace_guard: {'off' if config.allow_outside_workspace else 'on'}",
            f"audit: {config.audit}",
            f"encoding: {config.encoding}",
            f"timeout: {config.default_timeout:.0f}s (cap {config.max_timeout:.0f}s)",
        ]
        if kind == "unix" and flavour == "wsl":
            distros = ", ".join(wsl_distros()) or "none"
            selected = str(getattr(config, "wsl_distro", "") or "") or "(default)"
            lines.append(f"wsl_distro: {selected}")
            lines.append(f"wsl_distros_available: {distros}")
        if str(config.audit) == "rules":
            # Counts only: the rule list can be long, and tokens are charged for it.
            lines.append(f"auto_approve_rules: {len(_split_rules(config.auto_approve))}")
            lines.append(f"always_ask_rules: {len(_split_rules(config.always_ask))}")
        if sessions:
            lines.append("sessions:")
            lines.extend(f"- {item['cwd']} ({item['runs']} runs)" for item in sessions)
        else:
            lines.append("sessions: none started yet")
        return "\n".join(lines)

    return [
        ToolData(
            data=_schema(
                run_name,
                run_hint + " Use it for building, testing and inspecting the workspace.",
                {
                    _COMMAND_ARG: FunctionPropertySchema(
                        type="string",
                        description=f"{label} command line to execute.",
                    ),
                    _CWD_ARG: FunctionPropertySchema(
                        type="string",
                        description=(
                            "Subdirectory of the workspace to start in. "
                            "Relative; must stay inside the workspace."
                        ),
                    ),
                    _TIMEOUT_ARG: FunctionPropertySchema(
                        type="number",
                        description="Seconds; omit for the configured default.",
                    ),
                },
                [_COMMAND_ARG],
            ),
            func=run,
            custom_run=True,
        ),
        ToolData(
            data=_schema(
                status_name,
                f"Report the {label} session's current directory, and whether "
                f"{label} is installed on this machine.",
                {},
                [],
            ),
            func=status,
            custom_run=True,
        ),
    ]


def _build(kind: ShellKind, plugin_name: str):
    def build(_context: Any) -> list[ToolData]:
        return _tool_pair(kind, plugin_name)

    return build


async def pwsh_plugin(ctx: PluginContext):
    """Register the PowerShell tool pair."""
    ctx.config(ShellConfig)
    ctx.tool(
        ToolSource(
            name="pwsh",
            description="保目录持久化的 PowerShell",
            build=_build("pwsh", "shell-pwsh"),
        )
    )
    yield


async def unix_plugin(ctx: PluginContext):
    """Register the POSIX shell tool pair (bash and zsh share it)."""
    ctx.config(UnixConfig)
    ctx.tool(
        ToolSource(
            name="unix",
            description="bash / zsh，共用一套会话层",
            build=_build("unix", "shell-unix"),
        )
    )
    yield


def install_shell_plugins(runtime: PluginRuntime) -> None:
    """Register whichever shell families this machine actually has."""
    installed = available_shells()
    if "pwsh" in installed:
        runtime.register(
            pwsh_plugin,
            name="shell-pwsh",
            description="PowerShell 工具（pwsh_run / pwsh_status）",
        )
    else:
        logger.info("PowerShell not found; the pwsh shell plugin is not registered")

    if "unix" in installed:
        runtime.register(
            unix_plugin,
            name="shell-unix",
            description="Unix-like shell 工具（unix_run / unix_status，bash 或 zsh）",
        )
    else:
        logger.info("Neither bash nor zsh found; the unix shell plugin is not registered")


__all__ = [
    "ShellConfig",
    "UnixConfig",
    "install_shell_plugins",
    "looks_like_write",
    "pwsh_plugin",
    "unix_plugin",
]
