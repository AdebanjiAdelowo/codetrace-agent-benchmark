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
    assert "cd evaluation" in script
    manifest = json.loads((exp / "experiment_manifest.json").read_text())
    assert manifest["dataset_revision"] and manifest["instance_ids"] == ["toy__calc-1"]
    assert manifest["codetrace_git_commit"] is None or len(manifest["codetrace_git_commit"]) == 40
    # The same commit is in every run manifest (with "+dirty" when the tree had changes).
    for t in traces:
        run_commit = read_trace(t)[0].payload["manifest"]["codetrace_git_commit"]
        if manifest["codetrace_git_commit"] is None:
            assert run_commit is None
        else:
            assert run_commit.startswith(manifest["codetrace_git_commit"])
            assert run_commit.endswith("+dirty") == bool(manifest["codetrace_git_dirty"])
    # An experiment directory is never reused.
    with pytest.raises(SystemExit, match="already holds"):
        main(["run", "--config", str(cfg), "--runs-dir", str(runs)])
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
    import codetrace.cli as cli

    monkeypatch.setenv("CODETRACE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(cli, "git_state", lambda: {"commit": None, "dirty": None})
    cfg = _write_cfg(tmp_path, dataset_jsonl, upstream_repo[0], provider="anthropic")
    with pytest.raises(SystemExit, match="--allow-paid"):
        main(["run", "--config", str(cfg), "--runs-dir", str(tmp_path / "runs")])


@pytest.mark.parametrize(
    ("state", "extra", "refused"),
    [
        ({"commit": "a" * 40, "dirty": True}, [], True),
        ({"commit": None, "dirty": None}, [], True),
        ({"commit": "a" * 40, "dirty": True}, ["--allow-dirty"], False),
    ],
)
def test_paid_run_requires_clean_commit(
    tmp_path: Path,
    dataset_jsonl: Path,
    upstream_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, object],
    extra: list[str],
    refused: bool,
) -> None:
    import codetrace.cli as cli

    monkeypatch.setenv("CODETRACE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(cli, "git_state", lambda: state)
    cfg = _write_cfg(tmp_path, dataset_jsonl, upstream_repo[0], provider="anthropic")
    argv = [
        "run",
        "--config",
        str(cfg),
        "--runs-dir",
        str(tmp_path / "runs"),
        "--allow-paid",
        *extra,
    ]
    if refused:
        with pytest.raises(SystemExit, match="dirty"):
            main(argv)
    else:
        # Passes the git guard; stops at model construction (no API key -> no call is made).
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(
            "codetrace.models.anthropic_client.AnthropicModel.__init__",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model constructed")),
        )
        with pytest.raises(RuntimeError, match="model constructed"):
            main(argv)


def test_collect_eval_cli(
    tmp_path: Path,
    dataset_jsonl: Path,
    upstream_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODETRACE_DATA_DIR", str(tmp_path / "data"))
    cfg = _write_cfg(tmp_path, dataset_jsonl, upstream_repo[0])
    runs = tmp_path / "runs"
    assert main(["run", "--config", str(cfg), "--runs-dir", str(runs)]) == 0
    exp = runs / "cli_test"
    keys = ("submitted_ids", "completed_ids", "incomplete_ids", "empty_patch_ids", "resolved_ids",
            "unresolved_ids", "error_ids", "infra_failure_ids", "ambiguous_failure_ids")  # fmt: skip
    for seed in (0, 1):
        run_id = f"cli_test__A_baseline__s{seed}"
        summary = {"schema_version": 2, **{k: [] for k in keys}, "failure_reasons": {},
                   "submitted_ids": ["toy__calc-1"], "empty_patch_ids": ["toy__calc-1"]}  # fmt: skip
        (exp / "evaluation").mkdir(exist_ok=True)
        (exp / "evaluation" / f"m.{run_id}.json").write_text(json.dumps(summary))
    assert main(["collect-eval", "--experiment-dir", str(exp)]) == 0
    rows = [json.loads(x) for x in (exp / "evaluation" / "results.jsonl").read_text().splitlines()]
    assert [(r["seed"], r["category"]) for r in rows] == [(0, "empty_patch"), (1, "empty_patch")]


def test_annotate_and_analyze_cli(
    tmp_path: Path,
    dataset_jsonl: Path,
    upstream_repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from codetrace.workspace import ensure_mirror

    repo, base = upstream_repo
    data = tmp_path / "data"
    monkeypatch.setenv("CODETRACE_DATA_DIR", str(data))
    ensure_mirror(str(repo), data / "mirrors" / "toy__calc", commits=[base])  # offline seed
    anns = tmp_path / "annotations"
    init = ["annotate", "init", "--instance-ids", "toy__calc-1", "--annotations-dir", str(anns)]
    assert main([*init, "--dataset", str(dataset_jsonl)]) == 0
    ann_file = anns / "auto" / "toy__calc-1.yaml"
    with pytest.raises(SystemExit, match="exists"):
        main([*init, "--dataset", str(dataset_jsonl)])
    # The toy dataset revision is not the pinned one, so validate reports exactly that.
    assert main(["annotate", "validate", "--check-files", "--repo", "toy/calc", str(ann_file)]) == 1
    assert "dataset_revision" in capsys.readouterr().out
    assert main(["annotate", "agreement", str(ann_file), str(ann_file)]) == 0
    assert '"f1": 1.0' in capsys.readouterr().out

    cfg = _write_cfg(tmp_path, dataset_jsonl, repo)
    runs = tmp_path / "runs"
    assert main(["run", "--config", str(cfg), "--runs-dir", str(runs)]) == 0
    exp = runs / "cli_test"
    az = [
        "analyze",
        "--experiment-dir",
        str(exp),
        "--annotations-dir",
        str(anns),
        "--annotation-set",
        "auto",
    ]
    with pytest.raises(SystemExit, match="adjudicated"):
        main(az)
    assert main([*az, "--allow-provisional", "--tau", "any"]) == 0
    summary = json.loads((exp / "analysis" / "summary_auto_tauany.json").read_text())
    assert summary["provisional"] is True
    assert summary["per_config"]["A_baseline"]["n_runs"] == 2
    rows = [
        json.loads(x)
        for x in (exp / "analysis" / "metrics_auto_tauany.jsonl").read_text().splitlines()
    ]
    # The heuristic explorer reads calc/core.py around its search hit and submits no edit.
    assert all(r["undefined_reason"] == "no_patch" and r["discovery_recall"] == 1.0 for r in rows)
