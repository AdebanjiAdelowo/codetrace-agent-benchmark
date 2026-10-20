from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from codetrace.evidence import (
    EvidenceUnit,
    auto_annotation,
    criticality_kappa,
    edit_site_spans,
    line_f1,
    load_annotation,
    load_annotation_set,
    save_annotation,
    validate_against_files,
)
from codetrace.tools import RepoTools

from .conftest import REFERENCE_PATCH


def _diff(path: str, header: str, body: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n{header}\n{body}"


def test_replacement_maps_to_removed_line() -> None:
    assert edit_site_spans(REFERENCE_PATCH) == ([("calc/core.py", 3, 3)], [])


def test_pure_insertion_middle_gets_two_anchors() -> None:
    p = _diff("f.py", "@@ -10,4 +10,5 @@", " a\n b\n+new\n c\n d\n")
    assert edit_site_spans(p)[0] == [("f.py", 11, 12)]


def test_insertion_at_end_of_hunk_and_file_start() -> None:
    eof = _diff("f.py", "@@ -1169,3 +1169,4 @@", " x\n y\n z\n+tail\n")
    assert edit_site_spans(eof)[0] == [("f.py", 1171, 1171)]
    start = _diff("f.py", "@@ -1,2 +1,3 @@", "+head\n a\n b\n")
    assert edit_site_spans(start)[0] == [("f.py", 1, 1)]


def test_zero_context_insertion() -> None:
    p = _diff("f.py", "@@ -5,0 +6,2 @@", "+x\n+y\n")
    assert edit_site_spans(p)[0] == [("f.py", 5, 5)]


def test_multi_hunk_multi_file_merge_and_new_file() -> None:
    p = (
        _diff("a.py", "@@ -1,5 +1,5 @@", " l1\n-l2\n-l3\n+L2\n l4\n l5\n")
        + "@@ -20,3 +20,2 @@\n l20\n-l21\n l22\n"
        + "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,1 @@\n+x\n"
        + _diff("b.py", "@@ -7,3 +7,3 @@", "-q\n-r\n+Q\n s\n")
    )
    spans, notes = edit_site_spans(p)
    assert spans == [("a.py", 2, 3), ("a.py", 21, 21), ("b.py", 7, 8)]
    assert notes == ["new file (no base lines): new.py"]


def test_auto_annotation_roundtrip(tmp_path: Path) -> None:
    ann = auto_annotation("toy__calc-1", REFERENCE_PATCH, "abc", "rev")
    assert ann.status == "auto_draft" and ann.annotation_set == "auto"
    assert [(u.id, u.kind, u.criticality, u.source) for u in ann.units] == [
        ("edit1", "edit_site", "required", "auto_patch")
    ]
    assert any("support units not annotated" in n for n in ann.notes)
    path = save_annotation(ann, tmp_path)
    assert path == tmp_path / "auto" / "toy__calc-1.yaml"
    assert load_annotation(path) == ann
    assert load_annotation_set(tmp_path, "auto") == {"toy__calc-1": ann}


def test_schema_rejects_bad_units() -> None:
    with pytest.raises(ValidationError):
        EvidenceUnit(id="x", path="a", start_line=5, end_line=4, kind="support",
                     criticality="required", source="manual")  # fmt: skip
    with pytest.raises(ValidationError):
        EvidenceUnit(id="x", path="a", start_line=1, end_line=1, kind="other",  # type: ignore[arg-type]
                     criticality="required", source="manual")  # fmt: skip
    ann = auto_annotation("t", REFERENCE_PATCH, "abc", "rev")
    with pytest.raises(ValidationError, match="duplicate"):
        type(ann).model_validate({**ann.model_dump(), "units": [ann.units[0].model_dump()] * 2})


def test_validate_against_files() -> None:
    ann = auto_annotation("t", REFERENCE_PATCH, "abc", "rev")
    assert validate_against_files(ann, {"calc/core.py": 7}) == []
    assert "has 2 lines" in validate_against_files(ann, {"calc/core.py": 2})[0]
    assert "does not exist" in validate_against_files(ann, {"calc/core.py": None})[0]


def _u(i: str, lo: int, hi: int, crit: str = "required", path: str = "a.py") -> EvidenceUnit:
    return EvidenceUnit(id=i, path=path, start_line=lo, end_line=hi, kind="support",
                        criticality=crit, source="manual")  # type: ignore[arg-type]  # fmt: skip


def test_line_f1() -> None:
    a = [_u("1", 1, 4)]
    assert line_f1(a, a)["f1"] == 1.0
    r = line_f1(a, [_u("2", 3, 6)])  # 2 of 4 lines shared each way
    assert (r["precision"], r["recall"], r["f1"]) == (0.5, 0.5, 0.5)
    assert line_f1([], [])["f1"] is None
    assert line_f1(a, [])["f1"] == 0.0


def test_criticality_kappa() -> None:
    a = [_u("1", 1, 2), _u("2", 10, 12), _u("3", 20, 21, "helpful"), _u("4", 30, 30, "helpful")]
    b = [
        _u("1", 1, 2),
        _u("2", 10, 12, "helpful"),
        _u("3", 20, 21, "helpful"),
        _u("4", 30, 30, "helpful"),
    ]
    k = criticality_kappa(a, b)
    # po = 3/4; pe = (2/4 * 1/4) + (2/4 * 3/4) = 0.5; kappa = (0.75 - 0.5) / 0.5 = 0.5
    assert (
        k["n_matched"] == 4 and k["observed_agreement"] == 0.75 and k["kappa"] == pytest.approx(0.5)
    )
    assert criticality_kappa(a, [_u("x", 100, 101)])["kappa"] is None  # nothing matched
    assert criticality_kappa([_u("1", 1, 2)], [_u("1", 1, 2)])["kappa"] is None  # pe == 1


# ------------------------------------------------------------------ isolation

AGENT_SIDE = ("agent.py", "tools.py", "workspace.py", "models/base.py",
              "models/scripted.py", "models/anthropic_client.py")  # fmt: skip
EVALUATOR_SIDE = {
    "codetrace.evidence",
    "codetrace.metrics",
    "codetrace.validation",
    "codetrace.evaluation",
}


def test_agent_side_never_imports_evaluator_side() -> None:
    src = Path(__file__).parents[1] / "src" / "codetrace"
    for rel in AGENT_SIDE:
        tree = ast.parse((src / rel).read_text())
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not (imported & EVALUATOR_SIDE), (rel, imported & EVALUATOR_SIDE)


def test_annotations_unreachable_from_agent_tools(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n")
    save_annotation(auto_annotation("t", REFERENCE_PATCH, "abc", "rev"), tmp_path / "annotations")
    tools = RepoTools(ws)
    assert tools.call("read_file", {"path": "../annotations/auto/t.yaml"}).is_error
    assert tools.call("search_code", {"pattern": "edit_site"}).text == "no matches"
