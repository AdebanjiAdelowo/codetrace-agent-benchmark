"""Offline end-to-end test of `codetrace run` with a local dataset and local repo source."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from codetrace.cli import main
from codetrace.trace import read_trace, validate_trace


def _write_cfg(tmp_path: Path, dataset: Path, upstream: Path, provider: str = "heuristic") -> Path:
    cfg = {
        "experiment_id": "cli_test",
        "instance_ids": ["toy__calc-1"],
        "seeds": [0, 1],
        "model": {"provider": provider},
        "budget": {"max_steps": 5},
        "dataset_path": str(dataset),
        "repo_source_dir": str(upstream.parent),
    }
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def test_cli_run_offline(
    tmp_path: Path,
    dataset_jsonl: Path,
    upstream_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = upstream_repo
    monkeypatch.setenv("CODETRACE_DATA_DIR", str(tmp_path / "data"))
    cfg = _write_cfg(tmp_path, dataset_jsonl, repo)
    runs = tmp_path / "runs"
    assert main(["run", "--config", str(cfg), "--runs-dir", str(runs)]) == 0

    exp = runs / "cli_test"
    traces = sorted(exp.glob("runs/*/trace.jsonl"))
    assert len(traces) == 2
    for t in traces:
        assert validate_trace(read_trace(t)) == []
    for seed in (0, 1):
        pred = [
            json.loads(x)
            for x in (exp / "predictions" / f"A_baseline__s{seed}.jsonl").read_text().splitlines()
        ]
        assert [p["instance_id"] for p in pred] == ["toy__calc-1"]
        assert (
            pred[0]["model_name_or_path"]
            == f"codetrace__A_baseline__s{seed}__heuristic-explorer-v1"
        )
    script = (exp / "evaluate.sh").read_text()
    assert script.count("swebench.harness.run_evaluation") == 2
    eval_ds = exp / "evaluator_dataset.jsonl"
    assert str(eval_ds.resolve()) in script
    assert [json.loads(x)["instance_id"] for x in eval_ds.read_text().splitlines()] == [
        "toy__calc-1"
    ]
    assert main(["validate-trace", *map(str, traces)]) == 0


def test_cli_refuses_paid_without_flag(
    tmp_path: Path,
    dataset_jsonl: Path,
    upstream_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODETRACE_DATA_DIR", str(tmp_path / "data"))
    cfg = _write_cfg(tmp_path, dataset_jsonl, upstream_repo[0], provider="anthropic")
    with pytest.raises(SystemExit, match="--allow-paid"):
        main(["run", "--config", str(cfg), "--runs-dir", str(tmp_path / "runs")])
