"""Anthropic Messages API adapter (manual tool-use loop; the loop lives in agent.py).

Not exercised by tests or the smoke run: it makes paid API calls. Requires the
``anthropic`` extra and a credential resolved by the SDK (``ANTHROPIC_API_KEY``
or an ``ant auth login`` profile).

Design notes for experimental validity:
- Server-side refusal fallbacks are deliberately NOT enabled: a fallback would
  silently change the model mid-run and confound configuration comparisons. A
  ``refusal`` stop reason is recorded and ends the run as a failure category.
- Effort is set explicitly (the model default may change between releases).
- History is append-only; context compaction is implemented in agent.py as a
  logged conversation reset, never as in-place edits of earlier turns.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

from codetrace.models.base import ModelInfo, ModelResponse
from codetrace.trace import TokenUsage

if TYPE_CHECKING:
    import anthropic
    from anthropic.types import MessageParam, OutputConfigParam, ToolParam

DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicModel:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 16000,
        effort: str = "high",
        timeout_s: float = 600.0,
        max_retries: int = 2,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        import anthropic

        # `client` is injectable so tests can use a mock HTTP transport.
        self._client = client or anthropic.Anthropic(timeout=timeout_s, max_retries=max_retries)
        if client is not None:
            max_retries = client.max_retries
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.info = ModelInfo(
            provider="anthropic",
            name=model,
            params={
                "max_tokens": max_tokens,
                "output_config.effort": effort,
                "thinking": "provider default (adaptive)",
                "fallbacks": "disabled",
                "prompt_caching": "automatic (top-level cache_control ephemeral)",
                "sdk_version": anthropic.__version__,
                "max_retries": max_retries,
            },
        )

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        t0 = time.monotonic()
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            # Canonical history is already in Messages format; cast at the SDK boundary.
            tools=cast("list[ToolParam]", tools),
            messages=cast("list[MessageParam]", messages),
            output_config=cast("OutputConfigParam", {"effort": self.effort}),
            # Top-level automatic prompt caching: the history is append-only within a
            # segment, so each request's prefix is reusable. Cost only; no behavior change.
            cache_control={"type": "ephemeral"},
        )
        latency = time.monotonic() - t0
        u = resp.usage
        usage = TokenUsage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            method="provider",
        )
        stop_details = getattr(resp, "stop_details", None)
        return ModelResponse(
            content=[b.model_dump(exclude_none=True) for b in resp.content],
            stop_reason=resp.stop_reason or "unknown",
            usage=usage,
            model=resp.model,
            latency_s=latency,
            stop_details=stop_details.model_dump() if stop_details is not None else None,
            request_id=getattr(resp, "_request_id", None),
        )
