"""Harness validation bundles: known-answer checks of the official evaluator.

A bundle (``eval_bundles/<name>/bundle.json``) lists the pinned dataset revision,
the task ids, the SHA-256 of the evaluator dataset export, and several prediction
sets with the outcome each must produce:

- ``smoke``: the deterministic smoke-run predictions (empty patches). The harness
  skips empty patches before any container starts, so this needs no images.
- ``gold``: the reference patches (``--predictions_path gold``). Expected: resolved.
- ``control``: a negative control that appends one comment line to the file the
  reference fix changes. Expected: applies, unresolved, FAIL_TO_PASS still failing.

These are checks of the evaluation pipeline, not research results.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codetrace.dataset import DATASET_NAME, DATASET_REVISION, EvaluatorTask, export_for_evaluator
from codetrace.evaluation import collect_evaluation, evaluator_command, write_predictions
from codetrace.workspace import create_workspace, ensure_mirror, repo_url

CONTROL_COMMENT = "# codetrace negative control: comment only, no functional change\n"
CONTROL_MODEL = "codetrace__negative_control"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference_files(task: EvaluatorTask) -> list[str]:
    return sorted(ln[6:] for ln in task.patch.splitlines() if ln.startswith("+++ b/"))


def make_control_patch(task: EvaluatorTask, mirror_root: Path, work_root: Path) -> str:
    """Append a comment line to the first file the reference patch changes and return
    the resulting diff against ``base_commit`` (evaluator-side; uses the reference patch
    only to choose the file)."""
    slug = task.repo.replace("/", "__")
    mirror = ensure_mirror(repo_url(task.repo), mirror_root / slug, commits=[task.base_commit])
    ws = create_workspace(mirror, task.base_commit, work_root / task.instance_id)
    target = ws.path / reference_files(task)[0]
    text = target.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    target.write_text(text + CONTROL_COMMENT, encoding="utf-8")
    patch = ws.diff()
    if not patch:
        raise RuntimeError(f"{task.instance_id}: control patch is empty")
    return patch


def write_control_predictions(
    tasks: list[EvaluatorTask], mirror_root: Path, work_root: Path, out: Path
) -> int:
    rows = (
        {
            "instance_id": t.instance_id,
            "model_name_or_path": CONTROL_MODEL,
            "model_patch": make_control_patch(t, mirror_root, work_root),
        }
        for t in tasks
    )
    return write_predictions(rows, out)


# ------------------------------------------------------------------ expectations


@dataclass(frozen=True)
class Expectation:
    category: str  # "empty_patch" | "resolved" | "unresolved"
    patch_applied: bool | None = None
    f2p_all_fail: bool = False  # every FAIL_TO_PASS test still failing
    no_regression: bool = False


EXPECTATIONS = {
    "empty_patch": Expectation("empty_patch"),
    "resolved": Expectation("resolved", patch_applied=True, no_regression=True),
    "unresolved_applied": Expectation("unresolved", patch_applied=True, f2p_all_fail=True),
}


def check_rows(rows: list[dict[str, Any]], expect: Expectation) -> list[str]:
    problems = []
    for r in rows:
        iid = r["instance_id"]
        if r["category"] != expect.category:
            problems.append(f"{iid}: category {r['category']} != expected {expect.category}")
            continue
        if expect.patch_applied is not None and r.get("patch_applied") != expect.patch_applied:
            problems.append(f"{iid}: patch_applied={r.get('patch_applied')}")
        if expect.f2p_all_fail and (r.get("f2p_success", 0) != 0 or r.get("f2p_failure", 0) == 0):
            problems.append(f"{iid}: FAIL_TO_PASS not all failing ({r.get('f2p_success')} passed)")
        if expect.no_regression and r.get("has_regression"):
            problems.append(f"{iid}: unexpected PASS_TO_PASS regression")
    return problems


# ------------------------------------------------------------------ bundle runner


def load_bundle(bundle_dir: Path) -> dict[str, Any]:
    bundle: dict[str, Any] = json.loads((bundle_dir / "bundle.json").read_text())
    if bundle["dataset"] != DATASET_NAME or bundle["dataset_revision"] != DATASET_REVISION:
        raise ValueError("bundle was built for a different dataset revision")
    for run in bundle["runs"]:
        if run["predictions"] != "gold":
            got = sha256_file(bundle_dir / run["predictions"])
            if got != run["predictions_sha256"]:
                raise ValueError(f"{run['name']}: predictions sha256 mismatch ({got})")
    return bundle


def run_bundle(
    bundle_dir: Path, out_dir: Path, dataset_file: Path, *, skip_docker: bool, max_workers: int = 1
) -> dict[str, Any]:
    """Run each prediction set through the official harness and check expectations.

    ``skip_docker`` runs only the sets whose expected category is ``empty_patch``.
    """
    bundle = load_bundle(bundle_dir)
    ids: list[str] = bundle["instance_ids"]
    out_dir.mkdir(parents=True, exist_ok=True)
    eval_ds = out_dir / "evaluator_dataset.jsonl"
    export_for_evaluator(dataset_file, ids, eval_ds)
    ds_sha = sha256_file(eval_ds)
    result: dict[str, Any] = {
        "bundle": bundle["name"],
        "evaluator_dataset_sha256": ds_sha,
        "evaluator_dataset_sha256_ok": ds_sha == bundle["evaluator_dataset_sha256"],
        "runs": [],
    }
    if not result["evaluator_dataset_sha256_ok"]:
        raise ValueError(f"evaluator dataset sha256 {ds_sha} != bundle")
    for run in bundle["runs"]:
        expect = EXPECTATIONS[run["expect"]]
        entry: dict[str, Any] = {"name": run["name"], "expect": run["expect"]}
        if skip_docker and expect.category != "empty_patch":
            entry["status"] = "skipped (needs Docker images)"
            result["runs"].append(entry)
            continue
        eval_dir = out_dir / run["name"]
        eval_dir.mkdir(parents=True, exist_ok=True)
        preds = run["predictions"]
        preds_arg = "gold" if preds == "gold" else str((bundle_dir / preds).resolve())
        run_id = f"{bundle['name']}__{run['name']}"
        cmd = evaluator_command(
            Path(preds_arg),
            run_id,
            ids,
            dataset_name=str(eval_ds.resolve()),
            max_workers=max_workers,
        )
        cmd[0] = sys.executable
        proc = subprocess.run(cmd, cwd=eval_dir, capture_output=True, text=True, check=False)
        (eval_dir / "harness_stdout.txt").write_text(
            proc.stdout + "\n--- stderr ---\n" + proc.stderr
        )
        entry["harness_exit_code"] = proc.returncode
        if proc.returncode != 0:
            entry["status"] = "harness error"
            result["runs"].append(entry)
            continue
        rows = collect_evaluation(eval_dir, run_id, ids)
        (eval_dir / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
        problems = check_rows(rows, expect)
        entry["status"] = "pass" if not problems else "FAIL"
        entry["problems"] = problems
        entry["categories"] = {r["instance_id"]: r["category"] for r in rows}
        result["runs"].append(entry)
    result["all_executed_passed"] = all(
        e["status"] in ("pass",) or e["status"].startswith("skipped") for e in result["runs"]
    )
    (out_dir / "validation_result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
