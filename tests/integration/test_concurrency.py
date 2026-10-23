import asyncio
import os
from collections import Counter

from httpx import AsyncClient

from tests.integration.support import create_resource, create_slot


async def _book_many(
    client: AsyncClient, slot_id: int, count: int, key: str | None = None
) -> list[int]:
    async def once(index: int) -> int:
        headers = {"Idempotency-Key": key} if key else {}
        email = "same@example.com" if key else f"guest{index}@example.com"
        response = await client.post(
            "/api/v1/bookings",
            json={"slot_id": slot_id, "customer_email": email},
            headers=headers,
        )
        return response.status_code

    return list(await asyncio.gather(*(once(i) for i in range(count))))


async def _confirmed(client: AsyncClient, resource_id: int, slot_id: int) -> tuple[int, int]:
    from datetime import timedelta

    from tests.integration.support import START

    response = await client.get(
        f"/api/v1/resources/{resource_id}/availability",
        params={"from": START.isoformat(), "to": (START + timedelta(hours=1)).isoformat()},
    )
    row = next(item for item in response.json() if item["id"] == slot_id)
    return int(row["booked_count"]), int(row["capacity"])


async def test_row_lock_admits_exactly_capacity(client: AsyncClient) -> None:
    resource_id = await create_resource(client)
    slot_id = await create_slot(client, resource_id, capacity=3)
    codes = Counter(await _book_many(client, slot_id, 50))
    assert codes[201] == 3
    assert codes[409] == 47
    booked, capacity = await _confirmed(client, resource_id, slot_id)
    assert booked == 3
    assert capacity == 3


async def test_atomic_update_admits_exactly_capacity(client: AsyncClient) -> None:
    os.environ["BOOKING_STRATEGY"] = "atomic_update"
    from slotwise.config import reset_settings

    reset_settings()
    health = await client.get("/health")
    assert health.json()["strategy"] == "atomic_update"
    resource_id = await create_resource(client)
    slot_id = await create_slot(client, resource_id, capacity=3)
    codes = Counter(await _book_many(client, slot_id, 50))
    assert codes[201] == 3
    assert codes[409] == 47
    booked, _capacity = await _confirmed(client, resource_id, slot_id)
    assert booked == 3


async def test_one_idempotency_key_creates_one_booking(client: AsyncClient) -> None:
    resource_id = await create_resource(client)
    slot_id = await create_slot(client, resource_id, capacity=20)
    codes = Counter(await _book_many(client, slot_id, 20, key="same-key"))
    assert codes[201] == 1
    assert codes[200] == 19
    booked, _capacity = await _confirmed(client, resource_id, slot_id)
    assert booked == 1


async def test_concurrent_cancels_decrement_once(client: AsyncClient) -> None:
    resource_id = await create_resource(client)
    slot_id = await create_slot(client, resource_id, capacity=1)
    created = await client.post(
        "/api/v1/bookings",
        json={"slot_id": slot_id, "customer_email": "ada@example.com"},
    )
    booking_id = created.json()["id"]

    async def cancel_once() -> int:
        response = await client.post(f"/api/v1/bookings/{booking_id}/cancel")
        return response.status_code

    codes = Counter(await asyncio.gather(cancel_once(), cancel_once()))
    assert codes[200] == 2
    booked, _capacity = await _confirmed(client, resource_id, slot_id)
    assert booked == 0
