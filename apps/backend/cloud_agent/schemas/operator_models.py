"""Models the deployment provides, as an administrator sets them up.

A *provided* connection is the deployment's own inference endpoint, offered to
every admitted owner without any setup of theirs. An administrator gives it a
name, a Base URL and a key; its models are the ones the provider lists for
that key, with the prices the listing gives. Its key never leaves the control
plane. Each request's tokens are recorded, with what they cost at those
prices; nothing here limits them (``docs/agents/specs/2026-11-12-platform-
model-relay.zh-CN.md``). What an owner is offered is
``schemas.config.ProvidedModelSource``.

Money is in whole millionths of a US dollar (``…_microusd``: 5000000 is $5),
and a price in those per million tokens (2800000 is $2.80 per million tokens).
"""

from __future__ import annotations

from datetime import (
    date,  # noqa: TC003 — Pydantic resolves annotations at runtime.
    datetime,  # noqa: TC003 — Pydantic resolves annotations at runtime.
)
from typing import Any

from pydantic import BaseModel, Field

_PRICE_UNIT = "In millionths of a US dollar per million tokens: 2800000 is $2.80."


class OperatorModelPrices(BaseModel):
    """What one model costs, as the provider's listing gives it."""

    input_price: int = Field(..., description=f"The price of input tokens. {_PRICE_UNIT}")
    output_price: int = Field(
        ..., description=f"The price of output tokens, reasoning included. {_PRICE_UNIT}"
    )
    cache_read_price: int | None = Field(
        None,
        description=(
            "The price of input tokens the provider read from its prompt cache. "
            f"Without one they are recorded at the input price. {_PRICE_UNIT}"
        ),
    )
    cache_write_price: int | None = Field(
        None,
        description=(
            "The price of input tokens the provider wrote to its prompt cache. "
            f"Without one they are recorded at the input price. {_PRICE_UNIT}"
        ),
    )


class OperatorModelConnectionView(BaseModel):
    """One provided connection, as an administrator reads it. Never its key."""

    id: str
    name: str
    base_url: str
    #: What the provider lists for the key, in its order.
    models: list[str]
    enabled: bool = False
    #: Each model's prices, from the provider's listing; a model absent here
    #: was listed with none, and its use is recorded by tokens alone.
    prices: dict[str, OperatorModelPrices] = Field(default_factory=dict)
    #: Each model's verification, in the shape an owner's connection reports
    #: (``api.model_connections.ModelStatusView``). A model no agent can use is
    #: listed here and offered to nobody.
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
    #: Of ``requests``: recorded by estimate, because the answer named no usage
    #: or was cut short before it could.
    estimated_requests: int = 0


class OperatorModelUsageResponse(BaseModel):
    """Use per day, account and model, newest day first and the dearest first within a day."""

    rows: list[OperatorModelUsageRow] = Field(default_factory=list)
    #: True when more rows matched than are answered.
    truncated: bool = False


__all__ = [
    "OperatorModelConnectionList",
    "OperatorModelConnectionView",
    "OperatorModelPrices",
    "OperatorModelUsageResponse",
    "OperatorModelUsageRow",
]
