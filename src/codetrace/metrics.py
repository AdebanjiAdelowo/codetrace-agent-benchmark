"""Context-loss metrics computed from traces and evidence annotations (PROTOCOL.md §3-4).

Patch-generation moment
-----------------------
``t_edit`` is the agent step whose model response contained the first *successful*
edit (``replace_in_file`` / ``create_file`` without error). The model generated that edit
having seen exactly the context of the request at step ``t_edit``, so:

- discovered set ``D``: required units covered by spans viewed at steps ``< t_edit``
  (tool results from the same response as the edit had not been seen by the model);
- accessible set ``A``: spans in the ``context_snapshot`` with
  ``reason="before_model_request"`` at step ``t_edit``.

Runs without a successful edit have no ``t_edit``: DR uses every view in the run, and CRR
and L are undefined (reason ``no_patch``). If ``D`` is empty, CRR and L are undefined
(reason ``nothing_discovered``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any, Literal

from codetrace.evidence import EvidenceUnit, TaskAnnotation
from codetrace.trace import CodeSpan, EventKind, TraceEvent

Tau = float | Literal["any"]
EDIT_TOOLS = ("replace_in_file", "create_file")


def coverage(unit: EvidenceUnit, spans: Iterable[CodeSpan]) -> float:
    """Fraction of the unit's lines covered by spans on the same path."""
    covered: set[int] = set()
    lines = set(unit.lines())
    for s in spans:
        if s.path == unit.path:
            covered |= lines & set(range(s.start_line, s.end_line + 1))
    return len(covered) / len(lines)


def is_covered(unit: EvidenceUnit, spans: Iterable[CodeSpan], tau: Tau) -> bool:
    c = coverage(unit, spans)
    return c > 0 if tau == "any" else c >= tau


@dataclass
class RunMetrics:
    run_id: str
    instance_id: str
    agent_config: str
    seed: int
    exit_status: str
    tau: Tau
    annotation_status: str
    n_required: int
    t_edit: int | None
    discovered_ids: list[str]
    discovery_recall: float | None
    retained_ids: list[str] = field(default_factory=list)
    lost_ids: list[str] = field(default_factory=list)
    conditional_retention: float | None = None
    context_loss: int | None = None  # run-level indicator L_r
    undefined_reason: str | None = None
    loss_attribution: dict[str, int | None] = field(default_factory=dict)  # unit -> compaction seq
    n_compactions_before_edit: int = 0


def _spans(events: Iterable[TraceEvent]) -> list[tuple[int, int, CodeSpan]]:
    """(seq, step, span) for every viewed code location."""
    return [
        (e.seq, e.step, CodeSpan.model_validate(e.payload["span"]))
        for e in events
        if e.kind is EventKind.CODE_LOCATION_VIEWED
    ]


def first_successful_edit_step(events: list[TraceEvent]) -> int | None:
    for e in events:
        if (
            e.kind is EventKind.TOOL_RESULT
            and e.payload.get("name") in EDIT_TOOLS
            and not e.payload.get("is_error")
        ):
            return e.step
    return None


def accessible_spans_at(events: list[TraceEvent], step: int) -> list[CodeSpan]:
    for e in events:
        if (
            e.kind is EventKind.CONTEXT_SNAPSHOT
            and e.step == step
            and e.payload.get("reason") == "before_model_request"
        ):
            return [CodeSpan.model_validate(s) for s in e.payload["spans"]]
    raise ValueError(f"no before_model_request snapshot at step {step}")


def run_metrics(events: list[TraceEvent], ann: TaskAnnotation, tau: Tau = 0.5) -> RunMetrics:
    manifest = events[0].payload["manifest"]
    if manifest["task_instance_id"] != ann.instance_id:
        raise ValueError("annotation is for a different task")
    if manifest["base_commit"] != ann.base_commit:
        raise ValueError("annotation base_commit does not match the run")
    end = events[-1]
    if end.kind is not EventKind.RUN_END:
        raise ValueError("aborted run (no run_end); report separately")
    req = ann.required
    if not req:
        raise ValueError(f"{ann.instance_id}: no required units (annotation defect)")
    viewed = _spans(events)
    t_edit = first_successful_edit_step(events)
    before = [s for _, step, s in viewed if t_edit is None or step < t_edit]
    discovered = [u for u in req if is_covered(u, before, tau)]
    m = RunMetrics(
        run_id=manifest["run_id"],
        instance_id=ann.instance_id,
        agent_config=manifest["agent_config"],
        seed=manifest["seed"],
        exit_status=end.payload["result"]["exit_status"],
        tau=tau,
        annotation_status=ann.status,
        n_required=len(req),
        t_edit=t_edit,
        discovered_ids=[u.id for u in discovered],
        discovery_recall=len(discovered) / len(req),
    )
    if t_edit is None:
        m.undefined_reason = "no_patch"
        return m
    compactions = [e for e in events if e.kind is EventKind.CONTEXT_COMPACTION and e.step < t_edit]
    m.n_compactions_before_edit = len(compactions)
    if not discovered:
        m.undefined_reason = "nothing_discovered"
        return m
    accessible = accessible_spans_at(events, t_edit)
    for u in discovered:
        if is_covered(u, accessible, tau):
            m.retained_ids.append(u.id)
        else:
            m.lost_ids.append(u.id)
            # Attribute to the latest compaction before t_edit that removed covering spans.
            culprit = None
            for c in compactions:
                removed = [CodeSpan.model_validate(s) for s in c.payload["removed_spans"]]
                if is_covered(u, removed, tau):
                    culprit = c.seq
            m.loss_attribution[u.id] = culprit
    m.conditional_retention = len(m.retained_ids) / len(discovered)
    m.context_loss = int(bool(m.lost_ids))
    return m


def _macro(rows: list[RunMetrics], attr: str) -> tuple[float | None, int]:
    """Task-macro mean: average over seeds within a task, then over tasks. Undefined
    values are dropped at run level; returns (mean, number of tasks contributing)."""
    by_task: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        v = getattr(r, attr)
        if v is not None:
            by_task[r.instance_id].append(float(v))
    task_means = [mean(v) for v in by_task.values() if v]
    return (mean(task_means) if task_means else None), len(task_means)


def aggregate(rows: list[RunMetrics]) -> dict[str, Any]:
    """Per-configuration summary with explicit undefined counts."""
    out: dict[str, Any] = {}
    for cfg in sorted({r.agent_config for r in rows}):
        sub = [r for r in rows if r.agent_config == cfg]
        dr, dr_tasks = _macro(sub, "discovery_recall")
        crr, crr_tasks = _macro(sub, "conditional_retention")
        clr, clr_tasks = _macro(sub, "context_loss")
        out[cfg] = {
            "n_runs": len(sub),
            "n_tasks": len({r.instance_id for r in sub}),
            "discovery_recall_macro": dr,
            "conditional_retention_macro": crr,
            "context_loss_rate_macro": clr,
            "n_tasks_contributing": {"dr": dr_tasks, "crr": crr_tasks, "clr": clr_tasks},
            "n_no_patch": sum(r.undefined_reason == "no_patch" for r in sub),
            "n_nothing_discovered": sum(r.undefined_reason == "nothing_discovered" for r in sub),
            "annotation_statuses": sorted({r.annotation_status for r in sub}),
        }
    return out


def to_row(m: RunMetrics) -> dict[str, Any]:
    return asdict(m)
