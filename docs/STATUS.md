# Status: Planned, Executed, Verified

Last updated: 2026-10-20. "Verified" means checked against an artifact or an independent
tool, as given in the evidence column. **No language model has been called. No benchmark
task has been graded inside a Docker container. No GitHub Actions workflow has run.**

## Repository and commits

- Repository: <https://github.com/AdebanjiAdelowo/codetrace-agent-benchmark> (private).
  Created 2026-10-20. Pushed normally, with no force-push and no history rewrite.
- **GitHub Actions is disabled on the repository** (set before the first push), so pushes
  start no workflow. See "Docker validation" below for why.

| commit | content |
|---|---|
| `9f1901de22fd6c265379deee8d6210bfe36f29aa` | Phase 1: harness, tracing, evaluation bridge, docs |
| `52236e89ba3519ebe33561aaf4fba47cf2d2760f` | Verification milestone: evaluator validation, adapter mocks, provenance |
| `caa7d80c5f7437c6ad2ba4893f5c958fbcfd8fd3` | Docs: verified outcomes, limitations, Phase 2 plan |
| `c5df5ea5938d3df4ade8b30b7b8fa37b654c7df9` | Workflow hardening: SHA-pinned actions, no credential persistence, env-passed input |
| `d7bb033af32ebe1e202866d9446f25a2c23bc1d0` | Phase 2 (offline): evidence units, annotation tooling, retention metrics |

Pre-push checks (2026-10-20): full-history secret scan clean. Nothing from `runs/`, `logs/`,
`.codetrace_data/`, `.venv`, credential files, or datasets was ever committed. The only
dataset-like strings are synthetic test fixtures. The push payload was 86 objects (420 KiB).

## Docker validation (official evaluator): NOT RUN, blocked on billing verification

| item | state |
|---|---|
| Workflow | `.github/workflows/swebench-harness-validation.yml`, manual trigger, `timeout-minutes: 120`, `ubuntu-24.04` (x86_64) |
| External services | none paid. GitHub-hosted runner, PyPI, Hugging Face (anonymous), Docker Hub (anonymous) |
| Billing check | **failed to verify**. The `gh` token lacks the `user` scope that every billing endpoint requires, and the account plan is not readable. Adding the scope needs an interactive login (`gh auth refresh -h github.com -s user`) |
| Decision | per the milestone's cost rule, the workflow was **not triggered** and Actions was disabled before the first push so CI could not start either |
| Actions run URL | none (no run exists) |
| Gold controls (3) | **not run**, expected resolved |
| Negative controls (3) | **not run**, expected applied and unresolved with FAIL_TO_PASS failing |
| Evaluator / dataset | `swebench==5.0.2`; `SWE-bench/SWE-bench_Lite@b0dde1093fe417d83b7184254edf8199c1f0dff5`; evaluator export SHA-256 `41ada449…` |

To proceed once billing is confirmed:

```bash
gh api -X PUT repos/AdebanjiAdelowo/codetrace-agent-benchmark/actions/permissions -F enabled=true
gh workflow run swebench-harness-validation.yml -R AdebanjiAdelowo/codetrace-agent-benchmark
```

## Provenance

- Run manifests record `codetrace_git_commit` (`+dirty` when the tree differs).
  Experiments write `experiment_manifest.json` with the commit, dirty flag, dataset revision,
  task ids, and environment.
- Paid runs refuse a dirty or uncommitted tree and never reuse an experiment directory.
- Annotation files record the dataset revision they were derived from. A local dataset
  file is recorded by SHA-256, never as the pinned revision.
- Runs made before the first commit (`smoke_heuristic_v1`, `smoke_heuristic_v2`) record
  `codetrace_git_commit: null` and are preserved unchanged.

## Verification matrix

| item | state | evidence |
|---|---|---|
| Dataset pinned | verified | 300 test instances; fresh download reproduces the evaluator-export SHA-256 |
| Dataset license | **unverified** | no license tag on the dataset card |
| Official evaluator CLI | executed locally on empty patches | 3 submitted, 3 `empty_patch`, 0 errors; no image pulled |
| Run-summary parser | verified on a real artifact | unmodified harness summary committed as a test fixture |
| `report.json` parser | verified against official grading code only | `get_eval_report` on synthetic logs: resolved, regression, not-fixed, apply-failure. **No container-produced report parsed yet** |
| Anthropic adapter | verified with mocks only | real SDK on a mock transport; **never called against the API** |
| CI workflow | simulated locally only | fresh clone: `uv sync --locked`, ruff, mypy, tests pass. **Never run on GitHub** |
| Workflow security | statically tested | actions pinned to commit SHAs, `persist-credentials: false`, read-only permissions, no secrets, no `${{ }}` in shell, timeouts ≤ 120 min |
| Local tests | passing | 101 passed, 0 skipped (with `eval` and `anthropic` extras); ruff and strict mypy clean |

## Phase 2 (offline) status

| step (PROTOCOL.md §11) | state | evidence |
|---|---|---|
| 1. Evidence-unit schema and storage | done | `codetrace.evidence`; isolation tests (agent-side modules never import evaluator-side ones; tools cannot reach `annotations/`) |
| 2. Automatic edit sites | done | unit tests on synthetic diffs; the 3 pilot drafts were checked by hand against base-commit files (all three land exactly on the changed or bracketing lines) |
| 3. Metrics | done | `codetrace.metrics`; 11 known-answer scenarios on traces from the real agent loop (resets, partial re-reads, same-turn views, failed edits, undefined cases, tau, macro aggregation) |
| 4. Annotation guide and pilot labels | **partial** | guide (`docs/ANNOTATION_GUIDE.md`) and tooling done; `annotations/auto/` holds edit-site drafts only. **Support units need two human annotators; none have been labelled** |
| 5 to 7 | not started | 5 needs approval for spend and the Docker gate |

Protocol revision made before any model run: the patch-generation moment is the request
whose response contained the first successful edit (PROTOCOL.md §3).

Provisional pipeline check (not a result): `codetrace analyze --annotation-set auto
--allow-provisional` on `smoke_heuristic_v2` gives DR = 0 for all 3 runs (the scripted
explorer never viewed a reference-fix location, in agreement with the earlier leakage
audit), and CRR undefined (`no_patch`) for all 3.

## Known limitations

- The Docker gate (PROTOCOL.md §10) is unmet, so no outcome may be reported.
- The adapter mocks cannot detect server-side rejection of request content. The first live
  run is an engineering check.
- Support-unit ground truth does not exist yet. Discovery recall computed now covers edit
  sites only.
- Actions pinned to existing majors (checkout v4, setup-uv v6, upload-artifact v4). Newer
  majors exist; upgrading is a separate, tested change.
- Local venv base is Anaconda CPython 3.12.2 (isolated). CI uses a uv-managed 3.12.
- Unauthenticated Docker Hub pulls on the runner may be rate-limited and would surface as
  harness errors.
