# Instructions for Evidence Reviewers

Thank you for reviewing. You are one of two **independent** reviewers. Your labels are
compared with the other reviewer's before any discussion, so please work alone.

## What you have

This package contains:

- `ANNOTATION_GUIDE.md`: the labelling rules. Read it first.
- `briefs/<task>.md`: for each task, the issue, the reference fix, the graded tests and
  test patch, and code excerpts around the lines the fix changes.
- `templates/<task>.yaml`: your annotation file for each task.

Briefs contain confidential evaluation material (reference fixes and hidden tests). Do
not paste them into any AI tool or share them.

## What to do for each task

1. Read the brief: issue, reference patch, and tests.
2. **Edit sites.** The template lists machine-derived edit sites (`source: auto_patch`).
   Check each range against the code at the base commit. Keep it, correct the lines, or
   explain in `uncertainty` why it is wrong. Do not delete it silently.
3. **Support units.** Add a unit for each piece of code a competent developer, having
   read only the issue, would need to read to write a correct fix. For each one, give:
   - `path`, `start_line`, `end_line` at the base commit (smallest range carrying the information);
   - `kind: support`, `source: manual`;
   - `role`: one of `definition`, `call_site`, `caller`, `invariant`, `sibling_pattern`,
     `data_flow`, `configuration`, `documentation`, `other`;
   - `criticality`: `required` (without it, a plausible fix would fail tests or break
     behavior) or `helpful`;
   - `confidence`: `high`, `medium`, or `low`;
   - `rationale`: one sentence on why the code is needed;
   - `uncertainty`: what could make this label wrong (leave empty only if nothing could);
   - `depends_on`: ids of units this one only makes sense together with (optional).
4. Record behavior outside the repository that the fix relies on (language semantics,
   standard library behavior) in `external_dependencies`.
5. Set `annotator` to your reviewer id and `status: annotated`.

Do not label test files or the issue text as evidence. Do not ask the other reviewer, and
do not look at any other annotation file for these tasks, until you have returned yours.

## Time and returning your work

Expect about 20 to 40 minutes per task. Return the three YAML files. The project
maintainer validates them (`codetrace annotate validate --check-files`), computes
agreement (`codetrace annotate agreement`), and only then schedules adjudication.

## Questions

If a rule in the guide is unclear, note your interpretation in `uncertainty` and continue.
Do not resolve it with the other reviewer before both annotations are returned.
