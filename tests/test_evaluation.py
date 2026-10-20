from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from codetrace.evaluation import (
    EVALUATOR_LOG_ROOT,
    collect_evaluation,
    evaluator_command,
    find_instance_reports,
    parse_instance_report,
    parse_run_summary,
    write_predictions,
)


def test_predictions_format(tmp_path: Path) -> None:
    p = tmp_path / "preds.jsonl"
    n = write_predictions(
        [{"instance_id": "a", "model_name_or_path": "m", "model_patch": "", "extra": "dropped"}], p
    )
    assert n == 1
    assert json.loads(p.read_text()) == {
        "instance_id": "a",
        "model_name_or_path": "m",
        "model_patch": "",
    }


def test_predictions_reject_bad_rows(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing"):
        write_predictions([{"instance_id": "a"}], tmp_path / "p.jsonl")
    row = {"instance_id": "a", "model_name_or_path": "m", "model_patch": ""}
    with pytest.raises(ValueError, match="duplicate"):
        write_predictions([row, row], tmp_path / "p.jsonl")


def test_command_uses_official_harness() -> None:
    cmd = evaluator_command(Path("/x/p.jsonl"), "run1", ["a", "b"])
    assert cmd[:3] == ["python", "-m", "swebench.harness.run_evaluation"]
    assert cmd[cmd.index("--dataset_name") + 1] == "SWE-bench/SWE-bench_Lite"
    assert "--cache_level" not in cmd  # removed in swebench 5.x
    assert cmd[-2:] == ["a", "b"] and "--modal" not in cmd
    assert evaluator_command(Path("p"), "r", ["a"], modal=True)[-2:] == ["--modal", "true"]


def _report(resolved: bool, p2p_fail: list[str]) -> dict[str, object]:
    return {
        "x__y-1": {
            "patch_is_None": False, "patch_exists": True, "patch_successfully_applied": True,
            "infra_failure": False,
            "resolved": resolved,
            "tests_status": {
                "FAIL_TO_PASS": {"success": ["t1"] if resolved else [], "failure": [] if resolved else ["t1"]},
                "PASS_TO_PASS": {"success": ["t2"], "failure": p2p_fail},
            },
        }
    }  # fmt: skip


def test_parse_report() -> None:
    o = parse_instance_report(_report(True, []), "x__y-1")
    assert o.resolved and o.patch_applied and o.f2p_success == 1 and not o.has_regression
    o = parse_instance_report(_report(False, ["t3"]), "x__y-1")
    assert not o.resolved and o.has_regression and o.p2p_failure == 1


def test_parse_report_rejects_unknown_structure() -> None:
    with pytest.raises(ValueError):
        parse_instance_report({"x__y-1": {"status": "ok"}}, "x__y-1")
    with pytest.raises(ValueError):
        parse_instance_report({}, "x__y-1")


def test_find_reports(tmp_path: Path) -> None:
    d = tmp_path / "run1" / "model" / "x__y-1"
    d.mkdir(parents=True)
    (d / "report.json").write_text("{}")
    assert find_instance_reports(tmp_path, "run1") == {"x__y-1": d / "report.json"}


def test_flags_exist_in_installed_harness() -> None:
    """Contract test against the real evaluator CLI (skipped without the `eval` extra)."""
    pytest.importorskip("swebench")
    help_text = subprocess.run(
        [sys.executable, "-m", "swebench.harness.run_evaluation", "--help"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    known = set(re.findall(r"--[a-z_]+", help_text))
    emitted = {
        c for c in evaluator_command(Path("p"), "r", ["a"], modal=True) if c.startswith("--")
    }
    assert emitted <= known, emitted - known


# ------------------------------------------------------------------ run summaries

FIXTURES = Path(__file__).parent / "fixtures"
SMOKE_IDS = ["pytest-dev__pytest-11143", "sympy__sympy-13480", "pylint-dev__pylint-7080"]


def test_parse_real_summary_artifact() -> None:
    """Fixture is the unmodified summary the official harness (5.0.2) wrote for the
    smoke_heuristic_v2 predictions on 2026-10-20."""
    s = parse_run_summary(
        json.loads((FIXTURES / "swebench_5.0.2_summary_empty_patches.json").read_text())
    )
    assert s.schema_version == 2
    assert [s.category(i) for i in SMOKE_IDS] == ["empty_patch"] * 3
    assert s.category("other__x-1") == "not_submitted"


def test_summary_rejects_inconsistent_partition() -> None:
    data = json.loads((FIXTURES / "swebench_5.0.2_summary_empty_patches.json").read_text())
    with pytest.raises(ValueError, match="both"):
        parse_run_summary({**data, "resolved_ids": [SMOKE_IDS[0]]})
    with pytest.raises(ValueError, match="partition"):
        parse_run_summary({**data, "empty_patch_ids": SMOKE_IDS[:2]})
    with pytest.raises(ValueError, match="schema_version"):
        parse_run_summary({**data, "schema_version": 1})


# ------------------------------------------------- official grading contract tests

F2P = ["tests/test_core.py::test_add"]
P2P = ["tests/test_core.py::test_greet", "tests/test_core.py::test_other"]


def _official_report(
    tmp_path: Path, log_lines: list[str], *, markers: bool = True
) -> dict[str, Any]:
    """Produce a per-instance report with the harness's own grading code (no Docker)."""
    swebench = pytest.importorskip("swebench")
    from swebench.harness.constants import END_TEST_OUTPUT, START_TEST_OUTPUT
    from swebench.harness.grading import get_eval_report
    from swebench.harness.utils import make_test_spec

    assert swebench.__version__ == "5.0.2"
    spec = make_test_spec({
        "instance_id": "toy__calc-1", "image": "example/none:latest", "repo": "toy/calc",
        "version": "0.1", "FAIL_TO_PASS": json.dumps(F2P), "PASS_TO_PASS": json.dumps(P2P),
        "log_parser": "parse_log_pytest", "eval_type": "pass_and_fail",
        "eval_script": "#!/bin/bash\npytest tests/test_core.py\n",
    })  # fmt: skip
    body = "\n".join(log_lines)
    log = tmp_path / "test_output.txt"
    log.write_text(f"{START_TEST_OUTPUT}\n{body}\n{END_TEST_OUTPUT}\n" if markers else body + "\n")
    pred = {"instance_id": "toy__calc-1", "model_name_or_path": "m", "model_patch": "diff"}
    report: dict[str, Any] = get_eval_report(spec, pred, str(log), include_tests_status=True)
    return report


def test_official_report_resolved(tmp_path: Path) -> None:
    rep = _official_report(tmp_path, [f"PASSED {t}" for t in F2P + P2P])
    o = parse_instance_report(rep, "toy__calc-1")
    assert o.resolved and o.patch_applied and not o.has_regression
    assert (o.f2p_success, o.f2p_failure, o.p2p_success, o.p2p_failure) == (1, 0, 2, 0)


def test_official_report_regression(tmp_path: Path) -> None:
    rep = _official_report(
        tmp_path, [f"PASSED {F2P[0]}", f"PASSED {P2P[0]}", f"FAILED {P2P[1]} - boom"]
    )
    o = parse_instance_report(rep, "toy__calc-1")
    assert not o.resolved and o.has_regression and o.p2p_failure == 1 and o.f2p_success == 1


def test_official_report_not_fixed(tmp_path: Path) -> None:
    rep = _official_report(tmp_path, [f"FAILED {F2P[0]} - assert", *(f"PASSED {t}" for t in P2P)])
    o = parse_instance_report(rep, "toy__calc-1")
    assert not o.resolved and o.patch_applied and o.f2p_failure == 1 and not o.has_regression


def test_official_report_patch_apply_failure(tmp_path: Path) -> None:
    from_harness = pytest.importorskip("swebench.harness.constants")
    rep = _official_report(tmp_path, [from_harness.APPLY_PATCH_FAIL], markers=False)
    o = parse_instance_report(rep, "toy__calc-1")
    assert not o.resolved and not o.patch_applied and o.f2p_success == o.f2p_failure == 0


def _write_eval_dir(
    tmp_path: Path, run_id: str, reports: dict[str, dict[str, Any]], summary: dict[str, Any]
) -> Path:
    eval_dir = tmp_path / "evaluation"
    eval_dir.mkdir()
    for iid, rep in reports.items():
        d = eval_dir / EVALUATOR_LOG_ROOT / run_id / "m" / iid
        d.mkdir(parents=True)
        (d / "report.json").write_text(json.dumps(rep))
    (eval_dir / f"m.{run_id}.json").write_text(json.dumps(summary))
    return eval_dir


def _summary(**ids: list[str]) -> dict[str, Any]:
    keys = ("submitted_ids", "completed_ids", "incomplete_ids", "empty_patch_ids", "resolved_ids",
            "unresolved_ids", "error_ids", "infra_failure_ids", "ambiguous_failure_ids")  # fmt: skip
    return {"schema_version": 2, **{k: [] for k in keys}, **ids, "failure_reasons": {}}


def test_collect_evaluation_merges_and_checks(tmp_path: Path) -> None:
    rep = _official_report(tmp_path, [f"PASSED {t}" for t in F2P + P2P])
    summary = _summary(submitted_ids=["toy__calc-1", "e-1"], completed_ids=["toy__calc-1"],
                       resolved_ids=["toy__calc-1"], empty_patch_ids=["e-1"])  # fmt: skip
    eval_dir = _write_eval_dir(tmp_path, "r1", {"toy__calc-1": rep}, summary)
    rows = collect_evaluation(eval_dir, "r1", ["toy__calc-1", "e-1"])
    assert [(r["instance_id"], r["category"]) for r in rows] == [
        ("toy__calc-1", "resolved"),
        ("e-1", "empty_patch"),
    ]
    assert rows[0]["resolved"] and rows[0]["report_path"].endswith("toy__calc-1/report.json")


def test_collect_evaluation_detects_disagreement(tmp_path: Path) -> None:
    rep = _official_report(tmp_path, [f"FAILED {F2P[0]} - x", *(f"PASSED {t}" for t in P2P)])
    summary = _summary(
        submitted_ids=["toy__calc-1"], completed_ids=["toy__calc-1"], resolved_ids=["toy__calc-1"]
    )
    eval_dir = _write_eval_dir(tmp_path, "r1", {"toy__calc-1": rep}, summary)
    with pytest.raises(ValueError, match="summary says resolved"):
        collect_evaluation(eval_dir, "r1", ["toy__calc-1"])


def test_collect_evaluation_requires_report_for_graded(tmp_path: Path) -> None:
    summary = _summary(submitted_ids=["x-1"], completed_ids=["x-1"], unresolved_ids=["x-1"])
    eval_dir = _write_eval_dir(tmp_path, "r1", {}, summary)
    with pytest.raises(ValueError, match=r"no report\.json"):
        collect_evaluation(eval_dir, "r1", ["x-1"])
