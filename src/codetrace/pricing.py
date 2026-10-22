"""Versioned API price table and cost accounting.

Prices are USD per million tokens, copied from the official pricing page and dated. A run
records the table it used, so later price changes cannot silently alter reported costs.

Accounting (``usage_cost``) prices each usage component separately. Cache writes are
priced by their reported TTL; any cache-write tokens without a TTL breakdown are priced
at the 1-hour rate (the higher one), so the estimate never undercounts.

Projection (``projected_call_cost``) is a conservative reservation used *before* a request
to enforce a spending cap: every input token is assumed to be billed at the highest input
rate, one token per UTF-8 byte of serialized content plus 4096 tokens of framing
allowance, and the full ``max_tokens`` of output. This is an estimate, not a provider
tokenizer guarantee or an invoice ceiling. Unacknowledged requests can incur charges.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from codetrace.trace import TokenUsage

FRAMING_TOKEN_ALLOWANCE = 4096


@dataclass(frozen=True)
class PriceTable:
    model: str
    input: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float
    output: float
    source: str
    retrieved: str

    @property
    def max_input_rate(self) -> float:
        return max(self.input, self.cache_write_5m, self.cache_write_1h)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


PRICES: dict[str, PriceTable] = {
    "claude-opus-5-5": PriceTable(
        model="claude-opus-5-5",
        input=4.00,
        cache_write_5m=5.00,
        cache_write_1h=8.00,
        cache_read=0.20,
        output=20.00,
        source="https://platform.claude.com/docs/en/about-claude/pricing",
        retrieved="2026-10-20",
    ),
}


def price_table(model: str) -> PriceTable | None:
    return PRICES.get(model)


def usage_cost(usage: TokenUsage, prices: PriceTable) -> float:
    """USD cost of one or more requests' usage."""
    labelled = usage.cache_write_5m_tokens + usage.cache_write_1h_tokens
    unlabelled = max(0, usage.cache_creation_input_tokens - labelled)
    per_m = (
        usage.input_tokens * prices.input
        + usage.cache_write_5m_tokens * prices.cache_write_5m
        + (usage.cache_write_1h_tokens + unlabelled) * prices.cache_write_1h
        + usage.cache_read_input_tokens * prices.cache_read
        + usage.output_tokens * prices.output
    )
    return per_m / 1_000_000


def estimate_request_tokens(
    system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> int:
    """High local estimate; includes byte-level tokenization and protocol allowance."""
    content = system + json.dumps(messages, default=str) + json.dumps(tools)
    return len(content.encode("utf-8")) + FRAMING_TOKEN_ALLOWANCE


def projected_call_cost(
    input_tokens_upper: int, max_output_tokens: int, prices: PriceTable
) -> float:
    """Worst-rate reservation for the supplied token estimate (see module docstring)."""
    return (
        input_tokens_upper * prices.max_input_rate + max_output_tokens * prices.output
    ) / 1_000_000
