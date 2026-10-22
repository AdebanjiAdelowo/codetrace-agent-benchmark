# Status: Planned, Executed, Verified

Last updated: 2026-10-22. "Verified" means checked against an artifact or an independent
tool, as given in the evidence column. **No language model has been called. No GitHub
Actions workflow has run.** Official SWE-bench Docker evaluation has been run locally
for harness validation only (reference and negative-control patches, not agent output).

## Phase 3: frozen pilot preparation (offline milestone verified, 2026-10-22)

The single-task pilot safeguards and offline command are implemented. Configuration A
is available; B (ledger) and C (ledger plus retrieval) are still planned. No live model
call has been made, no new Docker evaluation has been run, and no research conclusion
follows from the simulation.

| component | state / evidence |
|---|---|
| Cost reservations and billing | Pre-request estimated dollar guard; per-response and cumulative acknowledged cost; cache TTL accounting; cost-capped runs disable retries |
| Total allowance | Exclusive local `REAL_ATTEMPT.json` allows one real attempt per frozen experiment directory, including concurrent invocations and different output roots; failed/interrupted attempts are never automatically cleared |
| Terminal handling | Truncated-response tool calls return errors without executing; context-window stop is terminal; interrupts save a partial patch/result and re-raise |
| Run provenance | Tool/template hashes, workspace path, recorded pricing and freeze/mode populated |
| Independent verification and recovery | `verify-run` recomputes usage/cost and checks patch/result/context consistency; `recover-run` reports an aborted prefix without editing or resuming the trace |
| Frozen protocol | `experiments/pilot_A_pytest-11143_v2/FROZEN.json`, SHA-256 `e94b258de7d6acd05155038408c381fc0f895c993a245edd02b2186397d5b83f`; hashes source, config, task record, prompts/tools, price table, lockfile and protocol |
| Offline simulation | `runs/pilot_A_pytest-11143_v2__sim__9ea1b161/summary.json`: 4 mock HTTP turns through the real SDK on the cached pytest repository; submitted a 234-byte inert comment-file patch; run verified, official evaluation **PENDING**, actual API spend **$0** |
| Fixture accounting | The reported `$0.008` is computed from canned usage, not actual spend. Simulation uses no model inference and contributes no performance results |
| Tests | **171 passed**, with `anthropic` and `eval` extras; Python socket connections blocked by default. Covers budgets/cache billing, resets, retries, truncation, interruption, recovery, artifact tampering, protocol drift and mocked evaluator wiring |
| Static checks | Ruff lint/format clean; strict mypy clean on 28 source files |
| Prior artifacts | All 98 files in `artifacts/manifests/runs_2026-10-20.sha256` unchanged; all 6 historical Docker reports still agree with independent raw-log checks |

The first v1 freeze/simulation is preserved. Final review added the one-attempt guard;
v1 now intentionally fails the source-drift check. v2 is the current review candidate.
Both simulations record the uncommitted working-tree state and use source hashes;
the source, freeze/config files and small manifests are included in the pilot milestone.
Full execution outputs remain local and Git-ignored.

The $5 proposed allowance is an application-level estimate, not a provider invoice
ceiling. Unknown charges from interrupted requests remain possible. Settings, commands,
and caveats: [PILOT.md](PILOT.md). No real-attempt marker has been created in either
experiment directory. No shared caches, Docker resources, or other projects were changed.

## Repository

- <https://github.com/AdebanjiAdelowo/codetrace-agent-benchmark> (private). GitHub Actions
  is disabled on the repository and has never run (billing could not be verified; see
  RESOURCES.md). Pushes are fast-forward only; history has never been rewritten.
- Commit history: `git log`. Commits cited below: `a1be786` and `1c4615b` (validation
  tooling at the time of each Docker run).

## Official Docker validation (local, 2026-10-20): PASSED, 6/6 controls

| item | value |
|---|---|
| Evaluator | `swebench==5.0.2`, `python -m swebench.harness.run_evaluation`, `--max_workers 1`, `--timeout 1800` |
| Dataset | `SWE-bench/SWE-bench_Lite@b0dde1093fe417d83b7184254edf8199c1f0dff5`, pinned local export (SHA-256 `41ada449…`) |
| Host | Apple Silicon (arm64), macOS 26.6.2, Docker Desktop 29.8.2 (aarch64 VM, 7.75 GiB). amd64 images run through Rosetta translation (`uname -m` = `x86_64`, CPU `VirtualApple`) |
| Images | official `swebench/sweb.eval.x86_64.*:latest`, pre-pulled with `--platform linux/amd64` because the harness pulls without a platform. IDs: pytest `sha256:e08ce142adec…` (3.84 GB), sympy `sha256:98270682f905…` (3.94 GB), pylint `sha256:3389f0d1319c…` (4.04 GB) |
| Artifacts | `runs/harness_validation_v1_docker/` (git-ignored): official summaries, `report.json`, `test_output.txt`, `run_instance.log`, `eval.sh`, `patch.diff` per run, plus `MANIFEST.sha256` (62 files; manifest SHA-256 `151df27c…`) |

| task | control | official category | patch applied | FAIL_TO_PASS | PASS_TO_PASS | test runtime | expectation |
|---|---|---|---|---|---|---|---|
| `pytest-dev__pytest-11143` | gold | resolved | yes | 1/1 pass | 114/114 pass | 13.2 s | **met** |
| `pytest-dev__pytest-11143` | comment-only | unresolved | yes | 0/1 pass | 114/114 pass | 14.2 s | **met** |
| `sympy__sympy-13480` | gold | resolved | yes | 1/1 pass | 43/43 pass | 10.1 s | **met** |
| `sympy__sympy-13480` | comment-only | unresolved | yes | 0/1 pass | 43/43 pass | 10.6 s | **met** |
| `pylint-dev__pylint-7080` | gold | resolved | yes | 1/1 pass | 120/120 pass | 20.6 s | **met** |
| `pylint-dev__pylint-7080` | comment-only | unresolved | yes | 0/1 pass | 120/120 pass | 20.8 s | **met** |

No regressions, no evaluation errors, no infrastructure failures, no leftover containers.
Each task ran sequentially with one image present at a time.

Notes on the evidence:

- **Independent cross-check.** `scripts/crosscheck_reports.py` re-derives every graded
  test's status from the raw `test_output.txt` without the harness's log parsers and
  compares it with `report.json`, CodeTrace's parser, and the summary category: 6/6 agree
  (`runs/harness_validation_v1_docker/crosscheck.json`).
- **pylint, ungraded failures.** The gold run exits 1 with "2 failed, 122 passed". The two
  failures (`test_generate_toml_config*`) are in neither graded list and fail identically
  under the control, so they are pre-existing environment failures that the dataset
  excludes by design. The control adds exactly the FAIL_TO_PASS failure.
- **pytest control, advisory label.** The harness summary marks it "ambiguous failure:
  `no_tests_collected`". That label comes from a regex classifier
  (`swebench/harness/infra_failure.py`) that matches "collected 0 items" printed by inner
  pytest sessions inside pytest's own test suite (the same lines occur in the gold log).
  116 tests ran. CodeTrace records the label as advisory and never lets it override the grade.
- **pytest control, provenance.** Executed at commit `a1be786`, where CodeTrace's old
  summary parser raised on the real summary after the harness had finished. Fixed in
  `1c4615b` and re-collected from the cached official outputs (no re-execution; report
  and test log byte-identical, first-attempt stdout and summary kept as `*.attempt1.*`).

## Report parser: validated on real container output

| finding on real 5.0.2 output | fix |
|---|---|
| With `--instance_ids`, `submitted_ids` lists every prediction in the file, not just evaluated tasks | categories must be disjoint and cover every *requested* task; partial coverage of `submitted_ids` is valid |
| `ambiguous_failure_ids` / `failure_reasons` can be false positives | stored as `harness_advisory_*`; never change the outcome |
| Reports include `FAIL_TO_FAIL` and `PASS_TO_FAIL` groups | tolerated; asserted empty in the real fixtures |
| pylint's log parser normalizes path-like parametrize ids (`[/mymodule.py]`) | independent cross-check implements the same documented id convention |

Rows now carry distinct fields: `patch_applied`, `resolved`, `has_regression`
(PASS_TO_PASS failure), and `evaluation_failed` (`error` / `incomplete` / infrastructure
failure / missing report). Regression tests use the six real reports and summaries,
sanitized by `scripts/sanitize_eval_fixtures.py`: test ids are hashed, structure and counts
are unchanged, and the sanitized and original copies were checked to parse identically
(`tests/fixtures/swebench_5.0.2_docker/`, `tests/test_real_reports.py`).

## Workspace bug found and fixed

The structural leakage check (each agent workspace tree must equal the upstream
base-commit tree) failed for pylint. Upstream tracks `tests/.pylint_primer_tests/.gitkeep`
under a `.gitignore`d directory, and the baseline commit's `git add -A` skipped it. Edits
to such files would have been missing from an agent's patch. Fixed (`git add -A --force`
at baseline). All three pilot workspaces now reproduce the upstream tree hash exactly
(regression test added). Existing smoke workspaces were built before the fix; their
patches were empty, so no result is affected, and they are kept unchanged as artifacts.

## Evidence annotation package (Phase 2, step 4)

| component | state |
|---|---|
| Schema v2 | `role`, `confidence`, `uncertainty`, `depends_on`, `external_dependencies`, `annotator_kind`. Only `human` annotations may be `annotated` or `adjudicated` (enforced by the schema). v1 files still load |
| `annotations/auto/` | machine-derived edit sites, 3 tasks, checked against base files |
| `annotations/assistant_draft/` | **AI-assistant** candidate support units (10 across 3 tasks) with role, necessity, confidence, uncertainty, and dependencies, each checked to land on the described code. **Not human labels**; for comparison after human review |
| Reviewer packages | `codetrace annotate package` builds blinded per-reviewer directories (instructions, guide, briefs, templates; no assistant draft, no other reviewer's work), written to git-ignored `review_packages/`. Built for `reviewer_a` and `reviewer_b`; templates validated |
| Human annotation | **not started**: needs two human reviewers. No `annotations/adjudicated/` exists, so `codetrace analyze` refuses final metrics |

## Historical verification run (2026-10-20; superseded counts above)

| check | result |
|---|---|
| pytest | 129 passed, 0 skipped (with `eval` and `anthropic` extras) |
| Ruff (lint, format) | clean |
| mypy `--strict` | clean on 22 files (`src/codetrace`, `scripts`) |
| Trace schema | exported schema equals committed schema; all 6 traces valid |
| Leakage audit | `scripts/audit_runs.py` passes on both smoke experiments; workspace trees equal upstream base trees after the fix |
| Artifact integrity | 26 pre-existing artifacts byte-identical; Docker-validation manifest verified |

## Earlier verified items (unchanged)

Dataset pinning and export determinism; official evaluator on empty patches; Anthropic
adapter against a mock transport (never against the real API); CI simulated locally from
a fresh clone (never on GitHub); workflow static checks; metrics on known-answer synthetic
traces.

## PENDING

| item | blocker | command |
|---|---|---|
| Human evidence annotation (2 reviewers, then adjudication) | needs people | send `review_packages/reviewer_a/` and `review_packages/reviewer_b/` separately; then `codetrace annotate validate --check-files` and `codetrace annotate agreement` |
| Frozen simulation through Docker | needs authorization for local Docker execution; daemon and matching image must already exist | `PYTHONPATH=src HF_HUB_OFFLINE=1 .venv/bin/python -m codetrace.cli pilot --config experiments/pilot_A_pytest-11143_v2/experiment.yaml --simulate experiments/pilot_A_pytest-11143_v2/simulation.json --evaluate-docker` |
| Live pilot, configuration A, one task/run | needs explicit paid authorization, price verification and a clean committed checkout | `PYTHONPATH=src .venv/bin/python -m codetrace.cli pilot --config experiments/pilot_A_pytest-11143_v2/experiment.yaml --allow-paid` |
| CI and harness workflow on GitHub | billing not verified; Actions disabled | see RESOURCES.md |

## Limitations

- Validation covers 3 small, single-hunk tasks from 3 repositories, under Rosetta
  emulation. It shows the pipeline grades correctly here, not that every task image runs
  correctly on Apple Silicon.
- Negative controls test the applied-and-unresolved path only. Patch-apply failures and
  in-container timeouts have been tested only through the official grading code on
  synthetic logs.
- Docker images were pulled by tag (`latest`); the image IDs above identify what was run.
  A future pull could return a different image.
- The assistant draft reflects one AI reading of the code and may anchor anyone who sees
  it. Review packages exclude it; its value is as a comparison point after human review.
- No agent has been evaluated; no research metric has been computed on model behavior.
