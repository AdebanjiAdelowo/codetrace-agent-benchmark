# CodeTrace: Measuring Context Loss in Agentic Software Engineering

CodeTrace studies whether coding agents lose task-relevant repository information between
code exploration and patch generation, and whether explicit evidence preservation
improves software engineering outcomes.

It separates four quantities that are usually conflated:

1. **Discovery**: did the agent ever view the code that a correct fix depends on?
2. **Retention**: was that code still in the agent's accessible context when it wrote the patch?
3. **Utilization**: did the patch actually depend on that evidence?
4. **Correctness**: does the patch resolve the issue without regressions, as judged by the
   official benchmark evaluator?

## How it works

- **Benchmark**: [SWE-bench Lite](https://huggingface.co/datasets/SWE-bench/SWE-bench_Lite),
  pinned to a fixed Hugging Face revision. Resolution is decided only by the official
  SWE-bench evaluation harness.
- **Information hiding**: the agent receives an `AgentTaskView` (repository, base commit,
  issue text) and nothing else. Reference patches, test patches, test names, and hints
  are held in a separate evaluator-only record. Repositories are exported at the base
  commit into a fresh single-commit git repository, so later history (including the
  upstream fix) is not reachable.
- **Harness**: a small, inspectable tool-use loop with read, search, and edit tools.
  There is no shell tool; agent-generated commands are never run on the host.
- **Tracing**: every run writes a JSONL trace of model requests and responses, token
  usage, tool calls and results, every code span made visible to the agent, context
  snapshots before each edit and submission, context resets, and the final patch.
- **Context model**: history is append-only within a segment. When the context limit is
  reached, the harness starts a new segment and logs exactly which code spans left the
  agent's context. This makes "evidence available before patch generation" a precisely
  defined, checkable quantity.

## Agent configurations

| ID | Configuration |
|----|---------------|
| A  | Standard agent baseline |
| B  | Agent with a structured evidence ledger |
| C  | Evidence ledger plus targeted evidence retrieval |

Model, task set, tools, environment, and budgets are held constant across configurations.

## Repository layout

```
src/codetrace/
  dataset.py          SWE-bench loading, information-hiding split, task selection
  workspace.py        sanitized repository snapshots and patch extraction
  tools.py            sandboxed repository tools
  agent.py            agent loop with trace instrumentation
  trace.py            trace schema, writer, validator
  evaluation.py       predictions file, official evaluator command, report parsing
  models/             model interface, Anthropic adapter, offline scripted models
configs/              experiment configurations
docs/                 methodology, metric definitions, protocol, resources
schemas/              exported JSON schema for traces
tests/                unit and integration tests (offline, CPU only)
```

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and git. Runs on Apple Silicon and Linux; no GPU needed.

```bash
uv sync --group dev
uv run pytest                 # offline test suite
uv run ruff check . && uv run mypy

# Deterministic pipeline smoke run (no API calls): explores real SWE-bench Lite
# repositories with a scripted policy and writes traces and predictions.
uv run codetrace run --config configs/smoke_heuristic.yaml

# Live model runs make paid API calls and require an explicit flag.
uv sync --extra anthropic
uv run codetrace run --config configs/pilot_anthropic.yaml --allow-paid
```

Each experiment writes `runs/<experiment_id>/` containing per-run traces, patches,
predictions in the official SWE-bench format, and an `evaluate.sh` script that invokes
the official evaluator. Evaluation needs Docker and substantial disk space; see
[docs/RESOURCES.md](docs/RESOURCES.md). The evaluator can also run remotely through the
harness's Modal backend.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components and data flow
- [docs/PROTOCOL.md](docs/PROTOCOL.md): experimental protocol and metric definitions
- [docs/TRACE_SCHEMA.md](docs/TRACE_SCHEMA.md): trace event reference
- [docs/DATASET.md](docs/DATASET.md): dataset provenance, licensing, and task selection
- [docs/RESOURCES.md](docs/RESOURCES.md): compute, disk, and cost requirements
- [docs/STATUS.md](docs/STATUS.md): planned, executed, and verified results

## Scope

Findings from Python repositories in SWE-bench Lite do not establish behavior on other
languages, proprietary codebases, or industrial-scale systems.
