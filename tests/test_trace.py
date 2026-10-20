from __future__ import annotations

import json
from pathlib import Path

import pytest

from codetrace.trace import (
    EventKind,
    TokenUsage,
    TraceWriter,
    export_json_schema,
    read_trace,
    validate_trace,
)


def _manifest() -> dict[str, object]:
    return {
        "run_id": "r1", "experiment_id": "e", "agent_config": "A_baseline", "seed": 0,
        "task_instance_id": "t", "dataset_name": "d", "dataset_revision": "rev", "repo": "o/r",
        "base_commit": "abc", "model_provider": "scripted", "model_name": "m", "model_params": {},
        "system_prompt_sha256": "x", "system_prompt": "s", "tool_names": ["submit"], "budget": {},
        "environment": {}, "codetrace_git_commit": None, "started_at": "now",
    }  # fmt: skip


def test_roundtrip_and_valid(tmp_path: Path) -> None:
    p = tmp_path / "trace.jsonl"
    with TraceWriter(p, "r1") as tw:
        tw.emit(EventKind.RUN_START, 0, manifest=_manifest())
        tw.emit(EventKind.TOOL_CALL, 1, tool_use_id="a", name="submit", input={})
        tw.emit(EventKind.TOOL_RESULT, 1, tool_use_id="a", output="submitted")
        tw.emit(EventKind.RUN_END, 1, result={})
    events = read_trace(p)
    assert [e.seq for e in events] == [0, 1, 2, 3]
    assert validate_trace(events) == []
    assert all(json.loads(line)["schema_version"] for line in p.read_text().splitlines())


def test_detects_aborted_and_unmatched(tmp_path: Path) -> None:
    p = tmp_path / "trace.jsonl"
    with TraceWriter(p, "r1") as tw:
        tw.emit(EventKind.RUN_START, 0, manifest=_manifest())
        tw.emit(EventKind.TOOL_CALL, 1, tool_use_id="a", name="read_file", input={})
    problems = validate_trace(read_trace(p))
    assert any("run_end" in x for x in problems)
    assert any("unmatched" in x for x in problems)


def test_invalid_manifest_detected(tmp_path: Path) -> None:
    p = tmp_path / "trace.jsonl"
    with TraceWriter(p, "r1") as tw:
        tw.emit(EventKind.RUN_START, 0, manifest={"run_id": "r1"})
        tw.emit(EventKind.RUN_END, 0, result={})
    assert any("manifest invalid" in x for x in validate_trace(read_trace(p)))


def test_refuses_overwrite(tmp_path: Path) -> None:
    p = tmp_path / "trace.jsonl"
    TraceWriter(p, "r").close()
    with pytest.raises(FileExistsError):
        TraceWriter(p, "r")


def test_malformed_line_raises(tmp_path: Path) -> None:
    p = tmp_path / "trace.jsonl"
    p.write_text('{"not": "an event"}\n')
    with pytest.raises(ValueError, match=":1:"):
        read_trace(p)


def test_token_usage_add_tracks_method() -> None:
    a = TokenUsage(input_tokens=1, output_tokens=2)
    b = TokenUsage(input_tokens=3, output_tokens=4, method="approx_chars_div_4")
    s = a + b
    assert (s.input_tokens, s.output_tokens, s.method) == (4, 6, "mixed")


def test_schema_export(tmp_path: Path) -> None:
    p = tmp_path / "schema.json"
    export_json_schema(p)
    schema = json.loads(p.read_text())
    assert set(schema) == {"trace_event", "run_manifest", "token_usage", "code_span"}
    kinds = schema["trace_event"]["$defs"]["EventKind"]["enum"]
    assert "context_compaction" in kinds and "run_end" in kinds


def test_total_processed_counts_cached_tokens() -> None:
    u = TokenUsage(
        input_tokens=10,
        output_tokens=5,
        cache_read_input_tokens=100,
        cache_creation_input_tokens=20,
    )
    assert u.total_processed == 135
