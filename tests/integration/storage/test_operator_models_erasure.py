"""Erasing an account takes its use of provided models with it, and what earlier releases kept."""

from __future__ import annotations

import pytest
from tests.integration.storage.test_local_account_parity_store import (  # noqa: F401 — fixtures
    _secret,
    _stopped,
    pool,
    storage,
)
from tests.support.local_accounts import act, make_account

from cloud_agent.domain.operator_models import ModelPrices

pytestmark = [pytest.mark.dbtest, pytest.mark.asyncio]

_ID = "oconn_" + "f" * 32


async def test_an_erased_account_leaves_no_usage_row_or_older_one_of_its_own(storage, pool) -> None:  # noqa: F811
    models = storage.operator_models
    await models.create_operator_connection(
        values={
            "id": _ID,
            "name": "Provider",
            "base_url": "https://provider.example/v1",
            "models": ["m1"],
            "api_key_ciphertext": "ciphertext",
            "model_checks": {"m1": {"protocols": {"chat_completions": {"status": "supported"}}}},
        }
    )
    prices = ModelPrices(input=2_800_000, output=8_800_000)
    await models.set_operator_prices(connection_id=_ID, prices={"m1": prices})
    gone = await make_account(storage, "gone@example.edu")
    stays = await make_account(storage, "stays@example.edu")
    for account in (gone, stays):
        await models.record_operator_request(
            connection_id=_ID,
            user_id=account,
            model="m1",
            cost_microusd=1_000,
            input_tokens=100,
            cache_read_tokens=0,
            cache_write_tokens=0,
            output_tokens=10,
            estimated=False,
        )
    async with pool.acquire() as conn:
        # What earlier releases kept of an account, which nothing reads now.
        for account in (gone, stays):
            await conn.execute(
                "INSERT INTO agent_operator_user_budgets "
                "(operator_connection_id, user_id, daily_budget_microusd) VALUES ($1, $2, 5)",
                _ID,
                account,
            )
            await conn.execute(
                "INSERT INTO agent_operator_model_user_access "
                "(operator_connection_id, user_id, model, offered) VALUES ($1, $2, 'm1', TRUE)",
                _ID,
                account,
            )
            await conn.execute(
                "INSERT INTO agent_operator_model_user_limits "
                "(operator_connection_id, user_id, model, daily_requests) VALUES ($1, $2, 'm1', 5)",
                _ID,
                account,
            )

    await act(storage, gone, "delete")
    await _stopped(storage, gone)
    await storage.local_accounts.erase_account(
        account_id=gone, actor_id="admin-token", actor_account_id=None, admin_ip="t"
    )

    async with pool.acquire() as conn:
        for table in (
            "agent_operator_user_budgets",
            "agent_operator_model_user_access",
            "agent_operator_model_usage",
            "agent_operator_model_user_limits",
        ):
            owners = await conn.fetch(f"SELECT user_id FROM {table}")
            assert [row["user_id"] for row in owners] == [stays], table
    # The connection and the model's prices are the deployment's, and stay.
    assert [row["id"] for row in await models.list_operator_connections()] == [_ID]
    assert (await models.operator_prices(connection_ids=[_ID]))[_ID] == {"m1": prices}
