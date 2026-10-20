# Architecture

```
            evaluator side                         |               agent side
                                                   |
 HF dataset (pinned) --> dataset.EvaluatorTask ----+--> .agent_view() --> AgentTaskView
        |                        |                 |                         |
        |                        |                 |   workspace.ensure_mirror (depth-1 fetch of base_commit)
        |                        |                 |   workspace.create_workspace (fresh 1-commit repo)
        |                        |                 |                         |
        |                        |                 |   agent.run_agent <--> models.* (scripted | anthropic)
        |                        |                 |        |   ^                 
        |                        |                 |        v   |                 
        |                        |                 |     tools.RepoTools (read/search/edit/submit, sandboxed)
        |                        |                 |        |
        |                        |                 |        +--> trace.TraceWriter --> runs/.../trace.jsonl
        |                        |                 |        +--> Workspace.diff()  --> runs/.../patch.diff
        v                        v                 |
 export_for_evaluator    evaluation.write_predictions  <-------------------+
        |                        |
        +-----> evaluation.evaluator_command --> evaluate.sh --> official SWE-bench harness (Docker)
                                                                       |
                                     evaluation.parse_instance_report <-+  logs/run_evaluation/.../report.json
```

## Components

| module | responsibility | key invariants |
|---|---|---|
| `dataset.py` | load the pinned split, split each record into `EvaluatorTask` and `AgentTaskView`, select tasks deterministically, export evaluator JSONL | the agent type has no field for patches, tests, or hints |
| `workspace.py` | depth-1 fetch of `base_commit`, export to a fresh single-commit repository, diff | no later upstream objects exist in the cache or the workspace |
| `tools.py` | sandboxed read, search, edit, and submit tools; report viewed spans | path confinement, `.git` blocked, no command execution |
| `agent.py` | tool-use loop, budgets, logged context resets, snapshots before edits | append-only history within a segment; every exit writes a patch and `run_end` |
| `models/` | `ModelClient` protocol; `AnthropicModel` (paid, gated by `--allow-paid`); `ScriptedModel` and `HeuristicExplorer` (offline) | scripted token counts are labelled as approximations |
| `trace.py` | Pydantic schema, JSONL writer, validator, JSON-schema export | sequence numbers contiguous; every tool call has a result |
| `evaluation.py` | predictions file, official command, report parsing | CodeTrace never decides resolution itself |
| `cli.py` | `select-tasks`, `run`, `validate-trace`, `export-schema` | refuses paid providers without `--allow-paid` |
| `netfix.py` | opt-in IPv4-only resolution (`CODETRACE_FORCE_IPV4=1`) for networks with broken IPv6 | off by default; not part of the method |

## Design decisions

- **Own minimal harness instead of an existing agent framework.** Context management is the
  object of study, so every token entering the context must be attributable. A small loop is
  easier to inspect and to hold constant across configurations.
- **No shell tool.** Agent commands are never run on the host, and test outcomes come only
  from the official evaluator. This limits ecological validity, as listed in the protocol's
  threats to validity.
- **Context resets instead of in-place pruning.** Current Claude models bind thinking blocks
  to unedited history, so editing earlier turns can be rejected. A reset starts a new
  conversation and logs exactly which spans left context, which also makes "accessible
  context" a precise set.
- **No server-side refusal fallback.** A fallback would switch models mid-run and confound
  comparisons, so refusals are recorded as their own exit status.
- **Prompt caching on.** It affects cost only. Budgets count all processed tokens, so caching
  does not change how much work a run is allowed.
