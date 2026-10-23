# First Anthropic live pilot: readiness decision

Review date: 2026-10-22 (Europe/Rome). **Offline milestone verified; live execution is
not authorized. The initial review was blocked by an unavailable Docker daemon; see
the 2026-10-23 update below for the current missing-image blocker.** No real API
call, live agent run, Docker evaluation, Actions run, or paid infrastructure provisioning
was performed in this review. The report and tests extend milestone commit `fc678c2`.
The exact commit carrying this report is available through `git log`.

The proposed decision is to accept the offline milestone and defer paid execution until
the owner authorizes the stated allowance, the shared Docker daemon is available with the
matching image, and the outstanding licensing and account-access questions are addressed.
The $5 figure below is an application-level estimate, not a guaranteed provider bill limit.

## Updated decision, 2026-10-23

**BLOCKED.** Docker Desktop now starts and the daemon responds, but the required
pytest evaluator image is missing. The image phase stopped without a pull, substitution,
Docker evaluation or paid API request. v2 and its fingerprints are unchanged. A fresh
non-Docker simulation passed, as did all 178 tests and the artifact/leakage audits.
The $5 proposal is still only an application estimate, and account access is unverified.
Dataset licensing now has component-specific findings rather than blanket clearance.
The 2026-10-22 observations below are historical; current evidence and blockers are in
[FINAL_OFFLINE_INTEGRATION.md](FINAL_OFFLINE_INTEGRATION.md) and
[LICENSING.md](LICENSING.md). Separate approval is required before a >500 MB image pull.

## Experiment and environment

| Item | Exact selection |
|---|---|
| Provider / model | Anthropic Messages API, `claude-opus-5-5` |
| Installed and frozen SDK | `anthropic==1.15.0` |
| Task | SWE-bench Lite `pytest-dev__pytest-11143` |
| Base repository commit | `6995257cf470d2143ad1683824962de4071c0eb7` |
| Dataset | `SWE-bench/SWE-bench_Lite@b0dde1093fe417d83b7184254edf8199c1f0dff5`, test split |
| Configuration | `experiments/pilot_A_pytest-11143_v2/experiment.yaml` |
| Freeze | `experiments/pilot_A_pytest-11143_v2/FROZEN.json` |
| Freeze SHA-256 | `e94b258de7d6acd05155038408c381fc0f895c993a245edd02b2186397d5b83f` |
| Agent / runs | Configuration A baseline; exactly one task, configuration and run; seed label 0 |
| Model settings | Effort `high`, up to 16,000 output tokens per response; adaptive thinking left at the provider default; no refusal fallback |
| Maximum model turns | 40 requests, including retries of truncated responses or no-tool nudges within the agent loop |
| Automatic HTTP retries | **0** in the pilot; capped runs reject clients configured with nonzero retries |
| Other budgets | 1,500,000 processed tokens; 1,800 seconds; context reset after a reported input size above 120,000 tokens |

The model identifier is confirmed in Anthropic's current [models overview](https://platform.claude.com/docs/en/models/overview).
Installed SDK version is local package metadata, not a claim that it is the newest SDK.
Account entitlement, credentials, rate limits and actual API behavior remain untested.
The seed label is experiment metadata; the live model is not seeded by this harness.

## Spending decision

| Scope | Limit or reservation |
|---|---|
| Per request | Dynamic: estimated input at $8/MTok plus the full output allowance at $20/MTok must fit the remaining run budget |
| Reservation formula | `(8 * estimated_input_tokens + 20 * 16000) / 1000000` USD |
| First request | 12,193 locally estimated input tokens; **$0.417544** reserved, including $0.32 for maximum output |
| Per run | **$5.00 estimated API allowance**; no next request when acknowledged cost plus its reservation exceeds $5 |
| Aggregate experiment | **$5.00 estimated allowance**, enforced by permitting one real attempt per frozen experiment directory |
| Restarts / concurrency | Exclusive `REAL_ATTEMPT.json` creation; remains present after failure or interruption; changing `--runs-dir` does not reset the allowance |
| Actual spend in this review | **$0.00**; the simulated $0.008 is fixture arithmetic only |

The local estimator budgets one token per serialized UTF-8 byte plus 4,096 tokens for
framing. It is deliberately conservative but not a provider tokenizer guarantee.
The guard selects prices by the configured model name and refuses unpriced models.
Tests with alternative mock-model rates verify that both reservations and acknowledged
costs use that configured table rather than the adapter's default model.

Processed tokens include uncached input, cache-read input, aggregate cache-creation input,
and output. The two cache-write TTL fields subdivide cache creation and are never added a
second time. Token and wall-time budgets are checked between requests, so an in-flight
request can exceed them. Unknown charges after a lost response, taxes, geographic/service
premiums, future rate changes or an inaccurate input estimate can exceed the $5 estimate.
There is no verified provider-side hard spending ceiling. The attempt marker is local:
copying a checkout or deliberately removing it can bypass that local protection and does
not confer authorization for another experiment.

## Cache assumptions

Prompt caching uses top-level `cache_control: {type: ephemeral, ttl: 5m}`. History is
append-only within a segment; a context reset starts a new conversation. Cache hits are
not guaranteed. Actual acknowledged cache usage is taken from the response.

| Billing component | USD per million tokens |
|---|---|
| Uncached input | $4.00 |
| Five-minute cache write | $5.00 |
| One-hour cache write | $8.00 |
| Cache read | $0.20 |
| Output | $20.00 |

The frozen 2026-10-20 table matches the official [pricing page](https://platform.claude.com/docs/en/about-claude/pricing)
when rechecked on 2026-10-22. Writes without a TTL breakdown are conservatively charged at
the higher one-hour rate in CodeTrace accounting. The reservation assumes the highest
input rate even when a cache hit is expected. Costs are estimates from acknowledged
usage and must later be reconciled with the provider's invoice.

## Failure, interruption and tool behavior

- An API exception ends the agent loop with `error`; there is no automatic paid restart. The SDK request timeout is 600 seconds.
  If a request was in flight, `billing_complete` is false and acknowledged cost is not
  treated as the final bill.
- `KeyboardInterrupt` attempts to finalize the current patch, trace and result with
  `interrupted`, then re-raises. The durable real-attempt marker remains. A process kill
  can leave a trace prefix; `recover-run` writes an exclusive recovery report, preserves
  the original bytes, and never resumes the conversation or calls the API.
- A `max_tokens` response cannot execute its tools: each receives a truncated-response
  error. A context-window-exceeded response terminates the run. Refusals terminate
  without falling back to a different model.
- `verify-run` independently recomputes usage and cost and checks patch hash/content,
  `result.json` versus `run_end`, request/compaction counts and retained/removed spans.
  It checks internal consistency, not provider billing truth or patch correctness.
- The agent has **no shell or test-execution tool**. It can list, read, search and edit
  repository files and submit. Host-side git and the evaluator are controlled by the
  harness. Hidden benchmark records are supplied only to the evaluator.

## Docker evaluation: prepared, not executed

The pilot requires swebench 5.0.2, a running daemon and the already present amd64 image:

```text
swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-11143:latest
sha256:e08ce142adec5ec985e867e910045734ec3c58fe174fc054c5f83855bed78fd4
```

Preflight checks the exact local image ID, architecture and at least **15 GiB free disk**
before any model call and again before evaluation. It does not start Docker, pull images,
provision resources or clean other projects. Read-only inspection on this review found
that the Docker socket was absent; daemon memory and image presence could not be checked.
Host disk had **43 GiB free**, memory_pressure reported **34% free**, and the project venv
was **403 MB**. The previously used Docker VM allocation was about 8 GB; that is historical,
not current availability. A single evaluator worker is proposed. Apple Silicon requires
amd64 translation. No GPU is required.

The expected evaluator command, generated inside a future run's `evaluation/` directory,
is:

```bash
<repo>/.venv/bin/python -m swebench.harness.run_evaluation \
  --dataset_name <run>/evaluator_dataset.jsonl --split test \
  --predictions_path <run>/predictions.jsonl --run_id <run_id>__evaluation \
  --max_workers 1 --timeout 1800 --instance_ids pytest-dev__pytest-11143
```

The exact pending evaluator script for the fresh simulation is
`bash runs/pilot_A_pytest-11143_v2__sim__2547ba89/evaluate.sh`, from the repository root.
It was not executed. The wrapper timeout is 2,100 seconds. The pinned local export contains the full evaluator
records; it remains Git-ignored and inaccessible through agent tools. The saved v2
simulation has an exact runnable command in its `evaluate.sh`, but executing it would
grade a canned comment-file patch, not a model-generated issue fix. No evaluator command
was run for this review. Before authorizing a live run, exercise the simulated Docker
path in a new run directory with `pilot --simulate ... --evaluate-docker`.

## Verification evidence and remaining limitations

Full regression results: **178 passed**, Ruff lint and format clean, strict mypy clean
on 28 source files. Seven additional test cases cover configured-model pricing, refusal
of unpriced models/retry-enabled clients, exact manifest fingerprints, cost tampering,
and interruption followed by a blocked paid restart. Default Python test sockets are
blocked. SDK requests in tests use a mock transport.

The saved v2 simulation and a fresh run from clean commit `fc678c2` both pass structural,
independent consistency and leakage checks. Their patches, token usage, tool calls,
context snapshots and viewed spans match. Timestamps, wall times and run IDs naturally
differ. The fresh manifest correctly records that commit without a dirty suffix and the
v2 freeze fingerprint. All **98 historical artifacts** and **29 earlier simulation
artifacts** remain byte-identical. GitHub reports the repository private and Actions
disabled. No real-attempt marker exists in either versioned experiment directory.

Local evidence (Git-ignored):

- `runs/readiness_audit_2026-10-22_fc678c2/`: pytest XML/text, static-check logs, artifact
  audit, saved/fresh leakage audits and the source secrets-audit summary.
- `runs/pilot_A_pytest-11143_v2__sim__2547ba89/`: fresh simulation, trace, patch and summary.
- `runs/pilot_A_pytest-11143_v2__sim__9ea1b161/`: unchanged earlier v2 simulation.

No live model inference, agent task-resolution rate, evidence-utilization effect or new
official grade is established. The scientific pilot is a single small Python task, not
an A/B/C comparison. Human evidence labels/adjudication remain absent; presence is not
utilization. B/C agents and ablations remain future work. Dataset-license verification
is unresolved. The model identifier and Docker tag can evolve; source freezing does
not establish future remote reproducibility. Simulated billing is not an invoice, and
local pattern-based secrets scanning is not proof against every credential format.
The full Docker-integrated pilot path is tested with mock evaluator output, not a new
container run. No claim extends these Python tasks to automotive-scale software.

After the listed prerequisites and **separate explicit paid authorization**, the proposed
live command is `PYTHONPATH=src .venv/bin/python -m codetrace.cli pilot --config
experiments/pilot_A_pytest-11143_v2/experiment.yaml --allow-paid`. It is presented for
review only and has not been executed.
