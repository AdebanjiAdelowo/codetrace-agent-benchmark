from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from codetrace.dataset import record_to_task
from codetrace.validation import (
    CONTROL_COMMENT,
    EXPECTATIONS,
    check_rows,
    load_bundle,
    make_control_patch,
    sha256_file,
)
from codetrace.workspace import create_workspace, ensure_mirror, git

BUNDLE = Path(__file__).parents[1] / "eval_bundles" / "harness_validation_v1"


def test_committed_bundle_is_consistent() -> None:
    bundle = load_bundle(BUNDLE)  # checks dataset revision and prediction hashes
    assert bundle["instance_ids"] == [
        "pytest-dev__pytest-11143", "sympy__sympy-13480", "pylint-dev__pylint-7080",
    ]  # fmt: skip
    assert {r["name"]: r["expect"] for r in bundle["runs"]} == {
        "smoke": "empty_patch", "gold": "resolved", "control": "unresolved_applied",
    }  # fmt: skip
    smoke = [json.loads(x) for x in (BUNDLE / "smoke_predictions.jsonl").read_text().splitlines()]
    assert [p["model_patch"] for p in smoke] == ["", "", ""]
    control = [
        json.loads(x) for x in (BUNDLE / "control_predictions.jsonl").read_text().splitlines()
    ]
    for p in control:
        added = [
            ln
            for ln in p["model_patch"].splitlines()
            if ln.startswith("+") and not ln.startswith("+++")
        ]
        assert added == ["+" + CONTROL_COMMENT.rstrip("\n")], p["instance_id"]


def test_bundle_tamper_detected(tmp_path: Path) -> None:
    copy = tmp_path / "b"
    shutil.copytree(BUNDLE, copy)
    with (copy / "control_predictions.jsonl").open("a") as fh:
        fh.write("\n")
    with pytest.raises(ValueError, match="sha256"):
        load_bundle(copy)


def test_control_patch_applies_and_is_comment_only(
    tmp_path: Path, upstream_repo: tuple[Path, str], task_record: dict[str, Any]
) -> None:
    repo, base = upstream_repo
    task = record_to_task(task_record)
    mirror_root = tmp_path / "mirrors"
    # Pre-seed the mirror from the local fixture so no network fetch happens.
    ensure_mirror(str(repo), mirror_root / "toy__calc", commits=[base])
    patch = make_control_patch(task, mirror_root, tmp_path / "work")
    assert "+++ b/calc/core.py" in patch and "+" + CONTROL_COMMENT.rstrip() in patch
    fresh = create_workspace(mirror_root / "toy__calc", base, tmp_path / "fresh")
    git("apply", "--check", "-", cwd=fresh.path, input_bytes=patch.encode())
    assert sha256_file(fresh.path / "calc" / "core.py")  # untouched pristine copy exists


def _row(cat: str, **kw: Any) -> dict[str, Any]:
    return {"instance_id": "x", "category": cat, **kw}


def test_expectations() -> None:
    assert check_rows([_row("empty_patch")], EXPECTATIONS["empty_patch"]) == []
    ok = _row("resolved", patch_applied=True, has_regression=False)
    assert check_rows([ok], EXPECTATIONS["resolved"]) == []
    assert check_rows([{**ok, "has_regression": True}], EXPECTATIONS["resolved"])
    ctrl = _row("unresolved", patch_applied=True, f2p_success=0, f2p_failure=1)
    assert check_rows([ctrl], EXPECTATIONS["unresolved_applied"]) == []
    assert check_rows([{**ctrl, "f2p_success": 1}], EXPECTATIONS["unresolved_applied"])
    assert check_rows([{**ctrl, "patch_applied": False}], EXPECTATIONS["unresolved_applied"])
    assert check_rows([_row("error")], EXPECTATIONS["resolved"])
