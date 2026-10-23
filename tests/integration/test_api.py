from datetime import timedelta

from httpx import AsyncClient

from tests.integration.support import START, create_resource, create_slot


async def test_desk_is_served(client: AsyncClient) -> None:
    page = await client.get("/")
    assert page.status_code == 200
    assert "One seat" in page.text
    css = await client.get("/assets/app.css")
    assert css.status_code == 200
    assert "Fraunces" in css.text
    script = await client.get("/assets/app.js")
    assert script.status_code == 200
    assert "Idempotency-Key" in script.text


async def test_health_checks_the_database(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["strategy"] == "row_lock"


async def test_resource_round_trip_and_slot_generation(client: AsyncClient) -> None:
    resource_id = await create_resource(client)
    listed = await client.get("/api/v1/resources")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "North studio"

    end = START + timedelta(hours=2)
    created = await client.post(
        f"/api/v1/resources/{resource_id}/slots",
        json={
            "from": START.isoformat(),
            "to": end.isoformat(),
            "duration_minutes": 30,
            "capacity": 4,
        },
    )
    assert created.status_code == 201
    assert len(created.json()) == 4

    window_end = START + timedelta(hours=3)
    available = await client.get(
        f"/api/v1/resources/{resource_id}/availability",
        params={"from": START.isoformat(), "to": window_end.isoformat()},
    )
    assert available.status_code == 200
    body = available.json()
    assert len(body) == 4
    assert body[0]["remaining"] == 4


async def test_a_full_slot_rejects_the_next_email(client: AsyncClient) -> None:
    resource_id = await create_resource(client)
    slot_id = await create_slot(client, resource_id, capacity=1)
    taken = await client.post(
        "/api/v1/bookings",
        json={"slot_id": slot_id, "customer_email": "ada@example.com"},
    )
    assert taken.status_code == 201
    full = await client.post(
        "/api/v1/bookings",
        json={"slot_id": slot_id, "customer_email": "grace@example.com"},
    )
    assert full.status_code == 409
    assert full.headers["content-type"].startswith("application/problem+json")
    assert "full" in full.json()["detail"].lower()


async def test_missing_slot_is_404_problem(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/bookings",
        json={"slot_id": 999, "customer_email": "ada@example.com"},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 404


async def test_booking_then_duplicate_email_then_cancel(client: AsyncClient) -> None:
    resource_id = await create_resource(client)
    slot_id = await create_slot(client, resource_id, capacity=2)

    first = await client.post(
        "/api/v1/bookings",
        json={"slot_id": slot_id, "customer_email": "Ada@Example.com"},
        headers={"Idempotency-Key": "desk-1"},
    )
    assert first.status_code == 201
    booking_id = first.json()["id"]
    assert first.json()["customer_email"] == "ada@example.com"

    replay = await client.post(
        "/api/v1/bookings",
        json={"slot_id": slot_id, "customer_email": "ada@example.com"},
        headers={"Idempotency-Key": "desk-1"},
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == booking_id

    duplicate = await client.post(
        "/api/v1/bookings",
        json={"slot_id": slot_id, "customer_email": "ada@example.com"},
    )
    assert duplicate.status_code == 409

    fetched = await client.get(f"/api/v1/bookings/{booking_id}")
    assert fetched.status_code == 200

    cancelled = await client.post(f"/api/v1/bookings/{booking_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    again = await client.post(f"/api/v1/bookings/{booking_id}/cancel")
    assert again.status_code == 200
    assert again.json()["status"] == "cancelled"

    available = await client.get(
        f"/api/v1/resources/{resource_id}/availability",
        params={"from": START.isoformat(), "to": (START + timedelta(hours=1)).isoformat()},
    )
    assert available.json()[0]["booked_count"] == 0
    assert available.json()[0]["remaining"] == 2
