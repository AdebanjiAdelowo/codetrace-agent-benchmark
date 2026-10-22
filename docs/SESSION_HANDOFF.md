# Session handoff: CodeTrace (2026-10-22)

Read with `STATUS.md` (artifact-backed results), `PILOT.md` (commands and cost caveats),
and `PROTOCOL.md` (research methodology). This handoff supersedes the 2026-10-20 list
of unfinished pilot implementation work.

## Current state

- Assigned repo: `~/Documents/GitHub/codetrace-agent-benchmark`, branch `main`.
- The pre-milestone base commit was `ba6c365`; use `git log -1` for the pilot milestone
  commit. The saved simulations record the dirty tree as it existed when executed;
  v2 freezes its exact source contents. Real pilots require a clean committed checkout.
- **178 tests pass**, Ruff lint/format clean, strict mypy clean on 28 source files.
  The project-specific `.venv` is approximately 403 MB; no dependency installation
  was needed. Default Python test sockets are blocked; no API requests were made.
- Phase 1 and the prior official Docker validation (3 gold + 3 comment-only controls)
  remain verified. All 98 recorded historical artifact hashes match. Re-running the
  independent parser on existing logs gives 6/6 agreement; no containers were run.
- Phase 2 metrics and annotation tools exist. Two human reviewers and adjudication
  are still needed; assistant drafts are not human ground truth.
- Phase 3 baseline pilot preparation is implemented and simulated. B/C agents are
  not yet implemented. No live model experiment or research finding exists.

## Latest readiness review

The offline milestone is committed and pushed as `fc678c2`. The later readiness review
adds seven regression cases and a decision report in `LIVE_PILOT_READINESS.md`; runtime
source and v2 fingerprints are unchanged. A fresh simulation from clean `fc678c2` is at
`runs/pilot_A_pytest-11143_v2__sim__2547ba89/`; it reproduces the saved v2 patch, usage,
tools and context spans. Audit logs are in `runs/readiness_audit_2026-10-22_fc678c2/`.
All 98 historical and 29 earlier simulation artifact hashes still match. Read-only
GitHub queries confirm privacy and disabled Actions. Docker inspection found the daemon
socket absent; no daemon was started and no evaluator or real API call was run.

## Implemented in the offline milestone

1. `agent.py`: `Budget.max_cost_usd`, conservative pre-request reservation, response
   and cumulative costs, total result cost, billing completeness; populated tool and
   task-template hashes, workspace path, pricing and freeze metadata.
2. Truncated (`max_tokens`) tool calls return errors and never execute. Context-window
   stops terminate. Keyboard interruption saves the current patch and finalized run,
   then re-raises; in-flight billing is marked incomplete.
3. `verify.py`: independent usage/cost/patch/result/context checks and aborted-prefix
   recovery, including a torn last line. Recovery never modifies or resumes a trace.
4. `freeze.py`: exclusive freeze creation and drift checking over config, all Python
   package source, task record, prompts/tools, pricing, uv lockfile and protocol;
   evaluator, SDK and previously validated image identity recorded.
5. `pilot.py`: single-task orchestration with explicit paid gate, clean-commit gate,
   mock transport mode, fresh workspace/tree check, prediction export, saved evaluator
   command, planned/executed/verified summary, opt-in official evaluation, collection
   and independent raw-log cross-check. Docker preflight runs before model calls and
   again before evaluation; it never starts Docker, pulls images, or cleans resources.
6. An exclusive local `REAL_ATTEMPT.json` in the frozen experiment directory permits
   one real attempt, even across concurrent calls or a different `--runs-dir`. It is
   Git-ignored and never cleared automatically, including after constructor failure.
   Review charges before authorizing another experiment. No such marker exists here.
7. Cross-check logic moved into `src/codetrace/crosscheck.py`; the existing script is
   a compatibility wrapper. Tests cover the real reports and mocked pipeline wiring.

## Saved execution evidence

Current freeze: `experiments/pilot_A_pytest-11143_v2/FROZEN.json`:
`e94b258de7d6acd05155038408c381fc0f895c993a245edd02b2186397d5b83f`.

Current simulation: `runs/pilot_A_pytest-11143_v2__sim__9ea1b161/`:

- Four mock HTTP responses through the real Anthropic SDK, using the cached pytest task.
- Reads code and creates `_codetrace_simulation.py` containing an inert comment;
  submits a 234-byte patch. This is canned behavior, not a model-generated issue fix.
- Valid trace and independent `verify-run` pass; summary says `offline_simulation`,
  `evaluation_status: PENDING`, `research_results: false`.
- Reported $0.008 is fixture arithmetic; actual API spend is $0.
- No official evaluator execution occurred during this continuation.

The v1 freeze and simulation (`runs/pilot_A_pytest-11143_v1__sim__e8869dca/`) are
preserved. v1 intentionally fails source drift after the one-attempt guard was added.
Do not overwrite its freeze. Use v2, or create v3 if source/protocol/config must change.

## Verification commands

From the repository root, with no install or network:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m codetrace.cli freeze --check \
  --config experiments/pilot_A_pytest-11143_v2/experiment.yaml
HF_HUB_OFFLINE=1 PYTHONPATH=src .venv/bin/python scripts/audit_runs.py \
  runs/pilot_A_pytest-11143_v2__sim__9ea1b161
PYTHONPATH=src .venv/bin/python -m codetrace.cli verify-run --run-dir \
  runs/pilot_A_pytest-11143_v2__sim__9ea1b161/runs/pytest-dev__pytest-11143__A_baseline__s0__e7277422
PYTHONPATH=src .venv/bin/python scripts/crosscheck_reports.py runs/harness_validation_v1_docker
shasum -a 256 -c artifacts/manifests/runs_2026-10-20.sha256
```

## Next steps and constraints

1. Review the local code and proposed v2 settings. Preserve v1/v2 freezes and outputs.
   Source changes require a new versioned freeze. No methodology change is needed to
   review or test the current implementation.
2. With owner authorization for Docker execution, use the simulated pilot command in
   `PILOT.md` with `--evaluate-docker`. It needs a running daemon, 15 GiB free disk,
   swebench 5.0.2, and the already present exact amd64 image. No pulls are automatic.
3. Before any real run: verify current pricing, commit the reviewed code/config/freeze,
   confirm the proposed $5 allowance, and obtain explicit paid authorization. Only
   then use `pilot --allow-paid`. The estimate is not an invoice guarantee; unknown
   charges are possible after interrupted requests. Automatic retries are disabled.
4. Arrange two human annotations and adjudication. Do not send messages to reviewers
   or publish anything without the owner's instructions.
5. After a verified live baseline pilot, agree on B/C ledger/retrieval behavior before
   scaling. The report, visualizations and BMW proposal remain future deliverables.

Actions remains disabled as previously recorded; do not enable it without permission.
Dataset licensing remains unverified (no card license tag). The readiness review queried Docker read-only and found its daemon socket absent.
Docker was not started, stopped, pulled, or pruned.
At the 2026-10-22 resource check, 47 GiB disk was free and memory_pressure reported 36%
free. Do not touch shared caches or other repositories.

Large execution artifacts under `runs/`, repository mirrors/workspaces under
`.codetrace_data/`, reviewer packages and `.venv/` remain local and Git-ignored.
The freeze/config and SHA-256 manifests are small, reviewable source artifacts.
