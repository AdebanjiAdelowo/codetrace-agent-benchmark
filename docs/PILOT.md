# Frozen single-task pilot

The proposed configuration is `experiments/pilot_A_pytest-11143_v2/experiment.yaml`:
one run of configuration A on `pytest-dev__pytest-11143`, Opus 5.5, effort high, 40
steps, 1.5M processed tokens, a 120k context-reset threshold, and 1800 seconds.
The proposed cost budget is $5 per run and $5 for this single-run experiment.
This file does not authorize paid execution. B/C comparisons remain future work.

## Freeze and simulate

Run from the repository root in the project environment:

```bash
# Create once, before simulation. Refuses to overwrite an existing freeze.
PYTHONPATH=src HF_HUB_OFFLINE=1 .venv/bin/python -m codetrace.cli freeze \
  --config experiments/pilot_A_pytest-11143_v2/experiment.yaml

# Check for drift in configuration, task record, prompts, tools, pricing, source,
# lockfile, or research protocol. Refuses to run on drift.
PYTHONPATH=src HF_HUB_OFFLINE=1 .venv/bin/python -m codetrace.cli freeze --check \
  --config experiments/pilot_A_pytest-11143_v2/experiment.yaml

PYTHONPATH=src HF_HUB_OFFLINE=1 .venv/bin/python -m codetrace.cli pilot \
  --config experiments/pilot_A_pytest-11143_v2/experiment.yaml \
  --simulate experiments/pilot_A_pytest-11143_v2/simulation.json
```

Simulation uses the real SDK's serializer and response parser with `MockTransport`, a
placeholder credential, and no network transport. The task and repository must already
be cached for a fully offline run. Each invocation creates a fresh workspace and unique
output directory. The canned policy reads code, creates an inert Python comment file,
and submits; it deliberately supplies no issue fix. Its usage and costs are invented
fixture values, identified as such in `summary.json`; actual API spend is zero. These
outputs must never be included in model-performance results.

`FROZEN.json` stores content hashes, the task-record hash (not the hidden record), the
versioned price table, swebench version, and the previously validated image ID. New source
changes require a new versioned experiment directory and freeze. A freeze checks content,
not authenticity; commit and review it before any real run. Simulation records a dirty
checkout explicitly. Real pilots require a clean committed checkout and `--allow-paid`.

## Spending and interruption

Before a request, the agent reserves the estimated worst-rate input cost plus the full
`max_tokens` output cost. The estimate uses one token per serialized UTF-8 byte and 4096
additional tokens for framing. The agent stops with `cost_budget` if acknowledged spend
plus this reservation exceeds the cap. Cache reads and both cache-write TTLs are priced
separately; writes with no TTL breakdown use the higher rate. Prices are the recorded
2026-10-20 snapshot, not a guarantee of current invoice rates.

This is an application-level estimated spending limit, not an absolute billing ceiling:
token estimation can differ from the provider tokenizer, and an unanswered request may
have incurred charges. Cost-capped runs disable automatic retries. An exclusive local `REAL_ATTEMPT.json`
record in the experiment directory permits only one real attempt, including concurrent
invocations or changes to `--runs-dir`. It is ignored by Git, never automatically cleared,
and retained even after constructor failure or an interrupted request. Review prior
charges before explicitly authorizing a new versioned experiment; copying a configuration
directory does not carry authorization for another paid run. Reported costs exclude
unacknowledged charges, tax and any provider-specific adjustments. Verify the pricing
snapshot before authorizing live work. The wall and token budgets are checked between
requests; an in-flight request can exceed them.

The agent writes cost and cumulative cost per response and in `result.json`, with
`billing_complete: false` after an unanswered request. A `KeyboardInterrupt` finalizes the
partial patch and `run_end`, writes `result.json`, and re-raises. A killed process may leave
only a trace prefix; use recovery, never resume that conversation:

```bash
PYTHONPATH=src .venv/bin/python -m codetrace.cli verify-run --run-dir runs/<experiment>/runs/<run>
PYTHONPATH=src .venv/bin/python -m codetrace.cli recover-run --run-dir runs/<experiment>/runs/<run>
```

Verification independently recomputes usage and cost and checks result/trace agreement,
patch bytes/hash, context snapshots and compaction spans. Recovery refuses finalized
runs, preserves original trace bytes, tolerates only a torn final JSONL line, and writes
`recovery.json` exclusively with acknowledged usage/cost and unknown-billing status.
A new invocation always has a new run ID.

## Official evaluation

Real pilots preflight Docker **before** calling the model: daemon must already be running,
15 GiB disk must be free, swebench must be 5.0.2, and the local amd64 image ID must match
the frozen ID. CodeTrace never starts Docker, pulls images, or cleans shared resources.
Preflight failure is recorded in the summary and prevents the model call.

Simulation leaves evaluation PENDING, with a saved command under `evaluate.sh`. Once
local Docker evaluation has been authorized, `pilot --simulate ... --evaluate-docker`
exercises the evaluator too, in a new output directory. Real evaluation invokes the
official harness in `evaluation/`, collects its reports, and independently cross-checks
raw test logs. Empty patches have no test logs, so raw-log verification remains false.
`summary.json` distinguishes planned, executed and verified stages, and identifies
`mode: offline_simulation | real`. A resolved/unresolved task is independent of pipeline
verification. An empty patch or infrastructure failure cannot acquire a verified raw-log
outcome merely because the evaluator returned zero.

After explicit paid authorization, the proposed live command is:

```bash
PYTHONPATH=src .venv/bin/python -m codetrace.cli pilot \
  --config experiments/pilot_A_pytest-11143_v2/experiment.yaml --allow-paid
```

No paid run is part of the offline milestone. Human evidence annotation, adjudication,
and subsequent metrics remain separate requirements for research conclusions.


## Freeze history

The v1 freeze and its saved simulation are retained as historical artifacts. Final review
added the exclusive real-attempt guard; v1 now intentionally fails the source-drift check.
The v2 freeze is the current review candidate and retains the same proposed task, model,
budgets and image. No real attempt has been claimed for either experiment.
