"""Models a deployment provides: budgets, prices, the relay's route list and the owner tag.

Pure rules, no I/O. A *provided* (operator) connection has the fields an
owner's connection has, so the same predicate decides which agents run which
model (``domain.model_connections.require_usable``). What is its own lives
here:

**Budgets.** Each owner may spend an amount per UTC day on a connection's
models together, in millionths of a US dollar. The connection has a default;
an account may have an amount of its own, which replaces it. ``0`` withholds
the connection, and a connection with no default chosen offers nothing.

**Prices.** A request costs what it used at its model's prices: input, output
and, where the provider prices them apart, tokens read from and written to
its prompt cache, each in millionths of a US dollar per million tokens. A
model with no prices is offered to nobody.

**Who a model is for.** A model is open to every owner, or only to the
accounts named for it; an account's own answer replaces either.

**The relay's routes.** The model relay forwards exactly the inference and
listing paths an agent CLI calls under the connection's Base URL, matched
whole, and nothing else under that prefix.

See ``docs/agents/specs/2026-11-12-platform-model-relay.zh-CN.md``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from cloud_agent.domain.model_connections import ModelConnectionInvalid
from cloud_agent.domain.model_usage import CHAT, MESSAGES, RESPONSES, TokenUsage
from cloud_agent_shared.model_connections import anthropic_base_url

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The domain the stored key of a provided connection is encrypted under, and
#: its members' translator tokens are derived under, from the settings root key.
OPERATOR_CONNECTION_KEY_DOMAIN = b"cloud-agent/operator-model-connection-key/v1"
#: What an owner's tag is a MAC of, ahead of the account id.
END_USER_TAG_DOMAIN = b"cloud-agent/operator-model-connection/end-user/v1"
END_USER_TAG_CHARS = 24

DEFAULT_USER_CONCURRENCY = 4

#: Money is kept in whole millionths of a US dollar, and a price in those per
#: million tokens, so that a cost is integer arithmetic: 2_800_000 is $2.80
#: per million tokens, and a request's cost is rounded half up to the nearest
#: millionth, as the gateway's cost counters round each request.
MICROUSD_PER_USD = 1_000_000
TOKENS_PER_PRICE = 1_000_000

_HEADER_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,62}")
#: Headers the relay writes, strips or passes on from the client; an owner tag
#: may not be one.
_RESERVED_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "host",
        "content-length",
        "content-type",
        "connection",
        "transfer-encoding",
        "cookie",
        "accept",
        "accept-encoding",
        "user-agent",
        "anthropic-version",
        "anthropic-beta",
        "openai-beta",
    }
)


class OperatorTermsMissing(Exception):
    """A connection cannot be enabled before it has a budget and every model has prices."""

    def __init__(self, models: list[str], *, budget: bool) -> None:
        """Keep the models that still need prices, and whether the budget is still to be chosen."""
        super().__init__("a default budget or model prices are missing")
        self.models = models
        self.budget = budget


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
    """What a request cost, in micro-dollars, rounded half up.

    ``usage.prompt`` counts every input token. Tokens read from or written to
    the provider's cache are taken out of it and charged at their own price
    only where the model has one; otherwise they stay in it, at the input
    price. The gateway's formula (``serving.storage.utils.calculate_cost``)
    and its rounding (``serving.spend_ledger.counted_amount``), in integers:
    the tests carry its own cases.
    """
    plain = usage.prompt
    read_price, write_price = prices.cache_read or 0, prices.cache_write or 0
    if read_price:
        plain -= usage.cache_read
    if write_price:
        plain -= usage.cache_write
    total = (
        max(plain, 0) * prices.input
        + usage.output * prices.output
        + usage.cache_read * read_price
        + usage.cache_write * write_price
    )
    return (total + TOKENS_PER_PRICE // 2) // TOKENS_PER_PRICE


@dataclass(frozen=True)
class Allowance:
    """What one owner may use of one provided model today."""

    #: Whether the model is this owner's to use at all: it has prices, it is
    #: open to them, and their budget on its connection is not zero.
    offered: bool
    #: Micro-dollars the owner may spend per UTC day on the connection's models.
    budget_microusd: int = 0
    #: Micro-dollars the owner's requests on the connection have cost today.
    spent_microusd: int = 0

    @property
    def exhausted(self) -> bool:
        """Whether today's budget is used up."""
        return self.spent_microusd >= self.budget_microusd

    @property
    def remaining_microusd(self) -> int:
        """What is left of today's budget."""
        return max(self.budget_microusd - self.spent_microusd, 0)


#: A model with no prices, or a connection with no budget chosen: offered to nobody.
NOT_OFFERED = Allowance(offered=False)


def effective_budget(default: int | None, own: int | None) -> int | None:
    """An owner's daily budget on a connection; ``None`` while none was chosen."""
    return own if own is not None else default


def effective_allowance(
    terms: Mapping[str, Any] | None,
    *,
    own_offered: bool | None = None,
    budget_microusd: int | None = None,
    spent_microusd: int = 0,
) -> Allowance:
    """The allowance an owner has on a model.

    ``terms`` is the model's stored row, ``None`` when it has none;
    ``own_offered`` is the account's own answer on the model, ``None`` when it
    has none; ``budget_microusd`` is the owner's budget on the connection
    (:func:`effective_budget`). A model without prices and a connection
    without a budget are offered to nobody, whatever an account's rows say.
    """
    if model_prices(terms) is None or budget_microusd is None:
        return NOT_OFFERED
    assert terms is not None
    open_to_owner = own_offered if own_offered is not None else not terms.get("named_only")
    return Allowance(
        offered=bool(open_to_owner) and budget_microusd > 0,
        budget_microusd=budget_microusd,
        spent_microusd=spent_microusd,
    )


def utc_day(now: datetime | None = None) -> datetime:
    """Midnight UTC of the day ``now`` is in."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def next_reset(now: datetime | None = None) -> datetime:
    """When today's counts start over: the next midnight, UTC."""
    return utc_day(now) + timedelta(days=1)


def clean_end_user_header(value: str | None) -> str | None:
    """The header a relayed request names its owner in; ``None`` sends none."""
    name = (value or "").strip()
    if not name:
        return None
    if not _HEADER_NAME.fullmatch(name) or name.lower() in _RESERVED_HEADERS:
        raise ModelConnectionInvalid(
            "An end-user header is a plain header name such as X-End-User, "
            "and not one the relay sets itself."
        )
    return name


def clean_default_model(value: str | None, models: list[str]) -> str | None:
    """The model a new owner starts on: one of ``models``, or none."""
    model = (value or "").strip()
    if not model:
        return None
    if model not in models:
        raise ModelConnectionInvalid("The default model must be one of the listed models.")
    return model


def end_user_tag(secret: str, user_id: str) -> str:
    """An opaque, stable tag for one account, as a provider may attribute requests by.

    A keyed digest: not the account id, its email or anything derived from
    them without the deployment's key, and the same for every request of the
    account, so an administrator can compute it and find the account's
    requests at the provider.
    """
    if not secret or not user_id:
        raise ValueError("an end-user tag needs the deployment's key and an account")
    key = hashlib.sha256(END_USER_TAG_DOMAIN + b"\0" + secret.encode()).digest()
    return hmac.new(key, user_id.encode(), hashlib.sha256).hexdigest()[:END_USER_TAG_CHARS]


@dataclass(frozen=True)
class RelayRoute:
    """One path the model relay forwards."""

    #: Whether the request is an inference call: its body names the model, and
    #: what it costs is charged to the owner's budget.
    inference: bool
    #: Whether a refusal is shaped as Anthropic's error rather than OpenAI's.
    anthropic: bool
    #: The inference API an answer on this path is read as
    #: (``domain.model_usage``); empty where nothing is charged.
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
    "DEFAULT_USER_CONCURRENCY",
    "END_USER_TAG_CHARS",
    "END_USER_TAG_DOMAIN",
    "MICROUSD_PER_USD",
    "NOT_OFFERED",
    "OPERATOR_CONNECTION_KEY_DOMAIN",
    "TOKENS_PER_PRICE",
    "Allowance",
    "ModelPrices",
    "OperatorTermsMissing",
    "RelayRoute",
    "clean_default_model",
    "clean_end_user_header",
    "effective_allowance",
    "effective_budget",
    "end_user_tag",
    "is_messages_path",
    "model_prices",
    "next_reset",
    "relay_route",
    "request_cost",
    "utc_day",
]
