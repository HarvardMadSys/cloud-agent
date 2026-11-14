"""Writing a charge says whether a failed write may have been counted.

A failure to get a connection, or the server refusing the statement, wrote
nothing, and the relay may write it again. A connection lost after the
statement was sent may have written it, and must not be retried.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import pytest

from cloud_agent.ports.operator_models import ChargeOutcomeUnknown
from cloud_agent.storage.repositories.operator_models import PostgresOperatorModelRepository

pytestmark = pytest.mark.asyncio

_CHARGE: dict[str, Any] = {
    "connection_id": "oconn_" + "a" * 32,
    "user_id": "user-1",
    "model": "m1",
    "cost_microusd": 700,
    "input_tokens": 100,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "output_tokens": 10,
    "estimated": False,
}


class _Connection:
    def __init__(self, failure: BaseException | None) -> None:
        self.failure = failure
        self.statements = 0

    async def execute(self, *_args: Any) -> str:
        self.statements += 1
        if self.failure is not None:
            raise self.failure
        return "INSERT 0 1"


class _Pool:
    def __init__(self, *, acquire: BaseException | None = None, execute=None) -> None:
        self.refuse = acquire
        self.connection = _Connection(execute)

    @asynccontextmanager
    async def acquire(self):
        if self.refuse is not None:
            raise self.refuse
        yield self.connection


async def test_a_written_charge_is_one_statement() -> None:
    pool = _Pool()
    await PostgresOperatorModelRepository(pool).record_operator_request(**_CHARGE)  # type: ignore[arg-type]
    assert pool.connection.statements == 1


@pytest.mark.parametrize(
    "failure",
    [ConnectionRefusedError("no database"), asyncpg.TooManyConnectionsError("full")],
)
async def test_no_connection_wrote_nothing_and_raises_as_it_is(failure) -> None:
    pool = _Pool(acquire=failure)
    with pytest.raises(type(failure)):
        await PostgresOperatorModelRepository(pool).record_operator_request(**_CHARGE)  # type: ignore[arg-type]
    assert pool.connection.statements == 0


async def test_a_statement_the_server_refused_wrote_nothing_and_raises_as_it_is() -> None:
    refused = asyncpg.CheckViolationError("refused")
    pool = _Pool(execute=refused)
    with pytest.raises(asyncpg.CheckViolationError):
        await PostgresOperatorModelRepository(pool).record_operator_request(**_CHARGE)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "failure",
    [
        asyncpg.ConnectionDoesNotExistError("connection was closed in the middle of operation"),
        asyncpg.AdminShutdownError("terminating connection due to administrator command"),
        ConnectionResetError("reset"),
        TimeoutError(),
    ],
)
async def test_a_connection_lost_after_sending_may_have_written_it(failure) -> None:
    pool = _Pool(execute=failure)
    with pytest.raises(ChargeOutcomeUnknown):
        await PostgresOperatorModelRepository(pool).record_operator_request(**_CHARGE)  # type: ignore[arg-type]
