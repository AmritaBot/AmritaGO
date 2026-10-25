"""Workflow registry.

An agent preset names a workflow in its front matter; an unknown name fails at
save time. ``react`` is the plain tool loop. ``react-step`` decomposes the task
into a DAG first, at the cost of one extra request, and emits ``step`` events.
Plugins may register more.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from amrita_core.agent.strategy import AgentStrategy
from amrita_core.builtins.agent import NoActionAgentStrategy
from amrita_core.chatmanager import _step_workflow_rendered

from app.runtime.tool_limit import LimitAwareStrategy


class WorkflowError(ValueError):
    """Raised when a workflow name cannot be resolved."""


#: How a workflow drives the model. Only ``mode`` is user-facing.
MODE_LABELS: dict[str, str] = {
    "chat": "直接回答",
    "react": "标准 ReAct",
    "step": "步进 ReAct",
}


@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    title: str
    description: str
    strategy: type[AgentStrategy]
    source: str = "builtin"
    #: ``chat`` | ``react`` | ``step``, shown so "ReAct" never hides the real loop.
    mode: str = "react"
    #: Pre-rendered pipeline; ``None`` means AmritaCore's default graph.
    pipeline: Any = None
    #: One sentence of advice; the UI shows it under the description.
    hint: str = ""

    @property
    def mode_label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode)

    def to_public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "mode": self.mode,
            "mode_label": self.mode_label,
            "hint": self.hint,
            "supports_steps": self.mode == "step",
        }


BUILTIN_WORKFLOWS: dict[str, WorkflowSpec] = {
    "chat": WorkflowSpec(
        name="chat",
        title="单轮对话",
        description="不进入工具循环，直接回答，适合纯对话与写作。",
        strategy=NoActionAgentStrategy,
        mode="chat",
        hint="不需要工具的任务走这里：省一轮请求，也不会调用任何工具。",
    ),
    "react": WorkflowSpec(
        name="react",
        title="标准 ReAct",
        description="多轮工具调用与推理，模型自己决定何时调用工具、何时收尾。",
        strategy=LimitAwareStrategy,
        mode="react",
        hint="绝大多数任务用这个：一轮一次请求，没有额外开销。",
    ),
    "react-step": WorkflowSpec(
        name="react-step",
        title="步进 ReAct",
        description="先把任务拆解成多步计划，再逐步执行；每一步有独立预算与进度事件。",
        strategy=LimitAwareStrategy,
        mode="step",
        pipeline=_step_workflow_rendered,
        hint="适合多步骤的复杂任务；开头会多一次「拆解」请求，简单问题不要用。",
    ),
}

DEFAULT_WORKFLOW = "react"


class WorkflowRegistry:
    """Built-in workflows plus the active plugin-provided set."""

    def __init__(self) -> None:
        self._extra: Mapping[str, WorkflowSpec] = MappingProxyType({})

    def install_extra(self, workflows: Mapping[str, WorkflowSpec]) -> None:
        """Replace the plugin-provided set with one immutable snapshot."""
        self._extra = MappingProxyType(dict(workflows))

    def list(self) -> list[WorkflowSpec]:
        merged = dict(BUILTIN_WORKFLOWS)
        merged.update(self._extra)
        return sorted(merged.values(), key=lambda item: item.name)

    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.list())

    def get(self, name: str | None) -> WorkflowSpec | None:
        if not name:
            return None
        return BUILTIN_WORKFLOWS.get(name) or self._extra.get(name)

    def require(self, name: str | None) -> WorkflowSpec:
        spec = self.get(name or DEFAULT_WORKFLOW)
        if spec is None:
            available = ", ".join(self.names()) or "(none)"
            raise WorkflowError(f"unknown workflow '{name}'; registered workflows: {available}")
        return spec


WORKFLOWS = WorkflowRegistry()


def to_public(spec: WorkflowSpec) -> dict[str, Any]:
    return spec.to_public()


__all__ = [
    "BUILTIN_WORKFLOWS",
    "DEFAULT_WORKFLOW",
    "MODE_LABELS",
    "WORKFLOWS",
    "WorkflowError",
    "WorkflowRegistry",
    "WorkflowSpec",
    "to_public",
]
