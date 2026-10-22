"""Offline failure-path tests through the SDK's HTTP serialization and retries."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("anthropic")
from codetrace.agent import Budget, run_agent
from codetrace.dataset import record_to_task
from codetrace.trace import EventKind, read_trace, validate_trace
from codetrace.verify import recover_run, verify_run
from codetrace.workspace import Workspace, create_workspace, ensure_mirror
from tests.test_anthropic_adapter import MockAPI, _message


@pytest.fixture
def ws(tmp_path: Path, upstream_repo: tuple[Path, str]) -> Workspace:
    repo, base = upstream_repo
    return create_workspace(ensure_mirror(str(repo), tmp_path / "mirror"), base, tmp_path / "ws")


def _call(name: str, id: str = "tool1", **input: Any) -> list[dict[str, Any]]:
    return [{"type": "tool_use", "id": id, "name": name, "input": input}]


def test_sockets_are_blocked() -> None:
    with pytest.raises(AssertionError, match="network access forbidden"):
        socket.create_connection(("example.com", 443))


def test_cost_limit_prevents_first_request(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    api = MockAPI([])
    r = run_agent(
        record_to_task(task_record).agent_view(),
        ws,
        api.model(),
        tmp_path,
        experiment_id="cap",
        budget=Budget(max_cost_usd=0.00001),
    )
    assert r.exit_status == "cost_budget" and r.steps == 0 and api.requests == []
    assert r.cost_usd == 0 and verify_run(Path(r.trace_path).parent)["verified"]


def test_cost_limit_accounts_for_cache_and_stops_before_next_call(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    usage = {
        "input_tokens": 10,
        "output_tokens": 100,
        "cache_read_input_tokens": 1000,
        "cache_creation_input_tokens": 100000,
        "cache_creation": {"ephemeral_5m_input_tokens": 60000, "ephemeral_1h_input_tokens": 40000},
    }
    api = MockAPI([(200, _message(_call("list_dir"), usage=usage))])
    r = run_agent(
        record_to_task(task_record).agent_view(),
        ws,
        api.model(),
        tmp_path,
        experiment_id="cap",
        budget=Budget(max_cost_usd=0.75),
    )
    assert r.exit_status == "cost_budget" and len(api.requests) == 1
    assert r.cost_usd == pytest.approx(0.62224)
    check = verify_run(Path(r.trace_path).parent)
    assert check["verified"], check
    manifest = read_trace(Path(r.trace_path))[0].payload["manifest"]
    assert all(
        manifest[k]
        for k in ("workspace_path", "pricing", "tool_specs_sha256", "task_template_sha256")
    )


def test_truncated_edit_and_submit_never_execute(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    edit = _call(
        "replace_in_file", path="calc/core.py", old_text="return a - b", new_text="CORRUPT"
    )
    api = MockAPI(
        [
            (200, _message(edit + _call("submit", id="submit1"), stop_reason="max_tokens")),
            (200, _message(_call("submit", id="submit2"))),
        ]
    )
    r = run_agent(
        record_to_task(task_record).agent_view(), ws, api.model(), tmp_path, experiment_id="trunc"
    )
    assert r.steps == 2 and r.exit_status == "submitted" and r.patch_bytes == 0
    blocks = api.requests[1]["messages"][-1]["content"]
    assert len(blocks) == 2 and all(b["is_error"] and "truncated" in b["content"] for b in blocks)
    assert verify_run(Path(r.trace_path).parent)["verified"]


def test_context_window_stop_is_terminal(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    api = MockAPI([(200, _message(_call("submit"), stop_reason="model_context_window_exceeded"))])
    r = run_agent(
        record_to_task(task_record).agent_view(), ws, api.model(), tmp_path, experiment_id="window"
    )
    assert r.exit_status == "model_context_window_exceeded" and r.patch_bytes == 0
    assert not any(e.kind is EventKind.TOOL_CALL for e in read_trace(Path(r.trace_path)))


def test_reset_sends_fresh_conversation_without_orphan_tools(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    api = MockAPI(
        [
            (200, _message(_call("read_file", path="calc/core.py"))),
            (200, _message(_call("submit", id="last"))),
        ]
    )
    r = run_agent(
        record_to_task(task_record).agent_view(),
        ws,
        api.model(),
        tmp_path,
        experiment_id="reset",
        budget=Budget(context_token_limit=10),
    )
    assert len(api.requests[1]["messages"]) == 1
    assert api.requests[1]["messages"][0]["role"] == "user"
    assert "Context reset" in api.requests[1]["messages"][0]["content"]
    assert verify_run(Path(r.trace_path).parent)["verified"]


@pytest.mark.parametrize("status", [429, 500])
def test_retry_then_success(status: int) -> None:
    api = MockAPI(
        [
            (status, {"type": "error", "error": {"type": "rate_limit_error", "message": "retry"}}),
            (200, _message(_call("submit"))),
        ]
    )
    model = api.model(max_retries=1)
    model.complete("sys", [{"role": "user", "content": "hi"}], [])
    assert len(api.requests) == 2


def test_connection_retry_then_success() -> None:
    import anthropic
    import httpx2

    from codetrace.models.anthropic_client import AnthropicModel

    attempts = []

    def respond(request: Any) -> Any:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx2.ConnectError("offline connection failure", request=request)
        return httpx2.Response(200, json=_message(_call("submit")))

    client = anthropic.Anthropic(
        api_key="mock",
        max_retries=1,
        http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
    )
    AnthropicModel(client=client).complete("s", [{"role": "user", "content": "hi"}], [])
    assert len(attempts) == 2


def test_retries_exhausted_produces_finalized_error(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    error = {"type": "error", "error": {"type": "rate_limit_error", "message": "busy"}}
    api = MockAPI([(429, error), (429, error)])
    r = run_agent(
        record_to_task(task_record).agent_view(),
        ws,
        api.model(max_retries=1),
        tmp_path,
        experiment_id="error",
    )
    assert r.exit_status == "error" and len(api.requests) == 2
    assert not r.billing_complete and validate_trace(read_trace(Path(r.trace_path))) == []


def test_interrupt_saves_partial_patch_and_reraises(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    api = MockAPI(
        [
            (
                200,
                _message(
                    _call(
                        "replace_in_file",
                        path="calc/core.py",
                        old_text="return a - b",
                        new_text="return a + b",
                    )
                ),
            )
        ]
    )
    model = api.model()
    original = model.complete
    count = 0

    def interrupt(*args: Any, **kwargs: Any) -> Any:
        nonlocal count
        count += 1
        if count > 1:
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    model.complete = interrupt  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        run_agent(
            record_to_task(task_record).agent_view(),
            ws,
            model,
            tmp_path,
            experiment_id="interrupt",
            run_id="interrupted",
        )
    path = tmp_path / "interrupted"
    result = json.loads((path / "result.json").read_text())
    assert result["exit_status"] == "interrupted" and not result["billing_complete"]
    assert "+    return a + b" in (path / "patch.diff").read_text()
    assert verify_run(path)["verified"]
    with pytest.raises(ValueError, match="already finalized"):
        recover_run(path)
