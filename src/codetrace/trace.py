"""Structured JSONL trace schema and writer.

One run of one agent configuration on one task produces one ``trace.jsonl`` file.
Every line is a single :class:`TraceEvent`. The first line is always a
``run_start`` event carrying the full :class:`RunManifest`; the last line of a
completed run is ``run_end``. A trace without ``run_end`` is an aborted run and
must be reported as such, never silently dropped.

Event kinds are deliberately enumerated up front (including the Phase 2 context
events) so that traces from every phase share one schema.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# 0.2.0: cache-write TTL split in TokenUsage; optional manifest fields for tool/template
# hashes, workspace path, price table, and experiment freeze. 0.1.0 traces remain valid.
SCHEMA_VERSION = "0.2.0"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class EventKind(StrEnum):
    RUN_START = "run_start"
    MODEL_REQUEST = "model_request"
    MODEL_RESPONSE = "model_response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    # A file region became visible to the agent (emitted by read/search tools).
    CODE_LOCATION_VIEWED = "code_location_viewed"
    # Context-management events (Phase 2 analysis keys off these).
    CONTEXT_SNAPSHOT = "context_snapshot"
    CONTEXT_COMPACTION = "context_compaction"
    RETRIEVAL = "retrieval"
    PATCH_GENERATED = "patch_generated"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ERROR = "error"
    RUN_END = "run_end"


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    # Provider breakdown of cache writes by TTL (subsets of cache_creation_input_tokens).
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    # "provider" when reported by the API; "approx_chars_div_4" for the scripted model.
    method: str = "provider"

    @property
    def total_processed(self) -> int:
        """All tokens the model processed: uncached input + cache reads + cache writes
        + output. (With prompt caching, ``input_tokens`` counts uncached input only.)"""
        return (
            self.input_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
            + self.output_tokens
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        method = self.method if self.method == other.method else "mixed"
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_write_5m_tokens=self.cache_write_5m_tokens + other.cache_write_5m_tokens,
            cache_write_1h_tokens=self.cache_write_1h_tokens + other.cache_write_1h_tokens,
            method=method,
        )


class CodeSpan(BaseModel):
    """A 1-indexed, inclusive line range in a repository file (repo-relative path)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class RunManifest(BaseModel):
    """Everything needed to identify and reproduce a run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    run_id: str
    experiment_id: str
    agent_config: str  # "A_baseline" | "B_ledger" | "C_ledger_retrieval"
    seed: int
    task_instance_id: str
    dataset_name: str
    dataset_revision: str
    repo: str
    base_commit: str
    model_provider: str
    model_name: str
    model_params: dict[str, Any]
    system_prompt_sha256: str
    system_prompt: str
    tool_names: list[str]
    budget: dict[str, Any]
    environment: dict[str, Any]
    codetrace_git_commit: str | None
    started_at: str
    # Added in 0.2.0 (optional so 0.1.0 manifests still validate).
    tool_specs_sha256: str | None = None
    task_template_sha256: str | None = None
    workspace_path: str | None = None
    pricing: dict[str, Any] | None = None
    freeze: dict[str, Any] | None = None


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    run_id: str
    seq: int = Field(ge=0)
    step: int = Field(ge=0)  # agent turn index; 0 = before the first model call
    kind: EventKind
    timestamp: str = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class TraceWriter:
    """Append-only JSONL writer. Flushes every event so aborted runs keep their prefix."""

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self._seq = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing trace {path}")
        self._fh = path.open("w", encoding="utf-8")

    def emit(self, kind: EventKind, step: int, **payload: Any) -> TraceEvent:
        event = TraceEvent(run_id=self.run_id, seq=self._seq, step=step, kind=kind, payload=payload)
        self._fh.write(event.model_dump_json() + "\n")
        self._fh.flush()
        self._seq += 1
        return event

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_trace(path: Path) -> list[TraceEvent]:
    """Parse and validate a trace file; raises on any malformed line."""
    events = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                events.append(TraceEvent.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"{path}:{lineno}: invalid trace event: {exc}") from exc
    return events


def validate_trace(events: list[TraceEvent]) -> list[str]:
    """Structural checks. Returns a list of problems (empty if valid)."""
    problems: list[str] = []
    if not events:
        return ["empty trace"]
    if events[0].kind is not EventKind.RUN_START:
        problems.append("first event is not run_start")
    else:
        try:
            RunManifest.model_validate(events[0].payload["manifest"])
        except Exception as exc:
            problems.append(f"run_start manifest invalid: {exc}")
    run_ids = {e.run_id for e in events}
    if len(run_ids) != 1:
        problems.append(f"multiple run_ids in one trace: {sorted(run_ids)}")
    seqs = [e.seq for e in events]
    if seqs != list(range(len(events))):
        problems.append("seq numbers are not contiguous from 0")
    if events[-1].kind is not EventKind.RUN_END:
        problems.append("trace does not end with run_end (aborted run)")
    calls = {e.payload.get("tool_use_id") for e in events if e.kind is EventKind.TOOL_CALL}
    results = {e.payload.get("tool_use_id") for e in events if e.kind is EventKind.TOOL_RESULT}
    if calls != results:
        problems.append(f"unmatched tool calls/results: {sorted(map(str, calls ^ results))}")
    return problems


def export_json_schema(path: Path) -> None:
    schema = {
        "trace_event": TraceEvent.model_json_schema(),
        "run_manifest": RunManifest.model_json_schema(),
        "token_usage": TokenUsage.model_json_schema(),
        "code_span": CodeSpan.model_json_schema(),
    }
    path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
