from __future__ import annotations

import pytest

from codetrace.pricing import (
    estimate_request_tokens,
    price_table,
    projected_call_cost,
    usage_cost,
)
from codetrace.trace import TokenUsage


def test_opus_55_table_matches_official_page_2026_10_20() -> None:
    p = price_table("claude-opus-5-5")
    assert p is not None
    assert (p.input, p.cache_write_5m, p.cache_write_1h, p.cache_read, p.output) == (
        4.0,
        5.0,
        8.0,
        0.2,
        20.0,
    )
    assert price_table("unknown-model") is None


def test_usage_cost_prices_each_component() -> None:
    p = price_table("claude-opus-5-5")
    assert p is not None
    u = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_input_tokens=1_000_000,
                   cache_creation_input_tokens=2_000_000, cache_write_5m_tokens=1_000_000)  # fmt: skip
    # 4 + 20 + 0.2 + 5 (5m writes) + 8 (1M unlabelled writes priced at the 1h rate)
    assert usage_cost(u, p) == pytest.approx(37.2)


def test_projection_reserves_highest_rate_for_supplied_estimate() -> None:
    p = price_table("claude-opus-5-5")
    assert p is not None
    msgs = [{"role": "user", "content": "x" * 4000}]
    est = estimate_request_tokens("s" * 1000, msgs, [])
    assert est >= 1000 + 4000 + 4096
    worst = projected_call_cost(est, 16000, p)
    actual = usage_cost(TokenUsage(input_tokens=est, output_tokens=16000), p)
    assert worst >= actual


def test_estimate_handles_multibyte_and_low_compression_input() -> None:
    messages = [{"role": "user", "content": "🧪漢字" * 100}]
    assert estimate_request_tokens("", messages, []) > 4096 + 1000
