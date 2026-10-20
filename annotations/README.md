# Evidence annotations (evaluator side)

Never visible to agents: agent workspaces are separate directories, the agent's tools
cannot leave their workspace, and agent-side modules never import annotation code
(tests/test_evidence.py, tests/test_annotation_package.py).

| set | who | status | use |
|---|---|---|---|
| `auto/` | machine (`annotator_kind: auto`) | `auto_draft` | edit sites derived from reference patches; reviewers confirm them |
| `assistant_draft/` | AI assistant (`annotator_kind: assistant`) | `auto_draft` | candidate support units with confidence and uncertainty; **not human labels**; kept out of review packages; compared with human labels after review |
| `reviewer_a/`, `reviewer_b/` | independent humans | `annotated` | returned review files (not yet present) |
| `adjudicated/` | humans, after discussion | `adjudicated` | final ground truth used by `codetrace analyze` (not yet present) |

The schema enforces provenance: only `annotator_kind: human` files may have status
`annotated` or `adjudicated`.

Workflow: `codetrace annotate package` builds blinded per-reviewer packages in the
git-ignored `review_packages/` directory (see docs/REVIEWER_INSTRUCTIONS.md and
docs/ANNOTATION_GUIDE.md).
