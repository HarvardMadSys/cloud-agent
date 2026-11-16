"""Provided model connections, their prices and the model relay's fence, against a real database.

Only Postgres can show the two things that matter here: that what requests
used adds up when they end together, and that a provided connection's
placeholder resolves only for the residency bound to it while its turn runs.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from tests.integration.storage.test_agent_job_store import _reserve_shared_chat
from tests.integration.storage.test_credential_queries_store import (  # noqa: F401 — fixtures
    db,
    store,
)

from cloud_agent.domain.model_connections import ModelConnectionConflict
from cloud_agent.domain.operator_models import ModelPrices
from cloud_agent.storage.repositories.operator_models import PostgresOperatorModelRepository

if TYPE_CHECKING:
    from cloud_agent.storage.assembly import Storage

pytestmark = [pytest.mark.dbtest, pytest.mark.asyncio]

_ID = "oconn_" + "a" * 32
_SUPPORTED = {"protocols": {"chat_completions": {"status": "supported"}}}
#: $2.80 in, $8.80 out and $0.80 for cached input per million tokens.
_PRICES = ModelPrices(input=2_800_000, output=8_800_000, cache_read=800_000)


@pytest_asyncio.fixture
async def models(db):  # noqa: F811 — the imported fixture
    """The capability under test."""
    return PostgresOperatorModelRepository(db)


async def _create(
    models: PostgresOperatorModelRepository, *, connection_id: str = _ID, name: str = "Provider"
) -> dict[str, Any]:
    return await models.create_operator_connection(
        values={
            "id": connection_id,
            "name": name,
            "base_url": "https://provider.example/v1",
            "models": ["m1", "m2"],
            "api_key_ciphertext": "ciphertext",
            "model_checks": {"m1": _SUPPORTED, "m2": _SUPPORTED},
        }
    )


async def _enabled(models: PostgresOperatorModelRepository) -> dict[str, Any]:
    """An enabled connection with both models priced, as their listing gave them."""
    await _create(models)
    await models.set_operator_prices(connection_id=_ID, prices={"m1": _PRICES, "m2": _PRICES})
    row = await models.update_operator_connection(connection_id=_ID, changes={"enabled": True})
    assert row is not None and row["enabled"]
    return row


async def _record(
    models: PostgresOperatorModelRepository,
    *,
    user: str = "user-1",
    model: str = "m1",
    cost: int = 1_000,
    estimated: bool = False,
) -> None:
    """One served request of 100 input tokens (60 of them cached) and 10 output tokens."""
    await models.record_operator_request(
        connection_id=_ID,
        user_id=user,
        model=model,
        cost_microusd=cost,
        input_tokens=100,
        cache_read_tokens=60,
        cache_write_tokens=0,
        output_tokens=10,
        estimated=estimated,
    )


# -- connections -------------------------------------------------------------------


async def test_a_connection_is_created_disabled_with_its_facts_at_revision_one(models) -> None:
    row = await _create(models)
    assert row["enabled"] is False
    assert (row["config_revision"], row["credential_revision"]) == (1, 1)
    assert row["model_checks"]["m1"]["revision"] == 1
    # Nothing is chosen or limited per connection any more.
    assert not {"default_model", "is_default", "user_daily_budget_microusd"} & set(row)
    assert await models.list_operator_connections() == [row]
    with pytest.raises(ModelConnectionConflict):
        await _create(models, connection_id="oconn_" + "b" * 32, name="PROVIDER")


async def test_a_connection_is_enabled_with_no_prices_and_nothing_else_set(models) -> None:
    await _create(models)
    enabled = await models.update_operator_connection(connection_id=_ID, changes={"enabled": True})
    assert enabled is not None and enabled["enabled"]
    assert (await models.operator_prices(connection_ids=[_ID]))[_ID] == {}
    assert await models.enabled_operator_connections() == [enabled]


async def test_prices_are_stored_for_listed_models_and_removed_when_the_listing_has_none(
    models,
) -> None:
    await _create(models)
    free = ModelPrices(input=0, output=0)
    await models.set_operator_prices(
        connection_id=_ID, prices={"m1": _PRICES, "m2": free, "m9": _PRICES}
    )
    # A model the connection does not list takes no prices.
    assert (await models.operator_prices(connection_ids=[_ID]))[_ID] == {"m1": _PRICES, "m2": free}
    # Read again: new prices replace the old ones, and a model listed without any loses its.
    cheaper = ModelPrices(input=1, output=2, cache_write=3)
    await models.set_operator_prices(connection_id=_ID, prices={"m1": cheaper, "m2": None})
    assert (await models.operator_prices(connection_ids=[_ID]))[_ID] == {"m1": cheaper}
    other = "oconn_" + "b" * 32
    assert await models.operator_prices(connection_ids=[_ID, other]) == {
        _ID: {"m1": cheaper},
        other: {},
    }


async def test_a_model_this_release_prices_reads_as_withheld_to_the_previous_one(
    db,  # noqa: F811
    models,
) -> None:
    """That release knows only a request limit, where no row and NULL mean different things."""
    await _create(models)
    await models.set_operator_prices(connection_id=_ID, prices={"m1": _PRICES})
    async with db.acquire() as conn:
        # A row that release wrote itself keeps the limit it was given.
        await conn.execute(
            "INSERT INTO agent_operator_model_limits "
            "(operator_connection_id, model, user_daily_requests) VALUES ($1, 'm2', 250)",
            _ID,
        )
    await models.set_operator_prices(connection_id=_ID, prices={"m2": _PRICES})
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT model, user_daily_requests, input_price FROM agent_operator_model_limits "
            "WHERE operator_connection_id = $1 ORDER BY model",
            _ID,
        )
    assert [tuple(row) for row in rows] == [("m1", 0, 2_800_000), ("m2", 250, 2_800_000)]


async def test_only_disabling_and_a_new_base_url_move_the_revision_processes_are_bound_to(
    models,
) -> None:
    row = await _enabled(models)
    renamed = await models.update_operator_connection(
        connection_id=_ID, changes={"name": "Renamed"}
    )
    assert renamed is not None and renamed["credential_revision"] == 1

    rotated = await models.commit_verified_operator_update(
        connection_id=_ID,
        expected=renamed,
        changes={"api_key_ciphertext": "new-ciphertext"},
        model_checks={"m1": _SUPPORTED, "m2": _SUPPORTED},
    )
    # A new key: every model's facts are replaced, and no process needs closing.
    assert rotated is not None
    assert (rotated["credential_revision"], rotated["config_revision"]) == (1, 2)
    assert rotated["model_checks"]["m1"]["revision"] == 2

    moved = await models.commit_verified_operator_update(
        connection_id=_ID,
        expected=rotated,
        changes={"base_url": "https://other.example/v1"},
        model_checks={"m1": _SUPPORTED, "m2": _SUPPORTED},
    )
    assert moved is not None
    assert (moved["credential_revision"], moved["config_revision"]) == (2, 3)

    # An edit verified against the row as it was before is not applied on top.
    assert (
        await models.commit_verified_operator_update(
            connection_id=_ID,
            expected=row,
            changes={"api_key_ciphertext": "late"},
            model_checks={"m1": _SUPPORTED, "m2": _SUPPORTED},
        )
        is None
    )
    disabled = await models.update_operator_connection(
        connection_id=_ID, changes={"enabled": False}
    )
    assert disabled is not None and disabled["credential_revision"] == 3


async def test_a_removed_model_takes_its_facts_and_its_prices(db, models) -> None:  # noqa: F811
    await _enabled(models)
    async with db.acquire() as conn:
        # An account's answer an earlier release wrote goes with the model too.
        await conn.execute(
            "INSERT INTO agent_operator_model_user_access "
            "(operator_connection_id, user_id, model, offered) VALUES ($1, 'user-1', 'm1', FALSE)",
            _ID,
        )
    row = await models.update_operator_connection(connection_id=_ID, changes={"models": ["m2"]})
    assert row is not None
    assert row["models"] == ["m2"] and list(row["model_checks"]) == ["m2"]
    assert list((await models.operator_prices(connection_ids=[_ID]))[_ID]) == ["m2"]
    async with db.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agent_operator_model_user_access") == 0
    # A list that adds a model needs a verification, so this path refuses it.
    assert (
        await models.update_operator_connection(connection_id=_ID, changes={"models": ["m2", "m3"]})
        is None
    )


async def test_a_verified_edit_that_lists_other_models_drops_the_removed_ones_prices(
    models,
) -> None:
    row = await _enabled(models)
    moved = await models.commit_verified_operator_update(
        connection_id=_ID,
        expected=row,
        changes={"api_key_ciphertext": "new-ciphertext", "models": ["m2", "m3"]},
        model_checks={"m2": _SUPPORTED, "m3": _SUPPORTED},
    )
    assert moved is not None and moved["models"] == ["m2", "m3"]
    assert list((await models.operator_prices(connection_ids=[_ID]))[_ID]) == ["m2"]


async def test_deleting_erases_the_key_and_keeps_a_tombstone(models) -> None:
    await _enabled(models)
    deleted = await models.delete_operator_connection(connection_id=_ID)
    assert deleted is not None
    assert deleted["api_key_ciphertext"] is None and not deleted["enabled"]
    assert deleted["deleted_at"] is not None and deleted["credential_revision"] == 2
    assert await models.list_operator_connections() == []
    # A turn that names it still finds a row to be refused by.
    tombstone = await models.get_operator_connection(connection_id=_ID)
    assert tombstone is not None and tombstone["deleted_at"] is not None
    assert await models.delete_operator_connection(connection_id=_ID) is None
    # Its name is free again.
    await _create(models, connection_id="oconn_" + "b" * 32)


# -- what the relay recorded --------------------------------------------------------


async def test_use_is_recorded_per_day_account_and_model(models) -> None:
    await _enabled(models)
    await _record(models, model="m1", cost=1_200)
    await _record(models, model="m2", cost=800, estimated=True)
    await _record(models, model="m2", cost=1_000)
    await _record(models, user="user-2", cost=2_999)
    # A model listed without prices is recorded by its tokens, at no cost.
    await _record(models, user="user-3", cost=0)

    today = datetime.now(UTC).date()
    rows = await models.operator_usage(connection_id=_ID, start=today, end=today, limit=10)
    # The dearest first within a day.
    assert [(row["user_id"], row["model"], row["cost_microusd"]) for row in rows] == [
        ("user-2", "m1", 2_999),
        ("user-1", "m2", 1_800),
        ("user-1", "m1", 1_200),
        ("user-3", "m1", 0),
    ]
    assert dict(rows[1]) == {
        "day": today,
        "user_id": "user-1",
        "model": "m2",
        "requests": 2,
        "cost_microusd": 1_800,
        "input_tokens": 200,
        "cache_read_tokens": 120,
        "cache_write_tokens": 0,
        "output_tokens": 20,
        "estimated_requests": 1,
    }


async def test_requests_that_end_together_are_all_recorded(models) -> None:
    await _enabled(models)
    await asyncio.gather(*(_record(models, cost=7) for _ in range(40)))
    today = datetime.now(UTC).date()
    [row] = await models.operator_usage(connection_id=_ID, start=today, end=today, limit=10)
    assert (row["requests"], row["cost_microusd"]) == (40, 280)


async def test_use_belongs_to_its_utc_day(db, models) -> None:  # noqa: F811
    await _enabled(models)
    await _record(models, cost=1_000)
    async with db.acquire() as conn:
        await conn.execute("UPDATE agent_operator_model_usage SET day = day - 1")
    await _record(models, cost=400)
    today = datetime.now(UTC).date()
    rows = await models.operator_usage(
        connection_id=_ID, start=today - timedelta(days=7), end=today, limit=10
    )
    assert [(row["day"], row["cost_microusd"]) for row in rows] == [
        (today, 400),
        (today - timedelta(days=1), 1_000),
    ]
    assert (
        await models.operator_usage(connection_id=_ID, start=today, end=today, limit=1) == rows[:1]
    )


async def test_a_record_written_later_lands_on_the_day_its_answer_ended(models) -> None:
    await _enabled(models)
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    await models.record_operator_request(
        connection_id=_ID,
        user_id="user-1",
        model="m1",
        cost_microusd=700,
        input_tokens=100,
        cache_read_tokens=0,
        cache_write_tokens=0,
        output_tokens=10,
        estimated=False,
        day=yesterday,
    )
    rows = await models.operator_usage(connection_id=_ID, start=yesterday, end=today, limit=10)
    assert [(row["day"], row["cost_microusd"]) for row in rows] == [(yesterday, 700)]


async def test_owners_are_offered_every_enabled_connection_oldest_first(models) -> None:
    await _enabled(models)
    await _create(models, connection_id="oconn_" + "b" * 32, name="Disabled")
    [source] = await models.enabled_operator_connections()
    assert source["id"] == _ID and source["models"] == ["m1", "m2"]
    later = "oconn_" + "c" * 32
    await _create(models, connection_id=later, name="Later")
    await models.update_operator_connection(connection_id=later, changes={"enabled": True})
    assert [row["id"] for row in await models.enabled_operator_connections()] == [_ID, later]
    await models.delete_operator_connection(connection_id=_ID)
    assert [row["id"] for row in await models.enabled_operator_connections()] == [later]


# -- residencies and the relay's fence ---------------------------------------------


async def _turn_on_provided(db, store, models) -> tuple[dict, dict, dict]:  # noqa: F811
    """A running shared-live turn whose job names the provided connection."""
    await _enabled(models)
    job, claim, sandbox = await _reserve_shared_chat(store)
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE agent_jobs SET model_connection_id = $2 WHERE id = $1", job["id"], _ID
        )
    return job, claim, sandbox


async def _bind(store, job, sandbox, *, revision: int = 1) -> bool:  # noqa: F811
    return await store.model_connections.bind_operator_residency(
        user_id=job["user_id"],
        connection_id=_ID,
        revision=revision,
        thread_id=job["thread_id"],
        generation=sandbox["residency_generation"],
    )


async def test_the_placeholder_resolves_only_for_the_residency_bound_to_the_connection(
    db,  # noqa: F811
    store: Storage,  # noqa: F811
    models,
) -> None:
    job, claim, sandbox = await _turn_on_provided(db, store, models)
    lookup = {
        "thread_id": job["thread_id"],
        "residency_generation": sandbox["residency_generation"],
    }

    unbound = await models.resolve_model_relay_caller(**lookup, model="m1")
    assert unbound is not None and unbound["bound_revision"] is None

    assert await _bind(store, job, sandbox)
    caller = await models.resolve_model_relay_caller(**lookup, model="m1")
    assert caller is not None
    assert (caller["job_id"], caller["attempt_id"], caller["user_id"]) == (
        job["id"],
        claim["attempt_id"],
        job["user_id"],
    )
    assert caller["connection"]["id"] == _ID and caller["bound_revision"] == 1
    assert caller["prices"] == _PRICES
    assert "allowance" not in caller and "global_concurrency" not in caller
    # A model listed without prices, and a request that names none.
    await models.set_operator_prices(connection_id=_ID, prices={"m2": None})
    unpriced = await models.resolve_model_relay_caller(**lookup, model="m2")
    assert unpriced is not None and unpriced["prices"] is None
    listing = await models.resolve_model_relay_caller(**lookup, model=None)
    assert listing is not None and listing["prices"] is None

    # Another residency generation, and Stop, buy nothing.
    assert (
        await models.resolve_model_relay_caller(
            thread_id=job["thread_id"],
            residency_generation=sandbox["residency_generation"] + 1,
            model="m1",
        )
        is None
    )
    async with db.acquire() as conn:
        await conn.execute("UPDATE agent_jobs SET cancel_requested = TRUE WHERE id = $1", job["id"])
    assert await models.resolve_model_relay_caller(**lookup, model="m1") is None


async def test_a_turn_on_an_owner_connection_resolves_to_no_provided_connection(
    db,  # noqa: F811
    store: Storage,  # noqa: F811
    models,
) -> None:
    await _enabled(models)
    job, _claim, sandbox = await _reserve_shared_chat(store)
    caller = await models.resolve_model_relay_caller(
        thread_id=job["thread_id"],
        residency_generation=sandbox["residency_generation"],
        model="m1",
    )
    # A live turn, so not a 401; but nothing of the deployment's to use.
    assert caller is not None and caller["connection"] is None
    assert caller["prices"] is None


async def test_a_binding_and_a_change_cannot_both_win(
    db,  # noqa: F811
    store: Storage,  # noqa: F811
    models,
) -> None:
    job, _claim, sandbox = await _turn_on_provided(db, store, models)

    # The change first: a binding at the old revision is refused.
    await models.update_operator_connection(connection_id=_ID, changes={"enabled": False})
    assert not await _bind(store, job, sandbox)
    await models.update_operator_connection(connection_id=_ID, changes={"enabled": True})
    assert not await _bind(store, job, sandbox, revision=1)

    # The binding first: the change's release finds it.
    assert await _bind(store, job, sandbox, revision=2)
    assert await models.release_stale_operator_residencies(connection_id=_ID) == []
    await models.update_operator_connection(connection_id=_ID, changes={"enabled": False})
    [released] = await models.release_stale_operator_residencies(connection_id=_ID)
    assert (released["thread_id"], released["generation"]) == (
        job["thread_id"],
        sandbox["residency_generation"],
    )
    # Released once.
    assert await models.release_stale_operator_residencies(connection_id=_ID) == []


async def test_a_rotated_key_leaves_bound_residencies_running(
    db,  # noqa: F811
    store: Storage,  # noqa: F811
    models,
) -> None:
    job, _claim, sandbox = await _turn_on_provided(db, store, models)
    assert await _bind(store, job, sandbox)
    row = await models.get_operator_connection(connection_id=_ID)
    assert row is not None
    await models.commit_verified_operator_update(
        connection_id=_ID,
        expected=row,
        changes={"api_key_ciphertext": "rotated"},
        model_checks={"m1": _SUPPORTED, "m2": _SUPPORTED},
    )
    assert await models.release_stale_operator_residencies(connection_id=_ID) == []
    caller = await models.resolve_model_relay_caller(
        thread_id=job["thread_id"],
        residency_generation=sandbox["residency_generation"],
        model="m1",
    )
    assert caller is not None
    assert caller["bound_revision"] == caller["connection"]["credential_revision"] == 1
    assert caller["connection"]["api_key_ciphertext"] == "rotated"


async def test_another_owners_residency_cannot_be_bound(
    db,  # noqa: F811
    store: Storage,  # noqa: F811
    models,
) -> None:
    job, _claim, sandbox = await _turn_on_provided(db, store, models)
    assert not await store.model_connections.bind_operator_residency(
        user_id="somebody-else",
        connection_id=_ID,
        revision=1,
        thread_id=job["thread_id"],
        generation=sandbox["residency_generation"],
    )


async def test_the_key_health_mark_keeps_when_it_started(models) -> None:
    await _enabled(models)
    await models.mark_operator_key_refused(connection_id=_ID, refused=True)
    first = (await models.get_operator_connection(connection_id=_ID))["key_refused_at"]
    assert first is not None
    await models.mark_operator_key_refused(connection_id=_ID, refused=True)
    assert (await models.get_operator_connection(connection_id=_ID))["key_refused_at"] == first
    await models.mark_operator_key_refused(connection_id=_ID, refused=False)
    assert (await models.get_operator_connection(connection_id=_ID))["key_refused_at"] is None


async def test_hosts_that_reach_the_relay_are_counted(store: Storage, models) -> None:  # noqa: F811
    assert await models.model_relay_host_count() == 0
    await store.host_pool.register_host(
        host="relay-host",
        credential_hash="hash",
        capabilities=["shared-sandbox-v1", "model-relay-v1"],
        slots_total=1,
    )
    await store.host_pool.register_host(
        host="plain-host",
        credential_hash="hash-2",
        capabilities=["shared-sandbox-v1"],
        slots_total=1,
    )
    assert await models.model_relay_host_count() == 1
