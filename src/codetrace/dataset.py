"""SWE-bench Lite loading, information-hiding split, and pilot task selection.

Information hiding is enforced by types: the agent only ever receives an
:class:`AgentTaskView`, which has no field for the reference patch, the test
patch, the FAIL_TO_PASS / PASS_TO_PASS lists, or hints. Those live only in
:class:`EvaluatorTask`, which the agent loop never imports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The SWE-bench organisation's copy is the one the official harness (swebench 5.0.2)
# loads; it carries the `image`, `eval_script`, and `log_parser` fields the harness
# requires. The older princeton-nlp copy lacks them. See docs/DATASET.md.
DATASET_NAME = "SWE-bench/SWE-bench_Lite"
# Hub commit pinned on 2026-10-20.
DATASET_REVISION = "b0dde1093fe417d83b7184254edf8199c1f0dff5"
SPLIT_FILES = {
    "dev": "data/dev-00000-of-00001.parquet",
    "test": "data/test-00000-of-00001.parquet",
}


@dataclass(frozen=True)
class AgentTaskView:
    """The only task information an agent is allowed to see."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str


@dataclass(frozen=True)
class EvaluatorTask:
    """Full record. Never passed to the agent loop."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    version: str
    environment_setup_commit: str
    created_at: str
    patch: str
    test_patch: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    hints_text: str

    def agent_view(self) -> AgentTaskView:
        return AgentTaskView(
            instance_id=self.instance_id,
            repo=self.repo,
            base_commit=self.base_commit,
            problem_statement=self.problem_statement,
        )


def _as_list(value: Any) -> tuple[str, ...]:
    # SWE-bench stores these as JSON-encoded strings.
    if isinstance(value, str):
        return tuple(json.loads(value)) if value else ()
    return tuple(value or ())


def record_to_task(rec: dict[str, Any]) -> EvaluatorTask:
    return EvaluatorTask(
        instance_id=rec["instance_id"],
        repo=rec["repo"],
        base_commit=rec["base_commit"],
        problem_statement=rec["problem_statement"],
        version=str(rec.get("version", "")),
        environment_setup_commit=rec.get("environment_setup_commit", ""),
        created_at=str(rec.get("created_at", "")),
        patch=rec["patch"],
        test_patch=rec["test_patch"],
        fail_to_pass=_as_list(rec.get("FAIL_TO_PASS")),
        pass_to_pass=_as_list(rec.get("PASS_TO_PASS")),
        hints_text=rec.get("hints_text", "") or "",
    )


def load_records(path: Path) -> list[dict[str, Any]]:
    """Load raw records from a local .parquet or .jsonl file."""
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        rows: list[dict[str, Any]] = pq.read_table(path).to_pylist()
        return rows
    raise ValueError(f"unsupported dataset file: {path}")


def download_split(split: str, cache_dir: Path | None = None) -> Path:
    """Download one split at the pinned revision (a few MB). Needs network."""
    from huggingface_hub import hf_hub_download

    return Path(
        hf_hub_download(
            repo_id=DATASET_NAME,
            repo_type="dataset",
            filename=SPLIT_FILES[split],
            revision=DATASET_REVISION,
            cache_dir=cache_dir,
        )
    )


def export_for_evaluator(source: Path, instance_ids: list[str], dest: Path) -> int:
    """Write the full pinned records for ``instance_ids`` as JSONL for the official
    harness (``--dataset_name <file>.jsonl``), so evaluation cannot silently use a
    newer Hub revision. Contains reference and test patches: evaluator side only."""
    wanted = set(instance_ids)
    rows = [r for r in load_records(source) if r["instance_id"] in wanted]
    if {r["instance_id"] for r in rows} != wanted:
        raise ValueError(
            f"instances missing from {source}: {sorted(wanted - {r['instance_id'] for r in rows})}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    return len(rows)


def load_tasks(path: Path) -> dict[str, EvaluatorTask]:
    tasks = [record_to_task(r) for r in load_records(path)]
    by_id = {t.instance_id: t for t in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("duplicate instance_id in dataset")
    return by_id


# --------------------------------------------------------------------------- selection


@dataclass(frozen=True)
class PatchStats:
    files_changed: int
    hunks: int
    lines_added: int
    lines_removed: int

    @property
    def lines_changed(self) -> int:
        return self.lines_added + self.lines_removed


def patch_stats(patch: str) -> PatchStats:
    files = hunks = added = removed = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return PatchStats(files, hunks, added, removed)


@dataclass(frozen=True)
class SelectionCriteria:
    """Deterministic pilot selection. Uses reference-patch size as a difficulty and
    environment-cost proxy; this biases the pilot toward small, localized fixes and
    is reported as a limitation."""

    repos: tuple[str, ...]
    max_files_changed: int = 1
    max_hunks: int = 2
    max_lines_changed: int = 12
    max_fail_to_pass: int = 3
    per_repo: int = 1
    n_tasks: int = 3


def select_tasks(
    tasks: dict[str, EvaluatorTask], criteria: SelectionCriteria
) -> list[tuple[EvaluatorTask, PatchStats]]:
    chosen: list[tuple[EvaluatorTask, PatchStats]] = []
    for repo in criteria.repos:  # repo order in the criteria is the priority order
        candidates = []
        for t in tasks.values():
            if t.repo != repo:
                continue
            s = patch_stats(t.patch)
            if (
                s.files_changed <= criteria.max_files_changed
                and s.hunks <= criteria.max_hunks
                and s.lines_changed <= criteria.max_lines_changed
                and 0 < len(t.fail_to_pass) <= criteria.max_fail_to_pass
            ):
                candidates.append((t, s))
        candidates.sort(key=lambda ts: (ts[1].lines_changed, ts[0].instance_id))
        chosen.extend(candidates[: criteria.per_repo])
        if len(chosen) >= criteria.n_tasks:
            break
    return chosen[: criteria.n_tasks]
