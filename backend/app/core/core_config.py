"""AmritaCore runtime config: schema-driven editing with validation.

Mirrors the AmritaBot config editor: the backend derives a form schema from the
Pydantic model itself, the client renders fields from that schema, and the
server re-validates whatever comes back before it is persisted. Two details
matter:

* **Schema from the model, not a hand-written list.** ``AmritaConfig`` already
  carries a description for every field, so a new option appears in the UI as
  soon as the model changes — nothing to keep in sync by hand.
* **Optimistic concurrency via a content hash.** The client sends back the hash
  it read. If the file changed underneath it the save is rejected with 409
  instead of silently clobbering the edit.

Only *some* fields are editable. Anything that would let a single request
exfiltrate data (API keys, base URLs) is intentionally excluded from the
editable set: those belong to presets, which have their own endpoint.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Union, get_args, get_origin

from amrita_core import get_config
from amrita_core.config import AmritaConfig
from amrita_sense.logging import logger
from pydantic import BaseModel, ValidationError

from app.core.config import PATHS
from app.core.storage import read_json, write_json

#: Short Chinese labels; the library's English description stays as a tooltip.
FIELD_LABELS: dict[str, str] = {
    "llm.max_tokens": "输出 Token 上限",
    "llm.llm_timeout": "请求超时（秒）",
    "llm.max_retries": "重试次数",
    "llm.max_fallbacks": "预设回退次数",
    "llm.context_strategy": "上下文超限策略",
    "llm.compaction_trigger_ratio": "压缩触发比例",
    "llm.slide_target_ratio": "滑动裁剪目标比例",
    "llm.memory_length_limit": "历史消息条数上限",
    "llm.enable_overflow_recovery": "超限后自动恢复",
    "llm.enable_multi_modal": "启用多模态",
    "llm.session_tokens_windows": "默认上下文窗口",
    "llm.compaction_max_tokens": "摘要输出上限",
    "function_config.use_minimal_context": "精简上下文",
    "function_config.agent_tool_call_limit": "工具调用轮数上限",
    "function_config.agent_tool_refusal_rounds": "超限后拒绝轮数",
    "function_config.validate_tool_arguments": "校验工具参数",
    "function_config.agent_step_token_budget": "单步 Token 预算",
    "function_config.agent_middle_message": "允许中间消息",
    "builtin.tool_calling_mode": "工具调用模式",
    "builtin.agent_thought_mode": "思考模式",
    "builtin.loop_reasoning_trigger": "重复推理上限",
    "builtin.react_config.structured_reasoning": "结构化推理",
    "builtin.react_config.reasoning_depth": "推理深度",
    "builtin.react_config.enable_reflection": "启用自我反思",
    "builtin.react_config.reflection_depth": "反思轮数",
    "builtin.react_config.reasoning_aware_tools": "按推理排序工具",
    "builtin.react_config.tool_prediction": "预测所需工具",
}

#: Replaces the library docs; "thinking" here always means the prompted flags.
FIELD_DESCRIPTIONS: dict[str, str] = {
    "builtin.tool_calling_mode": (
        "决定框架允许模型用几轮工具：agent 一轮一轮调下去，直到模型不再要工具；"
        "rag 只执行一轮工具，把结果当检索内容直接作答；none 不执行工具调用，只回答。"
        "它不改变模型能力，改变的是循环会不会继续——选错会让 Agent 看起来“不干活”。"
    ),
    "builtin.agent_thought_mode": (
        "提示词层的思考方式，与模型自身的推理能力无关，任何模型都能用。"
        "chat 直接回答；reasoning 先推理再执行；"
        "reasoning-required 每次调用工具前必须分析；reasoning-optional 允许但不强制。"
        "模型已开启原生思考时再用 reasoning 会造成双重消耗。"
    ),
    "builtin.react_config.structured_reasoning": (
        "让模型按 analyze → plan → execute → verify 四段标记分步推理。"
        "这也是提示词层的引导，不改变请求参数；"
        "模型本身已有原生思考时再开启，输出预算会被两套推理一起吃掉。"
    ),
    "builtin.react_config.reasoning_depth": (
        "结构化推理的步骤上限。越大越慢、越费 token；需要先开启结构化推理才生效。"
    ),
    "builtin.react_config.enable_reflection": (
        "执行完后再追加一轮自我检查，适合容易漏步骤的任务，代价是额外调用。"
    ),
    "builtin.react_config.reflection_depth": ("自我反思的轮数上限，仅在上面的开关开启时生效。"),
    "builtin.react_config.reasoning_aware_tools": (
        "按当前推理内容给工具排序，把更可能用到的排在前面。"
    ),
    "builtin.react_config.tool_prediction": (
        "推理阶段先预测将要调用的工具，需要先开启结构化推理才有效果。"
    ),
    "builtin.loop_reasoning_trigger": ("连续推理多少次后放弃。设得太小会让复杂任务提前收尾。"),
}

#: Fields that change agent behaviour; isolated because a wrong value can cripple a run.
UNSAFE_FIELDS: dict[str, str] = {
    "builtin.tool_calling_mode": (
        "直接决定工具循环跑几轮：选 rag / none 后模型仍然“会”工具，但框架不再执行，"
        "表现为 Agent 只说不做。"
    ),
    "builtin.agent_thought_mode": (
        "改变每次请求前强加的推理步骤。reasoning-required 会让每次工具调用都多一轮，"
        "reasoning 只在首轮生效；与原生思考叠加会重复消耗预算。"
    ),
    "builtin.loop_reasoning_trigger": (
        "连续推理达到这个次数就放弃循环，设得太小会让复杂任务提前收尾。"
    ),
    "builtin.react_config.structured_reasoning": (
        "改变提示词结构（analyze/plan/execute/verify），部分模型会因此偏离工具调用格式。"
    ),
    "builtin.react_config.reasoning_depth": "只在结构化推理开启时生效，影响每轮的推理展开层数。",
    "builtin.react_config.enable_reflection": (
        "每步结束后追加一轮自我检查，改变实际请求次数与最终回答的取舍。"
    ),
    "builtin.react_config.reflection_depth": "反思轮数，与上面的开关联动。",
    "builtin.react_config.reasoning_aware_tools": (
        "按推理内容重排工具顺序，会改变模型的选择倾向。"
    ),
    "builtin.react_config.tool_prediction": "预测工具列表会参与后续工具选择，弃用后行为不一定回到原样。",
    "function_config.agent_tool_call_limit": (
        "工具调用总轮数上限。调小会让多步骤任务在中途被硬性中断。"
    ),
    "function_config.agent_tool_refusal_rounds": (
        "达到上限后继续拒绝几轮，影响被中断后模型还能不能收尾。"
    ),
    "function_config.agent_middle_message": (
        "允许工具执行前先输出说明。关掉后过程信息会并入最终回答，时序改变。"
    ),
    "function_config.use_minimal_context": (
        "精简上下文会少发一部分历史，能省钱，但 Agent 可能因此忘记之前的约定。"
    ),
    "function_config.validate_tool_arguments": (
        "关掉后不再校验工具参数，非法参数会直接到达工具（包括 shell）。"
    ),
    "llm.context_strategy": (
        "上下文超限时的处理方式。选错会丢历史或直接报错，不同策略的取舍差很多。"
    ),
    "llm.compaction_trigger_ratio": "压缩触发的时机，设得过大可能已经超限才压缩。",
    "llm.slide_target_ratio": "滑动裁剪保留多少上下文，设得过小会让模型丢掉任务前提。",
    "llm.enable_overflow_recovery": "超限后的自动恢复，关闭后超限直接失败。",
    "llm.memory_length_limit": "历史消息条数上限，超出部分不进入请求，Agent 会“忘记”早期内容。",
}

#: Fields the UI may change; keys are dotted paths into ``AmritaConfig``.
EDITABLE_FIELDS: tuple[str, ...] = (
    "llm.max_tokens",
    "llm.llm_timeout",
    "llm.max_retries",
    "llm.max_fallbacks",
    "llm.context_strategy",
    "llm.compaction_trigger_ratio",
    "llm.slide_target_ratio",
    "llm.memory_length_limit",
    "llm.enable_overflow_recovery",
    "llm.enable_multi_modal",
    "llm.session_tokens_windows",
    "llm.compaction_max_tokens",
    "function_config.use_minimal_context",
    "function_config.agent_tool_call_limit",
    "function_config.agent_tool_refusal_rounds",
    "function_config.validate_tool_arguments",
    "function_config.agent_step_token_budget",
    "function_config.agent_middle_message",
    "builtin.tool_calling_mode",
    "builtin.agent_thought_mode",
    "builtin.loop_reasoning_trigger",
    "builtin.react_config.structured_reasoning",
    "builtin.react_config.reasoning_depth",
    "builtin.react_config.enable_reflection",
    "builtin.react_config.reflection_depth",
    "builtin.react_config.reasoning_aware_tools",
    "builtin.react_config.tool_prediction",
)


def flatten(data: dict[str, Any], parent: str = "", sep: str = ".") -> dict[str, Any]:
    """``{"a": {"b": 1}}`` → ``{"a.b": 1}``."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{parent}{sep}{key}" if parent else key
        if isinstance(value, dict):
            out.update(flatten(value, path, sep))
        else:
            out[path] = value
    return out


def _resolve_model(model: type[BaseModel], path: str) -> tuple[type[BaseModel], str] | None:
    """Walk dotted ``path`` down to the owning model and the leaf field name."""
    parts = path.split(".")
    current: Any = model
    for part in parts[:-1]:
        if not (isinstance(current, type) and hasattr(current, "model_fields")):
            return None
        info = current.model_fields.get(part)
        if info is None:
            return None
        annotation = info.annotation
        origin = get_origin(annotation)
        if origin is Union:
            inner = [arg for arg in get_args(annotation) if arg is not type(None)]
            annotation = inner[0] if len(inner) == 1 else None
        if not (isinstance(annotation, type) and issubclass(annotation, BaseModel)):
            return None
        current = annotation
    if not (isinstance(current, type) and hasattr(current, "model_fields")):
        return None
    leaf = parts[-1]
    return (current, leaf) if leaf in current.model_fields else None


def _short(description: str, limit: int = 160) -> str:
    """First sentence, trimmed: form help text, not a manual."""
    text = (description or "").strip().replace("\n", " ")
    for sep in (". ", "。"):
        head, found, _ = text.partition(sep)
        if found:
            text = head
            break
    return text if len(text) <= limit else text[: limit - 1] + "…"


def field_schema(path: str) -> dict[str, Any] | None:
    """Describe one editable field for the form renderer."""
    resolved = _resolve_model(AmritaConfig, path)
    if resolved is None:
        return None
    model, leaf = resolved
    info = model.model_fields[leaf]
    annotation = info.annotation

    kind = _kind_of(annotation)
    choices = _literal_values(annotation)
    unsafe_reason = UNSAFE_FIELDS.get(path)

    return {
        "path": path,
        "label": FIELD_LABELS.get(path, leaf),
        "type": kind,
        "choices": choices,
        "description": FIELD_DESCRIPTIONS.get(path) or _short(info.description or ""),
        "doc": (info.description or "").strip(),
        "default": _jsonable(info.default),
        "constraints": _constraints(info),
        # Isolated in the UI: a value here changes what the agent will do.
        "unsafe": unsafe_reason is not None,
        "unsafe_reason": unsafe_reason,
    }


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
    """Pull numeric bounds out of the field metadata so the form can enforce them."""
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


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _coerce(value: Any, kind: str) -> Any:
    """Convert a JSON form value to the type the model expects."""
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


def content_hash(flat: dict[str, Any]) -> str:
    payload = json.dumps(flat, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ConfigRejected(Exception):
    """Raised when the submitted values fail validation or the hash check."""

    def __init__(
        self,
        message: str,
        errors: list[dict[str, str]] | None = None,
        current_hash: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or []
        self.current_hash = current_hash


def _defaults() -> dict[str, Any]:
    """The library's defaults, from the live config if there is one.

    Falls back to a fresh model so read-only endpoints (and probes) still work
    before ``set_config`` has run at startup.
    """
    try:
        return get_config().model_dump(mode="json")
    except RuntimeError:
        return AmritaConfig().model_dump(mode="json")


async def load_raw() -> dict[str, Any]:
    """The persisted config snapshot, or the library defaults if never saved."""
    raw = await read_json(PATHS.core_config)
    if raw:
        return raw
    return _defaults()


def editable_values(raw: dict[str, Any]) -> dict[str, Any]:
    flat = flatten(raw)
    return {path: flat[path] for path in EDITABLE_FIELDS if path in flat}


async def read_config() -> dict[str, Any]:
    """Values, schema and drift for the config editor.

    Drift is reported rather than hidden, because the library's config model
    changes between versions and a silent rewrite is the worst outcome:

    * a field present in :data:`EDITABLE_FIELDS` but **absent from the file**
      gets ``source: "default"`` — the form shows the model default and says
      so, instead of pretending the user chose it;
    * a key **present in the file but gone from the model** lands in
      ``removed`` with its raw value, so the user can see what their old
      config contained and drop it deliberately.
    """
    stored = await read_json(PATHS.core_config)
    raw = stored or _defaults()
    values = editable_values(raw)
    model_flat = flatten(_defaults())
    file_flat = flatten(stored) if stored else {}

    fields: list[dict[str, Any]] = []
    for path in EDITABLE_FIELDS:
        schema = field_schema(path)
        if schema is None:
            continue
        # Presence in the file is the only honest signal, so defaults are not assumed.
        schema["source"] = "file" if path in file_flat else "default"
        fields.append(schema)

    removed = [
        {"path": path, "value": file_flat[path]}
        for path in sorted(file_flat)
        if path not in model_flat
    ]
    return {
        "values": values,
        "fields": fields,
        "hash": content_hash(values),
        # Keys the current schema no longer knows; shown read-only.
        "removed": removed,
        "missing": [field["path"] for field in fields if field["source"] == "default"],
    }


async def prune_unknown() -> dict[str, Any]:
    """Drop keys the current model does not define, keeping everything else.

    The result is written through the same validation as a normal save, so this
    cannot smuggle in a value the model would reject. A missing file is left
    missing — writing defaults out just because someone clicked "clean up" would
    freeze today's defaults into their config forever.
    """
    raw = await read_json(PATHS.core_config)
    if not raw:
        return await read_config()
    validated = AmritaConfig.model_validate(raw)
    await write_json(PATHS.core_config, validated.model_dump(mode="json"))
    logger.info("Pruned keys that the current AmritaConfig no longer defines")
    return await read_config()


async def save_config(submitted: dict[str, Any], provided_hash: str) -> dict[str, Any]:
    """Validate ``submitted`` values and persist them.

    Unsubmitted fields keep their stored value, so a partial form submission is
    safe. Validation runs against the complete merged config, which means a
    cross-field rule (e.g. slide target vs. compaction trigger) still fires.
    """
    current_raw = await load_raw()
    current_values = editable_values(current_raw)
    current_hash = content_hash(current_values)

    if provided_hash != current_hash:
        raise ConfigRejected("配置已被修改，请刷新后重试", current_hash=current_hash)

    unknown = [path for path in submitted if path not in EDITABLE_FIELDS]
    if unknown:
        raise ConfigRejected(
            "包含不可修改的字段",
            errors=[{"field": path, "message": "该字段不允许修改"} for path in unknown],
        )

    merged = json.loads(json.dumps(current_raw))
    for path, value in submitted.items():
        schema = field_schema(path)
        if schema is None:
            continue
        try:
            coerced = _coerce(value, schema["type"])
        except (TypeError, ValueError):
            raise ConfigRejected(
                "字段取值无效",
                errors=[{"field": path, "message": f"无法解析为 {schema['type']}"}],
            ) from None
        _set_path(merged, path, coerced)

    try:
        validated = AmritaConfig.model_validate(merged)
    except ValidationError as exc:
        errors = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ())),
                "message": str(err.get("msg", "")),
            }
            for err in exc.errors()
        ]
        logger.warning(f"Rejected core config: {errors}")
        raise ConfigRejected("配置校验失败", errors=errors) from None

    payload = validated.model_dump(mode="json")
    await write_json(PATHS.core_config, payload)

    new_values = editable_values(payload)
    return {"values": new_values, "hash": content_hash(new_values)}


async def apply_on_startup() -> None:
    """Install the persisted snapshot into the running library."""
    from amrita_core import set_config

    raw = await read_json(PATHS.core_config)
    if not raw:
        return
    try:
        set_config(AmritaConfig.model_validate(raw))
        logger.info("Loaded AmritaCore config from disk")
    except ValidationError as exc:
        logger.warning(f"Ignoring invalid core.json: {exc}")
