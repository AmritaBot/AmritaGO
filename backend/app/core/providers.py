"""Providers: an endpoint plus a key, with the models on top.

Metadata lives in ``providers/registry.json``, the API key (and optional extra
headers) in ``providers/<id>/credentials.enc``, so the provider list can be
read and shown without touching secrets. A :class:`ModelPreset` is derived on
demand and never persisted: editing a base URL fixes every model behind it.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from amrita_core.types import ModelPreset
from amrita_core.types.billing import RateConfig
from amrita_sense.logging import logger
from pydantic import BaseModel, Field

from app.core.config import PATHS
from app.core.crypto import SecretError, decrypt_blob, encrypt_blob

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ProviderError(ValueError):
    """Raised for anything the user can fix by editing the form."""


class ModelEntry(BaseModel):
    id: str
    label: str = ""
    max_context: int | None = None
    max_output: int = 28000
    multimodal: bool = False
    temperature: float = 0.7
    top_p: float = 0.8
    # Ask for the provider's own reasoning channel; off by default, not every model takes it.
    thinking: bool = False
    # Optional per-1k-token prices, in ``currency``.
    price_in: float | None = None
    price_out: float | None = None
    currency: str = "USD"


class Provider(BaseModel):
    id: str
    name: str
    base_url: str
    protocol: str = "openai"
    # Provider-level defaults a model may inherit.
    max_context: int | None = None
    max_output: int = 28000
    enabled: bool = True
    models: list[ModelEntry] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    has_api_key: bool = False

    def model_entry(self, model_id: str) -> ModelEntry | None:
        for entry in self.models:
            if entry.id == model_id:
                return entry
        return None

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "protocol": self.protocol,
            "max_context": self.max_context,
            "max_output": self.max_output,
            "enabled": self.enabled,
            "has_api_key": self.has_api_key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "models": [entry.model_dump() for entry in self.models],
        }


@dataclass
class Credentials:
    api_key: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)


class ProviderStore:
    """File-backed registry with the credentials split out."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    # paths

    @property
    def root(self):
        return PATHS.providers

    def _registry(self):
        return self.root / "registry.json"

    def _cred_path(self, provider_id: str):
        return self.root / provider_id / "credentials.enc"

    def _validate_id(self, provider_id: str) -> str:
        if not _SAFE_ID.match(provider_id):
            raise ProviderError("invalid provider id")
        return provider_id

    # reads

    def list_sync(self) -> list[Provider]:
        raw = read_json_sync_safe(self._registry())
        out: list[Provider] = []
        for item in raw:
            try:
                provider = Provider.model_validate(item)
            except ValueError:
                logger.warning("Skipping unreadable provider entry")
                continue
            provider.has_api_key = self._cred_path(provider.id).exists()
            out.append(provider)
        out.sort(key=lambda item: item.name.lower())
        return out

    async def list(self) -> list[Provider]:
        return await asyncio.to_thread(self.list_sync)

    def get_sync(self, provider_id: str) -> Provider | None:
        for provider in self.list_sync():
            if provider.id == provider_id:
                return provider
        return None

    async def get(self, provider_id: str) -> Provider | None:
        return await asyncio.to_thread(self.get_sync, provider_id)

    def credentials_sync(self, provider_id: str) -> Credentials:
        path = self._cred_path(self._validate_id(provider_id))
        if not path.exists():
            return Credentials()
        try:
            blob = decrypt_blob(PATHS.data, path.read_bytes())
        except SecretError as exc:
            logger.error(f"Cannot read credentials for {provider_id}: {exc}")
            return Credentials()
        try:
            data = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.error(f"Credential payload for {provider_id} is not JSON: {exc}")
            return Credentials()
        return Credentials(
            api_key=str(data.get("api_key") or ""),
            extra_headers={
                str(key): str(value) for key, value in (data.get("extra_headers") or {}).items()
            },
        )

    async def credentials(self, provider_id: str) -> Credentials:
        return await asyncio.to_thread(self.credentials_sync, provider_id)

    # writes

    async def save(self, provider: Provider, credentials: Credentials | None = None) -> Provider:
        self._validate_id(provider.id)
        provider.updated_at = time.time()
        if not provider.created_at:
            provider.created_at = provider.updated_at
        async with self._lock:
            providers = self.list_sync()
            replaced = False
            for index, existing in enumerate(providers):
                if existing.id == provider.id:
                    providers[index] = provider
                    replaced = True
                    break
            if not replaced:
                providers.append(provider)
            await asyncio.to_thread(self._write_registry, providers)
            if credentials is not None:
                await asyncio.to_thread(self._write_credentials, provider.id, credentials)
        provider.has_api_key = self._cred_path(provider.id).exists()
        return provider

    def _write_registry(self, providers: list[Provider]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = [provider.model_dump(exclude={"has_api_key"}) for provider in providers]
        write_json_sync_safe(self._registry(), payload)

    def _write_credentials(self, provider_id: str, credentials: Credentials) -> None:
        path = self._cred_path(provider_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not credentials.api_key and not credentials.extra_headers:
            path.unlink(missing_ok=True)
            return
        payload = json.dumps(
            {
                "api_key": credentials.api_key,
                "extra_headers": credentials.extra_headers,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        path.write_bytes(encrypt_blob(PATHS.data, payload))

    async def delete(self, provider_id: str) -> bool:
        async with self._lock:
            providers = self.list_sync()
            remaining = [item for item in providers if item.id != provider_id]
            if len(remaining) == len(providers):
                return False
            await asyncio.to_thread(self._write_registry, remaining)
        directory = self.root / self._validate_id(provider_id)
        if directory.exists():
            for child in directory.iterdir():
                child.unlink(missing_ok=True)
            directory.rmdir()
        return True

    async def set_enabled(self, provider_id: str, enabled: bool) -> Provider | None:
        provider = await self.get(provider_id)
        if provider is None:
            return None
        provider.enabled = enabled
        return await self.save(provider)

    # models

    async def add_model(self, provider_id: str, entry: ModelEntry) -> Provider:
        provider = await self.get(provider_id)
        if provider is None:
            raise ProviderError("provider not found")
        if provider.model_entry(entry.id) is not None:
            raise ProviderError(f"model '{entry.id}' is already added")
        provider.models.append(entry)
        return await self.save(provider)

    async def update_model(self, provider_id: str, entry: ModelEntry) -> Provider:
        provider = await self.get(provider_id)
        if provider is None:
            raise ProviderError("provider not found")
        for index, existing in enumerate(provider.models):
            if existing.id == entry.id:
                provider.models[index] = entry
                return await self.save(provider)
        raise ProviderError(f"model '{entry.id}' is not part of this provider")

    async def remove_model(self, provider_id: str, model_id: str) -> Provider:
        provider = await self.get(provider_id)
        if provider is None:
            raise ProviderError("provider not found")
        remaining = [item for item in provider.models if item.id != model_id]
        if len(remaining) == len(provider.models):
            raise ProviderError(f"model '{model_id}' is not part of this provider")
        provider.models = remaining
        return await self.save(provider)


def read_json_sync_safe(path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig") or "[]")
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(f"Cannot read {path}: {exc}")
        return []
    return raw if isinstance(raw, list) else []


def write_json_sync_safe(path, payload: Any) -> None:
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        from pathlib import Path

        Path(tmp).unlink(missing_ok=True)
        raise


PROVIDERS = ProviderStore()


def new_provider_id(name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:32] or "provider"
    return f"{slug}-{uuid.uuid4().hex[:6]}"


def build_preset(provider: Provider, entry: ModelEntry, credentials: Credentials) -> ModelPreset:
    """Assemble a run-time preset. Never persisted."""
    rate: RateConfig | None = None
    if entry.price_in is not None or entry.price_out is not None:
        from decimal import Decimal

        rate = RateConfig(
            per=1000,
            input=Decimal(str(entry.price_in or 0)),
            output=Decimal(str(entry.price_out or 0)),
            currency=entry.currency,
        )
    return ModelPreset.model_validate(
        {
            "name": f"{provider.name}/{entry.id}",
            "model": entry.id,
            "base_url": provider.base_url,
            "api_key": credentials.api_key,
            "protocol": provider.protocol,
            "rate": rate,
            "max_context": entry.max_context or provider.max_context,
            "max_output": entry.max_output or provider.max_output,
            "config": {
                "stream": True,
                "temperature": entry.temperature,
                "top_p": entry.top_p,
                "multimodal": entry.multimodal,
            },
            "thinking_config": (
                {"thinking_type": "enabled", "content_mode": "optional"} if entry.thinking else None
            ),
            "extra": dict(credentials.extra_headers),
        }
    )


def preset_key(provider_id: str, model_id: str) -> str:
    return f"{provider_id}::{model_id}"


def split_key(key: str) -> tuple[str, str]:
    provider_id, _, model_id = key.partition("::")
    if not provider_id or not model_id:
        raise ProviderError("a model reference must be 'provider::model'")
    return provider_id, model_id


async def resolve_preset(key: str | None) -> ModelPreset | None:
    """Turn a stored ``provider::model`` reference into a usable preset."""
    if not key:
        return None
    provider_id, model_id = split_key(key)
    provider = await PROVIDERS.get(provider_id)
    if provider is None or not provider.enabled:
        return None
    entry = provider.model_entry(model_id)
    if entry is None:
        return None
    credentials = await PROVIDERS.credentials(provider_id)
    return build_preset(provider, entry, credentials)


async def list_models() -> list[dict[str, Any]]:
    """Flat list for the model switcher: one row per provider/model pair."""
    providers = [provider for provider in await PROVIDERS.list() if provider.enabled]
    return [
        {
            "key": preset_key(provider.id, entry.id),
            "provider_id": provider.id,
            "provider": provider.name,
            "model": entry.id,
            "label": entry.label or entry.id,
            "multimodal": entry.multimodal,
            "max_context": entry.max_context or provider.max_context,
            "has_api_key": provider.has_api_key,
        }
        for provider in providers
        for entry in provider.models
    ]


# Probing


class ProbeResult(BaseModel):
    ok: bool
    models: list[str] = Field(default_factory=list)
    error: str | None = None
    latency_ms: int | None = None


async def probe_models(
    base_url: str,
    api_key: str,
    protocol: str = "openai",
    timeout: float = 12.0,
) -> ProbeResult:
    """Ask the endpoint which models it serves.

    A provider that does not implement ``/models`` is not broken — plenty of
    OpenAI-compatible gateways skip it — so a failure returns ``ok=False`` with
    the reason and the form stays usable for manual entry.
    """
    base = base_url.rstrip("/")
    if not base:
        return ProbeResult(ok=False, error="base URL is empty")
    if protocol == "anthropic":
        url = f"{base}/v1/models"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    else:
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return ProbeResult(ok=False, error=f"{type(exc).__name__}: {exc}")
    latency = int((time.monotonic() - started) * 1000)

    if response.status_code >= 400:
        detail = response.text[:200].replace("\n", " ")
        return ProbeResult(
            ok=False,
            error=f"HTTP {response.status_code} from {url}: {detail}",
            latency_ms=latency,
        )
    try:
        payload = response.json()
    except ValueError:
        return ProbeResult(ok=False, error="response is not JSON", latency_ms=latency)

    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return ProbeResult(ok=False, error="unexpected response shape", latency_ms=latency)

    names: list[str] = []
    for item in items:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("id") or item.get("name")
            if isinstance(name, str):
                names.append(name)
    names = sorted(set(names))
    return ProbeResult(ok=True, models=names, latency_ms=latency)


__all__ = [
    "PROVIDERS",
    "Credentials",
    "ModelEntry",
    "ProbeResult",
    "Provider",
    "ProviderError",
    "ProviderStore",
    "build_preset",
    "list_models",
    "new_provider_id",
    "preset_key",
    "probe_models",
    "resolve_preset",
    "split_key",
]
