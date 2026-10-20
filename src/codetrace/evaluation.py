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
from dataclasses import dataclass
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

    @property
    def has_regression(self) -> bool:
        return self.p2p_failure > 0


def parse_instance_report(report: dict[str, Any], instance_id: str) -> InstanceOutcome:
    if instance_id not in report:
        raise ValueError(f"report has no entry for {instance_id}: keys={list(report)}")
    r = report[instance_id]
    if "resolved" not in r:
        raise ValueError(f"unexpected report structure for {instance_id}: {sorted(r)}")
    ts = r.get("tests_status") or {}

    def count(group: str, kind: str) -> int:
        return len((ts.get(group) or {}).get(kind) or [])

    return InstanceOutcome(
        instance_id=instance_id,
        resolved=bool(r["resolved"]),
        patch_applied=bool(r.get("patch_successfully_applied", r.get("patch_exists", False))),
        f2p_success=count("FAIL_TO_PASS", "success"),
        f2p_failure=count("FAIL_TO_PASS", "failure"),
        p2p_success=count("PASS_TO_PASS", "success"),
        p2p_failure=count("PASS_TO_PASS", "failure"),
    )


EVALUATOR_LOG_ROOT = Path("logs/run_evaluation")


def find_instance_reports(log_root: Path, run_id: str) -> dict[str, Path]:
    """Locate ``<log_root>/<run_id>/<model>/<instance_id>/report.json`` files
    (``log_root`` is normally ``EVALUATOR_LOG_ROOT`` relative to the working dir)."""
    base = log_root / run_id
    return {p.parent.name: p for p in sorted(base.glob("*/*/report.json"))}
