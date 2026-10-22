"""Independent cross-check of official SWE-bench reports against raw test logs.

For every harness run under a validation output directory, this script re-derives the
pass/fail status of each graded test directly from ``test_output.txt`` (without the
harness's log parsers) and compares it with:

  1. the harness's per-instance ``report.json``;
  2. CodeTrace's parsed outcome (``parse_instance_report``);
  3. the harness's run summary category, via ``collect_evaluation``.

Supported log formats: pytest-style ``PASSED <id>`` / ``FAILED <id> - ...`` lines and
sympy's ``test_name ok|E|F`` lines. A graded test with no status line in the log is
reported as unknown, which counts as a disagreement.

Test-id convention: for tasks whose record names ``log_parser: parse_log_pylint`` the
dataset spells parametrized ids whose parameter starts with "/" by the last path component
only (``test_stdin[/mymodule.py]`` for the logged
``test_stdin[/testbed/tests/mymodule.py-mymodule-/testbed/tests/mymodule.py]``). This is
re-implemented here from the documented behavior, not imported from the harness.

"Ungraded failure lines" lists failing ids in the log outside both graded lists. For
pytest's own suite this includes output of inner test sessions, so it is descriptive only.

Usage: python scripts/crosscheck_reports.py runs/<validation_out> [--dataset FILE]
Exit status 1 on any disagreement.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from codetrace.evaluation import collect_evaluation, parse_instance_report

PYTEST_LINE = re.compile(r"^(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED) (\S+)", re.M)
SYMPY_LINE = re.compile(r"^(test_\w+) (ok|E|F|f|X|s)\s*$", re.M)
PASS = {"PASSED", "ok", "XPASS"}


PARAM = re.compile(r"(.*?)\[(.*)\]")


def _normalize_path_param(name: str) -> str:
    m = PARAM.search(name)
    if (
        m
        and m.group(2).startswith("/")
        and not m.group(2).startswith("//")
        and "*" not in m.group(2)
    ):
        return f"{m.group(1)}[/{m.group(2).split('/')[-1]}]"
    return name


def log_statuses(log: str, normalize_path_params: bool = False) -> dict[str, str]:
    body = log.split(">>>>> Start Test Output", 1)[-1].split(">>>>> End Test Output", 1)[0]
    status = {
        (_normalize_path_param(name) if normalize_path_params else name): st
        for st, name in PYTEST_LINE.findall(body)
    }
    status.update({name: st for name, st in SYMPY_LINE.findall(body)})
    return status


def check_run(eval_dir: Path, tasks: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    rows = []
    run_id = next(eval_dir.glob("logs/run_evaluation/*")).name
    report_paths = sorted(eval_dir.glob("logs/run_evaluation/*/*/*/report.json"))
    ids = [p.parent.name for p in report_paths]
    collected = {r["instance_id"]: r for r in collect_evaluation(eval_dir, run_id, ids)}
    for rp in report_paths:
        iid = rp.parent.name
        report = json.loads(rp.read_text())
        parsed = parse_instance_report(report, iid)
        task = tasks[iid]
        statuses = log_statuses(
            (rp.parent / "test_output.txt").read_text(errors="replace"),
            normalize_path_params=task.get("log_parser") == "parse_log_pylint",
        )
        problems: list[str] = []
        indep: dict[str, dict[str, list[str]]] = {}
        for group in ("FAIL_TO_PASS", "PASS_TO_PASS"):
            names = task[group]
            assert isinstance(names, list)
            ok = [n for n in names if statuses.get(n) in PASS]
            unknown = [n for n in names if n not in statuses]
            indep[group] = {"success": ok, "failure": [n for n in names if n not in ok]}
            if unknown:
                problems.append(f"{group}: {len(unknown)} graded tests have no status line")
            rep = report[iid]["tests_status"][group]
            if sorted(rep["success"]) != sorted(ok):
                problems.append(
                    f"{group} success differs from log: report={len(rep['success'])} log={len(ok)}"
                )
        indep_resolved = (
            not indep["FAIL_TO_PASS"]["failure"] and not indep["PASS_TO_PASS"]["failure"]
        )
        if indep_resolved != parsed.resolved:
            problems.append(f"resolved: log-derived={indep_resolved} parsed={parsed.resolved}")
        if parsed.resolved != report[iid]["resolved"]:
            problems.append("parser disagrees with report.json on resolved")
        if (collected[iid]["category"] == "resolved") != parsed.resolved:
            problems.append(f"summary category {collected[iid]['category']} vs parsed resolved")
        counts = (parsed.f2p_success, parsed.f2p_failure, parsed.p2p_success, parsed.p2p_failure)
        log_counts = tuple(
            len(indep[g][k])
            for g in ("FAIL_TO_PASS", "PASS_TO_PASS")
            for k in ("success", "failure")
        )
        if counts != log_counts:
            problems.append(f"counts parsed={counts} log={log_counts}")
        rows.append({
            "run": eval_dir.name, "instance_id": iid, "category": collected[iid]["category"],
            "patch_applied": parsed.patch_applied, "resolved": parsed.resolved,
            "has_regression": parsed.has_regression,
            "evaluation_failed": collected[iid]["evaluation_failed"],
            "f2p": f"{parsed.f2p_success}/{parsed.f2p_success + parsed.f2p_failure}",
            "p2p": f"{parsed.p2p_success}/{parsed.p2p_success + parsed.p2p_failure}",
            "ungraded_failure_lines_in_log": sorted(
                n for n, st in statuses.items()
                if st not in PASS | {"SKIPPED", "XFAIL", "s", "f"}
                and n not in task["FAIL_TO_PASS"] and n not in task["PASS_TO_PASS"]  # type: ignore[operator]
            ),
            "harness_advisory_reason": collected[iid]["harness_advisory_reason"],
            "agrees": not problems, "problems": problems,
        })  # fmt: skip
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--dataset", type=Path, help="default: <out_dir>/evaluator_dataset.jsonl")
    args = ap.parse_args()
    ds = args.dataset or args.out_dir / "evaluator_dataset.jsonl"
    tasks = {json.loads(x)["instance_id"]: json.loads(x) for x in ds.read_text().splitlines()}
    rows = []
    for eval_dir in sorted(p for p in args.out_dir.iterdir() if (p / "logs").is_dir()):
        if any(eval_dir.glob("logs/run_evaluation/*/*/*/report.json")):
            rows += check_run(eval_dir, tasks)
    print(json.dumps(rows, indent=2))
    return 0 if rows and all(r["agrees"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
