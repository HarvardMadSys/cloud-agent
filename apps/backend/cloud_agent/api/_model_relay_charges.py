"""Use the model relay could not record when its answer ended: written later.

A served request's tokens and cost are added to its owner's use today as its
answer ends (``record_operator_request``). When that write fails, dropping it
would leave the usage table short of what the deployment's key was used for.
As the gateway keeps a cost it has not written yet (hybridInference's
``serving.spend_ledger``), the relay keeps it here and writes it again, a
little later each time and then every few minutes, until the write lands. One
that has not landed when the process stops is reported with its amount.

Only a write that certainly did not land is tried again. One whose outcome is
unknown (the connection broke after the statement was sent) may have been
counted already. It is reported instead: counting it twice would record one
request twice.

Per process: a process that stops makes one last try for each record it still
holds, and reports what is left.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from cloud_agent.logging_utils import get_logger
from cloud_agent.ports.operator_models import ChargeOutcomeUnknown

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from datetime import date

    from cloud_agent.domain.model_usage import TokenUsage
    from cloud_agent.ports import OperatorModelRepository

logger = get_logger("cloud_agent.model_relay")

#: How long to wait before each further try, in seconds; the last is repeated
#: until the write lands, as the gateway's ledger keeps trying.
RETRY_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)


class _ChargeStorage(Protocol):
    operator_models: OperatorModelRepository


@dataclass(frozen=True)
class Charge:
    """One served request's tokens and cost, as they are added to its owner's use."""

    connection_id: str
    user_id: str
    model: str
    #: The UTC day its answer ended on, whenever it is written.
    day: date
    cost_microusd: int
    used: TokenUsage
    estimated: bool
    job_id: str | None = None

    def facts(self) -> dict[str, Any]:
        """What a log line says of it: who, which model, what it cost. Never content."""
        return {
            "job_id": self.job_id,
            "connection_id": self.connection_id,
            "user_id": self.user_id,
            "model": self.model,
            "day": self.day.isoformat(),
            "cost_microusd": self.cost_microusd,
            "input_tokens": self.used.prompt,
            "cache_read_tokens": self.used.cache_read,
            "cache_write_tokens": self.used.cache_write,
            "output_tokens": self.used.output,
            "estimated": self.estimated,
        }


async def write_charge(store: _ChargeStorage, charge: Charge) -> None:
    """Add ``charge`` to its owner's use on its day; raises when the write failed."""
    await store.operator_models.record_operator_request(
        connection_id=charge.connection_id,
        user_id=charge.user_id,
        model=charge.model,
        cost_microusd=charge.cost_microusd,
        input_tokens=charge.used.prompt,
        cache_read_tokens=charge.used.cache_read,
        cache_write_tokens=charge.used.cache_write,
        output_tokens=charge.used.output,
        estimated=charge.estimated,
        day=charge.day,
    )


def report(event: str, charge: Charge, *, error: bool = False) -> None:
    """One structured line about a charge."""
    log = logger.error if error else logger.info
    log(event, extra={"event": event, **charge.facts()})


class PendingCharges:
    """Records not written yet, each written again until it lands."""

    def __init__(self, *, delays: Sequence[float] = RETRY_DELAYS) -> None:
        """Wait ``delays`` before each further try; after the last, it again before every try."""
        self._delays = tuple(delays) or (0.0,)
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False

    @property
    def count(self) -> int:
        """How many charges are waiting to be written."""
        return len(self._tasks)

    def keep(self, store: _ChargeStorage, charge: Charge) -> None:
        """Write ``charge`` later."""
        task = asyncio.get_running_loop().create_task(self._write_later(store, charge))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self, timeout: float = 10.0) -> None:
        """Try every waiting charge once more now; report the ones that still fail."""
        if not self._tasks:
            return
        # A charge kept a moment ago may not have begun waiting yet; cancelled
        # before it starts, it would skip its last try.
        await asyncio.sleep(0)
        waiting = list(self._tasks)
        self._closing = True
        for task in waiting:
            task.cancel()
        try:
            _done, left = await asyncio.wait(waiting, timeout=timeout)
            for task in left:
                task.cancel()
            if left:
                await asyncio.wait(left, timeout=1.0)
        finally:
            self._closing = False

    def _waits(self) -> Iterator[float]:
        yield from self._delays
        while True:
            yield self._delays[-1]

    async def _write_later(self, store: _ChargeStorage, charge: Charge) -> None:
        try:
            for delay in self._waits():
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    if not self._closing:
                        raise
                    # The process is stopping: one last try now.
                    if not await self._try(store, charge):
                        report("agent_model_relay_charge_lost", charge, error=True)
                    return
                if await self._try(store, charge):
                    return
        except asyncio.CancelledError:
            report("agent_model_relay_charge_lost", charge, error=True)
            raise

    async def _try(self, store: _ChargeStorage, charge: Charge) -> bool:
        """Write ``charge`` once; whether there is nothing more to try."""
        try:
            await write_charge(store, charge)
        except ChargeOutcomeUnknown:
            report("agent_model_relay_charge_unknown", charge, error=True)
            return True
        except Exception:
            logger.warning(
                "agent_model_relay_charge_retry",
                extra={"event": "agent_model_relay_charge_retry", **charge.facts()},
                exc_info=True,
            )
            return False
        report("agent_model_relay_charged_late", charge)
        return True


__all__ = ["RETRY_DELAYS", "Charge", "PendingCharges", "report", "write_charge"]
