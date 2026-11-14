"""Models a deployment provides: budgets, prices, the relay's route list, the owner tag."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from cloud_agent.domain.model_connections import ModelConnectionInvalid
from cloud_agent.domain.model_usage import CHAT, MESSAGES, RESPONSES, TokenUsage, UsageReader
from cloud_agent.domain.operator_models import (
    Allowance,
    ModelPrices,
    clean_default_model,
    clean_end_user_header,
    effective_allowance,
    effective_budget,
    end_user_tag,
    model_prices,
    next_reset,
    relay_route,
    request_cost,
)

_PRICED = {"input_price": 2_800_000, "output_price": 8_800_000, "named_only": False}
_NAMED = {**_PRICED, "named_only": True}


@pytest.mark.parametrize(
    ("terms", "own_offered", "budget", "offered"),
    [
        # No prices, or no budget chosen: offered to nobody, whatever an account's rows say.
        (None, True, 5_000_000, False),
        ({"input_price": None, "output_price": None, "named_only": False}, True, 5_000_000, False),
        ({"input_price": 1, "output_price": None, "named_only": False}, None, 5_000_000, False),
        (_PRICED, True, None, False),
        # Open to every owner with a budget.
        (_PRICED, None, 5_000_000, True),
        (_PRICED, None, 0, False),
        # For named accounts only: an account's own answer opens it.
        (_NAMED, None, 5_000_000, False),
        (_NAMED, True, 5_000_000, True),
        # And an account's own answer withholds a model that is open.
        (_PRICED, False, 5_000_000, False),
        # A free model is still a priced one.
        ({"input_price": 0, "output_price": 0, "named_only": False}, None, 1, True),
    ],
)
def test_a_model_is_offered_to_an_owner_with_a_budget_it_is_open_to(
    terms, own_offered, budget, offered
) -> None:
    allowance = effective_allowance(terms, own_offered=own_offered, budget_microusd=budget)
    assert allowance.offered is offered


def test_an_accounts_own_budget_replaces_the_default() -> None:
    assert effective_budget(5_000_000, None) == 5_000_000
    assert effective_budget(5_000_000, 20_000_000) == 20_000_000
    assert effective_budget(5_000_000, 0) == 0
    assert effective_budget(0, 1_000_000) == 1_000_000
    # None chosen, and none of the account's own.
    assert effective_budget(None, None) is None
    assert effective_budget(None, 1_000_000) == 1_000_000


def test_a_budget_is_used_up_once_what_was_spent_reaches_it() -> None:
    assert not Allowance(offered=True, budget_microusd=3, spent_microusd=2).exhausted
    assert Allowance(offered=True, budget_microusd=3, spent_microusd=3).exhausted
    assert Allowance(offered=True, budget_microusd=3, spent_microusd=9).exhausted
    assert Allowance(offered=True, budget_microusd=3, spent_microusd=2).remaining_microusd == 1
    assert Allowance(offered=True, budget_microusd=3, spent_microusd=9).remaining_microusd == 0
    spent = effective_allowance(_PRICED, budget_microusd=5_000_000, spent_microusd=1_250_000)
    assert (spent.budget_microusd, spent.spent_microusd) == (5_000_000, 1_250_000)


def test_stored_terms_without_both_prices_have_none() -> None:
    assert model_prices(None) is None
    assert model_prices({"input_price": 1, "output_price": None}) is None
    assert model_prices({"input_price": None, "output_price": 1}) is None
    assert model_prices({"input_price": 0, "output_price": 0}) == ModelPrices(input=0, output=0)
    assert model_prices(
        {
            "input_price": 2_800_000,
            "output_price": 8_800_000,
            "cache_read_price": 800_000,
            "cache_write_price": None,
        }
    ) == ModelPrices(input=2_800_000, output=8_800_000, cache_read=800_000, cache_write=None)


#: $2.80 in, $8.80 out and $0.80 for cached input, per million tokens.
_PRICES = ModelPrices(input=2_800_000, output=8_800_000, cache_read=800_000)


@pytest.mark.parametrize(
    ("usage", "cost"),
    [
        (TokenUsage(), 0),
        # A million of each, at its price.
        (TokenUsage(prompt=1_000_000), 2_800_000),
        (TokenUsage(output=1_000_000), 8_800_000),
        # Cached input is taken out of the prompt and charged at its own price.
        (TokenUsage(prompt=1_000_000, cache_read=1_000_000), 800_000),
        (TokenUsage(prompt=100_000, cache_read=60_000, output=2_000), 177_600),
        # Tokens written to the cache have no price of their own here: they
        # stay in the prompt, at the input price.
        (TokenUsage(prompt=1_000, cache_write=1_000), 2_800),
        # A cost is rounded half up to the nearest millionth of a dollar.
        (TokenUsage(prompt=1), 3),
        (TokenUsage(output=1), 9),
        # A cache count larger than the prompt it is part of charges no negative input.
        (TokenUsage(prompt=10, cache_read=50), 40),
    ],
)
def test_a_request_costs_its_tokens_at_the_models_prices(usage, cost) -> None:
    assert request_cost(_PRICES, usage) == cost


def test_cache_tokens_with_a_price_of_their_own_are_charged_at_it() -> None:
    prices = ModelPrices(input=1_000_000, output=0, cache_read=100_000, cache_write=1_250_000)
    usage = TokenUsage(prompt=1_000_000, cache_read=400_000, cache_write=100_000)
    # 500k plain at $1, 400k read at $0.10, 100k written at $1.25.
    assert request_cost(prices, usage) == 500_000 + 40_000 + 125_000
    # Without them, every input token is plain.
    assert request_cost(ModelPrices(input=1_000_000, output=0), usage) == 1_000_000


def test_a_cost_is_rounded_half_up_as_the_gateway_rounds_each_request() -> None:
    # $1 per million tokens: a token is a millionth of a millionth of a dollar.
    prices = ModelPrices(input=1, output=0)
    assert request_cost(prices, TokenUsage(prompt=499_999)) == 0
    assert request_cost(prices, TokenUsage(prompt=500_000)) == 1
    assert request_cost(prices, TokenUsage(prompt=1_499_999)) == 1
    assert request_cost(prices, TokenUsage(prompt=1_500_000)) == 2


def _usd(price: str) -> int:
    """A price as the gateway's catalog writes it, in micro-dollars per million tokens."""
    whole, _, fraction = price.partition(".")
    return int(whole) * 1_000_000 + int((fraction + "000000")[:6])


def _catalog(pricing: dict[str, str]) -> ModelPrices:
    """A gateway catalog's ``pricing``, where a cache price of 0 means the input price."""
    return ModelPrices(
        input=_usd(pricing["prompt"]),
        output=_usd(pricing["completion"]),
        cache_read=_usd(pricing.get("input_cache_reads", "0")) or None,
        cache_write=_usd(pricing.get("input_cache_writes", "0")) or None,
    )


#: The gateway's own cost cases (hybridInference ``tests/unit/storage/test_cost_calculation.py``):
#: an answer's usage record, the model's catalog pricing, and what the gateway
#: counts, as it stores it, in micro-dollars.
_GATEWAY_CASES = [
    pytest.param(
        CHAT,
        {"prompt_tokens": 1000, "completion_tokens": 500},
        {"prompt": "0.15", "completion": "1.25"},
        775,
        id="basic",
    ),
    pytest.param(
        CHAT,
        {
            "prompt_tokens": 6000,
            "completion_tokens": 500,
            "reasoning_tokens": 200,
            "total_tokens": 6700,
            "cache_read_tokens": 5000,
        },
        {"prompt": "0.28", "completion": "0.42", "input_cache_reads": "0.028"},
        714,
        id="cache-and-reasoning-on-top",
    ),
    pytest.param(
        CHAT,
        {
            "prompt_tokens": 6000,
            "completion_tokens": 700,
            "reasoning_tokens": 200,
            "total_tokens": 6700,
            "cache_read_tokens": 5000,
        },
        {"prompt": "0.28", "completion": "0.42", "input_cache_reads": "0.028"},
        714,
        id="reasoning-included-is-not-billed-twice",
    ),
    pytest.param(
        CHAT,
        {"prompt_tokens": 1000, "completion_tokens": 100, "reasoning_tokens": 500},
        {"prompt": "0.5", "completion": "2.0"},
        1700,
        id="reasoning-without-a-total",
    ),
    pytest.param(
        CHAT,
        {
            "prompt_tokens": 10000,
            "completion_tokens": 0,
            "cache_read_tokens": 8000,
            "cache_write_tokens": 0,
        },
        {
            "prompt": "3.0",
            "completion": "15.0",
            "input_cache_reads": "0.30",
            "input_cache_writes": "3.75",
        },
        8400,
        id="cache-subtracted-from-prompt",
    ),
    pytest.param(
        CHAT,
        {"prompt_tokens": 100, "completion_tokens": 0, "cache_read_tokens": 500},
        {"prompt": "3.0", "completion": "15.0", "input_cache_reads": "0.30"},
        150,
        id="cache-larger-than-prompt-clamps",
    ),
    pytest.param(
        CHAT,
        {
            "prompt_tokens": 10000,
            "completion_tokens": 0,
            "cache_read_tokens": 8000,
            "cache_write_tokens": 0,
        },
        {"prompt": "3.0", "completion": "15.0"},
        30000,
        id="no-cache-price-falls-back-to-prompt",
    ),
    pytest.param(
        CHAT,
        {"prompt_tokens": 1000, "completion_tokens": 500},
        {"prompt": "0", "completion": "0"},
        0,
        id="free",
    ),
    pytest.param(
        MESSAGES,
        {
            "input_tokens": 21,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 50,
            "output_tokens": 30,
        },
        {
            "prompt": "3.0",
            "completion": "15.0",
            "input_cache_reads": "0.30",
            "input_cache_writes": "3.75",
        },
        731,
        id="anthropic-disjoint-input",
    ),
    pytest.param(
        CHAT,
        {
            "prompt_tokens": 1000,
            "prompt_cache_hit_tokens": 800,
            "prompt_cache_miss_tokens": 200,
            "completion_tokens": 50,
        },
        {"prompt": "0.27", "completion": "1.10", "input_cache_reads": "0.07"},
        165,
        id="deepseek-cache-hits",
    ),
    pytest.param(
        CHAT,
        {
            "prompt_tokens": 100_050,
            "completion_tokens": 200,
            "total_tokens": 100_250,
            "cache_read_tokens": 100_000,
            "cache_write_tokens": 0,
        },
        {
            "prompt": "3.0",
            "completion": "15.0",
            "input_cache_reads": "0.30",
            "input_cache_writes": "3.75",
        },
        33150,
        id="messages-log-shape",
    ),
    # The record the gateway itself answers with (``Usage.to_dict``): every
    # name it gives the cached tokens, and reasoning beside the counts.
    pytest.param(
        CHAT,
        {
            "prompt_tokens": 6000,
            "completion_tokens": 700,
            "total_tokens": 6700,
            "reasoning_tokens": 200,
            "cache_read_tokens": 5000,
            "cached_tokens": 5000,
            "prompt_tokens_details": {"cached_tokens": 5000},
        },
        {"prompt": "0.28", "completion": "0.42", "input_cache_reads": "0.028"},
        714,
        id="the-gateways-own-answer",
    ),
]


@pytest.mark.parametrize(("api", "usage", "pricing", "cost"), _GATEWAY_CASES)
def test_a_request_costs_what_the_gateway_would_count(api, usage, pricing, cost) -> None:
    reader = UsageReader(api, content_type="application/json")
    reader.feed(json.dumps({"usage": usage}).encode())
    used = reader.usage()
    assert used is not None and reader.complete
    assert request_cost(_catalog(pricing), used) == cost


def test_counts_start_over_at_the_next_utc_midnight() -> None:
    late = datetime(2026, 11, 12, 23, 59, 59, tzinfo=UTC)
    assert next_reset(late) == datetime(2026, 11, 13, tzinfo=UTC)
    assert next_reset(datetime(2026, 11, 13, tzinfo=UTC)) == datetime(2026, 11, 14, tzinfo=UTC)


@pytest.mark.parametrize(
    ("base_url", "method", "path", "inference", "anthropic"),
    [
        ("https://p.example/v1", "POST", "/v1/chat/completions", True, False),
        ("https://p.example/v1", "POST", "/v1/responses", True, False),
        ("https://p.example/v1", "GET", "/v1/models", False, False),
        # One trailing /v1 is dropped for Anthropic Messages, as the CLI's SDK appends its own.
        ("https://p.example/v1", "POST", "/v1/messages", True, True),
        ("https://p.example/v1", "POST", "/v1/messages/count_tokens", False, True),
        ("https://p.example/api/v1", "POST", "/api/v1/chat/completions", True, False),
        ("https://p.example/api/v1", "POST", "/api/v1/messages", True, True),
        ("https://p.example/anthropic", "POST", "/anthropic/v1/messages", True, True),
        ("https://p.example/anthropic", "GET", "/anthropic/v1/models", False, True),
        ("https://p.example", "POST", "/chat/completions", True, False),
        ("https://p.example", "POST", "/v1/messages", True, True),
    ],
)
def test_the_relay_forwards_the_paths_an_agent_cli_calls(
    base_url, method, path, inference, anthropic
) -> None:
    route = relay_route(base_url, method, path)
    assert route is not None
    assert (route.inference, route.anthropic) == (inference, anthropic)


def test_an_inference_route_names_the_api_its_answer_is_read_as() -> None:
    base = "https://p.example/v1"
    apis = {
        "/v1/chat/completions": CHAT,
        "/v1/responses": RESPONSES,
        "/v1/messages": MESSAGES,
    }
    for path, api in apis.items():
        route = relay_route(base, "POST", path)
        assert route is not None and route.api == api
    # What is not charged is not read.
    for method, path in (("GET", "/v1/models"), ("POST", "/v1/messages/count_tokens")):
        route = relay_route(base, method, path)
        assert route is not None and route.api == ""


@pytest.mark.parametrize(
    ("method", "path"),
    [
        # Whatever else the provider serves under the same prefix.
        ("GET", "/v1/responses/resp_123"),
        ("POST", "/v1/responses/resp_123/cancel"),
        ("DELETE", "/v1/responses"),
        ("POST", "/v1/embeddings"),
        ("POST", "/v1/files"),
        ("GET", "/v1/chat/completions"),
        ("POST", "/v1/models"),
        # Another spelling of a listed path is another path here.
        ("POST", "/v1/chat/completions/"),
        ("POST", "/v1/./chat/completions"),
        ("POST", "/v1/x/../chat/completions"),
        ("POST", "/v1/chat%2Fcompletions"),
        ("POST", "/v1/chat/completion%73"),
        ("POST", "//v1/chat/completions"),
        ("POST", "/V1/chat/completions"),
        # A path of another connection's shape.
        ("POST", "/chat/completions"),
        ("POST", "/api/v1/chat/completions"),
        ("POST", "/"),
        ("POST", ""),
    ],
)
def test_the_relay_forwards_nothing_else(method, path) -> None:
    assert relay_route("https://p.example/v1", method, path) is None


def test_an_owner_tag_is_stable_opaque_and_keyed() -> None:
    tag = end_user_tag("root-key", "01HZXACCOUNT")
    assert tag == end_user_tag("root-key", "01HZXACCOUNT")
    assert len(tag) == 24 and all(ch in "0123456789abcdef" for ch in tag)
    assert "01HZX" not in tag.upper()
    assert tag != end_user_tag("root-key", "01HZXOTHER")
    # Not computable without the deployment's key.
    assert tag != end_user_tag("another-key", "01HZXACCOUNT")
    with pytest.raises(ValueError, match="needs"):
        end_user_tag("", "01HZXACCOUNT")


@pytest.mark.parametrize("name", ["X-End-User", "x-user-id", "OpenAI-User"])
def test_an_owner_tag_header_is_a_plain_header_name(name) -> None:
    assert clean_end_user_header(f"  {name} ") == name


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_no_owner_tag_header_sends_none(empty) -> None:
    assert clean_end_user_header(empty) is None


@pytest.mark.parametrize(
    "name",
    ["Authorization", "x-api-key", "Host", "Content-Length", "X End", "X-End:User", "é", "1x"],
)
def test_an_owner_tag_header_cannot_be_one_the_relay_sets(name) -> None:
    with pytest.raises(ModelConnectionInvalid):
        clean_end_user_header(name)


def test_the_default_model_is_one_of_the_listed_models() -> None:
    assert clean_default_model(" m1 ", ["m1", "m2"]) == "m1"
    assert clean_default_model("", ["m1"]) is None
    assert clean_default_model(None, ["m1"]) is None
    with pytest.raises(ModelConnectionInvalid):
        clean_default_model("m3", ["m1", "m2"])
