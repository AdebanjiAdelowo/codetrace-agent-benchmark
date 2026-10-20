"""Known-answer tests for context-loss metrics.

Each scenario drives the real agent loop on the toy repository with a scripted model
whose reported input size forces context resets at chosen steps. Expected metric values
were derived by hand from the scenario before running it.

Toy file calc/core.py (base commit):
  1 def add(a, b):    2 docstring    3 return a - b    4-5 blank    6 def greet(name):    7 return ...
Required units: E1 = lines 3-3 (edit site), E2 = lines 6-7 (support). H = line 1 (helpful).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from codetrace.agent import Budget, run_agent
from codetrace.dataset import record_to_task
from codetrace.evidence import EvidenceUnit, TaskAnnotation
from codetrace.metrics import aggregate, coverage, is_covered, run_metrics
from codetrace.models.base import ModelResponse
from codetrace.models.scripted import ScriptedModel
from codetrace.trace import CodeSpan, EventKind, read_trace
from codetrace.workspace import create_workspace, ensure_mirror

FIX = (
    "replace_in_file",
    {"path": "calc/core.py", "old_text": "return a - b", "new_text": "return a + b"},
)
BAD_FIX = ("replace_in_file", {"path": "calc/core.py", "old_text": "no such text", "new_text": "x"})
READ_E1 = ("read_file", {"path": "calc/core.py", "start_line": 1, "end_line": 3})
READ_E1_ONLY = ("read_file", {"path": "calc/core.py", "start_line": 3, "end_line": 3})
READ_E2 = ("read_file", {"path": "calc/core.py", "start_line": 6, "end_line": 7})
SUBMIT = ("submit", {})
BIG = 10_000  # reported input tokens above the context limit -> reset before next step


class ControlledModel(ScriptedModel):
    """Scripted tool calls plus a chosen provider-style input size per step."""

    def __init__(self, steps: Sequence[tuple[list[tuple[str, dict[str, Any]]], int]]) -> None:
        super().__init__([s for s, _ in steps])
        self.sizes = [n for _, n in steps]

    def complete(self, system: str, messages: Any, tools: Any) -> ModelResponse:
        idx = self._i
        resp = super().complete(system, messages, tools)
        resp.usage.input_tokens = self.sizes[idx] if idx < len(self.sizes) else 1
        return resp


def _ann(base: str, status: str = "adjudicated") -> TaskAnnotation:
    u = dict(path="calc/core.py", source="manual")
    return TaskAnnotation(
        instance_id="toy__calc-1", dataset_revision="r", base_commit=base,
        annotation_set="adjudicated", annotator="test", annotator_kind="human", status=status,  # type: ignore[arg-type]
        units=[
            EvidenceUnit(id="E1", start_line=3, end_line=3, kind="edit_site", criticality="required", **u),  # type: ignore[arg-type]
            EvidenceUnit(id="E2", start_line=6, end_line=7, kind="support", criticality="required", **u),  # type: ignore[arg-type]
            EvidenceUnit(id="H", start_line=1, end_line=1, kind="support", criticality="helpful", **u),  # type: ignore[arg-type]
        ],
    )  # fmt: skip


@pytest.fixture
def run_scenario(tmp_path: Path, upstream_repo: tuple[Path, str], task_record: dict[str, Any]):  # type: ignore[no-untyped-def]
    repo, base = upstream_repo
    mirror = ensure_mirror(str(repo), tmp_path / "mirror")
    counter = iter(range(1000))

    def _run(steps: Any, seed: int = 0, tau: Any = 0.5) -> Any:
        n = next(counter)
        ws = create_workspace(mirror, base, tmp_path / f"ws{n}")
        res = run_agent(
            record_to_task(task_record).agent_view(), ws, ControlledModel(steps), tmp_path / f"out{n}",
            experiment_id="m", seed=seed, budget=Budget(context_token_limit=1000),
        )  # fmt: skip
        events = read_trace(Path(res.trace_path))
        return run_metrics(events, _ann(base), tau), events

    return _run


def test_retained_without_reset(run_scenario: Any) -> None:
    m, _ = run_scenario([([READ_E1], 10), ([READ_E2], 10), ([FIX], 10), ([SUBMIT], 10)])
    assert (m.t_edit, m.discovery_recall, m.conditional_retention, m.context_loss) == (
        3,
        1.0,
        1.0,
        0,
    )
    assert m.lost_ids == [] and m.n_compactions_before_edit == 0 and m.undefined_reason is None


def test_all_lost_to_reset(run_scenario: Any) -> None:
    m, events = run_scenario([([READ_E1], 10), ([READ_E2], BIG), ([FIX], 10), ([SUBMIT], 10)])
    assert (m.t_edit, m.discovery_recall, m.conditional_retention, m.context_loss) == (
        3,
        1.0,
        0.0,
        1,
    )
    comp = next(e for e in events if e.kind is EventKind.CONTEXT_COMPACTION)
    assert m.loss_attribution == {"E1": comp.seq, "E2": comp.seq}
    assert m.n_compactions_before_edit == 1


def test_partial_reread_after_reset(run_scenario: Any) -> None:
    m, events = run_scenario(
        [([READ_E1], 10), ([READ_E2], BIG), ([READ_E1_ONLY], 10), ([FIX], 10), ([SUBMIT], 10)]
    )
    comp = next(e for e in events if e.kind is EventKind.CONTEXT_COMPACTION)
    assert (m.t_edit, m.discovery_recall, m.conditional_retention, m.context_loss) == (
        4,
        1.0,
        0.5,
        1,
    )
    assert (
        m.retained_ids == ["E1"] and m.lost_ids == ["E2"] and m.loss_attribution == {"E2": comp.seq}
    )


def test_same_turn_view_is_not_discovery(run_scenario: Any) -> None:
    # E1 is read in the same response as the edit: the model had not seen it yet.
    m, _ = run_scenario([([READ_E2], 10), ([READ_E1, FIX], 10), ([SUBMIT], 10)])
    assert (m.t_edit, m.discovery_recall, m.conditional_retention, m.context_loss) == (
        2,
        0.5,
        1.0,
        0,
    )
    assert m.discovered_ids == ["E2"]


def test_nothing_discovered_is_undefined(run_scenario: Any) -> None:
    m, _ = run_scenario([([FIX], 10), ([SUBMIT], 10)])
    assert (m.t_edit, m.discovery_recall) == (1, 0.0)
    assert m.conditional_retention is None and m.context_loss is None
    assert m.undefined_reason == "nothing_discovered"


def test_no_patch_is_undefined_but_dr_uses_whole_run(run_scenario: Any) -> None:
    m, _ = run_scenario([([READ_E1], 10), ([SUBMIT], 10)])
    assert m.t_edit is None and m.discovery_recall == 0.5 and m.discovered_ids == ["E1"]
    assert m.conditional_retention is None and m.undefined_reason == "no_patch"


def test_failed_edit_is_not_the_patch_moment(run_scenario: Any) -> None:
    m, _ = run_scenario(
        [([READ_E1], 10), ([BAD_FIX], 10), ([READ_E2], 10), ([FIX], 10), ([SUBMIT], 10)]
    )
    assert (m.t_edit, m.discovery_recall, m.conditional_retention) == (4, 1.0, 1.0)


def test_tau_sensitivity(run_scenario: Any) -> None:
    half_e2 = ("read_file", {"path": "calc/core.py", "start_line": 7, "end_line": 7})
    steps = [([READ_E1], 10), ([half_e2], 10), ([FIX], 10), ([SUBMIT], 10)]
    assert run_scenario(steps, tau="any")[0].discovery_recall == 1.0
    assert run_scenario(steps, tau=0.5)[0].discovery_recall == 1.0
    assert run_scenario(steps, tau=1.0)[0].discovery_recall == 0.5


def test_coverage_function() -> None:
    u = EvidenceUnit(id="u", path="a.py", start_line=10, end_line=13, kind="support",
                     criticality="required", source="manual")  # fmt: skip
    spans = [
        CodeSpan(path="a.py", start_line=12, end_line=20),
        CodeSpan(path="b.py", start_line=1, end_line=99),
    ]
    assert coverage(u, spans) == 0.5
    assert (
        is_covered(u, spans, 0.5) and not is_covered(u, spans, 1.0) and is_covered(u, spans, "any")
    )
    assert coverage(u, []) == 0.0


def test_annotation_mismatch_rejected(run_scenario: Any, upstream_repo: tuple[Path, str]) -> None:
    _, events = run_scenario([([SUBMIT], 10)])
    bad = _ann("0" * 40)
    with pytest.raises(ValueError, match="base_commit"):
        run_metrics(events, bad)


def test_aggregate_task_macro(run_scenario: Any) -> None:
    keep = [([READ_E1], 10), ([READ_E2], 10), ([FIX], 10), ([SUBMIT], 10)]
    lose = [([READ_E1], 10), ([READ_E2], BIG), ([FIX], 10), ([SUBMIT], 10)]
    nopatch = [([READ_E1], 10), ([SUBMIT], 10)]
    rows = [
        run_scenario(keep, seed=0)[0],
        run_scenario(lose, seed=1)[0],
        run_scenario(nopatch, seed=2)[0],
    ]
    summ = aggregate(rows)["A_baseline"]
    # One task, three seeds: DR mean (1 + 1 + 0.5)/3; CRR over defined runs (1 + 0)/2; CLR (0 + 1)/2.
    assert summ["discovery_recall_macro"] == pytest.approx(2.5 / 3)
    assert summ["conditional_retention_macro"] == pytest.approx(0.5)
    assert summ["context_loss_rate_macro"] == pytest.approx(0.5)
    assert summ["n_no_patch"] == 1 and summ["n_nothing_discovered"] == 0 and summ["n_runs"] == 3
