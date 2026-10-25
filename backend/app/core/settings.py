"""App settings (``settings.json``) plus the legacy preset migration."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Literal

from amrita_core.types import ModelPreset
from amrita_sense.logging import logger
from pydantic import BaseModel, Field

from app.core.config import PATHS
from app.core.storage import read_json, read_json_sync, write_json


class AppSettings(BaseModel):
    """User-facing knobs persisted in ``data/settings.json``."""

    #: ``provider_id::model_id``; the run-time preset is built from it.
    active_model: str | None = None
    #: Agent preset name (``agents/<name>.md``). ``None`` → built-in standard.
    active_agent: str | None = None
    #: Registered workflow to run when the active agent does not pin one.
    active_workflow: str | None = None
    #: Workspace the UI is currently pointed at; ``None`` → the project root.
    active_workspace: str | None = None
    #: ``None`` → every discovered skill is enabled. ``[]`` → none.
    enabled_skills: list[str] | None = None
    #: MCP server names (keys of ``mcp.json``) that should be connected.
    enabled_mcp: list[str] = Field(default_factory=list)
    #: Tool name globs removed from every run's pool; a filter on the per-run copy.
    disabled_tools: list[str] = Field(default_factory=list)
    #: Plugins not loaded at all; they stay registered so the UI can re-enable them.
    disabled_plugins: list[str] = Field(default_factory=list)
    #: Developer mode: reload plugins to prove they are side-effect free.
    debug: bool = False
    #: Budget policy: ``graceful`` winds down, ``ask`` parks and offers continue / graceful / abort.
    tool_limit_policy: Literal["graceful", "ask"] = "graceful"
    #: Refusal rounds after the budget is spent, applied to ``agent_tool_refusal_rounds``.
    tool_limit_extension: int = Field(default=5, ge=1, le=50)
    #: How long the parked run waits for an answer before winding down.
    tool_limit_timeout: float = Field(default=300.0, ge=10, le=3600)
    #: ``presets/*.json`` imported once; set to ``false`` to re-run the import.
    legacy_presets_imported: bool = False
    updated_at: float = Field(default_factory=time.time)


_DEFAULT = AppSettings()


async def load_settings() -> AppSettings:
    raw = await read_json(PATHS.settings)
    return AppSettings.model_validate(raw) if raw else _DEFAULT.model_copy()


def load_settings_sync() -> AppSettings:
    raw = read_json_sync(PATHS.settings)
    return AppSettings.model_validate(raw) if raw else _DEFAULT.model_copy()


async def save_settings(settings: AppSettings) -> AppSettings:
    settings.updated_at = time.time()
    await write_json(PATHS.settings, settings.model_dump())
    return settings


# Legacy preset files


def preset_path(name: str) -> Path:
    """Resolve a legacy preset file.

    Presets were replaced by providers, but a ``~/.amrita/presets/*.json`` left
    behind by an older install is still readable: refusing to start because a
    stale file exists would be hostile, and quietly deleting it would lose a
    key. :func:`migrate_legacy_presets` imports them once.
    """
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_ ")
    if not safe.strip():
        raise ValueError("invalid preset name")
    return PATHS.presets / f"{safe}.json"


def list_presets_sync() -> list[ModelPreset]:
    PATHS.ensure()
    presets: list[ModelPreset] = []
    for path in sorted(PATHS.presets.glob("*.json")):
        try:
            presets.append(ModelPreset.load(path))
        except (ValueError, OSError) as exc:
            # One malformed preset must not hide every other preset.
            logger.warning(f"Skipping unreadable preset {path}: {exc}")
    return presets


async def list_presets() -> list[ModelPreset]:
    return await asyncio.to_thread(list_presets_sync)


def get_preset_sync(name: str) -> ModelPreset | None:
    for preset in list_presets_sync():
        if preset.name == name:
            return preset
    return None


async def get_preset(name: str) -> ModelPreset | None:
    return await asyncio.to_thread(get_preset_sync, name)


def preset_public(preset: ModelPreset) -> dict[str, Any]:
    """Serialize a legacy preset, masking its key."""
    data = preset.model_dump(mode="json")
    key = data.get("api_key") or ""
    data["api_key"] = f"{key[:4]}…{key[-4:]}" if len(key) > 12 else ("***" if key else "")
    data["has_api_key"] = bool(key)
    return data


def _provider_id_for(base_url: str, name: str) -> str:
    from app.core.providers import new_provider_id

    return new_provider_id(name or base_url)


async def migrate_legacy_presets() -> int:
    """Import ``presets/*.json`` into the provider store, once.

    Presets grouped by ``(base_url, protocol)`` become a single provider with
    one model each — that is the shape the user actually had. The legacy files
    are left alone: a failed migration must remain re-runnable, and the files
    themselves are the only remaining copy of a key if this ever goes wrong.

    "Once" is enforced by :attr:`AppSettings.legacy_presets_imported`, and that
    matters more than it looks: this used to run on every startup, so deleting a
    provider in the UI resurrected it (keyless) after a restart, and the app
    looked like it kept bootstrapping a provider the user had already removed.
    A migration that can silently undo a deletion is not a migration.
    """
    from app.core.providers import (
        PROVIDERS,
        Credentials,
        ModelEntry,
        Provider,
        preset_key,
    )

    settings = await load_settings()
    if settings.legacy_presets_imported:
        return 0

    presets = await list_presets()
    if not presets:
        # Nothing on disk: do not burn the flag, so a later preset is still offered.
        return 0

    # Mark before doing the work: "offered", not "succeeded".
    settings.legacy_presets_imported = True
    await save_settings(settings)

    existing = {provider.id: provider for provider in await PROVIDERS.list()}
    imported = 0
    for preset in presets:
        base = (preset.base_url or "").rstrip("/")
        if not base or not preset.model:
            continue
        match = next(
            (
                provider
                for provider in existing.values()
                if provider.base_url.rstrip("/") == base
                and provider.protocol == (preset.protocol or "openai")
            ),
            None,
        )
        if match is None:
            match = Provider(
                id=_provider_id_for(base, preset.name),
                name=preset.name or base,
                base_url=base,
                protocol=preset.protocol or "openai",
                max_context=preset.max_context,
                max_output=preset.max_output or 28000,
            )
            existing[match.id] = match
            await PROVIDERS.save(match)
        if match.model_entry(preset.model) is None:
            match.models.append(
                ModelEntry(
                    id=preset.model,
                    max_context=preset.max_context,
                    max_output=preset.max_output or 28000,
                    multimodal=bool(preset.config.multimodal),
                    temperature=preset.config.temperature,
                    top_p=preset.config.top_p,
                    thinking=preset.thinking_config is not None,
                )
            )
            await PROVIDERS.save(match)
        if preset.api_key and not match.has_api_key:
            await PROVIDERS.save(match, Credentials(api_key=preset.api_key))
        imported += 1

    if settings.active_model is None and presets:
        first = presets[0]
        base = (first.base_url or "").rstrip("/")
        provider = next(
            (item for item in existing.values() if item.base_url.rstrip("/") == base),
            None,
        )
        if provider is not None:
            settings.active_model = preset_key(provider.id, first.model)
            await save_settings(settings)

    logger.info(f"Imported {imported} legacy preset(s) into providers")
    return imported
