"""Models a deployment provides: budgets, prices, the relay's route list, the owner tag."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cloud_agent.domain.model_connections import ModelConnectionInvalid
from cloud_agent.domain.model_usage import CHAT, MESSAGES, RESPONSES, TokenUsage
from cloud_agent.domain.operator_models import (
    ESTIMATED_FILE_TOKENS,
    Allowance,
    ModelPrices,
    clean_default_model,
    clean_end_user_header,
    effective_allowance,
    effective_budget,
    end_user_tag,
    estimated_prompt_tokens,
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
        # A cost is rounded up to the next millionth of a dollar.
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


def test_a_requests_text_is_taken_as_one_token_per_four_bytes() -> None:
    assert estimated_prompt_tokens(None) == 0
    assert estimated_prompt_tokens({}) == 0
    assert estimated_prompt_tokens("abcd") == 1
    assert estimated_prompt_tokens("abcde") == 2
    # Keys are text the model reads too, as a tool's schema is; numbers are not counted.
    assert estimated_prompt_tokens({"abcd": "efgh", "n": 123456789}) == 3
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "x" * 4000}]}],
    }
    assert 1000 < estimated_prompt_tokens(body) < 1020
    # Bytes, not characters: text outside ASCII takes more of them.
    assert estimated_prompt_tokens("\u6a21\u578b" * 2) == 3


def test_an_embedded_file_counts_as_a_fixed_number_of_tokens_whatever_its_size() -> None:
    megabytes = "A" * 3_000_000
    assert estimated_prompt_tokens(megabytes) == ESTIMATED_FILE_TOKENS
    # The shapes the three APIs embed a screenshot in.
    for image in (
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + megabytes}},
        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": megabytes},
        },
    ):
        assert ESTIMATED_FILE_TOKENS <= estimated_prompt_tokens(image) < ESTIMATED_FILE_TOKENS + 30
    two = {"content": [megabytes, megabytes, {"type": "text", "text": "abcd"}]}
    # 23 bytes of keys and text beside the two files.
    assert estimated_prompt_tokens(two) == 2 * ESTIMATED_FILE_TOKENS + 6
    # Long text is still text: it has whitespace in it, as source code and prose do.
    prose = "word " * 2000
    assert estimated_prompt_tokens(prose) == 2500


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
