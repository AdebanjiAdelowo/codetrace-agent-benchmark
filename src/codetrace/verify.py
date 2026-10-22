"""Independent consistency checks and read-only recovery of an aborted trace.

Recovery reports only acknowledged provider usage. An unanswered request may have
incurred charges; never treat a recovered cost as a final invoice or resume a trace.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from codetrace.pricing import PriceTable, usage_cost
from codetrace.trace import EventKind, TokenUsage, TraceEvent, read_trace, validate_trace


def _totals(events: list[TraceEvent]) -> tuple[TokenUsage, float | None]:
    manifest = events[0].payload["manifest"]
    prices = PriceTable(**manifest["pricing"]) if manifest.get("pricing") else None
    total = TokenUsage(
        method="approx_chars_div_4" if manifest["model_provider"] == "scripted" else "provider"
    )
    cost = 0.0 if prices else None
    for event in events:
        if event.kind is EventKind.MODEL_RESPONSE:
            usage = TokenUsage.model_validate(event.payload["usage"])
            total += usage
            if prices:
                cost = (cost or 0) + usage_cost(usage, prices)
    return total, cost


def verify_run(run_dir: Path) -> dict[str, Any]:
    """Verify a finalized run without modifying it; older traces need no pricing fields."""
    events = read_trace(run_dir / "trace.jsonl")
    problems = validate_trace(events)
    if problems:
        return {"verified": False, "problems": problems}
    total, cost = _totals(events)
    result = events[-1].payload["result"]
    if TokenUsage.model_validate(result["usage"]) != total:
        problems.append("run_end usage differs from response sum")
    requests = [e for e in events if e.kind is EventKind.MODEL_REQUEST]
    if result["steps"] != (requests[-1].step if requests else 0):
        problems.append("run_end steps differ from model requests")
    if result["n_compactions"] != sum(e.kind is EventKind.CONTEXT_COMPACTION for e in events):
        problems.append("run_end compaction count differs from trace")
    disk_result = json.loads((run_dir / "result.json").read_text())
    if disk_result != result:
        problems.append("result.json differs from run_end")
    if cost is not None and not math.isclose(result.get("cost_usd", -1), cost, abs_tol=1e-12):
        problems.append("run_end cost differs from independently recomputed usage cost")
    patch_events = [e for e in events if e.kind is EventKind.PATCH_GENERATED]
    if len(patch_events) != 1:
        problems.append("expected one patch_generated event")
    else:
        patch = (run_dir / "patch.diff").read_bytes()
        payload = patch_events[0].payload
        if hashlib.sha256(patch).hexdigest() != payload["patch_sha256"]:
            problems.append("patch file hash differs from trace")
        if patch != payload["patch"].encode() or len(patch) != result["patch_bytes"]:
            problems.append("patch content/size differs from trace or run_end")
    spans: list[dict[str, Any]] = []
    segment = 0
    cumulative = 0.0
    prices = events[0].payload["manifest"].get("pricing")
    for event in events:
        payload = event.payload
        if event.kind is EventKind.CODE_LOCATION_VIEWED:
            if payload["segment"] != segment:
                problems.append(f"event {event.seq}: viewed span in wrong segment")
            spans.append(payload["span"])
        elif event.kind is EventKind.CONTEXT_SNAPSHOT:
            if payload["spans"] != spans or payload["segment"] != segment:
                problems.append(f"event {event.seq}: context snapshot differs from viewed spans")
        elif event.kind is EventKind.CONTEXT_COMPACTION:
            if (
                payload["removed_spans"] != spans
                or payload["from_segment"] != segment
                or payload["to_segment"] != segment + 1
            ):
                problems.append(f"event {event.seq}: compaction differs from previous context")
            spans = []
            segment += 1
        elif event.kind is EventKind.MODEL_RESPONSE and prices:
            expected = usage_cost(TokenUsage.model_validate(payload["usage"]), PriceTable(**prices))
            cumulative += expected
            for key, value in (("cost_usd", expected), ("cumulative_cost_usd", cumulative)):
                if not math.isclose(payload.get(key, -1), value, abs_tol=1e-12):
                    problems.append(f"event {event.seq}: {key} differs from recomputed cost")
    return {
        "verified": not problems,
        "run_id": result["run_id"],
        "problems": problems,
        "usage": total.model_dump(),
        "cost_usd": cost,
        "billing_complete": result.get("billing_complete", True),
    }


def recover_run(run_dir: Path) -> dict[str, Any]:
    """Save recovery.json from a valid trace prefix; never alter the original bytes.

    A torn final JSONL line is ignored and reported, but corruption earlier in the
    trace is rejected. Recovery is only for runs without run_end.
    """
    path = run_dir / "trace.jsonl"
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    events: list[TraceEvent] = []
    torn = False
    for idx, line in enumerate(lines):
        try:
            events.append(TraceEvent.model_validate_json(line))
        except Exception:
            if idx != len(lines) - 1 or line.endswith(b"\n"):
                raise ValueError("corrupt complete trace line") from None
            torn = True
    if not events or events[0].kind is not EventKind.RUN_START:
        raise ValueError("no valid run_start")
    if any(e.kind is EventKind.RUN_END for e in events):
        raise ValueError("run already finalized; use verify-run")
    if [e.seq for e in events] != list(range(len(events))) or len({e.run_id for e in events}) != 1:
        raise ValueError("invalid trace prefix sequence or run id")
    total, cost = _totals(events)
    requests = sum(e.kind is EventKind.MODEL_REQUEST for e in events)
    responses = sum(e.kind is EventKind.MODEL_RESPONSE for e in events)
    report = {
        "run_id": events[0].run_id,
        "status": "aborted",
        "resumable": False,
        "trace_sha256": hashlib.sha256(raw).hexdigest(),
        "torn_final_line": torn,
        "usage": total.model_dump(),
        "acknowledged_cost_usd": cost,
        "unanswered_requests": requests - responses,
        "billing_complete": False,
        "note": "Provider charges may exceed acknowledged usage; restart with a new run id.",
    }
    dest = run_dir / "recovery.json"
    with dest.open("x") as fh:
        fh.write(json.dumps(report, indent=2) + "\n")
    return report
