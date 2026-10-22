"""Offline HTTP response replay through the real Anthropic SDK.

Fixture format: a JSON array of {status: int, body: Messages-API-response} entries.
No real credentials or network transports are created. Usage is fixture data only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codetrace.config import ModelConfig
from codetrace.models.anthropic_client import AnthropicModel


def simulated_model(config: ModelConfig, fixture: Path) -> AnthropicModel:
    import anthropic
    import httpx2

    entries = json.loads(fixture.read_text())
    if not isinstance(entries, list) or not entries:
        raise ValueError("simulation fixture must contain a nonempty response list")

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path != "/v1/messages":
            raise ValueError("unexpected simulated API endpoint")
        if not entries:
            raise ValueError("simulation response fixture exhausted")
        entry: dict[str, Any] = entries.pop(0)
        return httpx2.Response(
            entry["status"],
            json=entry["body"],
            headers={"request-id": f"simulation-{len(entries)}"},
        )

    client = anthropic.Anthropic(
        api_key="offline-simulation-not-a-credential",
        base_url="https://simulation.invalid",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
    )
    return AnthropicModel(
        model=config.name or "", max_tokens=config.max_tokens, effort=config.effort, client=client
    )
