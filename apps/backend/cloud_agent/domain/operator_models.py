"""Models a deployment provides: prices, the relay's route list and the first model.

Pure rules, no I/O. A *provided* (operator) connection has the fields an
owner's connection has, so the same predicate decides which agents run which
model (``domain.model_connections.require_usable``). What is its own lives
here:

**Its models are its key's.** The connection lists what its provider lists
for its key, so the provider decides which models the deployment offers. The
first of them an agent can use is where a new owner starts.

**Prices, for the usage table.** A request's tokens are recorded with what
they cost at its model's prices: input, output and, where the provider prices
them apart, tokens read from and written to its prompt cache, each in
millionths of a US dollar per million tokens. They are the prices the
provider's model listing gives; a model it gives none for is recorded by its
tokens alone. Nothing is limited by them: the provider's own budget on the
key is what caps spending.

**The relay's routes.** The model relay forwards exactly the inference and
listing paths an agent CLI calls under the connection's Base URL, matched
whole, and nothing else under that prefix.

See ``docs/agents/specs/2026-11-12-platform-model-relay.zh-CN.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from cloud_agent.domain.model_usage import CHAT, MESSAGES, RESPONSES, TokenUsage
from cloud_agent_shared.model_connections import anthropic_base_url

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The domain the stored key of a provided connection is encrypted under, and
#: its members' translator tokens are derived under, from the settings root key.
OPERATOR_CONNECTION_KEY_DOMAIN = b"cloud-agent/operator-model-connection-key/v1"

#: Money is kept in whole millionths of a US dollar, and a price in those per
#: million tokens: 2_800_000 is $2.80 per million tokens. A request's cost is
#: rounded half up to the nearest millionth, as the gateway's cost counters
#: round each request.
MICROUSD_PER_USD = 1_000_000
TOKENS_PER_PRICE = 1_000_000
_MICRO = Decimal("0.000001")
#: The dearest price a listing is believed: a million dollars per million tokens.
_MAX_LISTED_USD = Decimal(1_000_000)


@dataclass(frozen=True)
class ModelPrices:
    """What one provided model costs, in micro-dollars per million tokens."""

    input: int
    output: int
    #: ``None`` charges tokens read from the provider's cache at ``input``.
    cache_read: int | None = None
    #: ``None`` charges tokens written to the provider's cache at ``input``.
    cache_write: int | None = None


def model_prices(terms: Mapping[str, Any] | None) -> ModelPrices | None:
    """The prices in a model's stored terms; ``None`` while it has none."""
    if terms is None or terms.get("input_price") is None or terms.get("output_price") is None:
        return None
    return ModelPrices(
        input=int(terms["input_price"]),
        output=int(terms["output_price"]),
        cache_read=_price(terms.get("cache_read_price")),
        cache_write=_price(terms.get("cache_write_price")),
    )


def _price(value: Any) -> int | None:
    return int(value) if value else None


def request_cost(prices: ModelPrices, usage: TokenUsage) -> int:
    """What a request cost, in micro-dollars: what the gateway records for it.

    ``usage.prompt`` counts every input token. Tokens read from or written to
    the provider's cache are taken out of it and charged at their own price
    only where the model has one; otherwise they stay in it, at the input
    price.

    Computed as the gateway computes it, step for step: in floating-point
    dollars (``serving.storage.utils.calculate_cost``), then rounded half up
    to a millionth (``serving.spend_ledger.counted_amount``). A price kept in
    micro-dollars divides to the same float the gateway parses from its
    catalog's text, so the two agree to the micro-dollar, even where the
    exact cost is half of one and the float lands a hair below it.
    ``tests/unit/test_cost_matches_gateway.py`` holds them to that.
    """
    input_price = prices.input / MICROUSD_PER_USD
    output_price = prices.output / MICROUSD_PER_USD
    read_price = (prices.cache_read or 0) / MICROUSD_PER_USD
    write_price = (prices.cache_write or 0) / MICROUSD_PER_USD
    plain = float(usage.prompt)
    if read_price > 0:
        plain -= usage.cache_read
    if write_price > 0:
        plain -= usage.cache_write
    plain = max(plain, 0.0)
    cost = (
        (plain * input_price / TOKENS_PER_PRICE)
        + (usage.output * output_price / TOKENS_PER_PRICE)
        + (usage.cache_read * read_price / TOKENS_PER_PRICE)
        + (usage.cache_write * write_price / TOKENS_PER_PRICE)
    )
    return int(Decimal(cost).quantize(_MICRO, rounding=ROUND_HALF_UP) * MICROUSD_PER_USD)


def listed_prices(pricing: Any) -> ModelPrices | None:
    """A model's prices as its provider's listing gives them; ``None`` when it gives none.

    Read the way an inference gateway in the hybridInference family lists
    them: ``pricing`` with ``prompt``, ``completion`` and, optionally,
    ``input_cache_reads`` and ``input_cache_writes``, each in US dollars per
    million tokens, as text or a number. A listing without both of the first
    two, or with a value that is not a non-negative number, gives none; a
    cache price of zero is no cache price, as the gateway reads it.
    """
    if not isinstance(pricing, dict):
        return None
    prompt = _listed_usd(pricing.get("prompt"))
    completion = _listed_usd(pricing.get("completion"))
    if prompt is None or completion is None:
        return None
    return ModelPrices(
        input=prompt,
        output=completion,
        cache_read=_listed_usd(pricing.get("input_cache_reads")) or None,
        cache_write=_listed_usd(pricing.get("input_cache_writes")) or None,
    )


def _listed_usd(value: Any) -> int | None:
    """Dollars per million tokens as micro-dollars per million tokens; ``None`` if unreadable."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        dollars = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not dollars.is_finite() or dollars < 0 or dollars > _MAX_LISTED_USD:
        return None
    return int((dollars * MICROUSD_PER_USD).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def first_usable_model(models: list[str], runtimes: Mapping[str, list[str]]) -> str | None:
    """Where a new owner starts on a connection: its first model an agent can use."""
    return next((model for model in models if runtimes.get(model)), None)


@dataclass(frozen=True)
class RelayRoute:
    """One path the model relay forwards."""

    #: Whether the request is an inference call: its body names the model, and
    #: what it used is recorded for its owner.
    inference: bool
    #: Whether a refusal is shaped as Anthropic's error rather than OpenAI's.
    anthropic: bool
    #: The inference API an answer on this path is read as
    #: (``domain.model_usage``); empty where nothing is recorded.
    api: str = ""


def relay_route(base_url: str, method: str, raw_path: str) -> RelayRoute | None:
    """The route for ``method`` on ``raw_path`` under a connection, or ``None``.

    ``raw_path`` is the path exactly as the client sent it, undecoded and
    without its query. It must equal one of the listed paths byte for byte, so
    an escaped or dot-segment spelling of another path matches nothing. A
    prefix match would also open whatever else the provider serves under the
    same prefix, such as stored responses.
    """
    openai = urlsplit(base_url).path.rstrip("/")
    anthropic = urlsplit(anthropic_base_url(base_url)).path.rstrip("/")
    routes: dict[tuple[str, str], RelayRoute] = {
        ("POST", f"{openai}/chat/completions"): RelayRoute(True, anthropic=False, api=CHAT),
        ("POST", f"{openai}/responses"): RelayRoute(True, anthropic=False, api=RESPONSES),
        ("GET", f"{openai}/models"): RelayRoute(inference=False, anthropic=False),
        ("POST", f"{anthropic}/v1/messages"): RelayRoute(True, anthropic=True, api=MESSAGES),
        ("POST", f"{anthropic}/v1/messages/count_tokens"): RelayRoute(
            inference=False, anthropic=True
        ),
    }
    # Claude Code and an OpenAI client both list ``…/v1/models``; where the two
    # spellings are one path, the OpenAI shape already listed above stands.
    routes.setdefault(("GET", f"{anthropic}/v1/models"), RelayRoute(False, anthropic=True))
    return routes.get((method.upper(), raw_path))


def is_messages_path(raw_path: str) -> bool:
    """Whether a path the relay refuses outright looks like an Anthropic call."""
    return "/messages" in raw_path


__all__ = [
    "MICROUSD_PER_USD",
    "OPERATOR_CONNECTION_KEY_DOMAIN",
    "TOKENS_PER_PRICE",
    "ModelPrices",
    "RelayRoute",
    "first_usable_model",
    "is_messages_path",
    "listed_prices",
    "model_prices",
    "relay_route",
    "request_cost",
]
