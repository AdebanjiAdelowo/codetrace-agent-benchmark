"""Deterministic, offline model stand-ins for tests and pipeline smoke runs.

These are NOT agents and produce no research results. They exist so that the
harness, tracing, workspace, and prediction pipeline can be exercised end to end
without network access or API spend. Token counts are approximations
(``method="approx_chars_div_4"``) and must never be reported as model cost.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from codetrace.models.base import ModelInfo, ModelResponse
from codetrace.trace import TokenUsage

Step = Sequence[tuple[str, dict[str, Any]]]  # [(tool_name, tool_input), ...]


def _approx_tokens(obj: Any) -> int:
    return max(1, len(json.dumps(obj, default=str)) // 4)


class ScriptedModel:
    """Replays a fixed list of tool-call steps, then calls ``submit``."""

    def __init__(self, steps: Sequence[Step], name: str = "scripted-replay") -> None:
        self.steps = list(steps)
        self.info = ModelInfo(provider="scripted", name=name, params={"n_steps": len(self.steps)})
        self._i = 0

    def _next_step(self, messages: list[dict[str, Any]]) -> Step:
        step = self.steps[self._i] if self._i < len(self.steps) else [("submit", {})]
        self._i += 1
        return step

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        step = self._next_step(messages)
        call_idx = self._i
        content: list[dict[str, Any]] = [
            {"type": "tool_use", "id": f"toolu_{call_idx:03d}_{j}", "name": n, "input": dict(a)}
            for j, (n, a) in enumerate(step)
        ]
        usage = TokenUsage(
            input_tokens=_approx_tokens([system, messages, tools]),
            output_tokens=_approx_tokens(content),
            method="approx_chars_div_4",
        )
        return ModelResponse(
            content=content,
            stop_reason="tool_use",
            usage=usage,
            model=self.info.name,
            latency_s=0.0,
        )


_IDENT = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]{2,})`|\b([A-Z][a-z]+[A-Z][A-Za-z0-9]+)\b")


def problem_identifiers(problem_statement: str, limit: int = 3) -> list[str]:
    """Deterministically extract code-like identifiers (backticked names, CamelCase)."""
    seen: list[str] = []
    for m in _IDENT.finditer(problem_statement):
        tok = (m.group(1) or m.group(2)).split(".")[-1].rstrip("()")
        if len(tok) >= 3 and tok not in seen:
            seen.append(tok)
        if len(seen) >= limit:
            break
    return seen


class HeuristicExplorer(ScriptedModel):
    """Pipeline smoke-test policy: search for identifiers from the problem statement,
    read around the first hit, then submit with no edits (an honest empty patch).

    It reacts to tool results, so it exercises the real request/response loop.
    """

    def __init__(self, problem_statement: str, max_reads: int = 2) -> None:
        super().__init__(steps=[], name="heuristic-explorer-v1")
        self.idents = problem_identifiers(problem_statement)
        self.max_reads = max_reads
        self.info = ModelInfo(
            provider="scripted",
            name=self.info.name,
            params={"identifiers": self.idents, "max_reads": max_reads},
        )
        self._phase = 0

    def _next_step(self, messages: list[dict[str, Any]]) -> Step:
        self._phase += 1
        if self._phase == 1:
            calls: list[tuple[str, dict[str, Any]]] = [("list_dir", {"path": "."})]
            calls += [("search_code", {"pattern": i}) for i in self.idents]
            return calls
        if self._phase == 2:
            last = messages[-1]["content"] if messages else []
            hits: list[tuple[str, int]] = []
            for block in last:
                if block.get("type") != "tool_result" or block.get("is_error"):
                    continue
                for text_line in str(block.get("content", "")).splitlines():
                    m = re.match(r"^([^:\s]+\.py):(\d+): ", text_line)
                    if m and (m.group(1), int(m.group(2))) not in hits:
                        hits.append((m.group(1), int(m.group(2))))
            reads = []
            seen_files: set[str] = set()
            for path, lineno in hits:
                if path in seen_files:
                    continue
                seen_files.add(path)
                start = max(1, lineno - 20)
                reads.append(
                    ("read_file", {"path": path, "start_line": start, "end_line": lineno + 20})
                )
                if len(reads) >= self.max_reads:
                    break
            if reads:
                return reads
        return [("submit", {})]


ResponseFn = Callable[[str, list[dict[str, Any]], list[dict[str, Any]]], ModelResponse]
