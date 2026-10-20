"""Static checks of GitHub Actions workflows (they cannot be run locally)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).parents[1] / ".github" / "workflows").glob("*.yml"))


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 parses `on:` as True
    return data


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_invariants(path: Path) -> None:
    wf = _load(path)
    assert wf["permissions"] == {"contents": "read"}
    assert "secrets." not in path.read_text(), "workflows must not use repository secrets"
    for name, job in wf["jobs"].items():
        assert 0 < job["timeout-minutes"] <= 120, name
        for step in job["steps"]:
            uses = step.get("uses")
            if uses:
                assert "@" in uses and not uses.endswith("@main"), uses


def test_expected_workflows_present() -> None:
    assert {p.name for p in WORKFLOWS} == {"ci.yml", "swebench-harness-validation.yml"}


def test_evaluation_workflow_is_manual_only() -> None:
    wf = _load(Path(__file__).parents[1] / ".github/workflows/swebench-harness-validation.yml")
    assert list(wf["on"]) == ["workflow_dispatch"]
    assert wf["concurrency"]["cancel-in-progress"] is False
    upload = next(
        s
        for s in wf["jobs"]["validate"]["steps"]
        if s.get("uses", "").startswith("actions/upload-artifact")
    )
    assert "!runs/harness_validation_v1/evaluator_dataset.jsonl" in upload["with"]["path"]
