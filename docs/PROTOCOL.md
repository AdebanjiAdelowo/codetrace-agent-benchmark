# Experimental Protocol and Metric Definitions

Status of this document: **design (pre-registration draft)**. Metric code is Phase 2 work;
nothing here has been computed on model runs yet. See [STATUS.md](STATUS.md).

## 1. Research questions

- **RQ1 (discovery)**: How often does a coding agent view the code a correct fix depends on?
- **RQ2 (retention)**: Of the relevant code it discovered, how much is still in its accessible
  context when it starts writing the patch?
- **RQ3 (intervention)**: Does an evidence ledger (B), or a ledger plus targeted retrieval (C),
  change resolved rate, retention, or cost relative to the baseline (A)?
- **RQ4 (utilization)**: When relevant evidence is present at patch time, does the patch depend
  on it? (Answered only through ablation, never from presence alone.)

## 2. Units and ground truth

### 2.1 Evidence units

For each task, the ground truth is a set of **evidence units**
`E* = {e_1, ..., e_n}`, each a code span in the repository at `base_commit`:

| field | meaning |
|---|---|
| `path`, `start_line`, `end_line` | 1-indexed inclusive span at `base_commit` |
| `kind` | `edit_site` (code the reference fix changes) or `support` (code needed to understand or correctly write the fix: callee definitions, invariants, call sites whose behavior must be preserved) |
| `criticality` | `required` or `helpful` |

All primary metrics use `E*_req`, the required units. Helpful units feed secondary analyses only.

### 2.2 Annotation protocol (summary; full guide is a Phase 2 deliverable)

1. **Edit sites (automatic, then checked).** Derived from the reference patch: each hunk's
   removed lines map to their base-commit lines. A pure insertion maps to the two anchor lines
   around the insertion point. Contiguous lines merge into one unit. These are always `required`.
2. **Support units (manual).** Two annotators work independently. They see the issue, the
   reference patch, and the test patch, and mark spans that a developer would *need to read* to
   produce a fix of equivalent correctness. The test for `required`: without this information,
   a competent developer would plausibly write an incorrect fix.
3. **Agreement.** Line-level F1 between annotators for each task, plus Cohen's kappa on
   `required` vs `helpful` for matched spans. Disagreements are adjudicated by discussion, and
   the adjudicated set is final. Pre-adjudication agreement is always reported.
4. **Isolation.** Annotations live in an evaluator-side directory. The agent loop never reads
   them; the information-hiding tests cover this.
5. **Pilot.** Label the 3 pilot tasks first, measure agreement, and revise the guide before
   labelling the 10 to 20 task set.

## 3. Instrumented quantities (from traces)

- `V_r(t)`: code spans viewed in run `r` up to step `t` (`code_location_viewed` events,
  emitted by `read_file`, `search_code`, and later by retrieval tools).
- `A_r(t)`: the **accessible context** at step `t`. History is append-only within a
  segment, so `A_r(t)` is the set of spans viewed in the current segment. Configurations B
  and C add spans whose lines are reproduced verbatim in a ledger, compaction note, or
  retrieval result inside the current segment. Logged as `context_snapshot` events.
- `t_edit`: the step of the **first successful edit** (`replace_in_file` / `create_file`
  with no error). This is the primary "patch generation" moment. A secondary analysis uses the
  snapshot before `submit`.

A unit `e` **is covered** by a set of spans `S` if the lines of `e` covered by spans in `S`
on the same path make up at least a fraction `tau` of `e`'s lines. Primary `tau = 0.5`;
sensitivity analysis at `tau` = any overlap and `tau = 1.0`.

## 4. Metrics

Notation for run `r` on task `k`: `R = E*_req(k)`.

### 4.1 Discovery recall

`DR_r = |{e in R : e covered by V_r(T)}| / |R|`

- `T = t_edit` if the run made a successful edit, otherwise the last step of the run.
- Denominator edge case: `|R| = 0` should not occur, because every task has at least one edit
  site. If it does, the task is excluded and reported as an annotation defect.

### 4.2 Conditional retention rate

`D_r = {e in R : e covered by V_r(t_edit - )}` (discovered before the first edit)

`CRR_r = |{e in D_r : e covered by A_r(t_edit)}| / |D_r|`

- **Undefined** when the run has no successful edit (a no-patch run) or `D_r` is empty.
  Undefined runs are excluded from the CRR mean, and their counts are always reported as
  separate rows: `n_no_patch` and `n_nothing_discovered`.
- CRR conditions on discovery, so a low DR cannot masquerade as a retention failure.

### 4.3 Context-loss rate

Run-level indicator: `L_r = 1` if `D_r \ A_r(t_edit)` is non-empty, else `0`.
Defined on the same runs as CRR.

`CLR = mean over eligible runs of L_r` (the fraction of runs that lost at least one discovered
required unit before patching). Unit-level loss `1 - CRR_r` is also reported. Every lost unit
is attributed to the `context_compaction` event that removed it.

### 4.4 Outcome and cost metrics

| metric | definition | source |
|---|---|---|
| Resolved | `resolved == true` in the official per-instance `report.json` | SWE-bench harness only |
| Regression | `PASS_TO_PASS.failure` non-empty | SWE-bench harness only |
| Patch applied | `patch_successfully_applied` | SWE-bench harness only |
| Evaluation error | harness error or missing report: reported as its own category, never as unresolved | harness logs |
| Tokens | sum over model calls of `total_processed` (uncached input + cache reads + cache writes + output), also reported per component | `model_response.usage` (provider-reported) |
| Cost (USD) | per-component tokens times the list price on the run date, with the price table recorded alongside the results | derived |
| Overhead | tokens and wall time attributable to ledger maintenance and retrieval calls (B and C) | tagged trace events |
| Wall time | `run_end` minus `run_start`, plus model latency and tool time separately | trace |

### 4.5 Aggregation

The primary estimate is the **task-macro mean**: average the 3 seeds within a task, then
average tasks with equal weight. The micro (pooled-run) mean is secondary. Undefined values
are excluded at the run level and their counts are reported for every cell.

## 5. Design

- **Tasks**: Pilot of 3 tasks, then 10 to 20 SWE-bench Lite test instances chosen by the
  deterministic rule in [DATASET.md](DATASET.md), fixed before any model run.
- **Configurations**: A, B, C, all with the same model and version, effort, `max_tokens`,
  tools (B and C add only ledger and retrieval tools), system prompt core, budgets, and
  context limit. Every difference is listed in each config's manifest.
- **Replication**: 3 independent runs per (task, configuration), with run order randomized
  and interleaved across configurations to spread time-of-day and API drift.
- **Budgets** count all processed tokens, including retrieval and ledger overhead, so B and
  C get no free context.
- **Smoke gate**: before any scaled run, one live run per configuration on one pilot task
  must produce a valid trace, pass `scripts/audit_runs.py`, and be graded by the official
  harness.

## 6. Analysis plan

- **Per-task table**: resolved fraction (k/3), DR, CRR, L, tokens, and time for each
  configuration.
- **Paired comparisons** (B vs A, C vs A, C vs B), paired over tasks: the difference in
  task-level resolved rate with a 95% percentile bootstrap CI over tasks (10,000 resamples,
  fixed seed), plus an exact sign test on tasks with non-zero difference. A mixed-effects
  logistic model (random intercept per task) is secondary.
- **Power caveat**: with 10 to 20 tasks and 3 seeds, only large effects (around 20 points
  or more in resolved rate) are reliably detectable. Smaller differences are reported as
  inconclusive, not as "no effect".
- **Failure taxonomy**: each unresolved run is labelled not-discovered, discovered-but-lost,
  retained-but-wrong-fix, no-patch, or evaluation-error, using the definitions above.

## 7. Evidence-ablation experiments (utilization, RQ4)

Presence of evidence does not show it was used. Utilization is probed by controlled
counterfactual replays:

1. Take runs whose `A_r(t_edit)` covers at least one required unit.
2. Rebuild the conversation as it stood just before `t_edit` and start a **new** conversation
   from it (no edits to an existing one), in three arms:
   - **intact**: unchanged;
   - **evidence ablated**: tool-result text covering the required units replaced by a
     same-length neutral placeholder;
   - **control ablation**: an equal number of lines from non-evidence spans replaced the same way.
3. Let the agent continue under the original budget remainder, 3 replays per arm, and grade
   with the official evaluator.
4. Utilization effect = resolved(intact) - resolved(evidence ablated), compared against
   resolved(intact) - resolved(control ablation). A difference beyond the control supports
   the claim that the patch depended on that evidence.

Lexical overlap between evidence and patch is reported only as a descriptive proxy, never as
evidence of utilization. Replays start from rebuilt transcripts, so they omit the model's
original hidden reasoning; this is a stated limitation.

## 8. Claims policy

- Configurations are randomized interventions on fixed tasks. Differences in outcome may be
  attributed to the configuration *for these tasks, this model, and these budgets*, within
  the uncertainty reported.
- The *mechanism* ("failures are caused by context loss") is claimed only where ablation
  supports it. Correlations between CRR and resolution are reported as associations.
- No generalization beyond Python repositories in SWE-bench Lite: not to other languages,
  proprietary or safety-critical codebases, or industrial (for example automotive) scale.

## 9. Threats to validity

- **Selection bias**: the pilot favors small, localized fixes (selection uses reference-patch size).
- **Annotation subjectivity** of support units: mitigated by dual annotation and reported agreement.
- **Contamination**: SWE-bench issues and fixes are public and may be in model training data,
  which can inflate resolved rates independent of exploration.
- **Provider drift**: model behavior may change over the collection period. Mitigation:
  short collection windows, interleaved order, and the model id plus response `model` field
  recorded per call.
- **Evaluation environment**: official images are x86_64. Emulated runs on Apple Silicon may
  hit timeouts. Infrastructure errors are reported separately and re-run, never scored as
  failures.
- **Tool design**: there is no shell or test execution, which differs from agents that run
  tests. Results are conditional on this tool set.
