from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from codetrace.dataset import (
    AgentTaskView,
    SelectionCriteria,
    export_for_evaluator,
    load_tasks,
    patch_stats,
    record_to_task,
    select_tasks,
)

from .conftest import REFERENCE_PATCH


def test_record_parsing(task_record: dict[str, Any]) -> None:
    t = record_to_task(task_record)
    assert t.fail_to_pass == ("tests/test_core.py::test_add",)
    assert t.pass_to_pass == ("tests/test_core.py::test_greet",)


def test_agent_view_hides_evaluator_fields(task_record: dict[str, Any]) -> None:
    view = record_to_task(task_record).agent_view()
    fields = {f.name for f in dataclasses.fields(AgentTaskView)}
    assert fields == {"instance_id", "repo", "base_commit", "problem_statement"}
    rendered = repr(view)
    for secret in (task_record["patch"], task_record["test_patch"], task_record["hints_text"]):
        assert secret not in rendered


def test_load_jsonl(dataset_jsonl: Path) -> None:
    tasks = load_tasks(dataset_jsonl)
    assert list(tasks) == ["toy__calc-1"]


def test_patch_stats() -> None:
    s = patch_stats(REFERENCE_PATCH)
    assert (s.files_changed, s.hunks, s.lines_added, s.lines_removed) == (1, 1, 1, 1)


def _rec(iid: str, repo: str, n_lines: int, f2p: str = '["t"]') -> dict[str, Any]:
    body = "".join(f"+x{i}\n" for i in range(n_lines))
    return {
        "instance_id": iid, "repo": repo, "base_commit": "c", "problem_statement": "p",
        "patch": f"diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n{body}",
        "test_patch": "", "FAIL_TO_PASS": f2p, "PASS_TO_PASS": "[]",
    }  # fmt: skip


def test_selection_is_deterministic_and_filtered() -> None:
    recs = [
        _rec("a__a-2", "a/a", 5), _rec("a__a-1", "a/a", 5), _rec("a__a-3", "a/a", 2, f2p="[]"),
        _rec("b__b-1", "b/b", 50), _rec("b__b-2", "b/b", 3), _rec("c__c-1", "c/c", 1),
    ]  # fmt: skip
    tasks = {r["instance_id"]: record_to_task(r) for r in recs}
    crit = SelectionCriteria(repos=("a/a", "b/b", "c/c"), n_tasks=2)
    got = [t.instance_id for t, _ in select_tasks(tasks, crit)]
    # a__a-3 excluded (no FAIL_TO_PASS); ties broken by instance_id; b__b-1 too large.
    assert got == ["a__a-1", "b__b-2"]
    assert got == [t.instance_id for t, _ in select_tasks(dict(reversed(tasks.items())), crit)]


def test_export_for_evaluator(tmp_path: Path, dataset_jsonl: Path) -> None:
    out = tmp_path / "eval.jsonl"
    assert export_for_evaluator(dataset_jsonl, ["toy__calc-1"], out) == 1
    assert "test_add_hidden_secret_9f3a" in out.read_text()  # full record, evaluator side
    with pytest.raises(ValueError, match="missing"):
        export_for_evaluator(dataset_jsonl, ["nope"], out)


def test_list_valued_test_fields(task_record: dict[str, Any]) -> None:
    rec = {**task_record, "FAIL_TO_PASS": ["a", "b"], "PASS_TO_PASS": []}
    t = record_to_task(rec)
    assert t.fail_to_pass == ("a", "b") and t.pass_to_pass == ()
