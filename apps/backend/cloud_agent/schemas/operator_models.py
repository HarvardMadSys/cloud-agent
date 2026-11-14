"""Models the deployment provides, as an administrator sets them up.

A *provided* connection is the deployment's own inference endpoint, offered to
every admitted owner without any setup of theirs. Its key never leaves the
control plane. Each request is charged what it used at its model's prices, and
every owner has one daily budget on a connection's models together
(``docs/agents/specs/2026-11-12-platform-model-relay.zh-CN.md``). What an owner
is offered is ``schemas.config.ProvidedModelSource``.

Money is in whole millionths of a US dollar (``…_microusd``: 5000000 is $5),
and a price in those per million tokens (2800000 is $2.80 per million tokens).
"""

from __future__ import annotations

from datetime import (
    date,  # noqa: TC003 — Pydantic resolves annotations at runtime.
    datetime,  # noqa: TC003 — Pydantic resolves annotations at runtime.
)
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: The most a daily budget, a price or a concurrency cap is ever set to: a
#: million dollars a day, and a million dollars per million tokens.
MAX_BUDGET_MICROUSD = 1_000_000_000_000
MAX_PRICE = 1_000_000_000_000
MAX_CONCURRENCY = 1024

_PRICE_UNIT = "In millionths of a US dollar per million tokens: 2800000 is $2.80."


class OperatorModelTerms(BaseModel):
    """What one model is provided on: its prices, who it is open to, and a cap on requests in flight."""

    input_price: int = Field(
        ..., ge=0, le=MAX_PRICE, description=f"The price of input tokens. {_PRICE_UNIT}"
    )
    output_price: int = Field(
        ...,
        ge=0,
        le=MAX_PRICE,
        description=f"The price of output tokens, reasoning included. {_PRICE_UNIT}",
    )
    cache_read_price: int | None = Field(
        None,
        gt=0,
        le=MAX_PRICE,
        description=(
            "The price of input tokens the provider read from its prompt cache. "
            f"Without one they are charged at the input price. {_PRICE_UNIT}"
        ),
    )
    cache_write_price: int | None = Field(
        None,
        gt=0,
        le=MAX_PRICE,
        description=(
            "The price of input tokens the provider wrote to its prompt cache. "
            f"Without one they are charged at the input price. {_PRICE_UNIT}"
        ),
    )
    #: ``True`` offers the model only to accounts it is named for; otherwise
    #: every owner with a budget may use it.
    named_only: bool = False
    #: Requests in flight across all owners; ``None`` is unlimited. "Global" is
    #: across owners, not across processes: see the description.
    global_concurrency: int | None = Field(
        None,
        ge=1,
        le=MAX_CONCURRENCY,
        description=(
            "Requests to this model in flight across all owners. Counted by each "
            "model relay process on its own requests: a deployment that runs "
            "several relay processes allows this many in each."
        ),
    )


class OperatorModelConnectionView(BaseModel):
    """One provided connection, as an administrator reads it. Never its key."""

    id: str
    name: str
    base_url: str
    models: list[str]
    default_model: str | None = None
    is_default: bool = False
    enabled: bool = False
    #: The header each relayed request names its owner in, by an opaque tag.
    end_user_header: str | None = None
    #: Requests one owner may have in flight at once, in each relay process.
    user_concurrency: int = Field(
        4,
        description=(
            "Requests one owner may have in flight at once. Counted by each model "
            "relay process on its own requests."
        ),
    )
    user_daily_budget_microusd: int | None = Field(
        None,
        description=(
            "What each owner may spend per UTC day on this connection's models "
            "together, in millionths of a US dollar. `null` is not chosen yet: the "
            "connection cannot be enabled and offers nothing. `0` offers it only to "
            "accounts given a budget of their own."
        ),
    )
    #: Each model's terms; a model absent here has no prices yet, is offered to
    #: nobody, and keeps the connection from being enabled.
    terms: dict[str, OperatorModelTerms] = Field(default_factory=dict)
    #: Each model's verification, in the shape an owner's connection reports
    #: (``api.model_connections.ModelStatusView``).
    model_status: list[dict[str, Any]] = Field(default_factory=list)
    #: Since when the provider has refused the stored key; ``None`` is healthy.
    key_refused_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class OperatorModelConnectionList(BaseModel):
    """Every provided connection, and whether any host can run a turn on one."""

    connections: list[OperatorModelConnectionView] = Field(default_factory=list)
    #: Enrolled hosts that reach the model relay. Zero means a turn on a
    #: provided connection waits until one is configured.
    relay_hosts: int = 0
    #: Whether owner and provided connections are switched on at all.
    feature_enabled: bool = True


class _Write(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperatorModelTermsWrite(OperatorModelTerms):
    """Set one model's prices, who it is open to, and its concurrency cap."""

    model_config = ConfigDict(extra="forbid")
    model: str = Field(..., min_length=1, max_length=256)


class OperatorModelUserBudgetWrite(_Write):
    """Give one account a daily budget of its own on a connection."""

    #: The account's id, or its email address.
    account: str = Field(..., min_length=1, max_length=320)
    #: In millionths of a US dollar; ``0`` withholds the connection from this account.
    daily_budget_microusd: int = Field(..., ge=0, le=MAX_BUDGET_MICROUSD)


class OperatorModelUserAccessWrite(_Write):
    """Offer one account a model, or withhold it, in place of the model's default."""

    #: The account's id, or its email address.
    account: str = Field(..., min_length=1, max_length=320)
    model: str = Field(..., min_length=1, max_length=256)
    offered: bool


class OperatorModelUserRow(BaseModel):
    """One account with a budget or a model answer of its own, or with use today."""

    account_id: str
    email: str = ""
    #: The account's own daily budget, in millionths of a US dollar; ``None``
    #: follows the connection's default.
    daily_budget_microusd: int | None = None
    #: The account's own answers, per model: offered or withheld. A model
    #: absent here follows the model's default.
    access: dict[str, bool] = Field(default_factory=dict)
    #: What the account's requests on the connection have cost today.
    spent_today_microusd: int = 0
    #: Requests the provider served the account today, across models.
    requests_today: int = 0


class OperatorModelUserList(BaseModel):
    """A page of accounts, by account id."""

    users: list[OperatorModelUserRow] = Field(default_factory=list)
    #: Pass as ``cursor`` for the next page; ``None`` on the last.
    next_cursor: str | None = None


class OperatorModelUsageRow(BaseModel):
    """What one account used of one model on one UTC day, and what it cost."""

    day: date
    account_id: str
    email: str = ""
    model: str
    #: Requests the provider served.
    requests: int
    #: What they cost at the model's prices, in millionths of a US dollar.
    cost_microusd: int = 0
    #: Every input token, the cached ones included.
    input_tokens: int = 0
    #: Of ``input_tokens``: read from, and written to, the provider's prompt cache.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    #: Of ``requests``: charged by estimate, because the answer named no usage
    #: or was cut short before it could.
    estimated_requests: int = 0


class OperatorModelUsageResponse(BaseModel):
    """Use per day, account and model, newest day first and the dearest first within a day."""

    rows: list[OperatorModelUsageRow] = Field(default_factory=list)
    #: True when more rows matched than are answered.
    truncated: bool = False


__all__ = [
    "MAX_BUDGET_MICROUSD",
    "MAX_CONCURRENCY",
    "MAX_PRICE",
    "OperatorModelConnectionList",
    "OperatorModelConnectionView",
    "OperatorModelTerms",
    "OperatorModelTermsWrite",
    "OperatorModelUsageResponse",
    "OperatorModelUsageRow",
    "OperatorModelUserAccessWrite",
    "OperatorModelUserBudgetWrite",
    "OperatorModelUserList",
    "OperatorModelUserRow",
]
