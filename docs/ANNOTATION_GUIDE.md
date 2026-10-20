# Evidence Annotation Guide (v1)

This guide defines how ground-truth **evidence units** are labelled for each task. The
units are the denominator of discovery recall and retention (PROTOCOL.md §2 and §4).
Annotation is evaluator-side work. Annotators see the reference patch and the tests; agents
never see the annotations.

## 1. What an evidence unit is

A contiguous line range in one file **at the task's `base_commit`** whose content a
developer needs in order to write a correct fix.

| field | values |
|---|---|
| `path` | repository-relative path at `base_commit` |
| `start_line`, `end_line` | 1-indexed, inclusive, at `base_commit` (not after the fix) |
| `kind` | `edit_site`: code the reference fix changes. `support`: code that must be read to write the fix correctly |
| `criticality` | `required` or `helpful` (see §3) |
| `source` | `auto_patch` (machine-derived) or `manual` |
| `rationale` | one sentence: why this code is needed |

## 2. Edit sites (automatic, then checked)

`codetrace annotate init` derives edit sites from the reference patch:

- removed or replaced lines map to their own base-commit lines;
- a pure insertion maps to the base lines on either side of the insertion point (only the
  line before, at the end of a hunk or file; line 1 at the start of a file);
- adjacent ranges merge. Files created by the fix have no base lines and are listed in `notes`.

The annotator checks each automatic edit site against the base file
(`codetrace annotate validate --check-files`) and keeps it `required`. Edit sites may be
widened only to complete a statement that spans several lines, and the rationale must
say so.

## 3. Support units (manual)

Read the issue, the reference patch, and the tests. Then ask, for each piece of code a
developer would plausibly consult:

> Without the information in these lines, would a competent developer who had read only
> the issue plausibly write a fix that fails the tests or changes behavior elsewhere?

- **Yes** → `required`. Typical cases: the definition of a function or class whose
  contract the fix relies on, the caller whose input triggers the bug, an invariant or
  constant the fix must respect, a sibling implementation the fix must mirror.
- **Useful but not decisive** → `helpful`. Typical cases: related documentation,
  neighboring code that confirms an assumption, similar code elsewhere.
- **Not evidence**: test files (they are hidden from the agent), the issue text itself,
  generic utilities everyone knows, and code visible only after the fix.

Granularity: mark the **smallest range that carries the needed information**, usually a
signature plus the few lines that matter, not whole functions. Do not split a unit just
because blank lines or comments occur inside it.

## 4. Process

1. `codetrace annotate init --instance-ids <ids>` creates `annotations/auto/<id>.yaml`.
2. Each annotator copies it to `annotations/<annotator_id>/<id>.yaml`, sets `annotator`,
   `annotation_set`, and `status: annotated`, then adds support units **independently**
   (no discussion before both are done).
3. `codetrace annotate validate --check-files annotations/<annotator_id>/*.yaml`.
4. `codetrace annotate agreement annotations/<a>/<id>.yaml annotations/<b>/<id>.yaml`
   reports line-level F1 on required units and on all units, plus Cohen's kappa on
   criticality for matched units (line Jaccard ≥ 0.5). Record the results before discussion.
5. Adjudicate by discussion. Write `annotations/adjudicated/<id>.yaml` with
   `status: adjudicated`. Record the disagreements that were resolved in `notes`.
6. Freeze: commit the adjudicated set before any live run is analyzed. Later changes need
   a new guide version and must be reported.

`codetrace analyze` uses `annotations/adjudicated/` by default and refuses any other
status unless `--allow-provisional` is passed. In that case every output is marked provisional.

## 5. Pilot

Run the process on the 3 pilot tasks first. If required-unit F1 is below 0.7 on any task,
revise this guide (with examples from the disagreement) before labelling more tasks.
