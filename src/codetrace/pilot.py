"""Single-run frozen pilot orchestration; simulation uses a mock HTTP transport.

Docker evaluation is opt-in in simulation. Real pilots require an existing matching
image and running daemon before any model call. This module never starts Docker,
pulls images, or provisions compute. A failed grade is distinct from a failed pipeline.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Any

from codetrace.agent import run_agent
from codetrace.config import data_dir, load_config
from codetrace.crosscheck import check_run
from codetrace.dataset import download_split, export_for_evaluator, load_tasks
from codetrace.env_info import environment_info, git_state
from codetrace.evaluation import (
    collect_evaluation,
    evaluator_command,
    shell_join,
    write_predictions,
)
from codetrace.freeze import check_freeze
from codetrace.models.anthropic_client import AnthropicModel
from codetrace.models.simulation import simulated_model
from codetrace.trace import utc_now
from codetrace.verify import verify_run
from codetrace.workspace import create_workspace, ensure_mirror, repo_url


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def docker_preflight(image: str | None, image_id: str | None, out: Path) -> dict[str, Any]:
    if not image or not image_id or not image_id.startswith("sha256:"):
        raise ValueError("Docker evaluation requires a frozen image and full image id")
    if version("swebench") != "5.0.2":
        raise ValueError("pilot requires swebench==5.0.2")
    free = shutil.disk_usage(out).free
    if free < 15 * 1024**3:
        raise RuntimeError("Docker evaluation PENDING: less than 15 GiB disk free")
    info = subprocess.run(
        ["docker", "info", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    daemon = json.loads(info.stdout)
    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    local = json.loads(inspect.stdout)[0]
    if local["Id"] != image_id or local["Architecture"] != "amd64":
        raise ValueError("local image differs from the frozen amd64 image; no pull attempted")
    return {
        "free_bytes": free,
        "image_id": local["Id"],
        "image": image,
        "daemon_memory_bytes": daemon.get("MemTotal"),
        "architecture": daemon.get("Architecture"),
        "environment": environment_info(),
    }


def run_pilot(
    config_path: Path,
    runs_dir: Path,
    *,
    simulation: Path | None = None,
    allow_paid: bool = False,
    evaluate_docker: bool = False,
) -> dict[str, Any]:
    # Authorization is checked before dataset acquisition, workspace setup or clients.
    if simulation is None and not allow_paid:
        raise ValueError("real pilot requires explicit --allow-paid authorization")
    frozen = check_freeze(config_path)
    cfg = load_config(config_path)
    state = git_state()
    if simulation is None and (not state["commit"] or state["dirty"]):
        raise ValueError("real pilot requires a clean committed working tree")
    do_evaluate = simulation is None or evaluate_docker
    mode = "offline_simulation" if simulation else "real"
    experiment = f"{cfg.experiment_id}__{'sim' if simulation else 'real'}__{uuid.uuid4().hex[:8]}"
    out = (runs_dir / experiment).resolve()
    out.mkdir(parents=True, exist_ok=False)
    summary: dict[str, Any] = {
        "experiment_id": experiment,
        "mode": mode,
        "created_at": utc_now(),
        "freeze_sha256": frozen["sha256"],
        "git": state,
        "planned": {"agent": True, "official_evaluation": True, "verification": True},
        "executed": {"agent": False, "official_evaluation": False},
        "verified": {"run": False, "evaluation": False},
        "simulation_fixture_sha256": (
            hashlib.sha256(simulation.read_bytes()).hexdigest() if simulation else None
        ),
        "evaluation_status": "PENDING",
        "research_results": False,
    }
    _json(out / "summary.json", summary)
    _json(out / "config.resolved.json", cfg.model_dump(mode="json"))
    _json(out / "FROZEN.json", frozen)
    model: AnthropicModel | None = None
    try:
        if do_evaluate:
            _json(
                out / "docker_preflight.json",
                docker_preflight(cfg.eval_image, cfg.eval_image_id, out),
            )
        dataset = cfg.dataset_path or download_split(cfg.split)
        task = load_tasks(dataset)[cfg.instance_ids[0]]
        if do_evaluate:
            record = next(r for r in _records(dataset) if r["instance_id"] == task.instance_id)
            if record.get("image") != cfg.eval_image:
                raise ValueError("dataset evaluator image differs from frozen image")
        slug = task.repo.replace("/", "__")
        source = str(cfg.repo_source_dir / slug) if cfg.repo_source_dir else repo_url(task.repo)
        root = data_dir()
        mirror = ensure_mirror(source, root / "mirrors" / slug, commits=[task.base_commit])
        ws = create_workspace(mirror, task.base_commit, root / "workspaces" / experiment)
        # Fresh tree must match upstream, including ignored-but-tracked files.
        from codetrace.workspace import git

        if git("rev-parse", "HEAD^{tree}", cwd=ws.path) != git(
            "rev-parse", f"{task.base_commit}^{{tree}}", cwd=mirror
        ):
            raise ValueError("workspace tree differs from frozen task base commit")
        if simulation is None:
            # One real attempt per frozen experiment directory, even with a different
            # --runs-dir. Exclusive creation prevents concurrent calls resetting the
            # total allowance. Never clear a possibly billed attempt automatically.
            attempt_path = config_path.parent / "REAL_ATTEMPT.json"
            try:
                with attempt_path.open("x") as fh:
                    json.dump(
                        {
                            "experiment_id": experiment,
                            "output_dir": str(out),
                            "freeze_sha256": frozen["sha256"],
                            "claimed_at": utc_now(),
                            "cost_cap_usd": cfg.budget.max_cost_usd,
                        },
                        fh,
                    )
                    fh.write("\n")
            except FileExistsError:
                raise ValueError(
                    "real attempt already claimed for this frozen experiment; "
                    "review existing usage before authorizing a new experiment"
                ) from None
        model = (
            simulated_model(cfg.model, simulation)
            if simulation
            else AnthropicModel(
                model=cfg.model.name or "",
                max_tokens=cfg.model.max_tokens,
                effort=cfg.model.effort,
                max_retries=0,
            )
        )
        summary["executed"]["agent"] = True
        result = run_agent(
            task.agent_view(),
            ws,
            model,
            out / "runs",
            experiment_id=experiment,
            budget=cfg.budget.to_budget(),
            seed=cfg.seeds[0],
            freeze={"sha256": frozen["sha256"], "mode": mode},
        )
        summary["agent_completed"] = True
        summary["run"] = asdict(result)
        summary["cost_note"] = (
            "Fixture usage only; actual API spend is zero."
            if simulation
            else "Acknowledged token cost; compare with provider invoice."
        )
        verification = verify_run(Path(result.trace_path).parent)
        _json(out / "verification.json", verification)
        summary["verified"]["run"] = verification["verified"]
        if not verification["verified"]:
            raise ValueError("run consistency verification failed")
        predictions = out / "predictions.jsonl"
        write_predictions(
            [
                {
                    "instance_id": task.instance_id,
                    "model_name_or_path": f"codetrace__{mode}",
                    "model_patch": Path(result.patch_path).read_text(),
                }
            ],
            predictions,
        )
        eval_dataset = out / "evaluator_dataset.jsonl"
        export_for_evaluator(dataset, cfg.instance_ids, eval_dataset)
        eval_id = experiment + "__evaluation"
        cmd = evaluator_command(
            predictions, eval_id, cfg.instance_ids, dataset_name=str(eval_dataset)
        )
        # Use the same isolated Python interpreter for execution and the saved command.
        cmd[0] = sys.executable
        eval_dir = out / "evaluation"
        eval_dir.mkdir()
        (out / "evaluate.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\ncd "
            + shell_join([str(eval_dir)])
            + "\n"
            + shell_join(cmd)
            + "\n"
        )
        summary["pending_evaluation_command"] = "bash " + shell_join([str(out / "evaluate.sh")])
        if do_evaluate:
            _json(
                out / "docker_preflight_before_evaluation.json",
                docker_preflight(cfg.eval_image, cfg.eval_image_id, out),
            )
            with (eval_dir / "harness_stdout.txt").open("w") as fh:
                proc = subprocess.run(
                    cmd,
                    cwd=eval_dir,
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=2100,
                )
            summary["executed"]["official_evaluation"] = True
            summary["evaluator_returncode"] = proc.returncode
            rows = collect_evaluation(eval_dir, eval_id, cfg.instance_ids)
            _json(eval_dir / "results.json", rows)
            summary["outcomes"] = rows
            records = {r["instance_id"]: r for r in _records(eval_dataset)}
            checked = (
                check_run(eval_dir, records)
                if any(eval_dir.glob("logs/run_evaluation/*/*/*/report.json"))
                else []
            )
            _json(out / "crosscheck.json", checked)
            # Empty patches have no raw logs and therefore cannot pass the raw-log cross-check.
            summary["verified"]["evaluation"] = (
                proc.returncode == 0
                and len(checked) == 1
                and all(r["agrees"] for r in checked)
                and not any(r["evaluation_failed"] for r in rows)
            )
            summary["evaluation_status"] = (
                "VERIFIED" if summary["verified"]["evaluation"] else "EXECUTED"
            )
        summary["pipeline_status"] = "complete" if do_evaluate else "evaluation_pending"
    except BaseException as exc:
        summary["pipeline_status"] = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        )
        summary["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        # Interrupts can finalize the run and re-raise before its result is returned.
        finalized = list(out.glob("runs/*/result.json"))
        if finalized and "run" not in summary:
            summary["run"] = json.loads(finalized[0].read_text())
            summary["agent_completed"] = True
            verification = verify_run(finalized[0].parent)
            _json(out / "verification.json", verification)
            summary["verified"]["run"] = verification["verified"]
        _json(out / "summary.json", summary)
        if model is not None:
            model.close()
    return {"output_dir": str(out), **summary}


def _records(path: Path) -> list[dict[str, Any]]:
    from codetrace.dataset import load_records

    return load_records(path)
