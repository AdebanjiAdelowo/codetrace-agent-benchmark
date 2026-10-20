"""Offline contract tests for the Anthropic adapter.

A real ``anthropic.Anthropic`` client is used with a mock HTTP transport, so the
SDK's own request serialization and response parsing are exercised. No network
access, no credentials, no cost. Skipped when the ``anthropic`` extra is absent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")

from codetrace.agent import Budget, run_agent  # noqa: E402
from codetrace.dataset import record_to_task  # noqa: E402
from codetrace.models.anthropic_client import DEFAULT_MODEL, AnthropicModel  # noqa: E402
from codetrace.tools import TOOL_SPECS  # noqa: E402
from codetrace.trace import EventKind, read_trace, validate_trace  # noqa: E402
from codetrace.workspace import Workspace, create_workspace, ensure_mirror  # noqa: E402


def _message(
    content: list[dict[str, Any]],
    stop_reason: str = "tool_use",
    usage: dict[str, int] | None = None,
    stop_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "msg_mock",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": stop_details,
        "usage": usage
        or {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }


class MockAPI:
    """Replays scripted responses and records every request body."""

    def __init__(self, responses: list[tuple[int, dict[str, Any]]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, request: Any) -> Any:
        assert request.url.path == "/v1/messages", request.url
        self.requests.append(json.loads(request.content))
        self.headers.append(dict(request.headers))
        status, body = self.responses.pop(0)
        return httpx2.Response(
            status, json=body, headers={"request-id": f"req_{len(self.requests)}"}
        )

    def model(self, max_retries: int = 0, **kw: Any) -> AnthropicModel:
        client = anthropic.Anthropic(
            api_key="test-key-not-real",
            base_url="https://api.anthropic.invalid",
            max_retries=max_retries,
            http_client=httpx2.Client(transport=httpx2.MockTransport(self)),
        )
        return AnthropicModel(client=client, **kw)


def test_request_shape_and_response_parsing() -> None:
    api = MockAPI(
        [
            (
                200,
                _message(
                    [
                        {"type": "thinking", "thinking": "", "signature": "sig-abc"},
                        {"type": "text", "text": "Looking."},
                        {"type": "tool_use", "id": "toolu_1", "name": "list_dir", "input": {}},
                    ],
                    usage={
                        "input_tokens": 7,
                        "output_tokens": 30,
                        "cache_read_input_tokens": 900,
                        "cache_creation_input_tokens": 50,
                    },
                ),
            )
        ]
    )
    m = api.model(effort="high", max_tokens=1234)
    resp = m.complete("SYS", [{"role": "user", "content": "hi"}], TOOL_SPECS)

    body = api.requests[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 1234
    assert body["system"] == "SYS"
    assert body["output_config"] == {"effort": "high"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert [t["name"] for t in body["tools"]] == [t["name"] for t in TOOL_SPECS]
    # Experimental-validity choices: no fallbacks, thinking left at the provider default.
    assert "fallbacks" not in body and "thinking" not in body and "temperature" not in body
    assert "anthropic-beta" not in api.headers[0]
    assert api.headers[0]["x-api-key"] == "test-key-not-real"

    assert resp.stop_reason == "tool_use" and resp.model == "claude-opus-5-5"
    assert [c.name for c in resp.tool_calls] == ["list_dir"]
    assert resp.content[0] == {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    u = resp.usage
    assert (
        u.input_tokens,
        u.output_tokens,
        u.cache_read_input_tokens,
        u.cache_creation_input_tokens,
    ) == (7, 30, 900, 50)
    assert u.method == "provider" and u.total_processed == 987
    assert resp.request_id == "req_1"
    assert (
        m.info.params["fallbacks"] == "disabled"
        and m.info.params["sdk_version"] == anthropic.__version__
    )


def test_dumped_blocks_are_valid_request_params() -> None:
    """Every field we echo back must exist on the SDK's corresponding *Param type."""
    blocks = [
        {"type": "text", "text": "t"},
        {"type": "thinking", "thinking": "x", "signature": "s"},
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "tool_use", "id": "toolu_9", "name": "submit", "input": {}},
    ]
    api = MockAPI([(200, _message(blocks))])
    resp = api.model().complete("S", [{"role": "user", "content": "x"}], TOOL_SPECS)
    params = {
        "text": anthropic.types.TextBlockParam,
        "thinking": anthropic.types.ThinkingBlockParam,
        "redacted_thinking": anthropic.types.RedactedThinkingBlockParam,
        "tool_use": anthropic.types.ToolUseBlockParam,
    }
    for block in resp.content:
        allowed = set(params[block["type"]].__annotations__)
        assert set(block) <= allowed, (block["type"], set(block) - allowed)


def test_refusal_is_reported() -> None:
    details = {"type": "refusal", "category": "cyber", "explanation": "declined"}
    api = MockAPI([(200, _message([], stop_reason="refusal", stop_details=details))])
    resp = api.model().complete("S", [{"role": "user", "content": "x"}], TOOL_SPECS)
    assert resp.stop_reason == "refusal"
    assert resp.stop_details is not None and resp.stop_details["category"] == "cyber"


def test_retry_on_overload_then_success() -> None:
    api = MockAPI(
        [
            (529, {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}),
            (200, _message([{"type": "tool_use", "id": "t", "name": "submit", "input": {}}])),
        ]
    )
    resp = api.model(max_retries=1).complete("S", [{"role": "user", "content": "x"}], TOOL_SPECS)
    assert len(api.requests) == 2 and resp.tool_calls[0].name == "submit"


def test_bad_request_raises_typed_error() -> None:
    api = MockAPI(
        [(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}})]
    )
    with pytest.raises(anthropic.BadRequestError):
        api.model().complete("S", [{"role": "user", "content": "x"}], TOOL_SPECS)


# ------------------------------------------------------------------ agent loop


@pytest.fixture
def ws(tmp_path: Path, upstream_repo: tuple[Path, str]) -> Workspace:
    repo, base = upstream_repo
    return create_workspace(ensure_mirror(str(repo), tmp_path / "mirror"), base, tmp_path / "ws")


def test_agent_loop_with_mocked_api(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    replace = {"path": "calc/core.py", "old_text": "return a - b", "new_text": "return a + b"}
    api = MockAPI(
        [
            (200, _message([
                {"type": "thinking", "thinking": "", "signature": "sig-1"},
                {"type": "tool_use", "id": "toolu_a", "name": "read_file", "input": {"path": "calc/core.py"}},
            ])),
            (200, _message([{"type": "tool_use", "id": "toolu_b", "name": "replace_in_file", "input": replace}])),
            (200, _message([{"type": "tool_use", "id": "toolu_c", "name": "submit", "input": {}}])),
        ]
    )  # fmt: skip
    task = record_to_task(task_record)
    res = run_agent(task.agent_view(), ws, api.model(), tmp_path / "out", experiment_id="mock")

    assert res.exit_status == "submitted" and res.steps == 3
    assert "+    return a + b" in Path(res.patch_path).read_text()
    assert res.usage["method"] == "provider" and res.usage["input_tokens"] == 300

    # History is append-only and echoes assistant blocks (incl. thinking signatures) unchanged.
    second = api.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-1"}
    result_block = second[2]["content"][0]
    assert result_block["type"] == "tool_result" and result_block["tool_use_id"] == "toolu_a"
    assert "return a - b" in result_block["content"]
    third = api.requests[2]["messages"]
    assert third[:3] == second  # earlier turns are never edited
    # The full system prompt and tools are identical on every request (cache prefix stable).
    assert len({json.dumps([r["system"], r["tools"]]) for r in api.requests}) == 1

    events = read_trace(Path(res.trace_path))
    assert validate_trace(events) == []
    manifest = events[0].payload["manifest"]
    assert manifest["model_provider"] == "anthropic" and manifest["model_name"] == "claude-opus-5-5"
    ids = [e.payload["request_id"] for e in events if e.kind is EventKind.MODEL_RESPONSE]
    assert ids == ["req_1", "req_2", "req_3"]
    # Hidden evaluator data never reached the (mock) API.
    sent = json.dumps(api.requests)
    assert "test_add_hidden_secret_9f3a" not in sent and "HINT_SECRET" not in sent


def test_agent_records_api_error(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    api = MockAPI(
        [(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}})]
    )
    res = run_agent(
        record_to_task(task_record).agent_view(), ws, api.model(), tmp_path / "out",
        experiment_id="mock", budget=Budget(max_steps=3),
    )  # fmt: skip
    assert res.exit_status == "error"
    events = read_trace(Path(res.trace_path))
    assert validate_trace(events) == []
    err = next(e for e in events if e.kind is EventKind.ERROR)
    assert err.payload["error_type"] == "BadRequestError"
