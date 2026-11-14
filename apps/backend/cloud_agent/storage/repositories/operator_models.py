"""Postgres persistence for the models a deployment provides, and owners' budgets on them."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import asyncpg

from cloud_agent.domain.model_connections import ModelConnectionConflict
from cloud_agent.domain.operator_models import (
    NOT_OFFERED,
    Allowance,
    OperatorTermsMissing,
    effective_allowance,
    effective_budget,
    model_prices,
)
from cloud_agent.ports.operator_models import ChargeOutcomeUnknown
from cloud_agent.storage._sql.accounts import local_access_ended
from cloud_agent.storage._sql.fences import RESIDENCY_CALLER_FROM, live_residency_caller_sql
from cloud_agent.storage._sql.rows import load_json
from cloud_agent.storage._sql.sandbox_transitions import (
    RELEASE_STALE_OPERATOR_RESIDENCIES_MEMBERS_SQL,
)
from cloud_agent_shared.model_connections import MODEL_RELAY_CAPABILITY

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

_COLUMNS = (
    "id, name, base_url, models, default_model, is_default, enabled, api_key_ciphertext, "
    "end_user_header, user_concurrency, user_daily_budget_microusd, model_checks, "
    "config_revision, credential_revision, key_refused_at, created_at, updated_at, deleted_at"
)
#: A model's terms: its prices, who it is open to, and its cap on requests in flight.
_TERMS = (
    "input_price, output_price, cache_read_price, cache_write_price, named_only, global_concurrency"
)
#: What an edit may change without a verification: none of these makes the
#: connection reach a new endpoint or list a model it has no facts for.
_UNVERIFIED = frozenset(
    {
        "name",
        "models",
        "default_model",
        "is_default",
        "enabled",
        "end_user_header",
        "user_concurrency",
        "user_daily_budget_microusd",
    }
)
#: What a verified edit may change. Enabling and the default flag are not
#: among them: they have rules of their own (``update_connection``).
_VERIFIED = frozenset(
    {
        "name",
        "models",
        "default_model",
        "end_user_header",
        "user_concurrency",
        "user_daily_budget_microusd",
        "base_url",
        "api_key_ciphertext",
    }
)
_ARRAY_COLUMNS = frozenset({"models"})
#: Today, as every budget counts it.
_TODAY = "(clock_timestamp() AT TIME ZONE 'UTC')::date"


def _spent_today(connection: str, user: str) -> str:
    """SQL for what one owner's requests on one connection have cost today."""
    return (
        "COALESCE((SELECT sum(u.cost_microusd) FROM agent_operator_model_usage u "
        f"WHERE u.operator_connection_id = {connection} AND u.user_id = {user} "
        f"AND u.day = {_TODAY}), 0)"
    )


def _connection(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["models"] = list(result.get("models") or [])
    checks = load_json(result.get("model_checks"))
    result["model_checks"] = checks if isinstance(checks, dict) else {}
    return result


def _placeholder(column: str, index: int) -> str:
    return f"${index}" + ("::text[]" if column in _ARRAY_COLUMNS else "")


async def read_operator_connection(conn: Any, connection_id: str) -> dict[str, Any] | None:
    """One provided connection, tombstones included, or ``None``."""
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM agent_operator_model_connections WHERE id = $1", connection_id
    )
    return None if row is None else _connection(row)


async def read_operator_allowance(
    conn: Any, *, connection_id: str, user_id: str, model: str
) -> Allowance:
    """One owner's allowance on one provided model, with what they spent today."""
    row = await conn.fetchrow(
        f"""
        SELECT t.input_price, t.output_price, t.named_only, a.offered AS own_offered,
               c.user_daily_budget_microusd AS default_budget,
               b.daily_budget_microusd AS own_budget,
               {_spent_today("c.id", "$2")} AS spent
        FROM agent_operator_model_connections c
        LEFT JOIN agent_operator_model_limits t
               ON t.operator_connection_id = c.id AND t.model = $3
        LEFT JOIN agent_operator_model_user_access a
               ON a.operator_connection_id = c.id AND a.model = $3 AND a.user_id = $2
        LEFT JOIN agent_operator_user_budgets b
               ON b.operator_connection_id = c.id AND b.user_id = $2
        WHERE c.id = $1
        """,
        connection_id,
        user_id,
        model,
    )
    if row is None:
        return NOT_OFFERED
    return effective_allowance(
        row,
        own_offered=row["own_offered"],
        budget_microusd=effective_budget(row["default_budget"], row["own_budget"]),
        spent_microusd=int(row["spent"]),
    )


async def bind_operator_residency(
    conn: Any,
    *,
    user_id: str,
    connection_id: str,
    revision: int,
    thread_id: str,
    generation: int,
) -> bool:
    """Stamp a residency with the provided connection revision its process starts on.

    Within the caller's transaction. Read under the connection's row lock, as
    an owner connection's binding is: a change that commits first refuses
    this, and one that commits after finds and closes the residency. Nothing
    else is recorded, since no key enters the sandbox.
    """
    current = await conn.fetchval(
        """
        SELECT credential_revision FROM agent_operator_model_connections
        WHERE id = $1 AND deleted_at IS NULL AND enabled
        FOR SHARE
        """,
        connection_id,
    )
    if current is None or int(current) != revision:
        return False
    held = await conn.fetchval(
        f"""
        SELECT 1 FROM agent_thread_sandbox_memberships m
        JOIN agent_threads t ON t.id = m.thread_id
        WHERE m.thread_id = $1 AND m.residency_generation = $2
          AND m.residency_status = 'hot'
          AND m.residency_lease_expires_at > clock_timestamp()
          AND t.user_id = $3
          AND NOT {local_access_ended("t.user_id")}
        FOR UPDATE OF m
        """,
        thread_id,
        generation,
        user_id,
    )
    if held is None:
        return False
    await conn.execute(
        """
        INSERT INTO agent_operator_connection_residencies
            (thread_id, residency_generation, operator_connection_id, credential_revision)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (thread_id) DO UPDATE
        SET residency_generation = EXCLUDED.residency_generation,
            operator_connection_id = EXCLUDED.operator_connection_id,
            credential_revision = EXCLUDED.credential_revision
        """,
        thread_id,
        generation,
        connection_id,
        revision,
    )
    return True


class PostgresOperatorModelRepository:
    """Provided connections, their limits, and what the model relay reads and counts."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Wrap the pool the control plane already owns."""
        self._pool = pool

    # -- connections -----------------------------------------------------------

    async def list_operator_connections(self) -> list[dict[str, Any]]:
        """Every live provided connection, oldest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_COLUMNS} FROM agent_operator_model_connections
                WHERE deleted_at IS NULL ORDER BY created_at, id
                """
            )
        return [_connection(row) for row in rows]

    async def get_operator_connection(self, *, connection_id: str) -> dict[str, Any] | None:
        """One provided connection, tombstones included, or ``None``."""
        async with self._pool.acquire() as conn:
            return await read_operator_connection(conn, connection_id)

    async def create_operator_connection(self, *, values: dict[str, Any]) -> dict[str, Any]:
        """Insert a disabled connection at its first revisions, with its verified models."""
        checks = {
            model: {**result, "revision": 1} for model, result in values["model_checks"].items()
        }
        async with self._pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO agent_operator_model_connections
                        (id, name, base_url, models, default_model, api_key_ciphertext,
                         end_user_header, user_concurrency, model_checks,
                         user_daily_budget_microusd)
                    VALUES ($1, $2, $3, $4::text[], $5, $6, $7, $8, $9::jsonb, $10)
                    RETURNING {_COLUMNS}
                    """,
                    values["id"],
                    values["name"],
                    values["base_url"],
                    values["models"],
                    values.get("default_model"),
                    values["api_key_ciphertext"],
                    values.get("end_user_header"),
                    int(values["user_concurrency"]),
                    json.dumps(checks),
                    values.get("user_daily_budget_microusd"),
                )
            except asyncpg.UniqueViolationError as exc:
                raise ModelConnectionConflict(values["name"]) from exc
        assert row is not None
        return _connection(row)

    async def update_operator_connection(
        self, *, connection_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Apply an edit that needs no verification; ``None`` when nothing live matched.

        Renames, the default model and flag, the owner tag header, the
        per-owner concurrency, the default budget, a model list that only
        shrinks, and enabling or disabling. Enabling is refused
        (:class:`OperatorTermsMissing`) before the connection has a default
        budget and every listed model has prices. Disabling raises the
        credential revision, so the caller's release finds every process
        started before it. A removed model takes its verification, its terms,
        accounts' answers on it and, when it was the default model, that
        choice along. Making a connection the default takes the flag from any
        other.
        """
        unknown = set(changes) - _UNVERIFIED
        if unknown:
            raise ValueError(f"a provided connection cannot change {sorted(unknown)} unverified")
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                "SELECT models, enabled, user_daily_budget_microusd "
                "FROM agent_operator_model_connections "
                "WHERE id = $1 AND deleted_at IS NULL FOR UPDATE",
                connection_id,
            )
            if current is None:
                return None
            if not changes:
                return await read_operator_connection(conn, connection_id)
            models = list(changes.get("models", current["models"]))
            if not set(models) <= set(current["models"]):
                return None
            if changes.get("enabled") and not current["enabled"]:
                budget = changes.get(
                    "user_daily_budget_microusd", current["user_daily_budget_microusd"]
                )
                await self._require_terms(conn, connection_id, models, budget)
            if changes.get("is_default"):
                await conn.execute(
                    "UPDATE agent_operator_model_connections SET is_default = FALSE "
                    "WHERE is_default AND id <> $1",
                    connection_id,
                )
            args: list[Any] = [connection_id]
            assignments = _unverified_assignments(changes, args)
            try:
                row = await conn.fetchrow(
                    f"""
                    UPDATE agent_operator_model_connections
                    SET {", ".join(assignments)}, updated_at = NOW()
                    WHERE id = $1 AND deleted_at IS NULL
                    RETURNING {_COLUMNS}
                    """,
                    *args,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ModelConnectionConflict(str(changes.get("name"))) from exc
            if "models" in changes:
                await _forget_removed_models(conn, connection_id, models)
        return None if row is None else _connection(row)

    @staticmethod
    async def _require_terms(
        conn: Any, connection_id: str, models: list[str], budget: int | None
    ) -> None:
        rows = await conn.fetch(
            "SELECT model FROM agent_operator_model_limits WHERE operator_connection_id = $1 "
            "AND input_price IS NOT NULL AND output_price IS NOT NULL",
            connection_id,
        )
        priced = {str(row["model"]) for row in rows}
        missing = [model for model in models if model not in priced]
        if missing or budget is None:
            raise OperatorTermsMissing(missing, budget=budget is None)

    async def commit_verified_operator_update(
        self,
        *,
        connection_id: str,
        expected: Mapping[str, Any],
        changes: dict[str, Any],
        model_checks: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Apply an edit whose new models or endpoint were verified, if nothing moved meanwhile.

        Lands only while the connection is live and still at ``expected``'s
        revisions, model list and stored facts, as an owner connection's edit
        does. A new key or Base URL replaces every model's facts and raises
        the config revision. Only a new Base URL raises the credential
        revision: a member's process holds a placeholder and the address it
        was launched at, never the key, so a rotated key serves the very next
        request and closes nothing.
        """
        unknown = set(changes) - _VERIFIED
        if unknown:
            raise ValueError(f"a provided connection cannot change {sorted(unknown)}")
        endpoint = "base_url" in changes or "api_key_ciphertext" in changes
        revision = int(expected["config_revision"]) + (1 if endpoint else 0)
        checks = {model: {**result, "revision": revision} for model, result in model_checks.items()}
        args: list[Any] = [
            connection_id,
            int(expected["credential_revision"]),
            int(expected["config_revision"]),
            list(expected["models"]),
            json.dumps(checks),
            json.dumps(expected["model_checks"]),
        ]
        assignments = ["model_checks = $5::jsonb"]
        for column, value in changes.items():
            args.append(value)
            assignments.append(f"{column} = {_placeholder(column, len(args))}")
        if endpoint:
            assignments.append("config_revision = config_revision + 1")
        if "api_key_ciphertext" in changes:
            assignments.append("key_refused_at = NULL")
        if "base_url" in changes:
            assignments.append("credential_revision = credential_revision + 1")
        async with self._pool.acquire() as conn, conn.transaction():
            try:
                row = await conn.fetchrow(
                    f"""
                    UPDATE agent_operator_model_connections
                    SET {", ".join(assignments)}, updated_at = NOW()
                    WHERE id = $1 AND deleted_at IS NULL
                      AND credential_revision = $2 AND config_revision = $3
                      AND models = $4::text[] AND model_checks = $6::jsonb
                    RETURNING {_COLUMNS}
                    """,
                    *args,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ModelConnectionConflict(str(changes.get("name"))) from exc
            if row is not None and "models" in changes:
                await _forget_removed_models(conn, connection_id, list(changes["models"]))
        return None if row is None else _connection(row)

    async def delete_operator_connection(self, *, connection_id: str) -> dict[str, Any] | None:
        """Keep the row as a tombstone for the turns that name it, and erase the key."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE agent_operator_model_connections
                SET deleted_at = NOW(), enabled = FALSE, is_default = FALSE,
                    api_key_ciphertext = NULL, key_refused_at = NULL,
                    credential_revision = credential_revision + 1,
                    config_revision = config_revision + 1, model_checks = '{{}}'::jsonb,
                    updated_at = NOW()
                WHERE id = $1 AND deleted_at IS NULL
                RETURNING {_COLUMNS}
                """,
                connection_id,
            )
        return None if row is None else _connection(row)

    async def mark_operator_key_refused(self, *, connection_id: str, refused: bool) -> None:
        """Remember since when the provider refuses the stored key, or that it no longer does."""
        value = "COALESCE(key_refused_at, NOW())" if refused else "NULL"
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE agent_operator_model_connections SET key_refused_at = {value} "
                "WHERE id = $1 AND deleted_at IS NULL",
                connection_id,
            )

    async def model_relay_host_count(self) -> int:
        """How many enrolled, enabled hosts say their sandboxes reach the model relay."""
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT count(*) FROM agent_runner_hosts
                WHERE credential_hash IS NOT NULL AND status <> 'disabled'
                  AND capabilities @> $1::jsonb
                """,
                json.dumps([MODEL_RELAY_CAPABILITY]),
            )
        return int(count or 0)

    # -- terms, budgets and use ------------------------------------------------

    async def operator_terms(
        self, *, connection_ids: list[str]
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Each connection's stored terms, per model."""
        result: dict[str, dict[str, dict[str, Any]]] = {cid: {} for cid in connection_ids}
        if not connection_ids:
            return result
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT operator_connection_id, model, {_TERMS}
                FROM agent_operator_model_limits
                WHERE operator_connection_id = ANY($1::text[])
                """,
                connection_ids,
            )
        for row in rows:
            terms = dict(row)
            connection_id, model = str(terms.pop("operator_connection_id")), str(terms.pop("model"))
            result[connection_id][model] = terms
        return result

    async def set_operator_terms(
        self, *, connection_id: str, model: str, terms: Mapping[str, Any]
    ) -> bool:
        """Set one listed model's terms; ``False`` when it is not listed.

        A new row is written with the request limit ``0``: a previous release,
        which reads only that, then offers the model to nobody by default
        rather than without limit.
        """
        async with self._pool.acquire() as conn:
            written = await conn.fetchval(
                f"""
                INSERT INTO agent_operator_model_limits
                    (operator_connection_id, model, user_daily_requests, {_TERMS})
                SELECT c.id, $2, 0, $3, $4, $5, $6, $7, $8
                FROM agent_operator_model_connections c
                WHERE c.id = $1 AND c.deleted_at IS NULL AND $2 = ANY(c.models)
                ON CONFLICT (operator_connection_id, model) DO UPDATE
                SET input_price = EXCLUDED.input_price,
                    output_price = EXCLUDED.output_price,
                    cache_read_price = EXCLUDED.cache_read_price,
                    cache_write_price = EXCLUDED.cache_write_price,
                    named_only = EXCLUDED.named_only,
                    global_concurrency = EXCLUDED.global_concurrency
                RETURNING 1
                """,
                connection_id,
                model,
                terms["input_price"],
                terms["output_price"],
                terms.get("cache_read_price"),
                terms.get("cache_write_price"),
                bool(terms.get("named_only")),
                terms.get("global_concurrency"),
            )
        return written is not None

    async def set_operator_user_budget(
        self, *, connection_id: str, user_id: str, daily_budget_microusd: int
    ) -> bool:
        """Give one account its own daily budget; ``False`` when the connection is gone."""
        async with self._pool.acquire() as conn:
            written = await conn.fetchval(
                """
                INSERT INTO agent_operator_user_budgets
                    (operator_connection_id, user_id, daily_budget_microusd)
                SELECT c.id, $2, $3 FROM agent_operator_model_connections c
                WHERE c.id = $1 AND c.deleted_at IS NULL
                ON CONFLICT (operator_connection_id, user_id) DO UPDATE
                SET daily_budget_microusd = EXCLUDED.daily_budget_microusd
                RETURNING 1
                """,
                connection_id,
                user_id,
                daily_budget_microusd,
            )
        return written is not None

    async def clear_operator_user_budget(self, *, connection_id: str, user_id: str) -> bool:
        """Return one account to the default budget; ``False`` when it had none of its own."""
        async with self._pool.acquire() as conn:
            removed = await conn.fetchval(
                """
                DELETE FROM agent_operator_user_budgets
                WHERE operator_connection_id = $1 AND user_id = $2
                RETURNING 1
                """,
                connection_id,
                user_id,
            )
        return removed is not None

    async def set_operator_user_access(
        self, *, connection_id: str, user_id: str, model: str, offered: bool
    ) -> bool:
        """Offer one account a listed model, or withhold it; ``False`` when not listed."""
        async with self._pool.acquire() as conn:
            written = await conn.fetchval(
                """
                INSERT INTO agent_operator_model_user_access
                    (operator_connection_id, user_id, model, offered)
                SELECT c.id, $2, $3, $4 FROM agent_operator_model_connections c
                WHERE c.id = $1 AND c.deleted_at IS NULL AND $3 = ANY(c.models)
                ON CONFLICT (operator_connection_id, user_id, model) DO UPDATE
                SET offered = EXCLUDED.offered
                RETURNING 1
                """,
                connection_id,
                user_id,
                model,
                offered,
            )
        return written is not None

    async def clear_operator_user_access(
        self, *, connection_id: str, user_id: str, model: str
    ) -> bool:
        """Return one account to a model's default; ``False`` when it had no answer of its own."""
        async with self._pool.acquire() as conn:
            removed = await conn.fetchval(
                """
                DELETE FROM agent_operator_model_user_access
                WHERE operator_connection_id = $1 AND user_id = $2 AND model = $3
                RETURNING 1
                """,
                connection_id,
                user_id,
                model,
            )
        return removed is not None

    async def list_operator_users(
        self, *, connection_id: str, cursor: str | None, limit: int
    ) -> list[dict[str, Any]]:
        """Accounts with a budget or an answer of their own, or with use today, by account id.

        Each as ``{"user_id", "daily_budget_microusd": n|None, "access":
        {model: bool}, "spent_today_microusd": n, "requests_today": n}``.
        """
        async with self._pool.acquire() as conn:
            ids = [
                str(row["user_id"])
                for row in await conn.fetch(
                    f"""
                    SELECT user_id FROM (
                        SELECT user_id FROM agent_operator_user_budgets
                        WHERE operator_connection_id = $1
                        UNION
                        SELECT user_id FROM agent_operator_model_user_access
                        WHERE operator_connection_id = $1
                        UNION
                        SELECT user_id FROM agent_operator_model_usage
                        WHERE operator_connection_id = $1 AND day = {_TODAY} AND requests > 0
                    ) ids
                    WHERE $2::text IS NULL OR user_id > $2
                    ORDER BY user_id LIMIT $3
                    """,
                    connection_id,
                    cursor,
                    limit,
                )
            ]
            return await _user_rows(conn, connection_id, ids)

    async def operator_user(self, *, connection_id: str, user_id: str) -> dict[str, Any]:
        """One account as :meth:`list_operator_users` lists it.

        Answered for any account, one with nothing of its own and no use
        today included: its row then says so.
        """
        async with self._pool.acquire() as conn:
            [row] = await _user_rows(conn, connection_id, [user_id])
        return row

    async def operator_usage(
        self, *, connection_id: str, start: date, end: date, limit: int
    ) -> list[dict[str, Any]]:
        """Requests, tokens and cost per day, account and model in ``[start, end]``.

        Newest day first, and within a day the dearest first.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT day, user_id, model, requests, cost_microusd, input_tokens,
                       cache_read_tokens, cache_write_tokens, output_tokens, estimated_requests
                FROM agent_operator_model_usage
                WHERE operator_connection_id = $1 AND day BETWEEN $2 AND $3 AND requests > 0
                ORDER BY day DESC, cost_microusd DESC, requests DESC, user_id, model
                LIMIT $4
                """,
                connection_id,
                start,
                end,
                limit,
            )
        return [dict(row) for row in rows]

    async def operator_sources_for_user(self, *, user_id: str) -> list[dict[str, Any]]:
        """Every enabled connection, each with ``user_id``'s allowance on each listed model.

        A connection row plus ``allowances``: ``{model: Allowance}`` for every
        listed model, the ones withheld from this owner included; and the
        owner's budget on the connection and what they spent on it today
        (``budget_microusd``, ``None`` while none is chosen, and
        ``spent_microusd``).
        """
        async with self._pool.acquire() as conn:
            connections = [
                _connection(row)
                for row in await conn.fetch(
                    f"""
                    SELECT {_COLUMNS} FROM agent_operator_model_connections
                    WHERE deleted_at IS NULL AND enabled ORDER BY created_at, id
                    """
                )
            ]
            if not connections:
                return []
            ids = [connection["id"] for connection in connections]
            budgets = await conn.fetch(
                f"""
                SELECT c.id, b.daily_budget_microusd AS own_budget,
                       {_spent_today("c.id", "$2")} AS spent
                FROM agent_operator_model_connections c
                LEFT JOIN agent_operator_user_budgets b
                       ON b.operator_connection_id = c.id AND b.user_id = $2
                WHERE c.id = ANY($1::text[])
                """,
                ids,
                user_id,
            )
            rows = await conn.fetch(
                """
                SELECT t.operator_connection_id, t.model, t.input_price, t.output_price,
                       t.named_only, a.offered AS own_offered
                FROM agent_operator_model_limits t
                LEFT JOIN agent_operator_model_user_access a
                       ON a.operator_connection_id = t.operator_connection_id
                      AND a.model = t.model AND a.user_id = $2
                WHERE t.operator_connection_id = ANY($1::text[])
                """,
                ids,
                user_id,
            )
        own = {str(row["id"]): row for row in budgets}
        terms = {(str(row["operator_connection_id"]), str(row["model"])): row for row in rows}
        for connection in connections:
            mine = own[connection["id"]]
            budget = effective_budget(connection["user_daily_budget_microusd"], mine["own_budget"])
            spent = int(mine["spent"])
            connection["budget_microusd"] = budget
            connection["spent_microusd"] = spent
            connection["allowances"] = {}
            for model in connection["models"]:
                row = terms.get((connection["id"], model))
                connection["allowances"][model] = effective_allowance(
                    row,
                    own_offered=None if row is None else row["own_offered"],
                    budget_microusd=budget,
                    spent_microusd=spent,
                )
        return connections

    # -- residencies -----------------------------------------------------------

    async def release_stale_operator_residencies(
        self, *, connection_id: str
    ) -> list[dict[str, Any]]:
        """Release the open residencies a change to this connection left behind."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(RELEASE_STALE_OPERATOR_RESIDENCIES_MEMBERS_SQL, connection_id)
        return [dict(row) for row in rows]

    # -- the model relay -------------------------------------------------------

    async def resolve_model_relay_caller(
        self, *, thread_id: str, residency_generation: int, model: str | None
    ) -> dict[str, Any] | None:
        """Name the running turn a model relay placeholder speaks for, and what it may use.

        ``None`` unless the residency passes the relays' shared fence
        (:func:`~cloud_agent.storage._sql.fences.live_residency_caller_sql`).
        Otherwise the turn's job, owner and provided connection, with the
        revision this residency's process was bound to (``bound_revision``,
        ``None`` when it was bound to none) and, for ``model``, the owner's
        allowance (``allowance``), the model's prices (``prices``, ``None``
        while it has none) and its deployment-wide concurrency cap.
        ``connection`` is ``None`` when the turn does not run on a provided
        connection at all.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT j.id AS job_id, a.id AS attempt_id, j.thread_id, j.user_id,
                       j.model_connection_id, b.credential_revision AS bound_revision
                FROM {RESIDENCY_CALLER_FROM}
                LEFT JOIN agent_operator_connection_residencies b
                       ON b.thread_id = m.thread_id
                      AND b.residency_generation = m.residency_generation
                      AND b.operator_connection_id = j.model_connection_id
                WHERE {live_residency_caller_sql("$1", "$2")}
                """,
                thread_id,
                residency_generation,
            )
            if row is None:
                return None
            caller = dict(row)
            connection_id = caller.get("model_connection_id")
            caller["connection"] = (
                await read_operator_connection(conn, str(connection_id)) if connection_id else None
            )
            caller["allowance"] = None
            caller["prices"] = None
            caller["global_concurrency"] = None
            if caller["connection"] is not None and model is not None:
                caller["allowance"] = await read_operator_allowance(
                    conn,
                    connection_id=str(connection_id),
                    user_id=str(caller["user_id"]),
                    model=model,
                )
                terms = await conn.fetchrow(
                    f"SELECT {_TERMS} FROM agent_operator_model_limits "
                    "WHERE operator_connection_id = $1 AND model = $2",
                    str(connection_id),
                    model,
                )
                caller["prices"] = model_prices(terms)
                caller["global_concurrency"] = (
                    None if terms is None else terms["global_concurrency"]
                )
        return caller

    async def record_operator_request(
        self,
        *,
        connection_id: str,
        user_id: str,
        model: str,
        cost_microusd: int,
        input_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        output_tokens: int,
        estimated: bool,
        day: date | None = None,
    ) -> None:
        """Add one served request, its tokens and its cost to the owner's use on ``day``.

        One statement, so requests of one owner on one model that end together
        queue on their own row and none is lost. A failure to get a
        connection, or the server refusing the statement, wrote nothing and
        raises as it is; a connection lost after the statement was sent
        raises :class:`ChargeOutcomeUnknown`.
        """
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    f"""
                    INSERT INTO agent_operator_model_usage AS u
                        (operator_connection_id, user_id, model, day, requests, cost_microusd,
                         input_tokens, cache_read_tokens, cache_write_tokens, output_tokens,
                         estimated_requests)
                    VALUES ($1, $2, $3, COALESCE($10::date, {_TODAY}), 1,
                            $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (operator_connection_id, user_id, model, day) DO UPDATE
                    SET requests = u.requests + 1,
                        cost_microusd = u.cost_microusd + EXCLUDED.cost_microusd,
                        input_tokens = u.input_tokens + EXCLUDED.input_tokens,
                        cache_read_tokens = u.cache_read_tokens + EXCLUDED.cache_read_tokens,
                        cache_write_tokens = u.cache_write_tokens + EXCLUDED.cache_write_tokens,
                        output_tokens = u.output_tokens + EXCLUDED.output_tokens,
                        estimated_requests = u.estimated_requests + EXCLUDED.estimated_requests
                    """,
                    connection_id,
                    user_id,
                    model,
                    cost_microusd,
                    input_tokens,
                    cache_read_tokens,
                    cache_write_tokens,
                    output_tokens,
                    1 if estimated else 0,
                    day,
                )
            except (asyncpg.PostgresConnectionError, asyncpg.OperatorInterventionError) as exc:
                # The connection broke, or the server stopped it, with the
                # statement sent: it may have been counted.
                raise ChargeOutcomeUnknown(str(exc)) from exc
            except asyncpg.PostgresError:
                # The server refused the statement: nothing was written.
                raise
            except Exception as exc:
                raise ChargeOutcomeUnknown(str(exc)) from exc


async def _user_rows(conn: Any, connection_id: str, ids: list[str]) -> list[dict[str, Any]]:
    """What each of ``ids`` has of its own on a connection, and its use today, in that order."""
    users: dict[str, dict[str, Any]] = {
        user_id: {
            "user_id": user_id,
            "daily_budget_microusd": None,
            "access": {},
            "spent_today_microusd": 0,
            "requests_today": 0,
        }
        for user_id in ids
    }
    if not ids:
        return []
    for row in await conn.fetch(
        """
        SELECT user_id, daily_budget_microusd FROM agent_operator_user_budgets
        WHERE operator_connection_id = $1 AND user_id = ANY($2::text[])
        """,
        connection_id,
        ids,
    ):
        users[str(row["user_id"])]["daily_budget_microusd"] = int(row["daily_budget_microusd"])
    for row in await conn.fetch(
        """
        SELECT user_id, model, offered FROM agent_operator_model_user_access
        WHERE operator_connection_id = $1 AND user_id = ANY($2::text[])
        """,
        connection_id,
        ids,
    ):
        users[str(row["user_id"])]["access"][str(row["model"])] = bool(row["offered"])
    for row in await conn.fetch(
        f"""
        SELECT user_id, sum(requests) AS requests, sum(cost_microusd) AS cost
        FROM agent_operator_model_usage
        WHERE operator_connection_id = $1 AND user_id = ANY($2::text[])
          AND day = {_TODAY} AND requests > 0
        GROUP BY user_id
        """,
        connection_id,
        ids,
    ):
        user = users[str(row["user_id"])]
        user["requests_today"] = int(row["requests"])
        user["spent_today_microusd"] = int(row["cost"])
    return list(users.values())


def _unverified_assignments(changes: dict[str, Any], args: list[Any]) -> list[str]:
    """The ``SET`` list of an edit that needs no verification, judged against the stored row."""
    placeholders: dict[str, str] = {}
    assignments: list[str] = []
    for column, value in changes.items():
        args.append(value)
        placeholders[column] = _placeholder(column, len(args))
        assignments.append(f"{column} = {placeholders[column]}")
    if "enabled" in changes:
        assignments.append(
            "credential_revision = credential_revision + "
            f"CASE WHEN enabled AND NOT {placeholders['enabled']} THEN 1 ELSE 0 END"
        )
    if "models" in changes:
        assignments.append(
            "model_checks = (SELECT COALESCE(jsonb_object_agg(key, value), '{}'::jsonb) "
            f"FROM jsonb_each(model_checks) WHERE key = ANY({placeholders['models']}))"
        )
        if "default_model" not in changes:
            assignments.append(
                "default_model = CASE WHEN default_model = "
                f"ANY({placeholders['models']}) THEN default_model END"
            )
    return assignments


async def _forget_removed_models(conn: Any, connection_id: str, models: list[str]) -> None:
    """Drop the terms of models a connection no longer lists, and accounts' answers on them.

    A model listed again later starts with no prices, so it is offered to
    nobody until its administrator sets them. The last table is the request
    limits a previous release wrote, which nothing reads any more.
    """
    for table in (
        "agent_operator_model_limits",
        "agent_operator_model_user_access",
        "agent_operator_model_user_limits",
    ):
        await conn.execute(
            f"DELETE FROM {table} WHERE operator_connection_id = $1 AND NOT (model = ANY($2::text[]))",
            connection_id,
            models,
        )


__all__ = [
    "PostgresOperatorModelRepository",
    "bind_operator_residency",
    "read_operator_allowance",
    "read_operator_connection",
]
