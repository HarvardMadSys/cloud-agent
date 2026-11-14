"""Erasing an account takes its budget, its model answers and its use of provided models with it."""

from __future__ import annotations

import pytest
from tests.integration.storage.test_local_account_parity_store import (  # noqa: F401 — fixtures
    _secret,
    _stopped,
    pool,
    storage,
)
from tests.support.local_accounts import act, make_account

pytestmark = [pytest.mark.dbtest, pytest.mark.asyncio]

_ID = "oconn_" + "f" * 32


async def test_an_erased_account_leaves_no_budget_access_or_usage_row(storage, pool) -> None:  # noqa: F811
    models = storage.operator_models
    await models.create_operator_connection(
        values={
            "id": _ID,
            "name": "Provider",
            "base_url": "https://provider.example/v1",
            "models": ["m1"],
            "api_key_ciphertext": "ciphertext",
            "user_concurrency": 4,
            "model_checks": {"m1": {"protocols": {"chat_completions": {"status": "supported"}}}},
        }
    )
    terms = {"input_price": 2_800_000, "output_price": 8_800_000}
    await models.set_operator_terms(connection_id=_ID, model="m1", terms=terms)
    gone = await make_account(storage, "gone@example.edu")
    stays = await make_account(storage, "stays@example.edu")
    for account in (gone, stays):
        await models.set_operator_user_budget(
            connection_id=_ID, user_id=account, daily_budget_microusd=50_000_000
        )
        await models.set_operator_user_access(
            connection_id=_ID, user_id=account, model="m1", offered=True
        )
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
        # What a previous release kept of an account, which nothing reads now.
        for account in (gone, stays):
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
    assert (await models.operator_terms(connection_ids=[_ID]))[_ID]["m1"][
        "input_price"
    ] == 2_800_000
