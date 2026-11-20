"""The relay's cost is what the inference gateway records for the same usage and prices.

``_gateway_cost`` below is the gateway's own code, copied: hybridInference
``apps/backend/serving/storage/utils.py`` (``billable_output_tokens``,
``calculate_cost``) and ``apps/backend/serving/spend_ledger.py``
(``counted_amount``), at ``dev`` ``b98678a42``. Cloud Agent may not import it
(AGENTS.md, no Gateway dependency), so the copy is the oracle the relay's
integer arithmetic is held to, over usage and prices drawn at random. A change
on either side that makes them disagree fails here.

Both compute in floating point and round the result half up to a millionth
of a dollar, step for step, so they agree to the micro-dollar for every
request, even where the exact cost is half of one and the float lands a hair
below it.
"""

from __future__ import annotations

import math
import random
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest

from cloud_agent.domain.model_usage import TokenUsage, billable_output
from cloud_agent.domain.operator_models import ModelPrices, request_cost

# -- the gateway's code, as it is --------------------------------------------


def billable_output_tokens(
    *,
    prompt_tokens: float,
    completion_tokens: float,
    reasoning_tokens: float = 0.0,
    total_tokens: float | None = None,
) -> float:
    if reasoning_tokens <= 0:
        return completion_tokens

    if total_tokens is not None:
        prompt_plus_completion = prompt_tokens + completion_tokens
        if math.isclose(total_tokens, prompt_plus_completion, rel_tol=0.0, abs_tol=1e-9):
            return completion_tokens

        prompt_plus_completion_plus_reasoning = prompt_plus_completion + reasoning_tokens
        if math.isclose(
            total_tokens,
            prompt_plus_completion_plus_reasoning,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            return completion_tokens + reasoning_tokens

    return completion_tokens + reasoning_tokens


def calculate_cost(usage: dict[str, Any] | None, pricing: dict[str, str] | None) -> float | None:
    if not usage or not pricing:
        return None

    try:
        prompt_raw = usage.get("prompt_tokens")
        prompt_tokens = float(prompt_raw) if prompt_raw is not None else 0.0
        completion_raw = usage.get("completion_tokens")
        completion_tokens = float(completion_raw) if completion_raw is not None else 0.0
        reasoning_raw = usage.get("reasoning_tokens")
        reasoning_tokens = float(reasoning_raw) if reasoning_raw is not None else 0.0
        total_raw = usage.get("total_tokens")
        total_tokens = float(total_raw) if total_raw is not None else None
        cache_read_raw = usage.get("cache_read_tokens")
        cache_write_raw = usage.get("cache_write_tokens")
        cache_read_tokens = float(cache_read_raw) if cache_read_raw is not None else 0.0
        cache_write_tokens = float(cache_write_raw) if cache_write_raw is not None else 0.0

        prompt_price = float(pricing.get("prompt", "0"))
        completion_price = float(pricing.get("completion", "0"))
        cache_read_price = float(pricing.get("input_cache_reads", "0"))
        cache_write_price = float(pricing.get("input_cache_writes", "0"))

        billable_prompt_tokens = prompt_tokens
        if cache_read_price > 0:
            billable_prompt_tokens -= cache_read_tokens
        if cache_write_price > 0:
            billable_prompt_tokens -= cache_write_tokens
        billable_prompt_tokens = max(billable_prompt_tokens, 0.0)

        output_tokens = billable_output_tokens(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total_tokens,
        )

        return (
            (billable_prompt_tokens * prompt_price / 1_000_000)
            + (output_tokens * completion_price / 1_000_000)
            + (cache_read_tokens * cache_read_price / 1_000_000)
            + (cache_write_tokens * cache_write_price / 1_000_000)
        )
    except (ValueError, TypeError):
        return None


_MICRO = Decimal("0.000001")


def counted_amount(cost_usd: float) -> Decimal:
    return Decimal(cost_usd).quantize(_MICRO, rounding=ROUND_HALF_UP)


# -- the comparison ------------------------------------------------------------


def _gateway_cost(usage: dict[str, Any], pricing: dict[str, str]) -> int:
    """What the gateway adds to its counters for one request, in micro-dollars."""
    cost = calculate_cost(usage, pricing)
    assert cost is not None
    return int(counted_amount(cost) / _MICRO)


def _microusd(price: str) -> int:
    whole, _, fraction = price.partition(".")
    return int(whole) * 1_000_000 + int((fraction + "000000")[:6])


def _relay_cost(usage: dict[str, Any], pricing: dict[str, str]) -> tuple[int, int]:
    """The relay's cost in micro-dollars, and the exact cost's remainder in millionths of one.

    The remainder tells where the exact cost is half a micro-dollar, which is
    where floating point is most likely to tip the rounding.
    """
    prices = ModelPrices(
        input=_microusd(pricing["prompt"]),
        output=_microusd(pricing["completion"]),
        cache_read=_microusd(pricing.get("input_cache_reads", "0")) or None,
        cache_write=_microusd(pricing.get("input_cache_writes", "0")) or None,
    )
    prompt = usage["prompt_tokens"]
    used = TokenUsage(
        prompt=prompt,
        cache_read=usage.get("cache_read_tokens", 0),
        cache_write=usage.get("cache_write_tokens", 0),
        output=billable_output(
            prompt=prompt,
            completion=usage["completion_tokens"],
            reasoning=usage.get("reasoning_tokens", 0),
            total=usage.get("total_tokens"),
        ),
    )
    plain = prompt
    if prices.cache_read:
        plain -= used.cache_read
    if prices.cache_write:
        plain -= used.cache_write
    exact = (
        max(plain, 0) * prices.input
        + used.output * prices.output
        + used.cache_read * (prices.cache_read or 0)
        + used.cache_write * (prices.cache_write or 0)
    )
    return request_cost(prices, used), exact % 1_000_000


def _price(rng: random.Random) -> str:
    """A catalog price, as the gateway's catalogs write them: 0, or up to six decimals."""
    if rng.random() < 0.1:
        return "0"
    places = rng.choice([0, 1, 2, 3, 4, 6])
    whole = rng.choice([0, 0, 0, 1, 2, 3, 5, 15, 75])
    fraction = rng.randrange(10**places) if places else 0
    return f"{whole}.{fraction:0{places}d}" if places else str(whole)


def _usage(rng: random.Random) -> dict[str, Any]:
    prompt = rng.choice([0, rng.randrange(50), rng.randrange(20_000), rng.randrange(1_000_001)])
    completion = rng.choice([0, rng.randrange(100), rng.randrange(40_000)])
    usage: dict[str, Any] = {"prompt_tokens": prompt, "completion_tokens": completion}
    if rng.random() < 0.6:
        # Mostly part of the prompt, sometimes (malformed) more than all of it.
        usage["cache_read_tokens"] = rng.randrange(
            max(prompt, 1) * (2 if rng.random() < 0.1 else 1)
        )
    if rng.random() < 0.3:
        usage["cache_write_tokens"] = rng.randrange(max(prompt // 4, 1))
    if rng.random() < 0.5:
        reasoning = rng.randrange(1, 30_000)
        usage["reasoning_tokens"] = reasoning
        usage["total_tokens"] = rng.choice(
            [None, prompt + completion, prompt + completion + reasoning, rng.randrange(10**6)]
        )
        if usage["total_tokens"] is None:
            del usage["total_tokens"]
    return usage


@pytest.mark.parametrize("seed", range(4))
def test_the_relay_counts_what_the_gateway_counts_for_any_usage_and_prices(seed) -> None:
    rng = random.Random(20261114 + seed)
    halves = 0
    for _ in range(5_000):
        usage = _usage(rng)
        pricing = {"prompt": _price(rng), "completion": _price(rng)}
        if rng.random() < 0.6:
            pricing["input_cache_reads"] = _price(rng)
        if rng.random() < 0.3:
            pricing["input_cache_writes"] = _price(rng)
        relay, remainder = _relay_cost(usage, pricing)
        halves += remainder == 500_000
        assert relay == _gateway_cost(usage, pricing), (usage, pricing, relay)
    # The draws do reach the half-way costs, where a rewrite in exact
    # arithmetic would disagree with the gateway's floats.
    assert halves > 0


def test_exactly_half_a_micro_dollar_rounds_as_the_gateways_float_does() -> None:
    # One token at $0.50 per million tokens: exactly half a micro-dollar. The
    # float for it lies a hair below the half, and both round it down.
    usage = {"prompt_tokens": 1, "completion_tokens": 0}
    pricing = {"prompt": "0.5", "completion": "0"}
    assert _relay_cost(usage, pricing) == (0, 500_000)
    assert _gateway_cost(usage, pricing) == 0
