"""Agent loop (configuration A: standard baseline) with full trace instrumentation.

Context model
-------------
The conversation is append-only within a *segment*. When the provider-reported
input size of the last request exceeds ``budget.context_token_limit``, the loop
performs a *reset*: it starts a new segment containing the task prompt plus a
compaction note, and emits a ``context_compaction`` event listing every code
span that left the accessible context. Hence, at any step, the code spans in the
agent's accessible context are exactly those viewed in the current segment
(plus anything restated in the compaction note, which is recorded verbatim).

Exit statuses: submitted | max_steps | token_budget | wall_time | refusal |
no_tool_calls | error. The workspace diff is written as the patch in every case
and the exit status is reported next to it.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from codetrace.dataset import DATASET_NAME, DATASET_REVISION, AgentTaskView
from codetrace.env_info import codetrace_git_commit, environment_info
from codetrace.models.base import ModelClient
from codetrace.tools import TOOL_NAMES, TOOL_SPECS, RepoTools
from codetrace.trace import (
    CodeSpan,
    EventKind,
    RunManifest,
    TokenUsage,
    TraceWriter,
    utc_now,
)
from codetrace.workspace import Workspace

SYSTEM_PROMPT = """\
You are a software engineering agent working in a Python repository checked out at a \
fixed commit. Your job is to resolve the GitHub issue given by the user by editing \
source files.

You have tools to list directories, read files, search code, and edit files. You cannot \
run commands or tests. Explore the repository to locate the relevant code, make a \
minimal, correct change, and call `submit` when you are done. Do not modify or add tests. \
Your file edits at the time you call `submit` are your final answer."""

TASK_TEMPLATE = """\
Repository: {repo}

<issue>
{problem_statement}
</issue>

Resolve the issue by editing the repository, then call `submit`."""


@dataclass(frozen=True)
class Budget:
    max_steps: int = 40
    # All processed tokens (uncached + cached input + output) summed over model calls.
    max_total_tokens: int = 1_500_000
    max_wall_s: float = 1800.0
    context_token_limit: int = 120_000  # triggers a logged context reset
    max_no_tool_nudges: int = 2


@dataclass
class RunResult:
    run_id: str
    instance_id: str
    agent_config: str
    exit_status: str
    steps: int
    usage: dict[str, Any]
    wall_s: float
    patch_path: str
    trace_path: str
    patch_bytes: int
    files_modified: list[str] = field(default_factory=list)
    n_compactions: int = 0


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _spans_payload(spans: list[CodeSpan]) -> list[dict[str, Any]]:
    return [s.model_dump() for s in spans]


def baseline_compaction_note(files_modified: list[str]) -> str:
    """Configuration A's reset note: only that a reset happened and which files were
    already edited (so the agent does not lose track of its own partial patch)."""
    mods = ", ".join(sorted(set(files_modified))) or "none"
    return (
        "[Context reset: earlier conversation was removed to stay within the context "
        f"limit. Files you have already modified: {mods}. Re-read anything you need.]"
    )


def run_agent(
    task: AgentTaskView,
    workspace: Workspace,
    model: ModelClient,
    out_dir: Path,
    *,
    experiment_id: str,
    agent_config: str = "A_baseline",
    seed: int = 0,
    budget: Budget | None = None,
    run_id: str | None = None,
) -> RunResult:
    if not isinstance(task, AgentTaskView):  # information-hiding guard
        raise TypeError("run_agent only accepts an AgentTaskView")
    budget = budget or Budget()
    run_id = run_id or f"{task.instance_id}__{agent_config}__s{seed}__{uuid.uuid4().hex[:8]}"
    run_dir = out_dir / run_id
    trace_path = run_dir / "trace.jsonl"
    patch_path = run_dir / "patch.diff"
    tools = RepoTools(workspace.path)

    manifest = RunManifest(
        run_id=run_id,
        experiment_id=experiment_id,
        agent_config=agent_config,
        seed=seed,
        task_instance_id=task.instance_id,
        dataset_name=DATASET_NAME,
        dataset_revision=DATASET_REVISION,
        repo=task.repo,
        base_commit=task.base_commit,
        model_provider=model.info.provider,
        model_name=model.info.name,
        model_params=model.info.params,
        system_prompt_sha256=_sha256(SYSTEM_PROMPT),
        system_prompt=SYSTEM_PROMPT,
        tool_names=TOOL_NAMES,
        budget=asdict(budget),
        environment=environment_info(),
        codetrace_git_commit=codetrace_git_commit(),
        started_at=utc_now(),
    )

    task_prompt = TASK_TEMPLATE.format(repo=task.repo, problem_statement=task.problem_statement)
    total = TokenUsage(
        method=("provider" if model.info.provider != "scripted" else "approx_chars_div_4")
    )
    files_modified: list[str] = []
    n_compactions = 0
    exit_status = "max_steps"
    step = 0
    t0 = time.monotonic()

    with TraceWriter(trace_path, run_id) as tw:
        tw.emit(EventKind.RUN_START, 0, manifest=manifest.model_dump())
        segment = 0
        messages: list[dict[str, Any]] = [{"role": "user", "content": task_prompt}]
        context_spans: list[CodeSpan] = []  # spans viewed in the current segment
        last_input_tokens = 0
        nudges = 0
        try:
            while True:
                if step >= budget.max_steps:
                    exit_status = "max_steps"
                    tw.emit(EventKind.BUDGET_EXHAUSTED, step, budget="max_steps")
                    break
                if total.total_processed >= budget.max_total_tokens:
                    exit_status = "token_budget"
                    tw.emit(EventKind.BUDGET_EXHAUSTED, step, budget="max_total_tokens")
                    break
                if time.monotonic() - t0 >= budget.max_wall_s:
                    exit_status = "wall_time"
                    tw.emit(EventKind.BUDGET_EXHAUSTED, step, budget="max_wall_s")
                    break

                if last_input_tokens > budget.context_token_limit:
                    note = baseline_compaction_note(files_modified)
                    tw.emit(
                        EventKind.CONTEXT_COMPACTION,
                        step,
                        strategy="reset_with_note",
                        trigger_input_tokens=last_input_tokens,
                        from_segment=segment,
                        to_segment=segment + 1,
                        removed_spans=_spans_payload(context_spans),
                        removed_messages=len(messages),
                        note=note,
                    )
                    segment += 1
                    n_compactions += 1
                    context_spans = []
                    messages = [{"role": "user", "content": task_prompt + "\n\n" + note}]

                step += 1
                tw.emit(
                    EventKind.CONTEXT_SNAPSHOT,
                    step,
                    reason="before_model_request",
                    segment=segment,
                    spans=_spans_payload(context_spans),
                    n_messages=len(messages),
                )
                tw.emit(
                    EventKind.MODEL_REQUEST,
                    step,
                    segment=segment,
                    n_messages=len(messages),
                    # Only the newest message is logged; earlier ones are already in the trace.
                    new_message=messages[-1],
                )
                resp = model.complete(SYSTEM_PROMPT, messages, TOOL_SPECS)
                total = total + resp.usage
                last_input_tokens = (
                    resp.usage.input_tokens
                    + resp.usage.cache_read_input_tokens
                    + resp.usage.cache_creation_input_tokens
                )
                tw.emit(
                    EventKind.MODEL_RESPONSE,
                    step,
                    model=resp.model,
                    stop_reason=resp.stop_reason,
                    stop_details=resp.stop_details,
                    usage=resp.usage.model_dump(),
                    latency_s=round(resp.latency_s, 3),
                    request_id=resp.request_id,
                    content=resp.content,
                )
                messages.append({"role": "assistant", "content": resp.content})

                if resp.stop_reason == "refusal":
                    exit_status = "refusal"
                    break

                calls = resp.tool_calls
                if not calls:
                    nudges += 1
                    if nudges > budget.max_no_tool_nudges:
                        exit_status = "no_tool_calls"
                        break
                    messages.append(
                        {"role": "user", "content": "Continue using the tools, or call `submit`."}
                    )
                    continue

                results: list[dict[str, Any]] = []
                submitted = False
                for call in calls:
                    if call.name in ("replace_in_file", "create_file", "submit"):
                        tw.emit(
                            EventKind.CONTEXT_SNAPSHOT,
                            step,
                            reason=f"before_{call.name}",
                            segment=segment,
                            spans=_spans_payload(context_spans),
                        )
                    tw.emit(
                        EventKind.TOOL_CALL,
                        step,
                        tool_use_id=call.id,
                        name=call.name,
                        input=call.input,
                    )
                    tc0 = time.monotonic()
                    out = tools.call(call.name, call.input)
                    tw.emit(
                        EventKind.TOOL_RESULT,
                        step,
                        tool_use_id=call.id,
                        name=call.name,
                        is_error=out.is_error,
                        output=out.text,
                        output_sha256=_sha256(out.text),
                        duration_s=round(time.monotonic() - tc0, 4),
                        files_modified=out.files_modified,
                    )
                    for span in out.spans_viewed:
                        tw.emit(
                            EventKind.CODE_LOCATION_VIEWED,
                            step,
                            tool_use_id=call.id,
                            source_tool=call.name,
                            segment=segment,
                            span=span.model_dump(),
                        )
                    context_spans.extend(out.spans_viewed)
                    files_modified.extend(out.files_modified)
                    block: dict[str, Any] = {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": out.text,
                    }
                    if out.is_error:
                        block["is_error"] = True
                    results.append(block)
                    submitted = submitted or out.submitted
                messages.append({"role": "user", "content": results})
                if submitted:
                    exit_status = "submitted"
                    break
        except Exception as exc:  # recorded, not swallowed silently
            exit_status = "error"
            tw.emit(EventKind.ERROR, step, error_type=type(exc).__name__, message=str(exc))

        patch = workspace.diff()
        patch_path.write_text(patch, encoding="utf-8")
        tw.emit(
            EventKind.PATCH_GENERATED,
            step,
            patch_sha256=_sha256(patch),
            patch_bytes=len(patch.encode("utf-8")),
            files_modified=sorted(set(files_modified)),
            patch=patch,
        )
        wall = time.monotonic() - t0
        result = RunResult(
            run_id=run_id,
            instance_id=task.instance_id,
            agent_config=agent_config,
            exit_status=exit_status,
            steps=step,
            usage=total.model_dump(),
            wall_s=round(wall, 3),
            patch_path=str(patch_path),
            trace_path=str(trace_path),
            patch_bytes=len(patch.encode("utf-8")),
            files_modified=sorted(set(files_modified)),
            n_compactions=n_compactions,
        )
        tw.emit(EventKind.RUN_END, step, result=asdict(result), finished_at=utc_now())

    (run_dir / "result.json").write_text(json.dumps(asdict(result), indent=2) + "\n")
    return result
