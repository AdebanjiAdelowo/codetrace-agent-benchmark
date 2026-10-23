# Dataset: Provenance, Licensing, and Task Selection

## Source

| item | value | verified |
|---|---|---|
| Dataset | `SWE-bench/SWE-bench_Lite` (Hugging Face) | yes, 2026-10-20 |
| Pinned revision | `b0dde1093fe417d83b7184254edf8199c1f0dff5` | yes |
| Split used | `test`, 300 instances | yes (counted) |
| Evaluator | `swebench==5.0.2` (PyPI), MIT License | yes (PyPI metadata) |

### Why the `SWE-bench/` copy and not `princeton-nlp/`

The project first pinned `princeton-nlp/SWE-bench_Lite` (revision `6ec7bb89…`). A
comparison on 2026-10-20 found:

- swebench 5.0.2 requires the `image`, `eval_script`, and `log_parser` fields when it builds
  test specs (`swebench/harness/utils.py::make_test_spec`). Only the `SWE-bench/` copy has them.
- Across all 300 instances, `instance_id`, `base_commit`, `patch`, `test_patch`,
  `problem_statement`, `version`, and `environment_setup_commit` are identical in both copies.
- After normalizing encoding (JSON string vs list), `FAIL_TO_PASS`/`PASS_TO_PASS` differ for
  exactly one instance, `sympy__sympy-13177`, which the `princeton-nlp` copy lists with one
  extra test in each field. It is not in the pilot.

Evaluation receives a **local JSONL export of the pinned revision**
(`runs/<experiment>/evaluator_dataset.jsonl`), so the harness cannot silently load a newer
Hub version. The harness's own loaders (`load_swebench_dataset`, `make_test_spec`,
`get_predictions_from_file`) were run on these files and accept them.

## Licensing

| component | license | status |
|---|---|---|
| SWE-bench code and harness | MIT | verified (PyPI) |
| SWE-bench Lite dataset card | **no license tag** on either Hugging Face copy | **unverified**: confirm terms before redistributing any dataset content |
| Task repositories | their own licenses (for example pytest MIT, sympy BSD, pylint GPL-2.0) | **unverified**: check each repository's LICENSE at `base_commit` before redistributing code excerpts |

Policy: this repository commits no dataset records, repository code, or traces. All of
these are written under the git-ignored `runs/` and `.codetrace_data/` directories. Reports
quote only small excerpts, as research use requires.

### Pinned pytest review (2026-10-23)

The pinned dataset card was fetched directly: no `license` key and no licensing text
were present. The SWE-bench GitHub license endpoint reports MIT; pytest's `LICENSE`
at `6995257cf470d2143ad1683824962de4071c0eb7` is also MIT. Other task repositories
remain unverified here. Harness/source licensing does not automatically cover all
benchmark issue prose, compiled metadata, or generated traces. Follow the component
matrix and redistribution boundaries in [LICENSING.md](LICENSING.md).

## Information hiding

- The agent receives `AgentTaskView(instance_id, repo, base_commit, problem_statement)`.
  `hints_text` is excluded (the "no hints" setting).
- Repositories are fetched at depth 1 at `base_commit` only, then exported into a fresh
  one-commit git repository. Later upstream commits never exist on disk for that repository.
- Tools reject `.git` and any path outside the workspace, including through symlinks.
- `scripts/audit_runs.py` checks every trace for test identifiers, test-patch lines, and
  reference-patch lines (excluding the agent's own patch).

## Pilot task selection

The rule is deterministic, implemented in `codetrace.dataset.select_tasks` and recorded in
`configs/tasks/pilot_selection.json`:

- go through repositories in priority order and take at most 1 task per repository, 3 in total;
- reference patch: 1 file, at most 2 hunks, at most 12 changed lines;
- 1 to 3 `FAIL_TO_PASS` tests;
- ties broken by fewest changed lines, then `instance_id`.

Repository priority favors projects whose evaluation environments are small:
`psf/requests`, `pallets/flask`, `pytest-dev/pytest`, `sympy/sympy`, `pylint-dev/pylint`.

**Selection history (reported for transparency).** The first pass used the first four
repositories and returned only 2 tasks. No `requests` instance met the `FAIL_TO_PASS` limit
(those with small patches have 4 to 12 F2P tests), and no `flask` instance met the hunk limit.
The criteria were left unchanged, and `pylint-dev/pylint` was appended as the next
repository. Re-running selection on the `SWE-bench/` copy produced the same three tasks.

| instance | repo version | reference patch | F2P | P2P |
|---|---|---|---|---|
| `pytest-dev__pytest-11143` | 8.0 | 1 file, 1 hunk, +1/-0 | 1 | 114 |
| `sympy__sympy-13480` | 1.1 | 1 file, 1 hunk, +1/-1 | 1 | 43 |
| `pylint-dev__pylint-7080` | 2.15 | 1 file, 1 hunk, +1/-0 | 1 | 120 |

These are deliberately easy, localized fixes. Pilot results will not represent SWE-bench
Lite as a whole.

## Scaling to 10 to 20 tasks (planned)

Freeze a rule before any model runs, for example the same filters with `per_repo = 2` across
all 12 Lite repositories, with a cap on evaluation image size. Record the selection output
and commit the config before the first live run.
