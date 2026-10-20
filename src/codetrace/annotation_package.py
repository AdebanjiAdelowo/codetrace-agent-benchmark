"""Blinded review packages for independent human evidence annotation.

Each reviewer receives a standalone directory containing only:

- ``INSTRUCTIONS.md`` (docs/REVIEWER_INSTRUCTIONS.md) and ``ANNOTATION_GUIDE.md``;
- ``templates/<instance_id>.yaml``: their own template (status ``template``, human
  annotator, machine-derived edit sites pre-filled for confirmation, no support units);
- ``briefs/<instance_id>.md``: issue text, reference patch, graded tests, the test
  patch, and base-commit excerpts around each edit site.

Packages never contain the assistant draft or another reviewer's work, so reviewers
annotate blind. Briefs contain benchmark content and hidden tests: packages are written
outside the repository's tracked files (``review_packages/`` is git-ignored) and never
inside an agent workspace. Evaluator side only.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from codetrace.dataset import EvaluatorTask
from codetrace.evidence import TaskAnnotation, save_annotation

EXCERPT_CONTEXT = 15


def reviewer_template(auto: TaskAnnotation, reviewer: str) -> TaskAnnotation:
    if auto.annotation_set != "auto":
        raise ValueError("templates are built from the machine-derived 'auto' set")
    return TaskAnnotation(
        instance_id=auto.instance_id,
        dataset_revision=auto.dataset_revision,
        base_commit=auto.base_commit,
        annotation_set=reviewer,
        annotator="TO_FILL",
        annotator_kind="human",
        status="template",
        notes=[
            "Confirm or correct each machine-derived edit site, then add support units "
            "following ANNOTATION_GUIDE.md. Set annotator and status: annotated when done.",
        ],
        units=[u.model_copy() for u in auto.units],
    )


def write_brief(task: EvaluatorTask, auto: TaskAnnotation, base_files: dict[str, str]) -> str:
    """Markdown brief for one task. ``base_files`` maps path -> base-commit file text."""
    out = [
        f"# Review brief: {task.instance_id}",
        "",
        f"- Repository: `{task.repo}` at base commit `{task.base_commit}`",
        f"- Dataset revision: `{auto.dataset_revision}`",
        "- Confidential evaluator material: do not share with any agent or paste into a model.",
        "",
        "## Issue",
        "",
        task.problem_statement.strip(),
        "",
        "## Reference patch",
        "",
        "```diff",
        task.patch.rstrip(),
        "```",
        "",
        "## Graded tests",
        "",
        "FAIL_TO_PASS:",
        *[f"- `{t}`" for t in task.fail_to_pass],
        "",
        f"PASS_TO_PASS: {len(task.pass_to_pass)} tests (regression guard).",
        "",
        "## Test patch (hidden from agents)",
        "",
        "```diff",
        task.test_patch.rstrip(),
        "```",
        "",
        "## Base-commit excerpts around machine-derived edit sites",
        "",
    ]
    for u in auto.units:
        lines = base_files[u.path].splitlines()
        lo = max(1, u.start_line - EXCERPT_CONTEXT)
        hi = min(len(lines), u.end_line + EXCERPT_CONTEXT)
        out += [
            f"### `{u.path}` lines {lo}-{hi} (edit site {u.id}: {u.start_line}-{u.end_line})",
            "",
            "```",
        ]
        for n in range(lo, hi + 1):
            marker = ">>" if u.start_line <= n <= u.end_line else "  "
            out.append(f"{marker}{n:>6}  {lines[n - 1]}")
        out += ["```", ""]
    out += [
        "Read further in the repository at the base commit as needed "
        "(`git show <base_commit>:<path>`); line numbers refer to that commit.",
        "",
    ]
    return "\n".join(out)


def build_package(
    reviewer: str,
    autos: dict[str, TaskAnnotation],
    tasks: dict[str, EvaluatorTask],
    base_files: dict[str, dict[str, str]],
    docs_dir: Path,
    out_dir: Path,
) -> Path:
    """Write ``<out_dir>/<reviewer>/`` and return it. Refuses to overwrite."""
    root = out_dir / reviewer
    if root.exists():
        raise FileExistsError(f"{root} exists; remove it deliberately to rebuild")
    (root / "briefs").mkdir(parents=True)
    shutil.copy(docs_dir / "REVIEWER_INSTRUCTIONS.md", root / "INSTRUCTIONS.md")
    shutil.copy(docs_dir / "ANNOTATION_GUIDE.md", root / "ANNOTATION_GUIDE.md")
    for iid, auto in sorted(autos.items()):
        template = reviewer_template(auto, reviewer)
        saved = save_annotation(template, root)  # -> root/<reviewer>/<iid>.yaml
        (root / "templates").mkdir(exist_ok=True)
        saved.rename(root / "templates" / f"{iid}.yaml")
        (root / "briefs" / f"{iid}.md").write_text(
            write_brief(tasks[iid], auto, base_files[iid]), encoding="utf-8"
        )
    (root / reviewer).rmdir()
    return root
