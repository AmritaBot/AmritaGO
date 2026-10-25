"""Per-plugin settings, declared by the plugin and rendered by the web UI.

A plugin declares a Pydantic model; the runtime stores values under
``~/.amrita/plugin_configs/<plugin>.json``. The form comes from the model, so
adding a field needs no frontend change, and values are validated on save.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from app.core.config import PATHS
from app.core.storage import write_json


class PluginConfigError(ValueError):
    """Raised when a submitted value does not validate."""

    def __init__(
        self,
        message: str,
        errors: list[dict[str, str]] | None = None,
        current_hash: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or []
        # Set when the save lost a race; the client can retry with this hash.
        self.current_hash = current_hash


def _kind_of(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is Literal:
        return "literal"
    if origin is Union:
        inner = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(inner) == 1:
            return _kind_of(inner[0])
        return "string"
    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    return "string"


def _literal_values(annotation: Any) -> list[str] | None:
    origin = get_origin(annotation)
    if origin is Literal:
        return [str(arg) for arg in get_args(annotation)]
    if origin is Union:
        inner = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(inner) == 1:
            return _literal_values(inner[0])
    return None


def _constraints(info: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for meta in info.metadata:
        for attr, key in (
            ("ge", "min"),
            ("gt", "exclusive_min"),
            ("le", "max"),
            ("lt", "exclusive_max"),
        ):
            value = getattr(meta, attr, None)
            if value is not None:
                out[key] = value
    return out


def _default(info: Any) -> Any:
    value = info.get_default(call_default_factory=True)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# Pydantic reports constraint failures in English; the form is Chinese.
_VALIDATION_MESSAGES: dict[str, str] = {
    "greater_than_equal": "不能小于 {ge}",
    "less_than_equal": "不能大于 {le}",
    "greater_than": "必须大于 {gt}",
    "less_than": "必须小于 {lt}",
    "string_too_short": "长度不能少于 {min_length} 个字符",
    "string_too_long": "长度不能超过 {max_length} 个字符",
    "missing": "必填",
    "int_parsing": "必须是整数",
    "int_type": "必须是整数",
    "float_parsing": "必须是数字",
    "float_type": "必须是数字",
    "bool_parsing": "必须是布尔值",
    "bool_type": "必须是布尔值",
    "string_type": "必须是文本",
    "literal_error": "取值不在允许范围内",
    "extra_forbidden": "该字段不受支持",
}


def _validation_message(error: Any) -> str:
    """A readable message for one Pydantic error, falling back to its own text."""
    template = _VALIDATION_MESSAGES.get(str(error.get("type", "")))
    if template is None:
        return str(error.get("msg", "取值无效"))
    try:
        return template.format(**(error.get("ctx") or {}))
    except (KeyError, IndexError):
        return template


def describe(model: type[BaseModel], prefix: str = "") -> list[dict[str, Any]]:
    """Flatten a Pydantic model into form descriptors.

    Nested models become dotted paths, matching the AmritaCore editor, so one
    renderer handles both screens.
    """
    out: list[dict[str, Any]] = []
    for name, info in model.model_fields.items():
        path = f"{prefix}{name}"
        annotation = info.annotation
        origin = get_origin(annotation)
        inner = annotation
        if origin is Union:
            candidates = [arg for arg in get_args(annotation) if arg is not type(None)]
            inner = candidates[0] if len(candidates) == 1 else annotation

        if isinstance(inner, type) and issubclass(inner, BaseModel):
            out.extend(describe(inner, f"{path}."))
            continue

        out.append(
            {
                "path": path,
                "label": (info.title or name).replace("_", " "),
                "type": _kind_of(annotation),
                "choices": _literal_values(annotation),
                "description": (info.description or "").strip(),
                "default": _default(info),
                "constraints": _constraints(info),
            }
        )
    return out


def coerce(value: Any, kind: str) -> Any:
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if kind == "integer":
        return int(value)
    if kind == "number":
        return float(value)
    return "" if value is None else str(value)


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = target
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


def content_hash(values: dict[str, Any]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical(model: BaseModel) -> dict[str, Any]:
    """Flatten a validated model into the form the hash is computed from.

    ``mode="json"`` is load-bearing, not cosmetic: Python mode dumps an ``int``
    default as ``180`` while JSON mode writes ``180.0``, so hashing the two
    produces different digests. :meth:`PluginConfigStore.save_model` and
    :func:`describe_plugin` must agree exactly, or the optimistic-concurrency
    check rejects every save with a 409 no matter what the user changed.
    """
    return _flatten(model.model_dump(mode="json"))


class PluginConfigStore:
    """Reads and writes one JSON file per plugin, without knowing the models."""

    def _path(self, plugin: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in plugin)
        return PATHS.plugin_configs / f"{safe}.json"

    def read_sync(self, plugin: str) -> dict[str, Any]:
        return _read_json_safe(self._path(plugin))

    async def read(self, plugin: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.read_sync, plugin)

    async def write(self, plugin: str, values: dict[str, Any]) -> None:
        path = self._path(plugin)
        path.parent.mkdir(parents=True, exist_ok=True)
        await write_json(path, values)

    async def delete(self, plugin: str) -> bool:
        path = self._path(plugin)
        if not path.exists():
            return False
        await asyncio.to_thread(path.unlink)
        return True

    async def load_model(self, plugin: str, model: type[BaseModel]) -> BaseModel:
        """Stored values merged over the model's defaults, then validated."""
        return self.merge(model, await self.read(plugin))

    @staticmethod
    def merge(model: type[BaseModel], stored: dict[str, Any] | None) -> BaseModel:
        """Defaults overlaid with the stored values, validated.

        Shared by :meth:`load_model` and the save path: the concurrency hash must
        be computed from exactly the model a reader would see, or a save can be
        rejected as "changed elsewhere" when nothing changed at all.
        """
        merged = model().model_dump()
        for key, value in (stored or {}).items():
            if key in model.model_fields:
                merged[key] = value
        try:
            return model.model_validate(merged)
        except ValidationError:
            # A hand-edited file must not take the plugin down; defaults still work.
            return model()

    async def save_model(
        self,
        plugin: str,
        model: type[BaseModel],
        submitted: dict[str, Any],
        provided_hash: str | None = None,
    ) -> dict[str, Any]:
        stored = await self.read(plugin)
        fields = describe(model)
        allowed = {field["path"] for field in fields}

        current = self.merge(model, stored).model_dump(mode="json")
        current_hash = content_hash(_flatten(current))
        if provided_hash is not None and provided_hash != current_hash:
            raise PluginConfigError("配置已被修改，请刷新后重试", current_hash=current_hash)

        unknown = [path for path in submitted if path not in allowed]
        if unknown:
            raise PluginConfigError(
                "包含不可修改的字段",
                [{"field": path, "message": "该字段不存在"} for path in unknown],
            )

        merged = json.loads(json.dumps(current))
        kinds = {field["path"]: field["type"] for field in fields}
        for path, value in submitted.items():
            try:
                coerced = coerce(value, kinds.get(path, "string"))
            except (TypeError, ValueError):
                raise PluginConfigError(
                    "字段取值无效",
                    [{"field": path, "message": f"无法解析为 {kinds.get(path)}"}],
                ) from None
            _set_path(merged, path, coerced)

        try:
            validated = model.model_validate(merged)
        except ValidationError as exc:
            raise PluginConfigError(
                "配置校验失败",
                [
                    {
                        "field": ".".join(str(part) for part in err.get("loc", ())),
                        "message": _validation_message(err),
                    }
                    for err in exc.errors()
                ],
            ) from None

        payload = validated.model_dump(mode="json")
        await self.write(plugin, payload)
        flat = _flatten(payload)
        return {"values": flat, "hash": content_hash(flat)}


def _flatten(data: dict[str, Any], parent: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{parent}.{key}" if parent else key
        if isinstance(value, dict):
            out.update(_flatten(value, path))
        else:
            out[path] = value
    return out


def _read_json_safe(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


CONFIGS = PluginConfigStore()


async def describe_plugin(plugin: str, model: type[BaseModel]) -> dict[str, Any]:
    """Everything the UI needs to render one plugin's settings."""
    stored = await CONFIGS.read(plugin)
    values = canonical(CONFIGS.merge(model, stored))
    return {
        "plugin": plugin,
        "fields": describe(model),
        "values": values,
        "hash": content_hash(values),
    }


__all__ = [
    "CONFIGS",
    "PluginConfigError",
    "PluginConfigStore",
    "coerce",
    "content_hash",
    "describe",
    "describe_plugin",
]
