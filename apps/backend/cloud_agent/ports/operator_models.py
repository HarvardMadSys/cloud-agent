"""Port: the models a deployment provides, their prices, and the model relay's reads and records."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from cloud_agent.domain.operator_models import ModelPrices


class ChargeOutcomeUnknown(Exception):
    """A charge's statement was sent and its outcome never came back.

    It may have been counted. Writing it again could count it twice, so it is
    reported instead of retried. Any other failure of
    :meth:`OperatorModelRepository.record_operator_request` wrote nothing.
    """


class OperatorModelRepository(Protocol):
    """Persist provided connections and record what the model relay's requests used.

    Rows are plain dictionaries with the fields an owner's connection row has,
    ``api_key_ciphertext`` included; encrypting and decrypting is the caller's
    job, and a row is never answered to anyone as-is.
    """

    async def list_operator_connections(self) -> list[dict[str, Any]]:
        """Every live provided connection, oldest first."""
        ...

    async def get_operator_connection(self, *, connection_id: str) -> dict[str, Any] | None:
        """One provided connection, deleted ones included; ``None`` otherwise."""
        ...

    async def create_operator_connection(self, *, values: dict[str, Any]) -> dict[str, Any]:
        """Insert a verified, disabled connection; ``ModelConnectionConflict`` on a taken name."""
        ...

    async def update_operator_connection(
        self, *, connection_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Apply an edit that needs no verification; ``None`` when nothing live matched.

        Raises ``OperatorTermsMissing`` for enabling before the connection has
        a default budget and every listed model has prices, and ``ValueError``
        for a field that needs :meth:`commit_verified_update`.
        """
        ...

    async def commit_verified_operator_update(
        self,
        *,
        connection_id: str,
        expected: Mapping[str, Any],
        changes: dict[str, Any],
        model_checks: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Apply a verified edit only while the row is still ``expected``; ``None`` otherwise."""
        ...

    async def delete_operator_connection(self, *, connection_id: str) -> dict[str, Any] | None:
        """Tombstone a connection and erase its key; ``None`` when there was none."""
        ...

    async def mark_operator_key_refused(self, *, connection_id: str, refused: bool) -> None:
        """Record that the provider refuses the stored key, or accepts it again."""
        ...

    async def model_relay_host_count(self) -> int:
        """How many enrolled hosts advertise that their sandboxes reach the model relay."""
        ...

    async def operator_prices(
        self, *, connection_ids: list[str]
    ) -> dict[str, dict[str, ModelPrices]]:
        """Each connection's models that have prices: ``{connection: {model: prices}}``."""
        ...

    async def set_operator_prices(
        self, *, connection_id: str, prices: Mapping[str, ModelPrices | None]
    ) -> None:
        """Store listed models' prices; ``None`` removes a model's."""
        ...

    async def operator_usage(
        self, *, connection_id: str, start: date, end: date, limit: int
    ) -> list[dict[str, Any]]:
        """Requests, their tokens and their cost per day, account and model, newest day first."""
        ...

    async def enabled_operator_connections(self) -> list[dict[str, Any]]:
        """Every enabled provided connection, oldest first."""
        ...

    async def release_stale_operator_residencies(
        self, *, connection_id: str
    ) -> list[dict[str, Any]]:
        """End every open residency started on an older revision, or on a dead connection."""
        ...

    async def resolve_model_relay_caller(
        self, *, thread_id: str, residency_generation: int, model: str | None
    ) -> dict[str, Any] | None:
        """Name the running turn a model relay placeholder speaks for; ``None`` when it buys nothing.

        Fenced like every relay's caller: the residency is the chat's hot,
        unexpired one and is serving the job's current, running, leased
        attempt on its own enrolled host, and the job has not been asked to
        stop. Answers the job, its owner, its provided ``connection`` row
        (``None`` when the turn runs on none), the revision this residency was
        bound to, and for ``model`` its ``prices`` (``None`` when it has none).
        """
        ...

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

        ``day`` is the UTC day its answer ended on, today when ``None``.
        ``input_tokens`` is every input token, the cached ones included.
        ``estimated`` says the answer named no usage, or was cut short, and
        the tokens are the relay's estimate. Raises :class:`ChargeOutcomeUnknown`
        when the statement was sent and its outcome is not known; any other
        failure wrote nothing.
        """
        ...


__all__ = ["ChargeOutcomeUnknown", "OperatorModelRepository"]
