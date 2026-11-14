"""Provided model connections, budgets and the model relay's fence, against a real database.

Only Postgres can show the two things that matter here: that what requests
cost adds up when they end together, and that a provided connection's
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
from cloud_agent.domain.operator_models import ModelPrices, OperatorTermsMissing
from cloud_agent.storage.repositories.operator_models import PostgresOperatorModelRepository

if TYPE_CHECKING:
    from cloud_agent.storage.assembly import Storage

pytestmark = [pytest.mark.dbtest, pytest.mark.asyncio]

_ID = "oconn_" + "a" * 32
_SUPPORTED = {"protocols": {"chat_completions": {"status": "supported"}}}
#: $2.80 in, $8.80 out and $0.80 for cached input per million tokens, open to every owner.
_TERMS: dict[str, Any] = {
    "input_price": 2_800_000,
    "output_price": 8_800_000,
    "cache_read_price": 800_000,
    "cache_write_price": None,
    "named_only": False,
    "global_concurrency": None,
}
_BUDGET = 5_000_000


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
            "default_model": "m1",
            "api_key_ciphertext": "ciphertext",
            "end_user_header": None,
            "user_concurrency": 4,
            "model_checks": {"m1": _SUPPORTED, "m2": _SUPPORTED},
        }
    )


async def _enabled(
    models: PostgresOperatorModelRepository,
    *,
    budget: int = _BUDGET,
    named_only: tuple[str, ...] = (),
) -> dict[str, Any]:
    """An enabled connection: both models priced, ``named_only`` ones for named accounts."""
    await _create(models)
    for model in ("m1", "m2"):
        assert await models.set_operator_terms(
            connection_id=_ID, model=model, terms={**_TERMS, "named_only": model in named_only}
        )
    row = await models.update_operator_connection(
        connection_id=_ID, changes={"user_daily_budget_microusd": budget, "enabled": True}
    )
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
    assert (row["enabled"], row["is_default"]) == (False, False)
    assert (row["config_revision"], row["credential_revision"]) == (1, 1)
    assert row["model_checks"]["m1"]["revision"] == 1
    # No budget is chosen for it; one may be given at creation.
    assert row["user_daily_budget_microusd"] is None
    assert await models.list_operator_connections() == [row]
    with pytest.raises(ModelConnectionConflict):
        await _create(models, connection_id="oconn_" + "b" * 32, name="PROVIDER")


async def test_enabling_needs_a_budget_and_prices_for_every_listed_model(models) -> None:
    await _create(models)
    with pytest.raises(OperatorTermsMissing) as missing:
        await models.update_operator_connection(connection_id=_ID, changes={"enabled": True})
    assert (missing.value.models, missing.value.budget) == (["m1", "m2"], True)
    assert await models.set_operator_terms(
        connection_id=_ID, model="m1", terms={**_TERMS, "global_concurrency": 2}
    )
    with pytest.raises(OperatorTermsMissing) as missing:
        await models.update_operator_connection(connection_id=_ID, changes={"enabled": True})
    assert (missing.value.models, missing.value.budget) == (["m2"], True)
    # A model that is not listed takes no terms, so none can be set ahead of it.
    assert not await models.set_operator_terms(connection_id=_ID, model="m9", terms=_TERMS)
    assert await models.set_operator_terms(
        connection_id=_ID, model="m2", terms={**_TERMS, "named_only": True}
    )
    with pytest.raises(OperatorTermsMissing) as missing:
        await models.update_operator_connection(connection_id=_ID, changes={"enabled": True})
    assert (missing.value.models, missing.value.budget) == ([], True)
    # The budget may be chosen in the edit that enables. Zero is a choice too.
    enabled = await models.update_operator_connection(
        connection_id=_ID, changes={"enabled": True, "user_daily_budget_microusd": 0}
    )
    assert enabled is not None and enabled["enabled"]
    assert enabled["user_daily_budget_microusd"] == 0
    assert (await models.operator_terms(connection_ids=[_ID]))[_ID] == {
        "m1": {**_TERMS, "global_concurrency": 2},
        "m2": {**_TERMS, "named_only": True},
    }


async def test_a_model_this_release_prices_reads_as_withheld_to_the_previous_one(
    db,  # noqa: F811
    models,
) -> None:
    """That release knows only a request limit, where no row and NULL mean different things."""
    await _create(models)
    await models.set_operator_terms(connection_id=_ID, model="m1", terms=_TERMS)
    async with db.acquire() as conn:
        # A row that release wrote itself keeps the limit it was given.
        await conn.execute(
            "INSERT INTO agent_operator_model_limits "
            "(operator_connection_id, model, user_daily_requests) VALUES ($1, 'm2', 250)",
            _ID,
        )
    await models.set_operator_terms(connection_id=_ID, model="m2", terms=_TERMS)
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


async def test_a_removed_model_takes_its_facts_its_terms_and_the_default_choice(
    models,
) -> None:
    await _enabled(models)
    for model in ("m1", "m2"):
        await models.set_operator_user_access(
            connection_id=_ID, user_id="user-1", model=model, offered=False
        )
    row = await models.update_operator_connection(connection_id=_ID, changes={"models": ["m2"]})
    assert row is not None
    assert (row["models"], row["default_model"]) == (["m2"], None)
    assert list(row["model_checks"]) == ["m2"]
    assert list((await models.operator_terms(connection_ids=[_ID]))[_ID]) == ["m2"]
    [user] = await models.list_operator_users(connection_id=_ID, cursor=None, limit=10)
    assert user["access"] == {"m2": False}
    # A list that adds a model needs a verification, so this path refuses it.
    assert (
        await models.update_operator_connection(connection_id=_ID, changes={"models": ["m2", "m3"]})
        is None
    )


async def test_at_most_one_connection_is_where_a_new_owner_starts(models) -> None:
    await _create(models)
    other = "oconn_" + "b" * 32
    await _create(models, connection_id=other, name="Other")
    await models.update_operator_connection(connection_id=_ID, changes={"is_default": True})
    await models.update_operator_connection(connection_id=other, changes={"is_default": True})
    flags = {row["id"]: row["is_default"] for row in await models.list_operator_connections()}
    assert flags == {_ID: False, other: True}


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


# -- who a model is for, budgets and what was spent --------------------------------


async def test_a_model_is_open_to_every_owner_or_only_to_named_accounts(models, store) -> None:  # noqa: F811
    await _enabled(models, named_only=("m2",))
    connections = store.model_connections

    async def offered(user: str, model: str) -> bool:
        allowance = await connections.operator_allowance(
            connection_id=_ID, user_id=user, model=model
        )
        return allowance.offered

    assert await offered("user-1", "m1")
    assert not await offered("user-1", "m2")
    # A model with no prices is offered to nobody, whatever an account was given.
    assert not await offered("user-1", "m9")
    assert not await models.set_operator_user_access(
        connection_id=_ID, user_id="user-1", model="m9", offered=True
    )

    # An account's own answer replaces the model's default, in both directions.
    await models.set_operator_user_access(
        connection_id=_ID, user_id="user-1", model="m2", offered=True
    )
    await models.set_operator_user_access(
        connection_id=_ID, user_id="user-2", model="m1", offered=False
    )
    assert await offered("user-1", "m2")
    assert not await offered("user-2", "m1")
    assert not await offered("user-2", "m2")

    assert await models.clear_operator_user_access(connection_id=_ID, user_id="user-1", model="m2")
    assert not await models.clear_operator_user_access(
        connection_id=_ID, user_id="user-1", model="m2"
    )
    assert not await offered("user-1", "m2")


async def test_a_budget_is_the_accounts_own_or_the_connections_default(models, store) -> None:  # noqa: F811
    await _enabled(models, budget=5_000_000)
    connections = store.model_connections

    async def allowance(user: str):
        return await connections.operator_allowance(connection_id=_ID, user_id=user, model="m1")

    assert (await allowance("user-1")).budget_microusd == 5_000_000
    assert await models.set_operator_user_budget(
        connection_id=_ID, user_id="user-1", daily_budget_microusd=20_000_000
    )
    await models.set_operator_user_budget(
        connection_id=_ID, user_id="user-2", daily_budget_microusd=0
    )
    assert (await allowance("user-1")).budget_microusd == 20_000_000
    # A budget of zero withholds the connection from that account.
    assert not (await allowance("user-2")).offered
    assert (await allowance("user-3")).budget_microusd == 5_000_000

    assert await models.clear_operator_user_budget(connection_id=_ID, user_id="user-2")
    assert not await models.clear_operator_user_budget(connection_id=_ID, user_id="user-2")
    assert (await allowance("user-2")).offered

    # A default of zero offers the connection only to accounts with a budget of their own.
    await models.update_operator_connection(
        connection_id=_ID, changes={"user_daily_budget_microusd": 0}
    )
    assert not (await allowance("user-3")).offered
    assert (await allowance("user-1")).offered
    # No account can be given a budget on a connection that is gone.
    await models.delete_operator_connection(connection_id=_ID)
    assert not await models.set_operator_user_budget(
        connection_id=_ID, user_id="user-3", daily_budget_microusd=1
    )


async def test_what_requests_cost_is_spent_of_one_budget_across_the_models(models, store) -> None:  # noqa: F811
    await _enabled(models, budget=3_000)
    await _record(models, model="m1", cost=1_200)
    await _record(models, model="m2", cost=800, estimated=True)
    # Another account's spending is its own.
    await _record(models, user="user-2", cost=2_999)

    async def allowance(user: str, model: str):
        return await store.model_connections.operator_allowance(
            connection_id=_ID, user_id=user, model=model
        )

    for model in ("m1", "m2"):
        mine = await allowance("user-1", model)
        assert (mine.spent_microusd, mine.remaining_microusd, mine.exhausted) == (
            2_000,
            1_000,
            False,
        )
    await _record(models, model="m2", cost=1_000)
    assert (await allowance("user-1", "m1")).exhausted
    assert not (await allowance("user-2", "m1")).exhausted

    today = datetime.now(UTC).date()
    rows = await models.operator_usage(connection_id=_ID, start=today, end=today, limit=10)
    # The dearest first within a day.
    assert [(row["user_id"], row["model"], row["cost_microusd"]) for row in rows] == [
        ("user-2", "m1", 2_999),
        ("user-1", "m2", 1_800),
        ("user-1", "m1", 1_200),
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


async def test_requests_that_end_together_are_all_recorded(models, store) -> None:  # noqa: F811
    await _enabled(models)
    await asyncio.gather(*(_record(models, cost=7) for _ in range(40)))
    allowance = await store.model_connections.operator_allowance(
        connection_id=_ID, user_id="user-1", model="m1"
    )
    assert allowance.spent_microusd == 280
    [user] = await models.list_operator_users(connection_id=_ID, cursor=None, limit=10)
    assert user == {
        "user_id": "user-1",
        "daily_budget_microusd": None,
        "access": {},
        "spent_today_microusd": 280,
        "requests_today": 40,
    }


async def test_spending_belongs_to_its_utc_day(db, models, store) -> None:  # noqa: F811
    await _enabled(models, budget=1_000)
    await _record(models, cost=1_000)
    spent = {"connection_id": _ID, "user_id": "user-1", "model": "m1"}
    assert (await store.model_connections.operator_allowance(**spent)).exhausted
    # Yesterday's spending leaves today's budget whole.
    async with db.acquire() as conn:
        await conn.execute("UPDATE agent_operator_model_usage SET day = day - 1")
    assert (await store.model_connections.operator_allowance(**spent)).spent_microusd == 0
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


async def test_an_owner_is_shown_every_enabled_source_with_their_own_numbers(models) -> None:
    await _enabled(models, named_only=("m2",))
    await _create(models, connection_id="oconn_" + "b" * 32, name="Disabled")
    await models.set_operator_user_access(
        connection_id=_ID, user_id="user-1", model="m2", offered=True
    )
    await models.set_operator_user_budget(
        connection_id=_ID, user_id="user-1", daily_budget_microusd=9_000_000
    )
    await _record(models, cost=1_500)

    [source] = await models.operator_sources_for_user(user_id="user-1")
    assert source["id"] == _ID
    assert (source["budget_microusd"], source["spent_microusd"]) == (9_000_000, 1_500)
    assert {model: a.offered for model, a in source["allowances"].items()} == {
        "m1": True,
        "m2": True,
    }
    assert source["allowances"]["m2"].spent_microusd == 1_500
    [stranger] = await models.operator_sources_for_user(user_id="user-2")
    assert (stranger["budget_microusd"], stranger["spent_microusd"]) == (_BUDGET, 0)
    assert {model: a.offered for model, a in stranger["allowances"].items()} == {
        "m1": True,
        "m2": False,
    }


async def test_a_source_whose_model_lost_its_prices_offers_it_to_nobody(db, models) -> None:  # noqa: F811
    """As a connection a previous release enabled reads until it is priced."""
    await _enabled(models)
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE agent_operator_model_limits SET input_price = NULL WHERE model = 'm2'"
        )
    [source] = await models.operator_sources_for_user(user_id="user-1")
    assert {model: a.offered for model, a in source["allowances"].items()} == {
        "m1": True,
        "m2": False,
    }
    assert "m2" in (await models.operator_terms(connection_ids=[_ID]))[_ID]


async def test_accounts_are_paged_by_id(models) -> None:
    await _enabled(models)
    await models.set_operator_user_budget(
        connection_id=_ID, user_id="user-3", daily_budget_microusd=1
    )
    await models.set_operator_user_access(
        connection_id=_ID, user_id="user-1", model="m1", offered=False
    )
    await _record(models, user="user-2")
    first = await models.list_operator_users(connection_id=_ID, cursor=None, limit=2)
    assert [row["user_id"] for row in first] == ["user-1", "user-2"]
    assert first[0]["access"] == {"m1": False} and first[1]["requests_today"] == 1
    rest = await models.list_operator_users(connection_id=_ID, cursor="user-2", limit=2)
    assert [(row["user_id"], row["daily_budget_microusd"]) for row in rest] == [("user-3", 1)]
    # One account is read as the list gives it, and so is one the list leaves out.
    assert await models.operator_user(connection_id=_ID, user_id="user-1") == first[0]
    assert await models.operator_user(connection_id=_ID, user_id="nobody") == {
        "user_id": "nobody",
        "daily_budget_microusd": None,
        "access": {},
        "spent_today_microusd": 0,
        "requests_today": 0,
    }


# -- residencies and the relay's fence ---------------------------------------------


async def _turn_on_provided(db, store, models) -> tuple[dict, dict, dict]:  # noqa: F811
    """A running shared-live turn whose job names the provided connection."""
    await _enabled(models, named_only=("m2",))
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
    assert caller["allowance"].offered
    assert (caller["allowance"].budget_microusd, caller["allowance"].spent_microusd) == (_BUDGET, 0)
    assert caller["prices"] == ModelPrices(
        input=2_800_000, output=8_800_000, cache_read=800_000, cache_write=None
    )
    assert caller["global_concurrency"] is None
    # What the owner has spent on the connection today is read with it.
    await _record(models, user=job["user_id"], model="m2", cost=250)
    spent = await models.resolve_model_relay_caller(**lookup, model="m1")
    assert spent is not None and spent["allowance"].spent_microusd == 250
    # A model withheld from this owner, and a request that names none.
    withheld = await models.resolve_model_relay_caller(**lookup, model="m2")
    assert withheld is not None and not withheld["allowance"].offered
    assert withheld["prices"] is not None
    listing = await models.resolve_model_relay_caller(**lookup, model=None)
    assert listing is not None and listing["allowance"] is None and listing["prices"] is None

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
    assert caller["allowance"] is None


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
