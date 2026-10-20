# Trace Schema (v0.1.0)

One `trace.jsonl` is written per run. Every line is a `TraceEvent`:

```json
{"schema_version": "0.1.0", "run_id": "...", "seq": 0, "step": 0,
 "kind": "run_start", "timestamp": "2026-10-20T00:36:01.123+00:00", "payload": {...}}
```

`seq` is contiguous from 0. `step` is the agent turn (0 means before the first model call).
The machine-readable schema is in [`schemas/trace.schema.json`](../schemas/trace.schema.json)
(regenerate with `codetrace export-schema`; CI checks it is current). Validate with
`codetrace validate-trace <files>`.

## Event kinds and payloads

| kind | payload |
|---|---|
| `run_start` | `manifest`: `RunManifest` (run, experiment, config, seed, task, dataset name and revision, repo, base commit, model provider, name, and params, full system prompt and its SHA-256, tool names, budget, environment, CodeTrace git commit, start time) |
| `context_snapshot` | `reason` (`before_model_request`, `before_replace_in_file`, `before_create_file`, `before_submit`), `segment`, `spans` (accessible code spans), `n_messages` |
| `model_request` | `segment`, `n_messages`, `new_message` (the newest message only; earlier ones are already in the trace) |
| `model_response` | `model` (as returned by the provider), `stop_reason`, `stop_details`, `usage` (`TokenUsage`), `latency_s`, `request_id`, `content` (all blocks, as appended to history) |
| `tool_call` | `tool_use_id`, `name`, `input` |
| `tool_result` | `tool_use_id`, `name`, `is_error`, `output`, `output_sha256`, `duration_s`, `files_modified` |
| `code_location_viewed` | `tool_use_id`, `source_tool`, `segment`, `span` (`path`, `start_line`, `end_line`) |
| `context_compaction` | `strategy`, `trigger_input_tokens`, `from_segment`, `to_segment`, `removed_spans`, `removed_messages`, `note` (verbatim text carried into the new segment) |
| `retrieval` | reserved for configuration C |
| `budget_exhausted` | `budget` (`max_steps`, `max_total_tokens`, `max_wall_s`) |
| `error` | `error_type`, `message` |
| `patch_generated` | `patch`, `patch_sha256`, `patch_bytes`, `files_modified` |
| `run_end` | `result` (`RunResult`: exit status, steps, summed usage, wall time, paths, modified files, number of compactions), `finished_at` |

`TokenUsage.method` is `provider` for API-reported counts and `approx_chars_div_4` for the
offline scripted models. Approximate counts must never be reported as model cost.

## Exit statuses

`submitted`, `max_steps`, `token_budget`, `wall_time`, `refusal`, `no_tool_calls`, `error`.
A patch is written in every case. A trace without `run_end` is an aborted run and is reported
as such.

## Per-run directory

```
runs/<experiment_id>/
  experiment_manifest.json         # commit SHA, dirty flag, dataset revision, task ids, environment
  config.resolved.json
  evaluator_dataset.jsonl          # pinned full records (evaluator side only)
  predictions/<config>__s<seed>.jsonl
  evaluate.sh                      # official harness commands
  evaluation/                      # harness working directory
    <model>.<run_id>.json          # harness run summary (schema_version 2)
    logs/run_evaluation/<run_id>/<model>/<instance_id>/report.json
    results.jsonl                  # `codetrace collect-eval`: one checked row per task and run
  runs/<run_id>/{trace.jsonl, patch.diff, result.json}
```
