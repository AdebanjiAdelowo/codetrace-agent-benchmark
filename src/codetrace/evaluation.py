"""Bridge to the official SWE-bench evaluator.

CodeTrace never decides whether a task is resolved. It (1) writes predictions in
the official JSONL format, (2) builds the exact evaluator command, and (3) parses
the evaluator's own report files.

VERIFICATION STATUS: the command flags and the report layout below were checked
against the installed swebench 5.0.2 source and ``--help`` output (2026-10-20):
reports are written to ``logs/run_evaluation/<run_id>/<model>/<instance_id>/
report.json`` with keys ``resolved``, ``patch_successfully_applied`` and
``tests_status.{FAIL_TO_PASS,PASS_TO_PASS}.{success,failure}``. They have NOT yet
been checked against a real evaluation run; ``parse_instance_report`` raises on
unexpected structure rather than guessing.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from codetrace.dataset import DATASET_NAME

PREDICTION_KEYS = ("instance_id", "model_name_or_path", "model_patch")


def write_predictions(rows: Iterable[dict[str, str]], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    seen: set[str] = set()
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            missing = [k for k in PREDICTION_KEYS if k not in row]
            if missing:
                raise ValueError(f"prediction missing keys {missing}")
            if row["instance_id"] in seen:
                raise ValueError(f"duplicate prediction for {row['instance_id']}")
            seen.add(row["instance_id"])
            fh.write(json.dumps({k: row[k] for k in PREDICTION_KEYS}) + "\n")
            n += 1
    return n


def evaluator_command(
    predictions_path: Path,
    run_id: str,
    instance_ids: list[str],
    *,
    dataset_name: str = DATASET_NAME,
    max_workers: int = 1,
    timeout_s: int = 1800,
    modal: bool = False,
) -> list[str]:
    """Official harness invocation (``run_evaluation`` flags as in swebench 5.0.2).

    ``dataset_name`` should be the pinned local export (see
    ``dataset.export_for_evaluator``). The harness pulls prebuilt x86_64 images;
    on Apple Silicon they run under emulation (see docs/RESOURCES.md).
    """
    cmd = [
        "python", "-m", "swebench.harness.run_evaluation",
        "--dataset_name", dataset_name,
        "--split", "test",
        "--predictions_path", str(predictions_path),
        "--run_id", run_id,
        "--max_workers", str(max_workers),
        "--timeout", str(timeout_s),
        "--instance_ids", *instance_ids,
    ]  # fmt: skip
    if modal:
        cmd += ["--modal", "true"]
    return cmd


def shell_join(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


@dataclass(frozen=True)
class InstanceOutcome:
    instance_id: str
    resolved: bool
    patch_applied: bool
    f2p_success: int
    f2p_failure: int
    p2p_success: int
    p2p_failure: int  # regressions: previously passing tests that now fail
    infra_failure: bool = False  # harness-classified environment fault (5.x)
    infra_failure_reason: str | None = None

    @property
    def has_regression(self) -> bool:
        return self.p2p_failure > 0


_REQUIRED_INSTANCE_KEYS = ("patch_exists", "patch_successfully_applied", "resolved")


def parse_instance_report(report: dict[str, Any], instance_id: str) -> InstanceOutcome:
    """Parse one per-instance ``report.json`` (swebench 5.0.2 layout)."""
    if instance_id not in report:
        raise ValueError(f"report has no entry for {instance_id}: keys={list(report)}")
    r = report[instance_id]
    missing = [k for k in _REQUIRED_INSTANCE_KEYS if k not in r]
    if missing:
        raise ValueError(f"unexpected report structure for {instance_id}: missing {missing}")
    ts = r.get("tests_status") or {}
    if r["patch_successfully_applied"] and not ts:
        raise ValueError(f"{instance_id}: patch applied but no tests_status in report")

    def count(group: str, kind: str) -> int:
        return len((ts.get(group) or {}).get(kind) or [])

    return InstanceOutcome(
        instance_id=instance_id,
        resolved=bool(r["resolved"]),
        patch_applied=bool(r["patch_successfully_applied"]),
        f2p_success=count("FAIL_TO_PASS", "success"),
        f2p_failure=count("FAIL_TO_PASS", "failure"),
        p2p_success=count("PASS_TO_PASS", "success"),
        p2p_failure=count("PASS_TO_PASS", "failure"),
        infra_failure=bool(r.get("infra_failure", False)),
        infra_failure_reason=r.get("infra_failure_reason"),
    )


EVALUATOR_LOG_ROOT = Path("logs/run_evaluation")


def find_instance_reports(log_root: Path, run_id: str) -> dict[str, Path]:
    """Locate ``<log_root>/<run_id>/<model>/<instance_id>/report.json`` files
    (``log_root`` is normally ``EVALUATOR_LOG_ROOT`` relative to the working dir)."""
    base = log_root / run_id
    return {p.parent.name: p for p in sorted(base.glob("*/*/report.json"))}


# ------------------------------------------------------------------ run summary

# Mutually exclusive outcome categories, in the order they are assigned.
CATEGORIES = ("empty_patch", "incomplete", "error", "resolved", "unresolved")
_SUMMARY_ID_KEYS = (
    "submitted_ids", "completed_ids", "incomplete_ids", "empty_patch_ids",
    "resolved_ids", "unresolved_ids", "error_ids", "infra_failure_ids",
    "ambiguous_failure_ids",
)  # fmt: skip


@dataclass(frozen=True)
class RunSummary:
    """The harness's run-level report ``<model>.<run_id>.json`` (summary schema 2)."""

    schema_version: int
    ids: dict[str, frozenset[str]]
    failure_reasons: dict[str, Any]
    unstopped_containers: tuple[str, ...]
    unremoved_images: tuple[str, ...]

    def category(self, instance_id: str) -> str:
        for cat in CATEGORIES:
            if instance_id in self.ids[f"{cat}_ids"]:
                return cat
        return "not_submitted"


def parse_run_summary(data: dict[str, Any]) -> RunSummary:
    if data.get("schema_version") != 2:
        raise ValueError(f"unsupported summary schema_version: {data.get('schema_version')!r}")
    missing = [k for k in _SUMMARY_ID_KEYS if k not in data]
    if missing:
        raise ValueError(f"summary missing keys: {missing}")
    ids = {k: frozenset(data[k]) for k in _SUMMARY_ID_KEYS}
    # Categories are mutually exclusive (infra/ambiguous are advisory sub-labels).
    # They need not cover every submitted id: with --instance_ids, swebench 5.0.2 lists
    # every prediction in the file under submitted_ids but only evaluates the requested
    # subset (observed 2026-10-20). Coverage of the *requested* ids is checked in
    # collect_evaluation.
    seen: dict[str, str] = {}
    for cat in CATEGORIES:
        for iid in ids[f"{cat}_ids"]:
            if iid in seen:
                raise ValueError(f"{iid} is in both {seen[iid]} and {cat}")
            seen[iid] = cat
    if not set(seen) <= ids["submitted_ids"]:
        raise ValueError(
            f"categorized ids not submitted: {sorted(set(seen) - ids['submitted_ids'])}"
        )
    return RunSummary(
        schema_version=2,
        ids=ids,
        failure_reasons=dict(data.get("failure_reasons") or {}),
        unstopped_containers=tuple(data.get("unstopped_containers") or ()),
        unremoved_images=tuple(data.get("unremoved_images") or ()),
    )


def find_run_summary(eval_dir: Path, run_id: str) -> Path:
    hits = sorted(eval_dir.glob(f"*.{run_id}.json"))
    if len(hits) != 1:
        raise FileNotFoundError(f"expected one summary for {run_id} in {eval_dir}, found {hits}")
    return hits[0]


def collect_evaluation(
    eval_dir: Path, run_id: str, instance_ids: list[str]
) -> list[dict[str, Any]]:
    """Merge the run summary with per-instance reports into one row per task, and
    check that the two agree. ``eval_dir`` is the working directory the harness ran in."""
    summary = parse_run_summary(json.loads(find_run_summary(eval_dir, run_id).read_text()))
    reports = find_instance_reports(eval_dir / EVALUATOR_LOG_ROOT, run_id)
    rows = []
    for iid in instance_ids:
        cat = summary.category(iid)
        if cat == "not_submitted":
            raise ValueError(f"{iid}: requested but absent from every summary category")
        row: dict[str, Any] = {
            "instance_id": iid,
            "category": cat,
            "infra_failure": iid in summary.ids["infra_failure_ids"],
            # Advisory only: swebench's log classifier (infra_failure.py) re-labels
            # unresolved runs by regex and never changes `resolved`. It can misfire, e.g.
            # "no_tests_collected" on pytest's own suite, whose tests print
            # "collected 0 items" for inner sessions (observed 2026-10-20).
            "harness_advisory_ambiguous": iid in summary.ids["ambiguous_failure_ids"],
            "harness_advisory_reason": summary.failure_reasons.get(iid),
            "patch_applied": None,
            "resolved": cat == "resolved",
            "has_regression": None,
        }
        if iid in reports:
            outcome = parse_instance_report(json.loads(reports[iid].read_text()), iid)
            if (cat == "resolved") != outcome.resolved:
                raise ValueError(
                    f"{iid}: summary says {cat} but report.json resolved={outcome.resolved}"
                )
            row.update(asdict(outcome))
            row["has_regression"] = outcome.has_regression
            row["report_path"] = str(reports[iid].relative_to(eval_dir))
        elif cat in ("resolved", "unresolved"):
            raise ValueError(f"{iid}: graded as {cat} but no report.json found")
        # Evaluation failure: the harness could not produce a valid grade. This is
        # distinct from a patch that applied but did not resolve the task.
        row["evaluation_failed"] = cat in ("error", "incomplete") or bool(row["infra_failure"])
        rows.append(row)
    return rows
