"""Application settings and filesystem layout.

``~/.amrita`` holds user-global state (settings, providers, plugins, agents,
workspaces, sessions); ``<workspace>/.amrita`` holds project skills and agent
presets. Credentials stay in the data directory because they carry API keys.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _probe_project_root(start: Path) -> Path:
    """The nearest ancestor that looks like this checkout, else the cwd.

    Counting parent directories only holds for a source tree; an installed
    wheel sits in ``site-packages``, where no checkout exists above it and the
    process cwd is the only sensible default workspace.
    """
    for candidate in (start, *start.parents):
        if (candidate / "backend" / "pyproject.toml").is_file() and (
            candidate / "frontend" / "build.ts"
        ).is_file():
            return candidate
    return Path.cwd()


# Repository root of a source checkout; the process cwd when installed.
PROJECT_ROOT = _probe_project_root(Path(__file__).resolve().parent)

# Package directory (``…/app``); bundled assets sit next to the code.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# SPA bundle shipped inside the wheel by ``backend/hatch_build.py``.
BUNDLED_FRONTEND = PACKAGE_ROOT / "_frontend"


def _env_path(name: str, default: Path) -> Path:
    """Resolve a directory from an env var, or fall back to ``default``."""
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


def _home_data_dir() -> Path:
    return _env_path("AMRITA_GO_DATA", Path.home() / ".amrita")


def _workspace_dir() -> Path:
    """Workspace-local ``.amrita`` (project skills, project overrides)."""
    return _env_path("AMRITA_GO_WORKSPACE", PROJECT_ROOT / ".amrita")


def _frontend_dist() -> Path:
    """Prefer the bundled SPA, fall back to the repository build output."""
    if (BUNDLED_FRONTEND / "index.html").is_file():
        return BUNDLED_FRONTEND
    return PROJECT_ROOT / "frontend" / "dist"


@dataclass(frozen=True)
class Paths:
    data: Path = field(default_factory=_home_data_dir)
    workspace: Path = field(default_factory=_workspace_dir)
    frontend_dist: Path = field(
        default_factory=lambda: _env_path("AMRITA_GO_FRONTEND", _frontend_dist())
    )

    # --- user-global, under ~/.amrita ---------------------------------- #

    @property
    def settings(self) -> Path:
        return self.data / "settings.json"

    @property
    def core_config(self) -> Path:
        return self.data / "core.json"

    @property
    def presets(self) -> Path:
        return self.data / "presets"

    @property
    def providers(self) -> Path:
        return self.data / "providers"

    @property
    def mcp(self) -> Path:
        return self.data / "mcp.json"

    @property
    def sessions(self) -> Path:
        return self.data / "sessions"

    @property
    def workspaces_file(self) -> Path:
        return self.data / "workspaces.json"

    @property
    def user_skills(self) -> Path:
        return self.data / "skills"

    @property
    def user_agents(self) -> Path:
        return self.data / "agents"

    @property
    def plugins(self) -> Path:
        return self.data / "plugins"

    @property
    def plugin_configs(self) -> Path:
        return self.data / "plugin_configs"

    # --- workspace-local, under <repo>/.amrita ------------------------- #

    @property
    def workspace_skills(self) -> Path:
        return self.workspace / "skills"

    @property
    def workspace_agents(self) -> Path:
        return self.workspace / "agents"

    def skill_dirs(self, workspace: Path | None = None) -> list[Path]:
        """Workspace first so a project can override a global skill."""
        root = workspace or self.workspace
        return [root / ".amrita" / "skills", self.user_skills]

    def agent_dirs(self, workspace: Path | None = None) -> list[Path]:
        root = workspace or self.workspace
        return [root / ".amrita" / "agents", self.user_agents]

    def ensure(self) -> None:
        for path in (
            self.data,
            self.providers,
            self.sessions,
            self.user_skills,
            self.user_agents,
            self.plugins,
            self.plugin_configs,
            self.workspace_skills,
            self.workspace_agents,
        ):
            path.mkdir(parents=True, exist_ok=True)


PATHS = Paths()

# Host / port for the bundled uvicorn runner.
HOST = os.environ.get("AMRITA_GO_HOST", "127.0.0.1")
PORT = int(os.environ.get("AMRITA_GO_PORT", "8000"))

# Fallback prompt for a preset with no body; the prompt belongs to the preset.
DEFAULT_SYSTEM_PROMPT = """\
You are AmritaGO, a coding agent working inside the user's workspace. Use the
available tools to inspect files and run commands, and keep answers short.
"""
