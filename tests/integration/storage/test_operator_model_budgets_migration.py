"""``0120_operator_model_budgets`` against a real database.

It only adds columns and tables. What Postgres settles is what it carries
over from the request limits it replaces, what the new columns refuse, and
that a downgrade leaves the previous release what it reads.
"""

from __future__ import annotations

import asyncpg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from tests.integration.storage.test_agent_job_store import pool as pool
from tests.integration.storage.test_schema_upgrade import _db_config
from tests.support.migrations import REPO, apply_migrations, downgrade_to

pytestmark = pytest.mark.dbtest

_BEFORE = "0119_drop_account_provisioning"
_THIS = "0120_operator_model_budgets"
_ID = "oconn_" + "7" * 32
_NEW_TABLES = ("agent_operator_user_budgets", "agent_operator_model_user_access")
_NEW_COLUMNS = {
    "agent_operator_model_connections": {"user_daily_budget_microusd"},
    "agent_operator_model_limits": {
        "input_price",
        "output_price",
        "cache_read_price",
        "cache_write_price",
        "named_only",
    },
    "agent_operator_model_usage": {
        "cost_microusd",
        "input_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "output_tokens",
        "estimated_requests",
    },
}


def test_the_revision_extends_the_one_migration_line() -> None:
    script = ScriptDirectory.from_config(Config(toml_file=str(REPO / "pyproject.toml")))
    assert script.get_revision(_THIS).down_revision == _BEFORE
    assert script.get_heads() == [_THIS]


async def _added(pool: asyncpg.Pool) -> tuple[set[str], dict[str, set[str]]]:
    """Which of the revision's tables and columns the database has."""
    async with pool.acquire() as conn:
        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE tablename = ANY($1::text[])", list(_NEW_TABLES)
        )
        columns = await conn.fetch(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ANY($1::text[])",
            list(_NEW_COLUMNS),
        )
    found: dict[str, set[str]] = {table: set() for table in _NEW_COLUMNS}
    for row in columns:
        if row["column_name"] in _NEW_COLUMNS[row["table_name"]]:
            found[row["table_name"]].add(row["column_name"])
    return {row["tablename"] for row in tables}, found


async def _connection(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        INSERT INTO agent_operator_model_connections
            (id, name, base_url, models, api_key_ciphertext)
        VALUES ($1, 'Provider', 'https://provider.example/v1',
                ARRAY['open', 'named', 'unlimited'], 'ciphertext')
        """,
        _ID,
    )


@pytest.mark.asyncio
async def test_who_a_model_was_for_is_carried_over_and_no_budget_is_invented(pool) -> None:
    config = _db_config()
    downgrade_to(config, _BEFORE)
    try:
        async with pool.acquire() as conn:
            await _connection(conn)
            # As the previous release wrote them: a default per model, where 0
            # is for named accounts only, and an account's own number.
            await conn.executemany(
                "INSERT INTO agent_operator_model_limits "
                "(operator_connection_id, model, user_daily_requests, global_concurrency) "
                "VALUES ($1, $2, $3, $4)",
                [(_ID, "open", 100, 8), (_ID, "named", 0, None), (_ID, "unlimited", None, None)],
            )
            await conn.executemany(
                "INSERT INTO agent_operator_model_user_limits "
                "(operator_connection_id, user_id, model, daily_requests) VALUES ($1, $2, $3, $4)",
                [
                    (_ID, "granted", "named", 25),
                    (_ID, "granted", "unlimited", None),
                    (_ID, "withheld", "open", 0),
                ],
            )
            await conn.execute(
                "INSERT INTO agent_operator_model_usage "
                "(operator_connection_id, user_id, model, day, requests) "
                "VALUES ($1, 'granted', 'named', CURRENT_DATE, 12)",
                _ID,
            )
        apply_migrations(config, "head")

        async with pool.acquire() as conn:
            models = await conn.fetch(
                "SELECT model, named_only, input_price, output_price, global_concurrency, "
                "user_daily_requests FROM agent_operator_model_limits ORDER BY model"
            )
            access = await conn.fetch(
                "SELECT user_id, model, offered FROM agent_operator_model_user_access "
                "ORDER BY user_id, model"
            )
            budget = await conn.fetchval(
                "SELECT user_daily_budget_microusd FROM agent_operator_model_connections"
            )
            budgets = await conn.fetchval("SELECT count(*) FROM agent_operator_user_budgets")
            usage = await conn.fetchrow(
                "SELECT requests, cost_microusd, input_tokens, estimated_requests "
                "FROM agent_operator_model_usage"
            )
        # No prices: each model is offered to nobody until it is given some.
        assert [tuple(row) for row in models] == [
            ("named", True, None, None, None, 0),
            ("open", False, None, None, 8, 100),
            ("unlimited", False, None, None, None, None),
        ]
        assert [tuple(row) for row in access] == [
            ("granted", "named", True),
            ("granted", "unlimited", True),
            ("withheld", "open", False),
        ]
        # A request count has no price, so no budget follows from one.
        assert budget is None and budgets == 0
        assert tuple(usage) == (12, 0, 0, 0)
    finally:
        apply_migrations(config, "head")
        async with pool.acquire() as conn:
            for table in (
                "agent_operator_model_usage",
                "agent_operator_model_user_access",
                "agent_operator_model_user_limits",
                "agent_operator_model_limits",
                "agent_operator_model_connections",
            ):
                await conn.execute(f"DELETE FROM {table}")


@pytest.mark.asyncio
async def test_a_downgrade_drops_what_was_added_and_an_upgrade_puts_it_back(pool) -> None:
    config = _db_config()
    assert await _added(pool) == (set(_NEW_TABLES), _NEW_COLUMNS)
    downgrade_to(config, _BEFORE)
    try:
        assert await _added(pool) == (set(), {table: set() for table in _NEW_COLUMNS})
        async with pool.acquire() as conn:
            # The previous release's own tables are as it left them.
            assert await conn.fetchval("SELECT count(*) FROM agent_operator_model_user_limits") == 0
    finally:
        apply_migrations(config, "head")
    assert await _added(pool) == (set(_NEW_TABLES), _NEW_COLUMNS)


class _Undo(Exception):
    """Raised to roll back what the schema test wrote."""


@pytest.mark.asyncio
async def test_the_schema_holds_what_no_flow_may_write(pool) -> None:
    with pytest.raises(_Undo):
        await _write_what_the_schema_refuses(pool)


async def _write_what_the_schema_refuses(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await _connection(conn)

        async def refused(statement: str, *args: object) -> None:
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute(statement, *args)

        await refused(
            "UPDATE agent_operator_model_connections SET user_daily_budget_microusd = -1 "
            "WHERE id = $1",
            _ID,
        )
        price = (
            "INSERT INTO agent_operator_model_limits "
            "(operator_connection_id, model, input_price, output_price, {column}) "
            "VALUES ($1, 'open', $2, $3, $4)"
        )
        await refused(price.format(column="cache_read_price"), _ID, -1, 0, None)
        await refused(price.format(column="cache_read_price"), _ID, 0, -1, None)
        # A cache price is a price or absent: zero would read as free.
        await refused(price.format(column="cache_read_price"), _ID, 1, 1, 0)
        await refused(price.format(column="cache_write_price"), _ID, 1, 1, 0)
        # A model may be free.
        await conn.execute(price.format(column="cache_read_price"), _ID, 0, 0, None)
        await refused(
            "INSERT INTO agent_operator_user_budgets "
            "(operator_connection_id, user_id, daily_budget_microusd) VALUES ($1, 'u', -1)",
            _ID,
        )
        for column in ("cost_microusd", "input_tokens", "output_tokens", "estimated_requests"):
            await refused(
                "INSERT INTO agent_operator_model_usage "
                f"(operator_connection_id, user_id, model, day, {column}) "
                "VALUES ($1, 'u', 'open', CURRENT_DATE, -1)",
                _ID,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO agent_operator_user_budgets "
                    "(operator_connection_id, user_id, daily_budget_microusd) VALUES ($1, 'u', 1)",
                    "oconn_" + "0" * 32,
                )
        # Nothing of this is left for another test: the transaction is undone.
        raise _Undo
