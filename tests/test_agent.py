"""Integration tests: real workspace + real tools + trace writer, deterministic models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codetrace.agent import Budget, run_agent
from codetrace.dataset import record_to_task
from codetrace.models.base import ModelInfo, ModelResponse
from codetrace.models.scripted import HeuristicExplorer, ScriptedModel
from codetrace.trace import EventKind, TokenUsage, read_trace, validate_trace
from codetrace.workspace import Workspace, create_workspace, ensure_mirror

FIX_STEPS = [
    [("search_code", {"pattern": "def add"})],
    [("read_file", {"path": "calc/core.py", "start_line": 1, "end_line": 3})],
    [("replace_in_file", {"path": "calc/core.py", "old_text": "return a - b", "new_text": "return a + b"})],
    [("submit", {})],
]  # fmt: skip


@pytest.fixture
def ws(tmp_path: Path, upstream_repo: tuple[Path, str]) -> Workspace:
    repo, base = upstream_repo
    return create_workspace(ensure_mirror(str(repo), tmp_path / "mirror"), base, tmp_path / "ws")


class SpyModel(ScriptedModel):
    """Records every request so tests can inspect exactly what the model saw."""

    def __init__(self, steps: Any) -> None:
        super().__init__(steps)
        self.requests: list[str] = []

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        self.requests.append(json.dumps([system, messages, tools]))
        return super().complete(system, messages, tools)


def test_end_to_end_fix(tmp_path: Path, ws: Workspace, task_record: dict[str, Any]) -> None:
    task = record_to_task(task_record)
    model = SpyModel(FIX_STEPS)
    res = run_agent(task.agent_view(), ws, model, tmp_path / "out", experiment_id="t")

    assert res.exit_status == "submitted" and res.steps == 4
    assert res.files_modified == ["calc/core.py"]
    patch = Path(res.patch_path).read_text()
    assert "-    return a - b" in patch and "+    return a + b" in patch
    assert res.usage["method"] == "approx_chars_div_4"

    events = read_trace(Path(res.trace_path))
    assert validate_trace(events) == []
    kinds = [e.kind for e in events]
    assert kinds[0] is EventKind.RUN_START and kinds[-1] is EventKind.RUN_END
    viewed = [e.payload["span"] for e in events if e.kind is EventKind.CODE_LOCATION_VIEWED]
    assert {"path": "calc/core.py", "start_line": 1, "end_line": 3} in viewed
    pre_edit = [e for e in events if e.kind is EventKind.CONTEXT_SNAPSHOT
                and e.payload["reason"] == "before_replace_in_file"]  # fmt: skip
    assert (
        pre_edit
        and {"path": "calc/core.py", "start_line": 1, "end_line": 3} in pre_edit[0].payload["spans"]
    )
    patch_ev = next(e for e in events if e.kind is EventKind.PATCH_GENERATED)
    assert patch_ev.payload["patch"] == patch
    manifest = events[0].payload["manifest"]
    assert manifest["base_commit"] == task.base_commit and manifest["model_provider"] == "scripted"


def test_hidden_information_never_reaches_model(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    task = record_to_task(task_record)
    model = SpyModel(FIX_STEPS)
    res = run_agent(task.agent_view(), ws, model, tmp_path / "out", experiment_id="t")
    seen = "\n".join(model.requests)
    assert task.problem_statement in seen
    for secret in (
        "test_add_hidden_secret_9f3a",
        "HINT_SECRET",
        "FAIL_TO_PASS",
        "tests/test_core.py",
    ):
        assert secret not in seen
    # Nor in anything the run wrote except the agent's own patch.
    trace_text = Path(res.trace_path).read_text()
    assert "test_add_hidden_secret_9f3a" not in trace_text and "HINT_SECRET" not in trace_text


def test_rejects_full_task_record(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    with pytest.raises(TypeError):
        run_agent(record_to_task(task_record), ws, ScriptedModel([]), tmp_path, experiment_id="t")  # type: ignore[arg-type]


def test_max_steps_still_writes_patch(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    steps = [FIX_STEPS[2]] + [[("list_dir", {})]] * 10
    res = run_agent(
        record_to_task(task_record).agent_view(), ws, ScriptedModel(steps), tmp_path / "o",
        experiment_id="t", budget=Budget(max_steps=3),
    )  # fmt: skip
    assert res.exit_status == "max_steps" and res.steps == 3
    assert "+    return a + b" in Path(res.patch_path).read_text()
    events = read_trace(Path(res.trace_path))
    assert validate_trace(events) == []
    assert any(e.kind is EventKind.BUDGET_EXHAUSTED for e in events)


def test_context_reset_is_logged(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    model = SpyModel(FIX_STEPS)
    res = run_agent(
        record_to_task(task_record).agent_view(), ws, model, tmp_path / "o",
        experiment_id="t", budget=Budget(context_token_limit=10),  # every step triggers a reset
    )  # fmt: skip
    assert res.exit_status == "submitted" and res.n_compactions == 3
    events = read_trace(Path(res.trace_path))
    comps = [e for e in events if e.kind is EventKind.CONTEXT_COMPACTION]
    # The read span from step 2 is removed by the reset before step 3.
    assert {"path": "calc/core.py", "start_line": 1, "end_line": 3} in comps[1].payload[
        "removed_spans"
    ]
    pre_edit = next(e for e in events if e.kind is EventKind.CONTEXT_SNAPSHOT
                    and e.payload["reason"] == "before_replace_in_file")  # fmt: skip
    assert pre_edit.payload["spans"] == [] and pre_edit.payload["segment"] == 2
    # After a reset the model sees a single fresh user message (append-only, no edits).
    last_req = json.loads(model.requests[-1])
    assert len(last_req[1]) == 1 and "Context reset" in last_req[1][0]["content"]
    assert "calc/core.py" in last_req[1][0]["content"]  # already-modified file is restated


class _FakeModel:
    def __init__(self, responses: list[ModelResponse | Exception]) -> None:
        self.info = ModelInfo(provider="fake", name="fake")
        self.responses = responses

    def complete(self, system: str, messages: Any, tools: Any) -> ModelResponse:
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(content: list[dict[str, Any]], stop: str = "end_turn") -> ModelResponse:
    return ModelResponse(
        content=content,
        stop_reason=stop,
        usage=TokenUsage(input_tokens=5),
        model="fake",
        latency_s=0,
    )


@pytest.mark.parametrize(
    ("responses", "status"),
    [
        ([_resp([], stop="refusal")], "refusal"),
        ([_resp([{"type": "text", "text": "hm"}])] * 3, "no_tool_calls"),
        ([RuntimeError("boom")], "error"),
    ],
)
def test_terminal_statuses(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any], responses: list[Any], status: str
) -> None:
    res = run_agent(
        record_to_task(task_record).agent_view(), ws, _FakeModel(list(responses)), tmp_path / "o",
        experiment_id="t",
    )  # fmt: skip
    assert res.exit_status == status
    assert validate_trace(read_trace(Path(res.trace_path))) == []


def test_heuristic_explorer_runs(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    task = record_to_task(task_record)
    model = HeuristicExplorer(task.problem_statement)
    assert model.idents == ["add"]
    res = run_agent(task.agent_view(), ws, model, tmp_path / "o", experiment_id="t")
    assert res.exit_status == "submitted" and res.patch_bytes == 0
    events = read_trace(Path(res.trace_path))
    reads = [
        e for e in events if e.kind is EventKind.TOOL_CALL and e.payload["name"] == "read_file"
    ]
    assert reads and reads[0].payload["input"]["path"] == "calc/core.py"


def test_token_budget_counts_cache_reads(
    tmp_path: Path, ws: Workspace, task_record: dict[str, Any]
) -> None:
    cached = ModelResponse(
        content=[{"type": "tool_use", "id": "t1", "name": "list_dir", "input": {}}],
        stop_reason="tool_use",
        usage=TokenUsage(input_tokens=1, output_tokens=1, cache_read_input_tokens=1000),
        model="fake", latency_s=0,
    )  # fmt: skip
    res = run_agent(
        record_to_task(task_record).agent_view(), ws, _FakeModel([cached, cached, cached]),
        tmp_path / "o", experiment_id="t", budget=Budget(max_total_tokens=1500, context_token_limit=10**9),
    )  # fmt: skip
    assert res.exit_status == "token_budget" and res.steps == 2
