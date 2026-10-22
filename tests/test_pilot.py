"""Frozen pilot orchestration with synthetic tasks, canned API and mocked evaluation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("anthropic")

from codetrace.cli import main
from codetrace.freeze import check_freeze, write_freeze
from codetrace.pilot import run_pilot
from tests.test_anthropic_adapter import _message


@pytest.fixture
def pilot_setup(
    tmp_path: Path,
    upstream_repo: tuple[Path, str],
    dataset_jsonl: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path]:
    repo, _ = upstream_repo
    cfg = {
        "experiment_id": "toy_pilot",
        "instance_ids": ["toy__calc-1"],
        "dataset_path": str(dataset_jsonl),
        "repo_source_dir": str(repo.parent),
        "model": {"provider": "anthropic", "name": "claude-opus-5-5", "max_tokens": 1000},
        "budget": {"max_cost_usd": 5},
    }
    config = tmp_path / "experiment.yaml"
    config.write_text(yaml.safe_dump(cfg))
    response = tmp_path / "responses.json"
    calls = [
        ("read_file", {"path": "calc/core.py"}),
        (
            "replace_in_file",
            {"path": "calc/core.py", "old_text": "return a - b", "new_text": "return a + b"},
        ),
        ("submit", {}),
    ]
    response.write_text(
        json.dumps(
            [
                {
                    "status": 200,
                    "body": _message(
                        [
                            {"type": "tool_use", "id": f"t{i}", "name": n, "input": a},
                        ]
                    ),
                }
                for i, (n, a) in enumerate(calls)
            ]
        )
    )
    monkeypatch.setenv("CODETRACE_DATA_DIR", str(tmp_path / "data"))
    return config, response, tmp_path / "runs"


def test_frozen_simulation_end_to_end(pilot_setup: tuple[Path, Path, Path]) -> None:
    config, response, runs = pilot_setup
    frozen = write_freeze(config)
    assert check_freeze(config) == frozen
    assert (
        main(
            ["pilot", "--config", str(config), "--runs-dir", str(runs), "--simulate", str(response)]
        )
        == 0
    )
    out = next(runs.iterdir())
    summary = json.loads((out / "summary.json").read_text())
    assert summary["mode"] == "offline_simulation" and not summary["research_results"]
    assert summary["verified"]["run"] and not summary["executed"]["official_evaluation"]
    assert summary["evaluation_status"] == "PENDING"
    assert summary["run"]["patch_bytes"] > 0
    assert "actual API spend is zero" in summary["cost_note"]
    assert (out / "evaluate.sh").exists() and (out / "predictions.jsonl").exists()
    trace = next(out.glob("runs/*/trace.jsonl"))
    assert "test_add_hidden_secret_9f3a" not in trace.read_text()
    assert "HINT_SECRET" not in trace.read_text()
    assert main(["verify-run", "--run-dir", str(trace.parent)]) == 0


@pytest.mark.parametrize("drift", ["config", "dataset", "freeze"])
def test_drift_rejected_before_run(pilot_setup: tuple[Path, Path, Path], drift: str) -> None:
    config, response, runs = pilot_setup
    write_freeze(config)
    if drift == "config":
        cfg = yaml.safe_load(config.read_text())
        cfg["budget"]["max_cost_usd"] = 6
        config.write_text(yaml.safe_dump(cfg))
    elif drift == "dataset":
        dataset = Path(yaml.safe_load(config.read_text())["dataset_path"])
        row = json.loads(dataset.read_text())
        row["problem_statement"] = "changed"
        dataset.write_text(json.dumps(row))
    else:
        frozen_path = config.parent / "FROZEN.json"
        data = json.loads(frozen_path.read_text())
        data["sha256"] = "bad"
        frozen_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=r"drift|digest"):
        run_pilot(config, runs, simulation=response)
    assert not runs.exists()


def test_freeze_does_not_overwrite(pilot_setup: tuple[Path, Path, Path]) -> None:
    config, _, _ = pilot_setup
    write_freeze(config)
    with pytest.raises(FileExistsError):
        write_freeze(config)


def test_paid_gate_precedes_every_side_effect(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="allow-paid"):
        run_pilot(tmp_path / "nonexistent", tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_real_pilot_requires_clean_commit(
    pilot_setup: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _, runs = pilot_setup
    write_freeze(config)
    monkeypatch.setattr("codetrace.pilot.git_state", lambda: {"commit": "abc", "dirty": True})
    with pytest.raises(ValueError, match="clean committed"):
        run_pilot(config, runs, allow_paid=True)
    assert not runs.exists()


def test_failed_preflight_is_persisted_without_api_call(
    pilot_setup: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, response, runs = pilot_setup
    write_freeze(config)

    def fail(*args: Any) -> Any:
        raise RuntimeError("daemon unavailable")

    monkeypatch.setattr("codetrace.pilot.docker_preflight", fail)
    with pytest.raises(RuntimeError, match="daemon unavailable"):
        run_pilot(config, runs, simulation=response, evaluate_docker=True)
    summary = json.loads(next(runs.glob("*/summary.json")).read_text())
    assert not summary["executed"]["agent"]
    assert summary["evaluation_status"] == "PENDING" and summary["pipeline_status"] == "failed"


def test_simulation_through_mocked_evaluator_and_real_crosscheck(
    pilot_setup: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mocked evaluator output tests wiring only, never claims real Docker execution."""
    config, response, runs = pilot_setup
    cfg = yaml.safe_load(config.read_text())
    cfg["eval_image"] = "mock/image:latest"
    config.write_text(yaml.safe_dump(cfg))
    dataset = Path(cfg["dataset_path"])
    record = json.loads(dataset.read_text())
    record["image"] = cfg["eval_image"]
    record["FAIL_TO_PASS"] = json.loads(record["FAIL_TO_PASS"])
    record["PASS_TO_PASS"] = json.loads(record["PASS_TO_PASS"])
    dataset.write_text(json.dumps(record))
    write_freeze(config)
    monkeypatch.setattr("codetrace.pilot.docker_preflight", lambda *args: {"mocked": True})
    original = subprocess.run

    def fake_evaluator(cmd: list[str], **kwargs: Any) -> Any:
        if "swebench.harness.run_evaluation" not in cmd:
            return original(cmd, **kwargs)
        from tests.test_evaluation import _summary

        root = Path(kwargs["cwd"])
        run_id = cmd[cmd.index("--run_id") + 1]
        iid = "toy__calc-1"
        rp = root / "logs/run_evaluation" / run_id / "model" / iid
        rp.mkdir(parents=True)
        report = {
            iid: {
                "resolved": True,
                "patch_successfully_applied": True,
                "patch_exists": True,
                "tests_status": {
                    k: {"success": record[k], "failure": []}
                    for k in ("FAIL_TO_PASS", "PASS_TO_PASS")
                },
            }
        }
        (rp / "report.json").write_text(json.dumps(report))
        (rp / "test_output.txt").write_text(
            "\n".join("PASSED " + n for k in ("FAIL_TO_PASS", "PASS_TO_PASS") for n in record[k])
        )
        (root / f"model.{run_id}.json").write_text(
            json.dumps(_summary(submitted_ids=[iid], resolved_ids=[iid]))
        )
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("codetrace.pilot.subprocess.run", fake_evaluator)
    result = run_pilot(config, runs, simulation=response, evaluate_docker=True)
    assert result["mode"] == "offline_simulation"
    assert result["verified"] == {"run": True, "evaluation": True}
    assert result["outcomes"][0]["resolved"]


@pytest.mark.parametrize(
    "condition", ["disk", "daemon", "digest", "architecture", "version", "missing_image"]
)
def test_docker_preflight_refuses_invalid_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, condition: str
) -> None:
    from collections import namedtuple

    from codetrace.pilot import docker_preflight

    monkeypatch.setattr(
        "codetrace.pilot.version", lambda _: "other" if condition == "version" else "5.0.2"
    )
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(
        "codetrace.pilot.shutil.disk_usage",
        lambda _: usage(100, 1, 1 if condition == "disk" else 20 * 1024**3),
    )

    def respond(cmd: list[str], **kwargs: Any) -> Any:
        if condition == "daemon":
            raise subprocess.CalledProcessError(1, cmd)
        if cmd[1] == "info":
            return subprocess.CompletedProcess(cmd, 0, stdout='{"MemTotal": 8000000000}')
        body = [
            {
                "Id": "wrong" if condition == "digest" else "sha256:abc",
                "Architecture": "arm64" if condition == "architecture" else "amd64",
            }
        ]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body))

    monkeypatch.setattr("codetrace.pilot.subprocess.run", respond)
    with pytest.raises((RuntimeError, ValueError, subprocess.CalledProcessError)):
        docker_preflight(None if condition == "missing_image" else "image", "sha256:abc", tmp_path)


def test_source_drift_rejected(
    pilot_setup: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutate a miniature source root; never change the actual checkout during tests."""
    from codetrace.freeze import ROOT

    config, _, _ = pilot_setup
    root = tmp_path / "source"
    for relative in ("src/codetrace/agent.py", "uv.lock", "pyproject.toml", "docs/PROTOCOL.md"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    monkeypatch.setattr("codetrace.freeze.ROOT", root)
    write_freeze(config)
    (root / "src/codetrace/agent.py").write_text("changed")
    with pytest.raises(ValueError, match="drift"):
        check_freeze(config)


def test_real_attempt_guard_survives_failure_and_different_output_root(
    pilot_setup: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _, runs = pilot_setup
    cfg = yaml.safe_load(config.read_text())
    cfg["eval_image"] = "mock/image:latest"
    config.write_text(yaml.safe_dump(cfg))
    dataset = Path(cfg["dataset_path"])
    record = json.loads(dataset.read_text())
    record["image"] = cfg["eval_image"]
    dataset.write_text(json.dumps(record))
    write_freeze(config)
    monkeypatch.setattr("codetrace.pilot.git_state", lambda: {"commit": "abc", "dirty": False})
    monkeypatch.setattr("codetrace.pilot.docker_preflight", lambda *args: {"mocked": True})
    constructed = []

    def fail_constructor(**kwargs: Any) -> Any:
        constructed.append(1)
        raise RuntimeError("mock credential failure; no HTTP request made")

    monkeypatch.setattr("codetrace.pilot.AnthropicModel", fail_constructor)
    with pytest.raises(RuntimeError, match="credential failure"):
        run_pilot(config, runs, allow_paid=True)
    marker = config.parent / "REAL_ATTEMPT.json"
    before = marker.read_bytes()
    with pytest.raises(ValueError, match="real attempt already claimed"):
        run_pilot(config, runs.parent / "other_runs", allow_paid=True)
    assert constructed == [1] and marker.read_bytes() == before


def test_concurrent_attempt_marker_stops_model_construction(
    pilot_setup: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty exclusive marker from a killed process still reserves the attempt.
    config, _, runs = pilot_setup
    cfg = yaml.safe_load(config.read_text())
    cfg["eval_image"] = "mock/image:latest"
    config.write_text(yaml.safe_dump(cfg))
    dataset = Path(cfg["dataset_path"])
    record = json.loads(dataset.read_text())
    record["image"] = cfg["eval_image"]
    dataset.write_text(json.dumps(record))
    write_freeze(config)
    (config.parent / "REAL_ATTEMPT.json").touch()
    monkeypatch.setattr("codetrace.pilot.git_state", lambda: {"commit": "abc", "dirty": False})
    monkeypatch.setattr("codetrace.pilot.docker_preflight", lambda *args: {})
    with pytest.raises(ValueError, match="already claimed"):
        run_pilot(config, runs, allow_paid=True)


def test_interrupted_pilot_cannot_restart_paid_execution(
    pilot_setup: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from codetrace.config import load_config
    from codetrace.models.simulation import simulated_model

    config, response, runs = pilot_setup
    cfg = yaml.safe_load(config.read_text())
    cfg["eval_image"] = "mock/image:latest"
    config.write_text(yaml.safe_dump(cfg))
    dataset = Path(cfg["dataset_path"])
    record = json.loads(dataset.read_text())
    record["image"] = cfg["eval_image"]
    dataset.write_text(json.dumps(record))
    write_freeze(config)
    monkeypatch.setattr("codetrace.pilot.git_state", lambda: {"commit": "abc", "dirty": False})
    monkeypatch.setattr("codetrace.pilot.docker_preflight", lambda *args: {"mocked": True})
    attempts = []
    model = simulated_model(load_config(config).model, response)

    def interrupt(*args: Any, **kwargs: Any) -> Any:
        attempts.append(1)
        raise KeyboardInterrupt

    model.complete = interrupt  # type: ignore[method-assign]
    monkeypatch.setattr("codetrace.pilot.AnthropicModel", lambda **kwargs: model)
    with pytest.raises(KeyboardInterrupt):
        run_pilot(config, runs, allow_paid=True)
    marker = config.parent / "REAL_ATTEMPT.json"
    original = marker.read_bytes()
    summary = json.loads(next(runs.glob("*/summary.json")).read_text())
    assert summary["pipeline_status"] == "interrupted"
    assert summary["run"]["exit_status"] == "interrupted"
    assert not summary["run"]["billing_complete"]
    assert summary["verified"]["run"]
    with pytest.raises(ValueError, match="already claimed"):
        run_pilot(config, runs.parent / "retry_output", allow_paid=True)
    assert attempts == [1] and marker.read_bytes() == original
