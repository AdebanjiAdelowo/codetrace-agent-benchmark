"""Audit run artifacts for an experiment: trace validity and hidden-information leakage.

For every trace under runs/<experiment_id>/runs/, checks that:
  * the trace is structurally valid (codetrace.trace.validate_trace);
  * no FAIL_TO_PASS / PASS_TO_PASS test identifier from the evaluator record appears
    anywhere in the trace;
  * no substantive added line of the test patch appears in the trace;
  * no added line of the reference patch appears in the trace *outside the agent's own
    patch* (an agent may legitimately write the same line);
and reports whether any reference-patch file was viewed (a preview of discovery).

Usage: python scripts/audit_runs.py runs/<experiment_id> [--dataset FILE]
Exit status 1 if any check fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codetrace.dataset import download_split, load_tasks
from codetrace.trace import EventKind, read_trace, validate_trace


def _added_lines(patch: str, min_len: int) -> list[str]:
    return [
        ln[1:].strip()
        for ln in patch.splitlines()
        if ln.startswith("+") and not ln.startswith("+++") and len(ln[1:].strip()) >= min_len
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment_dir", type=Path)
    ap.add_argument("--dataset", type=Path, help="local dataset file (default: pinned download)")
    args = ap.parse_args()
    tasks = load_tasks(args.dataset or download_split("test"))
    failed = False
    rows = []
    for tp in sorted(args.experiment_dir.glob("runs/*/trace.jsonl")):
        events = read_trace(tp)
        problems = validate_trace(events)
        manifest = events[0].payload["manifest"]
        task = tasks[manifest["task_instance_id"]]
        agent_patch = next(
            (e.payload["patch"] for e in events if e.kind is EventKind.PATCH_GENERATED), ""
        )
        text = tp.read_text(encoding="utf-8")
        # Remove the agent's own patch (it appears JSON-escaped in the trace).
        if agent_patch:
            text = text.replace(json.dumps(agent_patch)[1:-1], "")
        test_ids = [t for t in (*task.fail_to_pass, *task.pass_to_pass) if t in text]
        test_lines = [x for x in _added_lines(task.test_patch, 25) if x in text]
        gold_lines = [x for x in _added_lines(task.patch, 8) if json.dumps(x)[1:-1] in text]
        gold_files = sorted(ln[6:] for ln in task.patch.splitlines() if ln.startswith("+++ b/"))
        viewed = {
            e.payload["span"]["path"] for e in events if e.kind is EventKind.CODE_LOCATION_VIEWED
        }
        ok = not (problems or test_ids or test_lines or gold_lines)
        failed |= not ok
        rows.append(
            {
                "run_id": manifest["run_id"],
                "instance_id": task.instance_id,
                "trace_valid": not problems,
                "trace_problems": problems,
                "leaked_test_ids": test_ids,
                "leaked_test_patch_lines": len(test_lines),
                "leaked_reference_patch_lines": len(gold_lines),
                "reference_files": gold_files,
                "reference_file_viewed": bool(viewed & set(gold_files)),
                "n_files_viewed": len(viewed),
                "audit_pass": ok,
            }
        )
    print(json.dumps(rows, indent=2))
    if not rows:
        print("no traces found", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
