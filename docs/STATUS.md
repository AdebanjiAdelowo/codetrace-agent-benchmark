# Status: Planned, Executed, Verified

Last updated: 2026-10-20. "Verified" means checked against an artifact or an independent
tool, as given in the evidence column. **No language model has been called. No benchmark
task has been graded inside a Docker container.**

## Commits

| commit | content |
|---|---|
| `9f1901de22fd6c265379deee8d6210bfe36f29aa` | Phase 1: harness, tracing, evaluation bridge, docs |
| `52236e89ba3519ebe33561aaf4fba47cf2d2760f` | Verification milestone: evaluator validation, adapter mocks, provenance |

Local only. No remote is configured and nothing has been pushed.

## Provenance

- Every run manifest records `codetrace_git_commit`, suffixed `+dirty` when the working tree
  differs from that commit.
- Every experiment writes `experiment_manifest.json` with the commit, a dirty flag, the
  dataset revision, task ids, and the environment.
- Paid runs refuse to start from a dirty or uncommitted tree (`--allow-dirty` overrides) and
  never reuse an existing experiment directory.
- Runs made before the first commit (`smoke_heuristic_v1`, `smoke_heuristic_v2`) record
  `codetrace_git_commit: null`. They are preserved unchanged: all 26 files were checked
  against SHA-256 fingerprints taken before the commit.

## Phase 1 verification matrix

| item | state | evidence |
|---|---|---|
| Dataset pinned (`SWE-bench/SWE-bench_Lite@b0dde10…`) | verified | 300 test instances; a fresh download reproduced the evaluator-export SHA-256 `41ada449…` byte for byte |
| Dataset license | **unverified** | no license tag on the dataset card (DATASET.md) |
| Official evaluator CLI (swebench 5.0.2) | **executed** | `run_evaluation` ran on the 3 smoke predictions: exit 0, 3 submitted, 3 `empty_patch`, 0 errors; no image pulled (Docker image count unchanged) |
| Task identifiers | verified | the harness accepted all 3 ids against the pinned export; the summary lists exactly these ids |
| Run-summary parser | verified on a real artifact | the unmodified harness summary is committed as `tests/fixtures/swebench_5.0.2_summary_empty_patches.json` and parsed in tests |
| Per-instance `report.json` parser | verified against official grading code | `swebench.harness.grading.get_eval_report` generated the reports for the resolved, regression, not-fixed, and patch-apply-failure paths; all parsed correctly. **Not yet on a container-produced report** |
| Summary/report consistency checks | implemented, tested | `collect_evaluation` rejects disagreeing summary and report, and graded ids missing a report |
| Parser bug fixed | done | `patch_applied` no longer falls back to `patch_exists` |
| Anthropic adapter | verified with mocks | real SDK (`anthropic` 1.15.0) on a mock HTTP transport: request shape, usage incl. cache tokens, refusal details, retry on 529, typed 400 error, append-only history with unchanged thinking signatures, hidden data never sent. **Never called against the real API** |
| CI workflow | simulated locally | fresh clone of `52236e8`: `uv sync --locked`, ruff, strict mypy, 70 passed / 7 skipped (swebench contract tests, by design), schema current. **Not run on GitHub** |
| Harness-validation workflow | simulated up to Docker | fresh clone, `--extra eval`: 20 contract/validation tests passed; `validate-harness --skip-docker` passed with a fresh dataset download. **Gold and control sets not run** |
| Workflow files | statically tested | YAML parses; manual-only trigger, read-only permissions, timeouts ≤ 120 min, no secrets. A YAML error in a step name was found and fixed by this check |
| Local test suite | passing | 77 passed, 0 skipped (with `eval` and `anthropic` extras) |

### Finding: empty patches never reach Docker

swebench 5.0.2 filters out empty `model_patch` predictions before any container starts
(`run_evaluation.py`, `empty_patch_ids`) and classifies them as `empty_patch`. The three
smoke predictions are empty by design, so **their official evaluation is complete**:
they were graded `empty_patch` locally, and running them remotely would produce the same
result. Validating Docker grading needs non-empty patches, so the harness-validation
bundle adds gold patches (expected: resolved) and a negative control (expected: applies,
unresolved).

## PENDING

| experiment | blocker | exact command |
|---|---|---|
| Harness validation with Docker (gold 3/3 resolved; control 3/3 applied and unresolved, F2P failing) | needs x86_64 Docker host with ≥15 GB free; local disk has 4.7 GiB free and the images are amd64-only | on GitHub (after creating a remote and pushing, **needs approval**): `gh workflow run swebench-harness-validation.yml`. On any x86_64 Linux host: `uv sync --locked --extra eval && uv run codetrace validate-harness --bundle eval_bundles/harness_validation_v1 --out runs/harness_validation_v1` |
| CI on GitHub | no remote; push needs approval | runs automatically on push to `main` |
| Live pilot, configuration A | needs authorization for paid API calls | `uv sync --extra anthropic && uv run codetrace run --config configs/pilot_anthropic.yaml --allow-paid` (requires a clean, committed tree) |

## Known limitations of the current verification

- No container-produced `report.json` has been parsed yet. Coverage comes from the
  official grading function on synthetic logs, not from real test runs.
- The adapter mocks fix the response shapes the tests assume. They cannot detect a
  server-side rejection, for example of echoed block fields. The first live run is the check.
- The local venv uses an Anaconda CPython 3.12.2 base (isolated, no system site packages).
  CI uses a uv-managed 3.12. The Python version is recorded in every manifest.
- Docker Hub rate limits for unauthenticated pulls could affect the GitHub runner. The
  workflow logs pull failures as harness errors rather than outcomes.

## Phases 2 to 5

Planned; see PROTOCOL.md §11 for the Phase 2 plan.
