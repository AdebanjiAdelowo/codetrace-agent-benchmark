# Session Handoff: CodeTrace (2026-10-20)

Checkpoint written because the working session may end. Read with docs/STATUS.md
(verified results), docs/PROTOCOL.md (methodology), and docs/RESOURCES.md. Labels:
**[verified]** = actually executed and checked; **[implemented]** = code plus offline
tests; **[planned]** = not done.

## A. Project overview

- **CodeTrace**: measures whether coding agents lose task-relevant repository evidence
  between exploration and patch generation (discovery, retention, utilization,
  correctness), on SWE-bench Lite.
- Repository: <https://github.com/AdebanjiAdelowo/codetrace-agent-benchmark> (private).
  Local: `~/Documents/GitHub/codetrace-agent-benchmark`. Branch `main`. No other
  branches, worktrees, or stashes.
- GitHub Actions is **disabled** on the repository and has never run (billing could not be
  verified). Keep it disabled unless the owner says otherwise.
- Current objective: **Phase 3**, preparing a single-task paid pilot (configuration A on
  `pytest-dev__pytest-11143`) without making any paid call until the owner authorizes it.

## B. Completed work

| milestone | state | key files |
|---|---|---|
| Phase 1: pinned dataset, sanitized workspaces, sandboxed tools, agent loop, JSONL traces, official-evaluator bridge | [verified] | `src/codetrace/{dataset,workspace,tools,agent,trace,evaluation}.py` |
| Official SWE-bench 5.0.2 Docker validation: gold resolves 3/3, comment-only controls apply and stay unresolved 3/3, no regressions | [verified] locally, Apple Silicon, Rosetta | `src/codetrace/validation.py`, `eval_bundles/harness_validation_v1/`, results in `runs/harness_validation_v1_docker/` (git-ignored) |
| Report parser validated on real container reports; independent cross-check 6/6 | [verified] | `scripts/crosscheck_reports.py`, `tests/test_real_reports.py`, `tests/fixtures/swebench_5.0.2_docker/` (sanitized) |
| Phase 2: evidence-unit schema v2, edit-site extraction, metrics (DR, CRR, CLR) with known-answer tests, analyze CLI | [implemented] | `src/codetrace/{evidence,metrics}.py`, `tests/test_metrics.py` |
| Annotation package: auto edit sites, AI-assistant candidate support units (**not human labels**), blinded reviewer packages | [implemented]; human labels **not started** | `annotations/`, `src/codetrace/annotation_package.py`, `docs/REVIEWER_INSTRUCTIONS.md` |
| Anthropic adapter tested with mock HTTP transport (never called for real) | [implemented] | `src/codetrace/models/anthropic_client.py`, `tests/test_anthropic_adapter.py` |

### Work in this checkpoint commit (Phase 3, partial) [implemented, offline only]

- `src/codetrace/pricing.py` (new): versioned price table for `claude-opus-5-5`, verified
  on 2026-10-20 from <https://platform.claude.com/docs/en/about-claude/pricing>: input $4,
  5m cache write $5, 1h cache write $8, cache read $0.20, output $20 per MTok. No
  long-context surcharge. Functions `usage_cost`, `estimate_request_tokens` (conservative,
  2 chars/token), `projected_call_cost` (upper bound). Tests: `tests/test_pricing.py`.
- `src/codetrace/trace.py`: schema 0.2.0 (backward compatible). `TokenUsage` gains
  `cache_write_5m_tokens` / `cache_write_1h_tokens`. `RunManifest` gains optional
  `tool_specs_sha256`, `task_template_sha256`, `workspace_path`, `pricing`, `freeze`.
  **These manifest fields are defined but not yet populated by `agent.py`.**
  `schemas/trace.schema.json` regenerated.
- `src/codetrace/models/anthropic_client.py`: explicit cache TTL (`ttl: "5m"`, typed as
  the SDK literal), parses cache-write TTL breakdown, records `service_tier`,
  `inference_geo`, `speed` in `ModelResponse.meta` (`models/base.py`).
- `artifacts/manifests/runs_2026-10-20.sha256` (new): SHA-256 of all 98 files under
  `runs/`, committed so artifact integrity is anchored in git.

## C. Current state

**Works [verified]:** 132 tests pass, ruff clean, mypy `--strict` clean (23 files, src and
scripts), all 6 stored traces validate, leakage audits pass, Docker-validation manifest
verifies, Docker Hub tag digests for the 3 pilot images still equal the validated image IDs.

**Not implemented yet (Phase 3 remainder):**

1. Cost-cap enforcement in `agent.py`: `Budget.max_cost_usd`; before each request, stop
   with exit status `cost_budget` if `spent + projected_call_cost(...) > cap`. Record
   `cost_usd` and cumulative cost per `model_response` event, and in `RunResult`.
2. `stop_reason` handling in `agent.py`: `max_tokens` with tool_use means do not execute;
   return `is_error` tool_results ("not executed: truncated"). `model_context_window_exceeded`
   means end the run with that exit status.
3. `KeyboardInterrupt`: finalize patch and `run_end` (status `interrupted`), then re-raise.
4. Populate the new manifest fields (workspace path, tool/template hashes, pricing).
5. `codetrace recover-run`: for traces without `run_end`, sum usage and cost from
   `model_response` events and write `recovery.json` (never modify the trace; never resume
   an aborted run; re-run from scratch with a new run id).
6. `codetrace verify-run`: independent consistency checks (usage sums equal run_end, cost
   recomputed from usage, patch hash equals file, result.json equals run_end, snapshot spans
   equal spans viewed in segment, compaction removed spans equal previous segment's views).
7. Frozen experiment: `experiments/pilot_A_pytest-11143_v1/experiment.yaml` plus
   `codetrace freeze` writing `FROZEN.json` (hashes of system prompt, task template, tool
   specs, price table, agent-side source files, config; dataset revision; swebench 5.0.2;
   image digest `sha256:e08ce142adec5ec985e867e910045734ec3c58fe174fc054c5f83855bed78fd4`).
   The pilot command must refuse if hashes drift.
8. `codetrace pilot` end-to-end command: freeze check, then a clean-commit check, then the
   agent run (paid only with `--allow-paid`; `--simulate <canned.json>` uses a mock
   transport), then prediction export, Docker preflight (daemon, 15 GiB floor, image digest),
   the official evaluator in `evaluation/`, `collect_evaluation`, crosscheck, verify-run,
   and `summary.json` with separate planned / executed / verified statuses and
   `mode: offline_simulation | real`.
9. Mock-API tests for: context reset (fresh conversation, no orphan tool_use), cache-token
   cost accounting, 429 and connection retries, retries exhausted, cost-cap stop,
   max_tokens truncation, interrupt recovery, and a socket-blocking fixture proving no
   network access.

**Blockers / constraints:**

- **Docker Desktop is not running** (daemon socket absent at checkpoint time; it was
  stopped outside this project). Another project (`course-learning-portal`, Supabase) uses
  Docker. Do not start, stop, or prune Docker without the owner's agreement.
- No paid API call has been authorized. No `ANTHROPIC_API_KEY` is set in this environment.
- Human evidence annotation needs two reviewers (packages built locally in git-ignored
  `review_packages/`).
- Dataset license unverified (no license tag on the dataset card).
- macOS quirks: Python downloads need `CODETRACE_FORCE_IPV4=1` and `HF_HUB_DISABLE_XET=1`.
  The venv's `.pth` file gets the hidden flag; tests use `pythonpath = ["src"]`, and ad-hoc
  commands use `PYTHONPATH=src uv run --no-sync python -m codetrace.cli ...`.
- The session scratchpad is temporary: an earlier artifact fingerprint stored there was lost.
  Keep fingerprints in `artifacts/manifests/` (committed).

**No processes are running.** No experiment is interrupted.

## D. Decisions and reasoning

- Own minimal harness (no shell tool) so every context token is attributable. Test outcomes
  come only from the official evaluator.
- Context compaction is a logged **reset** to a fresh conversation (append-only history;
  current Claude models reject edited history with thinking blocks).
- Patch-generation moment is the model request whose response contained the first successful
  edit. Same-turn tool results do not count as seen (PROTOCOL.md §3, revised before any
  model run).
- No server-side refusal fallbacks (would switch models mid-run). Effort fixed at `high`.
- Dataset `SWE-bench/SWE-bench_Lite@b0dde10…` (the princeton-nlp copy lacks fields
  swebench 5.0.2 needs). Evaluator gets a pinned local export.
- Harness advisory labels (`ambiguous_failure_ids`) never override grades (false positive
  observed on pytest's own suite).
- Only `annotator_kind: human` annotations may be `annotated` or `adjudicated` (schema-enforced).
  `codetrace analyze` refuses non-adjudicated sets unless `--allow-provisional`.
- Paid runs require `--allow-paid` and a clean committed tree.
- Rejected: princeton-nlp dataset copy, in-place history pruning, an external agent framework,
  and running model commands on the host.

## E. Reproducibility

```bash
cd ~/Documents/GitHub/codetrace-agent-benchmark
uv sync --locked --group dev --extra anthropic --extra eval   # Python 3.12, anthropic 1.15.0, swebench 5.0.2
chflags nohidden .venv/lib/python3.12/site-packages/*.pth     # macOS quirk, if imports fail
uv run --no-sync pytest -q && uv run --no-sync ruff check . && uv run --no-sync mypy
PYTHONPATH=src uv run --no-sync python -m codetrace.cli validate-trace runs/*/runs/*/trace.jsonl
PYTHONPATH=src uv run --no-sync python scripts/audit_runs.py runs/smoke_heuristic_v2
PYTHONPATH=src uv run --no-sync python scripts/crosscheck_reports.py runs/harness_validation_v1_docker
shasum -a 256 -c artifacts/manifests/runs_2026-10-20.sha256    # artifact integrity
```

Docker validation (needs Docker running and about 5 GiB free per task; one task at a time):

```bash
docker pull --platform linux/amd64 swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-11143:latest
PYTHONPATH=src uv run --no-sync python -m codetrace.cli validate-harness \
  --bundle eval_bundles/harness_validation_v1 --out runs/<new_dir> \
  --instance-ids pytest-dev__pytest-11143 --runs gold control
```

Seeds: agent seeds are config-level (`seeds: [0]`). The scripted models are deterministic.
The live model is not seedable (record it as such).

**Artifacts not in GitHub** (local only, git-ignored): `runs/` (smoke traces, Docker
validation logs and reports; fingerprints committed in `artifacts/manifests/`),
`.codetrace_data/` (repo mirrors and workspaces, recreatable), `review_packages/`
(recreatable with `codetrace annotate package`), `.venv/`. No external backup exists.

## F. Next steps (prioritized)

1. **Finish Phase 3 offline items 1 to 9 in section C**, in `src/codetrace/agent.py`,
   `src/codetrace/cli.py`, a new `src/codetrace/verify.py`, and
   `experiments/pilot_A_pytest-11143_v1/`. Acceptance: all new behaviors covered by
   mock-transport tests; full suite, ruff, and strict mypy clean; the `pilot --simulate` run
   produces a valid trace, passes `verify-run`, and writes `summary.json` with
   `mode: offline_simulation`.
2. Proposed frozen pilot values (to confirm with the owner): model `claude-opus-5-5`,
   effort `high`, `max_tokens` 16000, max steps 40, token budget 1.5M processed tokens,
   context reset at 120k, wall time 1800 s, **cost cap $5 per run and $5 total**. Expected
   cost about $1 to $3 (assumption, not measured).
3. When Docker is running again (owner's call), run the simulated pilot through the Docker
   step to exercise the full pipeline.
4. **Only with explicit owner authorization:** run the real paid pilot (one run).
5. Send review packages to two human reviewers; adjudicate; then compute metrics.
6. Update docs/STATUS.md with every executed or verified step.

## G. Resume instructions for a new session

1. Read this file, then docs/STATUS.md and docs/PROTOCOL.md.
2. Verify the state: `git fetch && git status && git log --oneline -3`. Local HEAD should
   equal `origin/main` and the tree should be clean.
3. Run the commands in section E. All must pass before changing code.
4. Continue with section F, step 1. Do not call the Anthropic API, enable GitHub Actions,
   provision infrastructure, or run Docker cleanup without explicit authorization.
