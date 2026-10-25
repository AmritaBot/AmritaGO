"""Hatch build hook that bundles the built SPA into the backend artifacts.

Without it a wheel holds only Python sources, so the server starts with no UI.
The hook finds (or builds) ``frontend/dist`` and maps it to ``app/_frontend``
in the wheel; the sdist keeps it at ``frontend/dist`` so a wheel can be rebuilt
from it without Bun.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

WHEEL_TARGET = "app/_frontend"
SDIST_TARGET = "frontend/dist"

_MISSING = (
    "frontend/dist/index.html is missing and could not be built (Bun is not "
    "available or `bun run build` failed). Run `bun install && bun run build` "
    "in frontend/ before packaging, or point AMRITA_GO_FRONTEND_SRC at a "
    "directory containing built assets."
)


class FrontendBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # Editable installs read from the source tree anyway, so keep them lean
        # instead of copying the bundle into site-packages on every sync.
        if version == "editable":
            return
        dist = self._dist_dir()
        target = SDIST_TARGET if self.target_name == "sdist" else WHEEL_TARGET
        build_data.setdefault("force_include", {})[str(dist)] = target

    def _dist_dir(self) -> Path:
        root = Path(self.root)
        frontend = root.parent / "frontend"
        candidates = [
            frontend / "dist",  # repository checkout
            root / SDIST_TARGET,  # wheel built from our own sdist
            root.parent / "frontend-dist",  # prebuilt artifacts next to backend
        ]
        env = os.environ.get("AMRITA_GO_FRONTEND_SRC")
        if env:
            candidates.insert(0, Path(env).expanduser())

        for candidate in candidates:
            if (candidate / "index.html").is_file():
                return candidate.resolve()

        self._build(frontend)
        dist = frontend / "dist"
        if not (dist / "index.html").is_file():
            raise RuntimeError(_MISSING)
        return dist.resolve()

    def _build(self, frontend: Path) -> None:
        bun = os.environ.get("BUN") or shutil.which("bun")
        if bun is None or not (frontend / "build.ts").is_file():
            raise RuntimeError(_MISSING)
        subprocess.run([bun, "run", "build"], cwd=frontend, check=True)
