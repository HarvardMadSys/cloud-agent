"""The runner pool document both the admin API and the drain command answer with."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cloud_agent.application import host_pool_view
from cloud_agent.domain.host_pool import HOST_ONLINE


@pytest.mark.parametrize(
    "row",
    [
        # The optional phase-G fields may still be absent, but migration 0051
        # backfills the protocol generation before this API starts.
        {
            "host": "legacy",
            "last_worker_id": "w-1",
            "first_seen_at": datetime.now(UTC),
            "last_seen_at": datetime.now(UTC),
            "is_active": False,
            "drain_fence_epoch": uuid4(),
        }
    ],
)
def test_a_row_without_optional_pool_columns_still_renders(row):
    item = host_pool_view.host_item(row)

    assert item.status == HOST_ONLINE
    assert item.enrolled is False
    assert item.slots_used == 0
    assert item.slots_total is None
    assert item.drain_fence_epoch == row["drain_fence_epoch"]
