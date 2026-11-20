"""Postgres persistence for the models a deployment provides, and what the relay records of their use."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import asyncpg

from cloud_agent.domain.model_connections import ModelConnectionConflict
from cloud_agent.domain.operator_models import model_prices
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

    from cloud_agent.domain.operator_models import ModelPrices

_COLUMNS = (
    "id, name, base_url, models, enabled, api_key_ciphertext, model_checks, "
    "config_revision, credential_revision, key_refused_at, created_at, updated_at, deleted_at"
)
#: A model's prices, as its provider's listing gave them.
_PRICES = "input_price, output_price, cache_read_price, cache_write_price"
#: What an edit may change without a verification: none of these makes the
#: connection reach a new endpoint or list a model it has no facts for.
_UNVERIFIED = frozenset({"name", "models", "enabled"})
#: What a verified edit may change. Enabling is not among them: it is applied
#: after a verified part committed (``update_connection``).
_VERIFIED = frozenset({"name", "models", "base_url", "api_key_ciphertext"})
_ARRAY_COLUMNS = frozenset({"models"})
#: Today, as the usage table counts it.
_TODAY = "(clock_timestamp() AT TIME ZONE 'UTC')::date"


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
    """Provided connections, their models' prices, and what the model relay reads and records."""

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
                        (id, name, base_url, models, api_key_ciphertext, model_checks)
                    VALUES ($1, $2, $3, $4::text[], $5, $6::jsonb)
                    RETURNING {_COLUMNS}
                    """,
                    values["id"],
                    values["name"],
                    values["base_url"],
                    values["models"],
                    values["api_key_ciphertext"],
                    json.dumps(checks),
                )
            except asyncpg.UniqueViolationError as exc:
                raise ModelConnectionConflict(values["name"]) from exc
        assert row is not None
        return _connection(row)

    async def update_operator_connection(
        self, *, connection_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Apply an edit that needs no verification; ``None`` when nothing live matched.

        Renames, a model list that only shrinks, and enabling or disabling.
        Disabling raises the credential revision, so the caller's release
        finds every process started before it. A removed model takes its
        verification and its prices along.
        """
        unknown = set(changes) - _UNVERIFIED
        if unknown:
            raise ValueError(f"a provided connection cannot change {sorted(unknown)} unverified")
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                "SELECT models FROM agent_operator_model_connections "
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
                SET deleted_at = NOW(), enabled = FALSE,
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

    # -- prices and use --------------------------------------------------------

    async def operator_prices(
        self, *, connection_ids: list[str]
    ) -> dict[str, dict[str, ModelPrices]]:
        """Each connection's models that have prices, with them."""
        result: dict[str, dict[str, ModelPrices]] = {cid: {} for cid in connection_ids}
        if not connection_ids:
            return result
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT operator_connection_id, model, {_PRICES}
                FROM agent_operator_model_limits
                WHERE operator_connection_id = ANY($1::text[])
                """,
                connection_ids,
            )
        for row in rows:
            prices = model_prices(row)
            if prices is not None:
                result[str(row["operator_connection_id"])][str(row["model"])] = prices
        return result

    async def set_operator_prices(
        self, *, connection_id: str, prices: Mapping[str, ModelPrices | None]
    ) -> None:
        """Store each listed model's prices as its provider's listing gave them.

        A model given ``None`` has its prices removed: its use is recorded by
        tokens alone. A model the connection does not list is skipped. A new
        row is written with the request limit ``0``, as a previous release
        reads only that and would otherwise offer the model without limit.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            for model, price in prices.items():
                if price is None:
                    await conn.execute(
                        "DELETE FROM agent_operator_model_limits "
                        "WHERE operator_connection_id = $1 AND model = $2",
                        connection_id,
                        model,
                    )
                    continue
                await conn.execute(
                    f"""
                    INSERT INTO agent_operator_model_limits
                        (operator_connection_id, model, user_daily_requests, {_PRICES})
                    SELECT c.id, $2, 0, $3, $4, $5, $6
                    FROM agent_operator_model_connections c
                    WHERE c.id = $1 AND c.deleted_at IS NULL AND $2 = ANY(c.models)
                    ON CONFLICT (operator_connection_id, model) DO UPDATE
                    SET input_price = EXCLUDED.input_price,
                        output_price = EXCLUDED.output_price,
                        cache_read_price = EXCLUDED.cache_read_price,
                        cache_write_price = EXCLUDED.cache_write_price
                    """,
                    connection_id,
                    model,
                    price.input,
                    price.output,
                    price.cache_read,
                    price.cache_write,
                )

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

    async def enabled_operator_connections(self) -> list[dict[str, Any]]:
        """Every enabled provided connection, oldest first: what owners are offered."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_COLUMNS} FROM agent_operator_model_connections
                WHERE deleted_at IS NULL AND enabled ORDER BY created_at, id
                """
            )
        return [_connection(row) for row in rows]

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
        """Name the running turn a model relay placeholder speaks for.

        ``None`` unless the residency passes the relays' shared fence
        (:func:`~cloud_agent.storage._sql.fences.live_residency_caller_sql`).
        Otherwise the turn's job, owner and provided connection, with the
        revision this residency's process was bound to (``bound_revision``,
        ``None`` when it was bound to none) and, for ``model``, its prices
        (``prices``, ``None`` when its listing gave none). ``connection`` is
        ``None`` when the turn does not run on a provided connection at all.
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
            caller["prices"] = None
            if caller["connection"] is not None and model is not None:
                caller["prices"] = model_prices(
                    await conn.fetchrow(
                        f"SELECT {_PRICES} FROM agent_operator_model_limits "
                        "WHERE operator_connection_id = $1 AND model = $2",
                        str(connection_id),
                        model,
                    )
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
        """Add one served request, its tokens and what they cost to the owner's use on ``day``.

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
    return assignments


async def _forget_removed_models(conn: Any, connection_id: str, models: list[str]) -> None:
    """Drop the prices of models a connection no longer lists, and what earlier releases kept.

    A model listed again later has its prices read again from the listing.
    The other two tables are accounts' answers on a model and the request
    limits that earlier releases wrote; nothing reads them any more.
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
    "read_operator_connection",
]
