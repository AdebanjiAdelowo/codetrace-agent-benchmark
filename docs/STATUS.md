# Status: Planned, Executed, Verified

Last updated: 2026-10-20. "Verified" means checked against an artifact or an independent tool,
as described in the evidence column. No model has been called and no SWE-bench evaluation has
been run.

## Phase 1: Benchmark and reproducibility

| item | state | evidence |
|---|---|---|
| Environment inspected | done | macOS 26.6.2 arm64, 18 GB RAM, uv 0.11.16, Docker 29.8.2; ~7.5 GiB free disk |
| Dataset availability, revision pinned | verified | `SWE-bench/SWE-bench_Lite@b0dde10…`, 300 test instances |
| Dataset license | **unverified** | no license tag on the dataset card; see DATASET.md |
| Harness version and CLI | verified | `swebench==5.0.2`; emitted flags checked against `--help` by a contract test |
| Evaluator-format compatibility | verified up to Docker | harness's own `load_swebench_dataset`, `make_test_spec`, `get_predictions_from_file` accept our exported dataset and predictions |
| Report parser vs harness source | verified against source | keys match `swebench/harness/grading.py` 5.0.2; **not** yet checked on a real report |
| Pinned, isolated environment | done | `uv.lock`, Python 3.12, project `.venv` |
| Baseline harness (configuration A) | implemented, offline-tested | `src/codetrace/agent.py`; 49 tests |
| Information hiding | verified offline + on smoke run | unit tests; `scripts/audit_runs.py` passes on all smoke traces |
| JSONL traces and manifest | verified | `codetrace validate-trace`: all smoke traces OK |
| Anthropic adapter | type-checked only | signature checked against `anthropic==1.15.0` with mypy strict; **never called** |
| CI workflow | written, **not run** | `.github/workflows/ci.yml`; nothing has been pushed |

### Executed: pipeline smoke test `smoke_heuristic_v2`

Command:

```bash
CODETRACE_FORCE_IPV4=1 uv run codetrace run --config configs/smoke_heuristic.yaml
uv run codetrace validate-trace runs/smoke_heuristic_v2/runs/*/trace.jsonl
uv run python scripts/audit_runs.py runs/smoke_heuristic_v2
```

What it exercised on the 3 real pilot tasks: pinned dataset download, depth-1 fetch at
`base_commit` from GitHub, sanitized workspaces (verified: 1 commit each), sandboxed tools,
the agent loop, the trace writer, predictions export, the evaluator dataset export, and
`evaluate.sh` generation.

The "model" was the deterministic `HeuristicExplorer` (no LLM). All three runs submitted
empty patches by design. **These runs have no research value and produce no metrics.**
Observation (pipeline only): the explorer never viewed any file changed by the reference fix.
This only confirms that the discovery check can be computed on real traces.

`smoke_heuristic_v1` (same runs against the superseded `princeton-nlp` copy) is kept locally
for reference only.

### PENDING

| experiment | why pending | exact command |
|---|---|---|
| Official evaluation of `smoke_heuristic_v2` | ~7.5 GiB free; 3 amd64 images ≈ 3.2 GB compressed, more uncompressed (RESOURCES.md) | `uv sync --extra eval && bash runs/smoke_heuristic_v2/evaluate.sh` on a host with ≥15 GB free (or add `--modal true`) |
| Gold-patch sanity check of the harness on the pilot | same | `python -m swebench.harness.run_evaluation --dataset_name runs/smoke_heuristic_v2/evaluator_dataset.jsonl --predictions_path gold --run_id gold_check --max_workers 1 --instance_ids pytest-dev__pytest-11143 sympy__sympy-13480 pylint-dev__pylint-7080` (expected: 3/3 resolved) |
| Live pilot, configuration A | needs explicit authorization for paid API calls | `uv sync --extra anthropic && uv run codetrace run --config configs/pilot_anthropic.yaml --allow-paid` |

## Phases 2 to 5

All planned; nothing implemented. See PROTOCOL.md for metric definitions, the annotation
protocol, the ablation design, and the analysis plan.
