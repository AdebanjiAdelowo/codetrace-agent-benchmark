from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codetrace.annotation_package import build_package, reviewer_template, write_brief
from codetrace.dataset import record_to_task
from codetrace.evidence import auto_annotation, load_annotation
from codetrace.tools import RepoTools

from .conftest import BUGGY, REFERENCE_PATCH

DOCS = Path(__file__).parents[1] / "docs"


def _inputs(task_record: dict[str, Any]) -> tuple[Any, ...]:
    task = record_to_task(task_record)
    auto = auto_annotation(task.instance_id, REFERENCE_PATCH, task.base_commit, "rev")
    return (
        task,
        auto,
        {task.instance_id: auto},
        {task.instance_id: task},
        {task.instance_id: {"calc/core.py": BUGGY}},
    )


def test_template_is_human_blank_with_edit_sites(task_record: dict[str, Any]) -> None:
    _, auto, *_ = _inputs(task_record)
    t = reviewer_template(auto, "reviewer_a")
    assert (t.status, t.annotator_kind, t.annotation_set) == ("template", "human", "reviewer_a")
    assert [u.kind for u in t.units] == ["edit_site"]
    with pytest.raises(ValueError):
        reviewer_template(t, "reviewer_b")  # only from the machine-derived set


def test_brief_contents(task_record: dict[str, Any]) -> None:
    task, auto, *_ = _inputs(task_record)
    brief = write_brief(task, auto, {"calc/core.py": BUGGY})
    for section in (
        "## Issue",
        "## Reference patch",
        "## Graded tests",
        "## Test patch (hidden from agents)",
    ):
        assert section in brief
    assert "test_add_hidden_secret_9f3a" in brief  # reviewers do see hidden tests
    assert ">>     3      return a - b" in brief  # edit-site line marked in the excerpt


def test_packages_are_blinded_and_identical(tmp_path: Path, task_record: dict[str, Any]) -> None:
    _, _, autos, tasks, files = _inputs(task_record)
    out = tmp_path / "pkgs"
    a = build_package("reviewer_a", autos, tasks, files, DOCS, out)
    b = build_package("reviewer_b", autos, tasks, files, DOCS, out)
    names = lambda r: sorted(str(p.relative_to(r)) for p in r.rglob("*") if p.is_file())  # noqa: E731
    assert names(a) == names(b) == [
        "ANNOTATION_GUIDE.md", "INSTRUCTIONS.md", "briefs/toy__calc-1.md", "templates/toy__calc-1.yaml",
    ]  # fmt: skip
    ta, tb = (
        load_annotation(a / "templates/toy__calc-1.yaml"),
        load_annotation(b / "templates/toy__calc-1.yaml"),
    )
    assert ta.model_dump(exclude={"annotation_set"}) == tb.model_dump(exclude={"annotation_set"})
    for f in out.rglob("*"):
        if f.is_file():
            assert "assistant_draft" not in f.read_text()
    with pytest.raises(FileExistsError):
        build_package("reviewer_a", autos, tasks, files, DOCS, out)


def test_packages_unreachable_from_agent_tools(tmp_path: Path, task_record: dict[str, Any]) -> None:
    _, _, autos, tasks, files = _inputs(task_record)
    build_package("reviewer_a", autos, tasks, files, DOCS, tmp_path / "review_packages")
    ws = tmp_path / "ws"
    ws.mkdir()
    tools = RepoTools(ws)
    assert tools.call(
        "read_file", {"path": "../review_packages/reviewer_a/briefs/toy__calc-1.md"}
    ).is_error
    assert tools.call("search_code", {"pattern": "hidden_secret"}).text == "no matches"


def test_repo_ignores_review_packages() -> None:
    import subprocess

    root = Path(__file__).parents[1]
    r = subprocess.run(["git", "check-ignore", "-q", "review_packages/x.md"], cwd=root, check=False)
    assert r.returncode == 0
