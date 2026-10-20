"""Command-line entry point: ``codetrace <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from codetrace.agent import run_agent
from codetrace.config import ExperimentConfig, data_dir, load_config
from codetrace.dataset import (
    DATASET_NAME,
    DATASET_REVISION,
    SelectionCriteria,
    download_split,
    export_for_evaluator,
    load_tasks,
    select_tasks,
)
from codetrace.env_info import environment_info, git_state
from codetrace.evaluation import (
    collect_evaluation,
    evaluator_command,
    shell_join,
    write_predictions,
)
from codetrace.models.base import ModelClient
from codetrace.models.scripted import HeuristicExplorer
from codetrace.netfix import apply_from_env as apply_ipv4_workaround
from codetrace.trace import export_json_schema, read_trace, utc_now, validate_trace
from codetrace.validation import run_bundle, write_control_predictions
from codetrace.workspace import create_workspace, ensure_mirror, repo_url

PILOT_REPOS = (
    "psf/requests",
    "pallets/flask",
    "pytest-dev/pytest",
    "sympy/sympy",
    "pylint-dev/pylint",
)


def _dataset_file(cfg_path: Path | None, split: str) -> Path:
    return cfg_path if cfg_path is not None else download_split(split)


def cmd_select_tasks(args: argparse.Namespace) -> int:
    path = _dataset_file(Path(args.dataset) if args.dataset else None, args.split)
    tasks = load_tasks(path)
    crit = SelectionCriteria(repos=tuple(args.repos), n_tasks=args.n)
    chosen = select_tasks(tasks, crit)
    out = {
        "dataset": DATASET_NAME,
        "revision": DATASET_REVISION,
        "split": args.split,
        "n_available": len(tasks),
        "criteria": asdict(crit),
        "selected": [
            {"instance_id": t.instance_id, "repo": t.repo, "version": t.version,
             "reference_patch_stats": asdict(s), "n_fail_to_pass": len(t.fail_to_pass),
             "n_pass_to_pass": len(t.pass_to_pass)}
            for t, s in chosen
        ],
    }  # fmt: skip
    text = json.dumps(out, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0


def _make_model(cfg: ExperimentConfig, problem_statement: str, allow_paid: bool) -> ModelClient:
    if cfg.model.provider == "heuristic":
        return HeuristicExplorer(problem_statement)
    if not allow_paid:
        raise SystemExit(
            "refusing to run a paid model without --allow-paid "
            f"(provider={cfg.model.provider}, model={cfg.model.name})"
        )
    from codetrace.models.anthropic_client import DEFAULT_MODEL, AnthropicModel

    return AnthropicModel(
        model=cfg.model.name or DEFAULT_MODEL,
        max_tokens=cfg.model.max_tokens,
        effort=cfg.model.effort,
    )


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    paid = cfg.model.provider != "heuristic"
    if paid and not args.allow_paid:
        raise SystemExit(
            "refusing to run a paid model without --allow-paid "
            f"(provider={cfg.model.provider}, model={cfg.model.name})"
        )
    git = git_state()
    if paid and (git["commit"] is None or git["dirty"]) and not args.allow_dirty:
        raise SystemExit(
            "refusing a paid run from an uncommitted or dirty working tree "
            f"(commit={git['commit']}, dirty={git['dirty']}); commit first or pass --allow-dirty"
        )
    dataset_file = _dataset_file(cfg.dataset_path, cfg.split)
    tasks = load_tasks(dataset_file)
    missing = [i for i in cfg.instance_ids if i not in tasks]
    if missing:
        raise SystemExit(f"instance ids not in dataset: {missing}")
    root = data_dir()
    exp_dir = Path(args.runs_dir) / cfg.experiment_id
    if (exp_dir / "experiment_manifest.json").exists():
        raise SystemExit(f"{exp_dir} already holds an experiment; use a new experiment_id")
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "config.resolved.json").write_text(cfg.model_dump_json(indent=2) + "\n")
    experiment_manifest = {
        "experiment_id": cfg.experiment_id,
        "created_at": utc_now(),
        "codetrace_git_commit": git["commit"],
        "codetrace_git_dirty": git["dirty"],
        "config_path": str(args.config),
        "dataset": DATASET_NAME,
        "dataset_revision": DATASET_REVISION,
        "instance_ids": cfg.instance_ids,
        "environment": environment_info(),
    }
    (exp_dir / "experiment_manifest.json").write_text(
        json.dumps(experiment_manifest, indent=2) + "\n"
    )

    results = []
    for agent_config in cfg.agent_configs:
        for seed in cfg.seeds:
            for iid in cfg.instance_ids:
                full = tasks[iid]
                view = full.agent_view()  # the agent never receives `full`
                slug = full.repo.replace("/", "__")
                source = (
                    str(cfg.repo_source_dir / slug) if cfg.repo_source_dir else repo_url(full.repo)
                )
                mirror = ensure_mirror(source, root / "mirrors" / slug, commits=[full.base_commit])
                ws_dir = root / "workspaces" / cfg.experiment_id / f"{iid}__{agent_config}__s{seed}"
                ws = create_workspace(mirror, full.base_commit, ws_dir)
                model = _make_model(cfg, view.problem_statement, args.allow_paid)
                res = run_agent(
                    view, ws, model, exp_dir / "runs",
                    experiment_id=cfg.experiment_id, agent_config=agent_config, seed=seed,
                    budget=cfg.budget.to_budget(),
                )  # fmt: skip
                print(json.dumps(asdict(res)))
                results.append((res, model.info.name, seed))

    # One predictions file per (config, seed): the official evaluator expects one
    # prediction per instance per file.
    eval_dataset = exp_dir / "evaluator_dataset.jsonl"
    export_for_evaluator(dataset_file, cfg.instance_ids, eval_dataset)
    commands = []
    for agent_config in cfg.agent_configs:
        for seed in cfg.seeds:
            group = [
                (r, m) for r, m, sd in results if r.agent_config == agent_config and sd == seed
            ]
            pred_path = exp_dir / "predictions" / f"{agent_config}__s{seed}.jsonl"
            write_predictions(
                (
                    {"instance_id": r.instance_id,
                     "model_name_or_path": f"codetrace__{agent_config}__s{seed}__{m}",
                     "model_patch": Path(r.patch_path).read_text()}
                    for r, m in group
                ),
                pred_path,
            )  # fmt: skip
            run_id = f"{cfg.experiment_id}__{agent_config}__s{seed}"
            cmd = evaluator_command(
                pred_path.resolve(),
                run_id,
                cfg.instance_ids,
                dataset_name=str(eval_dataset.resolve()),
            )
            commands.append(shell_join(cmd))
    script = exp_dir / "evaluate.sh"
    script.write_text(
        "#!/usr/bin/env bash\n# Official SWE-bench evaluation (requires the `eval` extra, Docker,\n"
        "# and disk per docs/RESOURCES.md). Status: PENDING until run.\n"
        "# Harness logs and reports are written under evaluation/ next to this script;\n"
        "# afterwards: codetrace collect-eval --experiment-dir <this dir>\n"
        "set -euo pipefail\n"
        'cd "$(dirname "$0")" && mkdir -p evaluation && cd evaluation\n'
        + "\n".join(commands)
        + "\n"
    )
    print(f"wrote {script}")
    return 0


def cmd_validate_trace(args: argparse.Namespace) -> int:
    bad = 0
    for p in args.paths:
        problems = validate_trace(read_trace(Path(p)))
        print(f"{p}: {'OK' if not problems else '; '.join(problems)}")
        bad += bool(problems)
    return 1 if bad else 0


def cmd_collect_eval(args: argparse.Namespace) -> int:
    exp_dir = Path(args.experiment_dir)
    cfg = ExperimentConfig.model_validate_json((exp_dir / "config.resolved.json").read_text())
    eval_dir = exp_dir / "evaluation"
    out_rows = []
    for agent_config in cfg.agent_configs:
        for seed in cfg.seeds:
            run_id = f"{cfg.experiment_id}__{agent_config}__s{seed}"
            for row in collect_evaluation(eval_dir, run_id, cfg.instance_ids):
                out_rows.append(
                    {"run_id": run_id, "agent_config": agent_config, "seed": seed, **row}
                )
    out = eval_dir / "results.jsonl"
    out.write_text("".join(json.dumps(r) + "\n" for r in out_rows))
    for r in out_rows:
        print(f"{r['run_id']}  {r['instance_id']:<28} {r['category']}")
    print(f"wrote {out}")
    return 0


def cmd_make_control(args: argparse.Namespace) -> int:
    tasks = load_tasks(_dataset_file(Path(args.dataset) if args.dataset else None, "test"))
    chosen = [tasks[i] for i in args.instance_ids]
    root = data_dir()
    n = write_control_predictions(
        chosen,
        root / "mirrors",
        root / "control_workspaces" / utc_now().replace(":", ""),
        Path(args.out),
    )
    print(f"wrote {n} control predictions to {args.out}")
    return 0


def cmd_validate_harness(args: argparse.Namespace) -> int:
    dataset_file = _dataset_file(Path(args.dataset) if args.dataset else None, "test")
    result = run_bundle(
        Path(args.bundle), Path(args.out), dataset_file,
        skip_docker=args.skip_docker, max_workers=args.max_workers,
    )  # fmt: skip
    print(json.dumps(result, indent=2))
    return 0 if result["all_executed_passed"] else 1


def cmd_export_schema(args: argparse.Namespace) -> int:
    export_json_schema(Path(args.out))
    print(f"wrote {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="codetrace")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("select-tasks", help="deterministic pilot task selection")
    s.add_argument("--dataset", help="local parquet/jsonl; default downloads the pinned revision")
    s.add_argument("--split", default="test")
    s.add_argument("--repos", nargs="+", default=list(PILOT_REPOS))
    s.add_argument("--n", type=int, default=3)
    s.add_argument("--out")
    s.set_defaults(func=cmd_select_tasks)

    r = sub.add_parser("run", help="run agent configurations on tasks")
    r.add_argument("--config", required=True)
    r.add_argument("--runs-dir", default="runs")
    r.add_argument("--allow-paid", action="store_true", help="permit paid model API calls")
    r.add_argument(
        "--allow-dirty", action="store_true", help="permit a paid run from a dirty working tree"
    )
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("collect-eval", help="parse official evaluator output for an experiment")
    c.add_argument("--experiment-dir", required=True)
    c.set_defaults(func=cmd_collect_eval)

    m = sub.add_parser("make-control-predictions", help="negative-control patches (harness check)")
    m.add_argument("--instance-ids", nargs="+", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--dataset")
    m.set_defaults(func=cmd_make_control)

    h = sub.add_parser("validate-harness", help="known-answer check of the official evaluator")
    h.add_argument("--bundle", required=True)
    h.add_argument("--out", required=True)
    h.add_argument("--dataset")
    h.add_argument("--skip-docker", action="store_true", help="run only empty-patch sets")
    h.add_argument("--max-workers", type=int, default=1)
    h.set_defaults(func=cmd_validate_harness)

    v = sub.add_parser("validate-trace", help="structural validation of trace files")
    v.add_argument("paths", nargs="+")
    v.set_defaults(func=cmd_validate_trace)

    e = sub.add_parser("export-schema", help="write the trace JSON schema")
    e.add_argument("--out", default="schemas/trace.schema.json")
    e.set_defaults(func=cmd_export_schema)

    args = p.parse_args(argv)
    apply_ipv4_workaround()
    rc: int = args.func(args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
