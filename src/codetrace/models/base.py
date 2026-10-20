"""Provider-neutral model interface.

The canonical conversation format is the Anthropic Messages format (a list of
``{"role", "content"}`` dicts with ``text`` / ``tool_use`` / ``tool_result``
blocks), because it is explicit about tool calls and is what the live adapter
sends. Other providers would translate at their adapter boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from codetrace.trace import TokenUsage


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ModelResponse:
    # Content blocks exactly as they must be appended back to the history
    # (includes thinking blocks for providers that require them unchanged).
    content: list[dict[str, Any]]
    stop_reason: str
    usage: TokenUsage
    model: str
    latency_s: float
    stop_details: dict[str, Any] | None = None
    request_id: str | None = None

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [
            ToolCall(id=b["id"], name=b["name"], input=dict(b.get("input") or {}))
            for b in self.content
            if b.get("type") == "tool_use"
        ]

    @property
    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.content if b.get("type") == "text")


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    name: str
    params: dict[str, Any] = field(default_factory=dict)


class ModelClient(Protocol):
    info: ModelInfo

    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelResponse: ...
