"""Regression tests on official SWE-bench 5.0.2 reports produced in Docker.

Fixtures: tests/fixtures/swebench_5.0.2_docker/, written by
scripts/sanitize_eval_fixtures.py from the local validation run of 2026-10-20 (gold and
comment-only negative-control patches for the three pilot tasks, x86_64 images under
emulation). Test identifiers are replaced by hashes; structure and counts are unchanged.
Expected values below are the evaluator's own outcomes, confirmed against the raw test
logs by scripts/crosscheck_reports.py.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from codetrace.evaluation import collect_evaluation, parse_instance_report, parse_run_summary
from codetrace.validation import EXPECTATIONS, check_rows

ROOT = Path(__file__).parent / "fixtures" / "swebench_5.0.2_docker"

# run dir -> (instance, category, f2p_success, f2p_failure, p2p_success, p2p_failure)
EXPECTED = {
    "gold__pytest-11143": ("pytest-dev__pytest-11143", "resolved", 1, 0, 114, 0),
    "control__pytest-11143": ("pytest-dev__pytest-11143", "unresolved", 0, 1, 114, 0),
    "gold__sympy-13480": ("sympy__sympy-13480", "resolved", 1, 0, 43, 0),
    "control__sympy-13480": ("sympy__sympy-13480", "unresolved", 0, 1, 43, 0),
    "gold__pylint-7080": ("pylint-dev__pylint-7080", "resolved", 1, 0, 120, 0),
    "control__pylint-7080": ("pylint-dev__pylint-7080", "unresolved", 0, 1, 120, 0),
}


def _run_id(run_dir: Path) -> str:
    return next((run_dir / "logs" / "run_evaluation").iterdir()).name


def test_fixture_set_complete() -> None:
    assert {p.name for p in ROOT.iterdir()} == set(EXPECTED)


@pytest.mark.parametrize("run", sorted(EXPECTED))
def test_real_report_parses_to_official_outcome(run: str) -> None:
    iid, cat, f2p_s, f2p_f, p2p_s, p2p_f = EXPECTED[run]
    report_path = next((ROOT / run).glob("logs/run_evaluation/*/*/*/report.json"))
    report = json.loads(report_path.read_text())
    raw = report[iid]
    o = parse_instance_report(report, iid)
    # Patch application, resolution, and regression are distinct fields.
    assert o.patch_applied is True and raw["patch_successfully_applied"] is True
    assert o.resolved is (cat == "resolved") is raw["resolved"]
    assert (o.f2p_success, o.f2p_failure, o.p2p_success, o.p2p_failure) == (
        f2p_s,
        f2p_f,
        p2p_s,
        p2p_f,
    )
    assert o.has_regression is False and o.infra_failure is False
    # 5.0.2 reports carry extra groups; they must be present and empty here.
    assert raw["tests_status"]["PASS_TO_FAIL"] == {"success": [], "failure": []}
    assert raw["tests_status"]["FAIL_TO_FAIL"] == {"success": [], "failure": []}


@pytest.mark.parametrize("run", sorted(EXPECTED))
def test_real_run_collected_and_meets_expectation(run: str) -> None:
    iid, cat, *_ = EXPECTED[run]
    rows = collect_evaluation(ROOT / run, _run_id(ROOT / run), [iid])
    assert [r["category"] for r in rows] == [cat]
    assert rows[0]["evaluation_failed"] is False
    expect = EXPECTATIONS["resolved" if run.startswith("gold") else "unresolved_applied"]
    assert check_rows(rows, expect) == []
    # A gold expectation must reject a control result and vice versa.
    other = EXPECTATIONS["unresolved_applied" if run.startswith("gold") else "resolved"]
    assert check_rows(rows, other) != []


def test_submitted_ids_exceed_evaluated_subset() -> None:
    """Real behavior: with --instance_ids, the summary lists every prediction in the file."""
    run = ROOT / "control__pytest-11143"
    s = parse_run_summary(json.loads(next(run.glob("*.json")).read_text()))
    assert len(s.ids["submitted_ids"]) == 3
    assert s.category("pytest-dev__pytest-11143") == "unresolved"
    assert s.category("sympy__sympy-13480") == "not_submitted"


def test_advisory_label_does_not_override_grade() -> None:
    """swebench's regex classifier marks the pytest control 'no_tests_collected' although
    116 tests ran (inner pytest sessions print 'collected 0 items')."""
    run = ROOT / "control__pytest-11143"
    row = collect_evaluation(run, _run_id(run), ["pytest-dev__pytest-11143"])[0]
    assert row["harness_advisory_ambiguous"] is True
    assert row["harness_advisory_reason"] == "no_tests_collected"
    assert row["category"] == "unresolved" and row["evaluation_failed"] is False


def test_fixtures_are_sanitized() -> None:
    for f in ROOT.rglob("*.json"):
        text = f.read_text()
        assert "/Users/" not in text and "/home/" not in text and "/testbed" not in text, f
    for f in ROOT.rglob("report.json"):
        for rec in json.loads(f.read_text()).values():
            for group in rec["tests_status"].values():
                for name in group["success"] + group["failure"]:
                    assert re.fullmatch(r"test_[0-9a-f]{12}", name), (f, name)


# ------------------------------------------------- independent log reader (script)

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import crosscheck_reports as cc  # noqa: E402


def test_log_reader_formats() -> None:
    log = (
        "noise\n>>>>> Start Test Output\n"
        "PASSED a.py::test_x\nFAILED a.py::test_y - AssertionError\n"
        "test_coth E\ntest_sinh ok\n"
        "PASSED t.py::T::test_stdin[/testbed/tests/m.py-m-/testbed/tests/m.py]\n"
        ">>>>> End Test Output\nPASSED outside.py::ignored\n"
    )
    st = cc.log_statuses(log)
    assert st["a.py::test_x"] == "PASSED" and st["a.py::test_y"] == "FAILED"
    assert st["test_coth"] == "E" and st["test_sinh"] == "ok"
    assert "outside.py::ignored" not in st
    assert "t.py::T::test_stdin[/m.py]" not in st
    assert (
        cc.log_statuses(log, normalize_path_params=True)["t.py::T::test_stdin[/m.py]"] == "PASSED"
    )
