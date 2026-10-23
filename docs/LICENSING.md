# Licensing and redistribution review

Reviewed 2026-10-23 for the pinned pytest pilot. This is an evidence and publication
boundary review, not a blanket legal clearance or a license grant from CodeTrace.
Availability on GitHub or Hugging Face does not itself establish redistribution rights.

## Primary evidence inspected

- The exact pinned [SWE-bench Lite card](https://huggingface.co/datasets/SWE-bench/SWE-bench_Lite/blob/b0dde1093fe417d83b7184254edf8199c1f0dff5/README.md)
  was fetched without changing the dataset revision. Its YAML has no `license` key;
  its body has no licensing text. Card SHA-256:
  `ec47dedb6d82aa9556d8fffed8ab6c0aa6feaae6d4bf943466d3fdbfd158ae68`.
- The official [SWE-bench license endpoint](https://api.github.com/repos/SWE-bench/SWE-bench/license)
  reports SPDX `MIT` for [LICENSE](https://github.com/SWE-bench/SWE-bench/blob/main/LICENSE),
  blob `969657c73e7be4c9a796dfac715fb0d57b2acf8a`. This verifies the harness/project
  license; it does not resolve the card's missing terms for all third-party content.
- [pytest LICENSE at the pilot base commit](https://github.com/pytest-dev/pytest/blob/6995257cf470d2143ad1683824962de4071c0eb7/LICENSE)
  was read from the cached Git object at that exact commit. It is MIT, copyright
  Holger Krekel and others. License SHA-256:
  `ca836a5f9ecca3b2f350230faa20a48fb8b145653b5568d784862df864706b9b`.
- [GitHub Terms of Service, user-generated content](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service#d-user-generated-content)
  distinguishes authors' rights, platform permissions and contributions to licensed
  repositories. Platform hosting permission is not a substitute for a downstream license.

Downloaded cards/license bodies and machine-readable findings are local, Git-ignored
in `runs/final_offline_preflight_2026-10-23_d3a920f/`. Other pilot repositories and every
file's third-party notices have not been independently cleared in this review.

## Component-specific conclusions

| Material | What is established | Publication boundary / uncertainty |
|---|---|---|
| Benchmark metadata | Public task IDs, commits, repository names and benchmark instructions are available; no dataset license declaration found at the pin | Publish independently written descriptions, identifiers and aggregate results with attribution; do not infer permission to republish the compiled dataset or substantial issue prose |
| Original pytest source | Exact base-commit repository license is MIT | Redistribution of covered source is permitted subject to retaining required notices; inspect per-file exceptions and third-party components. This does not clear other task repositories |
| Reference patches | Derived from upstream changes; pytest repository licensing is relevant | Preserve provenance/notices and verify the touched files and contribution terms before publishing patches. No blanket license for every benchmark record is established; keep evaluator copies local |
| Test patches and test IDs | Public benchmark material held separately for evaluation | Same component-rights review as source/patches, plus experimental contamination risk; keep full evaluator records out of Git and inaccessible to the agent |
| Generated traces | Include agent-visible source excerpts, issue prose, tool results and potentially model text | A new trace does not erase underlying rights or privacy concerns. Public release requires component review, secret/personal-data redaction and provider-term review for future model output |
| Human evidence annotations | No adjudicated human labels exist yet | Obtain contributor permission/consent and an explicit annotation license before release; keep reviewer work isolated from the agent |
| CodeTrace research artifacts | Authored configurations, methodology, aggregate metrics and integrity hashes can be separated from dataset content | Publish these only after owner approval; retain academic attribution. Never bundle credentials, full records, raw traces or third-party workspaces as a side effect |

MIT permits copying, modifying and distributing covered software subject to its notice
conditions. It does not establish that unrelated issue authors granted the same rights,
that every dependency has identical terms, or that all combined benchmark fields are
covered. Reference/test patches must be assessed by their originating content, not by
the file extension of their exported JSONL container.

The benchmark's documented evaluation workflow supports its intended research use, but
is not treated here as a substitute for clear terms covering every field. Further owner
or maintainer clarification is needed for public dataset/trace redistribution and the
planned transmission of issue prose and source excerpts to an external model provider.
No maintainer was contacted and no third-party material was published in this review.

## Enforced experimental boundaries

The agent receives only repository identity, base commit and issue text through
`AgentTaskView`. It has no shell or arbitrary host-file access. The workspace is a fresh
single-commit export of the base tree; path checks exclude `.git`, outside paths and
escaping symlinks. Reference/test patches, hints, graded test lists and human annotations
are not in the agent-facing view or workspace. The evaluator receives a separate pinned
export. Leakage checks found no hidden test IDs, test-patch lines or reference-fix lines
in the new simulation trace. Those checks are heuristic and do not prove universal
information-flow security.

For now, keep full records, source workspaces, raw evaluator logs, traces and review
packages Git-ignored. Small sanitized research fixtures already tracked are intentional
test fixtures; that status does not authorize adding unsanitized benchmark exports.
