from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from codetrace.evaluation import (
    evaluator_command,
    find_instance_reports,
    parse_instance_report,
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
