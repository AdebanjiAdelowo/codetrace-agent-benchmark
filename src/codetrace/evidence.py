"""Ground-truth evidence units: schema, annotation files, edit-site extraction, agreement.

Evaluator-side only. Nothing here is imported by the agent loop or tools, and the
annotation directory is never inside an agent workspace (see tests/test_evidence.py).

Annotation files live at ``annotations/<annotation_set>/<instance_id>.yaml`` where the
set is ``auto`` (machine-derived edit sites only), an annotator id, or ``adjudicated``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

ANNOTATION_SCHEMA_VERSION = 1
_AUTO_RATIONALE = "base-commit lines changed (or bracketing an insertion) by the reference patch"
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class EvidenceUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    kind: Literal["edit_site", "support"]
    criticality: Literal["required", "helpful"]
    source: Literal["auto_patch", "manual"]
    rationale: str = ""

    @model_validator(mode="after")
    def _ordered(self) -> EvidenceUnit:
        if self.end_line < self.start_line:
            raise ValueError(f"{self.id}: end_line < start_line")
        return self

    def lines(self) -> range:
        return range(self.start_line, self.end_line + 1)


class TaskAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = ANNOTATION_SCHEMA_VERSION
    instance_id: str
    dataset_revision: str
    base_commit: str
    annotation_set: str
    annotator: str
    status: Literal["auto_draft", "annotated", "adjudicated"]
    notes: list[str] = []
    units: list[EvidenceUnit]

    @model_validator(mode="after")
    def _unique_ids(self) -> TaskAnnotation:
        ids = [u.id for u in self.units]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.instance_id}: duplicate unit ids")
        return self

    @property
    def required(self) -> list[EvidenceUnit]:
        return [u for u in self.units if u.criticality == "required"]


def save_annotation(ann: TaskAnnotation, root: Path) -> Path:
    path = root / ann.annotation_set / f"{ann.instance_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(ann.model_dump(), sort_keys=False), encoding="utf-8")
    return path


def load_annotation(path: Path) -> TaskAnnotation:
    return TaskAnnotation.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_annotation_set(root: Path, annotation_set: str) -> dict[str, TaskAnnotation]:
    anns = [load_annotation(p) for p in sorted((root / annotation_set).glob("*.yaml"))]
    return {a.instance_id: a for a in anns}


# ------------------------------------------------------------------ edit sites


def edit_site_spans(patch: str) -> tuple[list[tuple[str, int, int]], list[str]]:
    """Map a unified diff onto base-commit line ranges.

    Removed lines map to themselves. A pure insertion maps to the base lines on either
    side of the insertion point (one line at the start of a file). Overlapping or adjacent
    ranges in a file are merged. Files that do not exist at the base commit have no base
    lines and are reported in the returned notes instead.
    """
    spans: dict[str, list[tuple[int, int]]] = {}
    notes: list[str] = []
    path: str | None = None
    old_line = 0
    old_end = 0
    in_hunk = False
    in_removal = False
    for raw in patch.splitlines():
        if raw.startswith("diff --git "):
            path, in_hunk = None, False
        elif raw.startswith("--- "):
            src = raw[4:].strip()
            path = None if src == "/dev/null" else src.removeprefix("a/")
            in_hunk = False
        elif raw.startswith("+++ "):
            dst = raw[4:].strip()
            if path is None and dst != "/dev/null":
                notes.append(f"new file (no base lines): {dst.removeprefix('b/')}")
        elif m := _HUNK.match(raw):
            in_hunk = True
            old_line = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            old_end = old_line + count - 1
            if count == 0:  # git uses "-N,0" for insertion after line N
                old_line += 1
            in_removal = False
        elif in_hunk and path is not None:
            if raw.startswith("-"):
                spans.setdefault(path, []).append((old_line, old_line))
                old_line += 1
                in_removal = True
            elif raw.startswith("+"):
                # Lines added right after removed lines replace them; only a pure
                # insertion gets anchor lines.
                if not in_removal:
                    before, after = old_line - 1, min(old_line, max(old_end, 1))
                    lo = max(1, before)
                    spans.setdefault(path, []).append((lo, max(lo, after)))
            elif raw.startswith(" "):
                old_line += 1
                in_removal = False
            # "\ No newline at end of file" and anything else: no line movement
    merged: list[tuple[str, int, int]] = []
    for p in sorted(spans):
        cur: tuple[int, int] | None = None
        for lo, hi in sorted(spans[p]):
            if cur and lo <= cur[1] + 1:
                cur = (cur[0], max(cur[1], hi))
            else:
                if cur:
                    merged.append((p, *cur))
                cur = (lo, hi)
        if cur:
            merged.append((p, *cur))
    return merged, notes


def auto_annotation(
    instance_id: str, patch: str, base_commit: str, dataset_revision: str
) -> TaskAnnotation:
    spans, notes = edit_site_spans(patch)
    units = [
        EvidenceUnit(
            id=f"edit{i}", path=p, start_line=lo, end_line=hi, kind="edit_site",
            criticality="required", source="auto_patch",
            rationale=_AUTO_RATIONALE,
        )
        for i, (p, lo, hi) in enumerate(spans, 1)
    ]  # fmt: skip
    return TaskAnnotation(
        instance_id=instance_id,
        dataset_revision=dataset_revision,
        base_commit=base_commit,
        annotation_set="auto",
        annotator="auto",
        status="auto_draft",
        notes=[*notes, "support units not annotated (requires human annotators)"],
        units=units,
    )


def validate_against_files(ann: TaskAnnotation, file_lengths: dict[str, int | None]) -> list[str]:
    """Check every unit's path exists at the base commit and its lines are in range.
    ``file_lengths`` maps path -> number of lines, or None if the file is absent."""
    problems = []
    for u in ann.units:
        n = file_lengths.get(u.path)
        if n is None:
            problems.append(f"{u.id}: {u.path} does not exist at base commit")
        elif u.end_line > n:
            problems.append(f"{u.id}: {u.path} has {n} lines, unit ends at {u.end_line}")
    return problems


# ------------------------------------------------------------------ agreement


def _line_set(units: list[EvidenceUnit]) -> set[tuple[str, int]]:
    return {(u.path, ln) for u in units for ln in u.lines()}


def line_f1(a: list[EvidenceUnit], b: list[EvidenceUnit]) -> dict[str, float | None]:
    """Line-level precision/recall/F1 of annotator b against annotator a (symmetric F1).
    Both empty -> F1 undefined (None)."""
    la, lb = _line_set(a), _line_set(b)
    if not la and not lb:
        return {"precision": None, "recall": None, "f1": None}
    inter = len(la & lb)
    p = inter / len(lb) if lb else 0.0
    r = inter / len(la) if la else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": p, "recall": r, "f1": f1}


def _overlap(u: EvidenceUnit, v: EvidenceUnit) -> float:
    if u.path != v.path:
        return 0.0
    inter = len(set(u.lines()) & set(v.lines()))
    return inter / len(set(u.lines()) | set(v.lines()))


def criticality_kappa(
    a: list[EvidenceUnit], b: list[EvidenceUnit], min_jaccard: float = 0.5
) -> dict[str, float | int | None]:
    """Cohen's kappa on required/helpful for units matched across annotators
    (greedy one-to-one matching by line Jaccard >= ``min_jaccard``)."""
    pairs: list[tuple[str, str]] = []
    used: set[int] = set()
    for u in a:
        best, best_j = None, min_jaccard
        for j, v in enumerate(b):
            if j in used:
                continue
            jac = _overlap(u, v)
            if jac >= best_j:
                best, best_j = j, jac
        if best is not None:
            used.add(best)
            pairs.append((u.criticality, b[best].criticality))
    n = len(pairs)
    if n == 0:
        return {"n_matched": 0, "kappa": None, "observed_agreement": None}
    po = sum(x == y for x, y in pairs) / n
    cats = ("required", "helpful")
    pe = sum(
        (sum(x == c for x, _ in pairs) / n) * (sum(y == c for _, y in pairs) / n) for c in cats
    )
    kappa = None if pe == 1.0 else (po - pe) / (1 - pe)
    return {"n_matched": n, "kappa": kappa, "observed_agreement": po}
