from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codetrace.agent import Budget, run_agent
from codetrace.dataset import record_to_task
from codetrace.models.scripted import ScriptedModel
from codetrace.verify import recover_run, verify_run
from codetrace.workspace import create_workspace, ensure_mirror


@pytest.fixture
def run_dir(tmp_path: Path, upstream_repo: tuple[Path, str], task_record: dict[str, Any]) -> Path:
    repo, base = upstream_repo
    ws = create_workspace(ensure_mirror(str(repo), tmp_path / "m"), base, tmp_path / "w")
    result = run_agent(
        record_to_task(task_record).agent_view(),
        ws,
        ScriptedModel(
            [
                [("read_file", {"path": "calc/core.py"})],
                [("submit", {})],
            ]
        ),
        tmp_path / "out",
        experiment_id="verify",
        budget=Budget(context_token_limit=1),
    )
    return Path(result.trace_path).parent


def test_consistent_run_verifies(run_dir: Path) -> None:
    assert verify_run(run_dir)["verified"]


@pytest.mark.parametrize("mutation", ["patch", "result", "usage", "snapshot", "compaction"])
def test_verifier_detects_artifact_tampering(run_dir: Path, mutation: str) -> None:
    trace = run_dir / "trace.jsonl"
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    if mutation == "patch":
        (run_dir / "patch.diff").write_text("tampered")
    elif mutation == "result":
        result = json.loads((run_dir / "result.json").read_text())
        result["steps"] = 999
        (run_dir / "result.json").write_text(json.dumps(result))
    else:
        if mutation == "usage":
            next(e for e in events if e["kind"] == "model_response")["payload"]["usage"][
                "input_tokens"
            ] += 1
        elif mutation == "snapshot":
            next(e for e in events if e["kind"] == "context_snapshot")["payload"]["spans"] = [
                {"path": "invented", "start_line": 1, "end_line": 5}
            ]
        else:
            next(e for e in events if e["kind"] == "context_compaction")["payload"][
                "removed_spans"
            ] = []
        trace.write_text("".join(json.dumps(e) + "\n" for e in events))
    assert not verify_run(run_dir)["verified"]


@pytest.mark.parametrize("torn", [False, True])
def test_aborted_prefix_recovery_preserves_original(run_dir: Path, torn: bool) -> None:
    trace = run_dir / "trace.jsonl"
    lines = trace.read_bytes().splitlines(keepends=True)
    prefix = b"".join(lines[:-2])  # lose final patch and run_end
    if torn:
        prefix += b'{"schema_version": "0.2.0", "kind":'
    trace.write_bytes(prefix)
    recovered = recover_run(run_dir)
    assert recovered["status"] == "aborted" and not recovered["resumable"]
    assert recovered["torn_final_line"] == torn and not recovered["billing_complete"]
    assert trace.read_bytes() == prefix
    assert recovered["usage"]["input_tokens"] > 0
    with pytest.raises(FileExistsError):
        recover_run(run_dir)


def test_recovery_rejects_corrupt_complete_line(run_dir: Path) -> None:
    trace = run_dir / "trace.jsonl"
    trace.write_bytes(trace.read_bytes().splitlines(keepends=True)[0] + b"broken\n")
    with pytest.raises(ValueError, match="corrupt complete"):
        recover_run(run_dir)
